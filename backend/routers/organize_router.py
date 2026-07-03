"""
Organize Router — Torrent analysis + TMDB lookup + Auto Process.

POST /api/organize/analyze
POST /api/organize/batch_start
POST /api/organize/auto_process
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Body, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from services.organize_service import analyze_torrent
from services.task_flow_service import auto_process_show

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class SeasonInfo(BaseModel):
    season_number: int
    downloaded_episodes: int = 0
    folder_path: str = ""
    size_bytes: int = 0


class TorrentInfo(BaseModel):
    hash: str
    name: str
    qb_category: str = ""
    size: int = 0
    added_on: Optional[str] = None


class BatchShowItem(BaseModel):
    title: str
    year: int
    tmdb_id: int
    category: str = ""
    total_episodes: int = 0
    overview: str = ""
    seasons: List[SeasonInfo] = []
    torrents: List[TorrentInfo] = []


class BatchStartRequest(BaseModel):
    shows: List[BatchShowItem]
    qb_config_id: str = ""


class AutoProcessRequest(BaseModel):
    torrent_name: str
    tmdb_id: Optional[int] = None
    qb_config_id: str = ""
    category: str = ""


class AutoProcessBatchRequest(BaseModel):
    seeds: List[AutoProcessRequest]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/organize/analyze")
async def analyze(req: dict = Body(...)):
    """Parse a torrent name and look up TMDB metadata.

    **Request**::

        { "torrent_name": "主角(2026) {tmdb=284110}" }

    **Response**::

        {
          "success": true,
          "title": "主角",
          "year": "2026",
          "season": 1,
          "total_episodes": 24,
          "tmdb_id": 284110,
          "tmdb_name": "主角",
          "source": "regex"
        }
    """
    name = req.get("torrent_name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="torrent_name is required")

    try:
        result = analyze_torrent(name)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("Unexpected error in analyze_torrent")
        raise HTTPException(status_code=500, detail=str(e))

    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error", "Analysis failed"))

    return result


@router.post("/organize/batch_start")
async def batch_start(req: BatchStartRequest, db: Session = Depends(get_db)):
    """Start batch organize workflow for one or more shows.

    **Request**::

        {
          "shows": [
            {
              "title": "翘楚",
              "year": 2026,
              "tmdb_id": 289271,
              "category": "国产剧",
              "total_episodes": 24,
              "overview": "...",
              "seasons": [
                {"season_number": 1, "downloaded_episodes": 24,
                 "folder_path": "", "size_bytes": 0}
              ],
              "torrents": [
                {"hash": "abc123...", "name": "翘楚(2026) {tmdb=289271}",
                 "qb_category": "国产剧", "size": 50000000000}
              ]
            }
          ],
          "qb_config_id": ""
        }

    **Response**::

        {
          "success": true,
          "total": 1,
          "ok": 1,
          "errors": 0,
          "results": [...]
        }
    """
    # Batch organize is now superseded by auto_process.
    # Process each show individually through auto_process_show.
    results = []
    for show in req.shows:
        try:
            r = auto_process_show(
                torrent_name=show.title,
                tmdb_id=show.tmdb_id,
                qb_config_id=req.qb_config_id,
                category=show.category,
                db=db,
            )
            results.append(r)
        except Exception as e:
            logger.exception(f"batch_start: failed for {show.title}")
            results.append({
                "success": False,
                "stage": "error",
                "message": str(e),
                "tmdb_id": show.tmdb_id,
            })

    ok_count = sum(1 for r in results if r.get("success"))
    return {
        "success": ok_count == len(results),
        "total": len(results),
        "ok": ok_count,
        "errors": len(results) - ok_count,
        "results": results,
    }


@router.post("/organize/auto_process")
async def auto_process(req: AutoProcessRequest, db: Session = Depends(get_db)):
    """Run automated completion-validation + smart-comparison for a single show.

    This is the Stage 1 pre-filter. It validates that ALL seasons are truly
    complete in the organized directory before deciding what to delete/keep.

    **Request**::

        {
          "torrent_name": "翘楚(2026) {tmdb=289271}",
          "tmdb_id": 289271,
          "qb_config_id": "",
          "category": "国产剧"
        }

    **Response**::

        {
          "success": true,
          "stage": "waiting_for_delete_webhook",
          "message": "已删除媒体库旧版本，等待Emby确认后执行移动",
          "task_id": 1,
          "tmdb_id": 289271,
          "details": {
            "title": "翘楚",
            "year": "2026",
            "category": "国产剧",
            "total_seasons": 1,
            "total_episodes": 24,
            "season_validation": [...],
            "comparison_results": [...]
          }
        }
    """
    name = req.torrent_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="torrent_name is required")

    try:
        result = auto_process_show(
            torrent_name=name,
            tmdb_id=req.tmdb_id,
            qb_config_id=req.qb_config_id,
            category=req.category,
            db=db,
        )
        return result
    except Exception as e:
        logger.exception("auto_process failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/organize/auto_process_batch")
async def auto_process_batch(req: AutoProcessBatchRequest, db: Session = Depends(get_db)):
    """Run auto_process for multiple shows (batch mode)."""
    results = []
    for seed in req.seeds:
        try:
            r = auto_process_show(
                torrent_name=seed.torrent_name,
                tmdb_id=seed.tmdb_id,
                qb_config_id=seed.qb_config_id,
                category=seed.category,
                db=db,
            )
            results.append(r)
        except Exception as e:
            logger.exception(f"auto_process_batch: failed for {seed.torrent_name}")
            results.append({
                "success": False,
                "stage": "error",
                "message": str(e),
                "tmdb_id": seed.tmdb_id,
            })

    ok_count = sum(1 for r in results if r.get("success"))
    return {
        "success": ok_count == len(results),
        "total": len(results),
        "ok": ok_count,
        "errors": len(results) - ok_count,
        "results": results,
    }
