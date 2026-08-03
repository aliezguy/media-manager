"""_audit_and_save_single_item 计数刷新测试 — Series 分集入库后实算 recursive_item_count。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base
from models import MediaMetadata
import routers.sync_actions as sa


def _make_session(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    monkeypatch.setattr(sa, "SessionLocal", TestSession)
    return TestSession


def _mock_env(monkeypatch):
    monkeypatch.setattr(sa, "load_config", lambda: {"max_actors_per_media": 50})


def test_series_audit_refreshes_count(monkeypatch):
    TestSession = _make_session(monkeypatch)
    _mock_env(monkeypatch)
    # Emby 实际 3 集；item.RecursiveItemCount 为 stale 值 7（模拟"实际 12 集只入库 7 集"）
    monkeypatch.setattr(sa, "_fetch_episodes", lambda *a, **k: [
        {"Id": "e1", "Name": "E1", "Type": "Episode", "ParentIndexNumber": 1, "IndexNumber": 1, "ProviderIds": {}},
        {"Id": "e2", "Name": "E2", "Type": "Episode", "ParentIndexNumber": 1, "IndexNumber": 2, "ProviderIds": {}},
        {"Id": "e3", "Name": "E3", "Type": "Episode", "ParentIndexNumber": 1, "IndexNumber": 3, "ProviderIds": {}},
    ])
    db = TestSession()
    # 空 People → _is_chinese_role_synced([]) == False → 走 else（未汉化）分支，无需 ensure_profiles_for_people
    item = {"Id": "s1", "Name": "Test", "Type": "Series", "People": [],
            "RecursiveItemCount": 7}  # stale
    result = sa._audit_and_save_single_item(
        db, item, "http://emby.test", "k", "u", library_id="lib1")

    assert result["episodes_processed"] == 3
    series_mm = db.query(MediaMetadata).filter(MediaMetadata.emby_item_id == "s1").first()
    assert series_mm.recursive_item_count == 3  # 实算覆盖 stale 7
    db.close()


def test_series_zero_episodes_keeps_stale_count(monkeypatch):
    TestSession = _make_session(monkeypatch)
    _mock_env(monkeypatch)
    monkeypatch.setattr(sa, "_fetch_episodes", lambda *a, **k: [])
    db = TestSession()
    item = {"Id": "s1", "Name": "Test", "Type": "Series", "People": [],
            "RecursiveItemCount": 7}
    result = sa._audit_and_save_single_item(
        db, item, "http://emby.test", "k", "u", library_id="lib1")

    assert result["episodes_processed"] == 0
    series_mm = db.query(MediaMetadata).filter(MediaMetadata.emby_item_id == "s1").first()
    assert series_mm.recursive_item_count == 7  # episodes_processed == 0 → 不刷新
    db.close()
