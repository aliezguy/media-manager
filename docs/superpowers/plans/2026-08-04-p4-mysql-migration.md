# P4 双库兼容与 MySQL 迁移 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 SQLite（默认）与 MySQL 的双引擎支持（核心代码层面的「双擎基建」）。默认配置下一切行为不变（74 个既有测试全绿、SQLite 测试速度不受损）；配置切到 `db_type=mysql` 后引擎自动走 MySQL 连接池与方言化 DDL。**数据直迁与流量切换属高危运维操作，已按用户指示拆分为后续独立 Ops 计划，不在本次范围**（见文末「已拆分」节），以此控制爆炸半径。

**本计划合并来源**（用户新需求 × 设计文档 `2026-08-03-actor-sinicization-governance-design.md` P4）：

| 本计划 Task | 来源 | 内容 |
|---|---|---|
| 1 | **新需求 Task 1** + 设计 4b | 配置层接入 + 引擎动态初始化 |
| 2 | **新需求 Task 2** + 设计 4a | 方言兼容的迁移层 |
| 3 | 设计 4c | models.py String 长度补齐 |
| 4 | **新需求 Task 3** | ORM JSON 双库兼容 + 中文防乱码守卫 |
| 5 | **新需求 Task 4** | 全量回归 |

> 设计 4d（一次性直迁脚本 + 生产切流）按用户指示**拆分为后续独立 Ops 计划**，不在本次范围（见文末「已拆分」节）——核心代码层面的「双擎基建」（Task 1-4）与高危运维操作（数据字典直迁 + 流量切换）分离，控制爆炸半径。

**Architecture:**
- **Task 1 配置层**：`config/settings.py` 的 `DEFAULT_CONFIG` 新增离散项 `db_type`(默认 `"sqlite"`) / `db_host` / `db_port` / `db_user` / `db_password` / `db_name`。`database.py` 新增 `_build_database_url(cfg)`（优先序：env `DATABASE_URL` → config 离散 `db_*` → 内置 SQLite 路径）与 `_engine_kwargs(url)`（按 `make_url(url).get_backend_name()` 分支：MySQL → `pool_pre_ping=True, pool_recycle=3600`；SQLite → `connect_args={"check_same_thread": False}` 保持原样）。MySQL URL 统一带 `?charset=utf8mb4`。
- **Task 2 迁移层**：`_run_migrations` 参数化（`_run_migrations(engine=None)`），把 4 处裸 `PRAGMA table_info()` 全换成 `inspect(engine).get_columns()`（方言无关）。抽纯函数 `_add_column_type(dialect, col_name)`：`douban_cast_cache` → SQLite `TEXT` / MySQL `JSON`；其余列统一 `VARCHAR(255)`（裸 `VARCHAR` 在 MySQL 必报 1064，SQLite 忽略长度故双兼容）。`actor_records` 的 DROP+重建分支（含 `AUTOINCREMENT` 裸 DDL）加 `dialect == "sqlite"` 守卫——MySQL 上由 `create_all` 直建，永不触发。
- **Task 3 长度补齐**：全量 `Column(String)` → `Column(String(255))`（主键/索引列 MySQL 必须有长度；`name` / `emby_item_id` 等 PK 列必须）。Text/Integer/JSON/DateTime 列不动。
- **Task 4 JSON 保障**：验证 SQLAlchemy `Column(JSON)` 在双引擎下的读写，覆盖 P3-3b `douban_cast_cache` 的 `{fetched_at, cast}` 结构与 `MediaTag.tags` 列表结构；中文防乱码守卫测试（SQLite 默认必跑，MySQL 由 `MYSQL_TEST_URL` env 门控）。
- **Task 5 回归**：默认配置下全量 74 测试绿 + 新增测试全绿，SQLite 测试速度不受损。
- **（已拆分）直迁**：原 Task 5（一次性直迁脚本 + 生产切流）拆为后续独立 Ops 计划，不在本次范围（素材保留在文末「已拆分」节）。

**Tech Stack:** Python 3.13 / SQLAlchemy 2.0.45 / 新增 `pymysql`（纯 Python 驱动，免编译）/ pytest（`sqlite :memory:` 约定 + monkeypatch）/ `MYSQL_TEST_URL` env 门控 MySQL 集成测试

---

## Global Constraints

- 测试运行目录：`cd backend`，解释器 `venv/bin/python -m pytest`。聚焦 `tests/test_db_engine.py tests/test_migration_dialect.py tests/test_models_mysql.py tests/test_json_dual_engine.py -v`；全量 `tests/ -q` 回归。
- **默认配置必须保持 SQLite**：`db_type` 默认 `"sqlite"`，`DEFAULT_CONFIG` 新项不写进 config.json 也能靠 `load_config` 兜底；日常开发与既有 74 测试零感知。
- **MySQL 集成测试一律门控**：`MYSQL_TEST_URL` env 存在才跑真实 MySQL 用例（`pytest.mark.skipif`），默认全量不打真实 MySQL——不拖慢、不依赖内网可用性。测试库用独立 `media_ai_test`，绝不碰生产 `media-ai`。
- **纯切换语义**：MySQL 连接失败 → 启动即报错并给出明确提示，不静默回退 SQLite（设计文档错误处理节）。
- **TDD**：每个 Task 先写失败测试 → 确认 RED → 实现 → GREEN → commit。提交粒度 = 每个 Task 一次 commit，之后停下等用户 Review，Review 通过才继续。
- **不新增 config 必需字段**：`db_type` 等离散项全部带默认值，无 MySQL 配置时与现状完全等价。
- 前置依赖：P3-3b 的 `douban_cast_cache` JSON 列（`MediaSyncStatus`）已存在且迁移层已加 `TEXT`，本计划将其升级为方言分支。

## 设计决策（供 Review 确认）

