"""批量补齐分集简介 + 写回 Emby — POST /api/sync/repair_episode_overviews。

需求（断点待办）:
  让用户对指定剧（item_ids）或全库（不传）一次性补齐分集简介：
  Emby 拉全部分集 → 逐集 LLM 翻译非中文简介（本地 qwen→云端兜底）→
  写回 Emby（People 原样回传，不动演员）→ 落库审计（overview_source/updated_at）。

对应实现（routers/sync_actions.py）:
  - RepairEpisodeOverviewsRequest(item_ids: list[str] = []) — 空 = 全库扫描
  - repair_episode_overviews() — 端点：预检总开关 → 解析目标剧 → 后台任务
  - _resolve_target_series(db, item_ids) — 显式 id 去空去重；空 → distinct Episode.parent_id
  - _repair_episode_overviews_task(task_id, series_ids) — 后台任务（sentinel 三防线）
  - _repair_series_episode_overviews(sinizer, db, series_id) — 单剧：拉取→翻译→写回→落库
  - _patch_episode_overview_db(db, ep_id, series_id, ep, source) — UPSERT MediaMetadata 审计

设计要点:
  - 尊重总开关 overview_translation_enabled（关闭 → 端点 400 / 任务终止）；
    ★ 不受 sinicize_translate_episode_overviews 限制（任务内显式
    sinizer.translate_episode_overviews = True，显式修复是独立动作）
  - 落库不走 save_media_to_db（会清掉 MediaSyncStatus 演员计数），直接补丁 MediaMetadata；
    仅 Emby 写回成功后才落库。
  - 逐集 try/except 隔离，单集失败不影响其他集。

全部 Boom/探针断言，不触网、不真调 LLM。
"""
import sys
import os
import copy
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import MediaMetadata
import routers.sync_actions as sa
import services.douban_service as ds


# ================================================================
# 共享常量与 helpers
# ================================================================

_CFG = {
    "emby_host": "http://emby.test", "emby_api_key": "k",
    "emby_user_id": "u", "max_actors_per_media": 50,
}

_CFG_MASTER_OFF = dict(_CFG, overview_translation_enabled=False)
_CFG_SINICIZE_FLAG_OFF = dict(_CFG, sinicize_translate_episode_overviews=False)

_JA_OVERVIEW = "今日は日本語のあらすじ。敵が現れる。"
_ZH_OVERVIEW = "这是一个翻译后的中文分集简介。"
_ZH_ALREADY = "这是已汉化的中文分集简介。"

_EPISODE_JA = {
    "Id": "e1", "Name": "EP1", "ParentIndexNumber": 1, "IndexNumber": 1,
    "Overview": _JA_OVERVIEW,
    "People": [{"Name": "Sun", "Type": "Actor", "Role": "Li"}],
}
_EPISODE_ZH = {
    "Id": "e2", "Name": "EP2", "ParentIndexNumber": 1, "IndexNumber": 2,
    "Overview": _ZH_ALREADY, "People": [],
}

_EPISODES_MIXED = [_EPISODE_JA, _EPISODE_ZH]
_EPISODES_ALL_JA = [_EPISODE_JA]


class _FakeTaskManager:
    """记录 create_task / update_progress / complete_task 调用（沿用 overview_router 测试）。"""

    def __init__(self):
        self.created = []
        self.completed = []
        self.progress = []

    def create_task(self, **kw):
        self.created.append(kw)
        return "task_repair"

    def update_progress(self, *a, **kw):
        self.progress.append((a, kw))
        return True

    def complete_task(self, task_id, message="", success=True):
        self.completed.append((task_id, message, success))
        return True


