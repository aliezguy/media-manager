# backend/models.py
import enum
from database import Base
from sqlalchemy import Column, Integer, String, Boolean, JSON, DateTime, Text
from datetime import datetime


class TaskStatus(str, enum.Enum):
    """自动化任务状态枚举"""
    INIT = "INIT"
    WAITING_FOR_DELETE_WEBHOOK = "WAITING_FOR_DELETE_WEBHOOK"               # Case B: 整剧删除等待 Emby webhook
    WAITING_FOR_SEASON_DELETE_WEBHOOK = "WAITING_FOR_SEASON_DELETE_WEBHOOK"  # Case C: 单季删除等待 Emby webhook
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
    FORCE_MOVE = "FORCE_MOVE"  # 手动强制移动：organized → media


class TaskActionLog(Base):
    """任务操作日志表 — 记录洗版流程中每个决策动作"""
    __tablename__ = "task_action_logs"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, index=True)
    tmdb_id = Column(Integer, index=True)
    title = Column(String(255))
    action_type = Column(String(255), nullable=False)
    target_name = Column(String(255), nullable=False)
    target_path = Column(String(255))
    reason = Column(String(255))
    detail = Column(JSON)
    created_at = Column(DateTime, default=datetime.now)

class MediaTag(Base):
    __tablename__ = "media_tags"

    # item_id 是主键，对应 Emby 的 ID
    item_id = Column(String(255), primary_key=True, index=True)
    name = Column(String(255))
    # 使用 JSON 类型直接存列表 ['古装', '悬疑']
    tags = Column(JSON)


class WashHistory(Base):
    __tablename__ = "wash_history"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), index=True)
    season = Column(Integer)
    tmdb_id = Column(Integer)
    status = Column(String(255))
    message = Column(String(255))
    wash_params = Column(JSON)
    # 🔥 新增字段，默认值为 'complete'
    wash_type = Column(String(255), default="complete")
    created_at = Column(DateTime, default=datetime.now)


class TvShowDetail(Base):
    """电视剧详情表 — 存储 TMDB 元数据"""
    __tablename__ = "tv_show_details"

    id = Column(Integer, primary_key=True, index=True)
    tmdb_id = Column(Integer, unique=True, index=True, nullable=False)
    title = Column(String(255), nullable=False)
    year = Column(Integer)
    category = Column(String(255))
    total_episodes = Column(Integer)
    overview = Column(String(255))
    created_at = Column(DateTime, default=datetime.now)


class CompletedSeasonRecord(Base):
    """已完结剧集记录表 — 记录已下载/已整理的季信息"""
    __tablename__ = "completed_season_records"

    id = Column(Integer, primary_key=True, index=True)
    tmdb_id = Column(Integer, nullable=False, index=True)
    season_number = Column(Integer, nullable=False)
    downloaded_episodes = Column(Integer, default=0)
    folder_path = Column(String(255))
    size_bytes = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now)


class TorrentRecord(Base):
    """种子记录表 — 关联种子 hash 与 TMDB ID"""
    __tablename__ = "torrent_records"

    id = Column(Integer, primary_key=True, index=True)
    hash = Column(String(255), unique=True, index=True, nullable=False)
    torrent_name = Column(String(255), nullable=False)
    tmdb_id = Column(Integer, index=True)
    qb_category = Column(String(255))
    size = Column(Integer)
    added_on = Column(DateTime)


class AutoTaskFlow(Base):
    """自动化任务流转表 — 状态机核心"""
    __tablename__ = "auto_task_flows"

    id = Column(Integer, primary_key=True, index=True)
    tmdb_id = Column(Integer, nullable=False, index=True)
    task_type = Column(String(255), nullable=False)
    status = Column(String(255), nullable=False, default=TaskStatus.INIT.value)
    retry_count = Column(Integer, default=0)
    error_message = Column(String(255))
    context = Column(JSON)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class ScheduledTask(Base):
    """定时扫描任务表 — 按 Cron 表达式定期扫描 CD2 目录并触发洗版"""
    __tablename__ = "scheduled_tasks"

    id = Column(Integer, primary_key=True, index=True)
    directory_path = Column(String(255), nullable=False)
    cron_expression = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    last_run_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now)


class ScanRunLog(Base):
    """扫描运行日志表 — 记录每次定时/手动扫描的宏观结果"""
    __tablename__ = "scan_run_logs"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, nullable=False, index=True)
    status = Column(String(255), nullable=False)  # SUCCESS / FAILED
    trigger_type = Column(String(255), nullable=False)  # CRON / MANUAL
    scanned_count = Column(Integer, default=0)
    processed_count = Column(Integer, default=0)
    details = Column(JSON)
    created_at = Column(DateTime, default=datetime.now)


