"""get_actor_items sync_status 归一化测试 — NULL → 'pending'，failed 原样透传。

对应设计文档 Phase 2 改动点 2：rec.get("status", "pending") → rec.get("status") or "pending"
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from database import Base
from models import MediaSyncStatus
import routers.emby as emby


class _FakeResp:
    """模拟 requests.get 返回值。"""
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _make_session(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    monkeypatch.setattr(emby, "SessionLocal", TestSession)
    return TestSession


def _seed(db):
    # s1: status 列真实 NULL（从未审计）—— 必须用原始 SQL 显式插 NULL，
    #     ORM 传 status=None 会被 Column(default="pending") 兜底成 'pending'
    db.execute(
        text("INSERT INTO media_sync_status (emby_item_id, library_id, title, status) "
             "VALUES ('s1', 'lib1', 'A', NULL)")
    )
    db.add(MediaSyncStatus(emby_item_id="s2", library_id="lib1", title="B", status="failed"))
    db.add(MediaSyncStatus(emby_item_id="s3", library_id="lib1", title="C", status="synced"))
    db.commit()
    db.close()


def _items(ids):
    return [{"Id": i, "Name": n, "Type": "Series", "People": [], "ProviderIds": {}}
            for i, n in zip(ids, ("A", "B", "C"))]


def _req(status_filter=None, limit=-1):
    return emby.ActorItemsRequest(
        emby_host="http://emby.test", emby_api_key="k", emby_user_id="u",
        library_id="lib1", limit=limit, start_index=0,
        status_filter=status_filter, search=None,
    )


def test_status_filter_path_null_to_pending(monkeypatch):
    TestSession = _make_session(monkeypatch)
    _seed(TestSession())
    # status_filter='pending' → DB 筛出 s1(NULL) + s2(failed)，Emby 仅按这些 ID 拉取
    monkeypatch.setattr(
        emby.requests, "get",
        lambda *a, **k: _FakeResp({"Items": _items(["s1", "s2"])}),
    )

    result = emby.get_actor_items(_req(status_filter="pending"))
    status_map = {it["id"]: it["sync_status"] for it in result["items"]}

    assert status_map["s1"] == "pending"  # NULL → pending（修复前为 None）
    assert status_map["s2"] == "failed"   # failed 原样透传


def test_nofilter_path_null_to_pending(monkeypatch):
    TestSession = _make_session(monkeypatch)
    _seed(TestSession())
    monkeypatch.setattr(
        emby.requests, "get",
        lambda *a, **k: _FakeResp({"Items": _items(["s1", "s2", "s3"]), "TotalRecordCount": 3}),
    )

    result = emby.get_actor_items(_req(status_filter=None, limit=-1))
    status_map = {it["id"]: it["sync_status"] for it in result["items"]}

    assert status_map["s1"] == "pending"  # NULL → pending（修复前为 None）
    assert status_map["s2"] == "failed"
    assert status_map["s3"] == "synced"


def test_status_filter_failed_returns_only_failed(monkeypatch):
    TestSession = _make_session(monkeypatch)
    _seed(TestSession())
    # status_filter='failed' → DB 只筛出 s2，Emby 按该 ID 拉取
    monkeypatch.setattr(
        emby.requests, "get",
        lambda *a, **k: _FakeResp({"Items": _items(["s2"])}),
    )

    result = emby.get_actor_items(_req(status_filter="failed"))
    ids = [it["id"] for it in result["items"]]

    assert ids == ["s2"]
    assert result["total"] == 1   # broken 时 total=2（s1+s2），修复后=1