1. **配置优先序改为三层**：env `DATABASE_URL`（最高，全量覆盖）→ config.json 离散 `db_type/db_host/db_port/db_user/db_password/db_name` → 内置默认 SQLite。这替代设计文档 4b 的 `config.json database_url` 键——用户新需求明确要求离散配置项，同时保留 env 直给 URL 的能力（运维可绕过 config 直接指库）。
2. **MySQL 驱动选 `pymysql`**：纯 Python 免编译，requirements.txt 新增一行；URL 形如 `mysql+pymysql://user:pass@host:port/db?charset=utf8mb4`。不选 `mysqlclient`（需编译，部署机不保证 toolchain）。
3. **裸 `VARCHAR` 统一 `VARCHAR(255)`**：现迁移层 `ADD COLUMN tmdb_id VARCHAR` / `translation_source VARCHAR DEFAULT ''` 在 MySQL 上必报 1064（MySQL 要求显式长度）。SQLite 忽略长度、接受 `VARCHAR(255)`，故不分方言、全局 `VARCHAR(255)` 最简。
4. **JSON 列方言分支用纯函数**：`_add_column_type(dialect, col_name)` 对 `douban_cast_cache` 返回 `JSON`(mysql) / `TEXT`(sqlite)，其余返回 `VARCHAR(255)`。纯函数可单测两分支，无需真实 MySQL。
5. **`_run_migrations` 参数化**：签名 `_run_migrations(engine=None)`（缺省用全局 `engine`），便于对任意引擎（测试 `:memory:` / MySQL 门控）执行迁移；`main.py` 零改动。
6. **`actor_records` DROP+重建分支加 `dialect=="sqlite"` 守卫**：该分支含 `AUTOINCREMENT` 裸 DDL（SQLite 专属），MySQL 上 legacy `image_url` 列不存在、永不触发，加守卫纯防御。`RENAME COLUMN` 分支 MySQL 8.0+ 原生支持，保留但同样不会在新建库触发。
7. **models 统一 `String(255)` + MySQL 索引长度风险**：utf8mb4 下 `VARCHAR(255)` = 1020B < InnoDB DYNAMIC 3072B 索引上限，安全。需确认目标 MySQL ≥ 5.7（DYNAMIC 行格式，默认）——若为旧版 COMPACT（767B 上限），索引列需缩短为 `String(128)`。集成测试建表即暴露此问题。
8. **直迁脚本保留显式主键 + 幂等 + `--dry-run`**：`media_metadata.emby_item_id`、`ActorProfile.name`、`ActorRecord.id` 原样搬入保证 `parent_id` / actor 关联不漂移；JSON 列 SQLite 落 TEXT → 读出 `json.loads` → 交 MySQL 原生 JSON 列序列化；行数对比 + 无数据重复写入实现可重跑。

---

### Task 1: 配置层接入与引擎动态初始化

**Files:**
- Modify: `backend/config/settings.py` — `DEFAULT_CONFIG` 增 6 个离散 db 项
- Modify: `backend/database.py` — 新增 `_build_database_url` / `_engine_kwargs`，重写引擎创建
- Modify: `requirements.txt` — 新增 `pymysql`
- Test: `backend/tests/test_db_engine.py`（新建）

**Interfaces:**
- Produces（后续 Task 依赖）：
  - `_build_database_url(cfg: dict | None = None) -> str` —— 按三层优先序解析 DB URL。`cfg` 缺省调 `load_config()`（测试可显式传入构造 dict，免读盘）。
  - `_engine_kwargs(url) -> dict` —— `url.get_backend_name()=="sqlite"` → `{"connect_args": {"check_same_thread": False}}`；否则 → `{"pool_pre_ping": True, "pool_recycle": 3600}`。
  - 模块级 `engine = create_engine(SQLALCHEMY_DATABASE_URL, **_engine_kwargs(make_url(SQLALCHEMY_DATABASE_URL)))` —— 向后兼容，`main.py` / 各 service 的 `from database import engine, SessionLocal, Base` 零改动。
