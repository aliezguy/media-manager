# P1 集数同步与计数正确性 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 Episode webhook 不刷新父 Series 集数（实际 12 集只入库 7 集）、单集汉化按集名搜豆瓣必失败（"第 26 集"）、BatchAudit 摘要用 TMDB 数冒充实际数（30 vs 12）三大正确性问题，并补上"空集检测 + 补齐"能力。

**Architecture:** 新增 `_compute_episode_diff`（纯函数空集/新增检测）+ `reconcile_series_episodes`（Emby 轻量对账 → 补库 → 刷新父 Series 计数）服务函数，webhook 的 Episode 分支改为对账父 Series 并按"内部空集是否存在"决定 sinicize 范围；`DoubanSinizer.sinicize` 顶部拦截 Episode 类型委派到 `_sinicize_episode_via_parent`（复用父 Series 豆瓣 ID + cast）；BatchAudit 摘要改为实际分集数，TMDB 数作括号参考；Series `recursive_item_count` 由分集列表实算。

**Tech Stack:** Python 3.13 / FastAPI / SQLAlchemy / SQLite(:memory: 测试) / pytest / Vue3(vue-tsc 验证)

## Global Constraints

- 缺口对比源是 **Emby 实际分集列表**，不拿 TMDB 期望数当标尺（避免在播剧"永远有缺口"误报）。
- 内部空集缺口判定：缺失分集所在季存在**更高的已入库集号**（`interior = missing 中 e < 该季 DB 最大集号`）。
- 分集 DB 同步统一走 `_process_episodes(... apply_localization=False ...)`，汉化交给 sinicize 层。
- 单集汉化**一律走父 Series**：不再按集名执行 `_find_douban_id`。
- 本计划仅重构后端 + 一处前端文案优先级，不改变已落地 `translation_cache` 设施。
- 测试运行目录：`cd backend`，venv 解释器 `venv/bin/python -m pytest`。

---

### Task 1: 纯函数 `_compute_episode_diff` — 空集/新增检测（TDD）

**Files:**
- Modify: `backend/routers/sync_actions.py`（新增函数，放在 `_fetch_episodes_light` 定义后，约 :1566）
- Test: `backend/tests/test_episode_diff.py`

**Interfaces:**
- Produces: `_compute_episode_diff(db_episodes: list[tuple], emby_episodes: list[tuple]) -> dict`
  - 入参均为 `(season, episode)` 整数元组列表
  - 返回 `{"missing": [(s,e), ...], "interior_gaps": [(s,e), ...]}`，均升序
  - `missing` = Emby 有而 DB 没有；`interior_gaps` = missing 中，该季存在更高已入库集号者

- [ ] **Step 1: 编写失败测试**

创建 `backend/tests/test_episode_diff.py`：
```python
"""_compute_episode_diff 测试 — 空集/新增检测纯函数。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routers.sync_actions import _compute_episode_diff


def test_no_diff():
    db = [(1, 1), (1, 2), (2, 1)]
    emby = [(1, 1), (1, 2), (2, 1)]
    assert _compute_episode_diff(db, emby) == {"missing": [], "interior_gaps": []}


def test_trailing_new_not_interior():
    # DB 有 1-7，Emby 有 1-12 → 8-12 为尾部新增，非内部空集
    db = [(1, i) for i in range(1, 8)]
    emby = [(1, i) for i in range(1, 13)]
    r = _compute_episode_diff(db, emby)
    assert r["missing"] == [(1, 8), (1, 9), (1, 10), (1, 11), (1, 12)]
    assert r["interior_gaps"] == []


def test_interior_hole_detected():
    # DB 有 1、3（缺 2），Emby 有 1-3 → 2 是中间空集
    db = [(1, 1), (1, 3)]
    emby = [(1, 1), (1, 2), (1, 3)]
    r = _compute_episode_diff(db, emby)
    assert r["missing"] == [(1, 2)]
    assert r["interior_gaps"] == [(1, 2)]


def test_new_series_all_trailing():
    # DB 空，Emby 有 1-3 → 全部缺失，无内部空集
    db = []
    emby = [(1, 1), (1, 2), (1, 3)]
    r = _compute_episode_diff(db, emby)
    assert r["missing"] == [(1, 1), (1, 2), (1, 3)]
    assert r["interior_gaps"] == []


def test_multiseason_independent():
    # 季 1 内部缺 2；季 2 尾部新增 5 — 互不影响
    db = [(1, 1), (1, 3), (2, 4)]
    emby = [(1, 1), (1, 2), (1, 3), (2, 4), (2, 5)]
    r = _compute_episode_diff(db, emby)
    assert r["interior_gaps"] == [(1, 2)]
    assert r["missing"] == [(1, 2), (2, 5)]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && venv/bin/python -m pytest tests/test_episode_diff.py -v`
Expected: FAIL（`ImportError: cannot import name '_compute_episode_diff'`）

- [ ] **Step 3: 实现 `_compute_episode_diff`**

在 `backend/routers/sync_actions.py` 的 `_fetch_episodes_light` 定义（:1566）之后追加：
```python
def _compute_episode_diff(
    db_episodes: list, emby_episodes: list,
) -> dict:
    """对比 DB 与 Emby 的分集集合，检测新增集与内部空集缺口。

    缺口语义：Emby 有而 DB 没有的分集（missing）；其中"该季存在更高已入库集号"
    的分集视为内部空集（interior_gaps，中间空洞）。尾部新增集（全部高于该季
    DB 最大集号）不是内部空集，不触发整体汉化，仅补库。

    Args:
        db_episodes:   DB 已有分集的 [(season, episode)] 元组列表
        emby_episodes: Emby 实际分集的 [(season, episode)] 元组列表

    Returns:
        {"missing": [(s,e), ...], "interior_gaps": [(s,e), ...]}（均升序）
    """
    db_set = set(db_episodes)
    emby_set = set(emby_episodes)
    missing = sorted(emby_set - db_set)

    # 每季 DB 已存在的最大集号（用于判定中间空洞）
    db_max: dict[int, int] = {}
    for season, ep in db_set:
        db_max[season] = max(db_max.get(season, 0), ep)

    interior = sorted(
        (s, e) for (s, e) in missing if e < db_max.get(s, -1)
    )
    return {"missing": missing, "interior_gaps": interior}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && venv/bin/python -m pytest tests/test_episode_diff.py -v`
