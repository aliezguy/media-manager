"""
演职员中文化 — 全局同步状态管理（防并发）。
前端轮询 GET /api/system_status 以获取进度。
"""

from threading import Lock

_lock = Lock()

GLOBAL_SYNC_STATE = {
    "is_running": False,
    "progress": 0,
    "total": 0,
    "current_task": "",
    "library_id": "",
}


def start_sync(total: int, library_id: str = ""):
    """标记同步开始。"""
    with _lock:
        GLOBAL_SYNC_STATE["is_running"] = True
        GLOBAL_SYNC_STATE["progress"] = 0
        GLOBAL_SYNC_STATE["total"] = total
        GLOBAL_SYNC_STATE["current_task"] = ""
        GLOBAL_SYNC_STATE["library_id"] = library_id


def update_progress(progress: int, task: str = ""):
    """更新进度和当前任务名。"""
    with _lock:
        GLOBAL_SYNC_STATE["progress"] = progress
        if task:
            GLOBAL_SYNC_STATE["current_task"] = task


def finish_sync():
    """标记同步结束。"""
    with _lock:
        GLOBAL_SYNC_STATE["is_running"] = False
        GLOBAL_SYNC_STATE["progress"] = GLOBAL_SYNC_STATE["total"]
        GLOBAL_SYNC_STATE["current_task"] = ""


def get_state() -> dict:
    """获取当前状态快照（线程安全）。"""
    with _lock:
        return dict(GLOBAL_SYNC_STATE)