- Consumes：既有 `load_config()`、`make_url`。

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_db_engine.py`：

```python
"""P4 Task 1 — 配置层与引擎动态初始化测试。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from sqlalchemy.engine import make_url
import database


def _mysql_cfg():
    return {"db_type": "mysql", "db_host": "192.168.31.135", "db_port": "3008",
            "db_user": "root", "db_password": "root", "db_name": "media-ai"}


def test_default_url_is_sqlite(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    url = database._build_database_url({})   # 空 cfg → 兜底默认
    assert url.startswith("sqlite:///")


def test_default_url_keeps_data_dir():
    # 不带任何配置时，默认仍指向 backend/data/emby_ai.db
    url = database._build_database_url({})
    assert url == database._DEFAULT_SQLITE_URL
    assert database._DEFAULT_SQLITE_URL.endswith("data/emby_ai.db")


def test_mysql_url_assembled_from_discrete_config():
    url = database._build_database_url(_mysql_cfg())
    assert url == ("mysql+pymysql://root:root@192.168.31.135:3008/media-ai?charset=utf8mb4")


def test_env_database_url_overrides_config(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///override.db")
    assert database._build_database_url(_mysql_cfg()) == "sqlite:///override.db"


def test_sqlite_engine_keeps_check_same_thread():
    url = make_url("sqlite:///:memory:")
    assert database._engine_kwargs(url) == {"connect_args": {"check_same_thread": False}}


def test_mysql_engine_gets_pool_params():
    url = make_url("mysql+pymysql://u:p@h:3306/db")
    kw = database._engine_kwargs(url)
    assert kw["pool_pre_ping"] is True
    assert kw["pool_recycle"] == 3600
    assert "check_same_thread" not in kw
```

- [ ] **Step 2: 确认 RED**（`database._build_database_url` / `_engine_kwargs` 不存在 → AttributeError）
- [ ] **Step 3: 实现**

`settings.py` `DEFAULT_CONFIG` 追加（放 `request_budget` 前，DB 属基础配置）：

```python
    # ★ 数据库配置（P4 双库兼容）— 生产切 MySQL，测试/默认 SQLite
    "db_type": "sqlite",          # sqlite | mysql
    "db_host": "",                # MySQL: 192.168.31.135
    "db_port": "",                # MySQL: 3008
    "db_user": "",
    "db_password": "",
    "db_name": "",                # MySQL: media-ai
```

`database.py` 重写引擎创建段：

```python
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker, declarative_base
import os
import logging
from config.settings import load_config

logger = logging.getLogger("uvicorn")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

_DEFAULT_SQLITE_URL = f"sqlite:///{os.path.join(DATA_DIR, 'emby_ai.db')}"


def _build_database_url(cfg: dict | None = None) -> str:
    """解析 DB URL。优先序：env DATABASE_URL → config 离散 db_* → 内置 SQLite。"""
    env_url = os.environ.get("DATABASE_URL")
    if env_url:
        return env_url
    cfg = cfg if cfg is not None else load_config()
    if cfg.get("db_type") == "mysql":
        return (f"mysql+pymysql://{cfg['db_user']}:{cfg['db_password']}"
                f"@{cfg['db_host']}:{cfg['db_port']}/{cfg['db_name']}?charset=utf8mb4")
    return _DEFAULT_SQLITE_URL


def _engine_kwargs(url) -> dict:
    """按方言决定连接池/连接参数。"""
    if url.get_backend_name() == "sqlite":
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True, "pool_recycle": 3600}


SQLALCHEMY_DATABASE_URL = _build_database_url()
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, **_engine_kwargs(make_url(SQLALCHEMY_DATABASE_URL))
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
```

`requirements.txt` 追加：`pymysql==1.1.1`（纯 Python，无需编译）。安装：`venv/bin/pip install pymysql==1.1.1`。

- [ ] **Step 4: 确认 GREEN**（`venv/bin/python -m pytest tests/test_db_engine.py -v` 全过）
- [ ] **Step 5: 全量回归 + commit**

`venv/bin/python -m pytest tests/ -q` → 既有 74 全绿（新测试 +6）。commit message：`feat: settings/database 接入 db_type 离散配置 + 引擎按方言初始化（P4-T1）`

- [ ] **Step 6: 停下等用户 Review**

---

### Task 2: 动态数据库迁移逻辑适配

**Files:**
- Modify: `backend/database.py` — `_run_migrations` 参数化 + 去 PRAGMA + 方言分支
- Test: `backend/tests/test_migration_dialect.py`（新建）

**Interfaces:**
- Produces：
  - `_run_migrations(engine=None)` —— 缺省用全局 `engine`；内部 `inspect(eng).get_columns(table)` 取代 `PRAGMA table_info`。
  - `_get_table_columns(eng, table_name) -> set[str]` —— 方言无关列名探测，表不存在返回空集。
  - `_add_column_type(dialect: str, col_name: str) -> str` —— 方言敏感列类型纯函数。
- Consumes：SQLAlchemy `inspect`。

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_migration_dialect.py`：

```python
"""P4 Task 2 — 迁移层方言适配测试。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from database import Base, _run_migrations, _add_column_type
import models  # noqa: 注册所有表


# ---------- 纯函数：方言分支 ----------

def test_add_column_type_json_sqlite_text():
    assert _add_column_type("sqlite", "douban_cast_cache") == "TEXT"


def test_add_column_type_json_mysql_native():
    assert _add_column_type("mysql", "douban_cast_cache") == "JSON"


def test_add_column_type_varchar_universal():
    # 裸 VARCHAR 在 MySQL 必须带长度；SQLite 忽略长度 → 全局 VARCHAR(255)
    for dialect in ("sqlite", "mysql"):
        assert _add_column_type(dialect, "tmdb_id") == "VARCHAR(255)"
        assert _add_column_type(dialect, "translation_source") == "VARCHAR(255) DEFAULT ''"


# ---------- 迁移函数：SQLite 上可执行且 JSON 落 TEXT ----------

def _sqlite_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return engine


def test_migration_runs_idempotent_on_sqlite():
    eng = _sqlite_session()
    _run_migrations(eng)          # 首次：无缺列 → no-op
    _run_migrations(eng)          # 再次：幂等不炸


def test_migration_adds_douban_cast_cache_as_text_on_sqlite():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)  # create_all 已含 douban_cast_cache
    cols = {c["name"]: str(c["type"]) for c in inspect(eng).get_columns("media_sync_status")}
    assert cols["douban_cast_cache"] == "TEXT"


def test_legacy_sqlite_db_migration_upgrades_to_text():
    """模拟旧库：无 douban_cast_cache 列 → _run_migrations 用 TEXT 补上。"""
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    with eng.connect() as conn:
        conn.execute(text("ALTER TABLE media_sync_status DROP COLUMN douban_cast_cache"))
        conn.commit()
    _run_migrations(eng)
    cols = {c["name"] for c in inspect(eng).get_columns("media_sync_status")}
    assert "douban_cast_cache" in cols


# ---------- MySQL 门控（真实集成） ----------

MYSQL_TEST_URL = os.environ.get("MYSQL_TEST_URL")


@pytest.mark.skipif(not MYSQL_TEST_URL, reason="MYSQL_TEST_URL 未配置")
def test_migration_adds_json_on_mysql():
    from sqlalchemy.engine import make_url
    eng = create_engine(MYSQL_TEST_URL, pool_pre_ping=True, pool_recycle=3600)
    Base.metadata.drop_all(bind=eng)   # 测试库清理
    Base.metadata.create_all(bind=eng)
    _run_migrations(eng)
    cols = {c["name"]: str(c["type"]) for c in inspect(eng).get_columns("media_sync_status")}
    assert cols["douban_cast_cache"] == "JSON"
```

> 注：`test_legacy_sqlite_db_migration_upgrades_to_text` 用 `DROP COLUMN` 模拟旧库——SQLite 3.35+ 支持 DROP COLUMN。若开发机 SQLite 过旧，改用手工建旧结构表替代（迁移函数本身不依赖 DROP）。

- [ ] **Step 2: 确认 RED**（`_run_migrations(eng)` 缺参数 / `_add_column_type` 不存在）
- [ ] **Step 3: 实现**

`database.py` `_run_migrations` 改造：

```python
from sqlalchemy import create_engine, inspect, text


