# 演职员汉化「纯净缓存」拦截重构 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为演员/角色中文化建立「全局复用 + 局部复用 + 纯净缓存防伪污染」机制：缓存命中直接复用、官方译名强制中文校验、失败降级 AI 兜底、按来源写入置信度。

**Architecture:** 新增两个共享模块（`translation_utils.py` 校验工具、`translation_cache.py` 查表/回写），为 `actor_profiles`（演员主表，全局复用）与 `actor_records`（演职员关联表，局部复用）增加 `confidence_level` / `translation_source` 列；在 `douban_service.py`（主汉化路径）接入「缓存查询 → 官方 API → 中文校验 → AI 兜底 → 最终入库」完整链路；`db_crud.save_media_to_db` 将置信度/来源持久化到关联表。

**Tech Stack:** Python 3.13 / FastAPI / SQLAlchemy / SQLite / pytest（新增 dev 依赖）

## Global Constraints

- 命中缓存条件统一为 `confidence_level >= 3`（常量 `CONFIDENCE_REUSE_THRESHOLD = 3`）。
- 来源常量：`official=4`（TMDB/豆瓣官方 API）、`ai_llm=3`（AI 大模型）、`manual=5`（UI 手动修改，预留）。
- 官方 API / AI 结果必须通过 `is_valid_chinese_translation()` 校验（含 ≥1 个中文字符），否则丢弃，绝不允许英文原名写入缓存。
- 校验工具独立成函数 `is_valid_chinese_translation(text)`，不依赖类实例。
- 中文字符判定与项目现有 `[一-鿿]`（`[一-鿿]`）保持一致。
- 本计划仅重构后端，不涉及任何前端 UI 修改。
- 演员主表 = `actor_profiles`（对应规格中「persons 表」）；关联表 = `actor_records`。

---

### Task 1: 表结构新增置信度/来源列 + 增量迁移

**Files:**
- Modify: `backend/models.py`（ActorProfile、ActorRecord）
- Modify: `backend/database.py::_run_migrations`

**Interfaces:**
- Produces: `ActorProfile.confidence_level`(Integer, default 0)、`ActorProfile.translation_source`(String, default '')、`ActorRecord.confidence_level`(Integer, default 0)、`ActorRecord.translation_source`(String, default '')。

- [ ] **Step 1: 修改 models.py 增加列**

在 `ActorProfile`（`backend/models.py`）中增加：
```python
    confidence_level = Column(Integer, default=0)      # 中文名译名置信度: 4官方 / 3AI / 5手动
    translation_source = Column(String, default="")    # "official" / "ai_llm" / "manual"
```

在 `ActorRecord` 中增加：
```python
    confidence_level = Column(Integer, default=0)      # 角色译名置信度
    translation_source = Column(String, default="")    # 角色译名来源
```

- [ ] **Step 2: 修改 database.py 增加增量迁移**

在 `_run_migrations()` 末尾（`conn.commit()` 之前）追加对两张表各两列的 ALTER：
```python
            # ---- actor_profiles / actor_records: 新增置信度与来源列 ----
            for table_name in ("actor_profiles", "actor_records"):
                try:
                    cols = [
                        row[1] for row in
                        conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
                    ]
                except Exception:
                    continue  # 表尚未创建，create_all 会处理
                for col_name, col_type in (("confidence_level", "INTEGER DEFAULT 0"),
                                           ("translation_source", "VARCHAR DEFAULT ''")):
                    if col_name not in cols:
                        conn.execute(text(
                            f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}"
                        ))
                        logger.info("📦 [Migration] %s 添加字段: %s", table_name, col_name)
```

- [ ] **Step 3: 验证迁移逻辑**

Run: `cd backend && venv/bin/python -c "import database; database._run_migrations(); from models import ActorProfile, ActorRecord; print(ActorProfile.confidence_level.type, ActorRecord.translation_source.type)"`
Expected: 打印 `INTEGER` `VARCHAR`，无异常。

---

