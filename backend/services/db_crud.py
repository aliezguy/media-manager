"""
演职员中文化 — 共享数据库 CRUD 操作。

本模块提供:
1. extract_provider_ids()  — 从 Emby Item 中提取 TMDB/IMDB/Douban ID（兼容大小写）
2. extract_external_images() — 从 Emby Item 中提取外部图片链接（优先 TMDB）
3. save_media_to_db()        — 通用入库函数，UPSERT media_sync_status + media_metadata + actor_records

所有函数由 audit_local 和后台汉化 Worker 共同复用，保证写入逻辑唯一。
"""

import logging
import traceback
from datetime import datetime

from database import SessionLocal
from models import MediaSyncStatus, MediaMetadata, ActorRecord
from services.tmdb_service import get_tmdb_image_urls
from services.actor_profile_service import ensure_profiles_for_people

logger = logging.getLogger("uvicorn")


# ==========================================
# 辅助函数（与 sync_actions 共用）
# ==========================================

def _safe_get(d: dict, key: str, default=""):
    """从字典安全取值，兼容 None 和空字符串。"""
    val = d.get(key)
    return val if val else default


def extract_provider_ids(item: dict) -> dict:
    """从 Emby Item 中安全提取 ProviderIds（兼容大小写）。

    Emby 不同插件/版本返回的 key 大小写不一致：
    原生 TMDB 插件 → Tmdb / Imdb
    部分第三方刮削器 → tmdb / imdb
    豆瓣插件        → Douban / douban
    """
    pids = item.get("ProviderIds", {}) or {}
    return {
        "tmdb_id": _safe_get(pids, "Tmdb") or _safe_get(pids, "tmdb"),
        "imdb_id": _safe_get(pids, "Imdb") or _safe_get(pids, "imdb"),
        "douban_id": _safe_get(pids, "Douban") or _safe_get(pids, "douban"),
    }


def extract_external_images(item: dict, provider_ids: dict, media_type: str) -> dict:
    """提取外部图片链接。

    优先级:
    1. Emby 响应中的 ImageUrls 字段（某些插件会写入外部链接）
    2. 通过 TMDB ID 调 API 获取 poster_path / backdrop_path 并拼接外链
    3. 都获取不到则返回空字符串（绝不 fallback 到 Emby 内部 ImageTag 相对路径）
    """
    result = {"poster_url": "", "backdrop_url": ""}

    # 策略 1: Emby 响应中的外部 ImageUrls
    image_urls = item.get("ImageUrls") or {}
    if isinstance(image_urls, dict):
        primary = image_urls.get("Primary") or image_urls.get("primary") or ""
        backdrop = image_urls.get("Backdrop") or image_urls.get("backdrop") or image_urls.get("Art") or ""
        if isinstance(primary, str) and primary.startswith("http"):
            result["poster_url"] = primary
        if isinstance(backdrop, str) and backdrop.startswith("http"):
            result["backdrop_url"] = backdrop

    # 策略 2: TMDB API 拼接外部 URL
    tmdb_id = provider_ids.get("tmdb_id", "")
    if tmdb_id and (not result["poster_url"] or not result["backdrop_url"]):
        tmdb_type = "movie" if media_type == "Movie" else "tv"
        try:
            tmdb_images = get_tmdb_image_urls(tmdb_id, tmdb_type)
            if not result["poster_url"] and tmdb_images.get("poster_url"):
                result["poster_url"] = tmdb_images["poster_url"]
            if not result["backdrop_url"] and tmdb_images.get("backdrop_url"):
                result["backdrop_url"] = tmdb_images["backdrop_url"]
        except Exception:
            pass  # TMDB 不可用时静默降级

    return result


# ==========================================
# 通用入库
# ==========================================

