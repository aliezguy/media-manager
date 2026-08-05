"""Task 5 — 演员库路径显式 skip_llm_enrich=False + 新增 /actors/repair_overview。

对应 docs/superpowers/plans/2026-08-05-sinicize-actor-bio-optimization.md Task 5
（用户已确认范围扩展：并入 refresh/repair_missing 回归修复）：

1. 回归修复：refresh_actor(:170) 与 _batch_repair_task(:302) 补传 skip_llm_enrich=False
   —— 演员库路径显式 False，LLM 简介补全不受 actor_bio_inline_enabled 配置影响（D3）。
2. 新端点 POST /actors/repair_overview + _repair_overview_task：
   - 查询 overview 为空 或 非中文 的演员（应用层 is_valid_chinese_translation 判定）
   - 逐演员 resolve_actor_profile(skip_llm_enrich=False) 强制补全（零网络 L0 命中优先）
   - 单演员 try/except 隔离，整体 try/except/finally 防悬挂（对齐 _repair_birthplace_task）

验证维度：
  1. 3 位演员（缺简介 / 非中文简介 / 完整中文）→ 前两位进入修复列表，中文者跳过
  2. 探针断言 repair_overview / refresh / repair_missing 三处均显式传 skip_llm_enrich=False
  3. 单演员 resolve 异常不炸整体，后续演员照常处理
  4. 端点 count 统计与后台任务触发

全部探针断言，不触网、不真调 LLM。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import BackgroundTasks
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import ActorProfile
from routers import actor_router as ar
import services.actor_profile_service as aps


# ================================================================
# 共享 helpers
# ================================================================

def _make_mem_session(monkeypatch):
    """内存 SQLite + 把 actor_router.SessionLocal 指向它。"""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(ar, "SessionLocal", Session)
    return Session


def _fake_profile(name, overview=""):
    """与 refresh_actor 返回值兼容的最小 profile dict。"""
    return {
        "name": name, "local_image_path": "", "image_url": "",
        "local_image_url": "", "source": "local", "tmdb_id": "",
        "imdb_id": "", "douban_celebrity_id": "", "birth_date": "",
        "birth_place": "", "overview": overview,
    }


def _stub_resolve(monkeypatch, fn):
    """stub actor_profile_service.resolve_actor_profile（repair 任务在函数内 import 同一模块属性）。"""
    monkeypatch.setattr(aps, "resolve_actor_profile", fn)


def _no_sleep(*a, **k):
    return None


# ================================================================
# 1. _repair_overview_task：3 位演员筛选（缺 / 非中文 / 完整中文）
# ================================================================

def test_repair_overview_task_selects_missing_and_force_false(monkeypatch):
    """缺简介 + 非中文简介进入修复列表（均显式 skip_llm_enrich=False），完整中文跳过。"""
    Session = _make_mem_session(monkeypatch)
    db = Session()
    db.add_all([
        ActorProfile(name="空简介", overview=None),
        ActorProfile(name="英文简介", overview="English actor bio"),
        ActorProfile(name="中文简介", overview="这是完整的中文演员简介"),
    ])
    db.commit()
    db.close()

    calls = []

    def fake_resolve(actor_name, db, context_info=None, force_refresh=False,
                     light_mode=False, skip_llm_enrich=None):
        calls.append({"name": actor_name, "skip_llm_enrich": skip_llm_enrich})
        return _fake_profile(actor_name, overview="这是补全后的中文简介")
    _stub_resolve(monkeypatch, fake_resolve)
    monkeypatch.setattr(ar.time, "sleep", _no_sleep)

    ar._repair_overview_task("task1")

    names = [c["name"] for c in calls]
    assert set(names) == {"空简介", "英文简介"}, (
        f"仅缺失/非中文简介应进入修复列表，实际={names}"
    )
    assert "中文简介" not in names, "完整中文简介应被跳过"
    assert all(c["skip_llm_enrich"] is False for c in calls), (
        "repair_overview 必须显式传 skip_llm_enrich=False（演员库路径，不受配置影响）"
    )


def test_repair_overview_task_no_targets_succeeds(monkeypatch):
    """无待修复演员（全部中文）→ 任务正常完成，resolve 0 次调用。"""
    Session = _make_mem_session(monkeypatch)
    db = Session()
    db.add(ActorProfile(name="中文简介", overview="这是完整的中文演员简介"))
    db.commit()
    db.close()

    calls = []

    def fake_resolve(*a, **k):
        calls.append(1)
        return _fake_profile("x")
    _stub_resolve(monkeypatch, fake_resolve)
    monkeypatch.setattr(ar.time, "sleep", _no_sleep)

    ar._repair_overview_task("task_empty")

    assert calls == [], "无待修复演员时不应调用 resolve_actor_profile"


# ================================================================
# 2. _repair_overview_task：单演员异常隔离
# ================================================================

def test_repair_overview_task_isolates_failures(monkeypatch):
    """单演员 resolve 抛异常不炸整体，后续演员照常处理。"""
    Session = _make_mem_session(monkeypatch)
    db = Session()
    db.add_all([
        ActorProfile(name="会失败", overview=None),
        ActorProfile(name="会成功", overview=None),
    ])
    db.commit()
    db.close()

    calls = []

    def fake_resolve(actor_name, db, context_info=None, force_refresh=False,
                     light_mode=False, skip_llm_enrich=None):
        calls.append(actor_name)
        if actor_name == "会失败":
            raise RuntimeError("boom")
        return _fake_profile(actor_name, overview="这是补全后的中文简介")
    _stub_resolve(monkeypatch, fake_resolve)
    monkeypatch.setattr(ar.time, "sleep", _no_sleep)

    # 不应抛异常，任务以失败计数隔离单演员
    ar._repair_overview_task("task2")

    assert calls == ["会失败", "会成功"] or set(calls) == {"会失败", "会成功"}, (
        f"单个演员异常不应阻断后续演员，实际={calls}"
    )
    assert len(calls) == 2


# ================================================================
# 3. 端点：count 统计 + 后台任务触发
# ================================================================

def test_repair_overview_endpoint_counts_broken(monkeypatch):
    """缺简介 + 非中文简介计 2，完整中文不计；返回 task_id 与 count。"""
    Session = _make_mem_session(monkeypatch)
    db = Session()
    db.add_all([
        ActorProfile(name="空简介", overview=None),
        ActorProfile(name="英文简介", overview="English actor bio"),
        ActorProfile(name="中文简介", overview="这是完整的中文演员简介"),
    ])
    db.commit()
    db.close()

    resp = ar.repair_overview(BackgroundTasks())

    assert resp["count"] == 2
    assert resp["task_id"], "存在需修复演员时应创建后台任务"
    assert resp["message"]


def test_repair_overview_endpoint_all_chinese_returns_empty(monkeypatch):
    """全部中文简介 → 不创建任务，返回空 task_id + count 0。"""
    Session = _make_mem_session(monkeypatch)
    db = Session()
    db.add(ActorProfile(name="中文简介", overview="这是完整的中文演员简介"))
    db.commit()
    db.close()

    resp = ar.repair_overview(BackgroundTasks())

    assert resp["count"] == 0
    assert resp["task_id"] == ""


# ================================================================
# 4. 回归修复：refresh_actor / _batch_repair_task 显式 skip_llm_enrich=False
# ================================================================

def test_refresh_actor_forces_skip_llm_enrich_false(monkeypatch):
    """:170 refresh_actor 调用 resolve 必须显式传 skip_llm_enrich=False（D3 演员库路径）。"""
    Session = _make_mem_session(monkeypatch)
    monkeypatch.setattr(ar, "load_config", lambda: {"emby_host": "", "emby_api_key": ""})

    captured = {}

    def fake_resolve(actor_name, db, context_info=None, force_refresh=False,
                     light_mode=False, skip_llm_enrich=None):
        captured["skip_llm_enrich"] = skip_llm_enrich
        return _fake_profile("Zhang Yi")
    _stub_resolve(monkeypatch, fake_resolve)

    resp = ar.refresh_actor("Zhang Yi")

    assert resp["name"] == "Zhang Yi"
    assert captured["skip_llm_enrich"] is False, (
        "refresh_actor 必须显式传 skip_llm_enrich=False（演员库路径，不受配置影响）"
    )


def test_batch_repair_task_forces_skip_llm_enrich_false(monkeypatch):
    """:302 _batch_repair_task 调用 resolve 必须显式传 skip_llm_enrich=False（D3 演员库路径）。"""
    Session = _make_mem_session(monkeypatch)
    db = Session()
    db.add(ActorProfile(name="Zhang Yi", tmdb_id=None, overview=""))
    db.commit()
    db.close()

    monkeypatch.setattr(ar, "load_config", lambda: {"emby_host": "", "emby_api_key": ""})
    captured = {}

    def fake_resolve(actor_name, db, context_info=None, force_refresh=False,
                     light_mode=False, skip_llm_enrich=None):
        captured["skip_llm_enrich"] = skip_llm_enrich
        return _fake_profile(actor_name, overview="这是补全后的中文简介")
    _stub_resolve(monkeypatch, fake_resolve)
    monkeypatch.setattr(ar.time, "sleep", _no_sleep)

    ar._batch_repair_task("task3")

    assert captured["skip_llm_enrich"] is False, (
        "_batch_repair_task 必须显式传 skip_llm_enrich=False（演员库路径，不受配置影响）"
    )
