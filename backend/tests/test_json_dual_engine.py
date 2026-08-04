"""P4 Task 3 — ORM JSON 双库兼容 + 中文防乱码守卫测试。

覆盖 P3-3b douban_cast_cache 的 {fetched_at, cast} 结构、MediaTag.tags 列表、
TaskActionLog.detail 嵌套 dict，在 SQLite（默认必跑）与 MySQL（MYSQL_TEST_URL 门控）
上做逐字回读与 U+FFFD 乱码守卫。
"""
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
    # 逐字符字节级守卫：任何一字节被编码表污染（U+FFFD 替换符）即失败。
    # 断言字符集 = payload 实际包含的中文字符（标题/名字等未进 JSON 的不在此列）。
    raw = json.dumps({"tags": tags, "cast": cast, "detail": detail}, ensure_ascii=False)
    assert "�" not in raw        # U+FFFD 替换符 = 乱码信号
    for ch in "古装悬疑文言文王阳张颂文高启强安欣原因整季完结，保留":
        assert ch in raw, f"字符 {ch!r} 在 JSON 回读中丢失/被污染"


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
