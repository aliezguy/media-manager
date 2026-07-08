import requests
import logging
import time as _time
from config.settings import load_config

logger = logging.getLogger("uvicorn")


def _tmdb_get(url: str, params: dict, timeout: int = 10, max_retries: int = 2):
    """TMDB HTTP GET with retry + exponential backoff for network errors."""
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            return resp
        except (requests.exceptions.SSLError, requests.exceptions.ConnectionError,
                requests.exceptions.Timeout, requests.exceptions.ChunkedEncodingError) as e:
            last_error = e
            if attempt < max_retries:
                wait = 1.5 * (2 ** attempt)
                logger.warning("TMDB 请求失败 (attempt %d/%d)，%ss 后重试: %s",
                               attempt + 1, max_retries + 1, wait, e)
                _time.sleep(wait)
        except Exception as e:
            last_error = e
            break

    logger.error("TMDB 请求最终失败: %s", last_error)
    return None


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

    base_url = cfg.get("tmdb_base_url", "") or "https://api.tmdb.org/3"
    url = f"{base_url}/{target_type}/{tmdb_id}"
    params = {
        "api_key": api_key,
        "language": "zh-CN"
    }

    try:
        resp = _tmdb_get(url, params=params, timeout=10)
        if resp is None or resp.status_code != 200:
            if resp:
                logger.error(f"❌ TMDB 查询失败: {resp.status_code} - {resp.text}")
            return None
        return resp.json()
    except Exception as e:
        logger.error(f"❌ TMDB 连接异常: {e}")

    return None