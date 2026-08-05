"""汉化流程集成分集简介翻译 — sinicize Series 时顺带 LLM 翻译非中文分集简介。

背景（根因）:
  强制汉化（force_translate_batch → sinicize）原本只汉化演员/角色，从不处理分集简介。
  分集简介翻译是全库 overview 汉化（overview_translator）的独立功能，且从未成功落地
  （全库 0 行 overview_source=local_llm/cloud_llm）。本次修复把分集简介翻译接进汉化流程。

对应实现:
  - sinicize 分集循环新增 _translate_episode_overview: 非中文简介 → translate_overview
    （本地 qwen→云端兜底 + 中文有效性验收）→ 就地写入 ep['Overview']，随 Emby 写回 + 落库共用
  - 落库标记 overview_source = local_llm / cloud_llm（防覆盖守卫据此保护 AI 中文）
  - 配置开关 sinicize_translate_episode_overviews（默认 True；False 时保持旧行为）

验证维度:
  1. 默认配置: 分集简介为日文 → 翻译被调用 → DB overview 汉化 + overview_source=local_llm
     → Emby 写回 update_data 携带中文 Overview。
  2. 开关关闭: sinicize_translate_episode_overviews=False → 不调翻译 → 简介保持原文。
  3. 已中文简介: 跳过翻译（needs_overview_translation=False），不调 LLM。
  4. 翻译失败: translate_overview 返回 None → 简介保持原文，演员汉化不受影响。

全部 Boom/探针断言，不触网、不真调 LLM。sinicize 内部多处 SessionLocal() 必须共享同一
数据库 → 用文件级 SQLite（tmp_path），而非 :memory:。
"""
import sys
import os
import copy
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import ActorProfile, MediaMetadata
import services.actor_profile_service as aps
import services.douban_service as ds


# ================================================================
# 共享常量与 helpers
# ================================================================

_DS_CFG = {
    "emby_host": "http://emby.test", "emby_api_key": "k",
    "emby_user_id": "u", "max_actors_per_media": 50,
}

# 开关关闭：sinicize_translate_episode_overviews=False → 保持旧行为
_DS_CFG_OFF = dict(_DS_CFG, sinicize_translate_episode_overviews=False)

# 全库 overview 总开关关闭 → 一并禁用分集简介翻译
_DS_CFG_MASTER_OFF = dict(_DS_CFG, overview_translation_enabled=False)

# 演员简介配置：与 sinicize 顶层 save_media_to_db 的 profile 解析兼容
_APS_CFG = {
    "douban_enabled": True, "enable_emby_avatar_first": False,
    "douban_cookie": "", "tmdb_api_key": "",
    "actor_ai_enabled": True, "actor_ai_local_first": True,
    "llm_cooldown_days": 7,
}

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

_JAPANESE_OVERVIEW = "今日は日本語のあらすじ。敵が現れる。"

_EPISODES = [
    {"Id": "e1", "Name": "第 1 集", "ParentIndexNumber": 1, "IndexNumber": 1,
     "Overview": _JAPANESE_OVERVIEW,
     "People": [
         {"Name": "Sun Honglei", "Type": "Actor", "Role": "Li Yan"},
     ]},
]

_EPISODES_ZH = [
    {"Id": "e1", "Name": "第 1 集", "ParentIndexNumber": 1, "IndexNumber": 1,
     "Overview": "这是已汉化的中文分集简介。",
     "People": [
         {"Name": "Sun Honglei", "Type": "Actor", "Role": "Li Yan"},
     ]},
]

_LOCALIZED_PEOPLE = [
    {"Name": "孙红雷", "Role": "李岩", "Type": "Actor"},
]

_ZH_OVERVIEW = "这是一个翻译后的中文分集简介。"


class _NoTranslator:
    def is_available(self):
        return False


def _make_db(tmp_path, fname="sinicize.db"):
    """文件级 SQLite：sinicize 内多处 SessionLocal() 必须共享同一 DB。"""
    engine = create_engine(f"sqlite:///{tmp_path}/{fname}")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def _make_sinizer(monkeypatch, Session, cfg=None):
    monkeypatch.setattr(ds, "SessionLocal", Session)
    monkeypatch.setattr(ds, "load_config", lambda: (cfg or _DS_CFG))
    return ds.DoubanSinizer()


def _stub_aps_network(monkeypatch, tmp_path):
    """stub actor_profile_service 的网络/文件系统/LLM 可用性（resolve 用）。"""
    monkeypatch.setattr(aps, "_PEOPLE_DIR", str(tmp_path / "people"))
    monkeypatch.setattr(aps, "_local_sniff_cache", {})
    monkeypatch.setattr(aps, "_find_local_avatar", lambda name: None)
    monkeypatch.setattr(aps, "_download_image", lambda *a, **k: True)
    monkeypatch.setattr(aps, "_local_file_exists", lambda p: True)
    monkeypatch.setattr(aps, "fetch_tmdb_person_details", lambda *a, **k: None)
    monkeypatch.setattr(aps, "_ai_providers_available", lambda cfg: True)


