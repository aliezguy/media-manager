# P3-3a / P3-3b 演员中文化请求治理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 Phase 3 剩余两项请求治理子任务——3a「轻量头像」让系列汉化跳过每演员 TMDB 上半场（TMDB 请求 → 0）；3b「系列级豆瓣 cast 缓存」让已汉化剧集重触发时复用新鲜 cast（豆瓣请求 → 0-2）。一次已汉化 28 人剧集的重触发从现状「2-4 豆瓣 + 0-56 TMDB」降为「0-2 豆瓣 + 0 TMDB」。

**Architecture:**
- **3a light_mode**：`resolve_actor_profile` 增加 `light_mode: bool = False` 参数，为 `True` 时跳过整个 TMDB 上半场（[actor_profile_service.py:1169-1202](backend/services/actor_profile_service.py#L1169-L1202)，每演员 0-2 次请求的大头）；L2 兜底槽改为「仅提升已缓存头像」（`existing.image_url`，CDN 下载而非 Provider API）。`ensure_profiles_for_people` / `save_media_to_db` 各增一个布尔透传参数；`DoubanSinizer.sinicize` 两处接线传 `True`（分集前置批处理 + 顶层 Series 入库）。演员库路径（actor_router / sync_actions 批量刷新）保持默认完整漏斗。
- **3b cast 缓存**：`MediaSyncStatus` 新增 `douban_cast_cache` JSON 列（自包含 `fetched_at` 时间戳 + `cast` 映射，格式 `{name: {avatar, douban_id, role}}`）；新增 `DoubanSinizer._load_douban_cast(series_id, douban_id)` 助手——新鲜缓存（< 7 天）直接复用返回 0 次豆瓣请求，过期/缺失则抓一次并回写；替换 `sinicize` 与 `_resolve_series_douban_context` 两处裸 `_fetch_douban_actors` 调用。

**Tech Stack:** Python 3.13 / SQLAlchemy（JSON 列）/ pytest（sqlite :memory: 约定，monkeypatch）/ 既有 `request_budget`（已在 `_fetch_actors_frodo`→`_frodo_get` 链路生效，缓存命中时 0 消耗）

## Global Constraints

- 测试运行目录：`cd backend`，解释器 `venv/bin/python -m pytest`。聚焦 `tests/test_light_mode.py tests/test_series_cast_cache.py -v`；全量 `tests/ -q` 回归。
- **light_mode 只影响系列汉化路径**：演员库（actor_router 强制刷新、sync_actions 批量刷新）保持默认 `light_mode=False` 走完整漏斗——「头像后补走演员库」是本轮分工本意。
- **cast 缓存新鲜度 < 7 天**：与 `actor_profile_service._NO_AVATAR_COOLDOWN_DAYS = 7` 一致。`fetched_at` 必须**自包含存于 JSON 内**，不依赖 `MediaSyncStatus.update_time`（该列带 `onupdate=datetime.now`，每次 sinicize 入库都会刷新，会导致缓存永不失效）。
- **不重写 `_fetch_douban_actors`**：只在其上叠加缓存读取层。返回的 actor dict 形状保持一致（`[{name, avatar, role, id}]`），`_build_douban_match_map` / `_match_and_update` 零改动。
- **不新增依赖、不改 config.json**：3a/3b 均无配置项（cast 缓存 TTL 用代码常量 `_DOUBAN_CAST_TTL_DAYS = 7`）。
- **TDD**：每个 Task 先写失败测试 → 跑通失败 → 实现 → 跑通通过 → commit。提交粒度 = 每个 Task 一次 commit。
- 依赖 `request_budget`（P3-3c 已完成）与 P1 单集走父系列（`_resolve_series_douban_context` / `_sinicize_episode_via_parent` 已存在）。

## 设计决策（供 Review 确认）

1. **cast 缓存 JSON 自包含 `fetched_at`**：设计文档只给了列名 `douban_cast_cache` 与内容 `name → {avatar, douban_id, role}`，未指定时间戳机制。若用 `update_time` 判断新鲜度，会被 sinicize 每次入库的 `onupdate` 刷新，缓存永不失效（bug）。故在 JSON 外层包一层 `{"fetched_at": ISO8601, "cast": {...}}`，内容格式与设计一致，时间戳自包含、不受无关写入污染。
2. **顶层 Series 演员也走 light_mode**：设计文档只点名分集前置批处理（douban_service.py:499），但顶层 `save_media_to_db`（:384）在 499 **之前**执行，若保持完整漏斗，顶层演员仍会各触发一次 TMDB（违背「TMDB → 0」）。因此 `save_media_to_db` 新增 `light_profiles` 透传参数，`sinicize` 顶层入库传 `True`；`audit_local` / `task_queue` 调用保持默认 `False`（完整漏斗，二者本就低频且需要全维数据）。
3. **L2「仅提升已缓存头像」**：light_mode 下跳过 TMDB 上半场后 `tmdb_avatar_bak` 恒为空，L2 原逻辑自然不触发新请求。补一个 4 行小逻辑：若 L1 未命中且 `existing.image_url` 非空（历史完整跑留下的 TMDB/豆瓣外链，且无本地文件、冷却已过期），将其提升为头像源——是 CDN 图片下载而非 Provider API 请求，符合「仅提升已缓存」。
4. **新演员无头像不建记录**：light_mode 下全新演员（`existing is None`）若无任何头像源，走既有 `:1403` 早退返回 `None`，与完整漏斗行为一致；有历史记录者仍会 UPSERT 刷新 `update_time`，重置 7 天无头像冷却。无需新增逻辑。
5. **迁移列类型 `TEXT`**：SQLAlchemy JSON 在 SQLite 落为 TEXT，`ALTER TABLE ... ADD COLUMN douban_cast_cache TEXT` 与 `create_all` 行为一致；P4 MySQL 迁移时再处理原生 JSON（不在本计划范围）。

---

### Task 1: `resolve_actor_profile` 增加 `light_mode`，跳过 TMDB 上半场 + L2 提升已缓存头像（3a 核心）

**Files:**
- Modify: `backend/services/actor_profile_service.py`
  - 函数签名 `:766`、docstring `:784-791`
  - Step 3 上半场守卫 `:1173`（`if force_refresh or needs_tmdb_meta:`）
  - L2 兜底块 `:1332-1339`（追加 light_mode 提升逻辑）
- Test: `backend/tests/test_light_mode.py`（新建）

**Interfaces:**
- Produces（后续 Task 依赖）：
  - `resolve_actor_profile(actor_name: str, db, context_info: dict | None = None, force_refresh: bool = False, light_mode: bool = False) -> dict | None` —— `light_mode=True` 时跳过 TMDB 上半场（不调 `fetch_tmdb_person_details`），仍走 L0/L0.5/L1/L2-提升；返回 dict 结构与现状一致。
- Consumes：既有 `fetch_tmdb_person_details`、`_build_standard_path`、`_download_image`、`_NO_AVATAR_COOLDOWN_DAYS`。

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_light_mode.py`（完整内容）：

```python
"""light_mode 轻量头像测试 — 3a：系列汉化跳过每演员 TMDB 上半场。

目标：让系列汉化（sinicize）不因每演员的 TMDB 详情请求打爆 Provider。
  - resolve_actor_profile(light_mode=True) 跳过整个 TMDB 上半场（0-2 次/演员的大头）
  - 轻量路径只走 L0 本地 → L0.5 Emby 原生 → L1 复用豆瓣演员表直链 → L2 提升已缓存头像
  - ensure_profiles_for_people / save_media_to_db 透传 light_mode，演员库路径保持完整漏斗
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base
from models import ActorProfile
import services.actor_profile_service as aps


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """隔离真实 people/ 文件系统与网络：临时目录 + 关闭冷却缓存。"""
    monkeypatch.setattr(aps, "_PEOPLE_DIR", str(tmp_path / "people"))
    monkeypatch.setattr(aps, "_local_sniff_cache", {})


def _make_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def _cfg():
    return {"douban_enabled": True, "enable_emby_avatar_first": False,
            "douban_cookie": "", "tmdb_api_key": ""}


def _no_download(*a, **k):
    return True


def test_light_mode_skips_tmdb_half(monkeypatch):
    Session = _make_db()
    db = Session()
    monkeypatch.setattr(aps, "load_config", lambda: _cfg())
    monkeypatch.setattr(aps, "_find_local_avatar", lambda name: None)
    monkeypatch.setattr(aps, "_download_image", _no_download)

    def boom(*a, **k):
        raise AssertionError("light_mode=True 不应触发 TMDB 请求")
    monkeypatch.setattr(aps, "fetch_tmdb_person_details", boom)

    prof = aps.resolve_actor_profile(
        "UniqueLightActor", db,
        context_info={"douban_avatar_url": "http://douban.test/a.jpg", "douban_id": "c1"},
        light_mode=True,
    )
    assert prof is not None
    assert prof["source"] == "douban"          # L1 豆瓣演员表直链命中
    assert prof["image_url"] == "http://douban.test/a.jpg"
    assert prof["tmdb_id"] == ""               # 未走 TMDB 上半场，无 TMDB 元数据
    db.close()


def test_full_mode_still_fetches_tmdb(monkeypatch):
    Session = _make_db()
    db = Session()
    monkeypatch.setattr(aps, "load_config", lambda: _cfg())
    monkeypatch.setattr(aps, "_find_local_avatar", lambda name: None)
    monkeypatch.setattr(aps, "_download_image", _no_download)

    called = []
    monkeypatch.setattr(aps, "fetch_tmdb_person_details",
                        lambda *a, **k: called.append(1) or None)

    prof = aps.resolve_actor_profile(
        "UniqueFullActor", db,
        context_info={"douban_avatar_url": "http://douban.test/a.jpg", "douban_id": "c1"},
        light_mode=False,   # 默认完整漏斗
    )
    assert called, "light_mode=False 应调用 TMDB 上半场"
    assert prof is not None
    db.close()


def test_light_mode_promotes_cached_image_url(monkeypatch):
    Session = _make_db()
    db = Session()
    # 历史完整跑留下的外链，但本地文件丢失、冷却已过期（8 天前）
    db.add(ActorProfile(
        name="OldActor", image_url="http://cached.test/x.jpg", source="tmdb",
        tmdb_id="777", update_time=datetime.now() - timedelta(days=8),
    ))
    db.flush()
    monkeypatch.setattr(aps, "load_config", lambda: _cfg())
    monkeypatch.setattr(aps, "_find_local_avatar", lambda name: None)
    monkeypatch.setattr(aps, "_download_image", _no_download)

    def boom(*a, **k):
        raise AssertionError("light_mode=True 不应触发 TMDB 请求")
    monkeypatch.setattr(aps, "fetch_tmdb_person_details", boom)

    prof = aps.resolve_actor_profile("OldActor", db, context_info={}, light_mode=True)
    assert prof is not None
    assert prof["image_url"] == "http://cached.test/x.jpg"  # L2 提升已缓存头像
    db.close()
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && venv/bin/python -m pytest tests/test_light_mode.py -v`
Expected: 3 个 FAIL——`TypeError: resolve_actor_profile() got an unexpected keyword argument 'light_mode'`。

- [ ] **Step 3: 实现**

`backend/services/actor_profile_service.py` 签名（`:766-771`）与 docstring（`:784-791`）：

```python
def resolve_actor_profile(
    actor_name: str,
    db,
    context_info: dict | None = None,
    force_refresh: bool = False,
    light_mode: bool = False,
) -> dict | None:
```

docstring 的 Args 末尾追加：

```python
        light_mode:    轻量模式（系列汉化专用）。True 时跳过整个 TMDB 上半场
                       （每演员 0-2 次请求的大头），只走 L0/L0.5/L1，L2 仅提升
                       已缓存头像；False 走完整漏斗。演员库刷新务必传 False。
```

Step 3 守卫（`:1169-1173`）：

```python
    # ---- Step 3: 前置 TMDB 元数据查询（仅取元数据，头像暂存备份绝不截留） ----
    has_overview = bool(profile_data["overview"])
    needs_tmdb_meta = not provider_tmdb_id or not has_overview

    if not light_mode and (force_refresh or needs_tmdb_meta):
```

L2 兜底块（`:1332-1339`）后追加：

```python
    # ---- 顺位 3.5: L2 轻量模式 — 仅提升已缓存头像（CDN 下载，非 Provider API 请求） ----
    if light_mode and not download_url and existing and existing.image_url:
        download_url = existing.image_url
        source = existing.source or "tmdb"
        logger.info(
            "   🥈 [Profile] L2 轻量模式提升已缓存头像: %s → %s",
            actor_name, download_url[:80],
        )
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && venv/bin/python -m pytest tests/test_light_mode.py -v`
Expected: 3 PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/services/actor_profile_service.py backend/tests/test_light_mode.py
git commit -m "feat: resolve_actor_profile 支持 light_mode 跳过 TMDB 上半场（P3-3a）"
```

---

### Task 2: `ensure_profiles_for_people` / `save_media_to_db` 透传 light_mode 参数（3a 管道）

**Files:**
- Modify: `backend/services/actor_profile_service.py`（`ensure_profiles_for_people` `:1460`，resolve 调用 `:1519`）
- Modify: `backend/services/db_crud.py`（`save_media_to_db` 签名 `:90-103`，ensure 调用 `:198`）
- Test: `backend/tests/test_light_mode.py`（追加 4 个测试）

**Interfaces:**
- Produces（Task 3 依赖）：
  - `ensure_profiles_for_people(db, people: list, light_mode: bool = False) -> dict`
  - `save_media_to_db(..., skip_profiles: bool = False, light_profiles: bool = False) -> None`
- Consumes：Task 1 的 `resolve_actor_profile(..., light_mode=...)`。

- [ ] **Step 1: 写失败测试**

追加到 `backend/tests/test_light_mode.py`（文件末尾）：

```python
import services.douban_service as ds
import services.db_crud as dbc


def test_ensure_profiles_forwards_light_mode(monkeypatch):
    Session = _make_db()
    db = Session()
    captured = {}

    def fake_resolve(name, db, context_info=None, force_refresh=False, light_mode=False):
        captured["light_mode"] = light_mode
        return {"name": name, "local_image_path": "", "image_url": "",
                "local_image_url": "", "source": "", "tmdb_id": "", "imdb_id": "",
                "douban_celebrity_id": "", "birth_date": "", "birth_place": "", "overview": ""}
    monkeypatch.setattr(aps, "resolve_actor_profile", fake_resolve)

    aps.ensure_profiles_for_people(db, [{"Name": "A", "Type": "Actor"}], light_mode=True)
    assert captured["light_mode"] is True
    db.close()


def test_ensure_profiles_default_full_mode(monkeypatch):
    Session = _make_db()
    db = Session()
    captured = {}

    def fake_resolve(name, db, context_info=None, force_refresh=False, light_mode=False):
        captured["light_mode"] = light_mode
        return {"name": name, "local_image_path": "", "image_url": "",
                "local_image_url": "", "source": "", "tmdb_id": "", "imdb_id": "",
                "douban_celebrity_id": "", "birth_date": "", "birth_place": "", "overview": ""}
    monkeypatch.setattr(aps, "resolve_actor_profile", fake_resolve)

    aps.ensure_profiles_for_people(db, [{"Name": "A", "Type": "Actor"}])  # 默认 False
    assert captured["light_mode"] is False
    db.close()


def test_save_media_to_db_forwards_light_profiles(monkeypatch):
    Session = _make_db()
    db = Session()
    captured = {}

    def fake_ensure(db, people, light_mode=False):
        captured["light_mode"] = light_mode
    monkeypatch.setattr(dbc, "ensure_profiles_for_people", fake_ensure)

    dbc.save_media_to_db(
        db,
        emby_item={"Id": "s1", "Name": "九门", "Type": "Series",
                   "People": [{"Name": "A", "Type": "Actor"}]},
        people=[{"Name": "A", "Type": "Actor"}],
        light_profiles=True,
    )
    assert captured["light_mode"] is True
    db.close()


def test_save_media_to_db_default_full_mode(monkeypatch):
    Session = _make_db()
    db = Session()
    captured = {}

    def fake_ensure(db, people, light_mode=False):
        captured["light_mode"] = light_mode
    monkeypatch.setattr(dbc, "ensure_profiles_for_people", fake_ensure)

    dbc.save_media_to_db(
        db,
        emby_item={"Id": "s2", "Name": "无", "Type": "Movie",
                   "People": [{"Name": "B", "Type": "Actor"}]},
        people=[{"Name": "B", "Type": "Actor"}],
        # 不传 light_profiles → 默认完整漏斗
    )
    assert captured["light_mode"] is False
    db.close()
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && venv/bin/python -m pytest tests/test_light_mode.py -v`
Expected: 前 3 个 PASS（Task 1），后 4 个 FAIL——`unexpected keyword argument 'light_mode'`（ensure 调用）与 `'light_profiles'`（save_media_to_db）。

- [ ] **Step 3: 实现**

`backend/services/actor_profile_service.py` `ensure_profiles_for_people` 签名（`:1460-1463`）：

```python
def ensure_profiles_for_people(
    db,
    people: list,
    light_mode: bool = False,
) -> dict:
```

resolve 调用（`:1519`）：

```python
                profile = resolve_actor_profile(
                    name, db, context_info=ctx, light_mode=light_mode,
                )
```

`backend/services/db_crud.py` `save_media_to_db` 签名（`:90-103`）末尾追加：

```python
    parent_id: str = None,
    skip_profiles: bool = False,
    light_profiles: bool = False,   # 系列汉化专用：True 时 profile 解析走 light_mode
):
```

docstring Args 追加一行：

```python
        light_profiles: True 时 ensure_profiles_for_people 传 light_mode=True
                       （系列汉化跳过 TMDB 上半场）；audit/task_queue 保持 False
```

ensure 调用（`:198`）：

```python
                ensure_profiles_for_people(db, people, light_mode=light_profiles)
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && venv/bin/python -m pytest tests/test_light_mode.py -v`
Expected: 7 PASS（Task 1 的 3 个 + Task 2 的 4 个）。

- [ ] **Step 5: Commit**

```bash
git add backend/services/actor_profile_service.py backend/services/db_crud.py backend/tests/test_light_mode.py
git commit -m "feat: ensure_profiles_for_people / save_media_to_db 透传 light_mode（P3-3a）"
```

---

### Task 3: `DoubanSinizer.sinicize` 接线——分集前置批处理与顶层入库走 light_mode（3a 落地）

**Files:**
- Modify: `backend/services/douban_service.py`
  - 分集前置批处理 `:499`（`ensure_profiles_for_people(ep_db, unique_people)`）
  - 顶层 Series 入库 `:384-395`（`save_media_to_db(...)`）
- Test: `backend/tests/test_light_mode.py`（追加集成测试）

**Interfaces:**
- Consumes：Task 1 的 `resolve_actor_profile(light_mode)`、Task 2 的 `ensure_profiles_for_people(db, people, light_mode)` 与 `save_media_to_db(..., light_profiles)`。
- 不产出新接口；行为契约：`sinicize` 系列流全程不触发 TMDB 每演员详情请求。

- [ ] **Step 1: 写失败测试**

追加到 `backend/tests/test_light_mode.py`（文件末尾）。用模块级 spy 断言两处接线：

```python
class _NoTranslator:
    def is_available(self):
        return False


def _make_sinizer(monkeypatch):
    """复用 test_episode_via_parent 的 _fresh_db 模式：内存库 + emby 配置。"""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    monkeypatch.setattr(ds, "SessionLocal", TestSession)
    monkeypatch.setattr(ds, "load_config", lambda: {
        "emby_host": "http://emby.test", "emby_api_key": "k",
        "emby_user_id": "u", "max_actors_per_media": 50,
    })
    return ds.DoubanSinizer()


def _stub_sinicize_flow(s, monkeypatch):
    """stub sinicize 全链路下游，返回两个 spy 列表。"""
    calls = {"save_light_profiles": [], "ensure_light_modes": []}

    monkeypatch.setattr(s, "_get_emby_item", lambda sid: {
        "Id": sid, "Name": "九门", "Type": "Series",
        "People": [{"Name": "Sun Honglei", "Type": "Actor", "Role": "Li Yan"}],
        "ProviderIds": {"Imdb": "tt0000001"}, "ProductionYear": "2020",
    })
    monkeypatch.setattr(s, "_find_douban_id", lambda *a, **k: "123")
    monkeypatch.setattr(s, "_fetch_douban_actors", lambda did: [
        {"name": "孙红雷", "role": "李岩", "avatar": "http://douban.test/a.jpg", "id": "c1"},
    ])

    def fake_match(emby_actors, douban_actors, db=None, emby_item_id="", parent_id="",
                   provider_tmdb_ids=None):
        return ([
            dict(a, Name="孙红雷", Role="李岩",
                 _cn_name_conf=4, _cn_name_src="official",
                 _cn_role_conf=4, _cn_role_src="official")
            for a in emby_actors
        ], [], {}, {}, {}, {})
    monkeypatch.setattr(s, "_match_and_update", fake_match)
    monkeypatch.setattr(ds, "get_translator", lambda: _NoTranslator())
    monkeypatch.setattr(s, "_write_back_emby", lambda *a, **k: True)

    def fake_save(**kw):
        calls["save_light_profiles"].append(kw.get("light_profiles", False))
    monkeypatch.setattr(ds, "save_media_to_db", fake_save)

    monkeypatch.setattr(s, "_fetch_episodes", lambda *a, **k: [
        {"Id": "e1", "Name": "第 1 集", "ParentIndexNumber": 1, "IndexNumber": 1,
         "People": [{"Name": "Sun Honglei", "Type": "Actor", "Role": "Li Yan"}]},
    ])

    def fake_ensure(db, people, light_mode=False):
        calls["ensure_light_modes"].append(light_mode)
    monkeypatch.setattr(ds, "ensure_profiles_for_people", fake_ensure)

    monkeypatch.setattr(s, "_localize_episode_people", lambda *a, **k: [
        {"Name": "孙红雷", "Role": "李岩", "Type": "Actor"},
    ])
    monkeypatch.setattr(s, "_write_back_episode", lambda *a, **k: True)
    return calls


def test_sinicize_series_uses_light_mode(monkeypatch):
    s = _make_sinizer(monkeypatch)
    calls = _stub_sinicize_flow(s, monkeypatch)

    result = s.sinicize("s1")

    assert result["success"] is True
    # 顶层 Series 入库走 light_profiles=True（在 499 之前执行，若为 False 顶层演员仍会打 TMDB）
    assert True in calls["save_light_profiles"]
    # 分集前置批处理走 light_mode=True
    assert calls["ensure_light_modes"] == [True]
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && venv/bin/python -m pytest tests/test_light_mode.py::test_sinicize_series_uses_light_mode -v`
Expected: FAIL——`assert True in [False]` 与 `assert [False] == [True]`。

- [ ] **Step 3: 实现**

`backend/services/douban_service.py` 顶层入库（`:384-395`）在 `people=all_people,` 后加：

```python
                people=all_people,    # 中文化后的完整人员列表
                light_profiles=True,  # ★ 系列汉化顶层演员也走 light_mode（P3-3a）
```

分集前置批处理（`:499`）：

```python
                        ensure_profiles_for_people(ep_db, unique_people, light_mode=True)
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && venv/bin/python -m pytest tests/test_light_mode.py -v`
Expected: 8 PASS。随后跑既有回归确认无破坏：

Run: `cd backend && venv/bin/python -m pytest tests/test_episode_via_parent.py tests/test_translation_chain.py tests/test_request_budget.py -q`
Expected: 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/services/douban_service.py backend/tests/test_light_mode.py
git commit -m "feat: sinicize 系列流接线 light_mode（前置批处理 + 顶层入库）（P3-3a）"
```

---

### Task 4: `MediaSyncStatus.douban_cast_cache` JSON 列 + 迁移（3b 存储层）

**Files:**
- Modify: `backend/models.py`（`MediaSyncStatus` `:152-169`）
- Modify: `backend/database.py`（`_run_migrations` 的 media_sync_status 段 `:40-49`）
- Test: `backend/tests/test_series_cast_cache.py`（新建）

**Interfaces:**
- Produces（Task 5 依赖）：
  - `models.MediaSyncStatus.douban_cast_cache: Column(JSON, nullable=True)` —— 存 `{"fetched_at": ISO8601, "cast": {name: {avatar, douban_id, role}}}`。
  - `database._run_migrations()` 为已存在的 `media_sync_status` 表补 `douban_cast_cache TEXT` 列（幂等：已有则跳过）。
- Consumes：无（纯存储）。

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_series_cast_cache.py`（完整内容，含后续 Task 将复用的 fixture）：

```python
"""系列级豆瓣 cast 缓存测试 — 3b：MediaSyncStatus.douban_cast_cache。

目标：已汉化剧集重触发时复用新鲜 cast（<7 天），0 次豆瓣请求。
  - douban_cast_cache 列可 round-trip（JSON 自包含 fetched_at + cast map）
  - 迁移为旧表补齐 douban_cast_cache 列
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base
from models import MediaSyncStatus
import services.douban_service as ds


def _make_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def _fresh_db(monkeypatch):
    """内存库 + 补齐 emby 配置，与 test_episode_via_parent 模式一致。"""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    monkeypatch.setattr(ds, "SessionLocal", TestSession)
    monkeypatch.setattr(ds, "load_config", lambda: {
        "emby_host": "http://emby.test", "emby_api_key": "k",
        "emby_user_id": "u", "max_actors_per_media": 50,
    })
    return TestSession


def test_douban_cast_cache_column_roundtrip():
    Session = _make_db()
    db = Session()
    rec = MediaSyncStatus(
        emby_item_id="s1",
        douban_cast_cache={"fetched_at": "2026-08-04T10:00:00",
                           "cast": {"孙红雷": {"avatar": "http://a/x.jpg",
                                                "douban_id": "c1", "role": "主演"}}},
    )
    db.add(rec)
    db.commit()
    got = db.query(MediaSyncStatus).filter(MediaSyncStatus.emby_item_id == "s1").first()
    assert got.douban_cast_cache["cast"]["孙红雷"]["douban_id"] == "c1"
    assert got.douban_cast_cache["fetched_at"].startswith("2026-08-04")
    db.close()


def test_migration_adds_douban_cast_cache(monkeypatch, tmp_path):
    import database
    from sqlalchemy import create_engine as ce, text

    eng = ce(f"sqlite:///{tmp_path}/mig_test.db")
    with eng.connect() as conn:
        conn.execute(text(
            "CREATE TABLE media_sync_status ("
            "emby_item_id VARCHAR PRIMARY KEY, title VARCHAR, "
            "status VARCHAR DEFAULT 'pending')"
        ))
        conn.commit()
    monkeypatch.setattr(database, "engine", eng)

    database._run_migrations()

    with eng.connect() as conn:
        cols = [r[1] for r in conn.execute(text(
            "PRAGMA table_info(media_sync_status)")).fetchall()]
    assert "douban_cast_cache" in cols
    assert "douban_id" in cols  # 既有迁移仍生效
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && venv/bin/python -m pytest tests/test_series_cast_cache.py -v`
Expected: roundtrip FAIL（`MediaSyncStatus` 无 `douban_cast_cache` 属性）；migration FAIL（`'douban_cast_cache' not in cols`）。

- [ ] **Step 3: 实现**

`backend/models.py` `MediaSyncStatus`（`:166` 后）：

```python
    error_message = Column(Text)
    update_time = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    # ★ 系列级豆瓣 cast 缓存（P3-3b）：{"fetched_at": ISO8601, "cast": {name: {avatar, douban_id, role}}}
    douban_cast_cache = Column(JSON, nullable=True)
```

`backend/database.py` `_run_migrations` media_sync_status 段（`:44-49` 的 for 循环后追加）：

```python
            for col_name in ("tmdb_id", "imdb_id", "douban_id"):
                if col_name not in existing:
                    conn.execute(text(
                        f"ALTER TABLE media_sync_status ADD COLUMN {col_name} VARCHAR"
                    ))
                    logger.info("📦 [Migration] media_sync_status 添加字段: %s", col_name)

            # ★ P3-3b: douban_cast_cache（JSON 缓存列，SQLite 落为 TEXT，与 create_all 一致）
            if "douban_cast_cache" not in existing:
                conn.execute(text(
                    "ALTER TABLE media_sync_status ADD COLUMN douban_cast_cache TEXT"
                ))
                logger.info("📦 [Migration] media_sync_status 添加字段: douban_cast_cache")
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && venv/bin/python -m pytest tests/test_series_cast_cache.py -v`
Expected: 2 PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/models.py backend/database.py backend/tests/test_series_cast_cache.py
git commit -m "feat: MediaSyncStatus 新增 douban_cast_cache JSON 列 + 迁移（P3-3b）"
```

---

### Task 5: `_load_douban_cast` 助手——缓存读/写/新鲜度（3b 核心逻辑）

**Files:**
- Modify: `backend/services/douban_service.py`
  - import `:20`（`from datetime import datetime` → 补 `timedelta`）
  - 常量区 `:82` 后（新增 `_DOUBAN_CAST_TTL_DAYS`）
  - 新增方法 `_load_douban_cast`（放在 `_fetch_douban_actors` `:945` 前）
- Test: `backend/tests/test_series_cast_cache.py`（追加 4 个单测）

**Interfaces:**
- Produces（Task 6 依赖）：
  - `_load_douban_cast(self, series_id: str, douban_id: str) -> list[dict] | None` —— 返回 `[{name, avatar, role, id}]`（与 `_fetch_douban_actors` 形状一致）；缓存新鲜（< 7 天）直接返回（0 请求）；过期/缺失抓一次并回写；抓取失败返回 `None`。
  - `_DOUBAN_CAST_TTL_DAYS = 7`（模块常量）。
- Consumes：`MediaSyncStatus.douban_cast_cache`（Task 4）、`_fetch_douban_actors`、`SessionLocal`。

- [ ] **Step 1: 写失败测试**

追加到 `backend/tests/test_series_cast_cache.py`（文件末尾）：

```python
def _seed_cache(TestSession, series_id="s1", douban_id="123", age_days=0):
    db = TestSession()
    db.add(MediaSyncStatus(
        emby_item_id=series_id, title="九门", status="synced", douban_id=douban_id,
        douban_cast_cache={
            "fetched_at": (datetime.now() - timedelta(days=age_days)).isoformat(timespec="seconds"),
            "cast": {"孙红雷": {"avatar": "http://a/x.jpg", "douban_id": "c1", "role": "主演"}},
        },
    ))
    db.commit()
    db.close()


def test_load_cast_hit_returns_cached_no_fetch(monkeypatch):
    TestSession = _fresh_db(monkeypatch)
    _seed_cache(TestSession)
    s = ds.DoubanSinizer()

    def boom(*a, **k):
        raise AssertionError("缓存命中不应发起 _fetch_douban_actors")
    monkeypatch.setattr(s, "_fetch_douban_actors", boom)

    actors = s._load_douban_cast("s1", "123")
    assert actors == [{"name": "孙红雷", "avatar": "http://a/x.jpg",
                       "role": "主演", "id": "c1"}]


def test_load_cast_expired_refetches_and_rewrites(monkeypatch):
    TestSession = _fresh_db(monkeypatch)
    _seed_cache(TestSession, age_days=8)  # 超过 7 天 → 过期
    s = ds.DoubanSinizer()
    captured = {}
    monkeypatch.setattr(s, "_fetch_douban_actors", lambda did: (
        captured.__setitem__("did", did)
        or [{"name": "新卡", "role": "客串", "avatar": "http://b/y.jpg", "id": "c2"}]
    ))

    actors = s._load_douban_cast("s1", "123")
    assert captured["did"] == "123"
    assert actors[0]["name"] == "新卡"
    # 新缓存已回写
    db = TestSession()
    rec = db.query(MediaSyncStatus).filter(MediaSyncStatus.emby_item_id == "s1").first()
    assert rec.douban_cast_cache["cast"]["新卡"]["avatar"] == "http://b/y.jpg"
    assert rec.douban_cast_cache["fetched_at"] >= (
        datetime.now() - timedelta(seconds=5)).isoformat(timespec="seconds")
    db.close()


def test_load_cast_missing_creates_record_and_writes(monkeypatch):
    TestSession = _fresh_db(monkeypatch)
    s = ds.DoubanSinizer()
    monkeypatch.setattr(s, "_fetch_douban_actors", lambda did: [
        {"name": "陈", "role": "主演", "avatar": "http://c/z.jpg", "id": "c3"},
    ])

    actors = s._load_douban_cast("s1", "123")
    assert actors and actors[0]["name"] == "陈"
    # 无记录 → 自动新建并回写缓存
    db = TestSession()
    rec = db.query(MediaSyncStatus).filter(MediaSyncStatus.emby_item_id == "s1").first()
    assert rec is not None
    assert rec.douban_id == "123"
    assert rec.douban_cast_cache["cast"]["陈"]["douban_id"] == "c3"
    db.close()


def test_load_cast_fetch_failure_returns_none(monkeypatch):
    TestSession = _fresh_db(monkeypatch)
    s = ds.DoubanSinizer()
    monkeypatch.setattr(s, "_fetch_douban_actors", lambda did: [])

    assert s._load_douban_cast("s1", "123") is None
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && venv/bin/python -m pytest tests/test_series_cast_cache.py -v`
Expected: 2 PASS（Task 4）+ 4 FAIL——`AttributeError: 'DoubanSinizer' object has no attribute '_load_douban_cast'`。

- [ ] **Step 3: 实现**

`backend/services/douban_service.py` import（`:20`）：

```python
from datetime import datetime, timedelta
```

常量区（`:82` 后）：

```python
MAX_ACTORS = 999

# ★ 系列级豆瓣 cast 缓存新鲜度（天），与 actor_profile_service 无头像冷却一致（P3-3b）
_DOUBAN_CAST_TTL_DAYS = 7
```

在 `_fetch_douban_actors`（`:945`）之前新增方法：

```python
    def _load_douban_cast(self, series_id: str, douban_id: str) -> list[dict] | None:
        """读取系列级豆瓣 cast：新鲜缓存（<7 天）直接复用（0 次豆瓣请求）。

        缓存 JSON 自包含 fetched_at（不依赖 MediaSyncStatus.update_time，
        后者带 onupdate 每次入库都会被刷新，会导致缓存永不失效）。
        过期/缺失 → 抓取一次并回写缓存（无记录时自动新建）。

        Returns:
            douban actor 列表 [{name, avatar, role, id}]（与 _fetch_douban_actors
            形状一致，供 _build_douban_match_map / _match_and_update 直接使用）；
            抓取失败返回 None。
        """
        # 1) 读缓存
        db = SessionLocal()
        try:
            rec = db.query(MediaSyncStatus).filter(
                MediaSyncStatus.emby_item_id == series_id
            ).first()
            cache = (rec.douban_cast_cache if rec else None) or {}
            fetched_at = cache.get("fetched_at", "")
            cast_map = cache.get("cast", {}) or {}
            if fetched_at and cast_map:
                try:
                    fetched_dt = datetime.fromisoformat(fetched_at)
                    if datetime.now() - fetched_dt < timedelta(days=_DOUBAN_CAST_TTL_DAYS):
                        logger.info(
                            "   💾 [Douban/Cast] cast 缓存命中 (series=%s, %d 位演员, 0 次请求)",
                            series_id, len(cast_map),
                        )
                        return [
                            {
                                "name": name,
                                "avatar": info.get("avatar", ""),
                                "role": info.get("role", ""),
                                "id": str(info.get("douban_id", "") or ""),
                            }
                            for name, info in cast_map.items()
                        ]
                except (ValueError, TypeError):
                    pass  # fetched_at 不可解析 → 视作过期，重新抓取
        finally:
            db.close()

        # 2) 过期/缺失 → 抓取一次
        actors = self._fetch_douban_actors(douban_id)
        if not actors:
            return None

        # 3) 回写缓存（无记录时新建，保持与 douban_id 回写语义一致）
        cast_map = {
            a["name"]: {
                "avatar": a.get("avatar", ""),
                "douban_id": str(a.get("id", "") or ""),
                "role": a.get("role", ""),
            }
            for a in actors
        }
        payload = {
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "cast": cast_map,
        }
        db = SessionLocal()
        try:
            rec = db.query(MediaSyncStatus).filter(
                MediaSyncStatus.emby_item_id == series_id
            ).first()
            if rec is None:
                rec = MediaSyncStatus(emby_item_id=series_id, douban_id=douban_id, status="pending")
                db.add(rec)
            rec.douban_cast_cache = payload
            db.commit()
            logger.info(
                "   💾 [Douban/Cast] cast 缓存已回写 (series=%s, %d 位演员)",
                series_id, len(cast_map),
            )
        finally:
            db.close()
        return actors
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && venv/bin/python -m pytest tests/test_series_cast_cache.py -v`
Expected: 6 PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/services/douban_service.py backend/tests/test_series_cast_cache.py
git commit -m "feat: _load_douban_cast 系列级 cast 缓存读写（P3-3b）"
```

---

### Task 6: 替换两处 `_fetch_douban_actors` 调用为 `_load_douban_cast`（3b 落地）

**Files:**
- Modify: `backend/services/douban_service.py`
  - `sinicize` cast 抓取 `:208`
  - `_resolve_series_douban_context` cast 抓取 `:697`
- Test: `backend/tests/test_series_cast_cache.py`（追加 2 个接线测试）

**Interfaces:**
- Consumes：Task 5 的 `_load_douban_cast(series_id, douban_id)`。
- 行为契约：`sinicize` 与 `_resolve_series_douban_context` 不再直接调 `_fetch_douban_actors`；cast 缓存命中时整条链路 0 次豆瓣请求。

- [ ] **Step 1: 写失败测试**

追加到 `backend/tests/test_series_cast_cache.py`（文件末尾）：

```python
def test_resolve_series_context_uses_fresh_cast_cache(monkeypatch):
    TestSession = _fresh_db(monkeypatch)
    _seed_cache(TestSession, series_id="s1", douban_id="12345")
    s = ds.DoubanSinizer()
    monkeypatch.setattr(s, "_get_emby_item", lambda sid: {
        "Id": sid, "Name": "九门", "Type": "Series",
        "People": [{"Name": "孙红雷", "Type": "Actor", "Role": "主演"}],
        "ProviderIds": {"Imdb": "tt0000001"},
    })

    def boom(*a, **k):
        raise AssertionError("cast 缓存命中不应触发 _fetch_douban_actors")
    monkeypatch.setattr(s, "_fetch_douban_actors", boom)

    ctx = s._resolve_series_douban_context("s1")
    assert ctx is not None
    douban_id, actors, match_map = ctx
    assert douban_id == "12345"            # DB 缓存 douban_id 命中
    assert actors[0]["name"] == "孙红雷"    # cast 缓存命中，未发请求
    assert "sun honglei" in match_map or "孙红雷".lower() in [k for k in match_map]


def test_sinicize_uses_load_douban_cast_not_raw_fetch(monkeypatch):
    """sinicize 走 _load_douban_cast 而非裸 _fetch_douban_actors（异常探针法）。"""
    TestSession = _fresh_db(monkeypatch)
    s = ds.DoubanSinizer()
    monkeypatch.setattr(s, "_get_emby_item", lambda sid: {
        "Id": sid, "Name": "九门", "Type": "Series",
        "People": [{"Name": "Sun Honglei", "Type": "Actor", "Role": "Li Yan"}],
        "ProviderIds": {"Imdb": "tt0000001"},
    })
    monkeypatch.setattr(s, "_find_douban_id", lambda *a, **k: "123")
    captured = {}

    def fake_load(series_id, douban_id):
        captured["series_id"] = series_id
        captured["douban_id"] = douban_id
        raise RuntimeError("LOAD_DOUBAN_CAST_CALLED")
    monkeypatch.setattr(s, "_load_douban_cast", fake_load)

    with pytest.raises(RuntimeError, match="LOAD_DOUBAN_CAST_CALLED"):
        s.sinicize("s1")
    assert captured == {"series_id": "s1", "douban_id": "123"}
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && venv/bin/python -m pytest tests/test_series_cast_cache.py -v`
Expected: 6 PASS（Task 4-5）+ 2 FAIL——`AssertionError: cast 缓存命中不应触发 _fetch_douban_actors` 与 `Failed: DID NOT RAISE`。

- [ ] **Step 3: 实现**

`backend/services/douban_service.py` `sinicize`（`:208`）：

```python
        # 3. 抓取豆瓣演员列表（系列级 cast 缓存：新鲜命中 0 次请求）
        douban_actors = self._load_douban_cast(item_id, douban_id)
        if not douban_actors:
            logger.warning(f"⚠️ [Douban中文化] 豆瓣演员列表为空")
            return result
```

`_resolve_series_douban_context`（`:697`）：

```python
        # 3) 拉 cast + 构建 match_map（系列级 cast 缓存：新鲜命中 0 次请求）
        douban_actors = self._load_douban_cast(series_id, douban_id)
        if not douban_actors:
            logger.warning("⚠ [Douban/Ep] 父 Series %s 豆瓣演员列表为空", series_id)
            return None
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && venv/bin/python -m pytest tests/test_series_cast_cache.py tests/test_light_mode.py tests/test_episode_via_parent.py -v`
Expected: 全部 PASS。随后全量回归：

Run: `cd backend && venv/bin/python -m pytest tests/ -q`
Expected: 全 PASS（既有 13 个测试文件 + 新增 2 个）。

- [ ] **Step 5: Commit**

```bash
git add backend/services/douban_service.py backend/tests/test_series_cast_cache.py
git commit -m "feat: sinicize / _resolve_series_douban_context 接入系列级 cast 缓存（P3-3b）"
```

---

## 验收清单（对照设计文档）

- [ ] 3a：`resolve_actor_profile(light_mode=True)` 跳过 TMDB 上半场（Task 1 测试证明 `fetch_tmdb_person_details` 未被调用）。
- [ ] 3a：`sinicize` 顶层入库 + 分集前置批处理均走 light_mode（Task 3 集成测试断言 `light_profiles=True` / `light_mode=True`）。
- [ ] 3a：演员库路径保持完整漏斗——`ensure_profiles_for_people` / `save_media_to_db` 默认参数为 `False`（Task 2 默认值测试）。
- [ ] 3b：`MediaSyncStatus.douban_cast_cache` 列 + 迁移幂等（Task 4）。
- [ ] 3b：cast 缓存新鲜（<7 天）命中 → 0 次豆瓣请求；过期/缺失 → 抓一次并回写（Task 5 单测）。
- [ ] 3b：`sinicize` 与 `_resolve_series_douban_context` 两处接入（Task 6 接线测试）。
- [ ] 全量 `venv/bin/python -m pytest tests/ -q` 通过。
- [ ] 请求量对照（设计文档）：已汉化 28 人剧集重触发 → 豆瓣 0-2、TMDB 0。

## 不做（YAGNI）

- 不做显式「系列开始→重置预算」生命周期钩子（3c 已用 600s 滑动窗口近似，本计划不追加）。
- 不改 `_fetch_douban_actors` 内部、不改 `_match_and_update` / `_build_douban_match_map`（形状已兼容）。
- 不缓存 douban_id（`_find_douban_id` 结果）——设计文档未列，`_resolve_series_douban_context` 已缓存父 Series douban_id，主 sinicize 入口保持现状。
- 不做 `_write_back_emby` 顶层回写预算接入（3c 范围外，设计未列）。
- 不做 P4 MySQL JSON 类型处理（P4 阶段再做）。
