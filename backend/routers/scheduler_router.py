"""
Scheduled Tasks Router — 定时扫描任务的 CRUD、手动执行与日志查询 API。

挂载于 /api/scheduled-tasks
"""

import logging
from datetime import datetime
from typing import Optional, List

from croniter import croniter
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import desc

from database import get_db
from models import ScheduledTask, ScanRunLog
from services.scheduler_service import (
    add_job,
    update_job,
    remove_job,
    execute_scan,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ScheduledTaskCreate(BaseModel):
    directory_path: str
    cron_expression: str
    is_active: bool = True


class ScheduledTaskUpdate(BaseModel):
    directory_path: Optional[str] = None
    cron_expression: Optional[str] = None
    is_active: Optional[bool] = None


class ScheduledTaskOut(BaseModel):
    id: int
    directory_path: str
    cron_expression: str
    is_active: bool
    last_run_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ScanRunLogOut(BaseModel):
    id: int
    task_id: int
    status: str
    trigger_type: str
    scanned_count: int
    processed_count: int
    details: Optional[dict] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _validate_cron(expression: str) -> None:
    """校验 Cron 表达式合法性，非法时抛出 HTTPException。"""
    if not croniter.is_valid(expression):
        raise HTTPException(
            status_code=400,
            detail=f"无效的 Cron 表达式: '{expression}'。"
                   f"示例: '0 2 * * *' 表示每天凌晨 2:00",
        )


# ---------------------------------------------------------------------------
# CRUD Endpoints
# ---------------------------------------------------------------------------

@router.get("/scheduled-tasks", response_model=List[ScheduledTaskOut])
def list_tasks(db: Session = Depends(get_db)):
    """获取所有定时任务列表（按创建时间倒序）。"""
    tasks = (
        db.query(ScheduledTask)
        .order_by(desc(ScheduledTask.created_at))
        .all()
    )
    return tasks


@router.post("/scheduled-tasks", response_model=ScheduledTaskOut)
def create_task(req: ScheduledTaskCreate, db: Session = Depends(get_db)):
    """创建新的定时扫描任务。

    校验 Cron 表达式合法性后持久化到数据库，并在调度器中注册。
    """
    _validate_cron(req.cron_expression)

    task = ScheduledTask(
        directory_path=req.directory_path,
        cron_expression=req.cron_expression,
        is_active=req.is_active,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    # 注册到调度器
    if task.is_active:
        add_job(task)

    logger.info(
        "[SchedulerRouter] 创建 task=%d path=%s cron='%s'",
        task.id, task.directory_path, task.cron_expression,
    )
    return task


@router.put("/scheduled-tasks/{task_id}", response_model=ScheduledTaskOut)
def update_task(task_id: int, req: ScheduledTaskUpdate, db: Session = Depends(get_db)):
    """更新定时扫描任务。

    可部分更新：路径、Cron 表达式、启停状态。
    更新后自动同步底层 APScheduler 作业。
    """
    task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务 #{task_id} 不存在")

    if req.directory_path is not None:
        task.directory_path = req.directory_path
    if req.cron_expression is not None:
        _validate_cron(req.cron_expression)
        task.cron_expression = req.cron_expression
    if req.is_active is not None:
        task.is_active = req.is_active

    db.commit()
    db.refresh(task)

    # 同步调度器
    update_job(task)

    logger.info(
        "[SchedulerRouter] 更新 task=%d is_active=%s cron='%s'",
        task.id, task.is_active, task.cron_expression,
    )
    return task


@router.delete("/scheduled-tasks/{task_id}")
def delete_task(task_id: int, db: Session = Depends(get_db)):
    """删除定时扫描任务，同时从调度器中移除。"""
    task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务 #{task_id} 不存在")

    # 先从调度器移除
    remove_job(task_id)

    # 删除关联的扫描日志
    db.query(ScanRunLog).filter(ScanRunLog.task_id == task_id).delete()

    db.delete(task)
    db.commit()

    logger.info("[SchedulerRouter] 删除 task=%d", task_id)
    return {"success": True, "message": f"任务 #{task_id} 已删除"}


# ---------------------------------------------------------------------------
# 手动触发
# ---------------------------------------------------------------------------

@router.post("/scheduled-tasks/{task_id}/run")
async def trigger_manual_run(task_id: int, db: Session = Depends(get_db)):
    """手动触发一次扫描（异步执行，无需等待 Cron 触发）。"""
    task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务 #{task_id} 不存在")

    logger.info("[SchedulerRouter] 手动触发 task=%d path=%s", task_id, task.directory_path)

    # 在线程中执行扫描（不阻塞 HTTP 响应和 event loop）
    import asyncio
    asyncio.create_task(asyncio.to_thread(execute_scan, task_id, "MANUAL"))

    return {
        "success": True,
        "message": f"任务 #{task_id} 手动扫描已触发",
        "task_id": task_id,
    }


# ---------------------------------------------------------------------------
# 日志查询
# ---------------------------------------------------------------------------

@router.get("/scheduled-tasks/{task_id}/logs", response_model=List[ScanRunLogOut])
def get_task_logs(
    task_id: int,
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """获取指定任务的扫描运行日志（按时间倒序）。"""
    task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务 #{task_id} 不存在")

    logs = (
        db.query(ScanRunLog)
        .filter(ScanRunLog.task_id == task_id)
        .order_by(desc(ScanRunLog.created_at))
        .limit(limit)
        .all()
    )
    return logs
