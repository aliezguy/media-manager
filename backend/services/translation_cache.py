"""演员/角色译名缓存查表 + 置信度回写。

核心职责：
  1. lookup_actor_name  — 演员名【全局复用】：以 tmdb_id（降级 raw_name）
                          在演员主表 actor_profiles 查中文名，命中要求 confidence>=3。
  2. lookup_role_name   — 角色名【局部复用】：以 role + emby_item_id（分集追溯
                          parent_id）在关联表 actor_records 限定上下文查询。
  3. upsert_actor_translation — 将官方/AI 译名回写 actor_profiles 并记录置信度。

命中条件统一由 CONFIDENCE_REUSE_THRESHOLD 控制，低置信度记录绝不复用，
从而防止「伪中文（英文原名）」污染缓存后被当作有效译名。
"""
import logging
import re

from models import ActorProfile, ActorRecord
from services.translation_utils import (
    is_valid_chinese_translation,
    CONFIDENCE_REUSE_THRESHOLD,
)

logger = logging.getLogger("uvicorn")


def lookup_actor_name(db, tmdb_id: str, raw_name: str) -> dict | None:
    """演员名全局查表：优先 tmdb_id，查不到则降级 raw_name。

    Args:
        db:        SQLAlchemy Session
        tmdb_id:   TMDB 人物 ID（语言无关的稳定锚点，首选）
        raw_name:  Emby 原始演员名（中文名直查 / 无 ID 时降级）

    Returns:
        {"name", "confidence_level", "translation_source"}，未命中或
        confidence < CONFIDENCE_REUSE_THRESHOLD 时返回 None。
    """
    profile = None
    if tmdb_id:
        profile = db.query(ActorProfile).filter(
            ActorProfile.tmdb_id == str(tmdb_id).strip()
        ).first()
    if profile is None and raw_name:
        profile = db.query(ActorProfile).filter(
            ActorProfile.name == raw_name.strip()
        ).first()
    if profile and (profile.confidence_level or 0) >= CONFIDENCE_REUSE_THRESHOLD:
        logger.info("   💾 [Cache] 演员名缓存命中: %s (conf=%s)", profile.name, profile.confidence_level)
        return {
            "name": profile.name,
            "confidence_level": profile.confidence_level,
            "translation_source": profile.translation_source or "",
        }
    return None


def lookup_role_name(
    db,
    role: str,
    emby_item_id: str,
    parent_id: str | None = None,
    actor_name: str = "",
) -> dict | None:
    """角色名局部查表：role + emby_item_id（分集时向上追溯 parent_id）。

    在限定上下文（本媒体项，分集另含其 Series）内查询 actor_records，
    已入库且 confidence >= 阈值的中文角色名可直接复用。

    Args:
        db:           SQLAlchemy Session
        role:         待翻译的角色名（Emby 原始值）
        emby_item_id: 当前媒体项 ID
        parent_id:    分集时传入 Series ID，实现向上追溯
        actor_name:   可选，演员名用于增强匹配

    Returns:
        {"role", "confidence_level", "translation_source"}，未命中返回 None。
    """
    if not role:
        return None
    scope_ids = [emby_item_id]
    if parent_id:
        scope_ids.append(parent_id)
    rows = db.query(ActorRecord).filter(
        ActorRecord.emby_item_id.in_(scope_ids)
    ).all()

    def _hit(r) -> bool:
        return (r.confidence_level or 0) >= CONFIDENCE_REUSE_THRESHOLD

    # 策略 1: role 精确匹配（限定上下文内，同一角色值已入库）
    for r in rows:
        if r.role and r.role == role and _hit(r) and is_valid_chinese_translation(r.role):
            logger.info("   💾 [Cache] 角色名缓存命中(role): %s (conf=%s)", r.role, r.confidence_level)
            return {
                "role": r.role,
                "confidence_level": r.confidence_level,
                "translation_source": r.translation_source or "",
            }
    # 策略 2: 演员名归一化匹配，提取该演员已入库的中文角色
    if actor_name:
        key = _norm_key(actor_name)
        for r in rows:
            if r.name and _norm_key(r.name) == key and r.role and _hit(r) \
                    and is_valid_chinese_translation(r.role):
                logger.info("   💾 [Cache] 角色名缓存命中(actor): %s (conf=%s)", r.role, r.confidence_level)
                return {
                    "role": r.role,
                    "confidence_level": r.confidence_level,
                    "translation_source": r.translation_source or "",
                }
    return None


def upsert_actor_translation(
    db, chinese_name: str, tmdb_id: str, source: str, confidence: int,
) -> None:
    """将确认有效的译名回写 actor_profiles（演员主表）并记录来源与置信度。

    调用方必须已通过 is_valid_chinese_translation 校验。

    ★ 覆盖判定增强：只有 new_confidence > current_confidence 才允许覆盖
      名称/来源/置信度（例如官方中文 4 随时可覆盖纯 AI 直出 2，反向则不允许）。
      相等或更低置信度一律保留既有记录，避免低质量译名降级覆盖。

    Args:
        chinese_name: 经校验的中文名
        tmdb_id:      TMDB 人物 ID（用于定位既有行）
        source:       translation_utils.SOURCE_*
        confidence:   translation_utils.CONFIDENCE_*
    """
    if not chinese_name:
        return
    profile = None
    if tmdb_id:
        profile = db.query(ActorProfile).filter(
            ActorProfile.tmdb_id == str(tmdb_id).strip()
        ).first()
    if profile is None:
        profile = db.query(ActorProfile).filter(
            ActorProfile.name == chinese_name
        ).first()
    if profile is None:
        # 新建记录：直接写入
        profile = ActorProfile(
            name=chinese_name,
            confidence_level=confidence,
            translation_source=source,
        )
        db.add(profile)
    else:
        # ★ 已有记录：仅更高置信度才可覆盖
        current = profile.confidence_level or 0
        if confidence > current:
            profile.name = chinese_name
            profile.confidence_level = confidence
            profile.translation_source = source
    if tmdb_id:
        profile.tmdb_id = str(tmdb_id).strip()


def _norm_key(text: str) -> str:
    """归一化匹配 key：去空格、转小写、去点号。"""
    return re.sub(r"[^a-z0-9]", "", text.lower())
