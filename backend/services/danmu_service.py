"""MisakaDanmaku 外部控制 API 的 requests 代理封装。

纯 HTTP 层：鉴权头、重试退避、JSON 解析、错误包装。**不含业务逻辑**
（展示/转换逻辑在 routers/danmu_router.py）。

完整 API 文档见项目根 docs/danmu-api.md。
"""

import logging
import time as _time

import requests

from config.settings import load_config

logger = logging.getLogger("uvicorn")


# ---------------------------------------------------------------------------
# 自定义异常
# ---------------------------------------------------------------------------
class DanmuConfigMissing(Exception):
    """未配置弹幕服务地址/密钥。"""


class DanmuUpstreamError(Exception):
    """上游返回非 2xx 或网络异常。"""

    def __init__(self, message: str, status_code: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
def get_danmu_config() -> dict:
    """从 config.yaml 读弹幕服务配置，每次现读（避免改配置后不生效）。"""
    cfg = load_config()
    return {
        "base_url": (cfg.get("danmu_base_url") or "").rstrip("/"),
        "api_key": cfg.get("danmu_api_key") or "",
    }


def is_configured() -> bool:
    cfg = get_danmu_config()
    return bool(cfg["base_url"] and cfg["api_key"])


# ---------------------------------------------------------------------------
# 底层请求（重试 + 指数退避，参照 tmdb_service._tmdb_get）
# ---------------------------------------------------------------------------
def _danmu_request(method: str, path: str, *, params: dict | None = None,
                   json_body: dict | None = None, timeout: int = 15,
                   max_retries: int = 2) -> requests.Response | None:
    """带 X-API-KEY 头的请求；网络错误重试；返回 Response 或 None。

    代理绕过：macOS 系统网络代理（如 Clash/Surge 127.0.0.1:1088）会被 requests
    通过 trust_env 自动使用，给请求叠加额外延迟。弹幕服务域名已验证可直连
    （0.05s），这里显式禁用代理直连，避免慢代理导致超时。
    """
    cfg = get_danmu_config()
    headers = {
        "X-API-KEY": cfg["api_key"],
        "Content-Type": "application/json",
    }
    url = f"{cfg['base_url']}{path}"
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            resp = requests.request(
                method, url, params=params, json=json_body,
                headers=headers, timeout=timeout,
                proxies={"http": None, "https": None},
            )
            return resp
        except (requests.exceptions.SSLError, requests.exceptions.ConnectionError,
                requests.exceptions.Timeout, requests.exceptions.ChunkedEncodingError) as e:
            last_error = e
            if attempt < max_retries:
                wait = 1.5 * (2 ** attempt)
                logger.warning("弹幕服务请求失败 (attempt %d/%d)，%ss 后重试: %s",
                               attempt + 1, max_retries + 1, wait, e)
                _time.sleep(wait)
        except Exception as e:
            last_error = e
            break

    logger.error("弹幕服务请求最终失败: %s", last_error)
    raise DanmuUpstreamError(f"弹幕服务连接失败: {last_error}")


def _danmu_json(method: str, path: str, *, params: dict | None = None,
                json_body: dict | None = None, timeout: int = 15) -> dict | list:
    """封装 _danmu_request：未配置 → DanmuConfigMissing；网络失败 → DanmuUpstreamError；
    上游非 2xx → DanmuUpstreamError(status, body)；成功 → resp.json()。"""
    if not is_configured():
        raise DanmuConfigMissing("未配置弹幕服务地址/密钥（基础配置 → 弹幕服务）")

    resp = _danmu_request(method, path, params=params, json_body=json_body, timeout=timeout)
    if resp is None:
        raise DanmuUpstreamError("弹幕服务无响应")

    if resp.status_code >= 400:
        body_text = resp.text[:300] if resp.text else ""
        logger.error("弹幕服务 %s %s → %s: %s", method, path, resp.status_code, body_text)
        raise DanmuUpstreamError(
            f"弹幕服务错误({resp.status_code})", status_code=resp.status_code, body=body_text
        )

    if not resp.content:
        return {}
    try:
        return resp.json()
    except ValueError:
        return {}


# ---------------------------------------------------------------------------
# 领域代理 — 与上游端点一一对应
# ---------------------------------------------------------------------------
def search(keyword: str, season: int | None = None, episode: str | None = None) -> dict:
    """GET /api/control/search → {"searchId", "results"}。searchId 上游缓存 10 分钟。

    上游并发搜多源，实测单次需 ~21s（斩神 18 条结果），故 timeout 放宽到 45s
    （其余端点仍用默认 15s）。代理已在 _danmu_request 中显式绕过。
    """
    params = {"keyword": keyword}
    if season is not None:
        params["season"] = season
    if episode:
        params["episode"] = episode
    return _danmu_json("GET", "/api/control/search", params=params, timeout=45)


def get_search_episodes(search_id: str, result_index: int) -> list:
    """GET /api/control/episodes?searchId=&result_index= → ProviderEpisodeInfo[]。"""
    return _danmu_json("GET", "/api/control/episodes",
                       params={"searchId": search_id, "result_index": result_index})


def import_edited(search_id: str, result_index: int, title: str | None,
                  episodes: list[dict], **meta_ids) -> dict:
    """POST /api/control/import/edited → {"status","message","taskId"}（202 后台任务）。

    episodes 项字段：provider / episodeId / title / episodeIndex / url（可选）。
    meta_ids 可选：tmdbId / tvdbId / doubanId / imdbId / bangumiId。
    """
    body = {
        "searchId": search_id,
        "result_index": result_index,
        "episodes": episodes,
    }
    if title:
        body["title"] = title
    body.update({k: v for k, v in meta_ids.items() if v is not None})
    return _danmu_json("POST", "/api/control/import/edited", json_body=body)


def get_library() -> list:
    """GET /api/control/library → LibraryAnimeInfo[]（含 sources[] 摘要，已按作品去重）。"""
    return _danmu_json("GET", "/api/control/library")


def get_anime_detail(anime_id: int) -> dict:
    """GET /api/control/library/anime/{animeId} → ControlAnimeDetailsResponse。"""
    return _danmu_json("GET", f"/api/control/library/anime/{anime_id}")


def update_anime(anime_id: int, patch: dict) -> dict:
    """PUT /api/control/library/anime/{animeId}（AnimeDetailUpdate，title/type/season 必填）。

    手动绑定持久化用 patch={"tmdbId": int}。
    """
    return _danmu_json("PUT", f"/api/control/library/anime/{anime_id}", json_body=patch)


def delete_anime(anime_id: int) -> dict:
    """DELETE /api/control/library/anime/{animeId} → ControlTaskResponse（后台任务）。"""
    return _danmu_json("DELETE", f"/api/control/library/anime/{anime_id}")


def get_anime_sources(anime_id: int) -> list:
    """GET /api/control/library/anime/{animeId}/sources → SourceInfo[]。"""
    return _danmu_json("GET", f"/api/control/library/anime/{anime_id}/sources")


def delete_source(source_id: int) -> dict:
    """DELETE /api/control/library/source/{sourceId} → ControlTaskResponse。"""
    return _danmu_json("DELETE", f"/api/control/library/source/{source_id}")


def get_source_episodes(source_id: int) -> list:
    """GET /api/control/library/source/{sourceId}/episodes → EpisodeDetail[]（含 commentCount）。"""
    return _danmu_json("GET", f"/api/control/library/source/{source_id}/episodes")


def delete_episode(episode_id: int) -> dict:
    """DELETE /api/control/library/episode/{episodeId} → ControlTaskResponse。"""
    return _danmu_json("DELETE", f"/api/control/library/episode/{episode_id}")


def get_task(task_id: str) -> dict:
    """GET /api/control/tasks/{taskId} → TaskInfo{taskId,title,status,progress,description,...}。"""
    return _danmu_json("GET", f"/api/control/tasks/{task_id}")


def list_tasks(status: str = "all") -> list:
    """GET /api/control/tasks?status= → TaskInfo[]。

    status 过滤：all / in_progress / completed。status 字段为上游中文枚举
    （等待/运行中/已完成/失败），原样透传，前端做展示映射。
    """
    return _danmu_json("GET", "/api/control/tasks", params={"status": status})