### Task 2: 中文校验工具 `is_valid_chinese_translation`（TDD）

**Files:**
- Create: `backend/services/translation_utils.py`
- Test: `backend/tests/test_translation_utils.py`

**Interfaces:**
- Produces: `is_valid_chinese_translation(text) -> bool`；常量 `SOURCE_OFFICIAL="official"`、`SOURCE_AI_LLM="ai_llm"`、`SOURCE_MANUAL="manual"`、`CONFIDENCE_REUSE_THRESHOLD=3`、`CONFIDENCE_OFFICIAL=4`、`CONFIDENCE_AI_LLM=3`、`CONFIDENCE_MANUAL=5`。

- [ ] **Step 1: 安装 pytest 并编写失败测试**

Run: `cd backend && venv/bin/python -m pip install pytest`

创建 `backend/tests/__init__.py`（空文件）与 `backend/tests/test_translation_utils.py`：
```python
"""translation_utils 测试 — 纯净缓存防伪污染核心判据。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.translation_utils import (
    is_valid_chinese_translation,
    SOURCE_OFFICIAL, SOURCE_AI_LLM, SOURCE_MANUAL,
    CONFIDENCE_REUSE_THRESHOLD, CONFIDENCE_OFFICIAL, CONFIDENCE_AI_LLM, CONFIDENCE_MANUAL,
)


def test_contains_chinese_returns_true():
    assert is_valid_chinese_translation("张译") is True
    assert is_valid_chinese_translation("沃尔特·怀特") is True
    assert is_valid_chinese_translation("混排 En 中") is True


def test_english_only_returns_false():
    assert is_valid_chinese_translation("Bryan Cranston") is False
    assert is_valid_chinese_translation("Walter White") is False
    assert is_valid_chinese_translation("Sun Hu") is False


def test_empty_and_non_string_return_false():
    assert is_valid_chinese_translation("") is False
    assert is_valid_chinese_translation(None) is False
    assert is_valid_chinese_translation("  ") is False
    assert is_valid_chinese_translation("123!@#") is False


def test_constants_consistency():
    assert CONFIDENCE_REUSE_THRESHOLD == 3
    assert CONFIDENCE_OFFICIAL == 4
    assert CONFIDENCE_AI_LLM == 3
    assert CONFIDENCE_MANUAL == 5
    assert SOURCE_OFFICIAL == "official"
    assert SOURCE_AI_LLM == "ai_llm"
    assert SOURCE_MANUAL == "manual"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && venv/bin/python -m pytest tests/test_translation_utils.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'services.translation_utils'`）

- [ ] **Step 3: 实现 translation_utils.py**

```python
"""中文化文本校验工具 — 纯净缓存防伪污染判据。

防止「伪中文（英文原名）」污染缓存：官方 API / AI 返回的译名，
必须经 is_valid_chinese_translation 校验（含至少一个中文字符）才允许回写。
"""
import re

# 中文字符（CJK 统一表意文字，含繁体），与项目现有 [一-鿿] 范围一致
_CHINESE_RE = re.compile(r"[一-鿿]")

# ---- 翻译来源 ----
SOURCE_OFFICIAL = "official"   # TMDB / 豆瓣 官方 API
SOURCE_AI_LLM = "ai_llm"       # AI 大模型兜底
SOURCE_MANUAL = "manual"       # UI 手动修改（预留）

# ---- 置信度 ----
CONFIDENCE_REUSE_THRESHOLD = 3  # >= 阈值可直接复用缓存
CONFIDENCE_OFFICIAL = 4
CONFIDENCE_AI_LLM = 3
CONFIDENCE_MANUAL = 5


def is_valid_chinese_translation(text) -> bool:
    """判断字符串是否包含至少一个中文字符。

    官方 API / AI 的译名在回写前必须通过此校验：
    全部为英文或非中文字符（说明官方无中文 / AI 未正确汉化）时返回 False，
    调用方应【直接丢弃】该结果并继续降级。
    """
    if not text or not isinstance(text, str):
        return False
    return bool(_CHINESE_RE.search(text))
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && venv/bin/python -m pytest tests/test_translation_utils.py -v`
Expected: PASS（全部通过）