def _get_table_columns(eng, table_name: str) -> set[str]:
    """方言无关列探测（替代 PRAGMA table_info）。表不存在返回空集。"""
    try:
        return {c["name"] for c in inspect(eng).get_columns(table_name)}
    except Exception:
        return set()


def _add_column_type(dialect: str, col_name: str) -> str:
    """方言敏感列类型。JSON 缓存列：SQLite TEXT / MySQL 原生 JSON；其余统一 VARCHAR(255)。"""
    if col_name == "douban_cast_cache":
        return "JSON" if dialect == "mysql" else "TEXT"
    return "VARCHAR(255)"


def _run_migrations(eng=None):
    """对现有表执行增量迁移（方言无关）。

    SQLite 不支持 ALTER TABLE DROP COLUMN，因此移除字段的策略是：
      1. 检查旧列是否存在于 inspect(eng).get_columns
      2. 如表无数据 → DROP TABLE + create_all 重建
      3. 如有数据 → 创建新表 → 复制数据 → 删旧表 → 重命名
    """
    eng = eng or engine
    dialect = eng.dialect.name
    try:
        with eng.connect() as conn:
            # ---- media_sync_status: 补齐 tmdb_id / imdb_id / douban_id ----
            existing = _get_table_columns(eng, "media_sync_status")
            for col_name in ("tmdb_id", "imdb_id", "douban_id"):
                if col_name not in existing:
                    conn.execute(text(
                        f"ALTER TABLE media_sync_status ADD COLUMN {col_name} "
                        f"{_add_column_type(dialect, col_name)}"
                    ))
                    logger.info("📦 [Migration] media_sync_status 添加字段: %s", col_name)

            # ★ P3-3b: douban_cast_cache（JSON 缓存列，方言分支：SQLite TEXT / MySQL JSON）
            if "douban_cast_cache" not in existing:
                conn.execute(text(
                    "ALTER TABLE media_sync_status ADD COLUMN douban_cast_cache "
                    f"{_add_column_type(dialect, 'douban_cast_cache')}"
                ))
                logger.info("📦 [Migration] media_sync_status 添加字段: douban_cast_cache")

            # ---- actor_records: 移除废弃的 image_url 列（SQLite 专属 DROP+重建） ----
            if dialect == "sqlite":
                ar_cols = _get_table_columns(eng, "actor_records")
                if "image_url" in ar_cols:
                    ...  # 原逻辑原样保留（AUTOINCREMENT 裸 DDL 仅 SQLite 可跑）

            # ---- actor_profiles: douban_id → douban_celebrity_id ----
            ap_cols = _get_table_columns(eng, "actor_profiles")
            if "douban_id" in ap_cols and "douban_celebrity_id" not in ap_cols:
                conn.execute(text(
                    "ALTER TABLE actor_profiles RENAME COLUMN douban_id TO douban_celebrity_id"
                ))
                conn.commit()
                logger.info("📦 [Migration] actor_profiles: douban_id → douban_celebrity_id")

            # ---- actor_profiles / actor_records: 新增置信度与译名来源列 ----
            for table_name in ("actor_profiles", "actor_records"):
                cols = _get_table_columns(eng, table_name)
                for col_name, suffix in (
                    ("confidence_level", "INTEGER DEFAULT 0"),
                    ("translation_source", "VARCHAR(255) DEFAULT ''"),
                ):
                    if col_name not in cols:
                        conn.execute(text(
                            f"ALTER TABLE {table_name} ADD COLUMN {col_name} {suffix}"
                        ))
                        logger.info("📦 [Migration] %s 添加字段: %s", table_name, col_name)

            conn.commit()
    except Exception as e:
        logger.debug("   [Migration] 跳过（表尚未创建或已是最新）: %s", e)
```

> 要点：`_add_column_type(dialect, col_name)` 的 `tmdb_id` 等返回 `VARCHAR(255)`，消除了设计文档 4a 未覆盖的「裸 VARCHAR 在 MySQL 报错」隐患；`translation_source` 同样带长度。

- [ ] **Step 4: 确认 GREEN**（`venv/bin/python -m pytest tests/test_migration_dialect.py -v`；MySQL 门控用例在无 env 时 skip）
- [ ] **Step 5: 全量回归 + commit**

`venv/bin/python -m pytest tests/ -q` → 全绿。commit message：`feat: _run_migrations 方言无关化（inspect 取代 PRAGMA + JSON/VARCHAR 分支）（P4-T2）`

- [ ] **Step 6: 停下等用户 Review**

---

### Task 3: models.py String 长度补齐

**Files:**
- Modify: `backend/models.py` — 全量 `Column(String)` → `Column(String(255))`
- Test: `backend/tests/test_models_mysql.py`（新建）

**Interfaces:**
- Produces：所有 String 列显式长度（MySQL 索引/主键列必需）；Text/Integer/JSON/DateTime/Boolean 不动。
- Consumes：无（纯模型定义改动，ORM 行为不变）。

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_models_mysql.py`：

