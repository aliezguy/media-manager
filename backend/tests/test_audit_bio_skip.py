"""Task 4 — 审计/审批流程调用点跟随配置（不显式传 skip_llm_enrich）。

对应 docs/superpowers/plans/2026-08-05-sinicize-actor-bio-optimization.md Task 4:
  sync_actions.py 四处调用点不显式传 skip_llm_enrich → 跟随配置
  actor_bio_inline_enabled（默认 False = 跳过简介 LLM）：
    :512  _audit_and_save_single_item 已汉化分支 → save_media_to_db
    :559  _audit_and_save_single_item 未汉化分支 → save_media_to_db
    :1318 _batch_enrich_episodes_task  guest_stars → ensure_profiles_for_people
    :2019 _batch_audit_task            guest_stars → ensure_profiles_for_people

验证维度：
  1. 调用点探针：四处调用点【均未显式传】skip_llm_enrich（kwarg 缺省，连 None 都不传）。
  2. 端到端默认配置：审计已汉化分支真实 save/ensure/resolve 链 → 新演员 ActorProfile
     已建、overview 未 LLM 填充、enrich_actor_metadata 0 次调用（Boom 探针）。
  3. 配置翻转：actor_bio_inline_enabled=True 时审计路径重新内联补简介（可切回旧行为）。

全部 Boom/探针断言，不触网、不真调 LLM。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import ActorProfile
from routers import sync_actions as sa
import services.actor_profile_service as aps
import services.actor_profile_ai as apa


# ================================================================
# 共享常量与 helpers
# ================================================================

# sync_actions 侧配置：审计入口只读 max_actors_per_media / tmdb_api_key / tmdb_base_url
_SA_CFG = {
    "tmdb_api_key": "k", "tmdb_base_url": "http://tmdb.test",
    "max_actors_per_media": 50,
}

# 默认配置：不带 actor_bio_inline_enabled 键 → resolve 走缺省值 False（跳过简介）
_APS_CFG_DEFAULT = {
    "douban_enabled": True, "enable_emby_avatar_first": False,
    "douban_cookie": "", "tmdb_api_key": "",
    "actor_ai_enabled": True, "actor_ai_local_first": True,
    "llm_cooldown_days": 7,
}

# 配置翻转：显式 True → 切回旧行为（内联补简介）
_APS_CFG_INLINE_ON = dict(_APS_CFG_DEFAULT, actor_bio_inline_enabled=True)

# 已汉化 item：≥90% 中文角色名 → is_synced=True → 走 :512 save_media_to_db
_SYNCED_ITEM = {
    "Id": "s1", "Name": "九门", "Type": "Series",
    "People": [
        {"Name": "孙红雷", "Type": "Actor", "Role": "李岩",
         "DoubanAvatarUrl": "http://douban.test/a.jpg", "DoubanCelebrityId": "c1"},
    ],
    "ProviderIds": {}, "ProductionYear": "2020",
}

# 未汉化 item：0% 中文角色名 → is_synced=False → 走 :559 save_media_to_db
_PENDING_ITEM = {
    "Id": "s2", "Name": "Test", "Type": "Series",
    "People": [{"Name": "Sun Honglei", "Type": "Actor", "Role": "Li Yan"}],
    "ProviderIds": {},
}

# TMDB 整季接口返回：单季单集 + 一位客串演员（触发 ensure_profiles_for_people 漏斗）
_TMDB_PAYLOAD = {
    "episodes": [{
        "episode_number": 1, "overview": "overview",
        "guest_stars": [{"name": "Guest Actor", "id": "12345", "character": "X"}],
    }],
}


def _make_db(fname="audit.db"):
    """文件级 SQLite（端到端共享同一 DB 用）。"""
    import tempfile
    tmpdir = tempfile.mkdtemp(prefix="audit_bio_")
    engine = create_engine(f"sqlite:///{tmpdir}/{fname}")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine), tmpdir


def _make_mem_session(monkeypatch):
    """内存 SQLite + 把 sa.SessionLocal 指向它（后台任务内部开会话用）。"""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(sa, "SessionLocal", Session)
    return Session


def _stub_aps_network(monkeypatch, tmp_path):
    """stub actor_profile_service 的网络/文件系统/LLM 可用性（resolve 用）。"""
    monkeypatch.setattr(aps, "_PEOPLE_DIR", str(tmp_path / "people"))
    monkeypatch.setattr(aps, "_local_sniff_cache", {})
    monkeypatch.setattr(aps, "_find_local_avatar", lambda name: None)
    monkeypatch.setattr(aps, "_download_image", lambda *a, **k: True)
    monkeypatch.setattr(aps, "_local_file_exists", lambda p: True)
    monkeypatch.setattr(aps, "fetch_tmdb_person_details", lambda *a, **k: None)
    # 豆瓣 Frodo API 详情：即便走到该分支也不触网（仅防御性 stub）
    monkeypatch.setattr(aps, "_douban_celebrity_details_with_retry", lambda *a, **k: None)
    # AI 声明为「可用」：若 skip 链路被破坏，enrich Boom 就会触发 → 回归能被发现
    monkeypatch.setattr(aps, "_ai_providers_available", lambda cfg: True)


def _fake_get(payload):
    def fake_get(url, **kwargs):
        resp = SimpleNamespace(status_code=200)
        resp.json = lambda: payload
        return resp
    return fake_get


def _patch_task_common(monkeypatch, Session, payload, capture):
    """给两个后台任务打上公共 mock：网络 / ensure spy / SessionLocal / load_config。"""
    monkeypatch.setattr(sa, "_requests", SimpleNamespace(get=_fake_get(payload)))
    monkeypatch.setattr(sa, "load_config", lambda: _SA_CFG)

    def fake_ensure(db, people, **kw):
        capture["ensure"].append(kw)
    monkeypatch.setattr(sa, "ensure_profiles_for_people", fake_ensure)

    monkeypatch.setattr(sa, "SessionLocal", Session)
    return sa


# ================================================================
# 1. 调用点探针：_audit_and_save_single_item 两个 save 分支均不传 skip
# ================================================================

def test_audit_and_save_call_sites_do_not_pass_skip_llm_enrich(monkeypatch):
    """审计入口已汉化(:512)/未汉化(:559) 两个 save_media_to_db 调用点
    均不显式传 skip_llm_enrich（kwarg 缺省 → 跟随配置）。"""
    Session = _make_mem_session(monkeypatch)
    monkeypatch.setattr(sa, "load_config", lambda: _SA_CFG)
    # 隔离调用点：分集拉取返回空，确保只捕获 Series 层那一次 save
    monkeypatch.setattr(sa, "_fetch_episodes", lambda *a, **k: [])

    captured = {"save": []}

    def fake_save(db, **kw):
        captured["save"].append(kw)
    monkeypatch.setattr(sa, "save_media_to_db", fake_save)

    for item in (_SYNCED_ITEM, _PENDING_ITEM):
        db = Session()
        try:
            sa._audit_and_save_single_item(
                db, item, "http://emby.test", "k", "u")
        finally:
            db.close()

    assert len(captured["save"]) == 2, (
        f"已汉化+未汉化两分支各应触发一次 save_media_to_db，实际={captured['save']}"
    )
    assert all("skip_llm_enrich" not in kw for kw in captured["save"]), (
        "审计入口 save_media_to_db 不得显式传 skip_llm_enrich（应跟随配置）"
    )


# ================================================================
# 2. 端到端默认配置：审计已汉化分支跳过简介 LLM + 身份解析保留
# ================================================================

def test_audit_default_config_skips_bio_llm(monkeypatch, tmp_path):
    """默认 config（无 actor_bio_inline_enabled 键 → False）：审计已汉化分支
    真实 save/ensure/resolve 链 → 新演员 ActorProfile 已建、overview 未 LLM 填充、
    enrich_actor_metadata 0 次调用（Boom 探针）。"""
    Session, _ = _make_db()
    _stub_aps_network(monkeypatch, tmp_path)
    monkeypatch.setattr(aps, "load_config", lambda: _APS_CFG_DEFAULT)
    monkeypatch.setattr(sa, "load_config", lambda: _SA_CFG)
    monkeypatch.setattr(sa, "_fetch_episodes", lambda *a, **k: [])

    enrich_calls = []
    monkeypatch.setattr(
        apa, "enrich_actor_metadata",
        lambda _n, profile_data, _existing, _cfg: (
            enrich_calls.append(1) or (dict(profile_data), 1, datetime.now(), "", {})
        ),
    )

    db = Session()
    try:
        sa._audit_and_save_single_item(
            db, _SYNCED_ITEM, "http://emby.test", "k", "u")
        db.commit()
    finally:
        db.close()

    assert enrich_calls == [], "默认 config=False 时审计路径全程不得触发 LLM 简介补全"

    db = Session()
    try:
        prof = db.query(ActorProfile).filter(
            ActorProfile.name == "孙红雷").first()
    finally:
        db.close()
    assert prof is not None, "审计已汉化分支应新建 孙红雷 的 ActorProfile（身份解析保留）"
    assert prof.overview in ("", None), (
        f"skip 简介后 overview 不应被 LLM 填充，实际={prof.overview!r}"
    )
    assert prof.local_image_path, "豆瓣直链头像仍应落盘（仅跳过 LLM，非跳过身份解析）"


# ================================================================
# 3. 配置翻转：True → 审计路径切回旧行为（内联补简介）
# ================================================================

def test_audit_config_true_reenables_inline_bio(monkeypatch, tmp_path):
    """actor_bio_inline_enabled=True：审计已汉化分支端到端重新触发 LLM 简介补全。

    该测试同时反证调用点「跟随配置」：若调用点显式传 True/False 而不跟随配置，
    配置翻转就不会生效。
    """
    Session, _ = _make_db()
    _stub_aps_network(monkeypatch, tmp_path)
    monkeypatch.setattr(aps, "load_config", lambda: _APS_CFG_INLINE_ON)
    monkeypatch.setattr(sa, "load_config", lambda: _SA_CFG)
    monkeypatch.setattr(sa, "_fetch_episodes", lambda *a, **k: [])

    enrich_calls = []
    monkeypatch.setattr(
        apa, "enrich_actor_metadata",
        lambda _n, profile_data, _existing, _cfg: (
            enrich_calls.append(1) or (dict(profile_data), 1, datetime.now(), "", {})
        ),
    )

    db = Session()
    try:
        sa._audit_and_save_single_item(
            db, _SYNCED_ITEM, "http://emby.test", "k", "u")
        db.commit()
    finally:
        db.close()

    assert enrich_calls, "config=True 时审计路径应重新触发 LLM 简介补全（切回旧行为）"


# ================================================================
# 4. 调用点探针：batch-enrich / batch_audit 的 guest_stars 漏斗均不传 skip
# ================================================================

def test_batch_enrich_guest_stars_do_not_pass_skip_llm_enrich(monkeypatch):
    """:1318 _batch_enrich_episodes_task guest_stars → ensure_profiles_for_people
    不显式传 skip_llm_enrich（kwarg 缺省 → 跟随配置）。"""
    capture = {"ensure": []}
    Session = _make_mem_session(monkeypatch)
    _patch_task_common(monkeypatch, Session, _TMDB_PAYLOAD, capture)

    sa._batch_enrich_episodes_task("t1", "series1", "123", [1], "Test Series")

    assert capture["ensure"], "batch-enrich 应调用 ensure_profiles_for_people 处理 guest_stars"
    assert all("skip_llm_enrich" not in kw for kw in capture["ensure"]), (
        "batch-enrich guest_stars 漏斗不得显式传 skip_llm_enrich（应跟随配置）"
    )


def test_batch_audit_guest_stars_do_not_pass_skip_llm_enrich(monkeypatch):
    """:2019 _batch_audit_task Phase 2 guest_stars → ensure_profiles_for_people
    不显式传 skip_llm_enrich（kwarg 缺省 → 跟随配置）。"""
    capture = {"ensure": []}
    Session = _make_mem_session(monkeypatch)
    _patch_task_common(monkeypatch, Session, _TMDB_PAYLOAD, capture)

    # Phase 1 汇聚：已汉化 Series + 有效 tmdb_id → 进入 series_queue
    monkeypatch.setattr(
        sa, "_sync_and_audit_single_item",
        lambda item_id, library_id="": {
            "success": True, "synced": True, "item_type": "Series",
            "item_name": "Test Series", "episodes_processed": 1, "tmdb_id": "123",
        },
    )
    monkeypatch.setattr(sa, "_fetch_tmdb_seasons", lambda base, key, tid: [1])

    sa._batch_audit_task("t2", ["series1"], "", "host", "key", "user")

    assert capture["ensure"], "batch_audit 应调用 ensure_profiles_for_people 处理 guest_stars"
    assert all("skip_llm_enrich" not in kw for kw in capture["ensure"]), (
        "batch_audit guest_stars 漏斗不得显式传 skip_llm_enrich（应跟随配置）"
    )
