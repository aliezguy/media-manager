"""maintenance_jobs.py — 全量汉化 / 全量审计 / 全库简介汉化可配置定时任务。

三个任务的配置持久化在 config.yaml（``localization_job`` / ``audit_job`` / ``overview_job``），
由本模块负责：
- 配置读写（含 last_run_at 持久化、next_run_at 动态计算）
- APScheduler Cron 作业注册/移除（与 scheduler_service 共用全局 scheduler）
- 执行函数：全量审计（Emby ↔ 本地比对补齐）、全量汉化（三位一体 + 批量汉化）

注意：为避免循环依赖，对 ``scheduler_service`` / ``sync_actions`` 的引用
全部使用函数内 inline import。
"""

import asyncio
import logging
from datetime import datetime

from apscheduler.triggers.cron import CronTrigger
from croniter import croniter

from config.settings import load_config, save_config

JOB_KEYS = ("localization_job", "audit_job", "overview_job")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 配置读写
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """当前时间的 ISO 字符串（秒级精度）。"""
    return datetime.now().isoformat(timespec="seconds")


def _compute_next_run(cron_expr: str, is_active: bool) -> str | None:
    """计算下次触发时间；未启用或表达式非法时返回 None。"""
    if not is_active or not cron_expr:
        return None
    try:
        return croniter(cron_expr, datetime.now()).get_next(datetime).isoformat(
            timespec="seconds"
        )
    except Exception:
        return None


