import qbittorrentapi
import logging
from config.settings import load_config

logger = logging.getLogger("uvicorn")

def get_qb_client(qb_config):
    """
    根据配置获取 qBittorrent 客户端实例
    """
    try:
        host = qb_config.get("host", "").strip()
        username = qb_config.get("username", "").strip()
        password = qb_config.get("password", "").strip()
        
        if not host:
            logger.error("❌ qBittorrent 连接失败: 未配置 Host")
            return None

        # 确保 host 包含协议
        if not host.startswith(('http://', 'https://')):
            host = f"http://{host}"
            
        logger.info(f"🔄 正在连接 qBittorrent: {host} (用户: {username})")
            
        qbt_client = qbittorrentapi.Client(
            host=host,
            username=username,
            password=password,
            REQUESTS_ARGS={'timeout': (3.1, 30)}
        )
        
        try:
            qbt_client.auth_log_in()
            if qbt_client.is_logged_in:
                logger.info(f"✅ qBittorrent 连接成功: {host}")
                return qbt_client
            else:
                logger.error(f"❌ qBittorrent 登录失败 (未报错但未登录): {host}")
                return None
        except qbittorrentapi.LoginFailed as e:
            logger.error(f"❌ qBittorrent 登录失败 ({host}): {e}")
            return None
    except Exception as e:
        logger.error(f"❌ 连接 qBittorrent 异常 ({qb_config.get('host')}): {e}")
        return None

def get_qb_data(config_id: str = None):
    """
    获取 qB 的基础信息：标签、分类
    """
    cfg = load_config()
    qb_configs = cfg.get("qb_configs", [])
    
    results = []
    for qb_cfg in qb_configs:
        if config_id and qb_cfg.get("id") != config_id:
            continue
            
        if not qb_cfg.get("active", True):
            continue
            
        client = get_qb_client(qb_cfg)
        if client:
            try:
                tags = client.torrents_tags()
                categories = client.torrents_categories()
                results.append({
                    "id": qb_cfg.get("id"),
                    "name": qb_cfg.get("name"),
                    "tags": tags,
                    "categories": list(categories.keys()) if isinstance(categories, dict) else categories
                })
            except Exception as e:
                logger.error(f"❌ 获取 qB 数据失败 ({qb_cfg.get('name')}): {e}")
                
    return results

def get_torrents(config_id: str, filter_status: str = None, tag: str = None, category: str = None, keyword: str = None):
    """
    获取种子列表
    """
    cfg = load_config()
    qb_configs = cfg.get("qb_configs", [])
    qb_cfg = next((c for c in qb_configs if c.get("id") == config_id), None)
    logger.info(f"关键字{keyword}")
    if not qb_cfg:
        return []
        
    client = get_qb_client(qb_cfg)
    if not client:
        return []
        
    try:
        torrents = client.torrents_info(filter=filter_status, tag=tag, category=category)
        # 简化返回的数据，只返回前端需要的
        result = []
        for t in torrents:
            # --- 新增逻辑：关键字匹配 ---
            if keyword and keyword.strip():
                # 不区分大小写
                if keyword.lower() not in t.name.lower():
                    continue
            # ------------------------
            result.append({
                "hash": t.hash,
                "name": t.name,
                "size": t.size,
                "progress": t.progress,
                "state": t.state,
                "category": t.category,
                "tags": t.tags,
                "added_on": t.added_on,
                "completion_on": t.completion_on,
                "ratio": t.ratio,
                "upspeed": t.upspeed,
                "dlspeed": t.dlspeed,
                "save_path": t.save_path
            })
        return result
    except Exception as e:
        logger.error(f"❌ 获取种子列表失败: {e}")
        return []

def delete_torrents(config_id: str, hashes: list, delete_files: bool = False):
    """
    删除种子
    """
    cfg = load_config()
    qb_configs = cfg.get("qb_configs", [])
    qb_cfg = next((c for c in qb_configs if c.get("id") == config_id), None)
    
    if not qb_cfg:
        return False
        
    client = get_qb_client(qb_cfg)
    if not client:
        return False
        
    try:
        client.torrents_delete(delete_files=delete_files, torrent_hashes=hashes)
        return True
    except Exception as e:
        logger.error(f"❌ 删除种子失败: {e}")
        return False

# 在 qb_service.py 末尾添加

def get_torrent_files(config_id: str, torrent_hash: str):
    """
    获取指定种子的文件列表
    """
    cfg = load_config()
    qb_configs = cfg.get("qb_configs", [])
    qb_cfg = next((c for c in qb_configs if c.get("id") == config_id), None)
    
    if not qb_cfg:
        logger.error(f"❌ 获取种子文件失败: 未找到配置 ID: {config_id}")
        return []
        
    client = get_qb_client(qb_cfg)
    if not client:
        logger.error(f"❌ 获取种子文件失败: 无法连接 qBittorrent 实例: {qb_cfg.get('name')}")
        return []
        
    try:
        # 调用 qBittorrent API 获取文件
        files = client.torrents_files(torrent_hash=torrent_hash)
        logger.info(f"✅ 从 qBittorrent 获取到文件数据 (hash: {torrent_hash}): {files}")
        # --- 修改重点开始：将对象手动转换为字典 ---
        result = []
        for f in files:
            # 手动提取需要的字段，构建标准字典
            result.append({
                "name": f.get("name"),       # 文件名
                "size": f.get("size"),       # 大小
                "progress": f.get("progress"), # 进度 (0-1)
                "priority": f.get("priority"), # 优先级
                "is_seed": f.get("is_seed")    # 是否在做种
            })
        
        return result 
        # --- 修改重点结束 ---
        return files
    except Exception as e:
        logger.error(f"❌ 获取种子文件失败: {e}", exc_info=True)
        return []
