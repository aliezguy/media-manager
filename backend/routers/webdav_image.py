"""WebDAV 图片代理 API — 媒体图 / 演员头像，缓存优先、TMDB 兜底、自动回写。"""
from typing import Literal

from fastapi import APIRouter, HTTPException

from services.webdav_image_service import migrate_local_people_to_webdav, serve_media_image, serve_people_image

router = APIRouter()


@router.get("/webdav-image/media")
async def get_media_image(
    media_type: Literal["movie", "tv"],
    tmdb_id: int,
    name: str,
    year: int,
    image_type: Literal["poster", "backdrop", "season-poster"],
    season: int | None = None,
):
    if image_type == "season-poster" and season is None:
        raise HTTPException(status_code=400, detail="season 参数必填（season-poster）")
    return await serve_media_image(media_type, tmdb_id, name, year, image_type, season)


@router.get("/webdav-image/people")
async def get_people_image(path: str):
    """按 DB local_image_path 取头像（如 '张/张译-tmdb-12345/folder.png'）。"""
    return await serve_people_image(path)


@router.post("/webdav-image/migrate-people")
async def migrate_people():
    """一次性/增量把本地 people/ 头像同步到 WebDAV（幂等）。"""
    return await migrate_local_people_to_webdav()