```python
"""P4 Task 3 — models String 长度补齐测试。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from sqlalchemy import create_engine, inspect
from database import Base
import models  # noqa: 注册所有表

# 必须显式长度的列（主键 / 索引 / 会被建索引的列）
REQUIRED_LENGTH_COLUMNS = {
    "media_sync_status": ["emby_item_id", "tmdb_id", "imdb_id", "douban_id", "library_id"],
    "media_metadata": ["emby_item_id", "parent_id"],
    "actor_profiles": ["name", "tmdb_id", "imdb_id", "douban_celebrity_id"],
    "actor_records": ["emby_item_id", "name"],
    "media_tags": ["item_id"],
    "torrent_records": ["hash", "tmdb_id"],
    "auto_task_flows": ["tmdb_id"],
    "task_action_logs": ["task_id", "tmdb_id"],
    "wash_history": ["name", "tmdb_id"],
    "tv_show_details": ["tmdb_id"],
    "completed_season_records": ["tmdb_id"],
    "scan_run_logs": ["task_id"],
}


def test_all_string_columns_have_explicit_length():
    for table_name, cols in REQUIRED_LENGTH_COLUMNS.items():
        table = Base.metadata.tables[table_name]
        for col_name in cols:
            col = table.columns[col_name]
            assert col.type.length is not None, \
                f"{table_name}.{col_name} 缺少 String 长度（MySQL 索引列必需）"
            assert col.type.length == 255, \
                f"{table_name}.{col_name} 长度应为 255，实际 {col.type.length}"


def test_no_bare_string_left():
    """全库兜底：任何 String 列都必须有显式长度。"""
    for table in Base.metadata.tables.values():
        for col in table.columns:
            if str(col.type).upper().startswith("VARCHAR"):
                assert col.type.length is not None, \
                    f"{table.name}.{col.name} 仍是裸 VARCHAR"


@pytest.mark.skipif(not os.environ.get("MYSQL_TEST_URL"), reason="MYSQL_TEST_URL 未配置")
def test_create_all_on_mysql():
    """门控集成：MySQL 上 create_all 不报 1064（裸 VARCHAR 会在此暴露）。"""
    eng = create_engine(os.environ["MYSQL_TEST_URL"], pool_pre_ping=True, pool_recycle=3600)
    Base.metadata.drop_all(bind=eng)
    Base.metadata.create_all(bind=eng)   # 裸 VARCHAR 或 String 无长度 → MySQL 报错
```

- [ ] **Step 2: 确认 RED**（`col.type.length is None` 断言大量失败——正是现库状态）
- [ ] **Step 3: 实现**

`models.py` 全量替换：所有 `Column(String,` / `Column(String)` → `Column(String(255)`。涉及：

| 表 | String 列 |
|---|---|
| `TaskActionLog` | title / action_type / target_name / target_path / reason |
| `MediaTag` | item_id(PK) / name |
| `WashHistory` | name |
| `TvShowDetail` | title / category / overview |
| `CompletedSeasonRecord` | folder_path |
| `TorrentRecord` | hash / torrent_name / qb_category |
| `AutoTaskFlow` | task_type / status / error_message |
| `ScheduledTask` | directory_path / cron_expression |
| `ScanRunLog` | status / trigger_type |
| `MediaSyncStatus` | emby_item_id(PK) / tmdb_id / imdb_id / douban_id / library_id / title / status |
| `MediaMetadata` | emby_item_id(PK) / parent_id / media_type / title |
| `ActorProfile` | name(PK) / source / tmdb_id / imdb_id / douban_celebrity_id / birth_date / birth_place / translation_source |
| `ActorRecord` | emby_item_id / name / role / type / translation_source |

Text 列（`overview` / `error_message` / `image_url` / `poster_url` / `backdrop_url`）保持 `Text`，MySQL 落 TEXT，无需长度。

- [ ] **Step 4: 确认 GREEN**（`venv/bin/python -m pytest tests/test_models_mysql.py -v`；`test_no_bare_string_left` 兜底全表）
- [ ] **Step 5: 全量回归 + commit**

`venv/bin/python -m pytest tests/ -q` → 全绿（既有 74 用 sqlite :memory: 建表，String 长度不影响）。commit message：`feat: models 全量 String 补 String(255) 长度（MySQL 索引列兼容）（P4-T3）`

- [ ] **Step 6: 停下等用户 Review**

---

### Task 4: ORM JSON 双库兼容性保障 + 中文防乱码守卫

**Files:**
- Test: `backend/tests/test_json_dual_engine.py`（新建，不改业务代码——JSON 列读写由 SQLAlchemy 透明处理，本 Task 是验证 + 守卫）

