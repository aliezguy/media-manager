"""
Task Flow Router — Query and manage automated task flows.

GET  /api/task_flows              — list tasks (filter by status)
GET  /api/task_flows/{tmdb_id}    — get task by TMDB ID
POST /api/task_flows/{tmdb_id}/retry — reset FAILED → INIT
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc

from database import get_db
from models import AutoTaskFlow, TaskStatus

logger = logging.getLogger(__name__)

router = APIRouter()


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
