import logging
import os

import yaml   # 运行期读取 YAML（原生支持 # 注释）

logger = logging.getLogger("uvicorn")

# ruamel.yaml round-trip：写入时保留手写注释（仅用于保存路径）。
# 采用惰性导入——缺失时降级为 PyYAML safe_dump（不保留注释并告警），
# 避免服务器因缺该依赖在启动/热重载时整进程崩溃。
try:
    from ruamel.yaml import YAML
    _HAS_RUAMEL = True
except ImportError:
    YAML = None
    _HAS_RUAMEL = False

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) # 定位到 backend 目录
DATA_DIR = os.path.join(BASE_DIR, 'data') # backend/data
CONFIG_FILE = os.path.join(DATA_DIR, 'config.yaml')   # 主配置（YAML，支持注释）
# 确保 data 目录存在
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)
DEFAULT_CONFIG = {
    # Emby 配置
    "emby_host": "",
    "emby_api_key": "",
    "emby_user_id": "",

    # MP 基础配置
    "mp_host": "http://127.0.0.1:3000",
    "mp_username": "",
    "mp_password": "",

    "tmdb_api_key": "",
    "tmdb_base_url": "https://api.tmdb.org/3",

    # 豆瓣 API 鉴权 Cookie（用于绕过 need_login 流控限制）
    "douban_cookie": "",
    # 豆瓣 API 总开关（封控严重时可临时关闭，以后恢复）
    "douban_enabled": True,
    
    # 洗版策略 (默认空)
    "wash_schemes": [],
    # 追更配置策略
    "subscribe_schemes": [],
    # qBittorrent 配置
    "qb_configs": [],
    # CloudDrive2 配置
    "cd2_host": "192.168.31.173",
    "cd2_port": "19797",
    "cd2_username": "",
    "cd2_password": "",
    "cd2_media_dir": "/80003588/emby库/电视剧/",
    "cd2_organized_dir": "/80003588/网盘整理/完结整理/电视剧/",

    # Emby ↔ CD2 路径映射（用于路径转换）
    "emby_prefix": "/volume3/emby影院/115网盘_3588/",
    "cd2_media_prefix": "/80003588/emby库/",

    # 演职员中文化 — 每个媒体最大入库演员数（抓取全量，回写截断）
    "max_actors_per_media": 50,

    # ★ 数据库配置（P4 双库兼容）— 生产切 MySQL，测试/默认 SQLite
    "db_type": "sqlite",          # sqlite | mysql
    "db_host": "",                # MySQL: 192.168.31.135
    "db_port": "",                # MySQL: 3008
    "db_user": "",
    "db_password": "",
    "db_name": "",                # MySQL: media-ai

    # ★ 请求预算（P3 豆瓣请求治理）— 进程级每 Provider 令牌桶上限
    "request_budget": {
        "douban_per_series": 30,
        "tmdb_per_min": 60,
        "emby_writeback_per_series": 50,
    },

    # ★ 汉化/审计可配置定时任务（next_run_at 由后端动态计算，不落盘）
    #   library_ids: 选中的媒体库 ID 列表（多选），执行时逐个串行
    "localization_job": {
        "library_ids": [],
        "cron_expression": "0 3 * * *",
        "is_active": False,
        "last_run_at": None,
    },
    "audit_job": {
        "library_ids": [],
        "cron_expression": "0 4 * * *",
        "is_active": False,
        "last_run_at": None,
    },
    "overview_job": {
        "library_ids": [],
        "cron_expression": "0 5 * * *",
        "is_active": False,
        "last_run_at": None,
    },

    # ★ 全库简介（Overview）汉化 — 本地 qwen 优先，云端兜底
    #   overview_job 定时触发 scan_and_translate；overview_* 为翻译链路调参
    "overview_translation_enabled": True,  # 总开关：False 时全库汉化不执行
    "overview_local_first": True,          # 本地 qwen 优先，超时/失败/NULL/未过中文校验 → 云端兜底
    "overview_chinese_ratio": 0.5,         # 「已中文」判定阈值（is_already_chinese）
    "overview_max_tokens": 1500,           # 翻译输出上限

    # ★ 演员元数据 AI 补全/汉化（出生地汉化 + 空值补全，严格防伪 NULL）
    #   流程顺序: 先 TMDB/豆瓣 → 为空才请求本地大模型(qwen2.5) → 仍无再请求其他 Provider
    "actor_ai_enabled": True,     # 总开关：False 时跳过所有演员元数据 LLM 调用
    "actor_ai_local_first": True, # 本地大模型优先（ollama qwen2.5），翻译不到再走其他 Provider
    "llm_cooldown_days": 7,       # LLM「不知道」冷静期: -1 无限期(status=2 永不再查) / 0 无 / N 天内不重查

    # ★ 汉化/审计是否内联补演员简介（D3 决策）
    #   False（默认）= 汉化/审计只建身份+TMDB/豆瓣免费元数据，不逐演员触发 LLM 简介补全（快）
    #   True = 切回旧行为，汉化/审计逐演员内联补简介
    #   演员库刷新/修复路径显式 skip_llm_enrich=False，不受本开关影响
    "actor_bio_inline_enabled": False,

    # ★ 汉化 Series 时是否顺带翻译分集简介（写回 Emby + 落库）
    #   True（默认）= 分集循环里对非中文简介调 LLM 翻译（overview 双重引擎），整部剧全中文
    #   False = 保持旧行为，只汉化演员/角色，分集简介交全库 overview 汉化任务处理
    "sinicize_translate_episode_overviews": True,

    # ★ WebDAV 图片缓存（统一媒体资源存储）— 环境变量优先，config.yaml 兜底
    "webdav_base_url": "",     # 如 http://192.168.31.135:5005
    "webdav_username": "",
    "webdav_password": "",
    "webdav_root_path": "",    # WebDAV 服务内的根目录，如 /dav（可空）
    "webdav_media_root": "library",     # tv/movie 的上级目录（WebDAV 内相对路径，可自定义）
    "webdav_people_root": "library",    # people 的上级目录（WebDAV 内相对路径，可自定义）

    # ★ 弹幕服务（MisakaDanmaku 外部控制 API）— 媒体弹幕管理页面代理
    #   完整 API 文档见 docs/danmu-api.md
    "danmu_base_url": "https://danmu.2503.seeyo.top:13360",
    "danmu_api_key": "",
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        # YAML 主格式：原生支持 # 注释
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
    else:
        return DEFAULT_CONFIG
    # 只做简单的字段合并，不再做数据格式转换
    for key, value in DEFAULT_CONFIG.items():
        if key not in data:
            data[key] = value
    return data

# ruamel.yaml round-trip：写入时保留手写注释与原有格式（仅用于保存路径）
if _HAS_RUAMEL:
    _rt_yaml = YAML()
    _rt_yaml.preserve_quotes = True   # 保留引号风格（如 '2026-08-06T03:00:07'）
    _rt_yaml.width = 4096             # 新写入的长字符串不折行（douban_cookie 等）
    _rt_yaml.default_flow_style = False  # 新写入的嵌套块用 block 风格（旧内容原样保留）

def save_config(new_config: dict):
    """浅合并写入 config.yaml，返回合并默认值后的完整配置。

    只落盘「磁盘已有键 ∪ 本次更新的键」——不会把 DEFAULT_CONFIG 的兜底字段
    重新塞回文件（遗留字段 sf_api_key/llm_base_url/llm_model_name 已移出默认值）。

    ruamel.yaml 可用时 round-trip 写入（保留手写注释）；缺失时降级为
    PyYAML safe_dump 并告警（不保留注释，但保证服务器不会起不来）。
    """
    if _HAS_RUAMEL:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                current = _rt_yaml.load(f) or {}
        else:
            current = {}
        current.update(new_config)
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            _rt_yaml.dump(current, f)
    else:
        if not getattr(save_config, "_warned_no_ruamel", False):
            logger.warning(
                "[Config] ruamel.yaml 未安装，保存 config.yaml 时无法保留手写注释。"
                "执行 pip install ruamel.yaml 后重启可启用。"
            )
            save_config._warned_no_ruamel = True
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                current = yaml.safe_load(f) or {}
        else:
            current = {}
        current.update(new_config)
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            yaml.safe_dump(current, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    # 响应/调用方沿用旧契约：返回含默认值的完整配置
    return load_config()

def get_webdav_config() -> dict:
    """WebDAV 连接 + 布局配置，环境变量优先，config.yaml 兜底。

    media_root / people_root 分别是 tv|movie 与 people 的上级目录（相对路径）。
    """
    cfg = load_config()
    return {
        "base_url":    os.environ.get("WEBDAV_BASE_URL")    or cfg.get("webdav_base_url", ""),
        "username":    os.environ.get("WEBDAV_USERNAME")    or cfg.get("webdav_username", ""),
        "password":    os.environ.get("WEBDAV_PASSWORD")    or cfg.get("webdav_password", ""),
        "root_path":   os.environ.get("WEBDAV_ROOT_PATH")   or cfg.get("webdav_root_path", ""),
        "media_root":  os.environ.get("WEBDAV_MEDIA_ROOT")  or cfg.get("webdav_media_root", "library"),
        "people_root": os.environ.get("WEBDAV_PEOPLE_ROOT") or cfg.get("webdav_people_root", "library"),
    }
