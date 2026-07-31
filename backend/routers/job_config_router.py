"""Job Config Router — 汉化/审计定时任务配置与手动触发 API。

挂载于 /api/jobs
"""

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from services.maintenance_jobs import (
    JOB_KEYS,
    get_job_config,
    run_job,
    save_job_config,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/jobs/config")
def get_jobs_config():
    """获取两个任务的当前配置（含 last_run_at / next_run_at）。

    返回:
        {"localization_job": {...}, "audit_job": {...}}
    """
    return {k: get_job_config(k) for k in JOB_KEYS}


@router.put("/jobs/config")
def update_jobs_config(payload: dict):
    """部分更新任务配置。

    请求体例:
        {"audit_job": {"library_ids": ["1", "2"], "cron_expression": "0 4 * * *", "is_active": true}}

    支持多选媒体库（library_ids 列表），执行时逐个串行。
    兼容旧字段 library_id（单个字符串）。非法 Cron 表达式返回 400。
    """
    if not payload:
        raise HTTPException(status_code=400, detail="请求体不能为空")

    for k in JOB_KEYS:
        if k in payload and payload[k]:
            try:
                save_job_config(k, payload[k])
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))

    return {k: get_job_config(k) for k in JOB_KEYS}


@router.post("/jobs/{job_key}/run")
async def trigger_job(job_key: str):
    """手动触发一次全量审计/汉化（异步执行，立即返回）。"""
    if job_key not in JOB_KEYS:
        raise HTTPException(status_code=404, detail=f"未知任务: {job_key}")

    cfg = get_job_config(job_key)
    if not cfg["library_ids"]:
        raise HTTPException(status_code=400, detail="请先在配置中选择媒体库")

    logger.info("[Jobs] 手动触发 %s libraries=%s", job_key, cfg["library_ids"])
    asyncio.create_task(asyncio.to_thread(run_job, job_key))

    return {"success": True, "message": f"任务已触发: {job_key}"}
