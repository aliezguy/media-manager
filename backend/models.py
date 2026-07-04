# backend/models.py
import enum
from database import Base
from sqlalchemy import Column, Integer, String, Boolean, JSON, DateTime
from datetime import datetime


class TaskStatus(str, enum.Enum):
    """自动化任务状态枚举"""
    INIT = "INIT"
    WAITING_FOR_DELETE_WEBHOOK = "WAITING_FOR_DELETE_WEBHOOK"
    WAITING_FOR_NEW_WEBHOOK = "WAITING_FOR_NEW_WEBHOOK"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ActionType(str, enum.Enum):
    """任务操作日志 — 动作类型枚举"""
    DELETE_TORRENT = "DELETE_TORRENT"
    DELETE_MEDIA = "DELETE_MEDIA"
    DELETE_ORGANIZED = "DELETE_ORGANIZED"
    MOVE_FOLDER = "MOVE_FOLDER"
    SKIP_FOLDER = "SKIP_FOLDER"
    KEEP_MEDIA = "KEEP_MEDIA"
    KEEP_ORGANIZED = "KEEP_ORGANIZED"
    KEEP_TORRENT = "KEEP_TORRENT"


class TaskActionLog(Base):
    """任务操作日志表 — 记录洗版流程中每个决策动作"""
    __tablename__ = "task_action_logs"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, index=True)
    tmdb_id = Column(Integer, index=True)
    title = Column(String)
    action_type = Column(String, nullable=False)
    target_name = Column(String, nullable=False)
    target_path = Column(String)
    reason = Column(String)
    detail = Column(JSON)
    created_at = Column(DateTime, default=datetime.now)

class MediaTag(Base):
    __tablename__ = "media_tags"

    # item_id 是主键，对应 Emby 的 ID
    item_id = Column(String, primary_key=True, index=True)
    name = Column(String)
    # 使用 JSON 类型直接存列表 ['古装', '悬疑']
    tags = Column(JSON)


class WashHistory(Base):
    __tablename__ = "wash_history"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    season = Column(Integer)
    tmdb_id = Column(Integer)
    status = Column(String)
    message = Column(String)
    wash_params = Column(JSON)
    # 🔥 新增字段，默认值为 'complete'
    wash_type = Column(String, default="complete")
    created_at = Column(DateTime, default=datetime.now)


class TvShowDetail(Base):
    """电视剧详情表 — 存储 TMDB 元数据"""
    __tablename__ = "tv_show_details"

    id = Column(Integer, primary_key=True, index=True)
    tmdb_id = Column(Integer, unique=True, index=True, nullable=False)
    title = Column(String, nullable=False)
    year = Column(Integer)
    category = Column(String)
    total_episodes = Column(Integer)
    overview = Column(String)
    created_at = Column(DateTime, default=datetime.now)


class CompletedSeasonRecord(Base):
    """已完结剧集记录表 — 记录已下载/已整理的季信息"""
    __tablename__ = "completed_season_records"

    id = Column(Integer, primary_key=True, index=True)
    tmdb_id = Column(Integer, nullable=False, index=True)
    season_number = Column(Integer, nullable=False)
    downloaded_episodes = Column(Integer, default=0)
    folder_path = Column(String)
    size_bytes = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now)


class TorrentRecord(Base):
    """种子记录表 — 关联种子 hash 与 TMDB ID"""
    __tablename__ = "torrent_records"

    id = Column(Integer, primary_key=True, index=True)
    hash = Column(String, unique=True, index=True, nullable=False)
    torrent_name = Column(String, nullable=False)
    tmdb_id = Column(Integer, index=True)
    qb_category = Column(String)
    size = Column(Integer)
    added_on = Column(DateTime)


class AutoTaskFlow(Base):
    """自动化任务流转表 — 状态机核心"""
    __tablename__ = "auto_task_flows"

    id = Column(Integer, primary_key=True, index=True)
    tmdb_id = Column(Integer, nullable=False, index=True)
    task_type = Column(String, nullable=False)
    status = Column(String, nullable=False, default=TaskStatus.INIT.value)
    retry_count = Column(Integer, default=0)
    error_message = Column(String)
    context = Column(JSON)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class ScheduledTask(Base):
    """定时扫描任务表 — 按 Cron 表达式定期扫描 CD2 目录并触发洗版"""
    __tablename__ = "scheduled_tasks"

    id = Column(Integer, primary_key=True, index=True)
    directory_path = Column(String, nullable=False)
    cron_expression = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    last_run_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now)


class ScanRunLog(Base):
    """扫描运行日志表 — 记录每次定时/手动扫描的宏观结果"""
    __tablename__ = "scan_run_logs"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, nullable=False, index=True)
    status = Column(String, nullable=False)  # SUCCESS / FAILED
    trigger_type = Column(String, nullable=False)  # CRON / MANUAL
    scanned_count = Column(Integer, default=0)
    processed_count = Column(Integer, default=0)
    details = Column(JSON)
    created_at = Column(DateTime, default=datetime.now)