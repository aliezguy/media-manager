"""单集汉化走父 Series 测试 — 委派钩子 + 上下文解析。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base
from models import MediaSyncStatus
import services.douban_service as ds


def _fresh_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    monkeypatch.setattr(ds, "SessionLocal", TestSession)
    # DoubanSinizer.__init__ 读取 emby 配置，测试环境补齐避免 sinicize 早退
    monkeypatch.setattr(ds, "load_config", lambda: {
        "emby_host": "http://emby.test",
        "emby_api_key": "k",
        "emby_user_id": "u",
        "max_actors_per_media": 50,
    })
    return TestSession


def test_sinicize_episode_delegates(monkeypatch):
    _fresh_db(monkeypatch)  # 补齐 emby 配置，防止 sinicize 早退
    s = ds.DoubanSinizer()
    captured = {}
    def fake_get(item_id):
        return {"Id": item_id, "Name": "第 26 集", "Type": "Episode",
                "SeriesId": "s1", "People": [{"Name": "A", "Type": "Actor"}]}
    def fake_via_parent(ep_id, ep):
        captured["ep_id"] = ep_id
        return {"success": True, "matched": 1, "total_actors": 1, "details": []}
    monkeypatch.setattr(s, "_get_emby_item", fake_get)
    monkeypatch.setattr(s, "_sinicize_episode_via_parent", fake_via_parent)

    result = s.sinicize("e26")
    assert captured["ep_id"] == "e26"
    assert result["success"] is True


def test_resolve_series_context_db_cache_hit(monkeypatch):
    TestSession = _fresh_db(monkeypatch)
    db = TestSession()
    db.add(MediaSyncStatus(emby_item_id="s1", title="九门", status="synced", douban_id="12345"))
    db.commit(); db.close()

    s = ds.DoubanSinizer()
    monkeypatch.setattr(s, "_get_emby_item", lambda sid: {
        "Id": sid, "Name": "九门", "Type": "Series",
        "People": [{"Name": "Chen", "Type": "Actor", "Role": "Lead"}],
        "ProviderIds": {"Imdb": "tt0000001"},
    })
    monkeypatch.setattr(s, "_find_douban_id", lambda *a, **k: "99999")  # 不应被调用
    monkeypatch.setattr(s, "_fetch_douban_actors", lambda did: [
        {"name": "陈", "role": "主演", "avatar": "", "id": "c1"},
    ])

    ctx = s._resolve_series_douban_context("s1")
    assert ctx is not None
    douban_id, actors, match_map = ctx
    assert douban_id == "12345"  # DB 缓存命中，未触发 find
    assert actors[0]["name"] == "陈"


def test_resolve_series_context_finds_and_writes_back(monkeypatch):
    TestSession = _fresh_db(monkeypatch)
    db = TestSession()
    db.add(MediaSyncStatus(emby_item_id="s1", title="九门", status="synced"))  # 无 douban_id
    db.commit(); db.close()

    s = ds.DoubanSinizer()
    monkeypatch.setattr(s, "_get_emby_item", lambda sid: {
        "Id": sid, "Name": "九门", "Type": "Series",
        "People": [{"Name": "Chen", "Type": "Actor", "Role": "Lead"}],
        "ProviderIds": {"Imdb": "tt0000001"},
    })
    monkeypatch.setattr(s, "_find_douban_id", lambda pids, title, mtype, year: "12345")
    monkeypatch.setattr(s, "_fetch_douban_actors", lambda did: [
        {"name": "陈", "role": "主演", "avatar": "", "id": "c1"},
    ])

    ctx = s._resolve_series_douban_context("s1")
    assert ctx is not None
    assert ctx[0] == "12345"
    # 已回写 DB 缓存
    db = TestSession()
    rec = db.query(MediaSyncStatus).filter(MediaSyncStatus.emby_item_id == "s1").first()
    assert rec.douban_id == "12345"
    db.close()


def test_resolve_series_context_creates_record_when_missing(monkeypatch):
    TestSession = _fresh_db(monkeypatch)
    # 父 Series 无 DB 记录 → 解析成功后自动新建 MediaSyncStatus 并保存 douban_id，
    # 避免后续单集汉化重复触发系列级豆瓣查找
    s = ds.DoubanSinizer()
    monkeypatch.setattr(s, "_get_emby_item", lambda sid: {
        "Id": sid, "Name": "九门", "Type": "Series",
        "People": [{"Name": "Chen", "Type": "Actor", "Role": "Lead"}],
        "ProviderIds": {"Imdb": "tt0000001"},
    })
    monkeypatch.setattr(s, "_find_douban_id", lambda pids, title, mtype, year: "12345")
    monkeypatch.setattr(s, "_fetch_douban_actors", lambda did: [
        {"name": "陈", "role": "主演", "avatar": "", "id": "c1"},
    ])

    ctx = s._resolve_series_douban_context("s1")
    assert ctx is not None
    assert ctx[0] == "12345"
    # 无记录 → 已自动新建并保存 douban_id
    db = TestSession()
    rec = db.query(MediaSyncStatus).filter(MediaSyncStatus.emby_item_id == "s1").first()
    assert rec is not None
    assert rec.douban_id == "12345"
    assert rec.status == "pending"
    assert rec.title == "九门"
    db.close()