**Interfaces:**
- Produces：双引擎 JSON 读写验证矩阵（含 P3-3b cast 缓存结构与中文编码守卫）。
- Consumes：既有 `Column(JSON)` 模型（`MediaTag.tags` / `TaskActionLog.detail` / `AutoTaskFlow.context` / `ScanRunLog.details` / `WashHistory.wash_params` / `MediaSyncStatus.douban_cast_cache`）。

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_json_dual_engine.py`：

```python
"""P4 Task 4 — ORM JSON 双库兼容 + 中文防乱码守卫测试。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import json
import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from database import Base
from models import MediaTag, MediaSyncStatus, TaskActionLog


def _sqlite_session():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    return eng, sessionmaker(bind=eng)


def _write_read(Session):
    db = Session()
    db.add(MediaTag(item_id="m1", name="长剧集", tags=["古装", "悬疑", "文言文"]))
    db.add(MediaSyncStatus(
        emby_item_id="s1", title="我的天才女友 第一季", status="synced",
        douban_cast_cache={
            "fetched_at": "2026-08-04T00:00:00",
            "cast": {"王阳": {"avatar": "http://d/a.jpg", "douban_id": "c1", "role": "高启强"},
                     "张颂文": {"avatar": "http://d/b.jpg", "douban_id": "c2", "role": "安欣"}},
        },
    ))
    db.add(TaskActionLog(task_id=1, tmdb_id=1, title="狂飙", action_type="KEEP_MEDIA",
                         target_name="狂飙", detail={"原因": "整季完结，保留"}))
    db.commit(); db.close()

    db = Session()
    tag = db.query(MediaTag).filter(MediaTag.item_id == "m1").first()
    cast = db.query(MediaSyncStatus).filter(MediaSyncStatus.emby_item_id == "s1").first()
    log = db.query(TaskActionLog).filter(TaskActionLog.task_id == 1).first()
    db.close()
    return tag.tags, cast.douban_cast_cache, log.detail


# ---------- SQLite（默认，必跑） ----------

def test_json_roundtrip_sqlite_chinese():
    _, Session = _sqlite_session()
    tags, cast, detail = _write_read(Session)
    assert tags == ["古装", "悬疑", "文言文"]
    assert cast["fetched_at"] == "2026-08-04T00:00:00"
    assert cast["cast"]["王阳"] == {"avatar": "http://d/a.jpg", "douban_id": "c1", "role": "高启强"}
    assert detail == {"原因": "整季完结，保留"}


def test_json_chinese_not_mangled_bytelevel_sqlite():
    _, Session = _sqlite_session()
    tags, cast, detail = _write_read(Session)
    # 逐字符字节级守卫：任何一字节被编码表污染（� / \u00??）即失败
    raw = json.dumps({"tags": tags, "cast": cast, "detail": detail}, ensure_ascii=False)
    assert "�" not in raw        # U+FFFD 替换符 = 乱码信号
    for ch in "我的天才女友 第一季高启强安欣狂飙":
        assert ch in raw


def test_json_null_column_roundtrip():
    _, Session = _sqlite_session()
    db = Session()
    db.add(MediaSyncStatus(emby_item_id="s-null", title="未缓存", status="pending",
                           douban_cast_cache=None))
    db.commit(); db.close()
    db = Session()
    rec = db.query(MediaSyncStatus).filter(MediaSyncStatus.emby_item_id == "s-null").first()
    assert rec.douban_cast_cache is None   # NULL 不误序列化为 "null"
    db.close()


# ---------- MySQL（门控集成，utf8mb4 全链路） ----------

MYSQL_TEST_URL = os.environ.get("MYSQL_TEST_URL")


@pytest.mark.skipif(not MYSQL_TEST_URL, reason="MYSQL_TEST_URL 未配置")
def test_json_roundtrip_mysql_chinese():
    """中文防乱码守卫：MySQL 上 JSON 列中文回读必须逐字一致。"""
    eng = create_engine(MYSQL_TEST_URL, pool_pre_ping=True, pool_recycle=3600)
    Base.metadata.drop_all(bind=eng)
    Base.metadata.create_all(bind=eng)
    Session = sessionmaker(bind=eng)
    tags, cast, detail = _write_read(Session)
    assert tags == ["古装", "悬疑", "文言文"]
    assert cast["cast"]["王阳"]["role"] == "高启强"
    assert detail == {"原因": "整季完结，保留"}


@pytest.mark.skipif(not MYSQL_TEST_URL, reason="MYSQL_TEST_URL 未配置")
def test_mysql_native_json_type():
    eng = create_engine(MYSQL_TEST_URL, pool_pre_ping=True, pool_recycle=3600)
    cols = {c["name"]: str(c["type"]) for c in inspect(eng).get_columns("media_sync_status")}
    assert cols["douban_cast_cache"] == "JSON"
```

- [ ] **Step 2: 确认 RED**（无——SQLite 侧本就通过，此步写测试先跑通确认基线；MySQL 门控用例默认 skip）
  > 说明：本 Task 无业务改动，测试即交付物。TDD 的红是「MySQL 中文乱码」这一潜在缺陷的守卫——通过 utf8mb4 全链路显式（建库 + 连接串）从根上防。
- [ ] **Step 3: 实现**（无业务代码改动。若门控 MySQL 用例跑挂，检查连接串是否带 `charset=utf8mb4`、建库 collation 是否 utf8mb4）
- [ ] **Step 4: 确认 GREEN**（`venv/bin/python -m pytest tests/test_json_dual_engine.py -v`；无 MYSQL_TEST_URL 时 3 条 SQLite 用例过、2 条 MySQL 用例 skip）
- [ ] **Step 5: 全量回归 + commit**

`venv/bin/python -m pytest tests/ -q` → 全绿。commit message：`test: ORM JSON 双库读写 + 中文防乱码守卫（P4-T4）`

- [ ] **Step 6: 停下等用户 Review**

---

## （已拆分）后续独立 Ops 计划：数据直迁与流量切换

> ⚠️ **本部分已按用户指示拆分为后续独立 Ops 计划，不在本次（Task 1-4 + 回归）范围执行。** 保留为未来 Ops 计划的完整素材：数据字典直迁（SQLite → MySQL）与流量切换（切 `DATABASE_URL` 重启）属高危运维操作，需在双擎基建安全落地后择机独立执行。以下步骤当前**不执行**。

**Files:**
- Add: `backend/scripts/migrate_sqlite_to_mysql.py`（新建，供 Ops 计划使用）
- Test: `backend/tests/test_migrate_script.py`（新建，随 Ops 计划实施）

**Interfaces:**
- Produces：
  - `main()` CLI：`--src`（SQLite 路径，默认 `backend/data/emby_ai.db`）、`--dry-run`（只打行数对比不写库）、`--host/--port/--user/--password/--db`（缺省走 `_build_database_url` 配置）。
  - `_read_rows(src_conn, table) -> list[dict]` —— 源表全量读取，JSON 列（SQLite TEXT）`json.loads` 成 Python 对象，显式主键保留。
  - `_copy_table(src_conn, dst_conn, table)` —— 目标 `INSERT`（显式列清单，跳过自增 id 冲突由「幂等去重」处理）。
  - `_row_count(conn, table) -> int`。
- Consumes：`Base.metadata.create_all` + `_run_migrations`（建目标表）、`_JSON_COLUMNS` 常量。

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_migrate_script.py`：

```python
"""P4 Task 5 — 直迁脚本测试（源读取 + 行变换；MySQL 门控 smoke）。"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from sqlalchemy import create_engine, text
from database import Base
import models  # noqa
from scripts import migrate_sqlite_to_mysql as m


