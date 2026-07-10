"""
残缺季雷达 — 按年节目录批量扫描剧集，检测文件数不足的 Season 文件夹。

复用现有模块：
- ``_SEASON_RE`` / ``_count_files_in_cd2_dir`` 来自 ``task_flow_service``
- ``get_tv_season_info`` 来自 ``organize_service``
- ``extract_tmdb_id_from_path`` 来自 ``utils.path_utils``
"""

import logging
import re
import time
from typing import Optional

from services.cd2_service import get_client
from services.task_flow_service import _SEASON_RE, _count_files_in_cd2_dir
from services.organize_service import get_tv_season_info
from utils.path_utils import extract_tmdb_id_from_path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 核心扫描逻辑
# ---------------------------------------------------------------------------

def check_incomplete_seasons(base_path: str, category: str = "") -> dict:
    """扫描指定目录下所有剧集的 Season 文件夹，检测文件数不完整的残缺季。

    Args:
        base_path: CD2 目录路径，如 ``/80003588/emby库/电视剧/国产剧/2026/``。
                   该目录下应存放以剧名为名的子目录（含 ``{tmdb=...}`` 标记）。
        category: 非空时用于日志上下文（如 "国产剧"）。

    Returns:
        {
            "total_shows_scanned": int,
            "total_seasons_checked": int,
            "incomplete_seasons": [
                {
                    "show_name": "某剧 (2026)",
                    "tmdb_id": 12345,
                    "season_num": 1,
                    "folder_name": "Season 1 - 4K",
                    "folder_path": "/.../Season 1 - 4K",
                    "actual_count": 8,
                    "expected_count": 10,
                },
                ...
            ],
            "empty_folders": [...],       # actual=0 的文件夹
            "unrecognized_folders": [...],# 无法提取 tmdb_id 的目录
        }
    """
    client = None
    try:
        client = get_client()
        return _do_check(client, base_path, category)
    finally:
        # CD2 client 由 cd2_service 管理生命周期，不在此处关闭
        pass


def _do_check(cd2, base_path: str, category: str = "") -> dict:
    """执行实际扫描逻辑（需要已连接的 CD2 client）。"""
    base_path = base_path.rstrip("/") + "/"

    # ---- 1. 获取年节目录下的所有子目录（剧集目录） ----
    try:
        top_files = cd2.get_sub_files(base_path)
    except Exception as e:
        logger.error("读取目录失败 '%s': %s", base_path, e)
        return {
            "total_shows_scanned": 0,
            "total_seasons_checked": 0,
            "incomplete_seasons": [],
            "empty_folders": [],
            "unrecognized_folders": [],
            "error": str(e),
        }

    show_dirs = [f for f in top_files if f.get("isDirectory")]
    if not show_dirs:
        logger.info("目录 '%s' 下没有子目录", base_path)
        return {
            "total_shows_scanned": 0,
            "total_seasons_checked": 0,
            "incomplete_seasons": [],
            "empty_folders": [],
            "unrecognized_folders": [],
        }

    incomplete_seasons: list[dict] = []
    empty_folders: list[dict] = []
    unrecognized: list[str] = []
    shows_scanned = 0
    total_seasons = 0

    # ---- 2. 遍历每部剧 ----
    for show in show_dirs:
        show_name = show.get("name", "")
        show_path = (base_path + show_name).rstrip("/") + "/"

        # 提取 tmdb_id
        tmdb_id = extract_tmdb_id_from_path(show_path)
        if tmdb_id is None:
            logger.debug("跳过无法识别 TMDB ID 的目录: %s", show_name)
            unrecognized.append(show_name)
            continue

        shows_scanned += 1

        # ---- 3. 获取该剧集下的所有子目录（Season 文件夹） ----
        try:
            show_files = cd2.get_sub_files(show_path)
        except Exception as e:
            logger.warning("读取剧集目录失败 '%s': %s", show_path, e)
            continue

        season_dirs = [f for f in show_files if f.get("isDirectory")]
        if not season_dirs:
            continue

        # ---- 4. 遍历每个 Season 文件夹 ----
        for sd in season_dirs:
            folder_name = sd.get("name", "")
            m = _SEASON_RE.match(folder_name)
            if not m:
                # 不是 Season 开头的文件夹，跳过（如 extra、featurettes 等）
                continue

            season_num = int(m.group(1))
            folder_path = show_path + folder_name
            total_seasons += 1

            # ---- 5. 获取 TMDB 预期集数 ----
            season_info = get_tv_season_info(tmdb_id, season_num)
            if season_info is None:
                logger.warning(
                    "[%s] S%d: TMDB 查询失败，跳过", show_name, season_num
                )
                continue

            expected_count = season_info.get("episode_count", 0)
            if expected_count == 0:
                logger.debug(
                    "[%s] S%d '%s': TMDB episode_count=0，跳过",
                    show_name, season_num, folder_name,
                )
                continue

            # ---- 6. 统计实际文件数 ----
            actual_count = _count_files_in_cd2_dir(
                cd2, folder_path, video_only=True, retries=2
            )

            # ---- 7. 判定 ----
            entry = {
                "show_name": show_name,
                "tmdb_id": tmdb_id,
                "season_num": season_num,
                "folder_name": folder_name,
                "folder_path": folder_path,
                "actual_count": actual_count,
                "expected_count": expected_count,
            }

            if actual_count == 0:
                empty_folders.append(entry)
            elif actual_count < expected_count:
                incomplete_seasons.append(entry)

            time.sleep(0.3)  # 温和限速，避免 CD2 压力

    logger.info(
        "残缺季核查完成: 扫描 %d 部剧 / %d 个季, "
        "残缺=%d, 空目录=%d, 无法识别=%d",
        shows_scanned, total_seasons,
        len(incomplete_seasons), len(empty_folders), len(unrecognized),
    )

    return {
        "total_shows_scanned": shows_scanned,
        "total_seasons_checked": total_seasons,
        "incomplete_seasons": incomplete_seasons,
        "empty_folders": empty_folders,
        "unrecognized_folders": unrecognized,
    }