def _make_db(tmp_path, fname="repair.db"):
    """文件级 SQLite：任务内单 SessionLocal 共享同一 DB，规避 :memory: 多连接问题。"""
    engine = create_engine(f"sqlite:///{tmp_path}/{fname}")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def _make_sinizer(monkeypatch, cfg=None, episodes=None, translate=None, write_ep=None):
    """真实 DoubanSinizer 实例 + stub 下游（沿用 sinicize 测试手法）。

    - _fetch_episodes → deepcopy 固定集列表（翻译会就地改 ep['Overview']，防污染）
    - ds.translate_overview → 可选 stub（默认不动，由测试显式给）
    - _write_back_episode → 可选 stub（默认不动）
    """
    monkeypatch.setattr(ds, "load_config", lambda: (cfg or _CFG))
    s = ds.DoubanSinizer()
    s.translate_episode_overviews = True  # 显式修复：绕过 sinicize 开关（任务内同样强制开启）
    monkeypatch.setattr(
        s, "_fetch_episodes",
        lambda series_id: copy.deepcopy(episodes if episodes is not None else _EPISODES_MIXED),
    )
    if translate is not None:
        monkeypatch.setattr(ds, "translate_overview", translate)
    # 默认 stub 写回（不触网）；测试可传入自定义捕获/失败 stub
    if write_ep is None:
        write_ep = lambda *a, **k: True
    monkeypatch.setattr(s, "_write_back_episode", write_ep)
    return s


def _translate_ok(text):
    return (_ZH_OVERVIEW, "local_llm", set())


def _translate_fail(text):
    return (None, "failed", set())


# ================================================================
# 1. _resolve_target_series：显式 id / 全库扫描
# ================================================================

def test_resolve_target_series_explicit_ids(monkeypatch, tmp_path):
    """显式 item_ids → 去空去重保序，直接返回。"""
    Session = _make_db(tmp_path)
    db = Session()
    try:
        targets = sa._resolve_target_series(db, ["s2", "s1", "s1", "", "  "])
    finally:
        db.close()
    assert targets == ["s2", "s1"], f"显式 id 应去空去重保序，实际={targets}"


def test_resolve_target_series_empty_scans_episodes(monkeypatch, tmp_path):
    """空 item_ids → 扫描 media_metadata 中 Episode 行的 distinct parent_id。"""
    Session = _make_db(tmp_path, "scan.db")
    db = Session()
    try:
        db.add_all([
            MediaMetadata(emby_item_id="e1", parent_id="s1", media_type="Episode"),
            MediaMetadata(emby_item_id="e2", parent_id="s1", media_type="Episode"),
            MediaMetadata(emby_item_id="e3", parent_id="s2", media_type="Episode"),
            MediaMetadata(emby_item_id="m1", parent_id=None, media_type="Movie"),
            MediaMetadata(emby_item_id="s3", parent_id=None, media_type="Series"),
        ])
        db.commit()
        targets = sa._resolve_target_series(db, [])
    finally:
        db.close()
    assert set(targets) == {"s1", "s2"}, f"全库模式应命中有分集记录的 Series，实际={targets}"


# ================================================================
# 2. _repair_series_episode_overviews：单剧补齐流程
# ================================================================

