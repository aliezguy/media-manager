"""
Scheduler Service — APScheduler 集成 + 定时目录扫描逻辑。

- 启动时加载所有 is_active=True 的 ScheduledTask 并注册 Cron 任务
- 扫描函数通过 BFS（最大深度 4）遍历 CD2 目录树，按规则识别候选剧集目录
- 候选目录严格串行处理（逐个 auto_process_show + 3s 缓冲），单目录失败不中止后续
- 提供 add/update/remove 方法供 CRUD 路由同步调度器状态
"""

import logging
import re
import time
import traceback
from collections import deque
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from database import SessionLocal
from models import ScheduledTask, ScanRunLog
from services.cd2_service import get_client as get_cd2_client
from services.task_flow_service import auto_process_show

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
_MAX_SCAN_DEPTH = 4                # BFS 最大扫描深度
_MAX_TOTAL_VISITED = 5000          # BFS 最大访问目录数（安全阀，防死循环）
_SEQUENTIAL_BUFFER_SECONDS = 3     # 串行处理间隔（秒）
_YEAR_RE = re.compile(r'^\d{4}$')  # 匹配四位数字年份

# ---------------------------------------------------------------------------
# 全局调度器实例（单例）
# ---------------------------------------------------------------------------
scheduler = AsyncIOScheduler()


def _get_db() -> Session:
    """获取一个新的数据库会话。"""
    return SessionLocal()


# ---------------------------------------------------------------------------
# 候选目录识别
# ---------------------------------------------------------------------------
def _is_candidate(dir_name: str, parent_name: str) -> bool:
    """判定一个子目录是否为「待洗版电视剧目录」。

    满足以下 **任一** 条件即命中：
    1. 目录名包含 ``tmdb``（不区分大小写），例如 ``主角 {tmdb=284110}``。
    2. 该目录的**直接父目录**名称是一个四位年份，例如父目录名为 ``2024``。
    """
    if 'tmdb' in dir_name.lower():
        return True
    if _YEAR_RE.match(parent_name):
        return True
    return False


def _collect_candidates(root_path: str, cd2) -> tuple[list[dict], int, int]:
    """BFS 遍历 CD2 目录树，收集所有候选剧集目录。

    参数：
        root_path: CD2 起始路径。
        cd2: 已连接的 CD2Client 实例。

    返回：
        (candidates, total_visited, max_depth_reached)

        candidates — 按发现顺序排列的候选列表，每个元素包含：
            name, full_path, parent_name, depth
        total_visited — BFS 访问过的目录总数（用于统计）。
        max_depth_reached — 实际到达的最大深度。

    遍历规则：
    - 最大深度 ``_MAX_SCAN_DEPTH``（4 层），防止无限递归。
    - 使用 ``visited`` 集合按规范化路径去重，防止符号链接或挂载点导致的环路。
    - 命中候选后**不再深入该子树**（避免把 Season 1 等子目录误扫）。
    """
    candidates: list[dict] = []
    visited: set[str] = set()
    # BFS 队列: (path, parent_name, depth) — 使用 deque 实现 O(1) popleft
    queue: deque[tuple[str, str, int]] = deque()
    queue.append((root_path, "", 0))
    visited.add(root_path.rstrip("/"))

    total_visited = 0
    max_depth_reached = 0

    while queue:
        current_path, parent_name, depth = queue.popleft()
        max_depth_reached = max(max_depth_reached, depth)

        if depth >= _MAX_SCAN_DEPTH:
            continue

        # ---- 获取当前目录下的子文件/子目录 ----
        try:
            sub_files = cd2.get_sub_files(current_path)
        except Exception as e:
            logger.warning(
                "[Scheduler] CD2 get_sub_files('%s') 失败: %s", current_path, e
            )
            continue

        for f in sub_files:
            if not f.get("isDirectory"):
                continue

            dir_name: str = f.get("name", "")
            full_path: str = f.get("fullPathName", "")
            if not dir_name or not full_path:
                continue

            # 去重（规范化尾部斜杠）
            normalized = full_path.rstrip("/")
            if normalized in visited:
                continue
            visited.add(normalized)
            total_visited += 1

            # 安全阀：防止目录结构异常膨胀导致资源耗尽
            if total_visited > _MAX_TOTAL_VISITED:
                logger.warning(
                    "[Scheduler] BFS 已访问 %d 个目录（超过上限 %d），停止遍历",
                    total_visited, _MAX_TOTAL_VISITED,
                )
                queue.clear()
                break

            if _is_candidate(dir_name, parent_name):
                candidates.append({
                    "name": dir_name,
                    "full_path": full_path,
                    "parent_name": parent_name,
                    "depth": depth + 1,
                })
                # 命中候选 → 不深入子树
            else:
                # 未命中 → 继续向下探索
                if depth + 1 < _MAX_SCAN_DEPTH:
                    queue.append((full_path, dir_name, depth + 1))

    return candidates, total_visited, max_depth_reached


