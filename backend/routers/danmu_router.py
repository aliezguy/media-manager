"""弹幕管理路由 — /api/danmu/*（纯代理 MisakaDanmaku 外部控制 API）。

按用户要求去掉了匹配/汉化/分类层：弹幕库列表直接展示弹幕库原始数据。
- services/danmu_service.py  纯 HTTP 代理（X-API-KEY、重试退避、错误包装）
- 本 router                 错误映射 + camelCase → snake_case 转换

数据存储：纯代理，**不落本项目库**。完整 API 文档见 docs/danmu-api.md。
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import danmu_service as ds

logger = logging.getLogger("uvicorn")

router = APIRouter()


# ---------------------------------------------------------------------------
# pydantic 请求模型（参照 cd2_router 把模型放 router 内）
# ---------------------------------------------------------------------------
class DanmuSearchRequest(BaseModel):
    keyword: str
    season: Optional[int] = None
    episode: Optional[str] = None


class EditedImportEpisode(BaseModel):
    provider: str
    episodeId: str
    title: str = ""
    episodeIndex: int
    url: Optional[str] = None


class EditedImportRequest(BaseModel):
    searchId: str
    result_index: int
    title: Optional[str] = None
    episodes: list[EditedImportEpisode]
    tmdbId: Optional[int] = None


# ---------------------------------------------------------------------------
# 错误映射
# ---------------------------------------------------------------------------
def _http(e: Exception) -> HTTPException:
    """DanmuConfigMissing → 400；连接/超时 → 504；上游错误 → 502。"""
    if isinstance(e, ds.DanmuConfigMissing):
        return HTTPException(status_code=400, detail=str(e))
    if isinstance(e, ds.DanmuUpstreamError):
        if e.status_code is None:
            return HTTPException(status_code=504, detail=str(e))
        return HTTPException(status_code=502, detail=f"{e} {e.body or ''}".strip())
    return HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 字段转换（camelCase → snake_case，纯展示层，无业务逻辑）
# ---------------------------------------------------------------------------
def _convert_source(s: dict) -> dict:
    """LibrarySourceBrief / SourceInfo camelCase → snake_case。"""
    return {
        "source_id": s.get("sourceId"),
        "provider_name": s.get("providerName"),
        "is_favorited": s.get("isFavorited"),
        "incremental_refresh_enabled": s.get("incrementalRefreshEnabled"),
        "is_finished": s.get("isFinished"),
    }


def _convert_anime(a: dict) -> dict:
    """LibraryAnimeInfo / ControlAnimeDetailsResponse → snake_case。

    备选名字/原名仅在详情接口（ControlAnimeDetailsResponse）返回，列表接口无——
    所以 alias_cn1/name_en 等在列表里为 None，抽屉详情才有值。
    """
    return {
        "anime_id": a.get("animeId"),
        "danmu_title": a.get("title"),
        "type": a.get("type"),
        "season": a.get("season"),
        "year": a.get("year"),
        "episode_count": a.get("episodeCount"),
        "source_count": a.get("sourceCount"),
        "sources": [_convert_source(s) for s in (a.get("sources") or [])],
        "group_name": a.get("groupName"),
        "image_url": a.get("imageUrl"),
        "tmdb_id": a.get("tmdbId"),
        # 备选名字（名字1/2/3）与原名（英文/日文/罗马音）— 仅详情有值
        "alias_cn1": a.get("aliasCn1"),
        "alias_cn2": a.get("aliasCn2"),
        "alias_cn3": a.get("aliasCn3"),
        "name_en": a.get("nameEn"),
        "name_jp": a.get("nameJp"),
        "name_romaji": a.get("nameRomaji"),
    }


def _convert_episode(e: dict) -> dict:
    """EpisodeDetail / ProviderEpisodeInfo → snake_case。"""
    return {
        "episode_id": e.get("episodeId") or e.get("episode_id"),
        "provider": e.get("provider"),
        "title": e.get("title"),
        "episode_index": e.get("episodeIndex") or e.get("episode_index"),
        "comment_count": e.get("commentCount"),
        "url": e.get("url"),
    }


def _convert_task(t: dict) -> dict:
    """TaskInfo → snake_case。status 为上游中文枚举（等待/运行中/已完成/失败），原样透传。"""
    return {
        "task_id": t.get("taskId"),
        "title": t.get("title"),
        "status": t.get("status"),
        "progress": t.get("progress"),
        "description": t.get("description"),
        "created_at": t.get("createdAt"),
        "is_system_task": t.get("isSystemTask"),
        "queue_type": t.get("queueType"),
        "task_type": t.get("taskType"),
    }


# ---------------------------------------------------------------------------
# 1. 状态 / 2. 弹幕库列表（直接展示原始数据）
# ---------------------------------------------------------------------------
@router.get("/danmu/status")
def danmu_status():
    cfg = ds.get_danmu_config()
    return {"configured": ds.is_configured(), "base_url": cfg["base_url"]}


@router.get("/danmu/library")
def danmu_library():
    try:
        items = [_convert_anime(a) for a in ds.get_library()]
    except Exception as e:
        raise _http(e) from e
    return {"items": items, "total": len(items)}


# ---------------------------------------------------------------------------
# 3. 详情抽屉三级懒加载
# ---------------------------------------------------------------------------
@router.get("/danmu/library/anime/{anime_id}/sources")
def danmu_anime_sources(anime_id: int):
    try:
        return [_convert_source(s) for s in ds.get_anime_sources(anime_id)]
    except Exception as e:
        raise _http(e) from e


@router.get("/danmu/library/source/{source_id}/episodes")
def danmu_source_episodes(source_id: int):
    try:
        return [_convert_episode(e) for e in ds.get_source_episodes(source_id)]
    except Exception as e:
        raise _http(e) from e


@router.get("/danmu/library/anime/{anime_id}")
def danmu_anime_detail(anime_id: int):
    """作品详情（含上游 tmdbId/groupId 等元数据，仅展示用）。"""
    try:
        return _convert_anime(ds.get_anime_detail(anime_id))
    except Exception as e:
        raise _http(e) from e


# ---------------------------------------------------------------------------
# 4. 搜索导入（Tab2）
# ---------------------------------------------------------------------------
@router.post("/danmu/search")
def danmu_search(req: DanmuSearchRequest):
    try:
        return ds.search(req.keyword, req.season, req.episode)
    except Exception as e:
        raise _http(e) from e


@router.get("/danmu/search/episodes")
def danmu_search_episodes(searchId: str, result_index: int):
    try:
        return [_convert_episode(e) for e in ds.get_search_episodes(searchId, result_index)]
    except Exception as e:
        raise _http(e) from e


@router.post("/danmu/import/edited")
def danmu_import_edited(req: EditedImportRequest):
    episodes = [ep.model_dump(exclude_none=True) for ep in req.episodes]
    try:
        # tmdbId 上游 schema 为 string（可选，前端目前不传）
        resp = ds.import_edited(
            req.searchId, req.result_index, req.title, episodes,
            tmdbId=str(req.tmdbId) if req.tmdbId is not None else None
        )
    except Exception as e:
        raise _http(e) from e
    return {"taskId": resp.get("taskId"), "message": resp.get("message", "导入任务已提交")}


@router.get("/danmu/tasks")
def danmu_tasks(status: str = "all"):
    """任务列表（status: all/in_progress/completed）→ TaskInfo[]，活动任务由前端置顶。"""
    try:
        return [_convert_task(t) for t in ds.list_tasks(status)]
    except Exception as e:
        raise _http(e) from e


@router.get("/danmu/tasks/{task_id}")
def danmu_task(task_id: str):
    try:
        return ds.get_task(task_id)
    except Exception as e:
        raise _http(e) from e


# ---------------------------------------------------------------------------
# 5. 删除（均 202 后台任务）
# ---------------------------------------------------------------------------
@router.delete("/danmu/library/anime/{anime_id}")
def danmu_delete_anime(anime_id: int):
    try:
        resp = ds.delete_anime(anime_id)
        return {"taskId": resp.get("taskId")}
    except Exception as e:
        raise _http(e) from e


@router.delete("/danmu/library/source/{source_id}")
def danmu_delete_source(source_id: int):
    try:
        resp = ds.delete_source(source_id)
        return {"taskId": resp.get("taskId")}
    except Exception as e:
        raise _http(e) from e


@router.delete("/danmu/library/episode/{episode_id}")
def danmu_delete_episode(episode_id: int):
    try:
        resp = ds.delete_episode(episode_id)
        return {"taskId": resp.get("taskId")}
    except Exception as e:
        raise _http(e) from e