Expected: PASS（全部通过）

- [ ] **Step 5: 提交**

```bash
git add backend/routers/sync_actions.py backend/tests/test_episode_diff.py
git commit -m "feat: 新增分集空集/新增检测纯函数 _compute_episode_diff"
```

---

### Task 2: 对账服务 `reconcile_series_episodes`（补库 + 刷新计数）（TDD）

**Files:**
- Modify: `backend/routers/sync_actions.py`（新增函数，放在 `_compute_episode_diff` 之后）
- Test: `backend/tests/test_series_reconcile.py`

**Interfaces:**
- Consumes: `_compute_episode_diff`（Task 1）、`_fetch_episodes_light`、`_process_episodes`、`load_config`、`SessionLocal`、`MediaMetadata`（均在 sync_actions.py 模块作用域）
- Produces: `reconcile_series_episodes(series_id, host="", api_key="", user_id="", library_id="") -> dict`
  - 返回 `{"success", "episodes_total", "synced_episodes", "interior_gaps", "full_sync"}`
  - `full_sync` = 是否存在内部空集（调用方据此决定整体汉化 vs 单集汉化）

- [ ] **Step 1: 编写失败测试**

创建 `backend/tests/test_series_reconcile.py`：
```python
"""reconcile_series_episodes 测试 — 轻量对账 + 补库 + 计数刷新。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base
from models import MediaMetadata, MediaSyncStatus
import routers.sync_actions as sa


def _make_session(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    monkeypatch.setattr(sa, "SessionLocal", TestSession)
    return TestSession


def _mock_env(monkeypatch):
    monkeypatch.setattr(sa, "load_config", lambda: {
        "emby_host": "http://emby.test",
        "emby_api_key": "k",
        "emby_user_id": "u",
    })


def test_interior_gap_triggers_full_sync(monkeypatch):
    TestSession = _make_session(monkeypatch)
    _mock_env(monkeypatch)
    # Emby 有 S1E1..E3；DB 预置 Series + E1 + E3（缺 E2 → 中间空集）
    monkeypatch.setattr(sa, "_fetch_episodes_light", lambda *a, **k: [
        {"Id": "e1", "Name": "E1", "ParentIndexNumber": 1, "IndexNumber": 1, "ProviderIds": {}},
        {"Id": "e2", "Name": "E2", "ParentIndexNumber": 1, "IndexNumber": 2, "ProviderIds": {}},
        {"Id": "e3", "Name": "E3", "ParentIndexNumber": 1, "IndexNumber": 3, "ProviderIds": {}},
    ])
    db = TestSession()
    db.add(MediaSyncStatus(emby_item_id="s1", title="Test", status="synced"))
    db.add(MediaMetadata(emby_item_id="s1", parent_id=None, media_type="Series", title="Test"))
    db.add(MediaMetadata(emby_item_id="e1", parent_id="s1", media_type="Episode", title="E1",
                         index_number=1, parent_index_number=1))
    db.add(MediaMetadata(emby_item_id="e3", parent_id="s1", media_type="Episode", title="E3",
                         index_number=3, parent_index_number=1))
    db.commit(); db.close()

    result = sa.reconcile_series_episodes("s1", library_id="lib1")
    assert result["success"] is True
    assert result["episodes_total"] == 3
    assert result["interior_gaps"] == [(1, 2)]
    assert result["full_sync"] is True
    assert result["synced_episodes"] == 3  # 内部空集 → 全量同步一次

    db = TestSession()
    eps = db.query(MediaMetadata).filter(
        MediaMetadata.parent_id == "s1", MediaMetadata.media_type == "Episode").all()
    assert {e.index_number for e in eps} == {1, 2, 3}
    series = db.query(MediaMetadata).filter(MediaMetadata.emby_item_id == "s1").first()
    assert series.recursive_item_count == 3  # 计数已实算刷新
    db.close()


def test_trailing_new_light_sync(monkeypatch):
    TestSession = _make_session(monkeypatch)
    _mock_env(monkeypatch)
    # DB 有 E1..E2；Emby 新增 E3 → 仅补 E3，不触发整体汉化
    monkeypatch.setattr(sa, "_fetch_episodes_light", lambda *a, **k: [
        {"Id": "e1", "Name": "E1", "ParentIndexNumber": 1, "IndexNumber": 1, "ProviderIds": {}},
        {"Id": "e2", "Name": "E2", "ParentIndexNumber": 1, "IndexNumber": 2, "ProviderIds": {}},
        {"Id": "e3", "Name": "E3", "ParentIndexNumber": 1, "IndexNumber": 3, "ProviderIds": {}},
    ])
    db = TestSession()
    db.add(MediaSyncStatus(emby_item_id="s1", title="Test", status="synced"))
    db.add(MediaMetadata(emby_item_id="s1", parent_id=None, media_type="Series", title="Test"))
    db.add(MediaMetadata(emby_item_id="e1", parent_id="s1", media_type="Episode", title="E1",
                         index_number=1, parent_index_number=1))
    db.add(MediaMetadata(emby_item_id="e2", parent_id="s1", media_type="Episode", title="E2",
                         index_number=2, parent_index_number=1))
    db.commit(); db.close()

    result = sa.reconcile_series_episodes("s1", library_id="lib1")
    assert result["success"] is True
    assert result["episodes_total"] == 3
    assert result["interior_gaps"] == []
    assert result["full_sync"] is False
    assert result["synced_episodes"] == 1  # 仅补 E3

    db = TestSession()
    eps = db.query(MediaMetadata).filter(
        MediaMetadata.parent_id == "s1", MediaMetadata.media_type == "Episode").all()
    assert {e.index_number for e in eps} == {1, 2, 3}
    db.close()


def test_no_diff_skips_sync(monkeypatch):
    TestSession = _make_session(monkeypatch)
    _mock_env(monkeypatch)
    monkeypatch.setattr(sa, "_fetch_episodes_light", lambda *a, **k: [
        {"Id": "e1", "Name": "E1", "ParentIndexNumber": 1, "IndexNumber": 1, "ProviderIds": {}},
    ])
    db = TestSession()
    db.add(MediaSyncStatus(emby_item_id="s1", title="Test", status="synced"))
    db.add(MediaMetadata(emby_item_id="s1", parent_id=None, media_type="Series", title="Test"))
    db.add(MediaMetadata(emby_item_id="e1", parent_id="s1", media_type="Episode", title="E1",
                         index_number=1, parent_index_number=1))
    db.commit(); db.close()

    result = sa.reconcile_series_episodes("s1", library_id="lib1")
    assert result["success"] is True
    assert result["episodes_total"] == 1
    assert result["synced_episodes"] == 0
    assert result["full_sync"] is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && venv/bin/python -m pytest tests/test_series_reconcile.py -v`