# ---------------------------------------------------------------------------
# 扫描核心逻辑（同步函数 — 在线程中运行，避免阻塞 event loop）
# ---------------------------------------------------------------------------
def execute_scan(task_id: int, trigger_type: str = "MANUAL") -> None:
    """执行一次目录扫描（同步，在线程中运行）。

    流程：
    1. 从 DB 加载 ScheduledTask。
    2. BFS 遍历 directory_path（最大深度 4），按规则收集候选剧集目录。
    3. **严格串行**处理每个候选目录：逐个调用 auto_process_show，
       每个之间间隔 ``_SEQUENTIAL_BUFFER_SECONDS`` 秒。
    4. 记录 ScanRunLog（含遍历统计 + 串行处理清单）。

    单个目录解析失败 **不会** 导致整个扫描任务崩溃，
    缓冲等待在异常时同样生效，确保后续剧集正常执行。
    """
    db: Session = _get_db()
    task: ScheduledTask | None = None
    log_entry: ScanRunLog | None = None

    try:
        # ================================================================
        # 阶段 1：加载任务
        # ================================================================
        task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
        if task is None:
            logger.error("[Scheduler] Task id=%s not found, aborting scan", task_id)
            return

        directory_path = task.directory_path
        logger.info(
            "[Scheduler] ===== 开始扫描 task=%d path='%s' trigger=%s =====",
            task_id, directory_path, trigger_type,
        )

        # ================================================================
        # 阶段 2：创建 ScanRunLog（preliminary）
        # ================================================================
        log_entry = ScanRunLog(
            task_id=task_id,
            status="RUNNING",
            trigger_type=trigger_type,
            scanned_count=0,
            processed_count=0,
            details={},
        )
        db.add(log_entry)
        db.commit()
        db.refresh(log_entry)

        # ================================================================
        # 阶段 3：BFS 收集候选目录
        # ================================================================
        try:
            cd2 = get_cd2_client()
        except Exception as e:
            logger.error("[Scheduler] 无法连接 CD2: %s", e)
            log_entry.status = "FAILED"
            log_entry.details = {"error": f"CD2 连接失败: {str(e)}"}
            db.commit()
            return

        try:
            candidates, total_visited, max_depth = _collect_candidates(
                directory_path, cd2
            )
        except Exception as e:
            logger.error(
                "[Scheduler] BFS 遍历失败 '%s': %s\n%s",
                directory_path, e, traceback.format_exc(),
            )
            log_entry.status = "FAILED"
            log_entry.details = {"error": f"BFS 遍历失败: {str(e)}"}
            db.commit()
            return

        candidate_count = len(candidates)
        log_entry.scanned_count = candidate_count

        logger.info(
            "[Scheduler] task=%d BFS 完成: 访问了 %d 个目录（最大深度 %d），"
            "命中 %d 个候选剧集",
            task_id, total_visited, max_depth, candidate_count,
        )

        if candidate_count == 0:
            log_entry.status = "SUCCESS"
            log_entry.processed_count = 0
            log_entry.details = {
                "traversal": {
                    "total_visited": total_visited,
                    "max_depth_reached": max_depth,
                },
                "candidates": 0,
                "processed": 0,
                "errors": 0,
                "mode": "sequential",
                "items": [],
            }
            db.commit()
            task.last_run_at = datetime.now()
            db.commit()
            logger.info("[Scheduler] task=%d 无候选目录，扫描结束", task_id)
            return

        # ================================================================
        # 阶段 4：严格串行处理
        # ================================================================
        detail_items: list[dict] = []
        processed_count = 0
        error_count = 0

        for idx, cand in enumerate(candidates, start=1):
            dir_name = cand["name"]
            logger.info(
                "[Scheduler] task=%d [%d/%d] 开始处理: '%s' (depth=%d, parent='%s')",
                task_id, idx, candidate_count, dir_name,
                cand["depth"], cand["parent_name"],
            )

            item_result = {
                "order": idx,
                "dir_name": dir_name,
                "full_path": cand["full_path"],
                "depth": cand["depth"],
                "parent_name": cand["parent_name"],
                "success": False,
                "stage": "error",
                "message": "",
            }

            try:
                r = auto_process_show(
                    torrent_name=dir_name,
                    tmdb_id=None,
                    qb_config_id="",
                    category="",
                    db=db,
                )
                item_result["success"] = r.get("success", False)
                item_result["stage"] = r.get("stage", "unknown")
                item_result["message"] = r.get("message", "")
                item_result["task_id"] = r.get("task_id")
                item_result["tmdb_id"] = r.get("tmdb_id")

                if r.get("success"):
                    processed_count += 1
                    logger.info(
                        "[Scheduler] task=%d [%d/%d] '%s' → OK stage=%s "
                        "task_flow_id=%s",
                        task_id, idx, candidate_count, dir_name,
                        item_result["stage"], r.get("task_id"),
                    )
                else:
                    error_count += 1
                    logger.warning(
                        "[Scheduler] task=%d [%d/%d] '%s' → FAILED stage=%s: %s",
                        task_id, idx, candidate_count, dir_name,
                        item_result["stage"], item_result["message"],
                    )
            except Exception as e:
                error_count += 1
                item_result["success"] = False
                item_result["stage"] = "exception"
                item_result["message"] = str(e)
                logger.error(
                    "[Scheduler] task=%d [%d/%d] '%s' → 异常: %s\n%s",
                    task_id, idx, candidate_count, dir_name,
                    e, traceback.format_exc(),
                )

            detail_items.append(item_result)

            # ---- 串行缓冲：无论成功/失败/异常，都等待后再处理下一个 ----
            if idx < candidate_count:
                logger.debug(
                    "[Scheduler] task=%d 等待 %ds 后处理下一个...",
                    task_id, _SEQUENTIAL_BUFFER_SECONDS,
                )
                time.sleep(_SEQUENTIAL_BUFFER_SECONDS)

        # ================================================================
        # 阶段 5：更新日志最终状态
        # ================================================================
        log_entry.processed_count = processed_count
        log_entry.status = "SUCCESS"  # 扫描流程本身完成（即使部分 item 失败）
        log_entry.details = {
            "traversal": {
                "total_visited": total_visited,
                "max_depth_reached": max_depth,
            },
            "candidates": candidate_count,
            "processed": processed_count,
            "errors": error_count,
            "mode": "sequential",
            "buffer_seconds": _SEQUENTIAL_BUFFER_SECONDS,
            "items": detail_items,
        }
        db.commit()

        # ---- 更新任务 last_run_at ----
        task.last_run_at = datetime.now()
        db.commit()

        logger.info(
            "[Scheduler] ===== task=%d 扫描完成 candidates=%d processed=%d "
            "errors=%d =====",
            task_id, candidate_count, processed_count, error_count,
        )

    except Exception as e:
        logger.error(
            "[Scheduler] task=%d 扫描崩溃: %s\n%s",
            task_id, e, traceback.format_exc(),
        )
        if log_entry is not None:
            try:
                log_entry.status = "FAILED"
                log_entry.details = log_entry.details or {}
                log_entry.details["fatal_error"] = str(e)
                db.commit()
            except Exception:
                pass
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 调度器生命周期管理
# ---------------------------------------------------------------------------
def _build_job_id(task_id: int) -> str:
    """为 ScheduledTask 生成唯一的 APScheduler job ID。"""
    return f"scheduled_scan_{task_id}"


