"""
演职员中文化 — 任务触发接口。
"""

import logging
import os
import re
import time as _time
import traceback
from datetime import datetime

import requests as _requests
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

from config.settings import load_config
from database import SessionLocal, DATA_DIR
from models import MediaSyncStatus, MediaMetadata, ActorRecord, ActorProfile
from services.task_queue import start_full_sync
from services.ai_translator import get_translator
from services.douban_service import _truncate_actors, DoubanSinizer
from services.db_crud import (
    save_media_to_db,
    extract_provider_ids,
    extract_external_images,
)
from services.actor_profile_service import resolve_actor_profile, ensure_profiles_for_people
from services.translation_utils import is_valid_chinese_translation, apply_overview_with_guard
from services.overview_translator import needs_overview_translation
from utils.task_manager import task_manager

router = APIRouter()
logger = logging.getLogger("uvicorn")


class FullSyncRequest(BaseModel):
    library_id: str


class AuditSelectedRequest(BaseModel):
    item_ids: list[str]


class SinicizeSelectedRequest(BaseModel):
    """选中项批量汉化请求。"""
    item_ids: list[str]


class SinicizeAllRequest(BaseModel):
    """全量汉化请求 — 按媒体库批量处理所有未汉化项。"""
    library_id: str


class ForceTranslateBatchRequest(BaseModel):
    """强制汉化请求 — 无视当前状态，强制将选中项重置为 pending 并重新汉化。"""
    item_ids: list[str]


class RepairEpisodeOverviewsRequest(BaseModel):
    """补齐分集简介请求 — item_ids 传了=只修指定剧；不传（空数组）= 全库扫描。"""
    item_ids: list[str] = []


@router.post("/sync/full")
def trigger_full_sync(req: FullSyncRequest):
    """触发全量汉化任务（非阻塞，立即返回）。

    请求体:
        {"library_id": "1875208"}

    返回:
        200: {"message": "全量同步任务已启动，共下发 690 个任务"}
        409: {"detail": "当前已有汉化任务正在后台执行中，请稍后再试"}
    """
    logger.info(f"📨 [SyncActions] 收到全量同步请求: library_id={req.library_id}")
    ok, msg = start_full_sync(req.library_id)
    if not ok:
        raise HTTPException(status_code=409, detail=msg)
    return {"message": msg}


# ==========================================
# audit_local 专用辅助
# ==========================================

def _count_chinese_roles(people: list) -> tuple:
    """统计演员中角色名为中文的数量。

    Returns:
        (chinese_role_count, total_actors)
    """
    actors = [p for p in people if p.get("Type") == "Actor"]
    total = len(actors)
    if total == 0:
        return 0, 0
    chinese_count = sum(
        1 for a in actors
        if a.get("Role") and is_valid_chinese_translation(a.get("Role", ""))
    )
    return chinese_count, total


def _is_chinese_role_synced(people: list) -> bool:
    """判断媒体是否已汉化：>= 90% 演员角色名含中文。"""
    chinese_count, total = _count_chinese_roles(people)
    return total > 0 and (chinese_count / total) >= 0.9


def _fetch_episodes(host: str, api_key: str, user_id: str,
                    series_id: str) -> list:
    """获取指定 Series 下的所有分集（Episodes），含分页保护。

    增强版：额外请求 RecursiveItemCount 等统计字段。
    """
    base = f"{host}/emby/Users/{user_id}/Items" if user_id else f"{host}/emby/Items"
    all_episodes = []
    start_index = 0
    page_size = 100

    while True:
        params = {
            "api_key": api_key,
            "ParentId": series_id,
            "IncludeItemTypes": "Episode",
            "Recursive": "true",
            "Fields": "People,ProviderIds,Overview,ProductionYear,RecursiveItemCount,ParentIndexNumber,IndexNumber",
            "StartIndex": start_index,
            "Limit": page_size,
        }
        try:
            resp = _requests.get(base, params=params, timeout=30)
            if resp.status_code != 200:
                logger.warning(
                    "   ⚠ [Episodes] 获取 %s 分集失败: HTTP %d",
                    series_id, resp.status_code,
                )
                break
            data = resp.json()
            items = data.get("Items", [])
            if not items:
                break
            all_episodes.extend(items)
            total = data.get("TotalRecordCount", 0)
            if start_index + page_size >= total:
                break
            start_index += page_size
        except Exception:
            logger.warning(
                "   ⚠ [Episodes] 获取 %s 分集异常: %s",
                series_id, traceback.format_exc(),
            )
            break

    return all_episodes


def _process_episodes(db, episodes: list, series_id: str, library_id: str,
                      apply_localization: bool = False,
                      douban_actor_map: dict = None,
                      series_name: str = "",
                      year: str = "",
                      max_actors: int = 50) -> int:
    """处理 Series 下的所有分集，统一通过 save_media_to_db 入库。

    Args:
        apply_localization: 是否对分集演员应用中文化（来自豆瓣匹配结果）
        douban_actor_map: {emby_actor_name: {"name": 中文名, "role": 中文角色名}}
        series_name: 剧集名称，供 AI 翻译上下文使用
        year:        剧集年份（ProductionYear），强化 AI 翻译作品消歧，可选
        max_actors:      最大入库演员数（用于截断）
    """
    processed = 0
    for ep in episodes:
        try:
            ep_id = ep.get("Id", "")
            if not ep_id:
                continue

            ep_people = ep.get("People", []) or []

            # ★ 分集演员中文化：使用豆瓣匹配结果替换英文名/角色
            if apply_localization and douban_actor_map:
                ep_people = _localize_episode_people(
                    ep_people, douban_actor_map, series_name=series_name, year=year,
                )

            # ★ 分集截断
            ep_people = _truncate_actors(ep_people, max_actors)

            pids = extract_provider_ids(ep)
            chinese_count, total_actors = _count_chinese_roles(ep_people)
            ep_status = "synced" if _is_chinese_role_synced(ep_people) else "pending"
            ep_images = extract_external_images(ep, pids, ep.get("Type", "Episode"))

            save_media_to_db(
                db,
                emby_item=ep,
                provider_ids=pids,
                images=ep_images,
                people=ep_people if apply_localization else None,
                library_id=library_id,
                status=ep_status,
                matched_actors=chinese_count,
                total_actors=total_actors,
                parent_id=series_id,
            )

            db.flush()
            processed += 1
        except Exception:
            logger.warning(
                "   ⚠ [Episode] 处理 %s (ID=%s) 失败:\n%s",
                ep.get("Name", "?"), ep.get("Id", "?"),
                traceback.format_exc(),
            )
            db.rollback()
            continue

    return processed


def _localize_episode_people(ep_people: list, douban_map: dict,
                             series_name: str = "", year: str = "") -> list:
    """对分集的演员列表应用中文化替换（含 AI 兜底 + 动态缓存）。

    三级漏斗策略：
    a. 全量字典匹配：先查 douban_map（豆瓣全量演员 + Series 层已汉化数据）
    b. AI 兜底翻译：未命中时，调 AI 翻译 Name + Role
    c. 动态缓存学习：AI 成功结果即时写入 douban_map，阻止重复 API 调用

    Args:
        ep_people:   Emby 分集的 People 列表
        douban_map:  {emby_name_lower: {"name": "中文名", "role": "中文角色"}}
                     此字典会被原地修改（引用传递），用于跨分集缓存
        series_name: 剧集名称，作为 AI 翻译上下文
        year:        剧集年份（ProductionYear），强化作品消歧，可选

    Returns:
        中文化后的 People 列表
    """
    # AI 翻译器（延迟初始化，仅在有未命中演员时才加载）
    _translator = None

    def _get_translator():
        nonlocal _translator
        if _translator is None:
            _translator = get_translator()
        return _translator

    # 收集本轮需要 AI 翻译的未命中项（姓名 + 角色分开）
    _pending_ai_names = {}    # {lookup_key: emby_name}
    _pending_ai_roles = {}    # {lookup_key: emby_role}

    localized = []
    for p in ep_people:
        person_type = p.get("Type", "Actor")
        # GuestStar (客串演员) 也纳入中文化范围
        if person_type not in ("Actor", "GuestStar"):
            localized.append(p)
            continue

        emby_name = (p.get("Name") or "").strip()
        emby_role = (p.get("Role") or "").strip()
        lookup_key = emby_name.lower()

        if lookup_key in douban_map:
            # ---- 漏斗 a: 全量字典命中 ----
            info = douban_map[lookup_key]
            new_p = dict(p)
            # ★ 注入豆瓣头像外链，供 actor_profile_service 超级漏斗短路使用
            new_p["DoubanAvatarUrl"] = info.get("avatar", "")
            # ★ 注入豆瓣演员 ID，使 L1 漏斗能精准调用 celebrity_details
            douban_id_str = str(info.get("douban_id", "") or "")
            if douban_id_str:
                new_p["DoubanCelebrityId"] = douban_id_str
            if info.get("name") and not is_valid_chinese_translation(emby_name):
                new_p["Name"] = info["name"]
            db_role = info.get("role", "")
            if db_role and db_role not in ("演员", "配音", "actor", "actress"):
                new_p["Role"] = db_role
            localized.append(new_p)
        else:
            # ---- 漏斗 b: 标记待 AI 翻译 ----
            if emby_name and not is_valid_chinese_translation(emby_name):
                _pending_ai_names[lookup_key] = emby_name
            if emby_role and not is_valid_chinese_translation(emby_role):
                _pending_ai_roles[lookup_key] = emby_role
            localized.append(p)

    # ---- 漏斗 b/c: AI 批量翻译 + 缓存回写 ----
    if _pending_ai_names or _pending_ai_roles:
        translator = _get_translator()
        if translator.is_available():
            # b1. 批量翻译人名
            if _pending_ai_names:
                unique_names = list(set(_pending_ai_names.values()))
                try:
                    name_map = translator.translate_names(unique_names, context=series_name, year=year)
                    # c. 动态缓存：将 AI 结果写入 douban_map
                    for lookup_key, original_name in _pending_ai_names.items():
                        translated = name_map.get(original_name, "")
                        if translated and is_valid_chinese_translation(translated):
                            douban_map.setdefault(lookup_key, {})["name"] = translated
                            logger.debug(
                                "   🤖 [AI缓存] 人名: %s → %s", original_name, translated,
                            )
                except Exception:
                    logger.debug("   ⚠ [AI] 批量人名翻译异常，跳过")

            # b2. 批量翻译角色名
            if _pending_ai_roles:
                unique_roles = list(set(_pending_ai_roles.values()))
                # 过滤掉明显的占位符
                unique_roles = [
                    r for r in unique_roles
                    if r and r.lower() not in ("actor", "actress", "guest", "guest star", "unknown")
                ]
                if unique_roles:
                    try:
                        role_map = translator.translate_roles(unique_roles, context=series_name, year=year)
                        # c. 动态缓存
                        for lookup_key, original_role in _pending_ai_roles.items():
                            translated = role_map.get(original_role, "")
                            if translated and is_valid_chinese_translation(translated):
                                douban_map.setdefault(lookup_key, {})["role"] = translated
                                logger.debug(
                                    "   🤖 [AI缓存] 角色: %s → %s", original_role, translated,
                                )
                    except Exception:
                        logger.debug("   ⚠ [AI] 批量角色翻译异常，跳过")

            # b3. 用更新后的 douban_map 重新应用翻译
            if _pending_ai_names or _pending_ai_roles:
                for i, p in enumerate(localized):
                    person_type = p.get("Type", "Actor")
                    if person_type not in ("Actor", "GuestStar"):
                        continue
                    emby_name = (p.get("Name") or "").strip()
                    lookup_key = emby_name.lower()
                    if lookup_key in douban_map:
                        info = douban_map[lookup_key]
                        new_p = dict(p)
                        # ★ 注入豆瓣头像外链
                        new_p["DoubanAvatarUrl"] = info.get("avatar", "")
                        # ★ 注入豆瓣演员 ID，使 L1 漏斗能精准调用 celebrity_details
                        douban_id_str = str(info.get("douban_id", "") or "")
                        if douban_id_str:
                            new_p["DoubanCelebrityId"] = douban_id_str
                        if info.get("name") and not is_valid_chinese_translation(emby_name):
                            new_p["Name"] = info["name"]
                        db_role = info.get("role", "")
                        if db_role and db_role not in ("演员", "配音", "actor", "actress"):
                            new_p["Role"] = db_role
                        localized[i] = new_p

    # ★ 空角色强制兜底：所有处理完毕后，仍为空的 Role 默认赋 "演员"
    for i, p in enumerate(localized):
        if p.get("Type", "") in ("Actor", "GuestStar"):
            if not (p.get("Role") or "").strip():
                new_p = dict(p)
                new_p["Role"] = "演员"
                localized[i] = new_p

    return localized


