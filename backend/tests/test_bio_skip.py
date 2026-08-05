"""skip_llm_enrich 三态参数测试 — 演员简介内联补全解耦。

核心语义（见 docs/superpowers/plans/2026-08-05-sinicize-actor-bio-optimization.md）:
  - skip_llm_enrich=None（默认）→ 跟随配置 actor_bio_inline_enabled（False=跳过简介，True=旧行为内联补）
  - skip_llm_enrich=True  → 强制跳过简介 LLM（汉化/审计默认路径的显式形态）
  - skip_llm_enrich=False → 强制补全（演员库刷新/修复路径，不受配置影响）

全部 Boom 探针断言调用次数，不触网、不真调 LLM。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import ActorProfile
import services.actor_profile_ai as apa
import services.actor_profile_service as aps
import services.db_crud as dbc


def _mem_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def _cfg_with_ai(inline_enabled=None):
    cfg = {
        "douban_enabled": True, "enable_emby_avatar_first": False,
        "douban_cookie": "", "tmdb_api_key": "",
        "sf_api_key": "x", "llm_base_url": "https://api.test/v1", "llm_model_name": "m",
        "actor_ai_enabled": True, "actor_ai_local_first": True, "llm_cooldown_days": 7,
    }
    if inline_enabled is not None:
        cfg["actor_bio_inline_enabled"] = inline_enabled
    return cfg


def _seed_cache_hit(Session, name="Zhang Yi"):
    """L0 数据库极速命中：有本地头像路径 + 物理文件存在。"""
    db = Session()
    db.add(ActorProfile(
        name=name, local_image_path="张/张译/folder.png",
        birth_place="Harbin, Canada", overview="A bio", source="tmdb",
        update_time=datetime.now(),
    ))
    db.flush()
    return db


def _boom_enrich_existing(calls):
    """Boom 探针：记录 _llm_enrich_existing 调用（含 kwargs），不触发任何 LLM。"""
    def _probe(*args, **kwargs):
        calls.append(kwargs)
        return None
    return _probe


# ================================================================
# 三态语义
# ================================================================

def test_skip_true_l0_cache_hit_no_enrich(monkeypatch):
    """skip_llm_enrich=True：L0 数据库极速命中时 _llm_enrich_existing 0 次调用。

    config 里 inline_enabled=True 也强制跳过 → 证明显式 True 优先于配置。
    """
    db = _seed_cache_hit(_mem_db())
    calls = []
    monkeypatch.setattr(aps, "load_config", lambda: _cfg_with_ai(inline_enabled=True))
    monkeypatch.setattr(aps, "_local_file_exists", lambda p: True)
    monkeypatch.setattr(aps, "_llm_enrich_existing", _boom_enrich_existing(calls))

    prof = aps.resolve_actor_profile(
        "Zhang Yi", db, context_info={}, light_mode=True, skip_llm_enrich=True,
    )
    assert prof is not None, "L0 命中仍应返回已有档案"
    assert calls == [], "skip_llm_enrich=True 时 L0 命中不得调用 _llm_enrich_existing"
    db.close()


def test_skip_true_full_network_no_enrich(monkeypatch):
    """skip_llm_enrich=True：完整网络路径（light_mode=False）时 enrich_actor_metadata 0 次调用。"""
    db = _mem_db()()
    calls = []
    monkeypatch.setattr(aps, "load_config", lambda: _cfg_with_ai(inline_enabled=True))
    monkeypatch.setattr(aps, "_local_file_exists", lambda p: True)
    monkeypatch.setattr(aps, "_find_local_avatar", lambda name: None)
    monkeypatch.setattr(aps, "fetch_tmdb_person_details", lambda *a, **k: None)
    monkeypatch.setattr(aps, "_ai_providers_available", lambda cfg: True)
    monkeypatch.setattr(
        apa, "enrich_actor_metadata",
        lambda *a, **k: calls.append(1) or ({}, 1, datetime.now(), "", {}),
    )

    aps.resolve_actor_profile(
        "NoOne", db, context_info={}, light_mode=False, skip_llm_enrich=True,
    )
    assert calls == [], "skip_llm_enrich=True 时完整网络路径也不得调用 enrich_actor_metadata"
    db.close()


def test_skip_none_config_false_skips(monkeypatch):
    """skip_llm_enrich=None + config actor_bio_inline_enabled=False → 同 skip=True（跟随配置）。"""
    db = _seed_cache_hit(_mem_db())
    calls = []
    monkeypatch.setattr(aps, "load_config", lambda: _cfg_with_ai(inline_enabled=False))
    monkeypatch.setattr(aps, "_local_file_exists", lambda p: True)
    monkeypatch.setattr(aps, "_llm_enrich_existing", _boom_enrich_existing(calls))

    prof = aps.resolve_actor_profile("Zhang Yi", db, context_info={}, light_mode=True)
    assert prof is not None
    assert calls == [], "config=False 时 None 跟随配置 → 跳过简介补全"
    db.close()


def test_skip_none_config_true_enriches(monkeypatch):
    """skip_llm_enrich=None + config actor_bio_inline_enabled=True → 同 skip=False（切回旧行为）。"""
    db = _seed_cache_hit(_mem_db())
    calls = []
    monkeypatch.setattr(aps, "load_config", lambda: _cfg_with_ai(inline_enabled=True))
    monkeypatch.setattr(aps, "_local_file_exists", lambda p: True)
    monkeypatch.setattr(aps, "_llm_enrich_existing", _boom_enrich_existing(calls))

    prof = aps.resolve_actor_profile("Zhang Yi", db, context_info={}, light_mode=True)
    assert prof is not None
    assert len(calls) == 1, "config=True 时 None 跟随配置 → 内联补简介（旧行为）"
    assert calls[0].get("skip_llm_enrich") is False, "跟随配置=True 应传 skip_llm_enrich=False 下去"
    db.close()


def test_skip_false_overrides_config(monkeypatch):
    """skip_llm_enrich=False（演员库路径）→ 即使 config=False 也始终补全。"""
    db = _seed_cache_hit(_mem_db())
    calls = []
    monkeypatch.setattr(aps, "load_config", lambda: _cfg_with_ai(inline_enabled=False))
    monkeypatch.setattr(aps, "_local_file_exists", lambda p: True)
    monkeypatch.setattr(aps, "_llm_enrich_existing", _boom_enrich_existing(calls))

    prof = aps.resolve_actor_profile(
        "Zhang Yi", db, context_info={}, light_mode=True, skip_llm_enrich=False,
    )
    assert prof is not None
    assert len(calls) == 1, "演员库路径显式 False → 即使 config=False 也始终补全"
    assert calls[0].get("skip_llm_enrich") is False
    db.close()


# ================================================================
# 配置默认值
# ================================================================

def test_config_default_inline_enabled_false():
    """新配置项 actor_bio_inline_enabled 默认 False（汉化/审计默认不内联补简介）。"""
    from config.settings import DEFAULT_CONFIG
    assert DEFAULT_CONFIG.get("actor_bio_inline_enabled") is False


# ================================================================
# Task 2: 透传 ensure_profiles_for_people / save_media_to_db
#   探针断言 skip_llm_enrich 入参 X 原样穿透，绝不丢失、绝不改写。
# ================================================================

_MISSING = "MISSING"


def _probe_resolve(captured):
    """ensure_profiles_for_people 的下游探针：捕获传给 resolve_actor_profile 的关键参数。"""
    def _fake_resolve(name, db, context_info=None, force_refresh=False,
                      light_mode=False, skip_llm_enrich=_MISSING):
        captured["light_mode"] = light_mode
        captured["skip_llm_enrich"] = skip_llm_enrich
        return {"name": name, "local_image_path": "", "image_url": "",
                "local_image_url": "", "source": "", "tmdb_id": "", "imdb_id": "",
                "douban_celebrity_id": "", "birth_date": "", "birth_place": "", "overview": ""}
    return _fake_resolve


def _probe_ensure(captured):
    """save_media_to_db 的下游探针：捕获传给 ensure_profiles_for_people 的关键参数。"""
    def _fake_ensure(db, people, light_mode=False, skip_llm_enrich=_MISSING):
        captured["light_mode"] = light_mode
        captured["skip_llm_enrich"] = skip_llm_enrich
    return _fake_ensure


def test_ensure_forwards_skip_true(monkeypatch):
    """ensure_profiles_for_people(skip_llm_enrich=True) → resolve_actor_profile 原样收到 True。"""
    db = _mem_db()()
    captured = {}
    monkeypatch.setattr(aps, "resolve_actor_profile", _probe_resolve(captured))

    aps.ensure_profiles_for_people(
        db, [{"Name": "A", "Type": "Actor"}], skip_llm_enrich=True,
    )
    assert captured["skip_llm_enrich"] is True, "显式 True 必须原样透传"
    db.close()


def test_ensure_forwards_skip_false(monkeypatch):
    """ensure_profiles_for_people(skip_llm_enrich=False) → resolve_actor_profile 原样收到 False。"""
    db = _mem_db()()
    captured = {}
    monkeypatch.setattr(aps, "resolve_actor_profile", _probe_resolve(captured))

    aps.ensure_profiles_for_people(
        db, [{"Name": "A", "Type": "Actor"}], skip_llm_enrich=False,
    )
    assert captured["skip_llm_enrich"] is False, "显式 False（演员库路径）必须原样透传"
    db.close()


def test_ensure_forwards_skip_none_default(monkeypatch):
    """ensure_profiles_for_people() 不传 → resolve_actor_profile 收到 None（跟随配置，非 MISSING）。"""
    db = _mem_db()()
    captured = {}
    monkeypatch.setattr(aps, "resolve_actor_profile", _probe_resolve(captured))

    aps.ensure_profiles_for_people(db, [{"Name": "A", "Type": "Actor"}])
    assert captured["skip_llm_enrich"] is None, "默认 None（跟随配置）必须原样透传"
    db.close()


def test_ensure_forwards_light_and_skip_combined(monkeypatch):
    """ensure_profiles_for_people(light_mode=True, skip_llm_enrich=True) → 两参数同时透传。"""
    db = _mem_db()()
    captured = {}
    monkeypatch.setattr(aps, "resolve_actor_profile", _probe_resolve(captured))

    aps.ensure_profiles_for_people(
        db, [{"Name": "A", "Type": "Actor"}], light_mode=True, skip_llm_enrich=True,
    )
    assert captured["light_mode"] is True, "light_mode 必须照常透传"
    assert captured["skip_llm_enrich"] is True, "skip_llm_enrich 必须与 light_mode 同时透传"
    db.close()


def test_save_forwards_skip_true(monkeypatch):
    """save_media_to_db(skip_llm_enrich=True) → ensure_profiles_for_people 原样收到 True。"""
    Session = _mem_db()
    db = Session()
    captured = {}
    monkeypatch.setattr(dbc, "ensure_profiles_for_people", _probe_ensure(captured))

    dbc.save_media_to_db(
        db,
        emby_item={"Id": "s1", "Name": "九门", "Type": "Series",
                   "People": [{"Name": "A", "Type": "Actor"}]},
        people=[{"Name": "A", "Type": "Actor"}],
        skip_llm_enrich=True,
    )
    assert captured["skip_llm_enrich"] is True, "显式 True 必须原样透传"
    db.close()


def test_save_forwards_skip_false(monkeypatch):
    """save_media_to_db(skip_llm_enrich=False) → ensure_profiles_for_people 原样收到 False。"""
    Session = _mem_db()
    db = Session()
    captured = {}
    monkeypatch.setattr(dbc, "ensure_profiles_for_people", _probe_ensure(captured))

    dbc.save_media_to_db(
        db,
        emby_item={"Id": "s2", "Name": "无", "Type": "Movie",
                   "People": [{"Name": "B", "Type": "Actor"}]},
        people=[{"Name": "B", "Type": "Actor"}],
        skip_llm_enrich=False,
    )
    assert captured["skip_llm_enrich"] is False, "显式 False 必须原样透传"
    db.close()


def test_save_forwards_skip_none_default(monkeypatch):
    """save_media_to_db() 不传 → ensure_profiles_for_people 收到 None（跟随配置，非 MISSING）。"""
    Session = _mem_db()
    db = Session()
    captured = {}
    monkeypatch.setattr(dbc, "ensure_profiles_for_people", _probe_ensure(captured))

    dbc.save_media_to_db(
        db,
        emby_item={"Id": "s3", "Name": "无", "Type": "Movie",
                   "People": [{"Name": "C", "Type": "Actor"}]},
        people=[{"Name": "C", "Type": "Actor"}],
    )
    assert captured["skip_llm_enrich"] is None, "默认 None（跟随配置）必须原样透传"
    db.close()


def test_save_forwards_light_and_skip_combined(monkeypatch):
    """save_media_to_db(light_profiles=True, skip_llm_enrich=True) → 两参数同时透传。"""
    Session = _mem_db()
    db = Session()
    captured = {}
    monkeypatch.setattr(dbc, "ensure_profiles_for_people", _probe_ensure(captured))

    dbc.save_media_to_db(
        db,
        emby_item={"Id": "s4", "Name": "九门", "Type": "Series",
                   "People": [{"Name": "A", "Type": "Actor"}]},
        people=[{"Name": "A", "Type": "Actor"}],
        light_profiles=True,
        skip_llm_enrich=True,
    )
    assert captured["light_mode"] is True, "light_profiles 必须照常透传"
    assert captured["skip_llm_enrich"] is True, "skip_llm_enrich 必须与 light_profiles 同时透传"
    db.close()
