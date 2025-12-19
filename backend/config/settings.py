import json
import os

CONFIG_FILE = "config.json"

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
    
    # 洗版策略 (默认空)
    "wash_schemes": []
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