"""
任务状态管理器 — 线程安全单例。

用于追踪后台批量任务（如分集批量富化）的实时进度，
供前端轮询 GET /api/tasks/{task_id} 获取状态。

用法:
    manager = TaskManager()
    task_id = manager.create_task(total=5, message="开始处理")
    manager.update_progress(task_id, current=3, message="处理第3季")
    manager.complete_task(task_id, message="全部完成")
    status = manager.get_status(task_id)  # {status, total, current, message}
"""

import uuid
import threading
import time
import logging
from typing import Optional

logger = logging.getLogger("uvicorn")

# 任务过期时间（秒）：完成后保留 10 分钟再清理
_TASK_TTL = 600


class TaskManager:
    """线程安全的任务状态管理器（单例模式）。"""

    _instance: Optional["TaskManager"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "TaskManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._tasks = {}
                    cls._instance._task_lock = threading.Lock()
        return cls._instance

    # ---- 基础 CRUD ----

    def create_task(
        self,
        total: int = 0,
        message: str = "",
        metadata: dict | None = None,
    ) -> str:
        """创建新任务并返回 task_id。

        Args:
            total:    总步数（如总季数）
            message:  初始描述
            metadata: 额外信息（如 item_id, item_name）

        Returns:
            唯一的 task_id (UUID hex)
        """
        task_id = uuid.uuid4().hex[:12]
        now = time.time()
        with self._task_lock:
            self._tasks[task_id] = {
                "status": "running",
                "total": total,
                "current": 0,
                "message": message,
                "metadata": metadata or {},
                "created_at": now,
                "updated_at": now,
            }
        logger.info(
            "📋 [TaskManager] 创建任务 %s: total=%d, message=%s",
            task_id, total, message,
        )
        return task_id

    def update_progress(
        self,
        task_id: str,
        current: int = None,
        message: str = None,
        increment: int = 0,
        total: int = None,
        status: str = None,
    ) -> bool:
        """更新任务进度。

        Args:
            task_id:   任务 ID
            current:   当前进度（绝对值），为 None 则不更新
            message:   新的描述信息，为 None 则不更新
            increment: 进度增量（累加到 current）
            total:     新的总步数，为 None 则不更新（支持运行时动态调整总量）
            status:    任务状态，为 None 则不更新（支持中途置为 error 等异常状态）

        Returns:
            True 如果任务存在并成功更新，False 如果任务不存在
        """
        with self._task_lock:
            task = self._tasks.get(task_id)
            if not task:
                logger.warning("   ⚠ [TaskManager] 任务 %s 不存在", task_id)
                return False
            if current is not None:
                task["current"] = current
            if increment:
                task["current"] = task.get("current", 0) + increment
            if total is not None:
                task["total"] = total
            if message is not None:
                task["message"] = message
            if status is not None:
                task["status"] = status
            task["updated_at"] = time.time()
        return True

    def complete_task(self, task_id: str, message: str = "", success: bool = True) -> bool:
        """标记任务完成。

        自动将 current 对齐到 total，确保前端进度条显示 100%。

        Args:
            task_id: 任务 ID
            message: 完成描述
            success: True → status='completed', False → status='error'

        Returns:
            True 如果任务存在并成功标记
        """
        with self._task_lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            task["status"] = "completed" if success else "error"
            # ★ 强制对齐：无论实际执行到哪一步，完成时 current == total
            task["current"] = task.get("total", task.get("current", 0))
            task["message"] = message
            task["updated_at"] = time.time()
        logger.info(
            "✅ [TaskManager] 任务 %s %s: %s",
            task_id, task["status"], message,
        )
        return True

    def get_status(self, task_id: str) -> dict | None:
        """获取任务状态。

        Returns:
            {
                "status": "running|completed|error",
                "total": int,
                "current": int,
                "message": str,
                "metadata": dict,
            }
            任务不存在返回 None。
        """
        with self._task_lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            return {
                "status": task["status"],
                "total": task["total"],
                "current": task["current"],
                "message": task["message"],
                "metadata": task.get("metadata", {}),
            }

    # ---- 维护 ----

    def cleanup_expired(self) -> int:
        """清理已完成且过期的任务，返回清理数量。"""
        now = time.time()
        removed = 0
        with self._task_lock:
            expired = [
                tid for tid, t in self._tasks.items()
                if t["status"] in ("completed", "error")
                and now - t["updated_at"] > _TASK_TTL
            ]
            for tid in expired:
                del self._tasks[tid]
                removed += 1
        if removed:
            logger.info("🧹 [TaskManager] 清理 %d 个过期任务", removed)
        return removed


# 模块级单例，供外部直接导入使用
task_manager = TaskManager()
