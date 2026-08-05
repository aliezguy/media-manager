"""
演员库管理 API — Actor Library

提供 actor_profiles 表的分页查询、搜索过滤，单个演员的强制刷新，
以及一键批量修复残缺元数据。
"""
import logging
import os
import random
import shutil
import threading
import time
import traceback
from datetime import datetime

import requests
from fastapi import APIRouter, Query, HTTPException, BackgroundTasks

from config.settings import load_config
from database import SessionLocal
from models import ActorProfile
from services.actor_profile_service import _download_image, is_image_content
from services.translation_utils import is_valid_chinese_translation
from utils.task_manager import task_manager

logger = logging.getLogger("uvicorn")
router = APIRouter()


@router.get("/actors")
def list_actors(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(24, ge=1, le=100, description="每页数量"),
    search: str = Query("", description="演员姓名模糊搜索"),
    has_local_image: str = Query("", description="本地头像过滤: true/false/空=全部"),
):
    """分页查询演员档案列表。

    支持按姓名模糊搜索、按本地头像有无过滤，默认按 update_time 倒序排列。
    """
    db = SessionLocal()
    try:
        q = db.query(ActorProfile)

        # 姓名模糊搜索
        if search.strip():
            q = q.filter(ActorProfile.name.contains(search.strip()))

        # 本地头像过滤
        if has_local_image.lower() == "true":
            q = q.filter(
                ActorProfile.local_image_path.isnot(None),
                ActorProfile.local_image_path != "",
            )
        elif has_local_image.lower() == "false":
            from sqlalchemy import or_
            q = q.filter(
                or_(
                    ActorProfile.local_image_path.is_(None),
                    ActorProfile.local_image_path == "",
                )
            )

        total = q.count()
        items = (
            q.order_by(ActorProfile.update_time.desc(), ActorProfile.name)
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )

        return {
            "total": total,
            "items": [
                {
                    "name": a.name,
                    "local_image_path": a.local_image_path or "",
                    "image_url": a.image_url or "",
                    "source": a.source or "",
                    "tmdb_id": a.tmdb_id or "",
                    "imdb_id": a.imdb_id or "",
                    "douban_celebrity_id": a.douban_celebrity_id or "",
                    "birth_date": a.birth_date or "",
                    "birth_place": a.birth_place or "",
                    "overview": a.overview or "",
                    "update_time": (
                        a.update_time.isoformat() if a.update_time else ""
                    ),
                }
                for a in items
            ],
        }
    finally:
        db.close()


@router.post("/actors/{actor_name}/refresh")
def refresh_actor(actor_name: str):
    """强制刷新单个演员档案 — 全量穿透强制更新。

    流程:
      1. 主动反查 Emby /Persons API，获取 emby_person_id + emby_image_tag
         （支撑 L0.5 Emby 原生头像优先逻辑）
      2. 调用 resolve_actor_profile(force_refresh=True, context_info=ctx)
         → 跳过 L0 缓存拦截 → 全量穿透 L0.5 Emby → L1 豆瓣 → L2 TMDB
      3. 提交事务，返回最新档案数据
    """
    from services.actor_profile_service import resolve_actor_profile

    cfg = load_config()
    emby_server = cfg.get("emby_host", "").rstrip("/")
    emby_api_key = cfg.get("emby_api_key", "")

    ctx = {}

    # ★ 主动反查 Emby 获取 Person ID 上下文（支撑 L0.5）
    if emby_server and emby_api_key:
        try:
            res = requests.get(
                f"{emby_server}/emby/Persons",
                params={"SearchTerm": actor_name, "api_key": emby_api_key},
                timeout=5,
            )
            if res.status_code == 200:
                items = res.json().get("Items", [])
                for item in items:
                    if item.get("Name") == actor_name:
                        ctx["emby_person_id"] = item.get("Id")
                        ctx["emby_image_tag"] = (
                            item.get("PrimaryImageTag")
                            or (
                                item.get("ImageTags", {}).get("Primary")
                                if isinstance(item.get("ImageTags"), dict)
                                else None
                            )
                        )
                        # ★ 核心修复：把 Emby 的 ProviderIds 完整塞入上下文
                        #    包含 DoubanCelebrityId → L1 豆瓣精准查询
                        #    包含 Tmdb → L2 TMDB 精准 ID 拦截
                        ctx["ProviderIds"] = item.get("ProviderIds", {})
                        break
            else:
                logger.warning(
                    "   ⚠ [Refresh] Emby /Persons 返回 %d: %s",
                    res.status_code,
                    res.text[:200],
                )
        except requests.exceptions.RequestException as e:
            logger.warning(
                "   ⚠ [Refresh] Emby 反查网络异常 (%s): %s",
                type(e).__name__,
                e,
            )
        except Exception as e:
            logger.warning("   ⚠ [Refresh] Emby 反查失败: %s", e)

    if ctx:
        logger.info(
            "   🚀 [Refresh] 携带 Emby 上下文穿透刷新: %s",
            actor_name,
        )
    else:
        logger.info(
            "   🚀 [Refresh] 无 Emby 上下文，穿透刷新（跳过 L0.5）: %s",
            actor_name,
        )

    db = SessionLocal()
    try:
        result = resolve_actor_profile(
            actor_name, db,
            context_info=ctx,
            force_refresh=True,
            # ★ 演员库路径：显式 False → LLM 简介补全始终开启，不受 actor_bio_inline_enabled 配置影响
            skip_llm_enrich=False,
        )

        if result is None:
            raise HTTPException(
                status_code=404,
                detail=f"无法解析演员 '{actor_name}'：所有图片源均无可用数据",
            )

        db.commit()
        return {
            "name": result["name"],
            "local_image_path": result.get("local_image_path", ""),
            "image_url": result.get("image_url", ""),
            "source": result.get("source", ""),
            "tmdb_id": result.get("tmdb_id", ""),
            "imdb_id": result.get("imdb_id", ""),
            "douban_celebrity_id": result.get("douban_celebrity_id", ""),
            "birth_date": result.get("birth_date", ""),
            "birth_place": result.get("birth_place", ""),
            "overview": result.get("overview", ""),
            "message": f"演员 '{actor_name}' 刷新成功",
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"刷新演员 '{actor_name}' 失败: {str(e)}",
        )
    finally:
        db.close()


