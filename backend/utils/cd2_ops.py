"""
CD2 Utility Operations — safe file-system helpers for CloudDrive2.

These functions wrap the CD2 gRPC client to provide higher-level operations
that avoid common pitfalls (e.g. moving entire show roots while scrapers are
writing .nfo / images into them).
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# move_show_by_seasons — safe show-level move (season sub-folders only)
# ---------------------------------------------------------------------------

def move_show_by_seasons(
    cd2,                           # CD2Client instance
    source_show_path: str,
    target_parent_path: str,
    conflict_policy: int = 1,      # 1 = Rename on conflict (default)
) -> dict:
    """Move a TV show from one CD2 directory to another **without** touching
    the show root folder.

    Instead of moving the entire ``source_show_path`` (which may have active
    scraper processes writing .nfo / images into the root), this function:

    1. Creates the target show folder (``target_parent_path/<show_name>/``)
       if it doesn't already exist.
    2. Lists all **sub-directories** inside *source_show_path* (typically
       ``Season 1``, ``Season 2``, …).
    3. Moves each sub-directory into the target show folder individually.
    4. **Does NOT delete** the source root folder — it stays in place so
       ongoing scraper writes don't collide.

    Parameters
    ----------
    cd2 : CD2Client
        An authenticated CD2 gRPC client instance.
    source_show_path : str
        Full CD2 path of the source show root, e.g.
        ``/80003588/网盘整理/完结整理/电视剧/国产剧/2026/主角(2026) {tmdb=284110}``.
    target_parent_path : str
        Parent directory under which the show folder will be created, e.g.
        ``/80003588/emby库/电视剧/国产剧/2026``.
    conflict_policy : int
        0 = Overwrite, 1 = Rename (default), 2 = Skip.

    Returns
    -------
    dict
        ``{"success": True/False, "moved_seasons": N, "target_show_path": "...",
        "errors": [...]}``
    """
    # ---- sanitise paths ----
    src = source_show_path.rstrip("/")
    tgt_parent = target_parent_path.rstrip("/")

    # ---- 1. Extract show folder name ----
    show_name = src.rsplit("/", 1)[-1]
    target_show_path = f"{tgt_parent}/{show_name}"

    logger.info(
        "move_show_by_seasons: '%s' → '%s' (conflictPolicy=%d)",
        src, target_show_path, conflict_policy,
    )

    # ---- 2. Create target show directory if it doesn't exist ----
    try:
        # Check existence by listing parent; if show folder not found, create it
        existing = cd2.get_sub_files(tgt_parent)
        existing_names = {f.get("name", "") for f in existing}
        if show_name not in existing_names:
            logger.info("Creating target show folder: '%s'", target_show_path)
            mk_result = cd2.create_folder(
                parent_path=tgt_parent,
                folder_name=show_name,
            )
            if not mk_result.get("success"):
                return {
                    "success": False,
                    "stage": "mkdir_failed",
                    "error": f"无法创建目标剧集目录: {mk_result.get('errorMessage', '未知错误')}",
                    "target_show_path": target_show_path,
                }
        else:
            logger.info("Target show folder already exists: '%s'", target_show_path)
    except Exception as e:
        logger.warning("Error checking/creating target directory: %s", e)
        # Continue anyway — the move itself will fail if the target is invalid

    # ---- 3. List source directory, collect sub-directories only ----
    try:
        items = cd2.get_sub_files(src)
    except Exception as e:
        logger.exception("Failed to list source directory: %s", src)
        return {
            "success": False,
            "stage": "list_failed",
            "error": f"无法列出源目录: {e}",
            "target_show_path": target_show_path,
        }

    dirs = [f for f in items if f.get("isDirectory")]
    if not dirs:
        logger.info("No sub-directories found in '%s' — nothing to move", src)
        return {
            "success": True,
            "moved_seasons": 0,
            "target_show_path": target_show_path,
            "note": "源目录中没有子文件夹，无需移动",
        }

    # ---- 4. Move each sub-directory (Season folder) individually ----
    moved_count = 0
    errors = []

    for d in dirs:
        dir_name = d.get("name", "")
        source_path = d.get("fullPathName") or f"{src}/{dir_name}"

        logger.info("Moving '%s' → '%s/'", source_path, target_show_path)

        try:
            move_result = cd2.move_files(
                [source_path],
                target_show_path,
                conflict_policy=conflict_policy,
            )
            if move_result.get("success") or move_result.get("assumedSuccess"):
                moved_count += 1
                logger.info("  ✓ moved '%s'", dir_name)
            else:
                err_msg = move_result.get("errorMessage", "unknown")
                logger.error("  ✗ failed to move '%s': %s", dir_name, err_msg)
                errors.append({"name": dir_name, "error": err_msg})
        except Exception as e:
            logger.exception("  ✗ exception moving '%s': %s", dir_name, e)
            errors.append({"name": dir_name, "error": str(e)})

    # ---- 5. Source root is intentionally NOT deleted ----
    logger.info(
        "move_show_by_seasons done: %d/%d seasons moved, source root preserved",
        moved_count, len(dirs),
    )

    return {
        "success": len(errors) == 0,
        "moved_seasons": moved_count,
        "total_seasons": len(dirs),
        "target_show_path": target_show_path,
        "errors": errors if errors else None,
        "source_root_preserved": True,
    }
