"""
CD2 (CloudDrive2) Router

Exposes REST endpoints for querying CloudDrive2 directory contents,
used by the front-end TorrentCleanup page for media-vs-organized comparison.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Body

from services.cd2_service import get_client, close_client, get_cd2_media_dir, get_cd2_organized_dir

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/cd2/directories")
async def get_cd2_directories(
    media_dir: Optional[str] = Query(None, description="媒体库目录路径"),
    organized_dir: Optional[str] = Query(None, description="已完结整理目录路径"),
    with_stats: bool = Query(False, description="是否获取文件夹详情（文件数、总大小）。默认关闭以提升性能"),
    include_details: bool = Query(False, description="[已废弃] 请使用 with_stats"),
    media_with_stats: Optional[bool] = Query(None, description="仅媒体库侧获取文件夹详情。为 None 时跟随 with_stats"),
    organized_with_stats: Optional[bool] = Query(None, description="仅已完结侧获取文件夹详情。为 None 时跟随 with_stats"),
):
    """Return file lists for both the media-library directory and the
    organized/finished directory from CloudDrive2.

    **Query parameters** (optional — defaults come from env vars):
    - ``media_dir``:  e.g. ``/80003588/emby库/电视剧/国产剧/``
    - ``organized_dir``: e.g. ``/80003588/网盘整理/完结整理/电视剧/国产剧``
    - ``with_stats``: 默认为 False。设 True 时对两侧文件夹额外调用
      GetFileDetailProperties 获取 fileCount / totalSize。
    - ``media_with_stats`` / ``organized_with_stats``: 按侧独立控制 stats，
      为 None 时跟随 ``with_stats``。用于防止年份层级误展示统计数据。

    **Response**::

        {
          "media": [ { "name": "...", "size": 123, "isDirectory": true, ... }, ... ],
          "organized": [ ... ]
        }
    """
    # Resolve paths — explicit query param > config > env var
    resolved_media = media_dir or get_cd2_media_dir()
    resolved_organized = organized_dir or get_cd2_organized_dir()

    # with_stats takes precedence; include_details kept for backward compat
    do_stats = with_stats or include_details

    client = None
    try:
        client = get_client()
        result = client.fetch_both_directories(
            media_dir=resolved_media,
            organized_dir=resolved_organized,
            include_details=do_stats,
            media_include_details=media_with_stats,
            organized_include_details=organized_with_stats,
        )
        return {
            "media": result["media"],
            "organized": result["organized"],
            "paths": {
                "media": resolved_media,
                "organized": resolved_organized,
            },
        }
    except PermissionError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("Unexpected error fetching CD2 directories")
        raise HTTPException(status_code=500, detail=str(e))
    # Note: we intentionally do NOT close the client here — the singleton
    # keeps the channel alive for subsequent requests.  It will be torn down
    # when the process exits (or via close_client() called elsewhere).


@router.delete("/cd2/delete")
async def delete_cd2_items(
    paths: list[str] = Body(..., embed=True, description="要删除的文件/文件夹完整路径列表"),
    permanent: bool = Body(False, description="是否彻底删除（不放入回收站）。仅 aliyundrive 支持"),
):
    """Delete files or folders from CloudDrive2.

    - By default (``permanent=False``), items are moved to the recycle bin.
    - Set ``permanent=True`` to permanently delete (only supported by aliyundrive).

    **Request**::

        {
          "paths": ["/80003588/emby库/电视剧/国产剧/2026/某剧/Season 1"],
          "permanent": false
        }

    **Response**::

        {
          "success": true,
          "deletedCount": 1,
          "resultFilePaths": ["/80003588/emby库/电视剧/国产剧/2026/某剧/Season 1"],
          "permanent": false
        }
    """
    if not paths:
        raise HTTPException(status_code=400, detail="paths 不能为空")

    # Sanitise: remove empty/whitespace-only entries
    clean_paths = [p.strip() for p in paths if p and p.strip()]
    if not clean_paths:
        raise HTTPException(status_code=400, detail="没有有效的路径")

    logger.info(
        "DELETE /api/cd2/delete — %d paths, permanent=%s",
        len(clean_paths), permanent,
    )
    for i, p in enumerate(clean_paths):
        logger.info("  [%d] %s", i + 1, p)

    client = None
    try:
        client = get_client()
        result = client.delete_files(paths=clean_paths, permanent=permanent)

        if not result.get("success"):
            raise HTTPException(
                status_code=502,
                detail=f"CD2 删除失败: {result.get('errorMessage', '未知错误')}",
            )

        deleted_paths = result.get("resultFilePaths", [])
        deleted_count = len(deleted_paths)
        logger.info(
            "CD2 delete completed — %d items deleted (permanent=%s)",
            deleted_count, permanent,
        )

        # CD2 删除成功后，启动延迟清理线程（3 分钟后尝试清理 Emby 残留僵尸记录）
        # 延迟 3 分钟是为了让 symedia 的 webhook 通知流程有机会正常完成；
        # 如果 symedia 已成功清理，此处的 search 将返回空（无需操作）。
        if deleted_paths:
            import threading
            import time as _time
            from services.emby_service import cleanup_emby_zombie

            def _delayed_cleanup():
                _time.sleep(180)  # 3 分钟
                for p in deleted_paths:
                    try:
                        cleanup_emby_zombie(p)
                    except Exception:
                        pass  # 清理失败不抛异常

            threading.Thread(target=_delayed_cleanup, daemon=True).start()
            logger.info("Emby zombie cleanup scheduled in 3 min for %d path(s)", deleted_count)

        return {
            "success": True,
            "deletedCount": deleted_count,
            "resultFilePaths": result.get("resultFilePaths", []),
            "permanent": permanent,
        }
    except HTTPException:
        raise
    except PermissionError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("Unexpected error deleting CD2 items")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cd2/mkdir")
async def create_cd2_folder(
    parent_path: str = Body(..., description="父目录完整路径"),
    folder_name: str = Body(..., description="要创建的文件夹名称"),
):
    """Create a new folder in CloudDrive2.

    **Request**::

        {
          "parent_path": "/80003588/emby库/电视剧/国产剧/2026/",
          "folder_name": "主角(2026) {tmdb=284110}"
        }

    **Response**::

        {
          "success": true,
          "folder": { "name": "...", "fullPathName": "...", ... }
        }
    """
    if not parent_path or not parent_path.strip():
        raise HTTPException(status_code=400, detail="parent_path 不能为空")
    if not folder_name or not folder_name.strip():
        raise HTTPException(status_code=400, detail="folder_name 不能为空")

    logger.info("POST /api/cd2/mkdir — '%s' under '%s'", folder_name.strip(), parent_path.strip())

    try:
        client = get_client()
        result = client.create_folder(
            parent_path=parent_path.strip(),
            folder_name=folder_name.strip(),
        )

        if not result.get("success"):
            raise HTTPException(
                status_code=502,
                detail=f"CD2 创建文件夹失败: {result.get('errorMessage', '未知错误')}",
            )

        return {
            "success": True,
            "folder": result.get("folder"),
        }
    except HTTPException:
        raise
    except PermissionError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("Unexpected error creating CD2 folder")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cd2/move")
async def move_cd2_items(
    source_paths: list[str] = Body(..., embed=True, description="要移动的文件/文件夹完整路径列表"),
    dest_path: str = Body(..., description="目标父目录路径"),
    conflict_policy: int = Body(1, description="冲突策略: 0=覆盖, 1=自动重命名(默认), 2=跳过"),
):
    """Move files or folders within CloudDrive2.

    **Request**::

        {
          "source_paths": ["/80003588/网盘整理/完结整理/电视剧/国产剧/2026/某剧/Season 1"],
          "dest_path": "/80003588/emby库/电视剧/国产剧/2026/某剧/",
          "conflict_policy": 1
        }

    **Response**::

        {
          "success": true,
          "movedCount": 1,
          "resultFilePaths": ["/80003588/emby库/电视剧/国产剧/2026/某剧/Season 1"],
          "destPath": "/80003588/emby库/电视剧/国产剧/2026/某剧/"
        }
    """
    if not source_paths:
        raise HTTPException(status_code=400, detail="source_paths 不能为空")
    if not dest_path or not dest_path.strip():
        raise HTTPException(status_code=400, detail="dest_path 不能为空")

    clean_sources = [p.strip() for p in source_paths if p and p.strip()]
    clean_dest = dest_path.strip()
    if not clean_sources:
        raise HTTPException(status_code=400, detail="没有有效的源路径")

    logger.info(
        "POST /api/cd2/move — %d paths → '%s' (conflictPolicy=%d)",
        len(clean_sources), clean_dest, conflict_policy,
    )
    for i, p in enumerate(clean_sources):
        logger.info("  [%d] %s", i + 1, p)

    client = None
    try:
        client = get_client()
        result = client.move_files(
            source_paths=clean_sources,
            dest_path=clean_dest,
            conflict_policy=conflict_policy,
        )

        if not result.get("success"):
            raise HTTPException(
                status_code=502,
                detail=f"CD2 移动失败: {result.get('errorMessage', '未知错误')}",
            )

        moved_count = len(result.get("resultFilePaths", []))
        logger.info("CD2 move completed — %d items moved", moved_count)

        return {
            "success": True,
            "movedCount": moved_count,
            "resultFilePaths": result.get("resultFilePaths", []),
            "destPath": clean_dest,
        }
    except HTTPException:
        raise
    except PermissionError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("Unexpected error moving CD2 items")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cd2/move_show_seasons")
async def move_show_seasons(
    source_show_path: str = Body(..., description="源剧集根目录完整路径"),
    target_parent_path: str = Body(..., description="目标父目录路径（年份目录）"),
    conflict_policy: int = Body(1, description="冲突策略: 0=覆盖, 1=自动重命名(默认), 2=跳过"),
):
    """将剧集从已完结目录逐季移动到媒体库，而非移动整剧根目录。

    与 ``POST /cd2/move`` 不同，此端点：
    1. 先在目标下创建同名剧集根目录（如不存在）
    2. 仅移动源目录中的子文件夹（Season 1, Season 2, …）
    3. **不删除**源剧集根目录，避免与刮削器的文件锁冲突

    **Request**::

        {
          "source_show_path": "/80003588/网盘整理/完结整理/电视剧/国产剧/2026/主角(2026) {tmdb=284110}",
          "target_parent_path": "/80003588/emby库/电视剧/国产剧/2026",
          "conflict_policy": 1
        }

    **Response**::

        {
          "success": true,
          "moved_seasons": 2,
          "total_seasons": 2,
          "target_show_path": "/80003588/emby库/电视剧/国产剧/2026/主角(2026) {tmdb=284110}",
          "errors": null,
          "source_root_preserved": true
        }
    """
    if not source_show_path or not source_show_path.strip():
        raise HTTPException(status_code=400, detail="source_show_path 不能为空")
    if not target_parent_path or not target_parent_path.strip():
        raise HTTPException(status_code=400, detail="target_parent_path 不能为空")

    logger.info(
        "POST /api/cd2/move_show_seasons — '%s' → '%s' (conflictPolicy=%d)",
        source_show_path.strip(), target_parent_path.strip(), conflict_policy,
    )

    try:
        from utils.cd2_ops import move_show_by_seasons

        client = get_client()
        result = move_show_by_seasons(
            cd2=client,
            source_show_path=source_show_path.strip(),
            target_parent_path=target_parent_path.strip(),
            conflict_policy=conflict_policy,
        )

        if not result.get("success"):
            raise HTTPException(
                status_code=502,
                detail=result.get("error", "移动失败"),
            )

        return result

    except HTTPException:
        raise
    except PermissionError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("Unexpected error in move_show_seasons")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 残缺季雷达
# ---------------------------------------------------------------------------

from pydantic import BaseModel


class CheckIncompleteRequest(BaseModel):
    path: str = Body(..., description="CD2 目录路径，例如 /80003588/emby库/电视剧/国产剧/2026/")


@router.post("/directories/check-incomplete")
async def check_incomplete_seasons(req: CheckIncompleteRequest):
    """扫描指定目录下所有剧集，检测残缺 Season 文件夹。

    **Request Body**::

        { "path": "/80003588/emby库/电视剧/国产剧/2026/" }

    **Response**::

        {
          "total_shows_scanned": 15,
          "total_seasons_checked": 42,
          "incomplete_seasons": [
            {
              "show_name": "某剧名称 (2026)",
              "tmdb_id": 12345,
              "season_num": 1,
              "folder_name": "Season 1 - 4K版本",
              "folder_path": "/.../Season 1 - 4K版本",
              "actual_count": 8,
              "expected_count": 10
            }
          ],
          "empty_folders": [...],
          "unrecognized_folders": ["未命名乱码文件夹"]
        }
    """
    from services.checker_service import check_incomplete_seasons as do_check

    try:
        result = do_check(base_path=req.path)
        return result
    except Exception as e:
        logger.exception("残缺季核查失败: path=%s", req.path)
        raise HTTPException(status_code=500, detail=str(e))


class CheckSingleShowRequest(BaseModel):
    path: str = Body(..., description="单个剧集目录路径，例如 /80003588/emby库/电视剧/国产剧/2026/主角(2026) {tmdb=284110}/")


@router.post("/directories/check-show-incomplete")
async def check_single_show_incomplete(req: CheckSingleShowRequest):
    """检查单个剧集目录下所有 Season 的文件完整性。

    与 ``POST /directories/check-incomplete``（批量扫描整个年份目录）不同，
    此端点仅扫描一个剧集目录，适用于在列表中快速核查某部剧。

    **Request Body**::

        { "path": "/80003588/emby库/电视剧/国产剧/2026/主角(2026) {tmdb=284110}/" }

    **Response**::

        {
          "show_name": "主角(2026) {tmdb=284110}",
          "show_path": "/80003588/.../",
          "tmdb_id": 284110,
          "total_seasons_checked": 3,
          "incomplete_seasons": [...],
          "empty_folders": [...],
          "complete_seasons": [...]
        }
    """
    from services.checker_service import check_single_show as do_check_single

    try:
        result = do_check_single(show_path=req.path)
        return result
    except Exception as e:
        logger.exception("单剧残缺季核查失败: path=%s", req.path)
        raise HTTPException(status_code=500, detail=str(e))
