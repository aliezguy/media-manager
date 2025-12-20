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
    
    # MP 基础配置
    "mp_host": "http://127.0.0.1:3000",
    "mp_username": "",
    "mp_password": "",

    "tmdb_api_key": "",
    
    # 洗版策略 (默认空)
    "wash_schemes": [],
    # 追更配置策略
    "subscribe_schemes": []
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