Expected: FAIL（`AttributeError: module 'routers.sync_actions' has no attribute 'reconcile_series_episodes'`）

- [ ] **Step 3: 实现 `reconcile_series_episodes`**

在 `_compute_episode_diff` 之后追加：
```python
def reconcile_series_episodes(
    series_id: str, host: str = "", api_key: str = "",
    user_id: str = "", library_id: str = "",
) -> dict:
    """轻量对账 Series 分集：拉 Emby 列表 → 对比 DB → 补库 → 刷新计数。

    供 webhook（新增分集）调用，解决「实际 12 集只入库 7 集」。
    - 只拉轻量字段（_fetch_episodes_light），避免重复抓取大字段
    - 内部空集缺口 → 全量同步一次该剧（_process_episodes 全量）
    - 仅尾部新增 → 只补缺失分集，不重扫已入库分集
    - 无论哪种，均用 Emby 实际分集数刷新父 Series recursive_item_count

    Returns:
        {"success": bool, "episodes_total": int, "synced_episodes": int,
         "interior_gaps": list, "full_sync": bool}
    """
    cfg = load_config()
    host = host or cfg.get("emby_host", "").rstrip("/")
    api_key = api_key or cfg.get("emby_api_key", "")
    user_id = user_id or cfg.get("emby_user_id", "")

    empty = {"success": False, "episodes_total": 0, "synced_episodes": 0,
             "interior_gaps": [], "full_sync": False}
    if not host or not api_key:
        logger.warning("⚠ [Reconcile] Emby 未配置，跳过 %s 对账", series_id)
        return empty

    emby_eps = _fetch_episodes_light(host, api_key, user_id, series_id)
    if not emby_eps:
        logger.warning("⚠ [Reconcile] %s 无分集数据（Emby 未找到或为空）", series_id)
        return empty

    db = SessionLocal()
    try:
        db_eps = [
            (r.parent_index_number or 0, r.index_number or 0)
            for r in db.query(MediaMetadata).filter(
                MediaMetadata.parent_id == series_id,
                MediaMetadata.media_type == "Episode",
            ).all()
        ]
        diff = _compute_episode_diff(
            db_eps,
            [(ep.get("ParentIndexNumber") or 0, ep.get("IndexNumber") or 0) for ep in emby_eps],
        )

        # ★ 补库：内部空集 → 全量同步一次；仅尾部新增 → 只补缺失分集
        if diff["interior_gaps"]:
            synced = _process_episodes(
                db, emby_eps, series_id, library_id,
                apply_localization=False, douban_actor_map=None, series_name="",
            )
            logger.info(
                "   📺 [Reconcile] %s 检测到内部空集 %s，全量同步 %d 个分集",
                series_id, diff["interior_gaps"], synced,
            )
        elif diff["missing"]:
            missing_eps = [
                ep for ep in emby_eps
                if (ep.get("ParentIndexNumber") or 0, ep.get("IndexNumber") or 0) in diff["missing"]
            ]
            synced = _process_episodes(
                db, missing_eps, series_id, library_id,
                apply_localization=False, douban_actor_map=None, series_name="",
            )
            logger.info(
                "   📺 [Reconcile] %s 补充 %d 个新增分集", series_id, synced,
            )
        else:
            synced = 0

        # ★ 用 Emby 实际分集数刷新父 Series 计数（不信任 stale RecursiveItemCount）
        series_mm = db.query(MediaMetadata).filter(
            MediaMetadata.emby_item_id == series_id
        ).first()
        if series_mm:
            series_mm.recursive_item_count = len(emby_eps)
        db.commit()

        return {
            "success": True,
            "episodes_total": len(emby_eps),
            "synced_episodes": synced,
            "interior_gaps": diff["interior_gaps"],
            "full_sync": bool(diff["interior_gaps"]),
        }
    except Exception:
        db.rollback()
        logger.error(
            "❌ [Reconcile] %s 对账异常:\n%s", series_id, traceback.format_exc(),
        )
        return empty
    finally:
        db.close()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && venv/bin/python -m pytest tests/test_series_reconcile.py -v`
Expected: PASS（全部通过）

- [ ] **Step 5: 提交**

```bash
git add backend/routers/sync_actions.py backend/tests/test_series_reconcile.py
git commit -m "feat: 新增 Series 分集对账 reconcile_series_episodes（补库+刷计数）"
```

---

### Task 3: 单集汉化走父 Series — `_sinicize_episode_via_parent` + `_resolve_series_douban_context`

**Files:**
- Modify: `backend/services/douban_service.py`
  - `sinicize` 顶部（:157 `series_library_id` 赋值后）插入 Episode 委派
  - 两个新方法追加到 `sinicize` 之后（`_get_emby_item` 之前，:630）