def _make_sqlite_file(tmp_path):
    db_path = tmp_path / "src.db"
    eng = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    with eng.connect() as conn:
        # 插入含中文 + JSON 列的样本行
        conn.execute(text(
            "INSERT INTO media_sync_status (emby_item_id, title, status, douban_cast_cache) "
            "VALUES ('s1', '我的天才女友 第一季', 'synced', :c)"),
            {"c": json.dumps({"fetched_at": "2026-08-04", "cast": {"王阳": {"role": "高启强"}}}, ensure_ascii=False)})
        conn.execute(text(
            "INSERT INTO media_metadata (emby_item_id, parent_id, media_type, title, index_number, parent_index_number) "
            "VALUES ('e1', 's1', 'Episode', '第1集', 1, 1)"))
        conn.commit()
    return str(db_path)


def test_read_rows_parses_json_columns(tmp_path):
    path = _make_sqlite_file(tmp_path)
    src = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    rows = m._read_rows(src, "media_sync_status")
    assert len(rows) == 1
    row = rows[0]
    assert row["emby_item_id"] == "s1"                 # 显式主键保留
    assert row["title"] == "我的天才女友 第一季"         # 中文不损
    assert isinstance(row["douban_cast_cache"], dict)  # TEXT → dict（交 MySQL JSON 序列化）
    assert row["douban_cast_cache"]["cast"]["王阳"]["role"] == "高启强"
    assert row.get("id") is None                       # 无自增 id 列（emby_item_id 是 PK）


def test_read_rows_preserves_int_pk(tmp_path):
    path = _make_sqlite_file(tmp_path)
    src = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    rows = m._read_rows(src, "media_metadata")
    assert rows[0]["emby_item_id"] == "e1"
    assert rows[0]["parent_id"] == "s1"                # 关联不漂移


MYSQL_TEST_URL = os.environ.get("MYSQL_TEST_URL")


