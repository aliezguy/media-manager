import json
import os

import yaml   # 配置文件 YAML 格式（原生支持 # 注释）

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) # 定位到 backend 目录
DATA_DIR = os.path.join(BASE_DIR, 'data') # backend/data
CONFIG_FILE = os.path.join(DATA_DIR, 'config.yaml')       # 主配置（YAML，支持注释）
CONFIG_FILE_JSON = os.path.join(DATA_DIR, 'config.json')  # 旧版 JSON 兼容（存在则读，保存后迁移）
# 确保 data 目录存在
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)
DEFAULT_CONFIG = {
    # Emby 配置
    "emby_host": "",
    "emby_api_key": "",
    "emby_user_id": "",
    "sf_api_key": "",

    # ★ LLM 通用配置（兼容 OpenAI SDK 接口的任意大模型）
    "llm_base_url": "https://api.siliconflow.cn/v1",
    "llm_model_name": "deepseek-ai/DeepSeek-V3",

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
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        # YAML 主格式：原生支持 # 注释
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
    elif os.path.exists(CONFIG_FILE_JSON):
        # 旧版 config.json 兼容：存在则读取，一旦保存即迁移为 YAML
        with open(CONFIG_FILE_JSON, 'r', encoding='utf-8') as f:
            data = json.load(f)
    else:
        return DEFAULT_CONFIG
    # 只做简单的字段合并，不再做数据格式转换
    for key, value in DEFAULT_CONFIG.items():
        if key not in data:
            data[key] = value
    return data

def save_config(new_config: dict):
    current = load_config()
    current.update(new_config)
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        yaml.safe_dump(current, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return current

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