- Test: `backend/tests/test_episode_via_parent.py`

**Interfaces:**
- Consumes: `_get_emby_item`(:630)、`_find_douban_id`(:653)、`_fetch_douban_actors`(:767)、`_build_douban_match_map`(:1834)、`_localize_episode_people`(:1562)、`_write_back_episode`(:1898)、`save_media_to_db`/`extract_provider_ids`（已导入 :27）、`SessionLocal`（已导入 :26）
- Produces:
  - `_sinicize_episode_via_parent(ep_item_id: str, ep_item: dict) -> dict` — 返回与 `sinicize` 相同结构 `{"success","matched","total_actors","details"}`
  - `_resolve_series_douban_context(series_id: str) -> Optional[tuple]` — 返回 `(douban_id, douban_actors, douban_match_map)` 或 None
  - 模块内需新增 import：`from models import MediaMetadata, MediaSyncStatus`（当前 douban_service 未导入）

- [ ] **Step 1: 编写失败测试**

创建 `backend/tests/test_episode_via_parent.py`：
```python
"""单集汉化走父 Series 测试 — 委派钩子 + 上下文解析。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base
from models import MediaSyncStatus
import services.douban_service as ds


def _fresh_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    monkeypatch.setattr(ds, "SessionLocal", TestSession)
    # DoubanSinizer.__init__ 读取 emby 配置，测试环境补齐避免 sinicize 早退
    monkeypatch.setattr(ds, "load_config", lambda: {
        "emby_host": "http://emby.test",
        "emby_api_key": "k",
        "emby_user_id": "u",
        "max_actors_per_media": 50,
    })
    return TestSession


def test_sinicize_episode_delegates(monkeypatch):
    _fresh_db(monkeypatch)  # 补齐 emby 配置，防止 sinicize 早退
    s = ds.DoubanSinizer()
    captured = {}
    def fake_get(item_id):
        return {"Id": item_id, "Name": "第 26 集", "Type": "Episode",
                "SeriesId": "s1", "People": [{"Name": "A", "Type": "Actor"}]}
    def fake_via_parent(ep_id, ep):
        captured["ep_id"] = ep_id
        return {"success": True, "matched": 1, "total_actors": 1, "details": []}
    monkeypatch.setattr(s, "_get_emby_item", fake_get)
    monkeypatch.setattr(s, "_sinicize_episode_via_parent", fake_via_parent)

    result = s.sinicize("e26")
    assert captured["ep_id"] == "e26"
    assert result["success"] is True


def test_resolve_series_context_db_cache_hit(monkeypatch):
    TestSession = _fresh_db(monkeypatch)
    db = TestSession()
    db.add(MediaSyncStatus(emby_item_id="s1", title="九门", status="synced", douban_id="12345"))
    db.commit(); db.close()

    s = ds.DoubanSinizer()
    monkeypatch.setattr(s, "_get_emby_item", lambda sid: {
        "Id": sid, "Name": "九门", "Type": "Series",
        "People": [{"Name": "Chen", "Type": "Actor", "Role": "Lead"}],
        "ProviderIds": {"Imdb": "tt0000001"},
    })
    monkeypatch.setattr(s, "_find_douban_id", lambda *a, **k: "99999")  # 不应被调用
    monkeypatch.setattr(s, "_fetch_douban_actors", lambda did: [
        {"name": "陈", "role": "主演", "avatar": "", "id": "c1"},
    ])

    ctx = s._resolve_series_douban_context("s1")
    assert ctx is not None
    douban_id, actors, match_map = ctx
    assert douban_id == "12345"  # DB 缓存命中，未触发 find
    assert actors[0]["name"] == "陈"


def test_resolve_series_context_finds_and_writes_back(monkeypatch):
    TestSession = _fresh_db(monkeypatch)
    db = TestSession()
    db.add(MediaSyncStatus(emby_item_id="s1", title="九门", status="synced"))  # 无 douban_id
    db.commit(); db.close()

    s = ds.DoubanSinizer()
    monkeypatch.setattr(s, "_get_emby_item", lambda sid: {
        "Id": sid, "Name": "九门", "Type": "Series",
        "People": [{"Name": "Chen", "Type": "Actor", "Role": "Lead"}],
        "ProviderIds": {"Imdb": "tt0000001"},
    })
    monkeypatch.setattr(s, "_find_douban_id", lambda pids, title, mtype, year: "12345")
    monkeypatch.setattr(s, "_fetch_douban_actors", lambda did: [
        {"name": "陈", "role": "主演", "avatar": "", "id": "c1"},
    ])

    ctx = s._resolve_series_douban_context("s1")
    assert ctx is not None
    assert ctx[0] == "12345"
    # 已回写 DB 缓存
    db = TestSession()
    rec = db.query(MediaSyncStatus).filter(MediaSyncStatus.emby_item_id == "s1").first()
    assert rec.douban_id == "12345"
    db.close()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && venv/bin/python -m pytest tests/test_episode_via_parent.py -v`
Expected: FAIL（`AttributeError: ... has no attribute '_sinicize_episode_via_parent'` 等）

- [ ] **Step 3: 新增 import + 委派钩子 + 两个新方法**

**Step 3a**: 在 `backend/services/douban_service.py` 顶部 import 区（:27 `from services.db_crud import ...` 后）追加：
```python
from models import MediaMetadata, MediaSyncStatus
```

**Step 3b**: 在 `sinicize`（:132-624）的 `series_library_id = item_data.get("ParentId", "") or ""`（:157）之后插入委派钩子：
```python
        item_type = item_data.get("Type", "")
        # ★ 单集一律走父 Series：不再按集名搜豆瓣（Frodo search 不索引单集页）
        if item_type == "Episode":
            logger.info(
                "   📺 [Douban/Ep] %s 为单集，走父 Series 定位汉化", item_id,
            )
            return self._sinicize_episode_via_parent(item_id, item_data)
```