@pytest.mark.skipif(not MYSQL_TEST_URL, reason="MYSQL_TEST_URL 未配置")
def test_dry_run_reports_counts(tmp_path):
    """门控：--dry-run 对 MySQL 只打行数对比、不写库。"""
    path = _make_sqlite_file(tmp_path)
    import subprocess, sys
    env = {**os.environ, "DATABASE_URL": MYSQL_TEST_URL}
    out = subprocess.run(
        [sys.executable, m.__file__, "--src", path, "--dry-run"],
        capture_output=True, text=True, env=env, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    assert out.returncode == 0, out.stderr
    assert "media_sync_status" in out.stdout
    assert "dry-run" in out.stdout.lower()
```

> 直迁对真实 MySQL 的完整 smoke（`--dry-run` + 全量）属生产切流步骤，由 Task 5 的 ops 清单人工执行，测试只守源读取与行变换正确性。

- [ ] **Step 2: 确认 RED**（`scripts/migrate_sqlite_to_mysql.py` 不存在 → ImportError）
- [ ] **Step 3: 实现**

创建 `backend/scripts/__init__.py`（空）与 `backend/scripts/migrate_sqlite_to_mysql.py`：

```python
"""一次性直迁脚本：SQLite → MySQL（纯切换，幂等可重跑）。

用法：
  venv/bin/python scripts/migrate_sqlite_to_mysql.py --dry-run          # 先看行数对比
  venv/bin/python scripts/migrate_sqlite_to_mysql.py                    # 正式直迁

流程：建库(utf8mb4) → create_all + _run_migrations 建 MySQL 表 → 逐表直迁
      （保留显式主键，JSON 列 SQLite TEXT → Python dict → MySQL 原生 JSON）→ 行数对比。
"""
import argparse, json, logging, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from database import Base, _run_migrations, _build_database_url
import models  # noqa: 注册所有表

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("migrate")

# 模型里全部 JSON 列（SQLite 落 TEXT，读回需 json.loads）
_JSON_COLUMNS = {"detail", "tags", "wash_params", "context", "details", "douban_cast_cache"}

# 目标表顺序：无 FK 约束，顺序自由；关联表放后阅读友好
_TABLE_ORDER = [
    "media_sync_status", "media_metadata", "actor_profiles", "actor_records",
    "media_tags", "tv_show_details", "completed_season_records", "torrent_records",
    "task_action_logs", "wash_history", "auto_task_flows", "scheduled_tasks", "scan_run_logs",
]


def _row_count(conn, table: str) -> int:
    return conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0


def _read_rows(src_conn, table: str) -> list[dict]:
    """读全表 → dict 列表。JSON 列从 SQLite TEXT 解析为 Python 对象。"""
    cols = [c["name"] for c in inspect(src_conn).get_columns(table)]
    rows = []
    for row in src_conn.execute(text(f"SELECT {', '.join(cols)} FROM {table}")):
        d = dict(zip(cols, row))
        for jcol in _JSON_COLUMNS:
            if jcol in d and d[jcol] is not None:
                d[jcol] = json.loads(d[jcol])   # TEXT → dict（MySQL JSON 列由驱动序列化）
        rows.append(d)
    return rows


def _copy_table(src_conn, dst_conn, table: str, dry_run: bool) -> int:
    rows = _read_rows(src_conn, table)
    if dry_run:
        return len(rows)
    if not rows:
        return 0
    cols = list(rows[0].keys())
    placeholders = ", ".join(":" + c for c in cols)
    col_list = ", ".join(cols)
    dst_conn.execute(text(f"DELETE FROM {table}"))   # 幂等：重跑先清目标
    dst_conn.executemany(
        text(f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"),
        rows,
    )
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description="SQLite → MySQL 一次性直迁")
    parser.add_argument("--src", default=os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "data", "emby_ai.db"), help="SQLite 源文件")
    parser.add_argument("--dry-run", action="store_true", help="只打行数对比，不写库")
    args = parser.parse_args()

    src_url = f"sqlite:///{args.src}"
    dst_url = _build_database_url()

    # 1. 建库（utf8mb4）— 仅 MySQL 目标
    if make_url(dst_url).get_backend_name() == "mysql":
        db = make_url(dst_url).database
        server = make_url(dst_url).set(database=None)
        with create_engine(server).connect() as conn:
            conn.execute(text(
                f"CREATE DATABASE IF NOT EXISTS `{db}` "
                f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"))
            conn.commit()
        logger.info("🛢️  已确保库 %s 为 utf8mb4", db)

    src = create_engine(src_url, connect_args={"check_same_thread": False})
    dst = create_engine(dst_url, pool_pre_ping=True, pool_recycle=3600)

    # 2. 建目标表
    Base.metadata.create_all(bind=dst)
    _run_migrations(dst)

    # 3. 直迁（--dry-run 只计数）
    with src.connect() as sc, dst.connect() as dc:
        logger.info("%-22s %8s %10s", "表", "SQLite", "MySQL(目标)")
        ok = True
        for table in _TABLE_ORDER:
            n_src = _row_count(sc, table)
            n_dst = _copy_table(sc, dc, table, args.dry_run)
            mark = "dry-run" if args.dry_run else ("OK" if n_src == n_dst else "⚠️ 不一致")
            if n_src != n_dst and not args.dry_run:
                ok = False
            logger.info("%-22s %8d %10s %s", table, n_src, n_dst, mark)
        if not args.dry_run:
            dc.commit()
    logger.info("完成%s。验证通过后切 DATABASE_URL 重启；保留 SQLite 文件至线上稳定。",
                "（dry-run，未写库）" if args.dry_run else "")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 确认 GREEN**（`venv/bin/python -m pytest tests/test_migrate_script.py -v`）
- [ ] **Step 5: 全量回归 + commit**

`venv/bin/python -m pytest tests/ -q` → 全绿。commit message：`feat: 新增 SQLite→MySQL 一次性直迁脚本（utf8mb4 + 幂等 + --dry-run）（P4-T5）`

- [ ] **Step 6: 生产切流（ops 清单，人工执行）**

> 严格按设计文档 4d 执行序，需真实 MySQL 可达（192.168.31.135:3008）：
> 1. `venv/bin/python scripts/migrate_sqlite_to_mysql.py --dry-run` → 核对每表行数。
> 2. `venv/bin/python scripts/migrate_sqlite_to_mysql.py` → 正式直迁（自动建库 utf8mb4 + 建表 + 搬数）。
> 3. 停服务 → 配置 `db_type=mysql` + host/port/user/password/name（或直接设 `DATABASE_URL`）→ 重启。
> 4. 抽查 MySQL 数据（`SELECT COUNT(*)` 各表、抽查一条中文标题 + cast 缓存无乱码）。
> 5. **保留 SQLite 文件备份**至线上稳定后再归档。

---

### Task 5: 全量回归

**Files:** 无（验证任务）

- [ ] **Step 1: 默认配置下全量回归**

```bash
cd backend && venv/bin/python -m pytest tests/ -q
```

- 预期：既有 74 个测试全绿 + 本计划新增测试（Task 1 `+6`、Task 2 `+6`、Task 3 `+3`、Task 4 `+5`）全绿。
- **SQLite 测试速度不受损**：全部新测试走 `sqlite :memory:`（内存建表 <10ms/例），无 IO 路径增加；计时对比基线（改动前 0.53s collect + 全量秒级）无显著回退。

- [ ] **Step 2: 门控 MySQL 集成回归（可选）**

配置测试库后跑：`MYSQL_TEST_URL="mysql+pymysql://root:root@192.168.31.135:3008/media_ai_test?charset=utf8mb4" venv/bin/python -m pytest tests/test_migration_dialect.py tests/test_models_mysql.py tests/test_json_dual_engine.py -v` → 验证 MySQL 侧建表/迁移/JSON 中文全链路。未配置 env 时这些用例自动 skip，不阻塞默认回归。

- [ ] **Step 3: 最终 commit（若上一步有产物变更）**

commit message：`test: P4 双库兼容全量回归（默认 SQLite 74+新增全绿）（P4-T6）`

- [ ] **Step 4: 停下等用户 Review 收官**

---

## 跨 Task 风险与缓解

1. **MySQL 索引长度上限**（utf8mb4 VARCHAR(255) = 1020B）：InnoDB DYNAMIC（MySQL ≥ 5.7 默认）3072B 上限下安全。若目标机为旧版 COMPACT（767B），Task 3 门控集成测试 `create_all` 即报错，缓解：索引列改 `String(128)`。
2. **裸 VARCHAR 1064**：Task 2 `_add_column_type` 全局 `VARCHAR(255)` 根治，不再依赖人工逐列检查。
3. **中文乱码**：Task 1 URL 强制 `charset=utf8mb4` + Task 5 建库 `utf8mb4_unicode_ci`，全链路显式；Task 4 守卫测试锁死回读逐字一致。
4. ~~迁移期间 SQLite 持续增长~~（随直迁脚本拆入后续独立 Ops 计划；其幂等重跑 + `--dry-run` 保障由该计划承担，见文末「已拆分」节）。
5. **启动失败静默回退**：纯切换语义——MySQL 不可达时 `create_engine` 建池不炸，但首个连接失败即抛错，启动日志明确提示；不写任何「回退 SQLite」逻辑。
6. **JSON 列 NULL 误序列化**：Task 4 `test_json_null_column_roundtrip` 守卫 `None` 不落 `"null"` 字符串。
7. **新增依赖**：`pymysql` 为纯 Python，requirements.txt 一行；不引入编译依赖。

## 相关文件

- `backend/config/settings.py`（db_type 等离散配置项）
- `backend/database.py`（`_build_database_url` / `_engine_kwargs` / `_run_migrations` 方言化）
- `backend/models.py`（全量 String(255)）
- `requirements.txt`（+ pymysql）
- `backend/tests/test_db_engine.py` / `test_migration_dialect.py` / `test_models_mysql.py` / `test_json_dual_engine.py`（新增）
- 后续 Ops 计划（已拆分）：`backend/scripts/migrate_sqlite_to_mysql.py` + `backend/tests/test_migrate_script.py`
- 前置依赖：`docs/superpowers/specs/2026-08-03-actor-sinicization-governance-design.md` P4 段（4a-4d）
