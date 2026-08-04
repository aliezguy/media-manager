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