> 注：`sinicize` 后续原有 `item_type = item_data.get("Type", "")`（:401）保留不变，Episode 分支提前 return 后不会执行到。

**Step 3c**: 在 `sinicize` 结束（:624 后、`_get_emby_item` 前）追加两个方法：
```python
    def _resolve_series_douban_context(self, series_id: str):
        """解析父 Series 的豆瓣上下文：优先 DB 缓存，无则系列级查找并回写。

        Returns:
            (douban_id, douban_actors, douban_match_map) 或 None
        """
        series_item = self._get_emby_item(series_id)
        if not series_item:
            logger.warning("⚠ [Douban/Ep] 父 Series %s 读取失败", series_id)
            return None
        series_name = series_item.get("Name", "")
        series_actors = [
            p for p in (series_item.get("People", []) or []) if p.get("Type") == "Actor"
        ]

        # 1) 优先 DB 缓存 douban_id
        douban_id = ""
        db_q = SessionLocal()
        try:
            rec = db_q.query(MediaSyncStatus).filter(
                MediaSyncStatus.emby_item_id == series_id
            ).first()
            if rec and rec.douban_id:
                douban_id = rec.douban_id
        finally:
            db_q.close()

        # 2) 无缓存 → 做一次系列级查找并回写 DB
        if not douban_id:
            pids = series_item.get("ProviderIds", {}) or {}
            douban_id = self._find_douban_id(
                pids, title=series_name, mtype="Series",
                year=str(series_item.get("ProductionYear", "")),
            )
            if not douban_id:
                logger.warning(
                    "⚠ [Douban/Ep] 父 Series %s 无法定位豆瓣条目，跳过", series_id,
                )
                return None
            db_w = SessionLocal()
            try:
                rec = db_w.query(MediaSyncStatus).filter(
                    MediaSyncStatus.emby_item_id == series_id
                ).first()
                if rec:
                    rec.douban_id = douban_id
                    rec.update_time = datetime.now()
                db_w.commit()
            finally:
                db_w.close()

        # 3) 拉 cast + 构建 match_map
        douban_actors = self._fetch_douban_actors(douban_id)
        if not douban_actors:
            logger.warning("⚠ [Douban/Ep] 父 Series %s 豆瓣演员列表为空", series_id)
            return None
        douban_match_map = self._build_douban_match_map(douban_actors, series_actors)
        return (douban_id, douban_actors, douban_match_map)

    def _sinicize_episode_via_parent(self, ep_item_id: str, ep_item: dict) -> dict:
        """单集汉化一律走父 Series：定位父系列豆瓣页 → 用系列 cast 本地化本集。

        豆瓣 Frodo search 不索引单集页（"第 26 集"这类泛化集名无法按集名搜到），
        因此单集被直接汉化时不再按集名找豆瓣条目。父系列豆瓣上下文解析失败时
        降级为 AI-only 本地化本集（不崩溃）。

        Returns:
            {"success", "matched", "total_actors", "details"}
        """
        result = {"success": False, "matched": 0, "total_actors": 0, "details": []}

        # 1. 定位父 Series：Emby SeriesId → ParentId → DB parent_id 兜底
        series_id = str(ep_item.get("SeriesId") or "").strip()
        if not series_id:
            series_id = str(ep_item.get("ParentId") or "").strip()
        if not series_id:
            db_q = SessionLocal()
            try:
                mm = db_q.query(MediaMetadata).filter(
                    MediaMetadata.emby_item_id == ep_item_id
                ).first()
                series_id = (mm.parent_id or "") if mm else ""
            finally:
                db_q.close()
        if not series_id:
            logger.warning("⚠ [Douban/Ep] %s 无法定位父 Series，跳过", ep_item_id)
            return result

        ep_people = [
            p for p in (ep_item.get("People", []) or [])
            if p.get("Type") in ("Actor", "GuestStar")
        ]
        if not ep_people:
            logger.warning("⚠ [Douban/Ep] %s 无演员数据", ep_item_id)
            return result
        result["total_actors"] = len(ep_people)

        # 2. 解析父 Series 豆瓣上下文（ID + cast + match_map）
        ctx = self._resolve_series_douban_context(series_id)
        douban_match_map = ctx[2] if ctx else {}
        if not ctx:
            logger.warning(
                "⚠ [Douban/Ep] 父 Series %s 无豆瓣上下文，本集降级为 AI-only 本地化",
                series_id,
            )

        # 3. 本地化本集演员（含缓存拦截 + AI 兜底；无豆瓣上下文时空 map 走 AI-only）
        db = SessionLocal()
        try:
            localized = self._localize_episode_people(
                ep_people, douban_match_map,
                series_name=ep_item.get("SeriesName", ""),
                db=db, emby_item_id=ep_item_id, parent_id=series_id,
            )
            localized = _truncate_actors(localized, self.max_actors_per_media)

            # 4. 回写 Emby（仅当有变更）
            if localized != ep_people:
                if not self._write_back_episode(ep_item_id, ep_item, localized):
                    logger.warning("⚠ [Douban/Ep] %s 回写 Emby 失败", ep_item_id)
                    return result

            # 5. 入库（actor_records + 置信度，skip_profiles=True 复用父系列漏斗结果）
            ep_pids = extract_provider_ids(ep_item)
            chinese_ep, total_ep = _count_chinese_roles_ep(localized)
            ep_status = "synced" if _is_chinese_role_synced_ep(localized) else "pending"
            save_media_to_db(
                db,
                emby_item=ep_item,
                provider_ids=ep_pids,
                images=None,
                people=localized,
                library_id=ep_item.get("ParentId", "") or "",
                status=ep_status,
                matched_actors=chinese_ep,
                total_actors=total_ep,
                parent_id=series_id,
                skip_profiles=True,
            )
            db.commit()

            result["success"] = True
            result["matched"] = sum(
                1 for p in localized
                if is_valid_chinese_translation(p.get("Name", ""))
            )
            result["details"] = [
                {"name": p.get("Name", ""), "role": p.get("Role", "")} for p in localized
            ]
            return result
        except Exception:
            db.rollback()
            logger.error(
                "❌ [Douban/Ep] %s 处理异常:\n%s", ep_item_id, traceback.format_exc(),
            )
            return result
        finally:
            db.close()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && venv/bin/python -m pytest tests/test_episode_via_parent.py -v`
