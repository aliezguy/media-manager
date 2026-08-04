import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) # 定位到 backend 目录
DATA_DIR = os.path.join(BASE_DIR, 'data') # backend/data
CONFIG_FILE = os.path.join(DATA_DIR, 'config.json')
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
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # 只做简单的字段合并，不再做数据格式转换
            for key, value in DEFAULT_CONFIG.items():
                if key not in data:
                    data[key] = value
            return data
    return DEFAULT_CONFIG

def save_config(new_config: dict):
    current = load_config()
    current.update(new_config)
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(current, f, indent=4, ensure_ascii=False)
    return current