def test_repair_series_translates_writes_back_and_persists(monkeypatch, tmp_path):
    """日文集 → 翻译 → Emby 写回（People 原样 + 中文 Overview）→ 落库审计；
    已中文集不动、不落库。"""
    Session = _make_db(tmp_path)
    translate_calls = []
    captured = {}

    def fake_translate(text):
        translate_calls.append(text)
        return (_ZH_OVERVIEW, "local_llm", set())

    def fake_write_ep(ep_id, ep_data, people):
        captured["ep_id"] = ep_id
        captured["overview"] = ep_data.get("Overview", "")
        captured["people"] = people
        return True

    s = _make_sinizer(monkeypatch, translate=fake_translate, write_ep=fake_write_ep)

    db = Session()
    try:
        stats = sa._repair_series_episode_overviews(s, db, "s1")
    finally:
        db.close()

    # --- 只翻译了日文集，入参即日文原文 ---
    assert translate_calls == [_JA_OVERVIEW], f"只应翻译日文集，实际={translate_calls}"
    # --- Emby 写回：1 次，携带中文 Overview，People 原样回传 ---
    assert captured["ep_id"] == "e1"
    assert captured["overview"] == _ZH_OVERVIEW, (
        f"Emby 写回应携带中文 Overview，实际={captured['overview']!r}"
    )
    assert captured["people"] == _EPISODE_JA["People"], "分集修复不得改动演员 People"
    # --- 统计 ---
    assert stats == {"total": 2, "translated": 1, "skipped": 1, "failed": 0}

    # --- 落库：e1 审计标记；e2（已中文）不落库 ---
    db = Session()
    try:
        rec = db.query(MediaMetadata).filter(MediaMetadata.emby_item_id == "e1").first()
        assert rec is not None, "翻译成功的分集应落库"
        assert rec.overview == _ZH_OVERVIEW
        assert rec.overview_source == "local_llm", (
            f"LLM 翻译产物应标记 local_llm，实际={rec.overview_source!r}"
        )
        assert rec.overview_updated_at is not None, "overview_updated_at 应被打点"
        assert rec.parent_id == "s1" and rec.media_type == "Episode"
        assert db.query(MediaMetadata).filter(
            MediaMetadata.emby_item_id == "e2"
        ).first() is None, "未翻译的已中文集不应落库"
    finally:
        db.close()


def test_repair_series_translation_failure_counts_failed(monkeypatch, tmp_path):
    """需要翻译但 translate_overview 失败 → failed 计数，不写回、不落库、不崩溃。"""
    Session = _make_db(tmp_path, "fail.db")
    write_called = []

    def fake_write_ep(*a, **k):
        write_called.append(a)
        return True

    s = _make_sinizer(monkeypatch, translate=_translate_fail, write_ep=fake_write_ep)
    db = Session()
    try:
        stats = sa._repair_series_episode_overviews(s, db, "s1")
    finally:
        db.close()

    assert stats["failed"] == 1, f"翻译失败应计 failed，实际={stats}"
    assert stats["translated"] == 0
    assert write_called == [], "翻译失败不得写回 Emby"
    db = Session()
    try:
        assert db.query(MediaMetadata).filter(
            MediaMetadata.emby_item_id == "e1"
        ).first() is None
    finally:
        db.close()


def test_repair_series_writeback_failure_not_persisted(monkeypatch, tmp_path):
    """翻译成功但 Emby 写回失败 → failed 计数，不落库（DB 与 Emby 保持一致）。"""
    Session = _make_db(tmp_path, "wb_fail.db")
    s = _make_sinizer(
        monkeypatch, translate=_translate_ok,
        write_ep=lambda *a, **k: False,
    )
    db = Session()
    try:
        stats = sa._repair_series_episode_overviews(s, db, "s1")
    finally:
        db.close()
    assert stats["failed"] == 1 and stats["translated"] == 0
    db = Session()
    try:
        assert db.query(MediaMetadata).filter(
            MediaMetadata.emby_item_id == "e1"
        ).first() is None, "写回失败不得落库"
    finally:
        db.close()


def test_repair_series_skips_episode_without_id(monkeypatch, tmp_path):
    """缺少 Id 的分集跳过（不翻译不崩溃）。"""
    Session = _make_db(tmp_path, "noid.db")
    episodes = [dict(_EPISODE_JA, Id=""), dict(_EPISODE_JA, Id="e9")]
    s = _make_sinizer(monkeypatch, episodes=episodes, translate=_translate_ok)
    db = Session()
    try:
        stats = sa._repair_series_episode_overviews(s, db, "s1")
    finally:
        db.close()
    assert stats["total"] == 2
    assert stats["skipped"] == 1, f"无 Id 分集应跳过，实际={stats}"
    assert stats["translated"] == 1


# ================================================================
# 3. _repair_episode_overviews_task：后台任务编排
# ================================================================