---

### Task 3: 查表与回写模块 `translation_cache.py`（TDD）

**Files:**
- Create: `backend/services/translation_cache.py`
- Test: `backend/tests/test_translation_cache.py`

**Interfaces:**
- Produces:
  - `lookup_actor_name(db, tmdb_id: str, raw_name: str) -> dict | None` — 返回 `{"name","confidence_level","translation_source"}`，命中要求 `confidence_level >= 3`。
  - `lookup_role_name(db, role: str, emby_item_id: str, parent_id: str | None = None, actor_name: str = "") -> dict | None` — 返回 `{"role","confidence_level","translation_source"}`，命中要求 `confidence_level >= 3`。
  - `upsert_actor_translation(db, chinese_name: str, tmdb_id: str, source: str, confidence: int) -> None` — 按 tmdb_id 或 name UPSERT `actor_profiles` 的置信度/来源。

- [ ] **Step 1: 编写失败测试**

创建 `backend/tests/test_translation_cache.py`：
```python
"""translation_cache 测试 — 全局/局部查表 + 置信度回写。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base
from models import ActorProfile, ActorRecord
from services.translation_cache import (
    lookup_actor_name, lookup_role_name, upsert_actor_translation,
)
from services.translation_utils import (
    SOURCE_OFFICIAL, SOURCE_AI_LLM, CONFIDENCE_OFFICIAL, CONFIDENCE_AI_LLM,
)

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
Session = sessionmaker(bind=engine)


def _fresh_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    return Session()


def test_lookup_actor_name_by_tmdb_id():
    db = _fresh_db()
    db.add(ActorProfile(name="张译", tmdb_id="12345",
                        confidence_level=CONFIDENCE_OFFICIAL, translation_source=SOURCE_OFFICIAL))
    db.commit()
    hit = lookup_actor_name(db, "12345", "Bryan")
    assert hit and hit["name"] == "张译" and hit["confidence_level"] == 4
    db.close()


def test_lookup_actor_name_low_confidence_ignored():
    db = _fresh_db()
    db.add(ActorProfile(name="Zhang Yi", tmdb_id="999", confidence_level=2, translation_source=SOURCE_AI_LLM))
    db.commit()
    assert lookup_actor_name(db, "999", "Zhang Yi") is None
    db.close()


def test_lookup_actor_name_fallback_to_raw_name():
    db = _fresh_db()
    db.add(ActorProfile(name="张译", tmdb_id="", confidence_level=CONFIDENCE_AI_LLM, translation_source=SOURCE_AI_LLM))
    db.commit()
    hit = lookup_actor_name(db, "", "张译")
    assert hit and hit["name"] == "张译"
    db.close()


def test_lookup_role_name_by_item_and_parent():
    db = _fresh_db()
    db.add(ActorRecord(emby_item_id="E1", name="布莱恩", role="沃尔特·怀特",
                       confidence_level=CONFIDENCE_OFFICIAL, translation_source=SOURCE_OFFICIAL))
    db.commit()
    hit = lookup_role_name(db, "沃尔特·怀特", "E1", parent_id="S1")
    assert hit and hit["role"] == "沃尔特·怀特"
    db.close()


def test_lookup_role_name_traces_up_to_parent():
    db = _fresh_db()
    db.add(ActorRecord(emby_item_id="S1", name="布莱恩", role="沃尔特·怀特",
                       confidence_level=CONFIDENCE_OFFICIAL, translation_source=SOURCE_OFFICIAL))
    db.commit()
    hit = lookup_role_name(db, "沃尔特·怀特", "E1", parent_id="S1")
    assert hit and hit["role"] == "沃尔特·怀特"
    db.close()


def test_lookup_role_name_low_confidence_ignored():
    db = _fresh_db()
    db.add(ActorRecord(emby_item_id="S1", name="布莱恩", role="Walter White",
                       confidence_level=2, translation_source=SOURCE_AI_LLM))
    db.commit()
    assert lookup_role_name(db, "Walter White", "E1", parent_id="S1") is None
    db.close()


def test_upsert_actor_translation_creates_and_updates():
    db = _fresh_db()
    upsert_actor_translation(db, "张译", "12345", SOURCE_OFFICIAL, CONFIDENCE_OFFICIAL)
    db.commit()
    row = db.query(ActorProfile).filter(ActorProfile.tmdb_id == "12345").first()
    assert row and row.name == "张译" and row.confidence_level == 4
    # 已存在则更新（不降级：用 max）
    upsert_actor_translation(db, "张译", "12345", SOURCE_AI_LLM, CONFIDENCE_AI_LLM)
    db.commit()
    row = db.query(ActorProfile).filter(ActorProfile.tmdb_id == "12345").first()
    assert row.confidence_level == 4  # 保持较高置信度
    db.close()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && venv/bin/python -m pytest tests/test_translation_cache.py -v`