def _build_douban_actor_map(douban_actors: list, emby_actors: list) -> dict:
    """构建豆瓣演员匹配映射表，供分集演员中文化使用。

    匹配逻辑与 DoubanSinizer._match_and_update() 保持一致：
    1. 直接中文名匹配
    2. 拼音降级匹配

    Returns:
        {emby_name_lower: {"name": "豆瓣中文名", "role": "豆瓣角色名"}}
    """
    import re as _re
    from pypinyin import lazy_pinyin as _lazy_pinyin

    def _to_pinyin_key(chinese_name: str) -> str:
        return "".join(_lazy_pinyin(chinese_name)).lower()

    def _normalize_english(name: str) -> str:
        return _re.sub(r"[^a-z]", "", name.lower())

    actor_map = {}
    douban_names = {da.get("name", "") for da in douban_actors}
    used_douban = set()

    for ea in emby_actors:
        emby_name = (ea.get("Name") or "").strip()
        if not emby_name:
            continue

        matched_da = None

        # Level 1: 直接中文名匹配
        if emby_name in douban_names:
            for da in douban_actors:
                if da.get("name") == emby_name and da["name"] not in used_douban:
                    matched_da = da
                    break

        # Level 2: 拼音降级匹配
        if not matched_da:
            emby_key = _normalize_english(emby_name)
            for da in douban_actors:
                if da.get("name") in used_douban:
                    continue
                py_key = _to_pinyin_key(da.get("name", ""))
                if emby_key == py_key:
                    matched_da = da
                    break

        if matched_da:
            used_douban.add(matched_da["name"])
            actor_map[emby_name.lower()] = {
                "name": matched_da.get("name", ""),
                "role": matched_da.get("role", ""),
                "avatar": matched_da.get("avatar", ""),
                "douban_id": str(matched_da.get("id", "") or ""),
            }

    return actor_map


def _enrich_actor_map_from_series(actor_map: dict, series_people: list):
    """用 Series 层级已有的中文数据丰富演员映射表。

    当 Series 已经汉化（演员名为中文），用 Series 的 Name→Role 关系
    填充 actor_map，以便分集中的同名演员获得正确的角色名。
    """
    for p in series_people:
        if p.get("Type") != "Actor":
            continue
        name = (p.get("Name") or "").strip()
        role = (p.get("Role") or "").strip()
        if not name:
            continue
        key = name.lower()
        # 不覆盖已有的豆瓣匹配结果
        if key not in actor_map and is_valid_chinese_translation(name):
            actor_map[key] = {"name": name, "role": role}


def _db_path() -> str:
    """返回当前 SQLite 数据库文件的绝对路径。"""
    return os.path.join(DATA_DIR, "emby_ai.db")


# ==========================================
# ★ 公共函数：单 Item 完整审计 & 深度入库
# ==========================================

def _audit_and_save_single_item(
    db,
    item: dict,
    host: str,
    api_key: str,
    user_id: str,
    library_id: str = "",
) -> dict:
    """对单个 Emby Item 执行完整的汉化率审计 + 深度分集入库。

    这是 audit_local 和 audit_selected 共用的唯一入口，
    保证无论从哪个入口触发，执行的逻辑完全一致。

    流程:
    1. 90% 汉化率判定 (中文 Role 占比)
    2. 提取 ProviderIds + 外部图片链接
    3. 顶层媒体 UPSERT 到 media_sync_status + media_metadata (+ actor_records)
    4. ★ 若 item['Type'] == 'Series'：
       a. 递归抓取所有 Episode（含分页保护）
       b. 构建演员中文映射表（来自 Series 自身已汉化数据）
       c. 对分集演员应用中文化替换
       d. 分集逐一 UPSERT 到三表
       e. 已汉化 Series 存演员详情，未汉化仅存元数据

    Args:
        db:           SQLAlchemy Session（调用者管理 commit/rollback）
        item:         Emby API 返回的 Item 字典
        host:         Emby 服务地址
        api_key:      Emby API Key
        user_id:      Emby User ID
        library_id:   所属媒体库 ID

    Returns:
        {"synced": bool, "item_type": str, "item_name": str, "episodes_processed": int}
    """
    item_id = item.get("Id", "")
    item_name = item.get("Name", "")
    item_type = item.get("Type", "")
    people = item.get("People", []) or []

    # ★ 继承 library_id：如果调用方未提供，尝试从 Item 自身提取
    # Emby 在列表视图中会把媒体库 ID 放在 ParentId 字段
    if not library_id:
        library_id = item.get("ParentId", "") or ""

    # ---- 1. 汉化率判定 ----
    pids = extract_provider_ids(item)
    chinese_count, total_actors = _count_chinese_roles(people)
    is_synced = _is_chinese_role_synced(people)
    item_status = "synced" if is_synced else "pending"
    images = extract_external_images(item, pids, item_type)
    episodes_processed = 0

    # ★ 按配置截断演员数（抓取时不截断，入库时截断）
    cfg = load_config()
    max_actors = cfg.get("max_actors_per_media", 50)

    try:
        if is_synced:
            # 截断回写：people 只保留前 max_actors 位 Actor
            people = _truncate_actors(people, max_actors)

            # ---- 已汉化 → 三表全量入库 ----
            save_media_to_db(
                db,
                emby_item=item,
                provider_ids=pids,
                images=images,
                people=people,
                library_id=library_id,
                status=item_status,
                matched_actors=chinese_count,
                total_actors=total_actors,
                parent_id=None,
            )
            db.flush()

            # ---- ★ Series 深度抓取分集 ----
            if item_type == "Series":
                total_episodes = item.get("RecursiveItemCount")
                logger.info(
                    "   📺 [Audit] Series 深度抓取分集: %s (ID: %s, 总 %s 项)",
                    item_name, item_id,
                    total_episodes if total_episodes is not None else "?",
                )
                episodes = _fetch_episodes(host, api_key, user_id, item_id)
                if episodes:
                    # 构建中文映射表（基于 Series 已有的汉化数据）
                    douban_map = _build_douban_actor_map(
                        douban_actors=[],
                        emby_actors=[p for p in people if p.get("Type") == "Actor"],
                    )
                    _enrich_actor_map_from_series(douban_map, people)

                    episodes_processed = _process_episodes(
                        db, episodes, item_id, library_id,
                        apply_localization=bool(douban_map),
                        douban_actor_map=douban_map,
                        series_name=item_name,
                        year=str(item.get("ProductionYear", "") or ""),
                        max_actors=max_actors,
                    )
                    logger.info(
                        "   ✅ [Audit] %s: %d 个分集已处理",
                        item_name, episodes_processed,
                    )
        else:
            # ---- 未汉化 → 状态记录 + 演员数据入库 ----
            # ★ 即使未汉化也保存 actor_records，避免后续汉化时
            #    因本地无演员数据而失败，消除"必须先审计再汉化"的割裂体验
            save_media_to_db(
                db,
                emby_item=item,
                provider_ids=pids,
                images=None,
                people=people,
                library_id=library_id,
                status=item_status,
                matched_actors=chinese_count,
                total_actors=total_actors,
                parent_id=None,
            )

            # ★ 即使未汉化，仍抓取分集元数据
            if item_type == "Series":
                episodes = _fetch_episodes(host, api_key, user_id, item_id)
                if episodes:
                    episodes_processed = _process_episodes(
                        db, episodes, item_id, library_id,
                        apply_localization=False,
                        douban_actor_map=None,
                        series_name=item_name,
                        year=str(item.get("ProductionYear", "") or ""),
                        max_actors=max_actors,
                    )

        # ★ 用实际入库分集数刷新父 Series 计数（不信任 stale RecursiveItemCount）
        if item_type == "Series" and episodes_processed > 0:
            series_mm = db.query(MediaMetadata).filter(
                MediaMetadata.emby_item_id == item_id
            ).first()
            if series_mm:
                series_mm.recursive_item_count = episodes_processed

        db.flush()
        return {
            "synced": is_synced,
            "item_type": item_type,
            "item_name": item_name,
            "episodes_processed": episodes_processed,
        }

    except Exception:
        logger.error(
            "   ❌ [Audit] 单 Item 审计失败: %s (ID=%s)\n%s",
            item_name, item_id, traceback.format_exc(),
        )
        raise


# ==========================================
# ★ 统一审计入口：_sync_and_audit_single_item
# ==========================================

def _sync_and_audit_single_item(emby_item_id: str, library_id: str = "") -> dict:
    """★ 统一审计入口：给定 Emby Item ID，自动完成 Emby 拉取 + 本地审计。

    这是所有审计路径（全量/选中项/大盘同步/巡逻/前置审计）共用的唯一入口。
    保证无论从哪个入口触发，执行的"对账 → 拉取 → 落盘 → 刮削"逻辑完全一致。

    **【关键修复】** 即使本地 DB 中没有该 Item 的记录，也会自动从 Emby 拉取并入库，
    彻底消除"本地找不到就 404"的问题。

    流程:
    1. 检查本地 media_sync_status 是否存在（日志用途）
    2. ★ 强制从 Emby API 拉取最新 Item 详情（含 People + ProviderIds）
    3. 如果 Emby 中也不存在 → 返回 success=False（真正的 404）
    4. 执行统一的审计逻辑 (_audit_and_save_single_item)
    5. 返回结构化结果（含 tmdb_id，供上层做 TMDB 富化等后续处理）

    Args:
        emby_item_id: Emby Item ID
        library_id:   所属媒体库 ID（可选，会自动从 Emby 数据中提取）

    Returns:
        {
            "success": bool,
            "synced": bool,
            "item_type": str,
            "item_name": str,
            "episodes_processed": int,
            "tmdb_id": str,
            "error": str,
        }
    """
    cfg = load_config()
    host = cfg.get("emby_host", "").rstrip("/")
    api_key = cfg.get("emby_api_key", "")
    user_id = cfg.get("emby_user_id", "")

    if not host or not api_key:
        return {
            "success": False, "error": "Emby 未配置",
            "synced": False, "item_type": "", "item_name": "",
            "episodes_processed": 0, "tmdb_id": "",
        }

    # ---- Step 1: 检查本地 DB 状态（纯日志用途，不影响流程） ----
    db_check = SessionLocal()
    try:
        local_sync = db_check.query(MediaSyncStatus).filter(
            MediaSyncStatus.emby_item_id == emby_item_id
        ).first()
        if not local_sync:
            logger.info("🔍 [SyncAudit] Item=%s 本地缺失，将从 Emby 拉取", emby_item_id)
        elif not local_sync.tmdb_id:
            logger.info("🔍 [SyncAudit] Item=%s 本地缺少 TMDB ID，将从 Emby 重新拉取", emby_item_id)
    finally:
        db_check.close()

    # ---- Step 2: ★ 强制从 Emby 拉取最新数据（关键修复） ----
    from services.emby_service import get_item_info
    item = get_item_info(emby_item_id)
    if not item:
        logger.warning("⚠ [SyncAudit] Emby 中未找到 Item=%s（可能已被删除）", emby_item_id)
        return {
            "success": False,
            "error": f"Emby 中未找到 Item {emby_item_id}（可能已被删除）",
            "synced": False, "item_type": "", "item_name": "",
            "episodes_processed": 0, "tmdb_id": "",
        }

    item_name = item.get("Name", "?")
    item_type = item.get("Type", "")

    # ---- Step 3: 执行统一审计逻辑 (_audit_and_save_single_item) ----
    db = SessionLocal()
    try:
        result = _audit_and_save_single_item(
            db, item, host, api_key, user_id, library_id=library_id
        )
        db.commit()

        pids = extract_provider_ids(item)
        tmdb_id = pids.get("tmdb_id", "")

        logger.info(
            "✅ [SyncAudit] %s (%s) 审计完成: synced=%s episodes=%d tmdb=%s",
            item_name, emby_item_id, result["synced"],
            result["episodes_processed"], tmdb_id or "无",
        )

        return {
            "success": True,
            "synced": result["synced"],
            "item_type": item_type,
            "item_name": item_name,
            "episodes_processed": result["episodes_processed"],
            "tmdb_id": tmdb_id,
            "error": "",
        }
    except Exception:
        db.rollback()
        logger.error(
            "❌ [SyncAudit] %s (%s) 审计异常:\n%s",
            item_name, emby_item_id, traceback.format_exc(),
        )
        return {
            "success": False,
            "error": traceback.format_exc(),
            "synced": False, "item_type": item_type,
            "item_name": item_name,
            "episodes_processed": 0, "tmdb_id": "",
        }
    finally:
        db.close()