def _fake_match(emby_actors, douban_actors, db=None, emby_item_id="", parent_id="",
                provider_tmdb_ids=None):
    """stub _match_and_update：官方中文名命中。"""
    updated, details = [], []
    for a, da in zip(emby_actors, douban_actors):
        entry = dict(a)
        entry.update({
            "Name": da["name"], "Role": da["role"],
            "DoubanAvatarUrl": da["avatar"], "DoubanCelebrityId": da["id"],
            "_cn_name_conf": 4, "_cn_name_src": "official",
            "_cn_role_conf": 4, "_cn_role_src": "official",
        })
        updated.append(entry)
        details.append({"actor": da["name"], "matched": True, "douban_name": da["name"]})
    return (updated, details, {}, {}, {}, {})


def _stub_sinicize_deps(s, monkeypatch, episodes=None, write_ep=None):
    """stub sinicize 全链路下游（网络/匹配/写回），save/ensure 保持真实。"""
    monkeypatch.setattr(s, "_get_emby_item", lambda sid: _EMBY_ITEM)
    monkeypatch.setattr(s, "_find_douban_id", lambda *a, **k: "123")
    monkeypatch.setattr(s, "_fetch_douban_actors", lambda did: _DOUBAN_ACTORS)
    monkeypatch.setattr(s, "_match_and_update", _fake_match)
    monkeypatch.setattr(ds, "get_translator", lambda: _NoTranslator())
    monkeypatch.setattr(s, "_write_back_emby", lambda *a, **k: True)
    # ★ deepcopy：_translate_episode_overview 会就地改 ep['Overview']，避免污染共享常量
    monkeypatch.setattr(
        s, "_fetch_episodes",
        lambda *a, **k: copy.deepcopy(episodes if episodes is not None else _EPISODES),
    )
    monkeypatch.setattr(s, "_localize_episode_people", lambda *a, **k: _LOCALIZED_PEOPLE)
    if write_ep is not None:
        monkeypatch.setattr(s, "_write_back_episode", write_ep)
    else:
        monkeypatch.setattr(s, "_write_back_episode", lambda *a, **k: True)


def _make_translate_stub(monkeypatch, result):
    """stub ds.translate_overview：记录入参并返回固定 (translated, source, nulls)。"""
    calls = []
    def fake_translate(text):
        calls.append(text)
        return result
    monkeypatch.setattr(ds, "translate_overview", fake_translate)
    return calls


def _make_write_ep_capture():
    """捕获 _write_back_episode 的 ep_data（验证 Overview 是否随写回）。"""
    captured = {}
    def fake_write_ep(episode_id, ep_data, people):
        captured["ep_id"] = episode_id
        captured["overview"] = ep_data.get("Overview", "")
        return True
    return fake_write_ep, captured


# ================================================================
# 1. 默认配置：日文分集简介 → 翻译 → 写回 Emby + 落库
# ================================================================

def test_sinicize_translates_episode_overview(monkeypatch, tmp_path):
    """默认配置汉化 Series：分集简介为日文 → translate_overview 被调用 →
    Emby 写回携带中文 Overview；DB 落库 overview 汉化 + overview_source=local_llm。"""
    Session = _make_db(tmp_path)
    _stub_aps_network(monkeypatch, tmp_path)
    monkeypatch.setattr(aps, "load_config", lambda: _APS_CFG)

    s = _make_sinizer(monkeypatch, Session)
    translate_calls = _make_translate_stub(monkeypatch, (_ZH_OVERVIEW, "local_llm", set()))
    fake_write_ep, captured = _make_write_ep_capture()
    _stub_sinicize_deps(s, monkeypatch, write_ep=fake_write_ep)

    result = s.sinicize("s1")
    assert result["success"] is True

    # --- 翻译被调用，入参即日文原文 ---
    assert translate_calls == [_JAPANESE_OVERVIEW], (
        f"translate_overview 应收到日文简介，实际={translate_calls}"
    )
    # --- Emby 写回 update_data 携带中文 Overview ---
    assert captured.get("overview") == _ZH_OVERVIEW, (
        f"Emby 写回应携带中文 Overview，实际={captured.get('overview')!r}"
    )
    # --- DB 落库：overview 汉化 + 来源标记 local_llm + 更新时间已打 ---
    db = Session()
    try:
        ep = db.query(MediaMetadata).filter(MediaMetadata.emby_item_id == "e1").first()
        assert ep is not None, "分集应入库 media_metadata"
        assert ep.overview == _ZH_OVERVIEW, (
            f"DB 分集简介应被汉化，实际={ep.overview!r}"
        )
        assert ep.overview_source == "local_llm", (
            f"LLM 翻译产物应标记 local_llm，实际={ep.overview_source!r}"
        )
        assert ep.overview_updated_at is not None, "overview_updated_at 应被打点"
    finally:
        db.close()


# ================================================================
# 2. 开关关闭：保持旧行为，不调翻译
# ================================================================