def test_task_processes_all_series_and_completes(monkeypatch, tmp_path):
    """多剧批量：逐剧处理，翻译统计聚合，finally 以 success=True 终结任务。"""
    Session = _make_db(tmp_path, "task.db")
    monkeypatch.setattr(sa, "SessionLocal", Session)
    monkeypatch.setattr(sa, "load_config", lambda: _CFG)
    fake_tm = _FakeTaskManager()
    monkeypatch.setattr(sa, "task_manager", fake_tm)

    # 每部剧一个日文集，distinct ep id 便于断言
    fetch_map = {
        "s1": [dict(_EPISODE_JA, Id="e1")],
        "s2": [dict(_EPISODE_JA, Id="e2")],
    }
    s = _make_sinizer(monkeypatch, translate=_translate_ok)
    s._fetch_episodes = lambda sid: copy.deepcopy(fetch_map.get(sid, []))
    monkeypatch.setattr(sa, "DoubanSinizer", lambda: s)

    sa._repair_episode_overviews_task("task_batch", ["s1", "s2"])

    assert fake_tm.completed, "任务必须 complete_task"
    assert fake_tm.completed[-1][2] is True, "全部成功应 success=True"
    # 两部剧各 1 集，翻译 2 集
    assert "翻译 2 集" in fake_tm.completed[-1][1], fake_tm.completed[-1][1]

    db = Session()
    try:
        for ep_id in ("e1", "e2"):
            rec = db.query(MediaMetadata).filter(
                MediaMetadata.emby_item_id == ep_id
            ).first()
            assert rec is not None and rec.overview == _ZH_OVERVIEW
    finally:
        db.close()


def test_task_isolates_series_failure(monkeypatch, tmp_path):
    """单剧异常隔离：一部剧崩溃不阻断后续，任务以部分失败完成。"""
    Session = _make_db(tmp_path, "iso.db")
    monkeypatch.setattr(sa, "SessionLocal", Session)
    monkeypatch.setattr(sa, "load_config", lambda: _CFG)
    fake_tm = _FakeTaskManager()
    monkeypatch.setattr(sa, "task_manager", fake_tm)

    boom = {"s1": True}
    s = _make_sinizer(monkeypatch, translate=_translate_ok)

    def fake_series(sinizer, db, series_id):
        if boom.get(series_id):
            raise RuntimeError("series boom")
        return {"total": 1, "translated": 1, "skipped": 0, "failed": 0}

    monkeypatch.setattr(sa, "_repair_series_episode_overviews", fake_series)
    monkeypatch.setattr(sa, "DoubanSinizer", lambda: s)

    sa._repair_episode_overviews_task("task_iso", ["s1", "s2"])

    assert fake_tm.completed and fake_tm.completed[-1][2] is True
    assert "成功 1" in fake_tm.completed[-1][1] and "失败 1" in fake_tm.completed[-1][1]


def test_task_aborts_when_master_switch_off(monkeypatch, tmp_path):
    """overview_translation_enabled=False → 任务终止，不实例化 sinizer。"""
    Session = _make_db(tmp_path, "master.db")
    monkeypatch.setattr(sa, "SessionLocal", Session)
    monkeypatch.setattr(sa, "load_config", lambda: _CFG_MASTER_OFF)
    fake_tm = _FakeTaskManager()
    monkeypatch.setattr(sa, "task_manager", fake_tm)
    built = []
    monkeypatch.setattr(sa, "DoubanSinizer", lambda: built.append(1) or _make_sinizer(monkeypatch))

    sa._repair_episode_overviews_task("task_off", ["s1"])

    assert built == [], "总开关关闭时不得实例化 DoubanSinizer"
    assert fake_tm.completed and fake_tm.completed[-1][2] is False, (
        "总开关关闭应 success=False 终结"
    )


