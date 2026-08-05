"""全库简介汉化 — 手动触发接口测试。

覆盖 overview_router：
- POST /api/overview/translate 预扫统计 / 无待翻译短路 / 提交后台任务
- _overview_translate_task 后台任务驱动 scan_and_translate 并 complete_task

全部 mock SessionLocal / task_manager / scan_and_translate，不触网。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import MediaMetadata
import routers.overview_router as overview_router


def _mem_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


class _FakeTaskManager:
    """记录 create_task / update_progress / complete_task 调用。"""

    def __init__(self):
        self.created = []
        self.completed = []
        self.progress = []

    def create_task(self, **kw):
        self.created.append(kw)
        return "task1"

    def update_progress(self, *a, **kw):
        self.progress.append((a, kw))
        return True

    def complete_task(self, task_id, message="", success=True):
        self.completed.append((task_id, message, success))
        return True


def test_router_no_pending_short_circuits(monkeypatch):
    """无待翻译外文简介 → 直接返回，不创建任务。"""
    db = _mem_db()
    monkeypatch.setattr(overview_router, "SessionLocal", lambda: db)
    fake_tm = _FakeTaskManager()
    monkeypatch.setattr(overview_router, "task_manager", fake_tm)
    bg = BackgroundTasks()
    # 直调函数时显式传 None（FastAPI 框架会从 Query 默认提取；直调需手动）
    result = overview_router.translate_overviews(bg, library_ids=None, media_type=None)
    assert result["count"] == 0
    assert result["task_id"] == ""
    assert fake_tm.created == []
    assert len(bg.tasks) == 0


def test_router_dispatches_background_task(monkeypatch):
    """有待翻译简介 → 创建任务并调度后台任务，立即返回 task_id。"""
    db = _mem_db()
    db.add(MediaMetadata(emby_item_id="m1", media_type="Movie", title="T",
                         overview="English movie"))
    db.commit()
    monkeypatch.setattr(overview_router, "SessionLocal", lambda: db)
    fake_tm = _FakeTaskManager()
    monkeypatch.setattr(overview_router, "task_manager", fake_tm)
    bg = BackgroundTasks()
    result = overview_router.translate_overviews(bg, library_ids=None, media_type=None)
    assert result["count"] == 1
    assert result["task_id"] == "task1"
    assert fake_tm.created[0]["total"] == 1
    assert len(bg.tasks) == 1
    assert bg.tasks[0].func is overview_router._overview_translate_task


def test_background_task_runs_scan_and_completes(monkeypatch):
    """后台任务：驱动 scan_and_translate（透传过滤条件）并 complete_task(success=True)。"""
    db = _mem_db()
    monkeypatch.setattr(overview_router, "SessionLocal", lambda: db)
    fake_tm = _FakeTaskManager()
    monkeypatch.setattr(overview_router, "task_manager", fake_tm)

    stats = {"total_media": 10, "targeted": 5, "translated": 4, "skipped": 5, "failed": 1}
    calls = []

    def fake_scan(db_, **kw):
        calls.append((db_, kw))
        return stats

    monkeypatch.setattr(overview_router, "scan_and_translate", fake_scan)
    overview_router._overview_translate_task("task1", "Movie", ["libA"])

    assert calls == [(
        db,
        {"media_type": "Movie", "library_ids": ["libA"], "task_id": "task1"},
    )]
    assert len(fake_tm.completed) == 1
    task_id, message, success = fake_tm.completed[0]
    assert task_id == "task1"
    assert success is True
    assert "✅" in message and "翻译 4 条" in message


def test_background_task_failure_completes_false(monkeypatch):
    """scan_and_translate 抛异常 → complete_task(success=False)。"""
    db = _mem_db()
    monkeypatch.setattr(overview_router, "SessionLocal", lambda: db)
    fake_tm = _FakeTaskManager()
    monkeypatch.setattr(overview_router, "task_manager", fake_tm)

    def boom(db_, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(overview_router, "scan_and_translate", boom)
    overview_router._overview_translate_task("task1")

    assert len(fake_tm.completed) == 1
    assert fake_tm.completed[0][0] == "task1"
    assert fake_tm.completed[0][2] is False
