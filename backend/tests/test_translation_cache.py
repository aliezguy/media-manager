"""translation_cache 测试 — 全局/局部查表 + 置信度回写。"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base
from models import ActorProfile, ActorRecord
from services.translation_cache import (
    lookup_actor_name, lookup_role_name, upsert_actor_translation,
)
from services.translation_utils import (
    SOURCE_OFFICIAL, SOURCE_AI_DIRECT, SOURCE_AI_FALLBACK,
    CONFIDENCE_OFFICIAL, CONFIDENCE_AI_DIRECT, CONFIDENCE_AI_FALLBACK, CONFIDENCE_NONE,
)

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
Session = sessionmaker(bind=engine)


def _fresh_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    return Session()


def test_lookup_actor_name_by_tmdb_id():
    db = _fresh_db()
    db.add(ActorProfile(name="张译", tmdb_id="12345",
                        confidence_level=CONFIDENCE_OFFICIAL, translation_source=SOURCE_OFFICIAL))
    db.commit()
    hit = lookup_actor_name(db, "12345", "Bryan")
    assert hit and hit["name"] == "张译" and hit["confidence_level"] == 4
    db.close()


def test_lookup_actor_name_below_threshold_ignored():
    db = _fresh_db()
    # conf=1（未翻译）< 复用门槛 2 → 不复用
    db.add(ActorProfile(name="Zhang Yi", tmdb_id="999", confidence_level=CONFIDENCE_NONE, translation_source=""))
    db.commit()
    assert lookup_actor_name(db, "999", "Zhang Yi") is None
    db.close()


def test_lookup_actor_name_ai_direct_reusable():
    db = _fresh_db()
    # conf=2（AI直出）>= 复用门槛 2 → 可复用
    db.add(ActorProfile(name="张译", tmdb_id="", confidence_level=CONFIDENCE_AI_DIRECT, translation_source=SOURCE_AI_DIRECT))
    db.commit()
    hit = lookup_actor_name(db, "", "张译")
    assert hit and hit["name"] == "张译"
    db.close()


def test_lookup_role_name_by_item():
    db = _fresh_db()
    db.add(ActorRecord(emby_item_id="E1", name="布莱恩", role="沃尔特·怀特",
                       confidence_level=CONFIDENCE_OFFICIAL, translation_source=SOURCE_OFFICIAL))
    db.commit()
    hit = lookup_role_name(db, "沃尔特·怀特", "E1", parent_id="S1", actor_name="布莱恩")
    assert hit and hit["role"] == "沃尔特·怀特"
    db.close()


def test_lookup_role_name_traces_up_to_parent():
    db = _fresh_db()
    db.add(ActorRecord(emby_item_id="S1", name="布莱恩", role="沃尔特·怀特",
                       confidence_level=CONFIDENCE_OFFICIAL, translation_source=SOURCE_OFFICIAL))
    db.commit()
    hit = lookup_role_name(db, "沃尔特·怀特", "E1", parent_id="S1", actor_name="布莱恩")
    assert hit and hit["role"] == "沃尔特·怀特"
    db.close()


def test_lookup_role_name_below_threshold_ignored():
    db = _fresh_db()
    db.add(ActorRecord(emby_item_id="S1", name="布莱恩", role="Walter White",
                       confidence_level=CONFIDENCE_NONE, translation_source=""))
    db.commit()
    assert lookup_role_name(db, "Walter White", "E1", parent_id="S1", actor_name="布莱恩") is None
    db.close()


def test_lookup_role_name_cross_actor_contamination_blocked():
    """★「相原龙」Bug 回归：不同演员查询同名角色，绝不能继承他人 conf=4 缓存。"""
    db = _fresh_db()
    # Actor A（仁科克基）已入库官方角色 相原龙 / conf=4
    db.add(ActorRecord(emby_item_id="S1", name="仁科克基", role="相原龙",
                       confidence_level=CONFIDENCE_OFFICIAL, translation_source=SOURCE_OFFICIAL))
    db.commit()
    # Actor B（斉川あい）查询同样的角色字符串 "相原龙" → 策略 1 必须未命中
    assert lookup_role_name(db, "相原龙", "E1", parent_id="S1", actor_name="斉川あい") is None
    db.close()


def test_lookup_role_name_same_actor_hit():
    """正控：同一演员查询自己的角色 → 命中并返回原置信度。"""
    db = _fresh_db()
    db.add(ActorRecord(emby_item_id="S1", name="仁科克基", role="相原龙",
                       confidence_level=CONFIDENCE_OFFICIAL, translation_source=SOURCE_OFFICIAL))
    db.commit()
    hit = lookup_role_name(db, "相原龙", "E1", parent_id="S1", actor_name="仁科克基")
    assert hit and hit["role"] == "相原龙" and hit["confidence_level"] == CONFIDENCE_OFFICIAL
    db.close()


def test_lookup_role_name_simplified_traditional_variant_reuse():
    """良性复用回归：简体已缓存 conf=4，繁体变体查询同角色 → 仍命中复用。"""
    db = _fresh_db()
    db.add(ActorRecord(emby_item_id="S1", name="五十岚隼士", role="日比野未来 / 坂宏人",
                       confidence_level=CONFIDENCE_OFFICIAL, translation_source=SOURCE_OFFICIAL))
    db.commit()
    hit = lookup_role_name(db, "日比野未来 / 坂宏人", "E1", parent_id="S1", actor_name="五十嵐隼士")
    assert hit and hit["role"] == "日比野未来 / 坂宏人" and hit["confidence_level"] == CONFIDENCE_OFFICIAL
    db.close()


def test_upsert_actor_translation_creates_and_strict_upgrades():
    db = _fresh_db()
    # 新建：直接写入 official/4
    upsert_actor_translation(db, "张译", "12345", SOURCE_OFFICIAL, CONFIDENCE_OFFICIAL)
    db.commit()
    row = db.query(ActorProfile).filter(ActorProfile.tmdb_id == "12345").first()
    assert row and row.name == "张译" and row.confidence_level == 4
    assert row.translation_source == SOURCE_OFFICIAL
    # 更低置信度（AI直出 2）尝试覆盖 → 严格升级判定：2 < 4 → 完全不覆盖（名称/来源/置信度均保留）
    upsert_actor_translation(db, "张译", "12345", SOURCE_AI_DIRECT, CONFIDENCE_AI_DIRECT)
    db.commit()
    row = db.query(ActorProfile).filter(ActorProfile.tmdb_id == "12345").first()
    assert row.confidence_level == 4
    assert row.translation_source == SOURCE_OFFICIAL
    assert row.name == "张译"
    db.close()


def test_upsert_actor_translation_higher_overwrites_lower():
    db = _fresh_db()
    # 先 AI直出 2，再官方 4 → 官方覆盖 AI
    upsert_actor_translation(db, "张译", "12345", SOURCE_AI_DIRECT, CONFIDENCE_AI_DIRECT)
    db.commit()
    upsert_actor_translation(db, "张译", "12345", SOURCE_OFFICIAL, CONFIDENCE_OFFICIAL)
    db.commit()
    row = db.query(ActorProfile).filter(ActorProfile.tmdb_id == "12345").first()
    assert row.confidence_level == 4
    assert row.translation_source == SOURCE_OFFICIAL
    db.close()


def test_upsert_actor_translation_equal_keeps_existing_source():
    db = _fresh_db()
    # conf=3 ai_fallback 已存在，再用 conf=3 覆盖 → 相等不覆盖来源
    upsert_actor_translation(db, "张译", "12345", SOURCE_AI_FALLBACK, CONFIDENCE_AI_FALLBACK)
    db.commit()
    upsert_actor_translation(db, "张译", "12345", SOURCE_AI_DIRECT, CONFIDENCE_AI_DIRECT)
    db.commit()
    row = db.query(ActorProfile).filter(ActorProfile.tmdb_id == "12345").first()
    assert row.confidence_level == 3
    assert row.translation_source == SOURCE_AI_FALLBACK
    db.close()