def test_task_overrides_sinicize_flag(monkeypatch, tmp_path):
    """★ 显式修复不受 sinicize_translate_episode_overviews=False 限制（任务内强制开启）。"""
    Session = _make_db(tmp_path, "override.db")
    monkeypatch.setattr(sa, "SessionLocal", Session)
    monkeypatch.setattr(sa, "load_config", lambda: _CFG_SINICIZE_FLAG_OFF)
    fake_tm = _FakeTaskManager()
    monkeypatch.setattr(sa, "task_manager", fake_tm)

    # 构造时开关确为关（模拟用户关了 sinicize 分集简介翻译）
    monkeypatch.setattr(ds, "load_config", lambda: _CFG_SINICIZE_FLAG_OFF)
    s = ds.DoubanSinizer()
    assert s.translate_episode_overviews is False, "前置：构造时开关应为关"
    s._fetch_episodes = lambda sid: copy.deepcopy([_EPISODE_JA])
    monkeypatch.setattr(ds, "translate_overview", _translate_ok)
    s._write_back_episode = lambda *a, **k: True
    monkeypatch.setattr(sa, "DoubanSinizer", lambda: s)

    sa._repair_episode_overviews_task("task_override", ["s1"])

    assert fake_tm.completed and fake_tm.completed[-1][2] is True
    db = Session()
    try:
        rec = db.query(MediaMetadata).filter(MediaMetadata.emby_item_id == "e1").first()
        assert rec is not None and rec.overview == _ZH_OVERVIEW, (
            "显式修复应无视 sinicize 开关完成翻译"
        )
    finally:
        db.close()


# ================================================================
# 4. 端点：POST /sync/repair_episode_overviews
# ================================================================

def test_endpoint_dispatches_for_explicit_ids(monkeypatch, tmp_path):
    """指定剧 → 创建后台任务并挂 _repair_episode_overviews_task，返回 count。"""
    Session = _make_db(tmp_path)
    monkeypatch.setattr(sa, "SessionLocal", Session)
    monkeypatch.setattr(sa, "load_config", lambda: _CFG)
    fake_tm = _FakeTaskManager()
    monkeypatch.setattr(sa, "task_manager", fake_tm)
    bg = BackgroundTasks()

    resp = sa.repair_episode_overviews(
        sa.RepairEpisodeOverviewsRequest(item_ids=["s1", "s2"]), bg,
    )
    assert resp["count"] == 2
    assert resp["task_id"] == "task_repair"
    assert fake_tm.created and fake_tm.created[0]["total"] == 2
    assert len(bg.tasks) == 1
    assert bg.tasks[0].func is sa._repair_episode_overviews_task, (
        "后台任务应挂 _repair_episode_overviews_task"
    )


def test_endpoint_no_targets_short_circuits(monkeypatch, tmp_path):
    """空 item_ids 且 DB 无分集记录 → 不创建任务，返回 count 0。"""
    Session = _make_db(tmp_path, "none.db")
    monkeypatch.setattr(sa, "SessionLocal", Session)
    monkeypatch.setattr(sa, "load_config", lambda: _CFG)
    fake_tm = _FakeTaskManager()
    monkeypatch.setattr(sa, "task_manager", fake_tm)
    bg = BackgroundTasks()

    resp = sa.repair_episode_overviews(sa.RepairEpisodeOverviewsRequest(), bg)
    assert resp["count"] == 0
    assert resp["task_id"] == ""
    assert fake_tm.created == []
    assert len(bg.tasks) == 0


def test_endpoint_master_switch_off_raises_400(monkeypatch, tmp_path):
    """overview_translation_enabled=False → HTTP 400，不建任务。"""
    Session = _make_db(tmp_path, "ep_master.db")
    monkeypatch.setattr(sa, "SessionLocal", Session)
    monkeypatch.setattr(sa, "load_config", lambda: _CFG_MASTER_OFF)
    fake_tm = _FakeTaskManager()
    monkeypatch.setattr(sa, "task_manager", fake_tm)

    with pytest.raises(HTTPException) as ei:
        sa.repair_episode_overviews(
            sa.RepairEpisodeOverviewsRequest(item_ids=["s1"]), BackgroundTasks(),
        )
    assert ei.value.status_code == 400
    assert fake_tm.created == []
