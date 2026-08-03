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
        {"Id": "e1", "Name": "E1", "ParentIndexNumber": 1, "IndexNumber": 1, "Type": "Episode", "ProviderIds": {}},
        {"Id": "e2", "Name": "E2", "ParentIndexNumber": 1, "IndexNumber": 2, "Type": "Episode", "ProviderIds": {}},
        {"Id": "e3", "Name": "E3", "ParentIndexNumber": 1, "IndexNumber": 3, "Type": "Episode", "ProviderIds": {}},
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
        {"Id": "e1", "Name": "E1", "ParentIndexNumber": 1, "IndexNumber": 1, "Type": "Episode", "ProviderIds": {}},
        {"Id": "e2", "Name": "E2", "ParentIndexNumber": 1, "IndexNumber": 2, "Type": "Episode", "ProviderIds": {}},
        {"Id": "e3", "Name": "E3", "ParentIndexNumber": 1, "IndexNumber": 3, "Type": "Episode", "ProviderIds": {}},
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
        {"Id": "e1", "Name": "E1", "ParentIndexNumber": 1, "IndexNumber": 1, "Type": "Episode", "ProviderIds": {}},
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
