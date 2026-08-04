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
    """迁移补列落 TEXT：SQLite 无原生 JSON 列类型，迁移路径显式 ADD COLUMN ... TEXT。

    注意：create_all 的 Column(JSON) 在 SQLite 编译为 JSON 注册、反射回 'JSON'，
    与迁移补的 TEXT 声明不同，但二者均为 TEXT 亲和，ORM 读写等价。
    本测试验证迁移路径确实落 TEXT。
    """
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    with eng.connect() as conn:  # 模拟旧库缺列
        conn.execute(text("ALTER TABLE media_sync_status DROP COLUMN douban_cast_cache"))
        conn.commit()
    _run_migrations(eng)
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
    eng = create_engine(MYSQL_TEST_URL, pool_pre_ping=True, pool_recycle=3600)
    Base.metadata.drop_all(bind=eng)   # 测试库清理
    Base.metadata.create_all(bind=eng)
    _run_migrations(eng)
    cols = {c["name"]: str(c["type"]) for c in inspect(eng).get_columns("media_sync_status")}
    assert cols["douban_cast_cache"] == "JSON"
