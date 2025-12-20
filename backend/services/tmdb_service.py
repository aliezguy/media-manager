import requests
import logging
from config.settings import load_config

logger = logging.getLogger("uvicorn")

def get_tmdb_info(tmdb_id, media_type="tv"):
    """
    查询 TMDB 详情
    media_type: 'tv' (电视剧) or 'movie' (电影)
    """
    cfg = load_config()
    api_key = cfg.get("tmdb_api_key")
    
    if not api_key:
        logger.error("❌ 未配置 TMDB API Key，无法自动分类")
        return None

    # MP 传过来的 type 是中文，需要转换
    target_type = "tv"
    if media_type == "电影":
        target_type = "movie"
    
    url = f"https://api.themoviedb.org/3/{target_type}/{tmdb_id}"
    params = {
        "api_key": api_key,
        "language": "zh-CN"
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        else:
            logger.error(f"❌ TMDB 查询失败: {resp.status_code} - {resp.text}")
    except Exception as e:
        logger.error(f"❌ TMDB 连接异常: {e}")
    
    return None