Expected: FAIL（`No module named 'services.translation_cache'`）

- [ ] **Step 3: 实现 translation_cache.py**

```python
"""演员/角色译名缓存查表 + 置信度回写。

核心职责：
  1. lookup_actor_name  — 演员名【全局复用】：以 tmdb_id（降级 raw_name）
                          在演员主表 actor_profiles 查中文名，命中要求 confidence>=3。
  2. lookup_role_name   — 角色名【局部复用】：以 role + emby_item_id（分集追溯
                          parent_id）在关联表 actor_records 限定上下文查询。
  3. upsert_actor_translation — 将官方/AI 译名回写 actor_profiles 并记录置信度。

命中条件统一由 CONFIDENCE_REUSE_THRESHOLD 控制，低置信度记录绝不复用，
从而防止「伪中文（英文原名）」污染缓存后被当作有效译名。
"""
import logging

from models import ActorProfile, ActorRecord
from services.translation_utils import (
    is_valid_chinese_translation,
    CONFIDENCE_REUSE_THRESHOLD,
)

logger = logging.getLogger("uvicorn")


def lookup_actor_name(db, tmdb_id: str, raw_name: str) -> dict | None:
    """演员名全局查表：优先 tmdb_id，查不到则降级 raw_name。

    Args:
        db:        SQLAlchemy Session
        tmdb_id:   TMDB 人物 ID（语言无关的稳定锚点，首选）
        raw_name:  Emby 原始演员名（中文名直查 / 无 ID 时降级）

    Returns:
        {"name", "confidence_level", "translation_source"}，未命中或
        confidence < CONFIDENCE_REUSE_THRESHOLD 时返回 None。
    """
    profile = None
    if tmdb_id:
        profile = db.query(ActorProfile).filter(
            ActorProfile.tmdb_id == str(tmdb_id).strip()
        ).first()
    if profile is None and raw_name:
        profile = db.query(ActorProfile).filter(
            ActorProfile.name == raw_name.strip()
        ).first()
    if profile and (profile.confidence_level or 0) >= CONFIDENCE_REUSE_THRESHOLD:
        logger.info("   💾 [Cache] 演员名缓存命中: %s (conf=%s)", profile.name, profile.confidence_level)
        return {
            "name": profile.name,
            "confidence_level": profile.confidence_level,
            "translation_source": profile.translation_source or "",
        }
    return None


def lookup_role_name(
    db,
    role: str,
    emby_item_id: str,
    parent_id: str | None = None,
    actor_name: str = "",
) -> dict | None:
    """角色名局部查表：role + emby_item_id（分集时向上追溯 parent_id）。

    在限定上下文（本媒体项，分集另含其 Series）内查询 actor_records，
    已入库且 confidence >= 阈值的中文角色名可直接复用。

    Args:
        db:           SQLAlchemy Session
        role:         待翻译的角色名（Emby 原始值）
        emby_item_id: 当前媒体项 ID
        parent_id:    分集时传入 Series ID，实现向上追溯
        actor_name:   可选，演员名用于增强匹配

    Returns:
        {"role", "confidence_level", "translation_source"}，未命中返回 None。
    """
    if not role:
        return None
    scope_ids = [emby_item_id]
    if parent_id:
        scope_ids.append(parent_id)
    rows = db.query(ActorRecord).filter(
        ActorRecord.emby_item_id.in_(scope_ids)
    ).all()

    def _hit(r) -> bool:
        return (r.confidence_level or 0) >= CONFIDENCE_REUSE_THRESHOLD

    # 策略 1: role 精确匹配（限定上下文内，同一角色值已入库）
    for r in rows:
        if r.role and r.role == role and _hit(r) and is_valid_chinese_translation(r.role):
            logger.info("   💾 [Cache] 角色名缓存命中(role): %s (conf=%s)", r.role, r.confidence_level)
            return {
                "role": r.role,
                "confidence_level": r.confidence_level,
                "translation_source": r.translation_source or "",
            }
    # 策略 2: 演员名归一化匹配，提取该演员已入库的中文角色
    if actor_name:
        key = _norm_key(actor_name)
        for r in rows:
            if r.name and _norm_key(r.name) == key and r.role and _hit(r) \
                    and is_valid_chinese_translation(r.role):
                logger.info("   💾 [Cache] 角色名缓存命中(actor): %s (conf=%s)", r.role, r.confidence_level)
                return {
                    "role": r.role,
                    "confidence_level": r.confidence_level,
                    "translation_source": r.translation_source or "",
                }
    return None


def upsert_actor_translation(
    db, chinese_name: str, tmdb_id: str, source: str, confidence: int,
) -> None:
    """将确认有效的译名回写 actor_profiles（演员主表）并记录来源与置信度。

    调用方必须已通过 is_valid_chinese_translation 校验；低置信度结果不回写。
    已存在记录时置信度取 max（不被低质量结果降级覆盖）。

    Args:
        chinese_name: 经校验的中文名
        tmdb_id:      TMDB 人物 ID（用于定位既有行）
        source:       translation_utils.SOURCE_*
        confidence:   translation_utils.CONFIDENCE_*
    """
    if not chinese_name:
        return
    profile = None
    if tmdb_id:
        profile = db.query(ActorProfile).filter(
            ActorProfile.tmdb_id == str(tmdb_id).strip()
        ).first()
    if profile is None:
        profile = db.query(ActorProfile).filter(
            ActorProfile.name == chinese_name
        ).first()
    if profile is None:
        profile = ActorProfile(name=chinese_name)
        db.add(profile)
    profile.name = chinese_name
    if tmdb_id:
        profile.tmdb_id = str(tmdb_id).strip()
    profile.confidence_level = max(profile.confidence_level or 0, confidence)
    profile.translation_source = source


def _norm_key(text: str) -> str:
    """归一化匹配 key：去空格、转小写、去点号。"""
    import re
    return re.sub(r"[^a-z0-9]", "", text.lower())
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && venv/bin/python -m pytest tests/test_translation_cache.py -v`
Expected: PASS（全部通过）

