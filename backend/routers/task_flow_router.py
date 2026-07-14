"""
Task Flow Router — Query and manage automated task flows.

GET  /api/task_flows              — list tasks (filter by status)
GET  /api/task_flows/{tmdb_id}    — get task by TMDB ID
POST /api/task_flows/{tmdb_id}/retry — reset FAILED → INIT
POST /api/tasks/{task_id}/force-move-season — 手动强制移动 organized → media
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import desc

from database import get_db
from models import AutoTaskFlow, TaskStatus, TaskActionLog, ActionType
from services.cd2_service import get_cd2_client

logger = logging.getLogger(__name__)

router = APIRouter()


class ForceMoveSeasonBody(BaseModel):
    season: int


def _sanitize_cd2_path(path: str) -> str:
    """Normalize CD2 paths by collapsing double slashes and trailing slashes."""
    while "//" in path:
        path = path.replace("//", "/")
    return path.rstrip("/")


@router.get("/task_flows")
def list_task_flows(
    status: str = Query(None, description="Filter by status: INIT, WAITING_FOR_DELETE_WEBHOOK, etc."),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """List recent task flows, optionally filtered by status."""
    q = db.query(AutoTaskFlow)
    if status:
        # Validate status against enum
        valid_statuses = [s.value for s in TaskStatus]
        if status not in valid_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status '{status}'. Valid: {valid_statuses}",
            )
        q = q.filter(AutoTaskFlow.status == status)
    return q.order_by(desc(AutoTaskFlow.updated_at)).limit(limit).all()


@router.get("/task_flows/{tmdb_id}")
def get_task_flow(tmdb_id: int, db: Session = Depends(get_db)):
    """Get the latest task flow for a given TMDB ID."""
    task = (
        db.query(AutoTaskFlow)
        .filter(AutoTaskFlow.tmdb_id == tmdb_id)
        .order_by(desc(AutoTaskFlow.created_at))
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail=f"No task found for tmdb_id={tmdb_id}")
    return task


@router.post("/task_flows/{tmdb_id}/retry")
def retry_task_flow(tmdb_id: int, db: Session = Depends(get_db)):
    """Reset a FAILED task back to INIT for re-processing."""
    task = (
        db.query(AutoTaskFlow)
        .filter(
            AutoTaskFlow.tmdb_id == tmdb_id,
            AutoTaskFlow.status == TaskStatus.FAILED.value,
        )
        .order_by(desc(AutoTaskFlow.created_at))
        .first()
    )
    if not task:
        raise HTTPException(
            status_code=404,
            detail=f"No FAILED task found for tmdb_id={tmdb_id}",
        )

    task.status = TaskStatus.INIT.value
    task.retry_count = (task.retry_count or 0) + 1
    task.error_message = None
    db.commit()

    logger.info(f"TaskFlow retry: tmdb={tmdb_id} reset to INIT (attempt #{task.retry_count})")
    return {
        "status": "reset",
        "task_id": task.id,
        "tmdb_id": tmdb_id,
        "retry_count": task.retry_count,
    }


@router.post("/tasks/{task_id}/force-move-season")
def force_move_season(
    task_id: int,
    body: ForceMoveSeasonBody,
    db: Session = Depends(get_db),
):
    """手动强制移动：将已完结目录中的 Season 移动到媒体库。

    使用场景：已完结 Season 不完整（文件数 < TMDB 预期），
    自动化流程跳过处理。用户确认后手动触发强制移动。
    """
    task = db.query(AutoTaskFlow).filter(AutoTaskFlow.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"任务 #{task_id} 不存在")

    ctx = task.context or {}
    skipped = ctx.get("skipped_incomplete_seasons", [])
    if not skipped:
        raise HTTPException(
            status_code=400,
            detail="该任务没有可强制移动的 Season（skipped_incomplete_seasons 为空）",
        )

    # 查找匹配的 Season
    target = None
    for s in skipped:
        if s.get("season") == body.season:
            target = s
            break

    if target is None:
        seasons = [s.get("season") for s in skipped]
        raise HTTPException(
            status_code=404,
            detail=f"Season {body.season} 不在可移动列表中，可移动的 Season: {seasons}",
        )

    title = ctx.get("title", f"TMDB:{task.tmdb_id}")
    org_path = target["organized_path"]
    org_name = target["organized_dir_name"]
    media_path = target["media_path"]
    season_num = target["season"]

    if not org_path:
        raise HTTPException(
            status_code=400,
            detail=f"S{season_num} organized_path 为空，无法移动",
        )

    # 获取 media 父目录路径（media_path 去掉最后一层目录名）
    media_parent = _sanitize_cd2_path("/".join(media_path.rstrip("/").split("/")[:-1]))

    cd2 = get_cd2_client()

    # Step 1: 删除媒体库目标目录（如果存在）
    try:
        media_files = cd2.get_sub_files(media_path)
        if media_files:
            logger.info(
                "[ForceMove] task=%d '%s' S%d — 删除媒体库现有目录: %s",
                task_id, title, season_num, media_path,
            )
            del_result = cd2.delete_files([media_path])
            if not del_result.get("success"):
                logger.warning(
                    "[ForceMove] task=%d '%s' S%d — 删除媒体库目录失败（可能已被删除）: %s",
                    task_id, title, season_num, del_result.get("errorMessage", ""),
                )
    except Exception as e:
        logger.warning(
            "[ForceMove] task=%d '%s' S%d — 检查/删除媒体库目录异常: %s",
            task_id, title, season_num, e,
        )

    # Step 2: 移动 organized → media
    logger.info(
        "[ForceMove] task=%d '%s' S%d — 移动 '%s' → '%s'",
        task_id, title, season_num, org_path, media_parent,
    )
    move_result = cd2.move_files([org_path], media_parent, conflict_policy=0)  # Overwrite

    if not move_result.get("success"):
        error_msg = move_result.get("errorMessage", "unknown")
        logger.error(
            "[ForceMove] task=%d '%s' S%d — 移动失败: %s",
            task_id, title, season_num, error_msg,
        )
        _write_action_log(
            db, task.id, task.tmdb_id, title,
            ActionType.FORCE_MOVE.value,
            target_name=f"S{season_num} - {org_name}",
            target_path=org_path,
            reason=f"手动强制移动失败: {error_msg}",
        )
        raise HTTPException(
            status_code=500,
            detail=f"CD2 移动失败: {error_msg}",
        )

    # Step 3: 写入操作日志
    _write_action_log(
        db, task.id, task.tmdb_id, title,
        ActionType.FORCE_MOVE.value,
        target_name=f"S{season_num} - {org_name}",
        target_path=org_path,
        reason=(
            f"手动强制移动: organized ({target['organized_file_count']}/{target['expected_file_count']})"
            f" → media ({target['media_file_count']}/{target['expected_file_count']})"
        ),
        detail={
            "season": season_num,
            "organized_path": org_path,
            "media_path": media_path,
            "media_parent": media_parent,
            "move_result": move_result,
        },
    )

    logger.info(
        "[ForceMove] task=%d '%s' S%d — 强制移动成功",
        task_id, title, season_num,
    )

    return {
        "success": True,
        "task_id": task_id,
        "season": season_num,
        "organized_path": org_path,
        "media_parent": media_parent,
        "message": f"Season {season_num} 已从已完结移动到媒体库",
    }


def _write_action_log(
    db: Session,
    task_id: int | None,
    tmdb_id: int,
    title: str,
    action_type: str,
    target_name: str,
    target_path: str,
    reason: str = "",
    detail: dict | None = None,
):
    """写入操作日志（本地 helper，避免跨模块导入 task_flow_service 中的同名函数）。"""
    log_entry = TaskActionLog(
        task_id=task_id,
        tmdb_id=tmdb_id,
        title=title,
        action_type=action_type,
        target_name=target_name,
        target_path=target_path,
        reason=reason,
        detail=detail,
        created_at=datetime.now(),
    )
    db.add(log_entry)
    db.commit()
