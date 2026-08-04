"""P4 Task 3 — models String 长度补齐测试。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from sqlalchemy import create_engine
from database import Base
import models  # noqa: 注册所有表

# 必须有显式 String 长度的列（主键 / 索引列，MySQL 建表必需）
# 注意：task_id / tmdb_id 等 Integer 列无需长度，不在此列
REQUIRED_LENGTH_COLUMNS = {
    "media_sync_status": ["emby_item_id", "tmdb_id", "imdb_id", "douban_id", "library_id"],
    "media_metadata": ["emby_item_id", "parent_id"],
    "actor_profiles": ["name", "tmdb_id", "imdb_id", "douban_celebrity_id"],
    "actor_records": ["emby_item_id", "name"],
    "media_tags": ["item_id"],
    "torrent_records": ["hash"],
    "wash_history": ["name"],
}


def test_indexed_string_columns_have_255_length():
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
