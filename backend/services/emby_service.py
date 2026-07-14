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


# ==========================================
# CD2 删除后的 Emby 僵尸清理
# ==========================================

def _make_headers() -> dict | None:
    """构建 Emby API 请求头。"""
    cfg = load_config()
    api_key = cfg.get("emby_api_key")
    if not api_key:
        return None
    return {"X-Emby-Token": api_key}


def _emby_host() -> str:
    cfg = load_config()
    return cfg.get("emby_host", "").rstrip('/')


def search_series_by_name(name: str) -> dict | None:
    """在 Emby 中按名称搜索剧集（Series）。

    返回匹配的第一个 Series Item，或 None。
    """
    host = _emby_host()
    headers = _make_headers()
    if not host or not headers:
        logger.warning("⚠️ Emby search 失败: 缺少 emby_host 或 emby_api_key")
        return None

    try:
        url = f"{host}/emby/Items"
        params = {
            "searchTerm": name,
            "IncludeItemTypes": "Series",
            "Recursive": "true",
            "Limit": "5",
        }
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        if resp.status_code != 200:
            logger.warning("Emby search '%s' failed: HTTP %d", name, resp.status_code)
            return None
        items = resp.json().get("Items", [])
        if not items:
            logger.info("Emby search '%s': 未找到匹配项", name)
            return None
        # 优先按名称精确匹配
        for item in items:
            if item.get("Name") == name:
                return item
        # 回退：返回第一个
        return items[0]
    except Exception as e:
        logger.warning("Emby search '%s' error: %s", name, e)
        return None


def delete_emby_item(item_id: str) -> bool:
    """从 Emby 中删除指定 Item（仅删数据库记录，不删文件）。

    CD2 已经删除了物理文件，所以 Emby 只需清理数据库。
    """
    host = _emby_host()
    headers = _make_headers()
    if not host or not headers:
        return False

    try:
        url = f"{host}/emby/Items/{item_id}"
        resp = requests.delete(url, headers=headers, timeout=10)
        if resp.status_code in (200, 204):
            logger.info("✅ Emby 已删除 Item %s", item_id)
            return True
        else:
            logger.warning("Emby 删除 Item %s 失败: HTTP %d", item_id, resp.status_code)
            return False
    except Exception as e:
        logger.warning("Emby 删除 Item %s 异常: %s", item_id, e)
        return False


def cleanup_emby_zombie(cd2_path: str) -> bool:
    """CD2 删除文件夹后，尝试清理 Emby 中残留的僵尸剧集记录。

    流程:
    1. CD2 路径 → Emby 路径
    2. 从路径中提取剧名
    3. 在 Emby 中搜索同名 Series
    4. 如果找到且路径匹配，删除该 Emby Item

    Args:
        cd2_path: CD2 中被删除的目录路径

    Returns:
        True 如果成功清理或无需清理，False 如果清理失败
    """
    import re
    from utils.path_utils import cd2_path_to_emby_path

    emby_path = cd2_path_to_emby_path(cd2_path).rstrip('/')

    # 从路径中提取剧名（路径最后一段）
    # 例如: /volume3/.../2026/超燃青春的合唱(2026) {tmdb=320614}
    show_dir = emby_path.split('/')[-1] if '/' in emby_path else emby_path
    if not show_dir:
        logger.info("Emby cleanup: 无法从路径提取剧名 '%s'", emby_path)
        return False

    # 去掉年份和 tmdb 标记，取纯剧名用于搜索
    # "超燃青春的合唱(2026) {tmdb=320614}" → "超燃青春的合唱"
    clean_name = re.sub(r'\s*\{tmdb=\d+\}', '', show_dir)
    clean_name = re.sub(r'\s*\(\d{4}\)\s*$', '', clean_name).strip()
    if not clean_name:
        clean_name = show_dir  # fallback

    logger.info(
        "Emby cleanup: CD2 '%s' → Emby '%s', 搜索 '%s'",
        cd2_path, emby_path, clean_name,
    )

    item = search_series_by_name(clean_name)
    if not item:
        logger.info("Emby cleanup: '%s' 未找到 Emby Item，可能已自动清理", clean_name)
        return True  # 无需清理 = 成功

    # 验证路径匹配（Item 的 Path 应包含我们的 emby_path）
    item_path = item.get("Path", "")
    item_name = item.get("Name", "")
    if emby_path not in item_path and clean_name != item_name:
        logger.info(
            "Emby cleanup: 找到 '%s' 但路径不匹配 (Item path='%s'), 跳过",
            item_name, item_path,
        )
        return True

    item_id = item.get("Id")
    logger.info(
        "Emby cleanup: 找到残留 Item '%s' (%s), 准备删除...",
        item_name, item_id,
    )
    return delete_emby_item(item_id)


# ==========================================
# Case C 超时兜底：Emby API 辅助方法
# ==========================================

def search_series_by_tmdb(tmdb_id: int) -> dict | None:
    """在 Emby 中按 TMDB ID 搜索剧集（Series）。

    使用 Emby 的 AnyProviderIdEquals 查询参数精确匹配 TMDB ID。

    Args:
        tmdb_id: TMDB 剧集 ID

    Returns:
        匹配的 Series Item dict，或 None
    """
    host = _emby_host()
    headers = _make_headers()
    if not host or not headers:
        logger.warning("Emby search_series_by_tmdb 失败: 缺少 emby_host 或 emby_api_key")
        return None

    try:
        url = f"{host}/emby/Items"
        params = {
            "IncludeItemTypes": "Series",
            "AnyProviderIdEquals": f"tmdb.{tmdb_id}",
            "Recursive": "true",
        }
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        if resp.status_code != 200:
            logger.warning("Emby search_series_by_tmdb(%d) failed: HTTP %d", tmdb_id, resp.status_code)
            return None
        items = resp.json().get("Items", [])
        if not items:
            logger.info("Emby search_series_by_tmdb(%d): 未找到匹配项", tmdb_id)
            return None
        return items[0]
    except Exception as e:
        logger.warning("Emby search_series_by_tmdb(%d) error: %s", tmdb_id, e)
        return None


def get_season_by_number(series_id: str, season_number: int) -> dict | None:
    """在 Emby 中查找指定 Series 下的特定 Season。

    Args:
        series_id: Emby Series Item ID
        season_number: Season 编号（如 4 表示 Season 4）

    Returns:
        匹配的 Season Item dict，或 None
    """
    host = _emby_host()
    headers = _make_headers()
    if not host or not headers:
        logger.warning("Emby get_season_by_number 失败: 缺少配置")
        return None

    try:
        url = f"{host}/emby/Items"
        params = {
            "ParentId": series_id,
            "IncludeItemTypes": "Season",
            "IndexNumber": season_number,
        }
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        if resp.status_code != 200:
            logger.warning(
                "Emby get_season_by_number(series=%s, S%d) failed: HTTP %d",
                series_id, season_number, resp.status_code,
            )
            return None
        items = resp.json().get("Items", [])
        if not items:
            logger.info(
                "Emby get_season_by_number(series=%s, S%d): 未找到",
                series_id, season_number,
            )
            return None
        return items[0]
    except Exception as e:
        logger.warning(
            "Emby get_season_by_number(series=%s, S%d) error: %s",
            series_id, season_number, e,
        )
        return None