async def _cron_job_wrapper(task_id: int):
    """Cron 触发时的包装函数 — 在线程中运行 execute_scan 避免阻塞 event loop。"""
    import asyncio
    logger.info("[Scheduler] Cron 触发 task=%d", task_id)
    await asyncio.to_thread(execute_scan, task_id, "CRON")


def add_job(task: ScheduledTask) -> None:
    """为已持久化的 ScheduledTask 注册 Cron 任务。"""
    job_id = _build_job_id(task.id)
    try:
        scheduler.add_job(
            _cron_job_wrapper,
            trigger=CronTrigger.from_crontab(task.cron_expression),
            args=[task.id],
            id=job_id,
            replace_existing=True,
        )
        logger.info(
            "[Scheduler] 已注册 job=%s cron='%s' path=%s",
            job_id, task.cron_expression, task.directory_path,
        )
    except Exception as e:
        logger.error(
            "[Scheduler] 注册 job=%s 失败 (cron='%s'): %s",
            job_id, task.cron_expression, e,
        )


def remove_job(task_id: int) -> None:
    """从调度器中移除指定任务的 Cron 作业。"""
    job_id = _build_job_id(task_id)
    try:
        scheduler.remove_job(job_id)
        logger.info("[Scheduler] 已移除 job=%s", job_id)
    except Exception:
        # Job may not exist in scheduler
        pass


