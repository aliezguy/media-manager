"""Task 3 — sinicize 两调用点跟随配置（不显式传 skip_llm_enrich）。

对应 docs/superpowers/plans/2026-08-05-sinicize-actor-bio-optimization.md Task 3:
  sinicize 顶层 save_media_to_db(:393) 与分集前置批处理 ensure_profiles_for_people(:509)
  不显式传 skip_llm_enrich → 跟随配置 actor_bio_inline_enabled（默认 False = 跳过简介 LLM）。

验证维度：
  1. 调用点探针：两个调用点【均未显式传】skip_llm_enrich（kwarg 缺省），light_mode 照常透传。
  2. 端到端默认配置：汉化一个 Series 后，新增演员 ActorProfile 记录已建、overview 未 LLM 填充、
     enrich_actor_metadata 0 次调用（Boom 探针）。
  3. 角色名翻译仍触发：_infer_missing_roles_via_ai 调用计数 > 0 且结果回填 actor_records
     （Requirement 2 保留，独立于简介补全）。
  4. 配置翻转：actor_bio_inline_enabled=True 时端到端重新内联补简介（可切回旧行为）。

全部 Boom/探针断言，不触网、不真调 LLM。sinicize 内部多处 SessionLocal() 必须共享同一
数据库 → 用文件级 SQLite（tmp_path），而非 :memory:。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import ActorProfile, ActorRecord
import services.actor_profile_service as aps
import services.actor_profile_ai as apa
import services.douban_service as ds


# ================================================================
# 共享常量与 helpers
# ================================================================

_DS_CFG = {
    "emby_host": "http://emby.test", "emby_api_key": "k",
    "emby_user_id": "u", "max_actors_per_media": 50,
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

_EMBY_ITEM = {
    "Id": "s1", "Name": "九门", "Type": "Series",
    "People": [
        {"Name": "Sun Honglei", "Type": "Actor", "Role": "Li Yan"},
        {"Name": "Zhang Guoqiang", "Type": "Actor", "Role": "Gao Cheng"},
    ],
    "ProviderIds": {"Imdb": "tt0000001"}, "ProductionYear": "2020",
}

_DOUBAN_ACTORS = [
    {"name": "孙红雷", "role": "李岩", "avatar": "http://douban.test/a.jpg", "id": "c1"},
    {"name": "张国强", "role": "高城", "avatar": "http://douban.test/b.jpg", "id": "c2"},
]

_EPISODES = [
    {"Id": "e1", "Name": "第 1 集", "ParentIndexNumber": 1, "IndexNumber": 1,
     "People": [
         {"Name": "Sun Honglei", "Type": "Actor", "Role": "Li Yan"},
         {"Name": "Zhang Guoqiang", "Type": "Actor", "Role": "Gao Cheng"},
     ]},
]

_LOCALIZED_PEOPLE = [
    {"Name": "孙红雷", "Role": "李岩", "Type": "Actor"},
    {"Name": "张国强", "Role": "高城", "Type": "Actor"},
]

_ROLE_INFER_MAP = {"孙红雷": "李岩", "张国强": "高城"}


class _NoTranslator:
    def is_available(self):
        return False


def _make_db(tmp_path, fname="sinicize.db"):
    """文件级 SQLite：sinicize 内多处 SessionLocal() 必须共享同一 DB。"""
    engine = create_engine(f"sqlite:///{tmp_path}/{fname}")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def _make_sinizer(monkeypatch, Session):
    monkeypatch.setattr(ds, "SessionLocal", Session)
    monkeypatch.setattr(ds, "load_config", lambda: _DS_CFG)
    return ds.DoubanSinizer()


def _stub_aps_network(monkeypatch, tmp_path):
    """stub actor_profile_service 的网络/文件系统/LLM 可用性（resolve 用）。"""
    monkeypatch.setattr(aps, "_PEOPLE_DIR", str(tmp_path / "people"))
    monkeypatch.setattr(aps, "_local_sniff_cache", {})
    monkeypatch.setattr(aps, "_find_local_avatar", lambda name: None)
    monkeypatch.setattr(aps, "_download_image", lambda *a, **k: True)
    monkeypatch.setattr(aps, "_local_file_exists", lambda p: True)
    monkeypatch.setattr(aps, "fetch_tmdb_person_details", lambda *a, **k: None)
    # AI 声明为「可用」：若 skip 链路被破坏，enrich Boom 就会触发 → 回归能被发现
    monkeypatch.setattr(aps, "_ai_providers_available", lambda cfg: True)


def _fake_match(emby_actors, douban_actors, db=None, emby_item_id="", parent_id="",
                provider_tmdb_ids=None):
    """stub _match_and_update：官方中文名命中、Role 置空（触发缺失角色推理）。"""
    updated, details = [], []
    for a, da in zip(emby_actors, douban_actors):
        entry = dict(a)
        entry.update({
            "Name": da["name"],
            "Role": "",
            "DoubanAvatarUrl": da["avatar"],
            "DoubanCelebrityId": da["id"],
            "_cn_name_conf": 4, "_cn_name_src": "official",
            "_cn_role_conf": 4, "_cn_role_src": "official",
        })
        updated.append(entry)
        details.append({"actor": da["name"], "matched": True, "douban_name": da["name"]})
    return (updated, details, {}, {}, {}, {})


def _stub_sinicize_deps(s, monkeypatch, infer_calls=None):
    """stub sinicize 全链路下游（网络/匹配/写回），save/ensure 保持真实。

    infer_calls 若给出列表 → _infer_missing_roles_via_ai 变为 spy（记录入参并返回
    固定角色映射），用于断言「角色名翻译仍触发」。
    """
    monkeypatch.setattr(s, "_get_emby_item", lambda sid: _EMBY_ITEM)
    monkeypatch.setattr(s, "_find_douban_id", lambda *a, **k: "123")
    monkeypatch.setattr(s, "_fetch_douban_actors", lambda did: _DOUBAN_ACTORS)
    monkeypatch.setattr(s, "_match_and_update", _fake_match)
    monkeypatch.setattr(ds, "get_translator", lambda: _NoTranslator())
    monkeypatch.setattr(s, "_write_back_emby", lambda *a, **k: True)
    monkeypatch.setattr(s, "_fetch_episodes", lambda *a, **k: _EPISODES)
    monkeypatch.setattr(s, "_localize_episode_people", lambda *a, **k: _LOCALIZED_PEOPLE)
    monkeypatch.setattr(s, "_write_back_episode", lambda *a, **k: True)

    def fake_infer(title, year, actors_list):
        if infer_calls is not None:
            infer_calls.append(list(actors_list))
        return _ROLE_INFER_MAP
    monkeypatch.setattr(s, "_infer_missing_roles_via_ai", fake_infer)


# ================================================================
# 1. 调用点探针：两个调用点均不显式传 skip_llm_enrich
# ================================================================

def test_sinicize_call_sites_do_not_pass_skip_llm_enrich(monkeypatch, tmp_path):
    """sinicize 顶层 save_media_to_db 与分集前置 ensure_profiles_for_people
    均不显式传 skip_llm_enrich（kwarg 缺省 → 跟随配置）。"""
    Session = _make_db(tmp_path, "probe.db")
    s = _make_sinizer(monkeypatch, Session)
    captured = {"save": [], "ensure": []}

    def fake_save(db, **kw):
        captured["save"].append(kw)
    monkeypatch.setattr(ds, "save_media_to_db", fake_save)

    def fake_ensure(db, people, **kw):
        captured["ensure"].append(kw)
    monkeypatch.setattr(ds, "ensure_profiles_for_people", fake_ensure)

    _stub_sinicize_deps(s, monkeypatch)

    result = s.sinicize("s1")
    assert result["success"] is True

    # 顶层入库调用点 (:393)
    assert captured["save"], "sinicize 应调用 save_media_to_db"
    assert all("skip_llm_enrich" not in kw for kw in captured["save"]), (
        "sinicize 顶层入库不得显式传 skip_llm_enrich（应跟随配置）"
    )
    # 分集前置批处理调用点 (:509)
    assert captured["ensure"], "sinicize 应调用分集前置 ensure_profiles_for_people"
    assert all("skip_llm_enrich" not in kw for kw in captured["ensure"]), (
        "分集前置批处理不得显式传 skip_llm_enrich（应跟随配置）"
    )
    assert all(kw.get("light_mode") is True for kw in captured["ensure"]), (
        "分集前置批处理 light_mode=True 应照常透传"
    )


# ================================================================
# 2. 端到端：默认配置 → 跳过简介 LLM + 角色翻译仍触发
# ================================================================

def test_sinicize_default_config_skips_bio_llm(monkeypatch, tmp_path):
    """默认 config（无 actor_bio_inline_enabled 键 → False）：汉化 Series 后新增演员
    ActorProfile 已建、overview 未 LLM 填充、enrich_actor_metadata 0 次调用；
    角色名翻译仍触发（_infer_missing_roles_via_ai 被调用 + 结果回填 actor_records）。"""
    Session = _make_db(tmp_path)
    _stub_aps_network(monkeypatch, tmp_path)
    monkeypatch.setattr(aps, "load_config", lambda: _APS_CFG_DEFAULT)

    enrich_calls = []
    monkeypatch.setattr(
        apa, "enrich_actor_metadata",
        lambda _n, profile_data, _existing, _cfg: (
            enrich_calls.append(1) or (dict(profile_data), 1, datetime.now(), "", {})
        ),
    )

    s = _make_sinizer(monkeypatch, Session)
    infer_calls = []
    _stub_sinicize_deps(s, monkeypatch, infer_calls)

    result = s.sinicize("s1")
    assert result["success"] is True

    # --- 1) enrich_actor_metadata 0 次调用（Boom 探针）---
    assert enrich_calls == [], "默认 config=False 时 sinicize 全程不得触发 LLM 简介补全"

    # --- 2) 新演员记录已建，overview 未被 LLM 填充 ---
    db = Session()
    try:
        sun = db.query(ActorProfile).filter(ActorProfile.name == "孙红雷").first()
        zhang = db.query(ActorProfile).filter(ActorProfile.name == "张国强").first()
        assert sun is not None, "顶层 save_media_to_db 应新建 孙红雷 的 ActorProfile"
        assert zhang is not None, "顶层 save_media_to_db 应新建 张国强 的 ActorProfile"
        assert sun.overview in ("", None), (
            f"skip 简介后 overview 不应被 LLM 填充，实际={sun.overview!r}"
        )
        assert zhang.overview in ("", None)
        assert sun.local_image_path, "TMDB/豆瓣 免费头像仍应落盘（仅跳过 LLM，非跳过身份解析）"

        # --- 3) 角色名翻译仍触发：缺失角色推理被调用 + 结果回填 ---
        assert infer_calls, "_infer_missing_roles_via_ai 应被 sinicize 调用（角色翻译保留）"
        assert infer_calls[0] == ["孙红雷", "张国强"], (
            f"缺失角色推理应收到空角色演员列表，实际={infer_calls}"
        )
        recs = db.query(ActorRecord).filter(ActorRecord.emby_item_id == "s1").all()
    finally:
        db.close()
    roles = {r.name: r.role for r in recs}
    assert roles.get("孙红雷") == "李岩", f"角色推理结果应回填 actor_records，实际={roles}"
    assert roles.get("张国强") == "高城"


# ================================================================
# 3. 配置翻转：True → 切回旧行为（内联补简介）
# ================================================================

def test_sinicize_config_true_reenables_inline_bio(monkeypatch, tmp_path):
    """actor_bio_inline_enabled=True：sinicize 端到端重新触发 LLM 简介补全（旧行为可切回）。

    该测试同时反证调用点「跟随配置」：若调用点显式传 True/False 而不跟随配置，
    配置翻转就不会生效。
    """
    Session = _make_db(tmp_path, "inline.db")
    _stub_aps_network(monkeypatch, tmp_path)
    monkeypatch.setattr(aps, "load_config", lambda: _APS_CFG_INLINE_ON)

    enrich_calls = []
    monkeypatch.setattr(
        apa, "enrich_actor_metadata",
        lambda _n, profile_data, _existing, _cfg: (
            enrich_calls.append(1) or (dict(profile_data), 1, datetime.now(), "", {})
        ),
    )

    s = _make_sinizer(monkeypatch, Session)
    _stub_sinicize_deps(s, monkeypatch)

    result = s.sinicize("s1")
    assert result["success"] is True
    assert enrich_calls, "config=True 时 sinicize 应重新触发 LLM 简介补全（切回旧行为）"