---

### Task 4: `save_media_to_db` 持久化角色置信度/来源

**Files:**
- Modify: `backend/services/db_crud.py`（ActorRecord 创建处）

**Interfaces:**
- Consumes: person dict 私有键 `_cn_role_conf`(int)、`_cn_role_src`(str)（由汉化链路在翻译决策时写入）。
- Produces: `ActorRecord.confidence_level` / `ActorRecord.translation_source` 正确落库。

- [ ] **Step 1: 修改 ActorRecord 创建逻辑**

在 `db_crud.py` 第 215 行附近，`db.add(ActorRecord(...))` 增加两字段：
```python
                db.add(ActorRecord(
                    emby_item_id=item_id,
                    name=name,
                    role=role,
                    type=person_type,
                    sort_order=idx,
                    # ★ 角色译名来源与置信度（由汉化链路写入 person 私有键）
                    confidence_level=p.get("_cn_role_conf") or 0,
                    translation_source=p.get("_cn_role_src") or "",
                ))
```

- [ ] **Step 2: 验证编译**

Run: `cd backend && venv/bin/python -c "import services.db_crud; print('ok')"`
Expected: `ok`

---

### Task 5: `douban_service.py` 接入完整链路（核心）

**Files:**
- Modify: `backend/services/douban_service.py`（`sinicize`、`_match_and_update`、`_localize_episode_people`）