def _normalize_library_ids(raw) -> list[str]:
    """把配置里的媒体库字段规范化为字符串列表。

    兼容旧配置：``library_id`` 为单个字符串（可为空），
    新配置 ``library_ids`` 为列表。统一返回去空后的列表。
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        raw = [raw]
    return [str(x).strip() for x in raw if str(x).strip()]


def get_job_config(job_key: str) -> dict:
    """返回任务当前配置（含 last_run_at / next_run_at）。

    优先读取 ``library_ids``（列表）；兼容旧配置的单个 ``library_id`` 字符串。
    """
    cfg = load_config().get(job_key, {})
    lib_ids = _normalize_library_ids(
        cfg.get("library_ids", cfg.get("library_id"))
    )
    return {
        "library_ids": lib_ids,
        "cron_expression": cfg.get("cron_expression", "") or "",
        "is_active": bool(cfg.get("is_active")),
        "last_run_at": cfg.get("last_run_at"),
        "next_run_at": _compute_next_run(
            cfg.get("cron_expression"), cfg.get("is_active")
        ),
    }


def save_job_config(job_key: str, data: dict) -> dict:
    """校验并保存任务配置，同步调度器，返回最新配置。

    支持 ``library_ids``（列表，多选）或旧字段 ``library_id``（单字符串），
    统一持久化为 ``library_ids`` 列表。cron_expression / is_active 照常更新，
    last_run_at 保留（执行后由 run_job 单独持久化）。
    """
    if job_key not in JOB_KEYS:
        raise ValueError(f"未知任务: {job_key}")

    cron_expr = str(data.get("cron_expression") or "")
    if not croniter.is_valid(cron_expr):
        raise ValueError(
            f"无效的 Cron 表达式: '{cron_expr}'。示例: '0 2 * * *' 表示每天凌晨 2:00"
        )

    # 兼容新旧字段：优先取 library_ids，缺省回退 library_id
    raw_ids = data.get("library_ids", data.get("library_id"))
    lib_ids = _normalize_library_ids(raw_ids)

    cur = load_config()
    job = dict(cur.get(job_key) or {})
    job["library_ids"] = lib_ids
    # 保留单值字段便于旧代码/日志读取（首项）
    job["library_id"] = lib_ids[0] if lib_ids else ""
    job["cron_expression"] = cron_expr
    job["is_active"] = bool(data.get("is_active", False))
    cur[job_key] = job
    save_config(cur)

    sync_job_scheduler(job_key)
    logger.info(
        "[MaintenanceJob] 已保存 %s libs=%s cron='%s' active=%s",
        job_key, lib_ids, cron_expr, job["is_active"],
    )
    return get_job_config(job_key)


# ---------------------------------------------------------------------------
# APScheduler 注册
# ---------------------------------------------------------------------------

def _job_id(job_key: str) -> str:
    return f"maintenance_{job_key}"


def sync_job_scheduler(job_key: str) -> None:
    """根据最新配置注册/移除 APScheduler 作业（先移除再按需添加）。"""
    from services.scheduler_service import scheduler

    jid = _job_id(job_key)
    try:
        scheduler.remove_job(jid)
    except Exception:
        pass  # job 可能不存在

    cfg = get_job_config(job_key)
    if cfg["is_active"] and cfg["library_ids"] and cfg["cron_expression"]:
        scheduler.add_job(
            _job_entry,
            trigger=CronTrigger.from_crontab(cfg["cron_expression"]),
            args=[job_key],
            id=jid,
            replace_existing=True,
        )
        logger.info(
            "[MaintenanceJob] 已注册 %s cron='%s' libs=%s",
            job_key, cfg["cron_expression"], cfg["library_ids"],
        )
    else:
        logger.info("[MaintenanceJob] %s 未启用，跳过注册", job_key)


async def _job_entry(job_key: str):
    """Cron 触发入口 — 在线程中执行，避免阻塞 event loop。"""
    logger.info("[MaintenanceJob] Cron 触发 %s", job_key)
    await asyncio.to_thread(run_job, job_key)


def load_maintenance_jobs() -> None:
    """启动时加载所有维护任务到调度器。"""
    for key in JOB_KEYS:
        try:
            sync_job_scheduler(key)
        except Exception as e:
            logger.error("[MaintenanceJob] 注册 %s 失败: %s", key, e)


# ---------------------------------------------------------------------------
# 执行入口
# ---------------------------------------------------------------------------

def run_job(job_key: str) -> dict:
    """执行一次全量汉化/审计，并持久化 last_run_at。

    支持多媒体库：对选中的每个媒体库**严格串行**执行——上一个库完成
    （成功或失败）后才开始下一个库，绝不并发，避免同时扫描导致 Emby /
    数据库 / 豆瓣接口压力过大。汇总每个库的执行结果后返回。
    """
    cfg = get_job_config(job_key)
    lib_ids = cfg["library_ids"]
    if not lib_ids:
        logger.warning("[MaintenanceJob] %s 未选择媒体库，跳过执行", job_key)
        return {"error": "未选择媒体库"}

    logger.info(
        "[MaintenanceJob] ===== 开始执行 %s libraries=%s 串行模式 =====",
        job_key, lib_ids,
    )

    results = []
    for idx, lib in enumerate(lib_ids, start=1):
        logger.info(
            "[MaintenanceJob] %s [%d/%d] 开始执行 library=%s",
            job_key, idx, len(lib_ids), lib,
        )
        try:
            if job_key == "localization_job":
                summary = _run_full_localization(lib)
            elif job_key == "overview_job":
                summary = _run_overview_translation(lib)
            else:
                summary = _run_full_audit(lib)
            summary = summary or {}
        except Exception as e:
            logger.exception(
                "[MaintenanceJob] %s library=%s 执行异常", job_key, lib
            )
            summary = {"error": str(e)}
        results.append({"library_id": lib, "result": summary})
        logger.info(
            "[MaintenanceJob] %s [%d/%d] library=%s 完成: %s",
            job_key, idx, len(lib_ids), lib, summary,
        )

    cur = load_config()
    cur.setdefault(job_key, {})["last_run_at"] = _now_iso()
    save_config(cur)

    logger.info("[MaintenanceJob] %s 全部完成: %s", job_key, results)
    return {"libraries": results}


def _run_full_audit(library_id: str) -> dict:
    """全量审计：比对 Emby 该库所有顶级媒体 ↔ 本地 media_metadata。

    找出 Emby 存在但本地缺失的媒体项，逐项走 _sync_and_audit_single_item 入库。
    单 Item 失败不影响其他 Item。
    """
    from database import SessionLocal
    from models import MediaMetadata
    from routers.sync_actions import _fetch_library_item_ids, _sync_and_audit_single_item

    cfg = load_config()
    host = (cfg.get("emby_host") or "").rstrip("/")
    api_key = cfg.get("emby_api_key") or ""
    user_id = cfg.get("emby_user_id") or ""
    if not host or not api_key:
        logger.warning("[MaintenanceJob] Emby 未配置，跳过全量审计")
        return {"error": "Emby 未配置"}

    emby_ids = _fetch_library_item_ids(host, api_key, user_id, library_id)
    logger.info(
        "[MaintenanceJob] 全量审计: library=%s Emby 共 %d 个顶级媒体项",
        library_id, len(emby_ids),
    )
    if not emby_ids:
        return {"total": 0, "missing": 0, "audited": 0, "failed": 0}

    db = SessionLocal()
    try:
        local = {
            r.emby_item_id
            for r in db.query(MediaMetadata.emby_item_id)
            .filter(MediaMetadata.emby_item_id.in_(emby_ids))
            .all()
        }
    finally:
        db.close()

    missing = [i for i in emby_ids if i not in local]
    logger.info(
        "[MaintenanceJob] 全量审计: Emby=%d 本地=%d 缺失=%d",
        len(emby_ids), len(local), len(missing),
    )

    audited = failed = 0
    for item_id in missing:
        try:
            result = _sync_and_audit_single_item(item_id, library_id=library_id)
            if result.get("success"):
                audited += 1
            else:
                failed += 1
                logger.warning(
                    "[MaintenanceJob] 审计失败 %s: %s",
                    item_id, result.get("error", "?"),
                )
        except Exception:
            failed += 1
            logger.exception("[MaintenanceJob] 审计异常 %s", item_id)

    return {
        "total": len(emby_ids),
        "missing": len(missing),
        "audited": audited,
        "failed": failed,
    }


def _run_full_localization(library_id: str) -> dict:
    """全量汉化：三位一体 — 先补齐审计缺失项，再批量汉化所有 pending 项。

    与 POST /api/douban/sinicize_all 的流程一致：
    1. 全量大盘比对（Emby ↔ 本地，缺失项审计入库）
    2. 查询该库 status=pending 的媒体项
    3. 走 _batch_sinicize_task 批量汉化（任务进度上报到大盘）
    """
    from database import SessionLocal
    from models import MediaSyncStatus
    from routers.sync_actions import _batch_sinicize_task
    from utils.task_manager import task_manager

    # 步骤 1：确保大盘数据完整（复用全量审计）
    _run_full_audit(library_id)

    # 步骤 2：查询 pending 项
    db = SessionLocal()
    try:
        pending = (
            db.query(MediaSyncStatus)
            .filter(
                MediaSyncStatus.library_id == library_id,
                MediaSyncStatus.status == "pending",
            )
            .all()
        )
        pending_ids = [p.emby_item_id for p in pending if p.emby_item_id]
    finally:
        db.close()

    if not pending_ids:
        logger.info(
            "[MaintenanceJob] 全量汉化: 媒体库 %s 没有待汉化项", library_id
        )
        return {"items": 0, "message": "该媒体库没有待汉化项"}

    # 步骤 3：批量汉化（复用 sinicize_all 的后台批处理）
    task_id = task_manager.create_task(
        total=len(pending_ids),
        message=f"全量汉化任务已启动，共 {len(pending_ids)} 项",
        metadata={"mode": "localization_job", "library_id": library_id},
    )
    _batch_sinicize_task(task_id, pending_ids)
    logger.info(
        "[MaintenanceJob] 全量汉化: task=%s items=%d", task_id, len(pending_ids),
    )
    return {"task_id": task_id, "items": len(pending_ids)}


def _run_overview_translation(library_id: str) -> dict:
    """全库简介汉化：扫描选中媒体库内所有非中文 overview，本地 qwen 优先、云端兜底。

    复用 overview_translator.scan_and_translate（单行 commit、防覆盖守卫已内建），
    库过滤经 MediaSyncStatus.library_id 桥接。
    """
    from database import SessionLocal
    from services.overview_translator import scan_and_translate

    db = SessionLocal()
    try:
        return scan_and_translate(db, library_ids=[library_id])
    finally:
        db.close()