# ==========================================
# 接口: POST /api/sync/audit_local
# ==========================================

@router.post("/sync/audit_local")
def audit_local_sync(req: FullSyncRequest):
    """扫描 Emby 库中全部媒体项并同步到本地数据库。

    ★ 统一调用 _sync_and_audit_single_item，与审计选中项逻辑完全一致。
    流程: 获取库内全部 ID → 逐项 _sync_and_audit_single_item（自动拉取+对账+落盘+刮削）
    """
    cfg = load_config()
    host = cfg.get("emby_host", "").rstrip("/")
    api_key = cfg.get("emby_api_key", "")
    user_id = cfg.get("emby_user_id", "")

    if not host or not api_key:
        raise HTTPException(status_code=400, detail="缺少 Emby 配置")

    logger.info("📁 [Audit] 数据库路径: %s", _db_path())
    logger.info("🚀 [Audit] 开始扫描库 ID=%s ...", req.library_id)

    # ---- Step 1: 从 Emby 获取全部顶级媒体 ID（轻量） ----
    item_ids = _fetch_library_item_ids(host, api_key, user_id, req.library_id)
    if not item_ids:
        return {
            "message": "媒体库中没有发现任何媒体项",
            "total_scanned": 0,
            "marked_as_synced": 0,
            "episodes_processed": 0,
            "db_sync_rows": 0,
            "db_metadata_rows": 0,
            "db_actor_rows": 0,
        }

    total_scanned = 0
    total_synced = 0
    total_episodes_processed = 0

    # ---- Step 2: 逐项统一审计 ----
    for idx, item_id in enumerate(item_ids):
        try:
            result = _sync_and_audit_single_item(item_id, library_id=req.library_id)
            total_scanned += 1

            if result["success"]:
                if result["synced"]:
                    total_synced += 1
                total_episodes_processed += result["episodes_processed"]
            else:
                logger.warning(
                    "   ⚠ [Audit] %s 审计失败: %s",
                    item_id, result.get("error", ""),
                )

            # 每 50 项输出一次进度日志
            if (idx + 1) % 50 == 0:
                logger.info(
                    "📋 [Audit] 进度 %d/%d | 已汉化: %d",
                    idx + 1, len(item_ids), total_synced,
                )

        except Exception:
            logger.warning(
                "   ⚠ [Audit] %s 审计异常，跳过\n%s",
                item_id, traceback.format_exc(),
            )
            continue

    # ---- 提交后验证 ----
    from sqlalchemy import func as _func
    db = SessionLocal()
    try:
        sync_count = db.query(_func.count(MediaSyncStatus.emby_item_id)).scalar() or 0
        meta_count = db.query(_func.count(MediaMetadata.emby_item_id)).scalar() or 0
        actor_count = db.query(_func.count(ActorRecord.id)).scalar() or 0
    finally:
        db.close()

    logger.info(
        "📋 [Audit] 扫描完成 | 总计: %d | 已汉化: %d | 分集处理: %d",
        total_scanned, total_synced, total_episodes_processed,
    )
    logger.info(
        "📊 [Audit] 数据库确认: "
        "media_sync_status=%d 行, media_metadata=%d 行, actor_records=%d 行",
        sync_count, meta_count, actor_count,
    )
    return {
        "message": "扫描完成",
        "total_scanned": total_scanned,
        "marked_as_synced": total_synced,
        "episodes_processed": total_episodes_processed,
        "db_sync_rows": sync_count,
        "db_metadata_rows": meta_count,
        "db_actor_rows": actor_count,
    }


# ==========================================
# 接口: POST /api/sync/audit_selected
# ==========================================

@router.post("/sync/audit_selected")
def audit_selected_sync(req: AuditSelectedRequest):
    """对用户选中的特定媒体项执行汉化率审计并更新数据库。

    ★ 统一调用 _sync_and_audit_single_item，与全量审计、批量审计逻辑完全一致。
    【关键修复】即使选中项在本地 DB 中不存在，也会自动从 Emby 拉取并入库。
    """
    cfg = load_config()
    host = cfg.get("emby_host", "").rstrip("/")
    api_key = cfg.get("emby_api_key", "")

    if not host or not api_key:
        raise HTTPException(status_code=400, detail="缺少 Emby 配置")

    if not req.item_ids:
        raise HTTPException(status_code=400, detail="item_ids 不能为空")

    logger.info(
        "🎯 [AuditSelected] 审计 %d 个选中项",
        len(req.item_ids),
    )

    total_checked = 0
    marked_synced = 0
    total_episodes_processed = 0
    failed_ids: list[str] = []

    for idx, item_id in enumerate(req.item_ids, 1):
        logger.info(
            "📋 [AuditSelected] 进度 %d/%d: %s",
            idx, len(req.item_ids), item_id,
        )
        try:
            # ★ 统一调用 — 自动处理 Emby 拉取 → 对账 → 落盘 → 刮削
            result = _sync_and_audit_single_item(item_id, library_id="")
            total_checked += 1

            if result["success"]:
                if result["synced"]:
                    marked_synced += 1
                total_episodes_processed += result["episodes_processed"]
            else:
                failed_ids.append(item_id)
                logger.warning(
                    "   ⚠ [AuditSelected] %s 审计失败: %s",
                    item_id, result.get("error", "?"),
                )

        except Exception:
            failed_ids.append(item_id)
            logger.warning(
                "   ⚠ [AuditSelected] %s 审计异常，跳过\n%s",
                item_id, traceback.format_exc(),
            )
            continue

    logger.info(
        "📋 [AuditSelected] 审计完成 | 共计: %d | 标记已汉化: %d | 分集: %d | 失败: %d",
        total_checked, marked_synced, total_episodes_processed, len(failed_ids),
    )
    return {
        "message": "局部审计完成",
        "total_checked": total_checked,
        "marked_synced": marked_synced,
        "episodes_processed": total_episodes_processed,
        "failed_ids": failed_ids if failed_ids else None,
    }


# ==========================================
# 接口: GET /api/media/{item_id}/details
# 分集数据透视 — 只读，查询本地 SQLite 中持久化的元数据与演员
# ==========================================

@router.get("/media/{item_id}/details")
def get_media_details(item_id: str):
    """获取剧集的分集数据透视 — 含顶层常驻演员与各分集专属演员。

    纯只读接口，数据全部来自本地 SQLite。
    ★ 演员头像与生平数据通过 name 关联 ActorProfile，
      优先返回 local_image_url (同源相对路径 /static_actors/...)，
      降级为外部 image_url。

    返回结构:
        {
            "series": {
                "emby_item_id": "...",
                "title": "...",
                "overview": "...",
                "recursive_item_count": 12,
                "actors": [
                    {
                        "name": "...", "role": "...", "type": "Actor",
                        "image_url": "...",            // 外部直链兜底
                        "local_image_url": "...",      // ★ 本地静态 URL (优先使用)
                        "birth_date": "...", "birth_place": "...", "overview": "...",
                        "sort_order": 0
                    }
                ]
            },
            "episodes": [...]
        }
    """
    # ★ 本地静态图统一用同源相对路径（/static_actors/...），
    #   由 Vite 代理 / 后端静态挂载转发到 FastAPI，localhost 与局域网 IP 访问均可达。
    #   注意：不可用 request.base_url 拼绝对地址 —— Vite 代理 changeOrigin 会把它写成
    #   127.0.0.1:8000，手机等局域网设备访问该地址会指向设备自身，导致头像裂图。

    db = SessionLocal()
    try:
        # 1. 查询顶层剧集元数据
        series_meta = db.query(MediaMetadata).filter(
            MediaMetadata.emby_item_id == item_id
        ).first()

        if not series_meta:
            raise HTTPException(status_code=404, detail=f"未找到媒体项: {item_id}")

        # 2. 收集所有相关的演员 name → 批量查 ActorProfile
        all_actor_names = set()

        # 2a. 顶层演员
        series_actor_records = db.query(ActorRecord).filter(
            ActorRecord.emby_item_id == item_id
        ).order_by(ActorRecord.sort_order).all()
        for a in series_actor_records:
            if a.name:
                all_actor_names.add(a.name)

        # 2b. 分集演员
        episodes_meta = db.query(MediaMetadata).filter(
            MediaMetadata.parent_id == item_id
        ).order_by(
            MediaMetadata.parent_index_number.asc(),
            MediaMetadata.index_number.asc(),
        ).all()

        ep_actor_records_map = {}  # {ep_item_id: [ActorRecord]}
        for ep in episodes_meta:
            ep_actors = db.query(ActorRecord).filter(
                ActorRecord.emby_item_id == ep.emby_item_id
            ).order_by(ActorRecord.sort_order).all()
            ep_actor_records_map[ep.emby_item_id] = ep_actors
            for a in ep_actors:
                if a.name:
                    all_actor_names.add(a.name)

        # ★ 批量查询 ActorProfile (一次查询，O(1) 字典查找)
        profiles = {}
        if all_actor_names:
            profile_rows = db.query(ActorProfile).filter(
                ActorProfile.name.in_(list(all_actor_names))
            ).all()
            for prof in profile_rows:
                profiles[prof.name] = prof

        # ---- 辅助: 组装单个演员的完整响应 ----
        def _build_actor(a: ActorRecord) -> dict:
            prof = profiles.get(a.name)
            local_image_url = ""
            image_url = ""

            if prof:
                # 优先本地静态文件 — 同源相对路径，浏览器按当前页面 origin 解析
                if prof.local_image_path:
                    local_image_url = f"/static_actors/{prof.local_image_path}"
                image_url = prof.image_url or ""

            return {
                "name": a.name,
                "role": a.role or "",
                "type": a.type,
                "image_url": image_url,                   # 外部直链兜底
                "local_image_url": local_image_url,       # 本地静态 URL（/static_actors/...）
                "local_image_path": prof.local_image_path if prof else "",   # ★ DB 相对地址 → 前端走 WebDAV 代理
                "birth_date": prof.birth_date if prof else "",
                "birth_place": prof.birth_place if prof else "",
                "overview": prof.overview if prof else "",
                "sort_order": a.sort_order,
            }

        # 3. 组装 Series
        series = {
            "emby_item_id": series_meta.emby_item_id,
            "title": series_meta.title,
            "overview": series_meta.overview,
            "recursive_item_count": series_meta.recursive_item_count,
            "actors": [_build_actor(a) for a in series_actor_records],
        }

        # 4. 组装 Episodes
        episodes = []
        for ep in episodes_meta:
            ep_records = ep_actor_records_map.get(ep.emby_item_id, [])
            episodes.append({
                "emby_item_id": ep.emby_item_id,
                "title": ep.title,
                "overview": ep.overview,
                "index_number": ep.index_number,
                "parent_index_number": ep.parent_index_number,
                "poster_url": ep.poster_url,
                "actors": [_build_actor(a) for a in ep_records],
            })

        return {"series": series, "episodes": episodes}

    except HTTPException:
        raise
    except Exception:
        logger.error(
            "❌ [MediaDetails] 查询失败 (item=%s):\n%s",
            item_id, traceback.format_exc(),
        )
        raise HTTPException(status_code=500, detail="服务器内部错误")
    finally:
        db.close()


# ==========================================
# 接口: GET /api/tasks/{task_id}
# 前端轮询后台任务进度
# ==========================================

@router.get("/tasks/{task_id}")
def get_task_status(task_id: str):
    """查询后台任务的实时进度。

    返回:
        {
            "status": "running|completed|error",
            "total": int, "current": int, "message": str,
            "metadata": dict,
        }
        任务不存在返回 404。
    """
    status = task_manager.get_status(task_id)
    if status is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return status


# ==========================================
# 接口: POST /api/episodes/batch-enrich
# 分集批量富化 — TMDB 季节 API → 简介 + 客串演员 + 本地漏斗
# ==========================================

class BatchEnrichRequest(BaseModel):
    item_id: str  # Emby Series Item ID