Expected: PASS（全部通过）

- [ ] **Step 5: 导入冒烟**

Run: `cd backend && venv/bin/python -c "from services import douban_service; from routers import sync_actions; print('OK')"`
Expected: `OK`

- [ ] **Step 6: 提交**

```bash
git add backend/services/douban_service.py backend/tests/test_episode_via_parent.py
git commit -m "feat: 单集汉化一律走父 Series 定位（_sinicize_episode_via_parent）"
```

---

### Task 4: webhook Episode 分支 — 对账父 Series + sinicize 范围决策

**Files:**
- Modify: `backend/routers/emby.py` `_handle_library_new_for_sinicize`（:776-830）
- Test: `backend/tests/test_webhook_episode.py`

**Interfaces:**
- Consumes: `reconcile_series_episodes`（Task 2）、`DoubanSinizer`（已局部导入 :783）
- Produces: Episode 事件下按 `reconcile` 结果决定汉化目标（series_id 整体 / item_id 单集），并保留 Series/Movie 原路径

- [ ] **Step 1: 编写失败测试**

创建 `backend/tests/test_webhook_episode.py`：
```python
"""webhook Episode 分支测试 — 对账父 Series + 决策汉化目标。"""
import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import routers.emby as emby


def _run(handler, payload):
    asyncio.run(handler(payload))


def test_episode_full_sync_targets_series(monkeypatch):
    targets = []
    monkeypatch.setattr(emby, "reconcile_series_episodes", lambda sid: {
        "success": True, "episodes_total": 12, "synced_episodes": 3,
        "interior_gaps": [(1, 8)], "full_sync": True,
    })
    monkeypatch.setattr(emby, "DoubanSinizer", lambda: type("S", (), {
        "sinicize": lambda self, tid: targets.append(tid) or {"success": True},
    })())
    _run(emby._handle_library_new_for_sinicize, {
        "Event": "library.new",
        "Item": {"Id": "e8", "Name": "E8", "Type": "Episode", "SeriesId": "s1"},
    })
    assert targets == ["s1"]  # 内部空集 → 整体汉化父 Series


def test_episode_light_targets_episode(monkeypatch):
    targets = []
    monkeypatch.setattr(emby, "reconcile_series_episodes", lambda sid: {
        "success": True, "episodes_total": 8, "synced_episodes": 1,
        "interior_gaps": [], "full_sync": False,
    })
    monkeypatch.setattr(emby, "DoubanSinizer", lambda: type("S", (), {
        "sinicize": lambda self, tid: targets.append(tid) or {"success": True},
    })())
    _run(emby._handle_library_new_for_sinicize, {
        "Event": "library.new",
        "Item": {"Id": "e8", "Name": "E8", "Type": "Episode", "SeriesId": "s1"},
    })
    assert targets == ["e8"]  # 仅尾部新增 → 汉化本单集


def test_series_keeps_original_path(monkeypatch):
    targets = []
    monkeypatch.setattr(emby, "_ensure_item_audited", lambda iid: True)
    monkeypatch.setattr(emby, "DoubanSinizer", lambda: type("S", (), {
        "sinicize": lambda self, tid: targets.append(tid) or {"success": True},
    })())
    _run(emby._handle_library_new_for_sinicize, {
        "Event": "library.new",
        "Item": {"Id": "s1", "Name": "九门", "Type": "Series"},
    })
    assert targets == ["s1"]
```

> 前提：`_handle_library_new_for_sinicize` 原使用函数内局部导入（`_ensure_item_audited`、`DoubanSinizer`），测试无法 monkeypatch。已确认无循环导入风险（grep 验证：services/sync_actions/douban_service 均不 import `routers.emby`），故本 Task 将这两个依赖提升为 emby.py **模块级 import**（Step 3a），使测试可拦截。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && venv/bin/python -m pytest tests/test_webhook_episode.py -v`
Expected: FAIL（`AttributeError: module 'routers.emby' has no attribute 'reconcile_series_episodes'`）

- [ ] **Step 3a: 提升模块级 import**

在 `backend/routers/emby.py` 顶部 import 区（:19 `from services.emby_service import ...` 之后）追加：
```python
# 供 _handle_library_new_for_sinicize 使用（模块级 import 以便测试 monkeypatch）
from routers.sync_actions import _ensure_item_audited, reconcile_series_episodes
from services.douban_service import DoubanSinizer
```

- [ ] **Step 3b: 替换 `_handle_library_new_for_sinicize`**

将 `backend/routers/emby.py` 中 :776-830 的整个函数替换为：
```python
async def _handle_library_new_for_sinicize(payload: dict):
    """Background task: 新入库媒体自动触发审计 + 汉化。

    从 Emby Webhook (item.created / library.new) 提取 item_id，
    依次执行前置审计和演员中文化，实现"新增即汉化"的实时响应。

    ★ Episode 分支：不再只审计单集，改为轻量对账父 Series——
      内部空集 → 整体汉化父 Series；仅尾部新增 → 汉化本单集（走父系列定位）。
    """
    item = payload.get("Item", {})
    item_id = item.get("Id", "")
    item_name = item.get("Name", "?")
    item_type = item.get("Type", "")

    if not item_id:
        logger.warning("⚠ [AutoSinicize] Webhook payload 中缺少 Item.Id，跳过")
        return

    logger.info(
        "🚀 [AutoSinicize] 收到 Emby 事件: %s — %s (%s, type=%s)",
        payload.get("Event", "?"), item_name, item_id, item_type,
    )

    sinizer = DoubanSinizer()
    try:
        # ★ Episode：轻量对账父 Series
        if item_type == "Episode":
            series_id = item.get("SeriesId") or item.get("ParentId") or ""
            if series_id:
                rec = reconcile_series_episodes(series_id)
                if rec.get("success"):
                    if rec.get("full_sync"):
                        logger.info(
                            "   📺 [AutoSinicize] %s 检测到内部空集缺口 %s，整体汉化父 Series",
                            item_name, rec.get("interior_gaps"),
                        )
                        sinizer.sinicize(series_id)
                    else:
                        logger.info(
                            "   🎬 [AutoSinicize] %s 对账完成(共 %d 集)，汉化本单集",
                            item_name, rec.get("episodes_total", 0),
                        )
                        sinizer.sinicize(item_id)
                    return
                logger.warning(
                    "   ⚠ [AutoSinicize] %s 父系列对账失败，退回单集审计", item_name,
                )
            else:
                logger.warning(
                    "   ⚠ [AutoSinicize] %s 无法定位父 Series，退回单集审计", item_name,
                )

        # ★ 非分集 / Episode 兜底：前置审计 + 汉化
        if not _ensure_item_audited(item_id):
            logger.warning(
                "   ⚠ [AutoSinicize] %s 审计后仍无演员数据（Emby 未刮削），跳过汉化",
                item_name,
            )
            return

        logger.info("   🎬 [AutoSinicize] Step 2/2: 执行汉化 %s", item_id)
        result = sinizer.sinicize(item_id)

        if result.get("success"):
            logger.info(
                "   ✅ [AutoSinicize] %s 汉化完成: matched=%d/%d",
                item_name,
                result.get("matched", 0),
                result.get("total_actors", 0),
            )
        else:
            logger.warning(
                "   ⚠ [AutoSinicize] %s 汉化未成功（可能无豆瓣条目或无演员数据）",
                item_name,
            )

    except Exception as e:
        logger.error(
            "❌ [AutoSinicize] %s 处理异常: %s\n%s",
            item_name, e, traceback.format_exc(),
        )
