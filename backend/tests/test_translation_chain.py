"""集成测试 — 完整链路：缓存查询 → 官方 API → 中文校验 → AI 兜底 → 最终入库。

覆盖《纯净缓存拦截》三大核心：
  1. 官方（豆瓣）返回有效中文 → 应用 + confidence=4/official，并回写 ActorProfile
  2. 官方返回伪中文（全英文） → 丢弃 + 标记 discarded_names（交 AI 兜底）
  3. 已入库缓存（confidence>=3）→ 二次处理直接复用，跳过官方/AI
  4. save_media_to_db 将角色置信度/来源持久化到 actor_records
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base
from models import ActorProfile, ActorRecord
from services.douban_service import DoubanSinizer
from services.db_crud import save_media_to_db
from services.translation_utils import (
    SOURCE_OFFICIAL, SOURCE_AI_FALLBACK, SOURCE_AI_DIRECT,
    CONFIDENCE_OFFICIAL, CONFIDENCE_AI_FALLBACK, CONFIDENCE_AI_DIRECT,
    is_valid_chinese_translation,
)

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
Session = sessionmaker(bind=engine)


def _fresh_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    return Session()


def _make_sinizer():
    """跳过 __init__（避免读配置/建 Session），仅测 _match_and_update。"""
    return object.__new__(DoubanSinizer)


def test_official_valid_translation_applied_and_confidence_tagged():
    db = _fresh_db()
    sinizer = _make_sinizer()
    # Emby 拼音名 "Sun Honglei" ↔ 豆瓣中文名"孙红雷" 拼音匹配成功
    emby_actors = [{"Name": "Sun Honglei", "Role": "Li Yan", "Type": "Actor"}]
    douban_actors = [{"name": "孙红雷", "role": "李岩", "id": "1001"}]

    updated, details, fallback_names, fallback_roles, direct_names, direct_roles = sinizer._match_and_update(
        emby_actors, douban_actors,
        db=db, emby_item_id="S1", parent_id="", provider_tmdb_ids={"sun honglei": "12345"},
    )
    assert updated[0]["Name"] == "孙红雷"
    assert updated[0]["_cn_name_conf"] == CONFIDENCE_OFFICIAL
    assert updated[0]["_cn_name_src"] == SOURCE_OFFICIAL
    assert updated[0]["Role"] == "李岩"
    assert updated[0]["_cn_role_conf"] == CONFIDENCE_OFFICIAL
    assert fallback_names == {} and fallback_roles == {}
    assert direct_names == {} and direct_roles == {}
    # 官方译名已回写 actor_profiles
    db.commit()
    prof = db.query(ActorProfile).filter(ActorProfile.tmdb_id == "12345").first()
    assert prof and prof.name == "孙红雷" and prof.confidence_level == CONFIDENCE_OFFICIAL
    db.close()


def test_official_pseudo_chinese_flagged_as_fallback():
    db = _fresh_db()
    sinizer = _make_sinizer()
    emby_actors = [{"Name": "Sun Hu", "Role": "Sun", "Type": "Actor"}]
    # 豆瓣返回全英文（伪中文）→ 必须被标记为 ai_fallback（官方查了但无中文）
    douban_actors = [{"name": "Sun Hu", "role": "Sun", "id": "2002"}]

    updated, details, fallback_names, fallback_roles, direct_names, direct_roles = sinizer._match_and_update(
        emby_actors, douban_actors,
        db=db, emby_item_id="S1", parent_id="",
    )
    # 伪中文不被应用：Name/Role 保留 Emby 原值
    assert updated[0]["Name"] == "Sun Hu"
    assert updated[0]["Role"] == "Sun"
    assert fallback_names.get("sun hu") == "Sun Hu"
    assert fallback_roles.get("sun hu") == "Sun"
    assert direct_names == {} and direct_roles == {}
    db.close()


def test_no_official_data_flagged_as_direct():
    db = _fresh_db()
    sinizer = _make_sinizer()
    emby_actors = [{"Name": "Some New Actor", "Role": "Some Role", "Type": "Actor"}]
    # 豆瓣完全没有这位演员 → 无官方数据 → 标记为 ai_direct（直接扔给 AI）
    douban_actors = []

    updated, details, fallback_names, fallback_roles, direct_names, direct_roles = sinizer._match_and_update(
        emby_actors, douban_actors,
        db=db, emby_item_id="S1", parent_id="",
    )
    assert fallback_names == {} and fallback_roles == {}
    assert direct_names.get("some new actor") == "Some New Actor"
    assert direct_roles.get("some new actor") == "Some Role"
    db.close()


def test_cached_name_reused_on_second_pass():
    db = _fresh_db()
    sinizer = _make_sinizer()
    # 首次：官方命中并回写 actor_profiles（confidence=4）
    emby_actors = [{"Name": "Sun Honglei", "Role": "Li Yan", "Type": "Actor"}]
    douban_actors = [{"name": "孙红雷", "role": "李岩", "id": "1001"}]
    sinizer._match_and_update(
        emby_actors, douban_actors,
        db=db, emby_item_id="S1", parent_id="",
        provider_tmdb_ids={"sun honglei": "12345"},
    )
    db.commit()

    # 二次：Emby 数据回到拼音名、且豆瓣不再提供匹配 → 缓存应直接复用
    emby2 = [{"Name": "Sun Honglei", "Role": "Li Yan", "Type": "Actor"}]
    updated2, _, _, _, _, _ = sinizer._match_and_update(
        emby2, [],
        db=db, emby_item_id="S2", parent_id="",
        provider_tmdb_ids={"sun honglei": "12345"},
    )
    assert updated2[0]["Name"] == "孙红雷"
    assert updated2[0]["_cn_name_conf"] == CONFIDENCE_OFFICIAL
    db.close()


def test_save_media_to_db_persists_role_confidence():
    db = _fresh_db()
    people = [{
        "Name": "布莱恩·克兰斯顿", "Role": "沃尔特·怀特", "Type": "Actor",
        "_cn_role_conf": CONFIDENCE_AI_FALLBACK, "_cn_role_src": SOURCE_AI_FALLBACK,
    }]
    save_media_to_db(
        db,
        emby_item={"Id": "E1", "Name": "S01E01", "Type": "Episode", "ProviderIds": {}},
        provider_ids={"tmdb_id": "", "imdb_id": "", "douban_id": ""},
        images={"poster_url": "", "backdrop_url": ""},
        people=people,
        parent_id="S1",
        skip_profiles=True,
    )
    db.commit()
    rec = db.query(ActorRecord).filter(ActorRecord.emby_item_id == "E1").first()
    assert rec and rec.role == "沃尔特·怀特"
    assert rec.confidence_level == CONFIDENCE_AI_FALLBACK
    assert rec.translation_source == SOURCE_AI_FALLBACK
    db.close()


def test_validation_gate_blocks_english():
    """防伪中文核心：英文原名绝不允许通过校验。"""
    assert is_valid_chinese_translation("Sun Hu") is False
    assert is_valid_chinese_translation("Walter White") is False