**Interfaces:**
- Consumes: `services.translation_utils.is_valid_chinese_translation` 及常量；`services.translation_cache.lookup_actor_name / lookup_role_name / upsert_actor_translation`。
- Produces: person dict 私有键 `_cn_role_conf` / `_cn_role_src`（供 `save_media_to_db` 落库）。

- [ ] **Step 1: `_match_and_update` 增加缓存查询 + 官方校验 + 置信度标记**

签名从 `(self, emby_actors, douban_actors)` 扩展为接收上下文：
```python
    def _match_and_update(
        self, emby_actors: list[dict], douban_actors: list[dict],
        db=None, emby_item_id: str = "", parent_id: str = "",
        provider_tmdb_ids: dict = None,
    ) -> tuple[list[dict], list[dict]]:
```
`provider_tmdb_ids` 为 `{emby_name_lower: tmdb_id}`。循环内（找到 `emby_name` 后、匹配豆瓣前）：
```python
            # ★ 缓存优先：命中即复用，跳过豆瓣匹配
            cached_name = None
            if db:
                hit = lookup_actor_name(
                    db,
                    (provider_tmdb_ids or {}).get(emby_name.lower(), ""),
                    emby_name,
                )
                if hit:
                    cached_name = hit["name"]
```
命中时直接构建 `new_entry`（保留 DoubanAvatarUrl 等注入），角色走 `lookup_role_name`；未命中再走豆瓣匹配。对豆瓣返回的 `matched_da["name"]` / `matched_da["role"]` 增加校验：
```python
                douban_name = matched_da.get("name", "")
                if not is_chinese and douban_name and is_valid_chinese_translation(douban_name):
                    new_entry["Name"] = douban_name
                    new_entry["_cn_name_conf"] = CONFIDENCE_OFFICIAL
                    new_entry["_cn_name_src"] = SOURCE_OFFICIAL
                # 伪中文（全英文）→ 丢弃，标记待 AI 兜底
                if not is_chinese and douban_name and not is_valid_chinese_translation(douban_name):
                    _discarded_names[emby_name.lower()] = emby_name

                douban_role = matched_da.get("role", "")
                if douban_role and douban_role not in ("演员", "配音", "actor", "actress"):
                    if is_valid_chinese_translation(douban_role):
                        new_entry["Role"] = douban_role
                        new_entry["_cn_role_conf"] = CONFIDENCE_OFFICIAL
                        new_entry["_cn_role_src"] = SOURCE_OFFICIAL
                    else:
                        new_entry["_cn_role_conf"] = 0
                        _discarded_roles[emby_name.lower()] = emby_role or douban_role
```
方法返回 `(updated, details, _discarded_names, _discarded_roles)`（向后兼容：调用处解包为 4 值）。

- [ ] **Step 2: `sinicize` 打开 DB、接入 AI 兜底、回写 ActorProfile 置信度**

