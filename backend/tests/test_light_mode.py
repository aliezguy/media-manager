"""light_mode 轻量头像测试 — 3a：系列汉化跳过每演员 TMDB 上半场。

目标：让系列汉化（sinicize）不因每演员的 TMDB 详情请求打爆 Provider。
  - resolve_actor_profile(light_mode=True) 跳过整个 TMDB 上半场（0-2 次/演员的大头）
  - 轻量路径只走 L0 本地 → L0.5 Emby 原生 → L1 复用豆瓣演员表直链 → L2 提升已缓存头像
  - ensure_profiles_for_people / save_media_to_db 透传 light_mode，演员库路径保持完整漏斗
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base
from models import ActorProfile
import services.actor_profile_service as aps


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """隔离真实 people/ 文件系统与网络：临时目录 + 关闭冷却缓存。"""
    monkeypatch.setattr(aps, "_PEOPLE_DIR", str(tmp_path / "people"))
    monkeypatch.setattr(aps, "_local_sniff_cache", {})


def _make_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def _cfg():
    return {"douban_enabled": True, "enable_emby_avatar_first": False,
            "douban_cookie": "", "tmdb_api_key": ""}


def _no_download(*a, **k):
    return True


def test_light_mode_skips_tmdb_half(monkeypatch):
    Session = _make_db()
    db = Session()
    monkeypatch.setattr(aps, "load_config", lambda: _cfg())
    monkeypatch.setattr(aps, "_find_local_avatar", lambda name: None)
    monkeypatch.setattr(aps, "_download_image", _no_download)

    def boom(*a, **k):
        raise AssertionError("light_mode=True 不应触发 TMDB 请求")
    monkeypatch.setattr(aps, "fetch_tmdb_person_details", boom)

    prof = aps.resolve_actor_profile(
        "UniqueLightActor", db,
        context_info={"douban_avatar_url": "http://douban.test/a.jpg", "douban_id": "c1"},
        light_mode=True,
    )
    assert prof is not None
    assert prof["source"] == "douban"          # L1 豆瓣演员表直链命中
    assert prof["image_url"] == "http://douban.test/a.jpg"
    assert prof["tmdb_id"] == ""               # 未走 TMDB 上半场，无 TMDB 元数据
    db.close()


def test_full_mode_still_fetches_tmdb(monkeypatch):
    Session = _make_db()
    db = Session()
    monkeypatch.setattr(aps, "load_config", lambda: _cfg())
    monkeypatch.setattr(aps, "_find_local_avatar", lambda name: None)
    monkeypatch.setattr(aps, "_download_image", _no_download)

    called = []
    monkeypatch.setattr(aps, "fetch_tmdb_person_details",
                        lambda *a, **k: called.append(1) or None)

    prof = aps.resolve_actor_profile(
        "UniqueFullActor", db,
        context_info={"douban_avatar_url": "http://douban.test/a.jpg", "douban_id": "c1"},
        light_mode=False,   # 默认完整漏斗
    )
    assert called, "light_mode=False 应调用 TMDB 上半场"
    assert prof is not None
    db.close()


def test_light_mode_promotes_cached_image_url(monkeypatch):
    Session = _make_db()
    db = Session()
    # 历史完整跑留下的外链，但本地文件丢失、冷却已过期（8 天前）
    db.add(ActorProfile(
        name="OldActor", image_url="http://cached.test/x.jpg", source="tmdb",
        tmdb_id="777", update_time=datetime.now() - timedelta(days=8),
    ))
    db.flush()
    monkeypatch.setattr(aps, "load_config", lambda: _cfg())
    monkeypatch.setattr(aps, "_find_local_avatar", lambda name: None)

    downloads = []
    def _record_download(url, *a, **k):
        downloads.append(url)
        return True
    monkeypatch.setattr(aps, "_download_image", _record_download)

    def boom(*a, **k):
        raise AssertionError("light_mode=True 不应触发 TMDB 请求")
    monkeypatch.setattr(aps, "fetch_tmdb_person_details", boom)

    prof = aps.resolve_actor_profile("OldActor", db, context_info={}, light_mode=True)
    assert prof is not None
    assert prof["image_url"] == "http://cached.test/x.jpg"  # L2 提升已缓存头像
    # ★ 关键硬化：3.5 提升块必须真正把已缓存外链送入下载路径。
    #   （若不靠此断言，UPSERT 的 image_url 兜底会掩盖 3.5 块缺失）
    assert downloads, "light_mode=True 应触发头像下载（3.5 提升）"
    assert "http://cached.test/x.jpg" in downloads
    db.close()


import services.douban_service as ds
import services.db_crud as dbc


def test_ensure_profiles_forwards_light_mode(monkeypatch):
    Session = _make_db()
    db = Session()
    captured = {}

    def fake_resolve(name, db, context_info=None, force_refresh=False, light_mode=False):
        captured["light_mode"] = light_mode
        return {"name": name, "local_image_path": "", "image_url": "",
                "local_image_url": "", "source": "", "tmdb_id": "", "imdb_id": "",
                "douban_celebrity_id": "", "birth_date": "", "birth_place": "", "overview": ""}
    monkeypatch.setattr(aps, "resolve_actor_profile", fake_resolve)

    aps.ensure_profiles_for_people(db, [{"Name": "A", "Type": "Actor"}], light_mode=True)
    assert captured["light_mode"] is True
    db.close()


def test_ensure_profiles_default_full_mode(monkeypatch):
    Session = _make_db()
    db = Session()
    captured = {}

    def fake_resolve(name, db, context_info=None, force_refresh=False, light_mode=False):
        captured["light_mode"] = light_mode
        return {"name": name, "local_image_path": "", "image_url": "",
                "local_image_url": "", "source": "", "tmdb_id": "", "imdb_id": "",
                "douban_celebrity_id": "", "birth_date": "", "birth_place": "", "overview": ""}
    monkeypatch.setattr(aps, "resolve_actor_profile", fake_resolve)

    aps.ensure_profiles_for_people(db, [{"Name": "A", "Type": "Actor"}])  # 默认 False
    assert captured["light_mode"] is False
    db.close()


def test_save_media_to_db_forwards_light_profiles(monkeypatch):
    Session = _make_db()
    db = Session()
    captured = {}

    def fake_ensure(db, people, light_mode=False):
        captured["light_mode"] = light_mode
    monkeypatch.setattr(dbc, "ensure_profiles_for_people", fake_ensure)

    dbc.save_media_to_db(
        db,
        emby_item={"Id": "s1", "Name": "九门", "Type": "Series",
                   "People": [{"Name": "A", "Type": "Actor"}]},
        people=[{"Name": "A", "Type": "Actor"}],
        light_profiles=True,
    )
    assert captured["light_mode"] is True
    db.close()


def test_save_media_to_db_default_full_mode(monkeypatch):
    Session = _make_db()
    db = Session()
    captured = {}

    def fake_ensure(db, people, light_mode=False):
        captured["light_mode"] = light_mode
    monkeypatch.setattr(dbc, "ensure_profiles_for_people", fake_ensure)

    dbc.save_media_to_db(
        db,
        emby_item={"Id": "s2", "Name": "无", "Type": "Movie",
                   "People": [{"Name": "B", "Type": "Actor"}]},
        people=[{"Name": "B", "Type": "Actor"}],
        # 不传 light_profiles → 默认完整漏斗
    )
    assert captured["light_mode"] is False
    db.close()


class _NoTranslator:
    def is_available(self):
        return False


def _make_sinizer(monkeypatch):
    """复用 test_episode_via_parent 的 _fresh_db 模式：内存库 + emby 配置。"""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    monkeypatch.setattr(ds, "SessionLocal", TestSession)
    monkeypatch.setattr(ds, "load_config", lambda: {
        "emby_host": "http://emby.test", "emby_api_key": "k",
        "emby_user_id": "u", "max_actors_per_media": 50,
    })
    return ds.DoubanSinizer()


def _stub_sinicize_flow(s, monkeypatch):
    """stub sinicize 全链路下游，返回两个 spy 列表。"""
    calls = {"save_light_profiles": [], "ensure_light_modes": []}

    monkeypatch.setattr(s, "_get_emby_item", lambda sid: {
        "Id": sid, "Name": "九门", "Type": "Series",
        "People": [{"Name": "Sun Honglei", "Type": "Actor", "Role": "Li Yan"}],
        "ProviderIds": {"Imdb": "tt0000001"}, "ProductionYear": "2020",
    })
    monkeypatch.setattr(s, "_find_douban_id", lambda *a, **k: "123")
    monkeypatch.setattr(s, "_fetch_douban_actors", lambda did: [
        {"name": "孙红雷", "role": "李岩", "avatar": "http://douban.test/a.jpg", "id": "c1"},
    ])

    def fake_match(emby_actors, douban_actors, db=None, emby_item_id="", parent_id="",
                   provider_tmdb_ids=None):
        return ([
            dict(a, Name="孙红雷", Role="李岩",
                 _cn_name_conf=4, _cn_name_src="official",
                 _cn_role_conf=4, _cn_role_src="official")
            for a in emby_actors
        ], [], {}, {}, {}, {})
    monkeypatch.setattr(s, "_match_and_update", fake_match)
    monkeypatch.setattr(ds, "get_translator", lambda: _NoTranslator())
    monkeypatch.setattr(s, "_write_back_emby", lambda *a, **k: True)

    def fake_save(db, **kw):  # db 是真实 save_media_to_db 的首个位置参数
        calls["save_light_profiles"].append(kw.get("light_profiles", False))
    monkeypatch.setattr(ds, "save_media_to_db", fake_save)

    monkeypatch.setattr(s, "_fetch_episodes", lambda *a, **k: [
        {"Id": "e1", "Name": "第 1 集", "ParentIndexNumber": 1, "IndexNumber": 1,
         "People": [{"Name": "Sun Honglei", "Type": "Actor", "Role": "Li Yan"}]},
    ])

    def fake_ensure(db, people, light_mode=False):
        calls["ensure_light_modes"].append(light_mode)
    monkeypatch.setattr(ds, "ensure_profiles_for_people", fake_ensure)

    monkeypatch.setattr(s, "_localize_episode_people", lambda *a, **k: [
        {"Name": "孙红雷", "Role": "李岩", "Type": "Actor"},
    ])
    monkeypatch.setattr(s, "_write_back_episode", lambda *a, **k: True)
    return calls


def test_sinicize_series_uses_light_mode(monkeypatch):
    s = _make_sinizer(monkeypatch)
    calls = _stub_sinicize_flow(s, monkeypatch)

    result = s.sinicize("s1")

    assert result["success"] is True
    # 顶层 Series 入库走 light_profiles=True（在 499 之前执行，若为 False 顶层演员仍会打 TMDB）
    assert True in calls["save_light_profiles"]
    # 分集前置批处理走 light_mode=True
    assert calls["ensure_light_modes"] == [True]