# ================================================================
# 一键批量修复 — 后台任务 + 触发接口
# ================================================================

def _batch_repair_task(task_id: str):
    """后台任务：遍历所有缺元数据的演员，强制穿透刷新。

    查询条件：overview 为空 或 tmdb_id 为空。
    每个演员独立 try/except 隔离，单个失败不影响整体。
    ★ try/except/finally 全局防线：保证任务永不被悬挂。
    """
    from services.actor_profile_service import resolve_actor_profile

    cfg = load_config()
    emby_server = cfg.get("emby_host", "").rstrip("/")
    emby_api_key = cfg.get("emby_api_key", "")

    db = SessionLocal()
    _repair_success = False
    _repair_final_msg = "❌ 批量修复失败，请查看服务端日志"
    repaired = 0
    failed = 0
    skipped = 0

    try:
        # 查询缺失 TMDB ID 的演员
        from sqlalchemy import or_
        broken = (
            db.query(ActorProfile)
            .filter(
                or_(
                    ActorProfile.tmdb_id.is_(None),
                    ActorProfile.tmdb_id == "",
                )
            )
            .all()
        )

        total = len(broken)
        task_manager.update_progress(
            task_id,
            total=total,
            message=f"发现 {total} 位演员缺少元数据，开始修复...",
        )

        if total == 0:
            _repair_success = True
            _repair_final_msg = "✅ 所有演员元数据已完整，无需修复"
            return

        logger.info(
            "🔧 [BatchRepair] 开始修复 %d 位残缺演员 (task=%s)",
            total, task_id,
        )

        for idx, actor in enumerate(broken):
            current = idx + 1
            actor_name = actor.name

            task_manager.update_progress(
                task_id,
                current=current - 1,
                message=f"修复中 {current}/{total}: {actor_name}",
            )

            try:
                # ★ 主动反查 Emby 获取 Person 上下文
                ctx = {}
                if emby_server and emby_api_key:
                    try:
                        res = requests.get(
                            f"{emby_server}/emby/Persons",
                            params={"SearchTerm": actor_name, "api_key": emby_api_key},
                            timeout=5,
                        )
                        if res.status_code == 200:
                            items = res.json().get("Items", [])
                            for item in items:
                                if item.get("Name") == actor_name:
                                    ctx["emby_person_id"] = item.get("Id")
                                    ctx["emby_image_tag"] = (
                                        item.get("PrimaryImageTag")
                                        or (
                                            item.get("ImageTags", {}).get("Primary")
                                            if isinstance(item.get("ImageTags"), dict)
                                            else None
                                        )
                                    )
                                    ctx["ProviderIds"] = item.get("ProviderIds", {})
                                    break
                    except Exception:
                        pass  # Emby 不可达不阻塞修复流程

                # ★ 强制穿透刷新（跳过 L0 缓存 + 冷却期）
                result = resolve_actor_profile(
                    actor_name, db,
                    context_info=ctx,
                    force_refresh=True,
                    # ★ 演员库路径：显式 False → LLM 简介补全始终开启，不受 actor_bio_inline_enabled 配置影响
                    skip_llm_enrich=False,
                )

                if result and (result.get("overview") or result.get("tmdb_id")):
                    db.commit()
                    repaired += 1
                    logger.info(
                        "   ✅ [BatchRepair] %s 修复成功 (tmdb=%s, overview=%d chars)",
                        actor_name,
                        result.get("tmdb_id", "-"),
                        len(result.get("overview", "")),
                    )
                else:
                    db.rollback()
                    skipped += 1
                    logger.warning(
                        "   ⏭ [BatchRepair] %s 仍未获取到元数据", actor_name,
                    )
            except Exception:
                db.rollback()
                failed += 1
                logger.error(
                    "   ❌ [BatchRepair] %s 异常:\n%s",
                    actor_name, traceback.format_exc(),
                )

            task_manager.update_progress(
                task_id,
                current=current,
                message=(
                    f"已完成 {current}/{total} "
                    f"（修复 {repaired} | 跳过 {skipped} | 失败 {failed}）"
                ),
            )

            # ★ 拟人化随机休眠：避免高频并发触发豆瓣/TMDB 反爬流控
            time.sleep(random.uniform(1.5, 3.5))

        _repair_success = True
        _repair_final_msg = (
            f"✅ 批量修复完成: {total} 位演员 | "
            f"修复 {repaired} | 跳过 {skipped} | 失败 {failed}"
        )

    except Exception as e:
        logger.error(
            "❌ [BatchRepair] 批量修复任务崩溃 (task=%s):\n%s",
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
        try:
            task_manager.complete_task(
                task_id, _repair_final_msg, success=_repair_success,
            )
        except Exception:
            try:
                task_manager.complete_task(
                    task_id, "❌ 批量修复异常终止", success=False,
                )
            except Exception:
                pass
        db.close()


@router.post("/actors/repair_missing")
def repair_missing_actors(background_tasks: BackgroundTasks):
    """一键批量修复：查询所有缺失元数据的演员，后台逐项穿透刷新。

    Returns:
        {"task_id": str} — 前端通过 GET /api/tasks/{task_id} 轮询进度
    """
    db = SessionLocal()
    try:
        from sqlalchemy import or_
        broken_count = (
            db.query(ActorProfile)
            .filter(
                or_(
                    ActorProfile.tmdb_id.is_(None),
                    ActorProfile.tmdb_id == "",
                )
            )
            .count()
        )
    finally:
        db.close()

    if broken_count == 0:
        return {
            "task_id": "",
            "message": "所有演员元数据已完整，无需修复",
        }

    task_id = task_manager.create_task(
        total=broken_count,
        message=f"发现 {broken_count} 位演员缺少元数据，准备修复...",
        metadata={"type": "batch_repair", "count": broken_count},
    )

    background_tasks.add_task(_batch_repair_task, task_id=task_id)

    logger.info(
        "🔧 [BatchRepair] 触发批量修复: task=%s count=%d",
        task_id, broken_count,
    )
    return {
        "task_id": task_id,
        "message": f"批量修复已启动，共 {broken_count} 位演员",
    }


# ================================================================
# 存量英文出生地批量汉化 — 后台任务 + 触发接口
# ================================================================

def _repair_birthplace_task(task_id: str):
    """后台任务：批量汉化所有非空非中文的出生地。

    查询 birth_place 非空的行，Python 侧过滤出非中文出生地，
    逐个调 translate_birth_place（本地 qwen 优先 + strict-NULL），成功且含中文 → 写回。
    单条失败保留原值，绝不影响其他演员。
    """
    from services.actor_profile_ai import translate_birth_place, merge_sources, merge_field_sources

    db = SessionLocal()
    _repair_success = False
    _repair_final_msg = "❌ 出生地批量汉化失败，请查看服务端日志"
    repaired = 0
    failed = 0
    skipped = 0

    try:
        rows = (
            db.query(ActorProfile)
            .filter(ActorProfile.birth_place.isnot(None), ActorProfile.birth_place != "")
            .all()
        )
        targets = [r for r in rows if not is_valid_chinese_translation(r.birth_place)]
        total = len(targets)

        task_manager.update_progress(
            task_id,
            total=total,
            message=f"发现 {total} 条非中文出生地，开始汉化...",
        )

        if total == 0:
            _repair_success = True
            _repair_final_msg = "✅ 所有出生地均已汉化，无需修复"
            return

        logger.info(
            "🔧 [RepairBirthplace] 开始汉化 %d 条英文出生地 (task=%s)",
            total, task_id,
        )

        for idx, actor in enumerate(targets):
            current = idx + 1
            original = actor.birth_place or ""
            task_manager.update_progress(
                task_id,
                current=current - 1,
                message=f"汉化中 {current}/{total}: {actor.name}",
            )

            try:
                ctx = {}
                translated = translate_birth_place(original, actor.name, ctx=ctx)
                if is_valid_chinese_translation(translated) and translated != original:
                    actor.birth_place = translated
                    actor.llm_translation_source = merge_sources(
                        actor.llm_translation_source or "",
                        ",".join(sorted(ctx.get("_sources") or ())),
                    )
                    actor.llm_field_sources = merge_field_sources(
                        actor.llm_field_sources, ctx.get("_field_sources") or {},
                    )
                    actor.update_time = datetime.now()
                    db.commit()
                    repaired += 1
                    logger.info(
                        "   ✅ [RepairBirthplace] %s: %r → %r",
                        actor.name, original, translated,
                    )
                else:
                    db.rollback()
                    skipped += 1
                    logger.warning(
                        "   ⏭ [RepairBirthplace] %s: LLM 返回 NULL/无效，保留原值 %r",
                        actor.name, original,
                    )
            except Exception:
                db.rollback()
                failed += 1
                logger.error(
                    "   ❌ [RepairBirthplace] %s 异常:\n%s",
                    actor.name, traceback.format_exc(),
                )

            task_manager.update_progress(
                task_id,
                current=current,
                message=(
                    f"已完成 {current}/{total} "
                    f"（汉化 {repaired} | 跳过 {skipped} | 失败 {failed}）"
                ),
            )

            # 拟人化随机休眠，避免高频并发触发 LLM Provider 限流
            time.sleep(random.uniform(0.5, 1.5))

        _repair_success = True
        _repair_final_msg = (
            f"✅ 出生地批量汉化完成: {total} 条 | "
            f"汉化 {repaired} | 跳过 {skipped} | 失败 {failed}"
        )

    except Exception as e:
        logger.error(
            "❌ [RepairBirthplace] 任务崩溃 (task=%s):\n%s",
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
        try:
            task_manager.complete_task(
                task_id, _repair_final_msg, success=_repair_success,
            )
        except Exception:
            try:
                task_manager.complete_task(
                    task_id, "❌ 出生地汉化异常终止", success=False,
                )
            except Exception:
                pass
        db.close()


@router.post("/actors/repair_birthplace")
def repair_birthplace(background_tasks: BackgroundTasks):
    """一键批量汉化存量英文出生地（后台任务，前端经 GET /api/tasks/{task_id} 轮询）。"""
    db = SessionLocal()
    try:
        rows = (
            db.query(ActorProfile)
            .filter(ActorProfile.birth_place.isnot(None), ActorProfile.birth_place != "")
            .all()
        )
        broken_count = sum(1 for r in rows if not is_valid_chinese_translation(r.birth_place))
    finally:
        db.close()

    if broken_count == 0:
        return {"task_id": "", "message": "所有出生地均已汉化，无需修复", "count": 0}

    task_id = task_manager.create_task(
        total=broken_count,
        message=f"发现 {broken_count} 条非中文出生地，准备汉化...",
        metadata={"type": "repair_birthplace", "count": broken_count},
    )

    background_tasks.add_task(_repair_birthplace_task, task_id=task_id)

    logger.info(
        "🔧 [RepairBirthplace] 触发批量汉化: task=%s count=%d",
        task_id, broken_count,
    )
    return {
        "task_id": task_id,
        "message": f"出生地批量汉化已启动，共 {broken_count} 条",
        "count": broken_count,
    }


# ================================================================
# 演员简介一键补全（overview 为空/非中文）— 后台任务 + 触发接口
# ================================================================

def _repair_overview_task(task_id: str):
    """后台任务：批量补全所有缺失或非中文的演员简介（overview）。

    查询 overview 为空 或 非中文 的演员，逐个调 resolve_actor_profile
    （显式 skip_llm_enrich=False → 强制 LLM 补全/汉化，不受 actor_bio_inline_enabled
    配置影响）：L0 库命中 → _llm_enrich_existing 零网络补简介；无缓存才落网络路径。
    llm_check_status=2 冷静期天然限流冷门演员。单条失败保留原值，绝不影响其他演员。
    """
    from services.actor_profile_service import resolve_actor_profile

    db = SessionLocal()
    _repair_success = False
    _repair_final_msg = "❌ 简介批量补全失败，请查看服务端日志"
    repaired = 0
    failed = 0
    skipped = 0

    try:
        rows = db.query(ActorProfile).all()
        targets = [
            r for r in rows
            if not r.overview or not is_valid_chinese_translation(r.overview)
        ]
        total = len(targets)

        task_manager.update_progress(
            task_id,
            total=total,
            message=f"发现 {total} 位演员简介缺失/非中文，开始补全...",
        )

        if total == 0:
            _repair_success = True
            _repair_final_msg = "✅ 所有演员简介均已完整，无需修复"
            return

        logger.info(
            "🔧 [RepairOverview] 开始补全 %d 位演员简介 (task=%s)",
            total, task_id,
        )

        for idx, actor in enumerate(targets):
            current = idx + 1
            actor_name = actor.name

            task_manager.update_progress(
                task_id,
                current=current - 1,
                message=f"补全中 {current}/{total}: {actor_name}",
            )

            try:
                # ★ 演员库路径：显式 skip_llm_enrich=False → 强制补简介，不受配置影响
                result = resolve_actor_profile(
                    actor_name, db,
                    context_info={},
                    skip_llm_enrich=False,
                )
                if result and (
                    result.get("overview")
                    and is_valid_chinese_translation(result["overview"])
                ):
                    db.commit()
                    repaired += 1
                    logger.info(
                        "   ✅ [RepairOverview] %s 简介已补全 (%d chars)",
                        actor_name, len(result["overview"]),
                    )
                else:
                    db.rollback()
                    skipped += 1
                    logger.warning(
                        "   ⏭ [RepairOverview] %s 简介仍未补全", actor_name,
                    )
            except Exception:
                db.rollback()
                failed += 1
                logger.error(
                    "   ❌ [RepairOverview] %s 异常:\n%s",
                    actor_name, traceback.format_exc(),
                )

            task_manager.update_progress(
                task_id,
                current=current,
                message=(
                    f"已完成 {current}/{total} "
                    f"（补全 {repaired} | 跳过 {skipped} | 失败 {failed}）"
                ),
            )

            # 拟人化随机休眠，避免高频并发触发 LLM Provider 限流
            time.sleep(random.uniform(0.5, 1.5))

        _repair_success = True
        _repair_final_msg = (
            f"✅ 简介批量补全完成: {total} 位演员 | "
            f"补全 {repaired} | 跳过 {skipped} | 失败 {failed}"
        )

    except Exception as e:
        logger.error(
            "❌ [RepairOverview] 任务崩溃 (task=%s):\n%s",
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
        try:
            task_manager.complete_task(
                task_id, _repair_final_msg, success=_repair_success,
            )
        except Exception:
            try:
                task_manager.complete_task(
                    task_id, "❌ 简介补全异常终止", success=False,
                )
            except Exception:
                pass
        db.close()


@router.post("/actors/repair_overview")
def repair_overview(background_tasks: BackgroundTasks):
    """一键批量补全缺失/非中文演员简介（后台任务，前端经 GET /api/tasks/{task_id} 轮询）。"""
    db = SessionLocal()
    try:
        rows = db.query(ActorProfile).all()
        broken_count = sum(
            1 for r in rows
            if not r.overview or not is_valid_chinese_translation(r.overview)
        )
    finally:
        db.close()

    if broken_count == 0:
        return {"task_id": "", "message": "所有演员简介均已完整，无需修复", "count": 0}

    task_id = task_manager.create_task(
        total=broken_count,
        message=f"发现 {broken_count} 位演员简介缺失/非中文，准备补全...",
        metadata={"type": "repair_overview", "count": broken_count},
    )

    background_tasks.add_task(_repair_overview_task, task_id=task_id)

    logger.info(
        "🔧 [RepairOverview] 触发批量补全: task=%s count=%d",
        task_id, broken_count,
    )
    return {
        "task_id": task_id,
        "message": f"简介批量补全已启动，共 {broken_count} 位演员",
        "count": broken_count,
    }


# ================================================================
# 历史路径一键修复 — 扫描并重命名缺少规范 ID 的本地图片文件夹
# ================================================================

# people 目录 (项目根/people/)
PEOPLE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "people")
)


@router.post("/actors/fix_paths")
@router.get("/actors/fix_paths")
def fix_historical_actor_paths():
    """将不带规范 ID（-tmdb- 或 -douban-）的旧路径进行物理重命名和 DB 更新。

    核心功能：
      1. 扫描数据库中 local_image_path 缺少 -tmdb- / -douban- 的历史记录
      2. 将旧目录下的图片原封不动地移动到规范新目录（保留原始扩展名）
      3. 冲突解决：若移动后新目录下存在多张图片，以文件大小（getsize）
         作为清晰度判断基准，保留体积最大的一张，删除其余冗余图片
      4. 将最终获胜的图片名称更新至数据库 local_image_path
      5. 清理安全的空旧目录

    Returns:
        {"status": "success", "total_scanned": int, "fixed_count": int,
         "resolved_conflict_count": int, "errors": [...]}
    """
    db = SessionLocal()
    try:
        # 查找有图片但路径中不含 -tmdb- 或 -douban- 规范标记的记录
        actors_to_fix = (
            db.query(ActorProfile)
            .filter(
                ActorProfile.local_image_path.isnot(None),
                ActorProfile.local_image_path != "",
                ~ActorProfile.local_image_path.like("%-tmdb-%"),
                ~ActorProfile.local_image_path.like("%-douban-%"),
            )
            .all()
        )

        fixed_count = 0
        resolved_conflict_count = 0
        errors = []

        for actor in actors_to_fix:
            try:
                current_path = actor.local_image_path
                path_parts = current_path.replace("\\", "/").split("/")

                if len(path_parts) < 3:
                    continue

                parent_dir = path_parts[-3]       # 首字，如 "曲"
                old_actor_dir = path_parts[-2]    # 旧目录名，如 "曲靖"
                old_file_name = path_parts[-1]    # 原始文件名，如 "folder.png"

                # ---- 优先提取规范 ID ----
                target_id = getattr(actor, 'tmdb_id', '') or ''
                id_type = "tmdb" if target_id else ""
                if not target_id:
                    target_id = getattr(actor, 'douban_celebrity_id', '') or ''
                    id_type = "douban" if target_id else ""

                if not target_id:
                    continue  # 无任何 ID 的旧记录无法规范化，跳过

                new_actor_dir = f"{actor.name}-{id_type}-{target_id}"
                new_rel_dir = f"{parent_dir}/{new_actor_dir}"

                # 已经是规范路径，跳过
                if f"{parent_dir}/{old_actor_dir}" == new_rel_dir:
                    continue

                old_abs_path = os.path.join(PEOPLE_DIR, current_path)
                old_abs_dir = os.path.dirname(old_abs_path)
                new_abs_dir = os.path.join(PEOPLE_DIR, new_rel_dir)

                os.makedirs(new_abs_dir, exist_ok=True)

                # ---- 1. 安全移动：加 moved_ 前缀防止覆盖同名文件 ----
                if os.path.exists(old_abs_path):
                    temp_move_path = os.path.join(new_abs_dir, f"moved_{old_file_name}")
                    shutil.move(old_abs_path, temp_move_path)
                    logger.info(
                        "   📦 [FixPaths] 迁移: %s → %s",
                        current_path, temp_move_path,
                    )

                # ---- 2. 优胜劣汰比对：扫描新目录内所有图片，找出体积最大（最清晰）的 ----
                valid_exts = (".png", ".jpg", ".jpeg", ".webp")
                best_file = None
                max_size = -1

                files_in_new_dir = []
                if os.path.exists(new_abs_dir):
                    files_in_new_dir = os.listdir(new_abs_dir)

                for f in files_in_new_dir:
                    if f.startswith("folder") or f.startswith("moved_folder"):
                        ext = os.path.splitext(f)[1].lower()
                        if ext in valid_exts:
                            f_path = os.path.join(new_abs_dir, f)
                            try:
                                f_size = os.path.getsize(f_path)
                                if f_size > max_size:
                                    max_size = f_size
                                    best_file = f
                            except OSError:
                                pass

                # ---- 3. 清理与回写：先杀光所有冗余文件，最后再把获胜者重命名 ----
                if best_file:
                    best_ext = os.path.splitext(best_file)[1].lower()
                    final_file_name = f"folder{best_ext}"
                    best_file_path = os.path.join(new_abs_dir, best_file)
                    final_abs_path = os.path.join(new_abs_dir, final_file_name)

                    # ★ 先遍历删除败者（绝对安全，不会删到 best_file）
                    for f in files_in_new_dir:
                        if f != best_file:
                            if (f.startswith("folder") or f.startswith("moved_folder")) and f.endswith(valid_exts):
                                try:
                                    os.remove(os.path.join(new_abs_dir, f))
                                    resolved_conflict_count += 1
                                    logger.info(
                                        "   🗑 [FixPaths] 淘汰冗余图片: %s/%s",
                                        new_rel_dir, f,
                                    )
                                except OSError:
                                    pass

                    # ★ 最后，只有当获胜者的名字还不叫 folder.xxx 时，才进行重命名
                    if best_file != final_file_name:
                        shutil.move(best_file_path, final_abs_path)

                    # ---- 4. 最终路径更新至数据库 ----
                    actor.local_image_path = f"{new_rel_dir}/{final_file_name}"
                    fixed_count += 1
                    logger.info(
                        "   ✅ [FixPaths] 规范化: %s → %s (winner=%s, %d bytes)",
                        current_path, actor.local_image_path,
                        final_file_name, max_size,
                    )
                else:
                    logger.warning(
                        "   ⚠ [FixPaths] 迁移后新目录无有效图片: %s", actor.name,
                    )

                # ---- 5. 清理安全的旧空目录 ----
                if os.path.exists(old_abs_dir) and os.path.isdir(old_abs_dir):
                    try:
                        if not os.listdir(old_abs_dir):
                            os.rmdir(old_abs_dir)
                            logger.debug("   🧹 [FixPaths] 清理空目录: %s", old_abs_dir)
                    except OSError:
                        pass

            except Exception as e:
                errors.append(f"{actor.name}: {str(e)}")
                logger.error(
                    "   ❌ [FixPaths] 修复失败: %s — %s",
                    actor.name, e,
                )

        db.commit()
        logger.info(
            "🔧 [FixPaths] 历史路径大迁徙完成: scanned=%d fixed=%d conflicts=%d errors=%d",
            len(actors_to_fix), fixed_count, resolved_conflict_count, len(errors),
        )
        return {
            "status": "success",
            "total_scanned": len(actors_to_fix),
            "fixed_count": fixed_count,
            "resolved_conflict_count": resolved_conflict_count,
            "errors": errors,
        }
    except Exception as e:
        db.rollback()
        logger.error(
            "❌ [FixPaths] 批量修复异常: %s\n%s",
            e, traceback.format_exc(),
        )
        raise HTTPException(
            status_code=500,
            detail=f"历史路径修复失败: {str(e)}",
        )
    finally:
        db.close()


# ================================================================
# 极速恢复 API — 利用数据库残存的 image_url 重新下载丢失的物理文件
# ================================================================

@router.post("/actors/recover_images")
@router.get("/actors/recover_images")
def recover_missing_images():
    """极速恢复：扫描数据库，若发现本地文件丢失但存在网络直链，则直接重新下载。

    适用于 fix_paths 搬家过程中因 BUG 导致物理文件被误删、
    但数据库中仍保留 image_url 和 local_image_path 的场景。
    相比 force_refresh 全量穿透，此接口仅做一次 HTTP 下载，零外部 API 查询。

    Returns:
        {"status": "success", "recovered_count": int, "failed_count": int, "errors": [...]}
    """
    db = SessionLocal()
    try:
        actors = (
            db.query(ActorProfile)
            .filter(
                ActorProfile.local_image_path.isnot(None),
                ActorProfile.local_image_path != "",
                ActorProfile.image_url.isnot(None),
                ActorProfile.image_url != "",
            )
            .all()
        )

        recovered_count = 0
        failed_count = 0
        errors = []

        for actor in actors:
            try:
                abs_path = os.path.join(PEOPLE_DIR, actor.local_image_path)

                # 物理文件确实丢失了
                if not os.path.exists(abs_path):
                    os.makedirs(os.path.dirname(abs_path), exist_ok=True)

                    if _download_image(actor.image_url, abs_path):
                        recovered_count += 1
                        logger.info(
                            "   ✅ [Recover] 极速恢复: %s → %s",
                            actor.name, actor.local_image_path,
                        )
                    else:
                        # 下载失败，清空本地路径，交由后续常规批量修复处理
                        actor.local_image_path = ""
                        failed_count += 1
                        logger.warning(
                            "   ⚠ [Recover] 下载失败，清空路径: %s (%s)",
                            actor.name, actor.image_url[:80],
                        )
            except Exception as e:
                errors.append(f"{actor.name}: {str(e)}")
                actor.local_image_path = ""
                failed_count += 1
                logger.error(
                    "   ❌ [Recover] 恢复异常: %s — %s",
                    actor.name, e,
                )

        db.commit()
        logger.info(
            "🩹 [Recover] 极速恢复完成: recovered=%d failed=%d errors=%d",
            recovered_count, failed_count, len(errors),
        )
        return {
            "status": "success",
            "recovered_count": recovered_count,
            "failed_count": failed_count,
            "errors": errors,
        }
    except Exception as e:
        db.rollback()
        logger.error(
            "❌ [Recover] 批量恢复异常: %s\n%s",
            e, traceback.format_exc(),
        )
        raise HTTPException(
            status_code=500,
            detail=f"极速恢复失败: {str(e)}",
        )
    finally:
        db.close()


# ================================================================
# 一键清洗无效头像 — 根治「数据库有记录但物理文件丢失/损坏」的脏数据
# ================================================================

# 有效图片魔数签名 → 根治「HTML 错误页冒充图片」的假阳性。
# douban 等 CDN 反爬时返回 ~1KB 的 HTML 错误页，_download_image 会原样
# 落盘成 folder.jpg：文件"存在"且"非 0 字节"，但浏览器无法渲染成 <img>。
_DEBUG_NAMES = {"洪顺昌", "许曦文"}  # 命中则逐项打印校验明细


def _is_placeholder_path(value):
    """排除占位符脏文本：None / 空串 / 纯空白 / 'None' / 'null'（忽略大小写）"""
    if value is None:
        return True
    stripped = value.strip()
    return stripped == "" or stripped.lower() in {"none", "null"}


def _read_magic(path, length=16):
    """读取文件头魔数；读取失败返回 None"""
    try:
        with open(path, "rb") as f:
            return f.read(length)
    except OSError:
        return None


def _is_valid_image_file(path):
    """物理文件深度校验 — 全部满足才算有效。

    ① os.path.isfile：文件必须存在 且 是普通文件（拒绝目录冒充）
    ② os.path.getsize > 0：0 字节/空文件必须清洗
    ③ 文件头魔数确认为真实图片 (jpeg/png/webp/gif)：拒绝 HTML 错误页等
       非图片内容（极小但魔数合法的文件是真实图片，会保留）

    ★ 魔数判据与 _download_image 下载守卫共用 is_image_content，
       保证「下载拒绝的内容」与「清洗判无效的内容」永远一致。

    Returns:
        (is_valid: bool, reason: str | None)  — reason 仅在无效时非空
    """
    if not os.path.isfile(path):
        return False, "not_file"        # 不存在 / 目录 / 特殊文件
    try:
        size = os.path.getsize(path)
    except OSError:
        return False, "unreadable"      # 存在但不可读（权限/损坏）
    if size == 0:
        return False, "zero_byte"
    head = _read_magic(path)
    if head is None:
        return False, "unreadable"
    if not is_image_content(head):
        return False, "invalid_content"  # HTML 错误页 / 其他非图片内容
    return True, None


@router.post("/actors/cleanup_images")
def cleanup_broken_images():
    """一键清洗无效头像 — 消除 DB 与物理文件之间的脏数据（加固版）。

    此前的版本仅用 os.path.exists + 非 0 字节判断，产生「假阳性」：
    CDN 反爬的 HTML 错误页被以 folder.jpg 落盘 → 存在且非 0 字节
    → 判定有效，但前端 <img> 加载失败显示「缺头像」，名不副实。

    本次加固校验维度（全部满足才算"有效"）:
      1. local_image_path 非占位符（排除 "", "None", "null", 纯空白）
      2. 物理文件存在 且 是普通文件 (os.path.isfile，拒绝目录冒充)
      3. 文件大小 > 0 字节
      4. 文件头魔数确认为真实图片 (jpeg/png/webp/gif)，拒绝 HTML 冒充

    Returns:
        {"status": "success", "total_checked": int, "cleaned_count": int,
         "empty_file_count": int, "detail_by_reason": {reason: count},
         "cleaned": [{name, path, reason}, ...]}
    """
    db = SessionLocal()
    try:
        actors = (
            db.query(ActorProfile)
            .filter(
                ActorProfile.local_image_path.isnot(None),
                ActorProfile.local_image_path != "",
            )
            .all()
        )

        total_checked = len(actors)
        cleaned = []
        by_reason = {}  # reason → 数量

        for actor in actors:
            raw_path = actor.local_image_path or ""
            abs_path = os.path.join(PEOPLE_DIR, raw_path)

            is_valid = False
            if _is_placeholder_path(raw_path):
                reason = "placeholder"          # ① 占位符脏文本
            else:
                is_valid, reason = _is_valid_image_file(abs_path)  # ②③④

            # ★ 针对性 Debug 日志：洪顺昌 / 许曦文
            if actor.name in _DEBUG_NAMES:
                logger.info(
                    "🔎 [CleanupImages][DEBUG] %s | db_path=%r | abs=%r | "
                    "valid=%s reason=%s",
                    actor.name, raw_path, abs_path, is_valid, reason,
                )

            if is_valid:
                continue  # 真实图片，无需处理

            by_reason[reason] = by_reason.get(reason, 0) + 1
            cleaned.append({
                "name": actor.name,
                "path": raw_path,
                "reason": reason,
            })

            # ★ 清洗脏数据：图片字段 + 来源一起重置，等待下次穿透刷新重新抓取
            actor.local_image_path = ""
            actor.image_url = ""
            actor.source = ""

        db.commit()

        cleaned_count = len(cleaned)
        empty_file_count = by_reason.get("zero_byte", 0)
        logger.info(
            "🧹 [CleanupImages] 无效头像清洗完成: checked=%d cleaned=%d detail=%s",
            total_checked, cleaned_count, by_reason,
        )
        return {
            "status": "success",
            "total_checked": total_checked,
            "cleaned_count": cleaned_count,
            "empty_file_count": empty_file_count,
            "detail_by_reason": by_reason,
            "cleaned": cleaned[:200],  # 控制响应体积，全量明细见服务端日志
        }
    except Exception as e:
        db.rollback()
        logger.error(
            "❌ [CleanupImages] 清洗异常: %s\n%s",
            e, traceback.format_exc(),
        )
        raise HTTPException(
            status_code=500,
            detail=f"无效头像清洗失败: {str(e)}",
        )
    finally:
        db.close()
