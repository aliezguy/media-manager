import requests
import logging
import json
import traceback
from config.settings import load_config

logger = logging.getLogger("uvicorn")

def get_emby_headers():
    cfg = load_config()
    api_key = cfg.get("emby_api_key")
    if not api_key:
        logger.error("❌ [配置错误] 未在配置文件中找到 'emby_api_key'")
        return None
    return {
        "X-Emby-Token": api_key,
        "Content-Type": "application/json"
    }

# ==========================================
# 🔥 核心修改：升级获取详情逻辑
# ==========================================
def get_item_info(item_id):
    """
    查询 Emby 单个物品详情
    改进点：
    1. 优先使用 Users 端点 (如果你配置了 emby_user_id)，可以看到用户特定的状态
    2. 显式请求 Fields (Tags, LockData)，确保后续更新不会因为缺少字段而报错
    """
    cfg = load_config()
    host = cfg.get("emby_host", "").rstrip('/')
    api_key = cfg.get("emby_api_key")
    user_id = cfg.get("emby_user_id") # 获取 User ID
    
    if not host or not api_key:
        logger.error("❌ [配置错误] 未配置 emby_host 或 emby_api_key")
        return None
        
    if not item_id: 
        return None

    # 准备请求参数：显式要求返回 Tags 和 锁定状态
    params = {
        'api_key': api_key,
        'Fields': 'Tags,TagItems,LockData,LockedFields,ProviderIds,ProductionYear'
    }

    # 优先构造 URL：如果有 UserID，走 User 接口；否则走系统接口
    if user_id:
        url = f"{host}/emby/Users/{user_id}/Items/{item_id}"
    else:
        url = f"{host}/emby/Items/{item_id}"
    
    try:
        # logger.info(f"   ☁️ [发起请求] GET {url}") 
        resp = requests.get(url, params=params, timeout=10)
        
        if resp.status_code == 200:
            return resp.json()
        else:
            logger.error(f"❌ [Emby查询失败] HTTP {resp.status_code} | {resp.text[:100]}")
    except Exception as e:
        logger.error(f"❌ [Emby连接异常] 无法连接到 {host} | 错误: {e}")
    
    return None

# ==========================================
# 🔥 配合修改：更新逻辑 (使用上面获取到的完整信息)
# ==========================================
def update_item_tags(item_id, new_tags):
    """
    更新 Emby 物品标签
    1. 获取详情 (包含 LockData)
    2. 解锁 & 清理字段
    3. 提交更新
    """
    cfg = load_config()
    host = cfg.get("emby_host", "").rstrip('/')
    api_key = cfg.get("emby_api_key")
    
    if not host or not api_key: 
        logger.error("❌ 无法更新标签: 配置缺失")
        return False

    headers = {
        "X-Emby-Token": api_key, # 兼容性写法
        "Content-Type": "application/json"
    }

    try:
        # 1. 获取详情 (现在的 get_item_info 很健壮)
        # logger.info(f"   🔄 [更新流程] 正在获取旧标签... (ID: {item_id})")
        item_info = get_item_info(item_id)
        
        if not item_info:
            logger.error(f"   ❌ [更新中止] 无法获取物品详情，可能是网络不通或 ID 错误")
            return False

        # 2. 合并标签
        current_tags = item_info.get("Tags", []) or []
        merged_tags = list(set(current_tags + new_tags))
        
        # 如果标签没变，跳过
        if set(current_tags) == set(merged_tags):
             logger.info(f"   ⏭ [Emby] 标签无变化，跳过更新")
             return True

        # 3. 准备更新数据
        item_info["Tags"] = merged_tags
        
        # 🔥 解锁逻辑 (因为 get_item_info 请求了 LockData，这里一定能取到)
        if item_info.get('LockData'): item_info['LockData'] = False
        if item_info.get('LockedFields'): item_info['LockedFields'] = []

        # 🔥 清理干扰字段 (防止 500 错误)
        keys_to_remove = [
            'MediaSources', 'PlayUserData', 'SeasonUserData', 
            'Container', 'Size', 'TagItems', 'People', 'Studios', 'GenreItems'
        ]
        for k in keys_to_remove:
            if k in item_info:
                del item_info[k]

        # 4. 发送更新
        url = f"{host}/emby/Items/{item_id}"
        
        # 同时在 Query 和 Header 带上 Key，确保成功率
        resp = requests.post(url, json=item_info, headers=headers, params={'api_key': api_key}, timeout=10)
        
        if resp.status_code == 204 or resp.status_code == 200:
            logger.info(f"   ✅ [Emby] 标签更新成功！当前标签: {merged_tags}")
            return True
        else:
            logger.error(f"   ❌ [更新失败] HTTP {resp.status_code} | {resp.text[:200]}")
            
    except Exception as e:
        logger.error(f"❌ [更新异常] {e}")
        logger.error(traceback.format_exc())
        
    return False