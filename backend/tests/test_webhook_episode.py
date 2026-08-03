"""webhook Episode 分支测试 — 对账父 Series + 决策汉化目标。"""
import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import routers.emby as emby


def _run(handler, payload):
    asyncio.run(handler(payload))


def test_episode_full_sync_targets_series(monkeypatch):
    targets = []
    monkeypatch.setattr(emby, "reconcile_series_episodes", lambda sid: {
        "success": True, "episodes_total": 12, "synced_episodes": 3,
        "interior_gaps": [(1, 8)], "full_sync": True,
    })
    monkeypatch.setattr(emby, "DoubanSinizer", lambda: type("S", (), {
        "sinicize": lambda self, tid: targets.append(tid) or {"success": True},
    })())
    _run(emby._handle_library_new_for_sinicize, {
        "Event": "library.new",
        "Item": {"Id": "e8", "Name": "E8", "Type": "Episode", "SeriesId": "s1"},
    })
    assert targets == ["s1"]  # 内部空集 → 整体汉化父 Series


def test_episode_light_targets_episode(monkeypatch):
    targets = []
    monkeypatch.setattr(emby, "reconcile_series_episodes", lambda sid: {
        "success": True, "episodes_total": 8, "synced_episodes": 1,
        "interior_gaps": [], "full_sync": False,
    })
    monkeypatch.setattr(emby, "DoubanSinizer", lambda: type("S", (), {
        "sinicize": lambda self, tid: targets.append(tid) or {"success": True},
    })())
    _run(emby._handle_library_new_for_sinicize, {
        "Event": "library.new",
        "Item": {"Id": "e8", "Name": "E8", "Type": "Episode", "SeriesId": "s1"},
    })
    assert targets == ["e8"]  # 仅尾部新增 → 汉化本单集


def test_series_keeps_original_path(monkeypatch):
    targets = []
    monkeypatch.setattr(emby, "_ensure_item_audited", lambda iid: True)
    monkeypatch.setattr(emby, "DoubanSinizer", lambda: type("S", (), {
        "sinicize": lambda self, tid: targets.append(tid) or {"success": True},
    })())
    _run(emby._handle_library_new_for_sinicize, {
        "Event": "library.new",
        "Item": {"Id": "s1", "Name": "九门", "Type": "Series"},
    })
    assert targets == ["s1"]