@router.post("/episodes/batch-enrich")
def batch_enrich_episodes(
    req: BatchEnrichRequest,
    background_tasks: BackgroundTasks,
):
    """触发分集批量富化任务（后台执行，立即返回 task_id）。

    后台任务会:
    1. 查本地 DB 获取 Series 的 tmdb_id 及已有的分集记录
    2. 按季调用 TMDB GET /tv/{tmdb_id}/season/{season_number}
    3. 提取每集的 overview + guest_stars（客串演员）
    4. 客串演员传入本地 L0-L2 超级漏斗完成头像嗅探/下载
    5. 将简介写入 media_metadata、演员关联写入 actor_records

    Args:
        item_id: Emby Series 的 Item ID

    Returns:
        {"task_id": str, "message": str}
    """
    cfg = load_config()
    api_key = cfg.get("tmdb_api_key", "")
    if not api_key:
        raise HTTPException(status_code=400, detail="TMDB API Key 未配置")

    # 验证 Series 存在且有 tmdb_id
    db = SessionLocal()
    try:
        sync_status = db.query(MediaSyncStatus).filter(
            MediaSyncStatus.emby_item_id == req.item_id
        ).first()
        if not sync_status:
            raise HTTPException(status_code=404, detail="媒体项未入库，请先执行审计")
        if not sync_status.tmdb_id:
            raise HTTPException(
                status_code=400,
                detail="该媒体项缺少 TMDB ID，无法通过 TMDB API 获取分集数据",
            )

        # 统计已有的分集数据（用于估算总步数）
        existing_eps = db.query(MediaMetadata).filter(
            MediaMetadata.parent_id == req.item_id,
            MediaMetadata.parent_index_number.isnot(None),
        ).all()

        # 收集所有不同的季号
        season_numbers = sorted(set(
            ep.parent_index_number for ep in existing_eps
            if ep.parent_index_number is not None
        ))
        if not season_numbers:
            season_numbers = [1]  # 默认至少尝试第 1 季

        series_name = sync_status.title or req.item_id
        tmdb_id = sync_status.tmdb_id
    finally:
        db.close()

    # 创建后台任务
    task_id = task_manager.create_task(
        total=len(season_numbers),
        message=f"开始处理: {series_name}",
        metadata={
            "item_id": req.item_id,
            "item_name": series_name,
            "tmdb_id": tmdb_id,
            "season_count": len(season_numbers),
        },
    )

    # 提交后台任务
    background_tasks.add_task(
        _batch_enrich_episodes_task,
        task_id=task_id,
        item_id=req.item_id,
        tmdb_id=tmdb_id,
        season_numbers=season_numbers,
        series_name=series_name,
    )

    logger.info(
        "🚀 [BatchEnrich] 后台任务已提交: task=%s series=%s seasons=%d",
        task_id, series_name, len(season_numbers),
    )
    return {
        "task_id": task_id,
        "message": f"分集富化任务已启动，共 {len(season_numbers)} 季",
    }


# ==========================================
# 后台任务: 分集批量富化引擎
# ==========================================

def _batch_enrich_episodes_task(
    task_id: str,
    item_id: str,
    tmdb_id: str,
    season_numbers: list,
    series_name: str,
):
    """后台任务核心逻辑 — 按季调用 TMDB，处理 guest_stars + overview。

    设计要点（规避 N+1 查询）:
    - 使用 TMDB 整季接口 GET /tv/{id}/season/{n}，一次请求拿到全季数据
    - guest_stars 列表一次性传给 ensure_profiles_for_people 批量处理
    - 演员先去重（按 Name），再批量走漏斗，避免重复 TMDB 搜索

    Args:
        task_id:        任务 ID（用于进度上报）
        item_id:        Emby Series Item ID
        tmdb_id:        TMDB Series ID
        season_numbers: 需要处理的季号列表
        series_name:    剧集名称
    """
    cfg = load_config()
    api_key = cfg.get("tmdb_api_key", "")
    base_url = cfg.get("tmdb_base_url", "") or "https://api.tmdb.org/3"

    # ★ sentinel 变量：finally 块统一保证任务被正确终结
    _enrich_success = False
    _enrich_final_msg = f"❌ 任务失败: {series_name}"

    db = SessionLocal()
    total_enriched_eps = 0
    total_guest_stars = 0

    try:
        for idx, season_num in enumerate(season_numbers, 1):
            # ---- 进度上报 ----
            task_manager.update_progress(
                task_id,
                current=idx - 1,
                message=f"正在处理 {series_name} 第 {season_num} 季...",
            )

            logger.info(
                "📺 [BatchEnrich] 获取季数据: %s S%02d (进度 %d/%d)",
                series_name, season_num, idx, len(season_numbers),
            )

            # ---- Step 1: 调用 TMDB 整季接口 ----
            try:
                resp = _requests.get(
                    f"{base_url}/tv/{tmdb_id}/season/{season_num}",
                    params={
                        "api_key": api_key,
                        "language": "zh-CN",
                        "append_to_response": "credits",
                    },
                    timeout=30,
                )
                if resp.status_code != 200:
                    logger.warning(
                        "   ⚠ [BatchEnrich] TMDB S%02d HTTP %d: %s",
                        season_num, resp.status_code,
                        resp.text[:200] if resp.text else "",
                    )
                    task_manager.update_progress(
                        task_id,
                        current=idx,
                        message=f"第 {season_num} 季获取失败 (HTTP {resp.status_code})，继续下一季",
                    )
                    db.commit()
                    continue

                season_data = resp.json()
            except Exception:
                logger.error(
                    "   ❌ [BatchEnrich] TMDB S%02d 请求异常:\n%s",
                    season_num, traceback.format_exc(),
                )
                task_manager.update_progress(
                    task_id, current=idx,
                    message=f"第 {season_num} 季网络异常，继续下一季",
                )
                db.commit()
                continue

            episodes = season_data.get("episodes", [])
            if not episodes:
                logger.info(
                    "   ℹ️ [BatchEnrich] S%02d 无分集数据", season_num,
                )
                task_manager.update_progress(
                    task_id, current=idx,
                    message=f"第 {season_num} 季无分集数据",
                )
                db.commit()
                continue

            # ---- Step 2: 收集本季所有 guest_stars（跨集去重） ----
            season_guest_stars: dict[str, dict] = {}  # {name: person_dict}
            ep_updates: list[dict] = []  # [{ep_number, overview, guest_names}]

            for ep in episodes:
                ep_number = ep.get("episode_number")
                if ep_number is None:
                    continue

                overview = (ep.get("overview") or "").strip()
                guest_stars = ep.get("guest_stars", []) or []

                guest_names = []
                for gs in guest_stars:
                    gs_name = (gs.get("name") or "").strip()
                    if gs_name and gs_name not in season_guest_stars:
                        # 构造与 Emby People 兼容的 dict 格式
                        season_guest_stars[gs_name] = {
                            "Name": gs_name,
                            "Type": "GuestStar",
                            "Role": (gs.get("character") or "").strip(),
                            "Tmdb": str(gs.get("id", "")),
                            "DoubanAvatarUrl": "",
                            "DoubanCelebrityId": "",
                            "ProviderIds": {
                                "Tmdb": str(gs.get("id", "")),
                            },
                        }
                    guest_names.append(gs_name)

                ep_updates.append({
                    "episode_number": ep_number,
                    "overview": overview,
                    "guest_names": guest_names,
                    "guest_stars_raw": [
                        gs for gs in guest_stars
                        if (gs.get("name") or "").strip() in guest_names
                    ],
                })

            # ---- Step 3: 批量走本地漏斗 — 一次处理所有 guest_stars ----
            if season_guest_stars:
                logger.info(
                    "   👥 [BatchEnrich] S%02d 共 %d 位客串演员，批量走 L0-L2 漏斗",
                    season_num, len(season_guest_stars),
                )
                try:
                    people_list = list(season_guest_stars.values())
                    ensure_profiles_for_people(db, people_list)
                    db.flush()
                    total_guest_stars += len(people_list)
                except Exception:
                    logger.error(
                        "   ❌ [BatchEnrich] S%02d guest_stars 漏斗处理异常:\n%s",
                        season_num, traceback.format_exc(),
                    )
                    db.rollback()

            # ---- Step 4: 更新 media_metadata.overview + 写入 actor_records ----
            for ep_update in ep_updates:
                ep_number = ep_update["episode_number"]
                overview = ep_update["overview"]
                guest_names = ep_update["guest_names"]

                # 查找对应的本地 Episode 记录
                ep_records = db.query(MediaMetadata).filter(
                    MediaMetadata.parent_id == item_id,
                    MediaMetadata.parent_index_number == season_num,
                    MediaMetadata.index_number == ep_number,
                ).all()

                for ep_rec in ep_records:
                    # ★ 防覆盖守卫：AI 已汉化简介禁止被 TMDB 非中文新值覆盖，仅真正写入才置 update_time
                    if overview and apply_overview_with_guard(ep_rec, overview):
                        ep_rec.update_time = datetime.now()

                    # 写入客串演员到 actor_records（仅当该 Episode 尚无记录时）
                    if guest_names and ep_rec.emby_item_id:
                        existing_actors = db.query(ActorRecord).filter(
                            ActorRecord.emby_item_id == ep_rec.emby_item_id,
                            ActorRecord.type == "GuestStar",
                        ).count()
                        if existing_actors == 0:
                            for gs_name in guest_names:
                                gs_info = season_guest_stars.get(gs_name, {})
                                db.add(ActorRecord(
                                    emby_item_id=ep_rec.emby_item_id,
                                    name=gs_name,
                                    role=gs_info.get("Role", ""),
                                    type="GuestStar",
                                    sort_order=0,
                                ))

                total_enriched_eps += 1

            db.commit()

            task_manager.update_progress(
                task_id,
                current=idx,
                message=f"已完成 {series_name} 第 {season_num} 季 ({len(episodes)} 集)",
            )
            logger.info(
                "   ✅ [BatchEnrich] S%02d 完成: %d 集, %d 位客串演员",
                season_num, len(episodes),
                len(season_guest_stars),
            )

        # ---- 全部完成 ----
        _enrich_success = True
        _enrich_final_msg = (
            f"✅ 完成: {series_name} | "
            f"{len(season_numbers)} 季 / {total_enriched_eps} 集 | "
            f"客串演员 {total_guest_stars} 位"
        )

    except Exception:
        logger.error(
            "❌ [BatchEnrich] 批量分集任务异常:\n%s",
            traceback.format_exc(),
        )
        db.rollback()
        # ★ sentinel 保持 False，finally 块会以 error 状态调用 complete_task
    finally:
        db.close()
        # ★ 无论如何都会执行，确保任务状态被终结
        task_manager.complete_task(
            task_id, _enrich_final_msg, success=_enrich_success,
        )


# ==========================================
# 接口: POST /api/audit/batch
# ★ 统一审计入口 — 后台异步执行 + 整季 TMDB 批处理
# ==========================================

class BatchAuditRequest(BaseModel):
    """统一审计请求 — 支持按 item_ids 或 library_id 两种模式。

    - item_ids 模式（审计选中项）：前端传入选中的 ID 列表
    - library_id 模式（审计本地汉化状态）：后端自动拉取库内全部 ID
    """
    item_ids: list[str] = []
    library_id: str = ""


@router.post("/audit/batch")
def batch_audit(req: BatchAuditRequest, background_tasks: BackgroundTasks):
    """统一审计入口：后台批量执行，立即返回 task_id 供前端轮询。

    对 Series 使用 TMDB Season API 整季批处理，彻底杜绝逐集循环查询。

    请求体:
        {"item_ids": ["id1", "id2"], "library_id": ""}   // 审计选中项
        {"item_ids": [], "library_id": "1875208"}          // 审计全量汉化状态

    返回:
        200: {"task_id": "abc123", "message": "审计任务已启动，共 50 项"}
        400: 配置缺失 / 参数为空
    """
    cfg = load_config()
    host = cfg.get("emby_host", "").rstrip("/")
    api_key = cfg.get("emby_api_key", "")
    user_id = cfg.get("emby_user_id", "")

    if not host or not api_key:
        raise HTTPException(status_code=400, detail="缺少 Emby 配置")

    if not req.item_ids and not req.library_id:
        raise HTTPException(status_code=400, detail="item_ids 和 library_id 至少提供一个")

    task_id = task_manager.create_task(
        total=0,  # 将在 _batch_audit_task 中动态计算
        message="正在准备审计...",
        metadata={
            "mode": "selected" if req.item_ids else "library",
            "library_id": req.library_id,
            "item_count": len(req.item_ids) if req.item_ids else 0,
        },
    )

    background_tasks.add_task(
        _batch_audit_task,
        task_id=task_id,
        item_ids=req.item_ids,
        library_id=req.library_id,
        host=host,
        api_key=api_key,
        user_id=user_id,
    )

    logger.info(
        "🚀 [BatchAudit] 后台任务已提交: task=%s mode=%s ids=%d",
        task_id,
        "selected" if req.item_ids else "library",
        len(req.item_ids) if req.item_ids else 0,
    )
    return {
        "task_id": task_id,
        "message": f"审计任务已启动"
        + (f"，共 {len(req.item_ids)} 项" if req.item_ids else "（全库扫描）"),
    }


