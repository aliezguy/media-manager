"""
Task Dashboard Router — 自动化大盘查询接口

GET  /api/tasks                   — 分页返回 AutoTaskFlow 列表（含标题）
GET  /api/tasks/{task_id}/logs    — 返回指定任务的 TaskActionLog 时间线
GET  /api/tasks/stats             — 聚合统计卡片数据
"""

import logging

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import desc, func

from database import get_db
from models import AutoTaskFlow, TaskActionLog, TvShowDetail, TaskStatus

logger = logging.getLogger(__name__)

router = APIRouter()


def _resolve_title(task: AutoTaskFlow, db: Session) -> str:
    """从 task.context 或 TvShowDetail 表中解析剧集标题。"""
    ctx = task.context or {}
    title = ctx.get("title", "")
    if title:
        return title
    # 回退：通过 tmdb_id 查询 TvShowDetail
    detail = db.query(TvShowDetail).filter(
        TvShowDetail.tmdb_id == task.tmdb_id
    ).first()
    return detail.title if detail else f"TMDB:{task.tmdb_id}"


@router.get("/tasks/stats")
def get_task_stats(db: Session = Depends(get_db)):
    """返回任务大盘顶部统计卡片数据。"""
    total = db.query(func.count(AutoTaskFlow.id)).scalar() or 0
    completed = (
        db.query(func.count(AutoTaskFlow.id))
        .filter(AutoTaskFlow.status == TaskStatus.COMPLETED.value)
        .scalar() or 0
    )
    failed = (
        db.query(func.count(AutoTaskFlow.id))
        .filter(AutoTaskFlow.status == TaskStatus.FAILED.value)
        .scalar() or 0
    )
    waiting = (
        db.query(func.count(AutoTaskFlow.id))
        .filter(AutoTaskFlow.status == TaskStatus.WAITING_FOR_DELETE_WEBHOOK.value)
        .scalar() or 0
    )
    init_count = (
        db.query(func.count(AutoTaskFlow.id))
        .filter(AutoTaskFlow.status == TaskStatus.INIT.value)
        .scalar() or 0
    )
    return {
        "total": total,
        "completed": completed,
        "failed": failed,
        "waiting": waiting,
        "init": init_count,
    }


@router.get("/tasks")
def list_tasks(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=200, description="每页条数"),
    status: str = Query(None, description="按状态筛选，多个用逗号分隔"),
    db: Session = Depends(get_db),
):
    """分页返回 AutoTaskFlow 列表，含关联剧集标题。"""
    q = db.query(AutoTaskFlow)

    if status:
        status_list = [s.strip() for s in status.split(",") if s.strip()]
        valid_statuses = [s.value for s in TaskStatus]
        for st in status_list:
            if st not in valid_statuses:
                raise HTTPException(
                    status_code=400,
                    detail=f"无效的状态值 '{st}'，有效值: {valid_statuses}",
                )
        q = q.filter(AutoTaskFlow.status.in_(status_list))

    total = q.count()
    tasks = (
        q.order_by(desc(AutoTaskFlow.updated_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    # 批量获取所有 tmdb_id 对应的标题（一次查询避免 N+1）
    tmdb_ids = list({t.tmdb_id for t in tasks})
    title_map = {}
    if tmdb_ids:
        details = (
            db.query(TvShowDetail)
            .filter(TvShowDetail.tmdb_id.in_(tmdb_ids))
            .all()
        )
        title_map = {d.tmdb_id: d.title for d in details}

    items = []
    for task in tasks:
        title = task.context.get("title") if task.context else ""
        if not title:
            title = title_map.get(task.tmdb_id, f"TMDB:{task.tmdb_id}")
        items.append({
            "id": task.id,
            "tmdb_id": task.tmdb_id,
            "title": title,
            "task_type": task.task_type,
            "status": task.status,
            "retry_count": task.retry_count,
            "error_message": task.error_message,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/tasks/{task_id}/logs")
def get_task_logs(
    task_id: int,
    db: Session = Depends(get_db),
):
    """返回指定任务的 TaskActionLog 时间线（按 created_at 正序）。"""
    task = db.query(AutoTaskFlow).filter(AutoTaskFlow.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"任务 #{task_id} 不存在")

    logs = (
        db.query(TaskActionLog)
        .filter(TaskActionLog.task_id == task_id)
        .order_by(TaskActionLog.created_at)
        .all()
    )

    ctx = task.context or {}
    return {
        "task_id": task_id,
        "tmdb_id": task.tmdb_id,
        "total": len(logs),
        "skipped_incomplete_seasons": ctx.get("skipped_incomplete_seasons", []),
        "items": [
            {
                "id": log.id,
                "task_id": log.task_id,
                "tmdb_id": log.tmdb_id,
                "title": log.title,
                "action_type": log.action_type,
                "target_name": log.target_name,
                "target_path": log.target_path,
                "reason": log.reason,
                "detail": log.detail,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ],
    }


@router.delete("/tasks/{task_id}")
def delete_task(
    task_id: int,
    db: Session = Depends(get_db),
):
    """删除指定任务及其关联的所有操作日志。"""
    task = db.query(AutoTaskFlow).filter(AutoTaskFlow.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"任务 #{task_id} 不存在")

    # 先删除关联的 TaskActionLog（可能通过 task_id 或 tmdb_id 关联）
    deleted_logs = (
        db.query(TaskActionLog)
        .filter(TaskActionLog.task_id == task_id)
        .delete(synchronize_session="fetch")
    )

    db.delete(task)
    db.commit()

    logger.info(
        "Task #%d (tmdb=%d) deleted — %d action logs removed",
        task_id, task.tmdb_id, deleted_logs,
    )
    return {
        "success": True,
        "task_id": task_id,
        "deleted_logs": deleted_logs,
    }


class BatchDeleteBody(BaseModel):
    task_ids: List[int]


@router.post("/tasks/batch-delete")
def batch_delete_tasks(
    body: BatchDeleteBody,
    db: Session = Depends(get_db),
):
    """批量删除任务及其关联的操作日志。"""
    if not body.task_ids:
        raise HTTPException(status_code=400, detail="task_ids 不能为空")

    # 批量删除关联日志
    deleted_logs = (
        db.query(TaskActionLog)
        .filter(TaskActionLog.task_id.in_(body.task_ids))
        .delete(synchronize_session="fetch")
    )

    # 批量删除任务
    deleted_tasks = (
        db.query(AutoTaskFlow)
        .filter(AutoTaskFlow.id.in_(body.task_ids))
        .delete(synchronize_session="fetch")
    )

    db.commit()

    logger.info(
        "Batch delete: %d tasks, %d action logs removed",
        deleted_tasks, deleted_logs,
    )
    return {
        "success": True,
        "deleted_tasks": deleted_tasks,
        "deleted_logs": deleted_logs,
    }
