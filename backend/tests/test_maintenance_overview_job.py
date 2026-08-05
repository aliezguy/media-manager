"""overview_job 定时任务 + 配置 — Task 7 测试。

覆盖：
- settings.DEFAULT_CONFIG 含 overview_job / overview_* 键（默认值正确）
- maintenance_jobs.JOB_KEYS 含 overview_job
- save_job_config("overview_job", ...) 校验 / 持久化 / 同步调度器
- run_job("overview_job") → _run_overview_translation → scan_and_translate(library_ids)

全程 mock 配置读写与扫描层，绝不读写磁盘 config.json，不触网。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


# ================================================================
# 配置默认值
# ================================================================

def test_default_config_contains_overview_keys():
    from config.settings import DEFAULT_CONFIG
    job = DEFAULT_CONFIG["overview_job"]
    assert job["library_ids"] == []
    assert job["cron_expression"] == "0 5 * * *"
    assert job["is_active"] is False
    assert job["last_run_at"] is None
    assert DEFAULT_CONFIG["overview_translation_enabled"] is True
    assert DEFAULT_CONFIG["overview_local_first"] is True
    assert DEFAULT_CONFIG["overview_chinese_ratio"] == 0.5
    assert DEFAULT_CONFIG["overview_max_tokens"] == 1500


# ================================================================
# JOB_KEYS
# ================================================================

def test_job_keys_include_overview():
    from services.maintenance_jobs import JOB_KEYS
    assert "overview_job" in JOB_KEYS


# ================================================================
# save_job_config — overview_job 校验 / 持久化
# ================================================================

def test_save_job_config_overview_job(monkeypatch):
    from services import maintenance_jobs as mj
    cfg = {}
    monkeypatch.setattr(mj, "load_config", lambda: cfg)
    monkeypatch.setattr(mj, "save_config", lambda cur: cfg.update(cur))
    monkeypatch.setattr(mj, "sync_job_scheduler", lambda key: None)

    result = mj.save_job_config(
        "overview_job",
        {"library_ids": ["L1"], "cron_expression": "0 6 * * *", "is_active": True},
    )
    assert result["library_ids"] == ["L1"]
    assert cfg["overview_job"]["cron_expression"] == "0 6 * * *"
    assert cfg["overview_job"]["is_active"] is True
    # 旧单值字段兼容保留
    assert cfg["overview_job"]["library_id"] == "L1"


def test_save_job_config_overview_rejects_unknown_key(monkeypatch):
    from services import maintenance_jobs as mj
    monkeypatch.setattr(mj, "load_config", lambda: {})
    monkeypatch.setattr(mj, "save_config", lambda cur: None)
    with pytest.raises(ValueError):
        mj.save_job_config("not_a_job", {})


# ================================================================
# run_job — overview_job → _run_overview_translation → scan_and_translate
# ================================================================

def test_run_job_overview_calls_scan_and_translate(monkeypatch):
    from services import maintenance_jobs as mj

    cfg = {
        "overview_job": {
            "library_ids": ["L1"],
            "cron_expression": "0 5 * * *",
            "is_active": True,
            "last_run_at": None,
        }
    }
    monkeypatch.setattr(mj, "load_config", lambda: cfg)
    monkeypatch.setattr(mj, "save_config", lambda cur: cfg.update(cur))

    # mock 扫描层：_run_overview_translation 内 inline import
    class _FakeDB:
        def close(self):
            pass

    monkeypatch.setattr("database.SessionLocal", lambda: _FakeDB())
    called = {}

    def fake_scan(db, media_type=None, library_ids=None, task_id=None):
        called["libs"] = library_ids
        return {
            "total_media": 2, "targeted": 1,
            "translated": 1, "skipped": 1, "failed": 0,
        }

    monkeypatch.setattr("services.overview_translator.scan_and_translate", fake_scan)

    result = mj.run_job("overview_job")

    assert called["libs"] == ["L1"]
    assert result["libraries"][0]["library_id"] == "L1"
    assert result["libraries"][0]["result"]["translated"] == 1
    # last_run_at 被持久化
    assert cfg["overview_job"]["last_run_at"] is not None