# ==========================================
# 后台任务: 统一批量审计引擎
# ==========================================

def _fetch_library_item_ids(host: str, api_key: str, user_id: str,
                             library_id: str) -> list[str]:
    """从 Emby 库中获取所有顶层媒体项的 ID 列表（轻量，仅 ID）。

    用于 library_id 模式的审计：先拿到全部 ID，再按批次处理。
    """
    base = f"{host}/emby/Users/{user_id}/Items" if user_id else f"{host}/emby/Items"
    all_ids = []
    start_index = 0
    page_size = 200  # ID 列表很轻量，可以一次多拿

    while True:
        params = {
            "api_key": api_key,
            "ParentId": library_id,
            "IncludeItemTypes": "Series,Movie",
            "Recursive": "true",
            "Fields": "",  # ★ 仅需 ID，不要任何额外字段
            "StartIndex": start_index,
            "Limit": page_size,
        }
        try:
            resp = _requests.get(base, params=params, timeout=30)
            if resp.status_code != 200:
                logger.error(
                    "❌ [BatchAudit] 获取库 ID 列表失败 HTTP %d (start=%d)",
                    resp.status_code, start_index,
                )
                break
            data = resp.json()
            items = data.get("Items", [])
            if not items:
                break
            all_ids.extend(item.get("Id", "") for item in items if item.get("Id"))
            total = data.get("TotalRecordCount", 0)
            if start_index + page_size >= total:
                break
            start_index += page_size
        except Exception:
            logger.error(
                "❌ [BatchAudit] 获取库 ID 列表异常:\n%s",
                traceback.format_exc(),
            )
            break

    logger.info(
        "📋 [BatchAudit] 库 %s 共发现 %d 个顶层媒体项",
        library_id, len(all_ids),
    )
    return all_ids


def _fetch_episodes_light(host: str, api_key: str, user_id: str,
                          series_id: str) -> list:
    """轻量级分集发现 — 仅获取 ID/编号/标题，零演员/ProviderIds 开销。

    专为 Series 审计设计的极简 Emby 调用：
    - 不请求 People / ProviderIds / Overview 等大字段
    - 仅拿到 Emby Episode ID + 集号 + 季号，用于创建 MediaMetadata 锚点
    - 后续所有富化数据（简介、客串演员）由 TMDB Season API 提供

    契约：返回的每项 dict 保证至少含 `Type`（Episode）。Emby 分集接口通常自带
    Type，但缺省时不补齐会把 media_type 存成空串（save_media_to_db 用
    emby_item.get("Type", "")），导致按 media_type=="Episode" 查询漏掉刚入库
    分集。作为数据源头适配器，在此统一补齐，避免给下游调用方埋雷。
    """
    base = f"{host}/emby/Users/{user_id}/Items" if user_id else f"{host}/emby/Items"
    all_eps = []
    start_index = 0
    page_size = 100

    while True:
        params = {
            "api_key": api_key,
            "ParentId": series_id,
            "IncludeItemTypes": "Episode",
            "Recursive": "true",
            "Fields": "ParentIndexNumber,IndexNumber",  # ★ 仅季号+集号
            "StartIndex": start_index,
            "Limit": page_size,
        }
        try:
            resp = _requests.get(base, params=params, timeout=30)
            if resp.status_code != 200:
                logger.warning(
                    "   ⚠ [BatchAudit] 获取 %s 分集列表失败: HTTP %d",
                    series_id, resp.status_code,
                )
                break
            data = resp.json()
            items = data.get("Items", [])
            if not items:
                break
            all_eps.extend(items)
            total = data.get("TotalRecordCount", 0)
            if start_index + page_size >= total:
                break
            start_index += page_size
        except Exception:
            logger.warning(
                "   ⚠ [BatchAudit] 获取 %s 分集列表异常:\n%s",
                series_id, traceback.format_exc(),
            )
            break

    # ★ 数据源头适配器契约：返回的轻量 dict 必须至少含 Type 字段（见 docstring）
    for ep in all_eps:
        ep.setdefault("Type", "Episode")

    return all_eps


def _compute_episode_diff(
    db_episodes: list, emby_episodes: list,
) -> dict:
    """对比 DB 与 Emby 的分集集合，检测新增集与内部空集缺口。

    缺口语义：Emby 有而 DB 没有的分集（missing）；其中"该季存在更高已入库集号"
    的分集视为内部空集（interior_gaps，中间空洞）。尾部新增集（全部高于该季
    DB 最大集号）不是内部空集，不触发整体汉化，仅补库。

    Args:
        db_episodes:   DB 已有分集的 [(season, episode)] 元组列表
        emby_episodes: Emby 实际分集的 [(season, episode)] 元组列表

    Returns:
        {"missing": [(s,e), ...], "interior_gaps": [(s,e), ...]}（均升序）
    """
    db_set = set(db_episodes)
    emby_set = set(emby_episodes)
    missing = sorted(emby_set - db_set)

    # 每季 DB 已存在的最大集号（用于判定中间空洞）
    db_max: dict[int, int] = {}
    for season, ep in db_set:
        db_max[season] = max(db_max.get(season, 0), ep)

    interior = sorted(
        (s, e) for (s, e) in missing if e < db_max.get(s, -1)
    )
    return {"missing": missing, "interior_gaps": interior}


def reconcile_series_episodes(
    series_id: str, host: str = "", api_key: str = "",
    user_id: str = "", library_id: str = "",
) -> dict:
    """轻量对账 Series 分集：拉 Emby 列表 → 对比 DB → 补库 → 刷新计数。

    供 webhook（新增分集）调用，解决「实际 12 集只入库 7 集」。
    - 只拉轻量字段（_fetch_episodes_light），避免重复抓取大字段
    - 内部空集缺口 → 全量同步一次该剧（_process_episodes 全量）
    - 仅尾部新增 → 只补缺失分集，不重扫已入库分集
    - 无论哪种，均用 Emby 实际分集数刷新父 Series recursive_item_count

    Returns:
        {"success": bool, "episodes_total": int, "synced_episodes": int,
         "interior_gaps": list, "full_sync": bool}
    """
    cfg = load_config()
    host = host or cfg.get("emby_host", "").rstrip("/")
    api_key = api_key or cfg.get("emby_api_key", "")
    user_id = user_id or cfg.get("emby_user_id", "")

    empty = {"success": False, "episodes_total": 0, "synced_episodes": 0,
             "interior_gaps": [], "full_sync": False}
    if not host or not api_key:
        logger.warning("⚠ [Reconcile] Emby 未配置，跳过 %s 对账", series_id)
        return empty

    emby_eps = _fetch_episodes_light(host, api_key, user_id, series_id)
    if not emby_eps:
        logger.warning("⚠ [Reconcile] %s 无分集数据（Emby 未找到或为空）", series_id)
        return empty

    db = SessionLocal()
    try:
        db_eps = [
            (r.parent_index_number or 0, r.index_number or 0)
            for r in db.query(MediaMetadata).filter(
                MediaMetadata.parent_id == series_id,
                MediaMetadata.media_type == "Episode",
            ).all()
        ]
        diff = _compute_episode_diff(
            db_eps,
            [(ep.get("ParentIndexNumber") or 0, ep.get("IndexNumber") or 0) for ep in emby_eps],
        )

        # ★ 补库：内部空集 → 全量同步一次；仅尾部新增 → 只补缺失分集
        if diff["interior_gaps"]:
            synced = _process_episodes(
                db, emby_eps, series_id, library_id,
                apply_localization=False, douban_actor_map=None, series_name="",
            )
            logger.info(
                "   📺 [Reconcile] %s 检测到内部空集 %s，全量同步 %d 个分集",
                series_id, diff["interior_gaps"], synced,
            )
        elif diff["missing"]:
            missing_eps = [
                ep for ep in emby_eps
                if (ep.get("ParentIndexNumber") or 0, ep.get("IndexNumber") or 0) in diff["missing"]
            ]
            synced = _process_episodes(
                db, missing_eps, series_id, library_id,
                apply_localization=False, douban_actor_map=None, series_name="",
            )
            logger.info(
                "   📺 [Reconcile] %s 补充 %d 个新增分集", series_id, synced,
            )
        else:
            synced = 0

        # ★ 用 Emby 实际分集数刷新父 Series 计数（不信任 stale RecursiveItemCount）
        series_mm = db.query(MediaMetadata).filter(
            MediaMetadata.emby_item_id == series_id
        ).first()
        if series_mm:
            series_mm.recursive_item_count = len(emby_eps)
        db.commit()

        return {
            "success": True,
            "episodes_total": len(emby_eps),
            "synced_episodes": synced,
            "interior_gaps": diff["interior_gaps"],
            "full_sync": bool(diff["interior_gaps"]),
        }
    except Exception:
        db.rollback()
        logger.error(
            "❌ [Reconcile] %s 对账异常:\n%s", series_id, traceback.format_exc(),
        )
        return empty
    finally:
        db.close()


def _build_batch_audit_summary(
    total_scanned: int, total_synced: int,
    n_series: int, n_seasons: int,
    total_eps_actual: int, total_eps_enriched: int,
    total_guest_stars: int,
) -> str:
    """BatchAudit 最终摘要 — 分集数以【实际 Emby 入库数】为准，TMDB 数作括号参考。

    修复：原摘要用 TMDB 整季 episodes 数（含未播出）冒充实际数（30 vs 12）。
    """
    eps_part = f"分集 {total_eps_actual} 集"
    if total_eps_enriched != total_eps_actual:
        eps_part += f"（TMDB {total_eps_enriched}）"
    return (
        f"✅ 审计完成: {total_scanned} 项 | 已汉化 {total_synced} 项 | "
        f"{n_series} 部剧集 / {n_seasons} 季 | {eps_part} | "
        f"客串演员 {total_guest_stars} 位"
    )