# ---------------------------------------------------------------------------
# 单剧集残缺季检查
# ---------------------------------------------------------------------------

def check_single_show(show_path: str) -> dict:
    """检查单个剧集目录下的 Season 文件夹是否完整。

    与 ``check_incomplete_seasons`` 不同，此函数仅扫描单个剧集目录，
    适用于在目录列表中快速核查某一部剧的完整性。

    Args:
        show_path: 剧集目录完整路径，如
                   ``/80003588/emby库/电视剧/国产剧/2026/主角(2026) {tmdb=284110}/``

    Returns:
        {
            "show_name": "主角(2026) {tmdb=284110}",
            "show_path": "/80003588/.../主角(2026) {tmdb=284110}/",
            "tmdb_id": 284110,
            "total_seasons_checked": 3,
            "incomplete_seasons": [...],
            "empty_folders": [...],
            "complete_seasons": [...],
        }
    """
    client = None
    try:
        client = get_client()
        return _do_check_single_show(client, show_path)
    finally:
        pass


def _do_check_single_show(cd2, show_path: str) -> dict:
    """执行单剧集扫描逻辑（需要已连接的 CD2 client）。"""
    show_path = show_path.rstrip("/") + "/"

    # 提取剧名（路径最后一段）
    segments = show_path.rstrip("/").split("/")
    show_name = segments[-1] if segments else ""

    # 提取 tmdb_id
    tmdb_id = extract_tmdb_id_from_path(show_path)
    if tmdb_id is None:
        logger.warning("无法从路径提取 TMDB ID: %s", show_path)
        return {
            "show_name": show_name,
            "show_path": show_path,
            "tmdb_id": None,
            "total_seasons_checked": 0,
            "incomplete_seasons": [],
            "empty_folders": [],
            "complete_seasons": [],
            "error": "无法识别 TMDB ID",
        }

    # 获取该剧集下的所有子目录（Season 文件夹）
    try:
        show_files = cd2.get_sub_files(show_path)
    except Exception as e:
        logger.error("读取剧集目录失败 '%s': %s", show_path, e)
        return {
            "show_name": show_name,
            "show_path": show_path,
            "tmdb_id": tmdb_id,
            "total_seasons_checked": 0,
            "incomplete_seasons": [],
            "empty_folders": [],
            "complete_seasons": [],
            "error": str(e),
        }

    season_dirs = [f for f in show_files if f.get("isDirectory")]
    if not season_dirs:
        logger.info("剧集 '%s' 下没有 Season 子目录", show_name)
        return {
            "show_name": show_name,
            "show_path": show_path,
            "tmdb_id": tmdb_id,
            "total_seasons_checked": 0,
            "incomplete_seasons": [],
            "empty_folders": [],
            "complete_seasons": [],
        }

    incomplete_seasons: list[dict] = []
    empty_folders: list[dict] = []
    complete_seasons: list[dict] = []
    total_seasons = 0

    for sd in season_dirs:
        folder_name = sd.get("name", "")
        m = _SEASON_RE.match(folder_name)
        if not m:
            continue

        season_num = int(m.group(1))
        folder_path = show_path + folder_name
        total_seasons += 1

        # 获取 TMDB 预期集数
        season_info = get_tv_season_info(tmdb_id, season_num)
        if season_info is None:
            logger.warning("[%s] S%d: TMDB 查询失败，跳过", show_name, season_num)
            continue

        expected_count = season_info.get("episode_count", 0)
        if expected_count == 0:
            logger.debug("[%s] S%d: TMDB episode_count=0，跳过", show_name, season_num)
            continue

        # 统计实际文件数
        actual_count = _count_files_in_cd2_dir(
            cd2, folder_path, video_only=True, retries=2
        )

        entry = {
            "show_name": show_name,
            "tmdb_id": tmdb_id,
            "season_num": season_num,
            "folder_name": folder_name,
            "folder_path": folder_path,
            "actual_count": actual_count,
            "expected_count": expected_count,
        }

        if actual_count == 0:
            empty_folders.append(entry)
        elif actual_count < expected_count:
            incomplete_seasons.append(entry)
        else:
            complete_seasons.append(entry)

        time.sleep(0.3)

    logger.info(
        "单剧核查完成: '%s' — %d 个季, 完整=%d, 残缺=%d, 空=%d",
        show_name, total_seasons,
        len(complete_seasons), len(incomplete_seasons), len(empty_folders),
    )

    return {
        "show_name": show_name,
        "show_path": show_path,
        "tmdb_id": tmdb_id,
        "total_seasons_checked": total_seasons,
        "incomplete_seasons": incomplete_seasons,
        "empty_folders": empty_folders,
        "complete_seasons": complete_seasons,
    }
