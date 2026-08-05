"""全库简介汉化 — DB 集成测试。

覆盖：
- media_metadata 新字段迁移（旧库补列）
- Emby 同步（save_media_to_db）防覆盖守卫
- 全库扫描 scan_and_translate（入队判定 / 回写 / 来源标记 / 跳过逻辑）

全部 mock LLM 层，不触网；DB 用 SQLite 内存库。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from database import Base, _run_migrations
from models import MediaMetadata, MediaSyncStatus
from services import translation_utils as tu
import services.overview_translator as ot


def _mem_engine():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return engine


def _mem_session():
    return sessionmaker(bind=_mem_engine())()


# ================================================================
# 迁移：media_metadata 新审计列补列
# ================================================================

def test_migration_readds_overview_columns():
    """旧库缺 overview_source / overview_updated_at → _run_migrations 补回。"""
    eng = _mem_engine()
    with eng.connect() as conn:  # 模拟旧库缺列
        conn.execute(text("ALTER TABLE media_metadata DROP COLUMN overview_source"))
        conn.execute(text("ALTER TABLE media_metadata DROP COLUMN overview_updated_at"))
        conn.commit()
    _run_migrations(eng)
    cols = {c["name"] for c in inspect(eng).get_columns("media_metadata")}
    assert "overview_source" in cols
    assert "overview_updated_at" in cols


# ================================================================
# Emby 同步防覆盖守卫（save_media_to_db）
# ================================================================

def _seed_media(db, emby_item_id="item1", overview="", overview_source=""):
    db.add(MediaMetadata(
        emby_item_id=emby_item_id, media_type="Movie", title="T",
        overview=overview, overview_source=overview_source,
    ))
    db.commit()


def _sync_emby(db, emby_item_id, overview_text):
    from services.db_crud import save_media_to_db
    save_media_to_db(
        db,
        {"Id": emby_item_id, "Name": "T", "Type": "Movie", "Overview": overview_text},
        provider_ids={}, images={}, people=None, skip_profiles=True,
    )
    db.commit()


def test_sync_does_not_overwrite_ai_chinese():
    """AI 中文简介 + Emby 推英文 → 拒绝覆盖，overview 与来源均保持。"""
    db = _mem_session()
    _seed_media(db, overview="这是中文简介", overview_source=tu.SOURCE_LOCAL_LLM)
    _sync_emby(db, "item1", "English overview again")
    rec = db.query(MediaMetadata).filter_by(emby_item_id="item1").first()
    assert rec.overview == "这是中文简介"
    assert rec.overview_source == tu.SOURCE_LOCAL_LLM


def test_sync_overwrites_ai_with_official_chinese():
    """Emby 官方推纯中文 → 允许覆盖，来源标记 official。"""
    db = _mem_session()
    _seed_media(db, overview="这是中文简介", overview_source=tu.SOURCE_CLOUD_LLM)
    _sync_emby(db, "item1", "这是官方更新的中文简介")
    rec = db.query(MediaMetadata).filter_by(emby_item_id="item1").first()
    assert rec.overview == "这是官方更新的中文简介"
    assert rec.overview_source == tu.SOURCE_OFFICIAL


def test_sync_normal_writes_when_no_ai_source():
    """无 AI 来源的普通行 → 正常写入英文，来源保持空。"""
    db = _mem_session()
    _seed_media(db, overview="old english", overview_source="")
    _sync_emby(db, "item1", "brand new english")
    rec = db.query(MediaMetadata).filter_by(emby_item_id="item1").first()
    assert rec.overview == "brand new english"
    assert rec.overview_source == ""


def test_sync_empty_overview_not_clobber_ai():
    """AI 中文简介 + Emby 推空串 → 拒绝覆盖（空串非中文）。"""
    db = _mem_session()
    _seed_media(db, overview="这是中文简介", overview_source=tu.SOURCE_LOCAL_LLM)
    _sync_emby(db, "item1", "")
    rec = db.query(MediaMetadata).filter_by(emby_item_id="item1").first()
    assert rec.overview == "这是中文简介"
    assert rec.overview_source == tu.SOURCE_LOCAL_LLM


# ================================================================
# 全库扫描 scan_and_translate（mock 翻译层，验证入队/回写/跳过/过滤）
# ================================================================

def _seed_media_row(db, emby_item_id, media_type="Movie", overview=""):
    db.add(MediaMetadata(
        emby_item_id=emby_item_id, media_type=media_type, title=emby_item_id,
        overview=overview,
    ))
    db.commit()


def test_scan_translates_only_non_chinese(monkeypatch):
    """英文/低占比 → 翻译回写；中文/空 → 跳过。"""
    db = _mem_session()
    _seed_media_row(db, "en1", "Movie", "This is an English movie")
    _seed_media_row(db, "zh1", "Movie", "这是一部中文电影")
    _seed_media_row(db, "empty1", "Series", "")
    # 中文占比过低（2/35）→ 判定为非充分中文化，仍入队
    _seed_media_row(db, "mix1", "Series", "你好 movie about a detective and his friends")
    monkeypatch.setattr(
        ot, "translate_overview",
        lambda text, cfg=None, skip=None: ("这是中文简介", tu.SOURCE_LOCAL_LLM, set()),
    )
    stats = ot.scan_and_translate(db)
    assert stats == {
        "total_media": 4, "targeted": 2, "translated": 2, "skipped": 2, "failed": 0,
    }
    en = db.query(MediaMetadata).filter_by(emby_item_id="en1").first()
    assert en.overview == "这是中文简介"
    assert en.overview_source == tu.SOURCE_LOCAL_LLM
    assert en.overview_updated_at is not None
    zh = db.query(MediaMetadata).filter_by(emby_item_id="zh1").first()
    assert zh.overview == "这是一部中文电影"  # 未动
    empty = db.query(MediaMetadata).filter_by(emby_item_id="empty1").first()
    assert empty.overview == ""


def test_scan_failed_keeps_original(monkeypatch):
    """翻译失败（全部引擎失败）→ 保留原值，计入 failed。"""
    db = _mem_session()
    _seed_media_row(db, "en1", "Movie", "English overview")
    monkeypatch.setattr(
        ot, "translate_overview",
        lambda text, cfg=None, skip=None: (None, "failed", {"qwen2.5:7b"}),
    )
    stats = ot.scan_and_translate(db)
    assert stats["translated"] == 0
    assert stats["failed"] == 1
    rec = db.query(MediaMetadata).filter_by(emby_item_id="en1").first()
    assert rec.overview == "English overview"
    assert rec.overview_source == ""


def test_scan_library_filter(monkeypatch):
    """library_ids 过滤：经 MediaSyncStatus 桥接，只翻译命中库的行。"""
    db = _mem_session()
    _seed_media_row(db, "libA_en", "Movie", "English A")
    _seed_media_row(db, "libB_en", "Movie", "English B")
    db.add_all([
        MediaSyncStatus(emby_item_id="libA_en", library_id="libA", status="synced"),
        MediaSyncStatus(emby_item_id="libB_en", library_id="libB", status="synced"),
    ])
    db.commit()
    monkeypatch.setattr(
        ot, "translate_overview",
        lambda text, cfg=None, skip=None: ("这是中文", tu.SOURCE_LOCAL_LLM, set()),
    )
    stats = ot.scan_and_translate(db, library_ids=["libA"])
    assert stats["targeted"] == 1
    assert stats["translated"] == 1
    assert db.query(MediaMetadata).filter_by(emby_item_id="libB_en").first().overview == "English B"


def test_scan_media_type_filter(monkeypatch):
    """media_type 过滤：只翻译指定类型。"""
    db = _mem_session()
    _seed_media_row(db, "m1", "Movie", "English movie")
    _seed_media_row(db, "s1", "Series", "English series")
    monkeypatch.setattr(
        ot, "translate_overview",
        lambda text, cfg=None, skip=None: ("这是中文", tu.SOURCE_LOCAL_LLM, set()),
    )
    stats = ot.scan_and_translate(db, media_type="Movie")
    assert stats["targeted"] == 1
    assert db.query(MediaMetadata).filter_by(emby_item_id="s1").first().overview == "English series"