def _fetch_tmdb_seasons(tmdb_base: str, api_key: str, tmdb_id: str) -> list[int]:
    """通过 TMDB 获取剧集的所有季号列表。

    带 3 次网络重试：Timeout/ConnectionError 退避 2s 后重试；
    HTTP 404 立即放弃（说明该 ID 非剧集，可能是电影 ID 误填）。

    Returns:
        季号列表 (如 [1, 2, 3])，失败返回 [1] 兜底
    """
    MAX_RETRIES = 3
    RETRY_DELAY = 2.0

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = _requests.get(
                f"{tmdb_base}/tv/{tmdb_id}",
                params={"api_key": api_key, "language": "zh-CN"},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                seasons = [
                    s["season_number"]
                    for s in data.get("seasons", [])
                    if s.get("season_number", 0) > 0  # 排除特辑 (season 0)
                ]
                if seasons:
                    return sorted(seasons)
                # 200 但没有季数据（罕见），直接返回兜底值
                logger.info(
                    "   ⏭️ [TMDB] 季列表为空 (无 season_number>0): %s", tmdb_id,
                )
                return [1]

            # ---- HTTP 非 200 状态码处理 ----
            if resp.status_code == 404:
                logger.info(
                    "   ⏭️ [TMDB] 季列表 404 不存在，跳过查询 (可能非剧集ID): %s",
                    tmdb_id,
                )
                return [1]  # 不重试，直接兜底

            # 其他 HTTP 错误（429/500/502/503 等）— 视作网络异常重试
            logger.warning(
                "   ⚠ [BatchAudit] 获取 TMDB %s 季列表 HTTP %d (第 %d/%d 次尝试)",
                tmdb_id, resp.status_code, attempt, MAX_RETRIES,
            )
            if attempt < MAX_RETRIES:
                _time.sleep(RETRY_DELAY)

        except (_requests.exceptions.Timeout, _requests.exceptions.ConnectionError) as e:
            logger.warning(
                "   ⚠ [BatchAudit] 获取 TMDB %s 季列表网络异常 (%s) — 第 %d/%d 次尝试",
                tmdb_id, type(e).__name__, attempt, MAX_RETRIES,
            )
            if attempt < MAX_RETRIES:
                _time.sleep(RETRY_DELAY)

        except Exception as e:
            # 非网络异常（JSON 解析失败等），不重试，直接暴露真实死因
            logger.warning(
                "   ⚠ [BatchAudit] 获取 TMDB %s 季列表异常: %s",
                tmdb_id, str(e),
            )
            break

    return [1]  # 兜底：至少尝试第 1 季


def _batch_audit_task(
    task_id: str,
    item_ids: list[str],
    library_id: str,
    host: str,
    api_key: str,
    user_id: str,
):
    """★ 统一批量审计后台引擎。

    核心原则：
    1. 【绝对禁止】进入旧的逐集循环查询（_process_episodes / _fetch_episodes 全量版）
    2. Series 分集数据全部通过 TMDB Season API 整季获取
    3. 所有演员画像走本地 L0-L2 超级漏斗批量处理
    4. 进度按"季"为单位推进，前端轮询实时反馈

    流程概览：
    Phase 0 — 收集 ID 列表（library_id 模式下从 Emby 拉取）
    Phase 1 — 逐项状态检查 + UPSERT 入库 + 发现季数
    Phase 2 — 按季 TMDB 批处理（guest_stars 去重 + ensure_profiles + 批量写入）
    """
    cfg = load_config()
    tmdb_api_key = cfg.get("tmdb_api_key", "")
    tmdb_base = cfg.get("tmdb_base_url", "") or "https://api.tmdb.org/3"
    max_actors = cfg.get("max_actors_per_media", 50)

    # ★ sentinel 变量：finally 块统一调用 complete_task，
    #    无论成功、失败、还是早期 return，都能保证任务被正确终结
    _batch_audit_success = False
    _batch_audit_final_msg = "❌ 审计任务失败，请查看服务端日志"
    db = None  # ★ Phase 2 DB 会话，finally 块安全关闭

    # ---- Phase 0: 收集 ID 列表 ----
    if library_id and not item_ids:
        task_manager.update_progress(
            task_id, current=0,
            message="正在从 Emby 获取媒体库 ID 列表...",
        )
        item_ids = _fetch_library_item_ids(host, api_key, user_id, library_id)
        if not item_ids:
            # ★ sentinel: finally 块统一调用 complete_task
            _batch_audit_success = True
            _batch_audit_final_msg = "⚠ 媒体库中没有发现任何媒体项"
            return

    total_items = len(item_ids)
    task_manager.update_progress(
        task_id, total=total_items, current=0,
        message=f"开始审计 {total_items} 个媒体项...",
    )

    # ---- Phase 1: 逐项统一审计（通过 _sync_and_audit_single_item） ----
    series_queue: list[dict] = []  # [{item_id, tmdb_id, name}]
    total_synced = 0
    total_scanned = 0

    try:
        for idx, item_id in enumerate(item_ids):
            try:
                # ★ 统一调用公共入口 — 自动处理"Emby 拉取 → 对账 → 落盘 → 分集刮削"
                result = _sync_and_audit_single_item(item_id, library_id=library_id)
                total_scanned += 1

                if result["success"]:
                    if result["synced"]:
                        total_synced += 1
                        # 已汉化 Series → 收集到队列，Phase 2 做 TMDB 富化
                        if result["item_type"] == "Series" and result["tmdb_id"]:
                            series_queue.append({
                                "item_id": item_id,
                                "tmdb_id": result["tmdb_id"],
                                "name": result["item_name"],
                                # ★ 实际 Emby 分集数（Phase 1 分集已入库）
                                "episodes_actual": result.get("episodes_processed", 0),
                            })
                else:
                    logger.warning(
                        "   ⚠ [BatchAudit] %s 审计失败: %s",
                        item_id, result.get("error", "未知错误"),
                    )

            except Exception:
                logger.warning(
                    "   ⚠ [BatchAudit] %s 审计异常:\n%s",
                    item_id, traceback.format_exc(),
                )

            # ★ 逐项更新进度（而非等整批完成），确保前端进度条实时递增
            task_manager.update_progress(
                task_id, current=idx + 1,
                message=f"已审计 {idx + 1}/{total_items} 项"
                + (f"，已汉化 {total_synced}" if total_synced else ""),
            )

        if not series_queue:
            _batch_audit_success = True
            _batch_audit_final_msg = (
                f"✅ 审计完成: 共 {total_scanned} 项，已汉化 {total_synced} 项"
            )
            return

        # ---- Phase 2: 整季 TMDB 批处理 ----
        # ★ Phase 1 已通过 _audit_and_save_single_item 完成 Emby 分集抓取入库，
        #   此处仅做 TMDB 数据富化（简介 + 客串演员），不再重复创建分集锚点。
        series_with_seasons: list[dict] = []
        grand_total_seasons = 0

        task_manager.update_progress(
            task_id, current=0,
            message=f"正在分析 {len(series_queue)} 部剧集的季数结构...",
        )

        for sq in series_queue:
            seasons = _fetch_tmdb_seasons(tmdb_base, tmdb_api_key, sq["tmdb_id"])
            sq["seasons"] = seasons
            series_with_seasons.append(sq)
            grand_total_seasons += len(seasons)

        # ★ 将 total 调整为实际季数总和，进度更精确
        task_manager.update_progress(
            task_id, total=grand_total_seasons, current=0,
            message=f"开始处理 {len(series_with_seasons)} 部剧集（共 {grand_total_seasons} 季）...",
        )

        season_processed = 0
        total_guest_stars_all = 0
        total_eps_enriched = 0
        total_eps_actual = sum(
            sq.get("episodes_actual", 0) for sq in series_with_seasons
        )

        # ★ Phase 2 使用独立的 DB 会话（Phase 1 各调用已自行管理会话生命周期）
        db = SessionLocal()
        for sq in series_with_seasons:
            tmdb_id = sq["tmdb_id"]
            item_name = sq["name"]
            item_id = sq["item_id"]
            seasons = sq["seasons"]

            # ---- 逐季 TMDB 批处理 ----
            for season_num in seasons:
                try:
                    # ① 获取 TMDB 整季数据
                    tmdb_resp = _requests.get(
                        f"{tmdb_base}/tv/{tmdb_id}/season/{season_num}",
                        params={
                            "api_key": tmdb_api_key,
                            "language": "zh-CN",
                            "append_to_response": "credits",
                        },
                        timeout=30,
                    )
                    if tmdb_resp.status_code != 200:
                        logger.warning(
                            "   ⚠ [BatchAudit] 《%s》S%02d TMDB HTTP %d",
                            item_name, season_num, tmdb_resp.status_code,
                        )
                        season_processed += 1
                        task_manager.update_progress(
                            task_id, current=season_processed,
                            message=f"已处理《{item_name}》第 {season_num} 季 ({season_processed}/{grand_total_seasons})",
                        )
                        continue

                    season_data = tmdb_resp.json()
                    episodes = season_data.get("episodes", [])
                    if not episodes:
                        logger.info(
                            "   ℹ️ [BatchAudit] 《%s》S%02d 无分集数据",
                            item_name, season_num,
                        )
                        season_processed += 1
                        task_manager.update_progress(
                            task_id, current=season_processed,
                            message=f"已处理《{item_name}》第 {season_num} 季 ({season_processed}/{grand_total_seasons})",
                        )
                        continue

                    # ② 收集 + 去重 GuestStars（跨集合并，按 Name 为 key）
                    all_guest_stars: dict[str, dict] = {}
                    ep_guest_map: dict[int, list[str]] = {}  # {ep_number: [guest_names]}

                    for ep in episodes:
                        ep_num = ep.get("episode_number")
                        if ep_num is None:
                            continue

                        guest_names: list[str] = []
                        for gs in ep.get("guest_stars", []) or []:
                            gs_name = (gs.get("name") or "").strip()
                            if not gs_name:
                                continue
                            if gs_name not in all_guest_stars:
                                all_guest_stars[gs_name] = {
                                    "Name": gs_name,
                                    "Type": "GuestStar",
                                    "Role": (gs.get("character") or "").strip(),
                                    "DoubanAvatarUrl": "",
                                    "ProviderIds": {
                                        "Tmdb": str(gs.get("id", "")),
                                    },
                                }
                            guest_names.append(gs_name)
                        ep_guest_map[ep_num] = guest_names

                    # ③ 批量走本地 L0-L2 超级漏斗
                    if all_guest_stars:
                        logger.info(
                            "   👥 [BatchAudit] 《%s》S%02d 共 %d 位客串演员 → 漏斗",
                            item_name, season_num, len(all_guest_stars),
                        )
                        try:
                            people_list = list(all_guest_stars.values())
                            ensure_profiles_for_people(db, people_list)
                            db.flush()
                            total_guest_stars_all += len(people_list)
                        except Exception:
                            logger.error(
                                "   ❌ [BatchAudit] 《%s》S%02d 漏斗异常:\n%s",
                                item_name, season_num, traceback.format_exc(),
                            )
                            db.rollback()

                    # ④ 批量更新 Episode Overview + 写入 GuestStar ActorRecords
                    for ep in episodes:
                        ep_num = ep.get("episode_number")
                        if ep_num is None:
                            continue

                        overview = (ep.get("overview") or "").strip()
                        guest_names = ep_guest_map.get(ep_num, [])

                        # 按 (parent_id, season, episode) 定位 MediaMetadata 记录
                        ep_recs = db.query(MediaMetadata).filter(
                            MediaMetadata.parent_id == item_id,
                            MediaMetadata.parent_index_number == season_num,
                            MediaMetadata.index_number == ep_num,
                        ).all()

                        for ep_rec in ep_recs:
                            # ★ 防覆盖守卫：AI 已汉化简介禁止被 TMDB 非中文新值覆盖，仅真正写入才置 update_time
                            if overview and apply_overview_with_guard(ep_rec, overview):
                                ep_rec.update_time = datetime.now()

                            # 写入客串演员关联（仅当尚无记录时）
                            if guest_names and ep_rec.emby_item_id:
                                existing_count = db.query(ActorRecord).filter(
                                    ActorRecord.emby_item_id == ep_rec.emby_item_id,
                                ).count()
                                if existing_count == 0:
                                    for sort_idx, gs_name in enumerate(guest_names):
                                        gs_info = all_guest_stars.get(gs_name, {})
                                        db.add(ActorRecord(
                                            emby_item_id=ep_rec.emby_item_id,
                                            name=gs_name,
                                            role=gs_info.get("Role", ""),
                                            type="GuestStar",
                                            sort_order=sort_idx,
                                        ))

                        total_eps_enriched += 1

                    db.commit()
                    actual_season_eps = db.query(MediaMetadata).filter(
                        MediaMetadata.parent_id == item_id,
                        MediaMetadata.parent_index_number == season_num,
                        MediaMetadata.media_type == "Episode",
                    ).count()
                    logger.info(
                        "   ✅ [BatchAudit] 《%s》S%02d 完成: %d 集 (TMDB %d), %d 位客串",
                        item_name, season_num, actual_season_eps,
                        len(episodes), len(all_guest_stars),
                    )

                except Exception:
                    logger.error(
                        "   ❌ [BatchAudit] 《%s》S%02d 处理异常:\n%s",
                        item_name, season_num, traceback.format_exc(),
                    )
                    db.rollback()

                season_processed += 1
                # ★ 处理完一季后立即更新进度，确保前端进度条实时递增
                task_manager.update_progress(
                    task_id, current=season_processed,
                    message=f"已处理《{item_name}》第 {season_num} 季 ({season_processed}/{grand_total_seasons})",
                )

            # 每部剧集完成后的进度消息
            task_manager.update_progress(
                task_id, current=season_processed,
                message=f"已完成《{item_name}》（{len(seasons)} 季）",
            )

        # ---- 全部完成 ----
        _batch_audit_success = True
        _batch_audit_final_msg = _build_batch_audit_summary(
            total_scanned=total_scanned,
            total_synced=total_synced,
            n_series=len(series_with_seasons),
            n_seasons=grand_total_seasons,
            total_eps_actual=total_eps_actual,
            total_eps_enriched=total_eps_enriched,
            total_guest_stars=total_guest_stars_all,
        )
        logger.info(
            "✅ [BatchAudit] 任务 %s 全部完成: scanned=%d synced=%d series=%d seasons=%d eps=%d guests=%d",
            task_id, total_scanned, total_synced,
            len(series_with_seasons), grand_total_seasons,
            total_eps_actual, total_guest_stars_all,
        )

    except Exception:
        logger.error(
            "❌ [BatchAudit] 任务 %s 致命异常:\n%s",
            task_id, traceback.format_exc(),
        )
        if db:
            try:
                db.rollback()
            except Exception:
                pass
        # ★ sentinel 保持 False，finally 块会以 error 状态调用 complete_task
    finally:
        if db:
            try:
                db.close()
            except Exception:
                pass
        # ★ 无论如何都会执行，确保任务状态被终结
        task_manager.complete_task(
            task_id, _batch_audit_final_msg, success=_batch_audit_success,
        )


# ==========================================
# ★ 前置审计辅助：确保媒体项在本地有演员数据，无则自动从 Emby 拉取
# ==========================================

def _ensure_item_audited(item_id: str) -> bool:
    """确保媒体项在本地 actor_records 中有演员数据，无则自动触发审计。

    这是批量汉化的前置步骤 — 解决"必须先手动审计再汉化"的割裂体验：
    - 如果 actor_records 已有数据 → 直接返回 True
    - 如果 actor_records 为空 → 自动从 Emby 拉取 Item 详情并执行审计入库
    - 审计后仍无数据（Emby 未刮削元数据） → 返回 False，上层跳过

    Args:
        item_id: Emby Item ID

    Returns:
        True:  本地已有演员数据，或审计成功拉取到数据
        False: Emby 中该 Item 确实没有演员，应跳过汉化
    """
    db = SessionLocal()
    try:
        # ★ 快速路径：本地已有演员数据
        actor_count = db.query(ActorRecord).filter(
            ActorRecord.emby_item_id == item_id
        ).count()
        if actor_count > 0:
            return True
    finally:
        db.close()

    logger.info("🔍 [PreAudit] item=%s actor_records 为空，通过统一入口触发审计...", item_id)

    # ★ 调用统一审计入口 — 自动处理 Emby 拉取 → 对账 → 落盘 → 刮削
    result = _sync_and_audit_single_item(item_id, library_id="")

    if not result["success"]:
        # Emby 中不存在或网络异常 → 不阻塞，让 sinicize 自行处理
        logger.warning(
            "⚠ [PreAudit] %s 审计未成功: %s（不阻塞汉化流程）",
            item_id, result.get("error", "未知错误"),
        )
        return True

    # ★ 再次检查 actor_records（审计后应有数据）
    db2 = SessionLocal()
    try:
        actor_count = db2.query(ActorRecord).filter(
            ActorRecord.emby_item_id == item_id
        ).count()
        if actor_count == 0:
            logger.info(
                "ℹ️ [PreAudit] %s 审计后仍无演员数据（Emby 未刮削元数据），跳过汉化",
                result["item_name"],
            )
            return False

        logger.info(
            "✅ [PreAudit] %s 审计完成，actor_records=%d 条",
            result["item_name"], actor_count,
        )
        return True
    finally:
        db2.close()


# ==========================================
# ★ 统一批量汉化后台引擎
# ==========================================

def _batch_sinicize_task(
    task_id: str,
    item_ids: list[str],
):
    """后台任务核心逻辑 — 逐项调用 DoubanSinizer.sinicize()。

    每个 item 独立处理（含系列剧集递归），进度按 item 粒度推进。
    ★ 防弹级 try/except/finally 全局防线：无论任何原因崩溃，保证任务被终结。

    Args:
        task_id:  任务 ID（用于进度上报）
        item_ids: 待汉化的 Emby Item ID 列表
    """
    # ★ sentinel 变量：finally 块统一调用 complete_task，
    #    无论成功、失败、还是未捕获异常，都能保证任务被正确终结
    _sinicize_success = False
    _sinicize_final_msg = "❌ 批量汉化失败，请查看服务端日志"
    total_done = 0
    total_failed = 0

    try:
        sinizer = DoubanSinizer()

        for idx, item_id in enumerate(item_ids):
            current = idx + 1
            task_manager.update_progress(
                task_id, current=current - 1,
                message=f"正在汉化 {current}/{len(item_ids)}...",
            )

            try:
                # ★ 前置审计：确保本地有演员数据，无则自动从 Emby 拉取
                if not _ensure_item_audited(item_id):
                    total_failed += 1
                    logger.warning(
                        "⚠ [BatchSinicize] %s 无演员数据（Emby 未刮削元数据），跳过汉化",
                        item_id,
                    )
                    task_manager.update_progress(
                        task_id, current=current,
                        message=f"已完成 {current}/{len(item_ids)}（成功 {total_done}，失败 {total_failed}）",
                    )
                    continue

                # ★ 传入 task_id，使内部分集循环能做颗粒度进度反馈
                result = sinizer.sinicize(item_id, task_id=task_id)
                if result.get("success"):
                    total_done += 1
                    logger.info(
                        "✅ [BatchSinicize] %s 完成: matched=%d/%d",
                        item_id, result.get("matched", 0), result.get("total_actors", 0),
                    )
                else:
                    total_failed += 1
                    logger.warning(
                        "⚠ [BatchSinicize] %s 失败", item_id,
                    )
            except Exception:
                total_failed += 1
                logger.error(
                    "❌ [BatchSinicize] %s 异常:\n%s",
                    item_id, traceback.format_exc(),
                )

            task_manager.update_progress(
                task_id, current=current,
                message=f"已完成 {current}/{len(item_ids)}（成功 {total_done}，失败 {total_failed}）",
            )

        _sinicize_success = True
        _sinicize_final_msg = (
            f"✅ 批量汉化完成: {len(item_ids)} 项 | "
            f"成功 {total_done} | 失败 {total_failed}"
        )

    except Exception as e:
        # ★★★ 终极防线：捕获任何穿透循环的未预期异常 ★★★
        # 包括 DoubanSinizer 构造函数失败、迭代器异常、内存溢出等极端情况
        logger.error(
            "❌ [BatchSinicize] 批量汉化任务崩溃 (task=%s):\n%s",
            task_id, traceback.format_exc(),
        )
        _sinicize_final_msg = f"❌ 任务崩溃: {str(e)[:200]}"
        # ★ 先将状态置为 error，前端立刻感知异常
        try:
            task_manager.update_progress(
                task_id,
                status="error",
                message=_sinicize_final_msg,
            )
        except Exception:
            pass
    finally:
        # ★★★ 无论如何强制终结任务，前端轮询永远不会陷入死锁 ★★★
        try:
            task_manager.complete_task(
                task_id, _sinicize_final_msg, success=_sinicize_success,
            )
        except Exception:
            # 极端情况：complete_task 自身异常也确保任务被终结
            try:
                task_manager.complete_task(
                    task_id, "❌ 批量汉化异常终止", success=False,
                )
            except Exception:
                logger.error(
                    "❌ [BatchSinicize] complete_task 重复失败，任务 %s 可能悬挂",
                    task_id,
                )


# ==========================================
# 接口: POST /api/douban/sinicize_selected
# 同步选中项 → 后台异步执行 + task_id 轮询
# ==========================================

@router.post("/douban/sinicize_selected")
def sinicize_selected(req: SinicizeSelectedRequest, background_tasks: BackgroundTasks):
    """对选中的媒体项批量执行演员中文化（后台异步，立即返回 task_id）。

    请求体:
        {"item_ids": ["id1", "id2", "id3", ...]}

    返回:
        200: {"task_id": "abc123", "message": "批量汉化任务已启动，共 5 项"}
        400: item_ids 为空
    """
    if not req.item_ids:
        raise HTTPException(status_code=400, detail="item_ids 不能为空")

    task_id = task_manager.create_task(
        total=len(req.item_ids),
        message=f"批量汉化任务已启动，共 {len(req.item_ids)} 项",
        metadata={
            "mode": "sinicize_selected",
            "item_count": len(req.item_ids),
        },
    )

    background_tasks.add_task(
        _batch_sinicize_task,
        task_id=task_id,
        item_ids=req.item_ids,
    )

    logger.info(
        "🚀 [SinicizeSelected] 后台任务已提交: task=%s items=%d",
        task_id, len(req.item_ids),
    )
    return {
        "task_id": task_id,
        "message": f"批量汉化任务已启动，共 {len(req.item_ids)} 项",
    }


# ==========================================
# 接口: POST /api/douban/sinicize_all
# 全量汉化 → 自动查出所有未汉化项，后台异步执行
# ==========================================

@router.post("/douban/sinicize_all")
def sinicize_all(req: SinicizeAllRequest, background_tasks: BackgroundTasks):
    """对指定媒体库中所有未汉化媒体项执行全量汉化（后台异步，立即返回 task_id）。

    ★ 三位一体升级：内部先进行"全量大盘同步比对"——
    1. 从 Emby 获取该库所有顶级媒体 ID
    2. 与本地 media_sync_status 比对，找出缺失项
    3. 缺失项自动审计入库（status=pending）
    4. 比对完成后，再查询所有 pending 项推入后台汉化队列

    请求体:
        {"library_id": "1875208"}

    返回:
        200: {"task_id": "abc123", "message": "全量汉化任务已启动，共 50 项", "new_items": 3}
        400: library_id 为空 / 没有未汉化项
    """
    if not req.library_id:
        raise HTTPException(status_code=400, detail="library_id 不能为空")

    cfg = load_config()
    host = cfg.get("emby_host", "").rstrip("/")
    api_key = cfg.get("emby_api_key", "")
    user_id = cfg.get("emby_user_id", "")
    new_items_synced = 0

    # ==========================================
    # ★ 第一步：全量大盘同步比对（Emby ↔ 本地 DB）
    # ==========================================
    if host and api_key:
        logger.info(
            "🔍 [SinicizeAll] 开始全量大盘比对: library=%s", req.library_id,
        )

        # 1a. 从 Emby 获取所有顶级媒体 ID（轻量，仅 ID）
        try:
            emby_ids = _fetch_library_item_ids(host, api_key, user_id, req.library_id)
        except Exception as e:
            logger.error("❌ [SinicizeAll] 获取 Emby ID 列表异常: %s", e)
            emby_ids = []

        if emby_ids:
            # 1b. 查询本地已有的 ID
            db = SessionLocal()
            try:
                local_rows = (
                    db.query(MediaSyncStatus.emby_item_id)
                    .filter(MediaSyncStatus.emby_item_id.in_(emby_ids))
                    .all()
                )
                local_ids = {r.emby_item_id for r in local_rows}

                # 1c. 计算差集：Emby 有但本地缺失的
                missing_ids = [eid for eid in emby_ids if eid not in local_ids]

                if missing_ids:
                    logger.info(
                        "📊 [SinicizeAll] 大盘比对: Emby=%d 本地=%d 缺失=%d，开始自动补齐...",
                        len(emby_ids), len(local_ids), len(missing_ids),
                    )

                    # 1d. 批量拉取缺失项详情并审计入库
                    batch_size = 50
                    for i in range(0, len(missing_ids), batch_size):
                        batch = missing_ids[i:i + batch_size]
                        ids_param = ",".join(batch)

                        try:
                            base_url = (
                                f"{host}/emby/Users/{user_id}/Items"
                                if user_id else f"{host}/emby/Items"
                            )
                            params = {
                                "api_key": api_key,
                                "Ids": ids_param,
                                "Fields": "People,ProviderIds,Overview,ProductionYear,RecursiveItemCount",
                            }
                            resp = _requests.get(base_url, params=params, timeout=30)
                            if resp.status_code != 200:
                                logger.warning(
                                    "   ⚠ [SinicizeAll] 批量获取详情失败 HTTP %d，跳过该批次",
                                    resp.status_code,
                                )
                                continue

                            items = resp.json().get("Items", [])
                            for item in items:
                                item_id = item.get("Id", "")
                                item_name = item.get("Name", "?")
                                try:
                                    _audit_and_save_single_item(
                                        db, item, host, api_key, user_id,
                                        library_id=req.library_id,
                                    )
                                    new_items_synced += 1
                                except Exception:
                                    db.rollback()
                                    logger.warning(
                                        "   ⚠ [SinicizeAll] %s 审计入库异常，跳过",
                                        item_name,
                                    )
                                    continue

                            db.commit()
                        except Exception as e:
                            logger.error(
                                "   ❌ [SinicizeAll] 批量补齐异常: %s", e,
                            )
                            try:
                                db.rollback()
                            except Exception:
                                pass

                    logger.info(
                        "✅ [SinicizeAll] 大盘比对完成: 新入库 %d 项",
                        new_items_synced,
                    )
                else:
                    logger.info(
                        "📊 [SinicizeAll] 大盘比对: Emby=%d 本地=%d，数据完整无需补齐",
                        len(emby_ids), len(local_ids),
                    )
            except Exception as e:
                logger.error(
                    "❌ [SinicizeAll] 大盘比对异常: %s\n%s",
                    e, traceback.format_exc(),
                )
            finally:
                db.close()
    else:
        logger.warning("⚠ [SinicizeAll] Emby 未配置，跳过大盘比对，仅查询本地 DB")

    # ==========================================
    # ★ 第二步：查询所有 pending 项并推入后台汉化队列
    # ==========================================
    db = SessionLocal()
    try:
        pending_items = db.query(MediaSyncStatus).filter(
            MediaSyncStatus.library_id == req.library_id,
            MediaSyncStatus.status == "pending",
        ).all()

        pending_ids = [item.emby_item_id for item in pending_items if item.emby_item_id]

        if not pending_ids:
            raise HTTPException(
                status_code=400,
                detail=f"媒体库 {req.library_id} 中没有未汉化的媒体项",
            )
    finally:
        db.close()

    task_id = task_manager.create_task(
        total=len(pending_ids),
        message=f"全量汉化任务已启动，共 {len(pending_ids)} 项",
        metadata={
            "mode": "sinicize_all",
            "library_id": req.library_id,
            "item_count": len(pending_ids),
            "new_items_synced": new_items_synced,
        },
    )

    background_tasks.add_task(
        _batch_sinicize_task,
        task_id=task_id,
        item_ids=pending_ids,
    )

    logger.info(
        "🚀 [SinicizeAll] 后台任务已提交: task=%s library=%s items=%d new_items=%d",
        task_id, req.library_id, len(pending_ids), new_items_synced,
    )
    return {
        "task_id": task_id,
        "message": f"全量汉化任务已启动，共 {len(pending_ids)} 项",
        "new_items": new_items_synced,
    }


# ==========================================
# 接口: POST /api/sync/force_translate_batch
# 强制汉化 — 无视当前状态，强制覆盖重新汉化
# ==========================================

@router.post("/sync/force_translate_batch")
def force_translate_batch(req: ForceTranslateBatchRequest, background_tasks: BackgroundTasks):
    """对选中的媒体项强制执行汉化（无视当前状态，后台异步，立即返回 task_id）。

    无论 media_sync_status 中的当前状态是 synced、failed 还是 pending，
    全部强制 UPDATE 为 pending 并清空 error_message，然后推入后台汉化队列。

    请求体:
        {"item_ids": ["id1", "id2", "id3", ...]}

    返回:
        200: {"task_id": "abc123", "message": "强制汉化任务已启动，共 5 项"}
        400: item_ids 为空
    """
    if not req.item_ids:
        raise HTTPException(status_code=400, detail="item_ids 不能为空")

    # ★ 强制重置状态：无论当前是什么状态，全部改为 pending
    db = SessionLocal()
    try:
        updated_count = (
            db.query(MediaSyncStatus)
            .filter(MediaSyncStatus.emby_item_id.in_(req.item_ids))
            .update(
                {"status": "pending", "error_message": None},
                synchronize_session=False,
            )
        )
        db.commit()
        logger.info(
            "🔧 [ForceTranslate] 强制重置 %d 条记录为 pending（共请求 %d 个 ID）",
            updated_count, len(req.item_ids),
        )
    except Exception as e:
        db.rollback()
        logger.error("❌ [ForceTranslate] 数据库更新失败: %s", str(e))
        raise HTTPException(status_code=500, detail=f"数据库更新失败: {str(e)}")
    finally:
        db.close()

    task_id = task_manager.create_task(
        total=len(req.item_ids),
        message=f"强制汉化任务已启动，共 {len(req.item_ids)} 项",
        metadata={
            "mode": "force_translate_batch",
            "item_count": len(req.item_ids),
        },
    )

    background_tasks.add_task(
        _batch_sinicize_task,
        task_id=task_id,
        item_ids=req.item_ids,
    )

    logger.info(
        "🚀 [ForceTranslate] 后台任务已提交: task=%s items=%d (强制覆盖)",
        task_id, len(req.item_ids),
    )
    return {
        "task_id": task_id,
        "message": f"强制汉化任务已启动，共 {len(req.item_ids)} 项（已强制重置状态）",
    }


# ==========================================
# ★ 批量补齐分集简介 + 写回 Emby
#   POST /api/sync/repair_episode_overviews
# ==========================================

def _resolve_target_series(db, item_ids: list[str]) -> list[str]:
    """解析待修复的 Series 列表。

    - 显式 item_ids：去空去重保序直接返回；
    - 空（全库模式）：扫描 media_metadata 中 Episode 行的 distinct parent_id
      （真实翻译判定交给 Emby 新鲜数据上的 needs_overview_translation）。
    """
    seen = set()
    out = []
    for i in item_ids:
        i = (i or "").strip()
        if i and i not in seen:
            seen.add(i)
            out.append(i)
    if out:
        return out

    rows = db.query(MediaMetadata.parent_id).filter(
        MediaMetadata.media_type == "Episode",
        MediaMetadata.parent_id.isnot(None),
    ).all()
    for r in rows:
        pid = (r.parent_id or "").strip()
        if pid and pid not in seen:
            seen.add(pid)
            out.append(pid)
    return out


def _patch_episode_overview_db(db, ep_id: str, series_id: str, ep: dict, source: str) -> None:
    """落库分集简介审计：UPSERT MediaMetadata，写入中文 overview + 来源 + 时间戳。

    ★ 不走 save_media_to_db（它会 UPSERT MediaSyncStatus 并把 matched/total 清零，
      毁掉已汉化分集的演员计数）。仅补丁本表，模拟 scan_and_translate 的直接赋值。
    """
    rec = db.query(MediaMetadata).filter(
        MediaMetadata.emby_item_id == ep_id
    ).first()
    if rec is None:
        rec = MediaMetadata(emby_item_id=ep_id)
        db.add(rec)
    rec.media_type = "Episode"
    rec.parent_id = series_id
    rec.title = ep.get("Name") or ""
    rec.overview = ep.get("Overview") or ""
    rec.overview_source = source
    rec.overview_updated_at = datetime.now()


def _repair_series_episode_overviews(sinizer, db, series_id: str) -> dict:
    """对单个 Series 补齐分集简介：Emby 拉全部分集 → 逐集翻译非中文简介 →
    写回 Emby（People 原样回传，不动演员）→ 落库审计。逐集 try/except 隔离。

    Returns:
        {"total", "translated", "skipped", "failed"}
    """
    episodes = sinizer._fetch_episodes(series_id)
    stats = {"total": len(episodes), "translated": 0, "skipped": 0, "failed": 0}
    for ep in episodes:
        ep_id = ep.get("Id")
        if not ep_id:
            stats["skipped"] += 1
            continue
        try:
            # ★ 先按原文判定是否需要翻译（_translate_episode_overview 成功会就地改 Overview）
            needs = needs_overview_translation((ep.get("Overview") or "").strip())
            source = sinizer._translate_episode_overview(ep)
            if not needs:
                stats["skipped"] += 1
                continue
            if not source:
                # 需要翻译但全引擎失败/未过中文校验 → 保留原文
                stats["failed"] += 1
                logger.warning(
                    "   ⚠ [RepairEpOverview] %s 分集简介翻译失败，保留原文", ep_id,
                )
                continue
            # ★ 写回 Emby：Overview 已就地更新为中文；People 原样回传，不动演员
            if not sinizer._write_back_episode(ep_id, ep, ep.get("People") or []):
                stats["failed"] += 1
                logger.warning(
                    "   ⚠ [RepairEpOverview] %s 写回 Emby 失败", ep_id,
                )
                continue
            # ★ 仅写回成功才落库（DB 与 Emby 保持一致）
            _patch_episode_overview_db(db, ep_id, series_id, ep, source)
            db.commit()
            stats["translated"] += 1
        except Exception:
            db.rollback()
            stats["failed"] += 1
            logger.error(
                "   ❌ [RepairEpOverview] %s 异常:\n%s", ep_id, traceback.format_exc(),
            )
    return stats


def _repair_episode_overviews_task(task_id: str, series_ids: list[str]):
    """后台任务：逐剧补齐分集简介（写回 Emby + 落库审计），进度按剧粒度上报。

    ★ 防弹级 try/except/finally 防线（照抄 _batch_sinicize_task）：无论任何原因
    崩溃，保证任务被终结；单剧异常隔离，不阻断后续。
    """
    _repair_success = False
    _repair_final_msg = "❌ 分集简介补齐失败，请查看服务端日志"
    series_ok = 0
    series_failed = 0
    ep_translated = 0
    ep_failed = 0

    db = None
    try:
        db = SessionLocal()
        cfg = load_config()
        # ★ 总开关防御性复检：简介翻译全局关闭时任务不执行
        if not cfg.get("overview_translation_enabled", True):
            _repair_final_msg = (
                "⚠️ 简介翻译总开关已关闭（overview_translation_enabled=False），任务取消"
            )
            try:
                task_manager.update_progress(
                    task_id, status="error", message=_repair_final_msg,
                )
            except Exception:
                pass
            return

        sinizer = DoubanSinizer()
        # ★ 显式修复是独立动作：不受 sinicize_translate_episode_overviews 限制
        sinizer.translate_episode_overviews = True

        for idx, series_id in enumerate(series_ids):
            current = idx + 1
            task_manager.update_progress(
                task_id, current=idx,
                message=f"正在补齐 {current}/{len(series_ids)}...",
            )
            try:
                stats = _repair_series_episode_overviews(sinizer, db, series_id)
                ep_translated += stats["translated"]
                ep_failed += stats["failed"]
                series_ok += 1
                logger.info(
                    "   ✅ [RepairEpOverview] %s 完成: 翻译 %d 集 / 跳过 %d / 失败 %d",
                    series_id, stats["translated"], stats["skipped"], stats["failed"],
                )
            except Exception:
                series_failed += 1
                logger.error(
                    "   ❌ [RepairEpOverview] %s 系列异常:\n%s",
                    series_id, traceback.format_exc(),
                )
                try:
                    db.rollback()
                except Exception:
                    pass
            task_manager.update_progress(
                task_id, current=current,
                message=f"已完成 {current}/{len(series_ids)}（翻译 {ep_translated} 集 | 失败 {ep_failed} 集）",
            )

        _repair_success = True
        _repair_final_msg = (
            f"✅ 分集简介补齐完成: {len(series_ids)} 部剧 | "
            f"成功 {series_ok} | 失败 {series_failed} | 翻译 {ep_translated} 集 | 失败 {ep_failed} 集"
        )

    except Exception as e:
        logger.error(
            "❌ [RepairEpOverview] 任务崩溃 (task=%s):\n%s",
            task_id, traceback.format_exc(),
        )
        _repair_final_msg = f"❌ 任务崩溃: {str(e)[:200]}"
        try:
            task_manager.update_progress(
                task_id, status="error", message=_repair_final_msg,
            )
        except Exception:
            pass
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass
        try:
            task_manager.complete_task(
                task_id, _repair_final_msg, success=_repair_success,
            )
        except Exception:
            try:
                task_manager.complete_task(
                    task_id, "❌ 分集简介补齐异常终止", success=False,
                )
            except Exception:
                logger.error(
                    "❌ [RepairEpOverview] complete_task 重复失败，任务 %s 可能悬挂",
                    task_id,
                )


@router.post("/sync/repair_episode_overviews")
def repair_episode_overviews(
    req: RepairEpisodeOverviewsRequest, background_tasks: BackgroundTasks,
):
    """批量补齐分集简介 + 写回 Emby（后台异步，立即返回 task_id）。

    item_ids 传了 = 只修指定剧；不传（空数组）= 全库扫描有分集记录的 Series。

    请求体:
        {"item_ids": ["id1", "id2"]}   或   {}（全库）

    返回:
        200: {"task_id": "abc123", "message": "...", "count": 2}
        400: 简介翻译总开关已关闭
    """
    cfg = load_config()
    if not cfg.get("overview_translation_enabled", True):
        raise HTTPException(
            status_code=400,
            detail="简介翻译总开关已关闭（overview_translation_enabled=False），无法补齐分集简介",
        )

    db = SessionLocal()
    try:
        targets = _resolve_target_series(db, req.item_ids)
    finally:
        db.close()

    if not targets:
        return {"task_id": "", "message": "没有需要补齐分集简介的剧集", "count": 0}

    task_id = task_manager.create_task(
        total=len(targets),
        message=f"分集简介补齐任务已启动，共 {len(targets)} 部剧",
        metadata={
            "mode": "repair_episode_overviews",
            "item_count": len(targets),
        },
    )
    background_tasks.add_task(
        _repair_episode_overviews_task,
        task_id=task_id,
        series_ids=targets,
    )
    logger.info(
        "🚀 [RepairEpOverview] 触发批量补齐分集简介: task=%s series=%d",
        task_id, len(targets),
    )
    return {
        "task_id": task_id,
        "message": f"分集简介补齐任务已启动，共 {len(targets)} 部剧",
        "count": len(targets),
    }