def update_job(task: ScheduledTask) -> None:
    """更新调度任务：先移除旧作业，再根据当前状态注册。"""
    remove_job(task.id)
    if task.is_active:
        add_job(task)
    else:
        logger.info("[Scheduler] task=%d is_active=False, 跳过注册", task.id)


def load_all_tasks() -> None:
    """启动时从数据库加载所有活跃任务并注册。"""
    db: Session = _get_db()
    try:
        tasks = db.query(ScheduledTask).filter(ScheduledTask.is_active == True).all()
        for t in tasks:
            add_job(t)
        logger.info("[Scheduler] 已从数据库加载 %d 个活跃任务", len(tasks))
    except Exception as e:
        logger.error("[Scheduler] 加载任务失败: %s", e)
    finally:
        db.close()

    # ---- 注册 Case C 超时兜底检查任务（每 60 秒执行一次）----
    try:
        scheduler.add_job(
            _season_delete_timeout_job,
            trigger="interval",
            seconds=60,
            id="season_delete_timeout_check",
            replace_existing=True,
        )
        logger.info("[Scheduler] 已注册 Case C 超时兜底检查 (interval=60s)")
    except Exception as e:
        logger.error("[Scheduler] 注册超时检查任务失败: %s", e)

    # ---- 注册可配置的汉化/审计定时任务（config.json 中 localization_job/audit_job）----
    try:
        from services.maintenance_jobs import load_maintenance_jobs
        load_maintenance_jobs()
    except Exception as e:
        logger.error("[Scheduler] 注册维护任务失败: %s", e)


async def _season_delete_timeout_job():
    """超时兜底检查 — 在线程中运行以支持同步 DB 操作。"""
    import asyncio
    from services.task_flow_service import resolve_season_delete_timeouts

    def _run():
        db: Session = _get_db()
        try:
            count = resolve_season_delete_timeouts(db)
            if count > 0:
                logger.info("[TimeoutCheck] 处理了 %d 个超时的 pending Season", count)
        except Exception as e:
            logger.error("[TimeoutCheck] 执行失败: %s", e)
        finally:
            db.close()

    await asyncio.to_thread(_run)


