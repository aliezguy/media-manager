"""
演职员中文化 — 系统状态 API。
"""

from fastapi import APIRouter
from services.sync_state import get_state

router = APIRouter()


@router.get("/system_status")
def system_status():
    """返回全局同步状态（前端轮询）。"""
    return get_state()
