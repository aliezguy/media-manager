"""_build_batch_audit_summary 测试 — 摘要以实际分集数为准。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routers.sync_actions import _build_batch_audit_summary


def test_actual_equals_tmdb():
    msg = _build_batch_audit_summary(1, 1, 1, 1, 12, 12, 5)
    assert "分集 12 集" in msg
    assert "TMDB" not in msg


def test_actual_differs_tmdb_shows_reference():
    msg = _build_batch_audit_summary(1, 1, 1, 1, 12, 30, 8)
    assert "分集 12 集（TMDB 30）" in msg


def test_zero_eps():
    msg = _build_batch_audit_summary(3, 2, 0, 0, 0, 0, 0)
    assert "分集 0 集" in msg
