"""L1 豆瓣详情提取测试 — 重试 + 限流 + 启动验证（mock DoubanApi，不触网）。"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import ActorProfile
from services.douban_api import DoubanApi
import services.actor_profile_service as aps


def _mem_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


# ================================================================
# _douban_celebrity_details_with_retry: 重试 + 永久失败熔断
# ================================================================

def test_details_success_first_try(monkeypatch):
    monkeypatch.setattr(DoubanApi, "celebrity_details",
                        lambda self, cid: {"born_place": "中国北京"})
    out = aps._douban_celebrity_details_with_retry("张三", "123", "cookie")
    assert out == {"born_place": "中国北京"}


def test_details_retry_on_rate_limit(monkeypatch):
    calls = {"n": 0}
    def fake(self, cid):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"error": "rate_limit", "message": "too fast"}
        return {"born_place": "韩国首尔"}
    monkeypatch.setattr(DoubanApi, "celebrity_details", fake)
    sleeps = []
    monkeypatch.setattr(aps._time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(aps.random, "uniform", lambda a, b: 0.0)

    out = aps._douban_celebrity_details_with_retry("张三", "123", "cookie")
    assert calls["n"] == 2, "限流应重试一次后成功"
    assert out == {"born_place": "韩国首尔"}
    assert sleeps and sleeps[0] >= 1.5, "重试前应有指数退避"


def test_details_need_login_no_retry(monkeypatch):
    calls = {"n": 0}
    def fake(self, cid):
        calls["n"] += 1
        return {"error": "need_login", "message": "login required"}
    monkeypatch.setattr(DoubanApi, "celebrity_details", fake)

    out = aps._douban_celebrity_details_with_retry("张三", "123", "cookie")
    assert calls["n"] == 1, "need_login 是永久失败，不应重试"
    assert out is None


def test_details_not_found_no_retry(monkeypatch):
    calls = {"n": 0}
    def fake(self, cid):
        calls["n"] += 1
        return {"error": "movie_not_found"}
    monkeypatch.setattr(DoubanApi, "celebrity_details", fake)

    out = aps._douban_celebrity_details_with_retry("张三", "123", "cookie")
    assert calls["n"] == 1
    assert out is None


def test_details_retries_exhausted(monkeypatch):
    calls = {"n": 0}
    def fake(self, cid):
        calls["n"] += 1
        return {"error": "rate_limit", "message": "still limited"}
    monkeypatch.setattr(DoubanApi, "celebrity_details", fake)
    monkeypatch.setattr(aps._time, "sleep", lambda s: None)
    monkeypatch.setattr(aps.random, "uniform", lambda a, b: 0.0)

    out = aps._douban_celebrity_details_with_retry("张三", "123", "cookie")
    assert calls["n"] == 3, "1 次首试 + 2 次重试"
    assert out is None


def test_details_exception_then_success(monkeypatch):
    calls = {"n": 0}
    def fake(self, cid):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("boom")
        return {"info": "某演员简介"}
    monkeypatch.setattr(DoubanApi, "celebrity_details", fake)
    monkeypatch.setattr(aps._time, "sleep", lambda s: None)
    monkeypatch.setattr(aps.random, "uniform", lambda a, b: 0.0)

    out = aps._douban_celebrity_details_with_retry("张三", "123", "cookie")
    assert calls["n"] == 2
    assert out == {"info": "某演员简介"}


# ================================================================
# resolve_actor_profile 集成: L1 豆瓣详情块已启动
# ================================================================

def test_resolve_l1_douban_detail_fetched(monkeypatch):
    """L1 豆瓣详情块启动验证：有 douban_id 且无上下文头像 → 调详情提取中文元数据。"""
    Session = _mem_db()
    db = Session()
    monkeypatch.setattr(aps, "load_config", lambda: {
        "douban_enabled": True, "enable_emby_avatar_first": False,
        "douban_cookie": "ck=1", "tmdb_api_key": "",
        "actor_ai_enabled": False,   # 关闭 LLM 补全，聚焦豆瓣原生中文路径
    })
    monkeypatch.setattr(aps, "_find_local_avatar", lambda name: None)
    monkeypatch.setattr(aps, "_download_image", lambda *a, **k: True)

    called = []
    def fake_detail(actor_name, douban_id, cookie):
        called.append((actor_name, douban_id, cookie))
        return {"born_place": "中国北京市", "info": "著名演员。", "birthday": "1978-02-17"}
    monkeypatch.setattr(aps, "_douban_celebrity_details_with_retry", fake_detail)

    prof = aps.resolve_actor_profile(
        "张三", db, context_info={"douban_id": "123"}, light_mode=True,
    )
    assert called == [("张三", "123", "ck=1")], "L1 详情块应被触发"
    assert prof["birth_place"] == "中国北京市"
    assert prof["overview"] == "著名演员。"
    assert prof["birth_date"] == "1978-02-17"
    row = db.query(ActorProfile).filter(ActorProfile.name == "张三").first()
    assert row.birth_place == "中国北京市"
    db.close()


def test_resolve_l1_douban_skipped_when_avatar_already_set(monkeypatch):
    """上下文自带豆瓣头像直链时，详情块仍应跳过（头像已有，不浪费请求）。"""
    Session = _mem_db()
    db = Session()
    monkeypatch.setattr(aps, "load_config", lambda: {
        "douban_enabled": True, "enable_emby_avatar_first": False,
        "douban_cookie": "ck=1", "tmdb_api_key": "",
        "actor_ai_enabled": False,
    })
    monkeypatch.setattr(aps, "_find_local_avatar", lambda name: None)
    monkeypatch.setattr(aps, "_download_image", lambda *a, **k: True)
    called = []
    monkeypatch.setattr(aps, "_douban_celebrity_details_with_retry",
                        lambda *a, **k: called.append(1) or None)

    prof = aps.resolve_actor_profile(
        "张三", db,
        context_info={"douban_id": "123", "douban_avatar_url": "http://douban.test/a.jpg"},
        light_mode=True,
    )
    assert called == [], "已有头像直链时不应再调详情"
    assert prof["source"] == "douban"
    db.close()
