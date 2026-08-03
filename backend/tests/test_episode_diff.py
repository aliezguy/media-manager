"""_compute_episode_diff 测试 — 空集/新增检测纯函数。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routers.sync_actions import _compute_episode_diff


def test_no_diff():
    db = [(1, 1), (1, 2), (2, 1)]
    emby = [(1, 1), (1, 2), (2, 1)]
    assert _compute_episode_diff(db, emby) == {"missing": [], "interior_gaps": []}


def test_trailing_new_not_interior():
    # DB 有 1-7，Emby 有 1-12 → 8-12 为尾部新增，非内部空集
    db = [(1, i) for i in range(1, 8)]
    emby = [(1, i) for i in range(1, 13)]
    r = _compute_episode_diff(db, emby)
    assert r["missing"] == [(1, 8), (1, 9), (1, 10), (1, 11), (1, 12)]
    assert r["interior_gaps"] == []


def test_interior_hole_detected():
    # DB 有 1、3（缺 2），Emby 有 1-3 → 2 是中间空集
    db = [(1, 1), (1, 3)]
    emby = [(1, 1), (1, 2), (1, 3)]
    r = _compute_episode_diff(db, emby)
    assert r["missing"] == [(1, 2)]
    assert r["interior_gaps"] == [(1, 2)]


def test_new_series_all_trailing():
    # DB 空，Emby 有 1-3 → 全部缺失，无内部空集
    db = []
    emby = [(1, 1), (1, 2), (1, 3)]
    r = _compute_episode_diff(db, emby)
    assert r["missing"] == [(1, 1), (1, 2), (1, 3)]
    assert r["interior_gaps"] == []


def test_multiseason_independent():
    # 季 1 内部缺 2；季 2 尾部新增 5 — 互不影响
    db = [(1, 1), (1, 3), (2, 4)]
    emby = [(1, 1), (1, 2), (1, 3), (2, 4), (2, 5)]
    r = _compute_episode_diff(db, emby)
    assert r["interior_gaps"] == [(1, 2)]
    assert r["missing"] == [(1, 2), (2, 5)]