def test_sinicize_config_off_skips_episode_overview(monkeypatch, tmp_path):
    """sinicize_translate_episode_overviews=False → 不调 translate_overview，
    DB 简介保持原文（日本），Emby 写回也不携带中文。"""
    Session = _make_db(tmp_path, "off.db")
    _stub_aps_network(monkeypatch, tmp_path)
    monkeypatch.setattr(aps, "load_config", lambda: _APS_CFG)

    s = _make_sinizer(monkeypatch, Session, cfg=_DS_CFG_OFF)
    translate_calls = _make_translate_stub(monkeypatch, (_ZH_OVERVIEW, "local_llm", set()))
    fake_write_ep, captured = _make_write_ep_capture()
    _stub_sinicize_deps(s, monkeypatch, write_ep=fake_write_ep)

    result = s.sinicize("s1")
    assert result["success"] is True

    assert translate_calls == [], "开关关闭时不得调用 translate_overview"
    assert captured.get("overview") == _JAPANESE_OVERVIEW, (
        f"开关关闭时 Emby 写回应保持原简介，实际={captured.get('overview')!r}"
    )
    db = Session()
    try:
        ep = db.query(MediaMetadata).filter(MediaMetadata.emby_item_id == "e1").first()
        assert ep is not None
        assert ep.overview == _JAPANESE_OVERVIEW, (
            f"开关关闭时 DB 简介应保持原文，实际={ep.overview!r}"
        )
        assert ep.overview_source != "local_llm"
    finally:
        db.close()


# ================================================================
# 3. overview_translation_enabled=False（总开关）→ 一并禁用
# ================================================================

def test_sinicize_master_overview_switch_off(monkeypatch, tmp_path):
    """overview_translation_enabled=False 时，即使分集简介开关默认 True，也不翻译。"""
    Session = _make_db(tmp_path, "master_off.db")
    _stub_aps_network(monkeypatch, tmp_path)
    monkeypatch.setattr(aps, "load_config", lambda: _APS_CFG)

    s = _make_sinizer(monkeypatch, Session, cfg=_DS_CFG_MASTER_OFF)
    translate_calls = _make_translate_stub(monkeypatch, (_ZH_OVERVIEW, "local_llm", set()))
    _stub_sinicize_deps(s, monkeypatch)

    result = s.sinicize("s1")
    assert result["success"] is True
    assert translate_calls == [], "overview_translation_enabled=False 时不得翻译分集简介"


# ================================================================
# 4. 分集简介已中文 → 跳过翻译
# ================================================================

def test_sinicize_skips_already_chinese_overview(monkeypatch, tmp_path):
    """分集简介已含足够中文 → needs_overview_translation=False → 不调 LLM。"""
    Session = _make_db(tmp_path, "zh.db")
    _stub_aps_network(monkeypatch, tmp_path)
    monkeypatch.setattr(aps, "load_config", lambda: _APS_CFG)

    s = _make_sinizer(monkeypatch, Session)
    translate_calls = _make_translate_stub(monkeypatch, (_ZH_OVERVIEW, "local_llm", set()))
    fake_write_ep, captured = _make_write_ep_capture()
    _stub_sinicize_deps(s, monkeypatch, episodes=_EPISODES_ZH, write_ep=fake_write_ep)

    result = s.sinicize("s1")
    assert result["success"] is True

    assert translate_calls == [], "已中文简介不得触发翻译"
    assert captured.get("overview") == "这是已汉化的中文分集简介。", (
        f"Emby 写回应保持原中文简介，实际={captured.get('overview')!r}"
    )


# ================================================================
# 5. 翻译失败 → 简介保持原文，演员汉化不受影响
# ================================================================

def test_sinicize_overview_translate_failure_keeps_original(monkeypatch, tmp_path):
    """translate_overview 返回 None（全引擎失败/未过中文校验）→ 简介保持原文，
    演员汉化主流程照常成功。"""
    Session = _make_db(tmp_path, "fail.db")
    _stub_aps_network(monkeypatch, tmp_path)
    monkeypatch.setattr(aps, "load_config", lambda: _APS_CFG)

    s = _make_sinizer(monkeypatch, Session)
    translate_calls = _make_translate_stub(monkeypatch, (None, "failed", set()))
    fake_write_ep, captured = _make_write_ep_capture()
    _stub_sinicize_deps(s, monkeypatch, write_ep=fake_write_ep)

    result = s.sinicize("s1")
    assert result["success"] is True, "翻译失败不得拖垮演员汉化"

    assert translate_calls == [_JAPANESE_OVERVIEW]
    assert captured.get("overview") == _JAPANESE_OVERVIEW, (
        f"翻译失败时写回应保持原简介，实际={captured.get('overview')!r}"
    )
    db = Session()
    try:
        ep = db.query(MediaMetadata).filter(MediaMetadata.emby_item_id == "e1").first()
        assert ep is not None
        assert ep.overview == _JAPANESE_OVERVIEW, (
            f"翻译失败时 DB 简介应保持原文，实际={ep.overview!r}"
        )
        # 演员汉化仍正常：顶层孙红雷的 ActorProfile 已建
        assert db.query(ActorProfile).filter(ActorProfile.name == "孙红雷").first() is not None
    finally:
        db.close()
