"""sync_actions.py TMDB 分集 overview 写点 — 防覆盖守卫集成测试。

覆盖两处写点：
- ``_batch_enrich_episodes_task``（分集富化）
- ``_batch_audit_task`` Phase 2（整季 TMDB 批处理）

断言三个行为：
1. AI 中文简介 + TMDB 推非中文 → 拒绝覆盖，overview / overview_source / update_time 全保持
2. TMDB 推纯中文 → 允许覆盖，来源标记 official，update_time 更新
3. 无 AI 来源的普通行 → 正常写入，来源保持空，update_time 更新

全程 mock 网络层（_requests.get / _sync_and_audit_single_item / _fetch_tmdb_seasons /
ensure_profiles_for_people），不触网；DB 用 SQLite 内存库。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import MediaMetadata
from services import translation_utils as tu


OLD_TIME = datetime(2020, 1, 1, 12, 0, 0)


@pytest.fixture
def Session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def _seed_episode(Session, *, overview, overview_source, update_time=OLD_TIME):
    db = Session()
    db.add(MediaMetadata(
        emby_item_id="ep1", media_type="Episode", title="E1",
        parent_id="series1", parent_index_number=1, index_number=1,
        overview=overview, overview_source=overview_source,
        update_time=update_time,
    ))
    db.commit()
    db.close()


def _fetch_row(Session):
    db = Session()
    try:
        return db.query(MediaMetadata).filter_by(emby_item_id="ep1").first()
    finally:
        db.close()


def _tmdb_payload(overview):
    """构造 TMDB 整季接口返回：单季单集，无客串（跳过漏斗）。"""
    return {"episodes": [{"episode_number": 1, "overview": overview, "guest_stars": []}]}


def _fake_get(payload):
    def fake_get(url, **kwargs):
        resp = SimpleNamespace(status_code=200)
        resp.json = lambda: payload
        return resp
    return fake_get


def _patch_common(monkeypatch, Session, payload):
    """打上两个写点共用的网络 / 漏斗 mock，返回 sync_actions 模块。"""
    from routers import sync_actions as sa
    monkeypatch.setattr(sa, "_requests", SimpleNamespace(get=_fake_get(payload)))
    monkeypatch.setattr(sa, "ensure_profiles_for_people", lambda *a, **k: None)
    monkeypatch.setattr(sa, "SessionLocal", Session)
    return sa


# ================================================================
# 写点一：_batch_enrich_episodes_task
# ================================================================

def test_enrich_blocked_keeps_ai_chinese(monkeypatch, Session):
    """AI 中文简介 + TMDB 推英文 → 拒绝覆盖，update_time 不更新。"""
    sa = _patch_common(monkeypatch, Session, _tmdb_payload("English TMDB overview"))
    _seed_episode(Session, overview="这是AI中文简介", overview_source=tu.SOURCE_LOCAL_LLM)
    sa._batch_enrich_episodes_task("t1", "series1", "123", [1], "Test Series")
    rec = _fetch_row(Session)
    assert rec.overview == "这是AI中文简介"
    assert rec.overview_source == tu.SOURCE_LOCAL_LLM
    assert rec.update_time == OLD_TIME


def test_enrich_allows_official_chinese(monkeypatch, Session):
    """TMDB 推纯中文 → 允许覆盖，来源标记 official，update_time 更新。"""
    sa = _patch_common(monkeypatch, Session, _tmdb_payload("这是官方中文简介"))
    _seed_episode(Session, overview="这是AI中文简介", overview_source=tu.SOURCE_CLOUD_LLM)
    sa._batch_enrich_episodes_task("t2", "series1", "123", [1], "Test Series")
    rec = _fetch_row(Session)
    assert rec.overview == "这是官方中文简介"
    assert rec.overview_source == tu.SOURCE_OFFICIAL
    assert rec.update_time != OLD_TIME


def test_enrich_normal_writes_without_ai_source(monkeypatch, Session):
    """无 AI 来源 + TMDB 推英文 → 正常写入，来源保持空，update_time 更新。"""
    sa = _patch_common(monkeypatch, Session, _tmdb_payload("new english tmdb"))
    _seed_episode(Session, overview="old english", overview_source="")
    sa._batch_enrich_episodes_task("t3", "series1", "123", [1], "Test Series")
    rec = _fetch_row(Session)
    assert rec.overview == "new english tmdb"
    assert rec.overview_source == ""
    assert rec.update_time != OLD_TIME


# ================================================================
# 写点二：_batch_audit_task Phase 2
# ================================================================

def _patch_audit(monkeypatch, Session, payload):
    sa = _patch_common(monkeypatch, Session, payload)
    monkeypatch.setattr(
        sa, "_sync_and_audit_single_item",
        lambda item_id, library_id=None: {
            "success": True, "synced": True, "item_type": "Series",
            "tmdb_id": "123", "item_name": "Test Series", "episodes_processed": 1,
        },
    )
    monkeypatch.setattr(sa, "_fetch_tmdb_seasons", lambda base, key, tid: [1])
    return sa


def test_audit_blocked_keeps_ai_chinese(monkeypatch, Session):
    """AI 中文简介 + 审计 TMDB 推英文 → 拒绝覆盖，update_time 不更新。"""
    sa = _patch_audit(monkeypatch, Session, _tmdb_payload("English TMDB overview"))
    _seed_episode(Session, overview="这是AI中文简介", overview_source=tu.SOURCE_LOCAL_LLM)
    sa._batch_audit_task("t4", ["series1"], "", "host", "key", "user")
    rec = _fetch_row(Session)
    assert rec.overview == "这是AI中文简介"
    assert rec.overview_source == tu.SOURCE_LOCAL_LLM
    assert rec.update_time == OLD_TIME


def test_audit_allows_official_chinese(monkeypatch, Session):
    """审计 TMDB 推纯中文 → 允许覆盖，来源标记 official，update_time 更新。"""
    sa = _patch_audit(monkeypatch, Session, _tmdb_payload("这是官方中文简介"))
    _seed_episode(Session, overview="这是AI中文简介", overview_source=tu.SOURCE_CLOUD_LLM)
    sa._batch_audit_task("t5", ["series1"], "", "host", "key", "user")
    rec = _fetch_row(Session)
    assert rec.overview == "这是官方中文简介"
    assert rec.overview_source == tu.SOURCE_OFFICIAL
    assert rec.update_time != OLD_TIME
