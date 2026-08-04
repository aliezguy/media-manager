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