class MediaSyncStatus(Base):
    """演职员中文化 — 同步状态持久化"""
    __tablename__ = "media_sync_status"

    emby_item_id = Column(String(255), primary_key=True, index=True)
    tmdb_id = Column(String(255), index=True)
    imdb_id = Column(String(255), index=True)
    douban_id = Column(String(255), index=True)
    library_id = Column(String(255), index=True)
    title = Column(String(255))
    status = Column(String(255), default="pending")
    matched_actors = Column(Integer, default=0)
    total_actors = Column(Integer, default=0)
    error_message = Column(Text)
    update_time = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    # ★ 系列级豆瓣 cast 缓存（P3-3b）：{"fetched_at": ISO8601, "cast": {name: {avatar, douban_id, role}}}
    douban_cast_cache = Column(JSON, nullable=True)

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class MediaMetadata(Base):
    """媒体元数据表 — 标题、概述、图片外链"""
    __tablename__ = "media_metadata"

    emby_item_id = Column(String(255), primary_key=True, index=True)
    parent_id = Column(String(255), index=True, nullable=True)
    media_type = Column(String(255), nullable=False)
    title = Column(String(255))
    overview = Column(Text)
    index_number = Column(Integer, nullable=True)
    parent_index_number = Column(Integer, nullable=True)   # Season 编号 (仅 Episode)
    recursive_item_count = Column(Integer, nullable=True)   # 子项总数 (仅 Series: 含 Seasons + Episodes)
    poster_url = Column(Text)
    backdrop_url = Column(Text)
    # ★ 简介汉化审计：overview 由谁写入 — local_llm / cloud_llm / official / ""
    overview_source = Column(String(255), default="")
    overview_updated_at = Column(DateTime, nullable=True)   # 最近一次简介翻译时间（审计/后续冷静期预留）
    update_time = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class ActorProfile(Base):
    """全局演员元数据中心 — 全维度生平 + 本地化头像。

    通过 name (中文名) 作为主键，存储从豆瓣/TMDB 聚合的完整演员档案。
    local_image_path 指向本地下载的头像文件，解除对外部 CDN 的永久依赖。
    """
    __tablename__ = "actor_profiles"

    name = Column(String(255), primary_key=True, index=True)
    local_image_path = Column(String(255))                 # 相对路径 如 "张/张译.jpg"
    image_url = Column(Text)                          # 外部直链兜底 (豆瓣/TMDB)
    source = Column(String(255))                           # "douban" / "tmdb" / "emby"
    tmdb_id = Column(String(255), index=True)
    imdb_id = Column(String(255), index=True)
    douban_celebrity_id = Column(String(255), index=True)
    birth_date = Column(String(255))
    birth_place = Column(String(255))
    overview = Column(Text)
    confidence_level = Column(Integer, default=0)     # 中文名译名置信度: 4官方 / 3AI / 5手动
    translation_source = Column(String(255), default="")   # "official" / "ai_llm" / "manual"
    # ★ 大模型核查状态（出生地/简介/生日空值补全）:
    #   0=未检查(默认, 可触发 LLM) / 1=检查并成功更新 / 2=已检查但模型不知道(触发冷静期)
    llm_check_status = Column(Integer, default=0)
    # 最后 LLM 检查时间戳（配合 llm_cooldown_days 冷静期，避免重复击穿）
    llm_last_checked = Column(DateTime, nullable=True)
    # ★ 大模型翻译来源：成功产出数据的大模型名，逗号分隔去重（如 "gemini-2.5-flash,qwen2.5"）
    llm_translation_source = Column(String(255), default="")
    # ★ 按字段的大模型来源映射（JSON）: {"birth_place": "qwen2.5", "overview": "gemini-2.5-flash"}
    llm_field_sources = Column(JSON, nullable=True)
    update_time = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class ActorRecord(Base):
    """演员关联记录表 — 纯关联实体，连接 Emby 媒体项与演员。

    职责仅是记录某媒体项有哪些演员、饰演什么角色。
    头像、生平等全维度数据统一由 ActorProfile 管理，通过 name 关联。
    """
    __tablename__ = "actor_records"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    emby_item_id = Column(String(255), index=True, nullable=False)
    name = Column(String(255), nullable=False)             # → ActorProfile.name
    role = Column(String(255))
    type = Column(String(255), nullable=False, default="Actor")
    sort_order = Column(Integer, default=0)
    confidence_level = Column(Integer, default=0)     # 角色译名置信度: 4官方 / 3AI / 5手动
    translation_source = Column(String(255), default="")   # 角色译名来源
    update_time = Column(DateTime, default=datetime.now, onupdate=datetime.now)
