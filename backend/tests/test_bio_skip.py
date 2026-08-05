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