def save_media_to_db(
    db,
    emby_item: dict,
    provider_ids: dict = None,
    images: dict = None,
    people: list = None,
    library_id: str = "",
    status: str = "synced",
    matched_actors: int = 0,
    total_actors: int = 0,
    error_message: str = "",
    parent_id: str = None,
    skip_profiles: bool = False,
    light_profiles: bool = False,   # 系列汉化专用：True 时 profile 解析走 light_mode
):
    """通用入库：将一条 Emby 媒体数据 UPSERT 到三张同步表中。

    三个消费者共用此函数：
    1. audit_local 路由 — 扫描已汉化媒体
    2. DoubanSinizer.sinicize() — 单条刮削成功后
    3. task_queue._process_single_item() — 刮削失败时

    Args:
        db:           SQLAlchemy Session（调用者管理 commit/rollback）
        emby_item:    Emby API 返回的 Item 字典（需含 Id, Name, Type, Overview 等）
        provider_ids: {"tmdb_id", "imdb_id", "douban_id"} 预提取的外部 ID，
                      为 None 时自动从 emby_item 提取
        images:       {"poster_url", "backdrop_url"} 预提取的图片外链，
                      为 None 时自动从 emby_item 提取
        people:       中文化后的演员列表（Emby People 格式），None 则跳过
        library_id:   所属媒体库 ID
        status:       synced / pending / failed
        matched_actors: 匹配成功的演员数
        total_actors:   演员总数
        error_message:  失败原因（status=failed 时使用）
        parent_id:    父级 Series ID（Episode 入库时传入）
        skip_profiles: True 时跳过 ensure_profiles_for_people（调用方已提前批量处理）
        light_profiles: True 时 ensure_profiles_for_people 传 light_mode=True
                       （系列汉化跳过 TMDB 上半场）；audit/task_queue 保持 False
    """
    if provider_ids is None:
        provider_ids = extract_provider_ids(emby_item)

    item_id = emby_item.get("Id", "")
    if not item_id:
        logger.warning("   ⚠ [DBCrud] emby_item 缺少 Id，跳过入库")
        return

    title = emby_item.get("Name", "")
    media_type = emby_item.get("Type", "")
    overview = emby_item.get("Overview", "")
    index_number = emby_item.get("IndexNumber")
    # Episode 专属字段: 季号(ParentIndexNumber) 与 Series 总集数(RecursiveItemCount)
    parent_index_number = emby_item.get("ParentIndexNumber")
    recursive_item_count = emby_item.get("RecursiveItemCount")

    # 自动提取图片（如果调用方未提供）
    if images is None:
        images = extract_external_images(emby_item, provider_ids, media_type)

    poster_url = images.get("poster_url", "") or ""
    backdrop_url = images.get("backdrop_url", "") or ""

    try:
        # ---- A. media_sync_status ----
        rec = db.query(MediaSyncStatus).filter(
            MediaSyncStatus.emby_item_id == item_id
        ).first()
        if not rec:
            rec = MediaSyncStatus(emby_item_id=item_id)
            db.add(rec)
        rec.tmdb_id = provider_ids.get("tmdb_id", "") or None
        rec.imdb_id = provider_ids.get("imdb_id", "") or None
        rec.douban_id = provider_ids.get("douban_id", "") or None
        rec.library_id = library_id
        rec.title = title
        rec.status = status
        rec.matched_actors = matched_actors
        rec.total_actors = total_actors
        rec.error_message = error_message or ""
        rec.update_time = datetime.now()

        # ---- B. media_metadata ----
        rec2 = db.query(MediaMetadata).filter(
            MediaMetadata.emby_item_id == item_id
        ).first()
        if not rec2:
            rec2 = MediaMetadata(emby_item_id=item_id)
            db.add(rec2)
        rec2.parent_id = parent_id
        rec2.media_type = media_type
        rec2.title = title or ""
        rec2.overview = overview or ""
        rec2.index_number = index_number
        rec2.parent_index_number = parent_index_number
        rec2.recursive_item_count = recursive_item_count
        rec2.poster_url = poster_url
        rec2.backdrop_url = backdrop_url
        rec2.update_time = datetime.now()

        # ---- C. actor_records (纯关联) + actor_profiles (全维数据中心) ----
        if people:
            # ★ 预处理：触发超级漏斗，确保所有演员的 ActorProfile 存在
            #   L0 本地拦截 → L1 豆瓣下载 → L2 TMDB 下载 → UPSERT
            #   skip_profiles=True 时调用方已提前批量处理，直接跳过
            if not skip_profiles:
                logger.info(
                    "🎬 [DBCrud] 开始解析 %d 位演员 Profile: %s (ID=%s)",
                    len([p for p in people if p.get("Type") in ("Actor", "GuestStar")]),
                    title, item_id,
                )
                ensure_profiles_for_people(db, people, light_mode=light_profiles)

            # 先删后插 actor_records，保证关联数据与当前一致
            db.query(ActorRecord).filter(
                ActorRecord.emby_item_id == item_id
            ).delete()

            for idx, p in enumerate(people):
                person_type = p.get("Type", "Actor")
                if person_type not in ("Actor", "GuestStar"):
                    continue
                name = _safe_get(p, "Name")
                if not name:
                    continue
                role = _safe_get(p, "Role")

                # ★ ActorRecord 纯关系映射 — 头像/生平统一由 ActorProfile.name 关联
                db.add(ActorRecord(
                    emby_item_id=item_id,
                    name=name,
                    role=role,
                    type=person_type,
                    sort_order=idx,
                    # ★ 角色译名来源与置信度（由汉化链路写入 person 私有键）
                    confidence_level=p.get("_cn_role_conf") or 0,
                    translation_source=p.get("_cn_role_src") or "",
                ))

    except Exception:
        logger.error(
            "❌ [DBCrud] save_media_to_db 写入失败 (item=%s):\n%s",
            item_id, traceback.format_exc(),
        )
        raise  # 重新抛出，让调用者决定如何处理