```

> 说明：`reconcile_series_episodes` 保持函数内局部导入（与现 `_ensure_item_audited` 同模式）。测试通过 monkeypatch 局部导入无法生效，故测试采用 **Step 4 的替代验证**：直接调用 `reconcile_series_episodes` 的行为已在 Task 2 单测覆盖；本 Task 的验证以导入冒烟 + 真实 webhook 日志为主（执行时人工确认）。

- [ ] **Step 4: 运行测试 + 导入冒烟**

Run: `cd backend && venv/bin/python -m pytest tests/test_webhook_episode.py -v`
Expected: PASS（全部通过）

Run: `cd backend && venv/bin/python -c "from routers import emby; print('OK')"`
Expected: `OK`

- [ ] **Step 5: 提交**

```bash
git add backend/routers/emby.py backend/tests/test_webhook_episode.py
git commit -m "feat: webhook Episode 分支改为对账父 Series 并决策汉化范围"
```

---

### Task 5: BatchAudit 摘要改用实际分集数（修复 30 vs 12）

**Files:**
- Modify: `backend/routers/sync_actions.py`
  - 新增纯函数 `_build_batch_audit_summary`（放在 `reconcile_series_episodes` 之后）
  - `_batch_audit_task` Phase 1 收集 `episodes_actual`（:1705-1709）
  - `_batch_audit_task` Phase 2 摘要与单季日志（:1892-1929）
- Test: `backend/tests/test_batch_audit_summary.py`

**Interfaces:**
- Consumes: Phase 1 `result["episodes_processed"]`（`_sync_and_audit_single_item` 已返回）
- Produces: `_build_batch_audit_summary(total_scanned, total_synced, n_series, n_seasons, total_eps_actual, total_eps_enriched, total_guest_stars) -> str`

- [ ] **Step 1: 编写失败测试**

创建 `backend/tests/test_batch_audit_summary.py`：
```python
"""_build_batch_audit_summary 测试 — 摘要以实际分集数为准。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routers.sync_actions import _build_batch_audit_summary


def test_actual_equals_tmdb():
    msg = _build_batch_audit_summary(1, 1, 1, 1, 12, 12, 5)
    assert "分集 12 集" in msg
    assert "TMDB" not in msg


def test_actual_differs_tmdb_shows_reference():
    msg = _build_batch_audit_summary(1, 1, 1, 1, 12, 30, 8)
    assert "分集 12 集（TMDB 30）" in msg


def test_zero_eps():
    msg = _build_batch_audit_summary(3, 2, 0, 0, 0, 0, 0)
    assert "分集 0 集" in msg
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && venv/bin/python -m pytest tests/test_batch_audit_summary.py -v`
Expected: FAIL（`ImportError: cannot import name '_build_batch_audit_summary'`）

- [ ] **Step 3: 实现 `_build_batch_audit_summary` + 接线**

**Step 3a**: 在 `reconcile_series_episodes`（Task 2）之后追加：
```python
def _build_batch_audit_summary(
    total_scanned: int, total_synced: int,
    n_series: int, n_seasons: int,
    total_eps_actual: int, total_eps_enriched: int,
    total_guest_stars: int,
) -> str:
    """BatchAudit 最终摘要 — 分集数以【实际 Emby 入库数】为准，TMDB 数作括号参考。

    修复：原摘要用 TMDB 整季 episodes 数（含未播出）冒充实际数（30 vs 12）。
    """
    eps_part = f"分集 {total_eps_actual} 集"
    if total_eps_enriched != total_eps_actual:
        eps_part += f"（TMDB {total_eps_enriched}）"
    return (
        f"✅ 审计完成: {total_scanned} 项 | 已汉化 {total_synced} 项 | "
        f"{n_series} 部剧集 / {n_seasons} 季 | {eps_part} | "
        f"客串演员 {total_guest_stars} 位"
    )
```

**Step 3b**: `_batch_audit_task` Phase 1，`series_queue.append({...})`（:1705-1709）增加：
```python
                            series_queue.append({
                                "item_id": item_id,
                                "tmdb_id": result["tmdb_id"],
                                "name": result["item_name"],
                                # ★ 实际 Emby 分集数（Phase 1 分集已入库）
                                "episodes_actual": result.get("episodes_processed", 0),
                            })
```

**Step 3c**: `_batch_audit_task` Phase 2，在 `total_eps_enriched = 0`（:1761）之后追加实际计数：
```python
        total_eps_actual = sum(
            sq.get("episodes_actual", 0) for sq in series_with_seasons
        )
```

**Step 3d**: 单季完成日志（:1895-1899）改为实际 DB 分集数 + TMDB 参考：
```python
                    actual_season_eps = db.query(MediaMetadata).filter(
                        MediaMetadata.parent_id == item_id,
                        MediaMetadata.parent_index_number == season_num,
                        MediaMetadata.media_type == "Episode",
                    ).count()
                    logger.info(
                        "   ✅ [BatchAudit] 《%s》S%02d 完成: %d 集 (TMDB %d), %d 位客串",
                        item_name, season_num, actual_season_eps,
                        len(episodes), len(all_guest_stars),
                    )
```

**Step 3e**: 最终摘要（:1923-1929）改用 `_build_batch_audit_summary`：
```python
        _batch_audit_success = True
        _batch_audit_final_msg = _build_batch_audit_summary(
            total_scanned=total_scanned,
            total_synced=total_synced,
            n_series=len(series_with_seasons),
            n_seasons=grand_total_seasons,
            total_eps_actual=total_eps_actual,
            total_eps_enriched=total_eps_enriched,
            total_guest_stars=total_guest_stars_all,
        )
```
并同步 :1930-1935 的 logger 内 `eps=%d` 参数由 `total_eps_enriched` 改为 `total_eps_actual`。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && venv/bin/python -m pytest tests/test_batch_audit_summary.py -v`
Expected: PASS（全部通过）

- [ ] **Step 5: 导入冒烟**

Run: `cd backend && venv/bin/python -c "from routers import sync_actions; print('OK')"`
Expected: `OK`

- [ ] **Step 6: 提交**

```bash
git add backend/routers/sync_actions.py backend/tests/test_batch_audit_summary.py
git commit -m "fix: BatchAudit 摘要改用实际 Emby 分集数，TMDB 数作参考（30 vs 12）"
```

---

### Task 6: Series `recursive_item_count` 实算 + 前端优先实际分集数

**Files:**
- Modify: `backend/routers/sync_actions.py` `_audit_and_save_single_item`（:580 `db.flush()` 前）
- Modify: `frontend/src/components/ActorLocalizationStudio.vue`（:916）

**Interfaces:**
- Consumes: `_process_episodes` 返回值 `episodes_processed`（审计路径已有）
- Produces: Series 的 `MediaMetadata.recursive_item_count` 在每次分集入库后实算刷新

- [ ] **Step 1: 后端计数实算**

在 `_audit_and_save_single_item` 中，`if/else` 分支结束、`db.flush()`（:580）之前插入：
```python
        # ★ 用实际入库分集数刷新父 Series 计数（不信任 stale RecursiveItemCount）
        if item_type == "Series" and episodes_processed > 0:
            series_mm = db.query(MediaMetadata).filter(
                MediaMetadata.emby_item_id == item_id
            ).first()
            if series_mm:
                series_mm.recursive_item_count = episodes_processed
```

- [ ] **Step 2: 前端优先实际分集数**

将 `frontend/src/components/ActorLocalizationStudio.vue` :916 的：
```html
<span>共 {{ detailsData.series.recursive_item_count || detailsData.episodes.length }} 个子项</span>
```
改为：
```html
<span>共 {{ detailsData.episodes.length || detailsData.series.recursive_item_count }} 个子项</span>
```
> 实际入库分集数（episodes.length）优先；Series 记录缺失时才回退 recursive_item_count。

- [ ] **Step 3: 验证**

Run: `cd backend && venv/bin/python -m pytest tests/test_series_reconcile.py tests/test_episode_diff.py -v`
Expected: 全部 PASS（计数刷新逻辑已在 reconcile 单测覆盖）

Run: `cd frontend && npx vue-tsc --build`
Expected: 无类型错误

- [ ] **Step 4: 提交**

```bash
git add backend/routers/sync_actions.py frontend/src/components/ActorLocalizationStudio.vue
git commit -m "fix: Series 计数由分集列表实算，前端优先显示实际分集数"
```

---

### Task 7: 全量验证与回归

- [ ] **Step 1: 运行 P1 全部新测试**

Run: `cd backend && venv/bin/python -m pytest tests/test_episode_diff.py tests/test_series_reconcile.py tests/test_episode_via_parent.py tests/test_webhook_episode.py tests/test_batch_audit_summary.py -v`
Expected: 全部 PASS

- [ ] **Step 2: 全后端导入冒烟**

Run:
```bash
cd backend && venv/bin/python -c "
from routers import emby, sync_actions
from services import douban_service
print('ALL IMPORTS OK')
"
```
Expected: `ALL IMPORTS OK`

- [ ] **Step 3: 真实库回归（可选，需本地 Emby 环境）**

Run: 启动后端，向 `POST /api/webhook/emby` 投递一次 Episode `library.new` payload（参考设计文档附的"九门 Ep12"日志），确认：
- 日志出现 `[Reconcile]` 对账记录，父 Series `recursive_item_count` 更新为实际分集数
- 单集汉化不再出现 `名称搜索 '第 26 集'` 失败日志
- BatchAudit 摘要显示实际分集数（如 `分集 12 集（TMDB 30）`）

- [ ] **Step 4: 汇报**

按用户要求汇报：修改的核心文件、`reconcile_series_episodes` / `_sinicize_episode_via_parent` 实现、单集走父 Series 链路说明、30vs12 修复前后对比。