在 `sinicize` 顶部（第 160 行前）打开会话：`db = SessionLocal()`；为每名演员从 `ProviderIds`/`item_data` 构建 `provider_tmdb_ids`。第 4 步改为 4 值解包；第 5 步 AI 翻译（`roles_to_translate`）扩展为同时翻译被丢弃的人名/角色名：
```python
            # 官方结果被丢弃的英文原名 → 进入 AI 兜底
            translate_input = list(set(
                list(_discarded_names.values()) + list(_discarded_roles.values())
            ))
            if translate_input and translator.is_available():
                ...
```
AI 返回后再次经 `is_valid_chinese_translation` 校验，命中则写回 `_cn_*_conf=CONFIDENCE_AI_LLM / _cn_*_src=SOURCE_AI_LLM`，并调用 `upsert_actor_translation(db, chinese_name, tmdb_id, SOURCE_AI_LLM, CONFIDENCE_AI_LLM)`。

在 `_match_and_update` 返回后，对「官方命中中文名」的演员调用：
```python
            upsert_actor_translation(db, chinese_name, tmdb_id, SOURCE_OFFICIAL, CONFIDENCE_OFFICIAL)
```
（`db.commit()` 由 `sinicize` 尾部原有 `db.commit()` 统一提交；需确保 `db` 在 `finally` 中 close。）

- [ ] **Step 3: `_localize_episode_people` 接入缓存 + 校验**

签名增加 `db=None, emby_item_id="", parent_id=""`。分集循环内（第 7c 步调用处）传入 `db`、`ep_id`、`item_id`。方法内：
- 每名演员先 `lookup_actor_name` / `lookup_role_name(role, ep_id, parent_id=item_id, actor_name=emby_name)`，命中即应用中文名/角色并跳过 AI。
- `douban_map` 官方结果（`info["name"]` / `info["role"]`）写回前经 `is_valid_chinese_translation` 校验；有效则附 `_cn_*_conf`、`_cn_*_src`，伪中文丢弃并标记 AI。
- AI 结果校验通过后附 `_cn_*_conf=CONFIDENCE_AI_LLM`、`_cn_*_src=SOURCE_AI_LLM`，并 `upsert_actor_translation`。

- [ ] **Step 4: 语法与导入验证**

Run: `cd backend && venv/bin/python -c "from services import douban_service; print('ok')"`
Expected: `ok`

---

### Task 6: `sync_actions.py` 共用校验与查表

**Files:**
- Modify: `backend/routers/sync_actions.py`

- [ ] **Step 1: 替换内联中文字符判定**

将 `_localize_episode_people` 中 3 处 `_CHINESE_RE.search(...)` 替换为共享工具：
```python
from services.translation_utils import is_valid_chinese_translation
# 原 `not _CHINESE_RE.search(emby_name)` → `not is_valid_chinese_translation(emby_name)`
# 原 `_CHINESE_RE.search(translated)` → `is_valid_chinese_translation(translated)`
```

- [ ] **Step 2: 验证导入**

Run: `cd backend && venv/bin/python -c "from routers import sync_actions; print('ok')"`
Expected: `ok`

---

### Task 7: 全量验证与回归

- [ ] **Step 1: 运行全部新测试**

Run: `cd backend && venv/bin/python -m pytest tests/test_translation_utils.py tests/test_translation_cache.py -v`
Expected: 全部 PASS

- [ ] **Step 2: 全后端模块导入冒烟**

Run:
```bash
cd backend && venv/bin/python -c "
import database; database._run_migrations()
from models import ActorProfile, ActorRecord
from services import translation_utils, translation_cache, db_crud, actor_profile_service, douban_service, ai_translator
from routers import sync_actions, actor_router
print('ALL IMPORTS OK')
"
```
Expected: `ALL IMPORTS OK`

- [ ] **Step 3: 真实数据库迁移冒烟**

Run: `cd backend && venv/bin/python -c "import database; database._run_migrations(); import sqlite3; con=sqlite3.connect('data/emby_ai.db'); print([r[1] for r in con.execute('PRAGMA table_info(actor_profiles)')]); print([r[1] for r in con.execute('PRAGMA table_info(actor_records)')])"`
Expected: 两张表均包含 `confidence_level`、`translation_source`。

- [ ] **Step 4: 汇报**

按用户要求汇报：修改的核心文件、`is_valid_chinese_translation` 实现、完整链路串联说明。
