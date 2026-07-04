"""
任务流服务 — 自动洗版/清理工作流的核心编排逻辑。

两阶段状态机（数据库驱动，无内存状态）：

  阶段一  POST /api/organize/auto_process
          完整性校验 → 智能对比 → 前置删除 → AutoTaskFlow(INIT)
          首次导入及混合场景：内联移动 → 校验 → 删除种子。

  阶段二  Emby library.deleted webhook（仅整剧删除事件）
          重建目标目录 → 移动前统计 → 移动 → 移动后校验 →
          按剧名删除 qB 种子 → COMPLETED / FAILED。

  种子删除现在由文件系统校验（fileCount + totalSize）驱动，
  不再依赖 Emby library.new webhook。
"""

import logging
import time
from datetime import datetime
from typing import Optional

import grpc
from sqlalchemy.orm import Session
from sqlalchemy import desc

from models import (
    TvShowDetail,
    CompletedSeasonRecord,
    TorrentRecord,
    AutoTaskFlow,
    TaskStatus,
    ActionType,
    TaskActionLog,
)
from services.cd2_service import get_client as get_cd2_client
from services.qb_service import delete_torrents as qb_delete_torrents
from services.qb_service import get_torrents as qb_get_torrents
from services.organize_service import (
    parse_torrent_name,
    search_tmdb_tv,
    get_tv_details,
    get_tv_season_info,
    resolve_category,
)
from config.settings import load_config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

import os
import re

# 用于剧集文件计数的视频文件扩展名
_VIDEO_EXTENSIONS = {
    '.mkv', '.mp4', '.ts', '.avi', '.mov', '.wmv', '.flv',
    '.m2ts', '.iso', '.rmvb', '.webm', '.m4v', '.mpg', '.mpeg',
    '.ogm', '.divx', '.wmv', '.rm', '.asf', '.vob',
}

# 从目录名中提取季号的正常则表达式，例如：
#   "Season 1" / "Season 1 -2160p-WEB-DL-ADWeb" / "season 2"
_SEASON_RE = re.compile(r'^[Ss]eason\s+(\d+)\b')

# 用于区分 PARTIAL（部分）删除事件与 FULL（整剧）删除事件的正则表达式
_PARTIAL_DELETE_RE = re.compile(r'从\s+\S+\s+中删除了\s+\d+\s*项目')
_FULL_DELETE_RE = re.compile(r'从\s*mediaServer\s*中删除了')


def _sanitize_cd2_path(path: str) -> str:
    """移除 CD2 路径末尾的斜杠（根目录除外）。"""
    if path and path != "/":
        return path.rstrip("/")
    return path


def _strip_trailing_category(base: str, category: str) -> str:
    """从 *base* 末尾移除 *category*（如果存在），避免出现
    ``.../国产剧/国产剧/...`` 这样的双重分类路径。

    返回剥离后的 base（不含末尾斜杠）。
    """
    base = base.rstrip("/")
    if category:
        suffix = f"/{category}"
        if base.endswith(suffix):
            return base[:-len(suffix)]
    return base


def _cd2_dir_exists(cd2, path: str) -> bool:
    """检查 CD2 目录是否真实存在。"""
    path = path.rstrip("/")
    if not path or path == "/":
        return True
    parent = "/".join(path.split("/")[:-1]) or "/"
    folder_name = path.rsplit("/", 1)[-1]
    try:
        siblings = cd2.get_sub_files(parent)
        return folder_name in {f.get("name", "") for f in siblings}
    except Exception:
        return False


def _ensure_cd2_directory(cd2, target_path: str, known_existing_parent: str) -> bool:
    """在 CD2 上创建 *known_existing_parent* 到 *target_path* 之间所有缺失的中间目录。"""
    target = target_path.rstrip("/")
    parent = known_existing_parent.rstrip("/")
    if not target.startswith(parent):
        logger.error("_ensure_cd2_directory: target '%s' not under parent '%s'", target, parent)
        return False
    rel = target[len(parent):].strip("/")
    if not rel:
        return True
    segments = rel.split("/")
    current = parent
    for seg in segments:
        if not seg:
            continue
        next_path = f"{current}/{seg}"
        try:
            existing = cd2.get_sub_files(current)
            existing_names = {f.get("name", "") for f in existing}
        except Exception:
            existing_names = set()
        if seg not in existing_names:
            logger.info("_ensure_cd2_directory: creating '%s'", next_path)
            try:
                mk_result = cd2.create_folder(parent_path=current, folder_name=seg)
                if not mk_result.get("success"):
                    logger.error("_ensure_cd2_directory: failed to create '%s': %s",
                                 next_path, mk_result.get("errorMessage", "unknown"))
                    return False
            except Exception as e:
                logger.error("_ensure_cd2_directory: exception creating '%s': %s", next_path, e)
                return False
        current = next_path
    return True


def _count_files_in_cd2_dir(cd2, path: str, video_only: bool = True, retries: int = 3) -> int:
    """统计 CD2 目录中的文件数量，支持重试以容忍 CD2 缓存延迟。"""
    for attempt in range(retries):
        try:
            files = cd2.get_sub_files(path)
            if not files and attempt < retries - 1:
                # 空列表可能是 CD2 缓存未就绪，重试
                delay = [2, 4, 8][attempt] if attempt < 3 else 8
                logger.debug("_count_files_in_cd2_dir('%s') 返回空列表 (第%d/%d次), %ds 后重试…",
                             path, attempt + 1, retries, delay)
                time.sleep(delay)
                continue
            if video_only:
                count = 0
                for f in files:
                    if f.get("isDirectory"):
                        continue
                    name = f.get("name", "")
                    _, ext = os.path.splitext(name)
                    if ext.lower() in _VIDEO_EXTENSIONS:
                        count += 1
                logger.debug("_count_files_in_cd2_dir('%s') = %d (video-only)", path, count)
                return count
            else:
                return sum(1 for f in files if not f.get("isDirectory"))
        except Exception as e:
            if attempt < retries - 1:
                delay = [2, 4, 8][attempt] if attempt < 3 else 8
                logger.debug("_count_files_in_cd2_dir('%s') 异常 (第%d/%d次): %s, %ds 后重试…",
                             path, attempt + 1, retries, e, delay)
                time.sleep(delay)
            else:
                logger.warning("_count_files_in_cd2_dir('%s') 失败: %s", path, e)
    return 0


# ---------------------------------------------------------------------------
# 移动+校验辅助函数（移动后的文件系统校验替代 Emby library.new webhook）
# ---------------------------------------------------------------------------

def _get_season_stats(cd2, path: str, retries: int = 3, force_refresh: bool = False) -> dict:
    """获取 CD2 目录的递归 fileCount 和 totalSize，支持重试。

    使用 ``GetFileDetailProperties`` RPC 获取递归统计信息。
    在结果为空时以 2s/4s/8s 指数退避重试，容忍 CD2 缓存传播延迟。

    当 *force_refresh* 为 True 时，CD2 服务端绕过目录缓存直接查询云端，
    适用于移动/复制/删除操作后需要获取最新统计信息的场景。
    """
    for attempt in range(retries):
        props = cd2.get_file_detail_properties(path, force_refresh=force_refresh)
        if props and props.get("fileCount", 0) > 0:
            return {"fileCount": props["fileCount"], "totalSize": props.get("totalSize", 0)}
        if attempt < retries - 1:
            delay = [2, 4, 8][attempt]
            logger.debug("_get_season_stats('%s') 结果为空 (第%d/%d次), %ds 后重试…",
                         path, attempt + 1, retries, delay)
            time.sleep(delay)
    props = cd2.get_file_detail_properties(path, force_refresh=force_refresh)
    if props:
        return {"fileCount": props.get("fileCount", 0), "totalSize": props.get("totalSize", 0)}
    return {"fileCount": 0, "totalSize": 0}


def _verify_season_move(cd2, source_path: str, dest_parent_path: str,
                        season_dir_name: str, title: str, season_num: int) -> dict:
    """移动单个 Season 文件夹，并通过对比移动前后的 fileCount + totalSize 进行校验。

    1. 从源路径记录移动前统计信息
    2. 移动源路径 → 目标父目录
    3. 等待 CD2 缓存 (2s) + 重试统计查询最多 3 次
    4. 对比源与目标的 fileCount + totalSize
    5. 校验失败时追加长间隔重试（容忍 CD2 服务端缓存延迟）

    返回::

        {
          "success": bool,       # 移动是否成功？
          "verified": bool,      # fileCount & totalSize 是否匹配？
          "season": int,         # Season 编号
          "dir_name": str,       # Season 目录名
          "source_stats": {"fileCount": int, "totalSize": int},
          "dest_stats":   {"fileCount": int, "totalSize": int},
          "dest_path": str,
          "error": str,
          "retry_count": int,    # 校验重试次数（0 = 首次即通过）
        }
    """
    source_stats = _get_season_stats(cd2, source_path)
    logger.info("[%s] S%d '%s' 移动前统计: %d 文件, %d 字节",
                title, season_num, season_dir_name,
                source_stats["fileCount"], source_stats["totalSize"])
    if source_stats["fileCount"] == 0:
        return {"success": False, "verified": False,
                "season": season_num, "dir_name": season_dir_name,
                "source_stats": source_stats,
                "dest_stats": {"fileCount": 0, "totalSize": 0}, "dest_path": "",
                "error": "源目录文件数为 0 — 无法校验移动",
                "retry_count": 0}
    move_result = cd2.move_files([source_path], dest_parent_path, conflict_policy=1)
    if not move_result.get("success"):
        error = move_result.get("errorMessage", "unknown")
        logger.error("[%s] S%d '%s' 移动失败: %s", title, season_num, season_dir_name, error)
        return {"success": False, "verified": False,
                "season": season_num, "dir_name": season_dir_name,
                "source_stats": source_stats,
                "dest_stats": {"fileCount": 0, "totalSize": 0}, "dest_path": "",
                "error": f"移动失败: {error}",
                "retry_count": 0}
    dest_path = _sanitize_cd2_path(f"{dest_parent_path}/{season_dir_name}")
    logger.info("[%s] S%d 等待 2s 以便 CD2 缓存刷新…", title, season_num)
    time.sleep(2)
    # 使用 force_refresh=True 通知 CD2 服务端绕过目录缓存，直接查询云端最新数据
    dest_stats = _get_season_stats(cd2, dest_path, retries=3, force_refresh=True)
    logger.info("[%s] S%d '%s' 移动后统计 (force_refresh): %d 文件, %d 字节",
                title, season_num, season_dir_name,
                dest_stats["fileCount"], dest_stats["totalSize"])
    verified = (source_stats["fileCount"] == dest_stats["fileCount"]
                and source_stats["totalSize"] == dest_stats["totalSize"]
                and source_stats["fileCount"] > 0)
    retry_count = 0

    if verified:
        logger.info("[%s] S%d '%s' ✓ 校验通过 — %d 文件, %d 字节一致",
                    title, season_num, season_dir_name,
                    source_stats["fileCount"], source_stats["totalSize"])
    else:
        # CD2 force_refresh 可能仍需一点时间才能完全生效。
        # 追加 force_refresh 重试，最大程度绕过服务端缓存。
        logger.warning("[%s] S%d '%s' 首次 force_refresh 校验不匹配 — "
                       "源: %d 文件/%d 字节, 目标: %d 文件/%d 字节. "
                       "开始 CD2 缓存容错重试 (force_refresh)…",
                       title, season_num, season_dir_name,
                       source_stats["fileCount"], source_stats["totalSize"],
                       dest_stats["fileCount"], dest_stats["totalSize"])
        cache_retry_delays = [5, 10, 15, 20]
        for attempt, delay in enumerate(cache_retry_delays, 1):
            time.sleep(delay)
            dest_stats = _get_season_stats(cd2, dest_path, retries=1, force_refresh=True)
            retry_count = attempt
            logger.info("[%s] S%d '%s' force_refresh 重试 #%d (%ds): %d 文件, %d 字节",
                        title, season_num, season_dir_name,
                        attempt, delay,
                        dest_stats["fileCount"], dest_stats["totalSize"])
            if (source_stats["fileCount"] == dest_stats["fileCount"]
                    and source_stats["totalSize"] == dest_stats["totalSize"]):
                verified = True
                logger.warning("[%s] S%d '%s' ✓ force_refresh 重试 #%d 后校验通过 "
                               "— CD2 缓存延迟约 %ds",
                               title, season_num, season_dir_name,
                               attempt, 2 + sum(cache_retry_delays[:attempt]))
                break

        if not verified:
            logger.critical("[%s] S%d '%s' ✗ 校验失败（含 %d 次 force_refresh 重试）— "
                            "源: %d 文件/%d 字节, 目标: %d 文件/%d 字节. "
                            "种子文件将保留，请人工排查！",
                            title, season_num, season_dir_name, retry_count,
                            source_stats["fileCount"], source_stats["totalSize"],
                            dest_stats["fileCount"], dest_stats["totalSize"])

    return {"success": True, "verified": verified,
            "season": season_num, "dir_name": season_dir_name,
            "source_stats": source_stats,
            "dest_stats": dest_stats, "dest_path": dest_path,
            "error": "" if verified else "移动后文件数量或总大小不匹配",
            "retry_count": retry_count}


def _extract_version_keyword_sets(season_dir_names: list) -> list:
    """从每个 Season 目录名中独立提取版本关键词集合。

    每个目录名返回一个独立的 set，不做跨 Season 合并。
    例如 ["Season 1 -2160p-WEB-DL DV-HDSWEB", "Season 1 -2160p-WEB-DL-Pure@HDSWEB"] →
         [{"2160p", "web-dl", "dv", "hdsweb"}, {"2160p", "web-dl", "pure@hdsweb"}]

    种子只需匹配任意一个 set 的全部关键词即可被删除。
    """
    skip_words = {"season", "s01", "s1", "-", ""}
    result = []
    for name in season_dir_names:
        keywords = set()
        rest = _SEASON_RE.sub("", name).strip()
        rest = rest.lstrip("- ")
        if not rest:
            continue
        parts = re.split(r'[\s\-]+', rest)
        for p in parts:
            p_lower = p.lower().strip("()[]")
            if p_lower and p_lower not in skip_words and len(p_lower) >= 2:
                keywords.add(p_lower)
        if keywords:
            result.append(keywords)
    return result


def _is_port_8089(config_id: str) -> bool:
    """检查指定 qB 实例是否运行在 8089 端口。"""
    cfg = load_config()
    qb_configs = cfg.get("qb_configs", [])
    qb_cfg = next((c for c in qb_configs if c.get("id") == config_id), None)
    if not qb_cfg:
        return False
    host = qb_cfg.get("host", "")
    return ":8089" in host or host.endswith(":8089") or host.rstrip("/").endswith("8089")


def _delete_qb_torrents_by_title(
    qb_config_id: str,
    title: str,
    version_keywords: list = None,
    season_dir_names: list = None,
) -> dict:
    """按标题 + 版本关键词删除 qBittorrent 种子，包含文件。

    规则：
    1. 只删除下载完成的种子（progress >= 1.0）
    2. 端口 8089 的实例例外：无论是否完成，全部删除
    3. 有版本关键词时，种子名必须至少包含一个关键词，防止错删不同版本的种子
    4. 当 qb_config_id 为空时，自动遍历所有已配置的 qB 实例

    返回::

        {
            "success": bool,
            "deleted_count": int,
            "deleted_names": list[str],
            "skipped_incomplete": list[str],
            "skipped_version_mismatch": list[str],
            "error": str,
        }
    """
    if not title:
        return {"success": False, "deleted_count": 0, "deleted_names": [],
                "skipped_incomplete": [], "skipped_version_mismatch": [],
                "error": "缺少 title"}

    # 每个 Season 目录独立提取关键词集合（不做跨 Season 合并）
    keyword_sets = []
    if season_dir_names:
        keyword_sets = _extract_version_keyword_sets(season_dir_names)
    if version_keywords:
        keyword_sets.append(set(k.lower() for k in version_keywords))

    try:
        # 确定要查询的实例 ID 列表
        if qb_config_id:
            config_ids = [qb_config_id]
        else:
            logger.info(
                "_delete_qb_torrents_by_title: qb_config_id 为空，遍历所有 qB 实例查找 '%s'",
                title,
            )
            cfg = load_config()
            qb_configs = cfg.get("qb_configs", [])
            config_ids = [c.get("id") for c in qb_configs if c.get("id")]
            if not config_ids:
                logger.warning("_delete_qb_torrents_by_title: 没有可用的 qB 实例")
                return {"success": False, "deleted_count": 0, "deleted_names": [],
                        "skipped_incomplete": [], "skipped_version_mismatch": [],
                        "error": "没有可用的 qB 实例"}

        # 按实例分组 (cid → [(hash, name), ...])
        instance_torrents = {}
        skipped_incomplete = []
        skipped_version_mismatch = []

        for cid in config_ids:
            qb_result = qb_get_torrents(config_id=cid, keyword=title, page=1, page_size=200)
            matched = qb_result.get("torrents", [])
            if not matched:
                continue

            is_8089 = _is_port_8089(cid)
            filtered = []
            for t in matched:
                tname = t.get("name", "")
                # 版本关键词过滤：种子必须匹配至少一个 Season 的全部关键词
                if keyword_sets:
                    tname_lower = tname.lower()
                    if not any(all(kw in tname_lower for kw in kw_set) for kw_set in keyword_sets):
                        skipped_version_mismatch.append(tname)
                        continue
                # 完成状态过滤：只删已完成的（8089 端口除外）
                progress = t.get("progress", 0)
                if not is_8089 and progress < 1.0:
                    skipped_incomplete.append(tname)
                    continue
                filtered.append((t["hash"], tname))

            if filtered:
                instance_torrents[cid] = filtered
                logger.info(
                    "_delete_qb_torrents_by_title: 实例 '%s' 找到 %d 个匹配 '%s' 的种子"
                    "（已过滤 %d 个未完成, %d 个版本不匹配, 8089=%s）",
                    cid, len(filtered), title,
                    sum(1 for t in matched if t.get("progress", 0) < 1.0 and not is_8089),
                    sum(1 for t in matched
                        if keyword_sets and
                        not any(all(kw in t.get("name", "").lower() for kw in kw_set)
                                for kw_set in keyword_sets)),
                    is_8089,
                )

        if skipped_incomplete:
            logger.info(
                "_delete_qb_torrents_by_title: 跳过 %d 个未完成种子: %s",
                len(skipped_incomplete),
                [n[:60] for n in skipped_incomplete[:5]],
            )
        if skipped_version_mismatch:
            logger.info(
                "_delete_qb_torrents_by_title: 跳过 %d 个版本不匹配种子: %s",
                len(skipped_version_mismatch),
                [n[:60] for n in skipped_version_mismatch[:5]],
            )

        if not instance_torrents:
            logger.info(
                "_delete_qb_torrents_by_title: 未找到可删除的匹配 '%s' 的 qB 种子"
                "（搜索了 %d 个实例, 版本关键词=%s）",
                title, len(config_ids),
                [sorted(ks) for ks in keyword_sets] if keyword_sets else "(无)",
            )
            return {"success": True, "deleted_count": 0, "deleted_names": [],
                    "skipped_incomplete": skipped_incomplete,
                    "skipped_version_mismatch": skipped_version_mismatch,
                    "error": ""}

        # 按实例分别删除
        total_deleted = 0
        all_deleted_names = []
        for cid, torrents in instance_torrents.items():
            hashes = [h for h, _ in torrents]
            names = [n for _, n in torrents]
            logger.info(
                "_delete_qb_torrents_by_title: 正在实例 '%s' 删除 %d 个种子（含文件）",
                cid, len(hashes),
            )
            ok = qb_delete_torrents(cid, hashes, delete_files=True)
            if ok:
                total_deleted += len(hashes)
                all_deleted_names.extend(names)
            else:
                logger.error("_delete_qb_torrents_by_title: 实例 '%s' 删除失败", cid)

        if total_deleted > 0:
            return {"success": True, "deleted_count": total_deleted,
                    "deleted_names": all_deleted_names,
                    "skipped_incomplete": skipped_incomplete,
                    "skipped_version_mismatch": skipped_version_mismatch,
                    "error": ""}
        else:
            return {"success": False, "deleted_count": 0, "deleted_names": [],
                    "skipped_incomplete": skipped_incomplete,
                    "skipped_version_mismatch": skipped_version_mismatch,
                    "error": "所有实例的 qB 删除均返回失败"}
    except Exception as e:
        logger.exception("_delete_qb_torrents_by_title 对 '%s' 执行失败", title)
        return {"success": False, "deleted_count": 0, "deleted_names": [],
                "skipped_incomplete": [], "skipped_version_mismatch": [],
                "error": str(e)}

def _write_action_log(
    db: Session,
    task_id: Optional[int],
    tmdb_id: int,
    title: str,
    action_type: str,
    target_name: str,
    target_path: str = "",
    reason: str = "",
    detail: dict = None,
):
    """写入一条任务操作日志并立即提交。

    每次调用独立 commit，不影响主流程的 AutoTaskFlow 事务。
    日志记录的是 CD2 侧已实际发生的操作（删除/移动），
    即使后续 AutoTaskFlow 创建失败，已发生的操作也应被记录。
    """
    if db is None:
        return
    try:
        log_entry = TaskActionLog(
            task_id=task_id,
            tmdb_id=tmdb_id,
            title=title,
            action_type=action_type,
            target_name=target_name,
            target_path=target_path,
            reason=reason,
            detail=detail or {},
        )
        db.add(log_entry)
        db.commit()
    except Exception as e:
        logger.error("_write_action_log: 写入日志失败 (%s → %s): %s",
                     action_type, target_name, e)


# ---------------------------------------------------------------------------
# 阶段一：自动处理 — 完整性验证 + 智能对比 + 前置删除
# ---------------------------------------------------------------------------

def auto_process_show(
    torrent_name: str,
    tmdb_id: int = None,
    qb_config_id: str = "",
    category: str = "",
    db: Session = None,
) -> dict:
    """执行洗版流程的阶段一：解析种子 → 完整性校验 → 智能对比 → 决策。

    1. 解析种子名称 → TMDB 元数据
    2. 验证 CD2 已完结目录中所有季是否真正完整
    3. 与媒体库对比，逐季决定删除/保留/移动
    4. 根据决策结果创建 AutoTaskFlow 记录

    返回包含 ``success``、``stage``、``details``、``task_id`` 的字典。
    """
    config = load_config()
    organized_base = _sanitize_cd2_path(config.get("cd2_organized_dir", ""))
    media_base = _sanitize_cd2_path(config.get("cd2_media_dir", ""))

    result = {
        "success": False,
        "stage": "init",
        "message": "",
        "details": {},
        "task_id": None,
        "tmdb_id": tmdb_id,
    }

    # ---- 步骤 1: 解析种子名称 → TMDB 元数据 ----
    parsed = parse_torrent_name(torrent_name)
    if not parsed.get("success"):
        result["stage"] = "parse_failed"
        result["message"] = parsed.get("error", "无法解析种子名称")
        return result

    title = parsed.get("title", "")
    year = parsed.get("year")
    season = parsed.get("season", 1)

    if not tmdb_id:
        if parsed.get("tmdb_id"):
            tmdb_id = int(parsed["tmdb_id"])
        else:
            search_result = search_tmdb_tv(title, year)
            if search_result:
                tmdb_id = search_result.get("id")
            else:
                result["stage"] = "tmdb_not_found"
                result["message"] = f"TMDB 未找到: {title} ({year})"
                return result

    result["tmdb_id"] = tmdb_id
    result["details"]["title"] = title
    result["details"]["year"] = year

    if not category:
        category = resolve_category(tmdb_id) or ""
    result["details"]["category"] = category

    tv_details = get_tv_details(tmdb_id)
    if not tv_details:
        result["stage"] = "tmdb_error"
        result["message"] = f"无法获取 TMDB 详情: tmdb_id={tmdb_id}"
        return result

    total_seasons = tv_details.get("number_of_seasons", 1)
    total_episodes_all = tv_details.get("number_of_episodes", 0)
    result["details"]["total_seasons"] = total_seasons
    result["details"]["total_episodes"] = total_episodes_all

    # ---- 步骤 2: 校验已完结目录中所有季的完整性 ----
    folder_name = f"{title}({year}) {{tmdb={tmdb_id}}}"

    org_base_clean = _strip_trailing_category(organized_base, category)
    media_base_clean = _strip_trailing_category(media_base, category)

    organized_show_path = _sanitize_cd2_path(
        f"{org_base_clean}/{category}/{year}/{folder_name}" if category
        else f"{org_base_clean}/{year}/{folder_name}"
    )
    media_show_path = _sanitize_cd2_path(
        f"{media_base_clean}/{category}/{year}/{folder_name}" if category
        else f"{media_base_clean}/{year}/{folder_name}"
    )

    logger.info(
        "[%s] Resolved paths: organized='%s', media='%s'",
        title, organized_show_path, media_show_path,
    )

    cd2 = get_cd2_client()

    # ---- 确保已完结剧集目录存在（冷启动保护）----
    organized_show_files = []
    organized_dir_created = False
    try:
        organized_show_files = cd2.get_sub_files(organized_show_path)
    except Exception as e:
        logger.warning(
            "[%s] Failed to list organized dir '%s': %s",
            title, organized_show_path, e,
        )
        organized_show_files = []

    if not organized_show_files and not _cd2_dir_exists(cd2, organized_show_path):
        logger.info(
            "[%s] Organized show dir not found: '%s' — auto-creating",
            title, organized_show_path,
        )
        category_root = _sanitize_cd2_path(
            f"{org_base_clean}/{category}" if category else org_base_clean
        )
        if _ensure_cd2_directory(cd2, organized_show_path, category_root):
            organized_dir_created = True
            try:
                organized_show_files = cd2.get_sub_files(organized_show_path)
            except Exception:
                organized_show_files = []
        else:
            result["stage"] = "organized_not_found"
            result["message"] = (
                f"无法创建已完结目录: {organized_show_path}"
            )
            return result

    if not organized_show_files:
        logger.info(
            "[%s] Organized show dir is empty (created=%s) — nothing to process",
            title, organized_dir_created,
        )
        result["stage"] = "organized_empty"
        result["message"] = (
            f"已完结目录为空{'（已自动创建）' if organized_dir_created else ''}: "
            f"{organized_show_path}"
        )
        result["details"]["season_validation"] = []
        result["details"]["comparison_results"] = []
        result["success"] = True
        return result

    # ---- 2a. 从实际的剧集根目录列表构建 Season 目录映射 ----
    season_dir_map: dict[int, list[dict]] = {}
    for item in organized_show_files:
        if not item.get("isDirectory"):
            continue
        name = item.get("name", "")
        m = _SEASON_RE.match(name)
        if m:
            sn = int(m.group(1))
            season_dir_map.setdefault(sn, []).append(item)
        else:
            logger.debug("[%s] Skipping non-season dir: '%s'", title, name)

    _season_nums = sorted(season_dir_map.keys())
    _total_dirs = sum(len(v) for v in season_dir_map.values())
    logger.info(
        "[%s] Found %d Season dir(s) across %d season(s) in organized: %s",
        title, _total_dirs, len(_season_nums), _season_nums,
    )

    # ---- 2b. 逐季校验 TMDB 完整性 ----
    season_validation = []
    incomplete_seasons = []

    for season_num in range(1, total_seasons + 1):
        season_info = get_tv_season_info(tmdb_id, season_num)
        try:
            expected_eps = int(season_info.get("episode_count", 0)) if season_info else 0
        except (TypeError, ValueError):
            expected_eps = 0

        candidates = season_dir_map.get(season_num, [])
        if not candidates:
            sv = {
                "season": season_num,
                "expected_episodes": expected_eps,
                "actual_files": 0,
                "complete": False,
                "path": _sanitize_cd2_path(f"{organized_show_path}/Season {season_num}"),
            }
            season_validation.append(sv)
            incomplete_seasons.append(season_num)
            logger.warning(
                "[%s] Season %d: no directory found in organized",
                title, season_num,
            )
            continue

        complete_candidates = []
        for sd in candidates:
            sd_path = sd.get("fullPathName") or _sanitize_cd2_path(
                f"{organized_show_path}/{sd['name']}"
            )
            count = _count_files_in_cd2_dir(cd2, sd_path)
            logger.info(
                "[%s] S%d candidate '%s': expected=%d, actual=%d%s",
                title, season_num, sd.get("name", ""),
                expected_eps, count,
                " * MATCH" if count == expected_eps and count > 0 else "",
            )
            if count == expected_eps and count > 0:
                complete_candidates.append(sd)
            else:
                logger.warning(
                    "[%s] S%d skipping incomplete '%s': %d/%d files "
                    "(保留在已完结目录)",
                    title, season_num, sd.get("name", ""),
                    count, expected_eps,
                )
                _write_action_log(
                    db, None, tmdb_id, title,
                    ActionType.SKIP_FOLDER.value,
                    target_name=sd.get("name", ""),
                    target_path=sd_path,
                    reason=f"已完结目录文件不足: {count}/{expected_eps} 文件",
                )
                _write_action_log(
                    db, None, tmdb_id, title,
                    ActionType.KEEP_ORGANIZED.value,
                    target_name=sd.get("name", ""),
                    target_path=sd_path,
                    reason=f"已完结 S{season_num} 文件不完整 ({count}/{expected_eps})，保留在已完结目录等待补全",
                )

        if complete_candidates:
            sv = {
                "season": season_num,
                "expected_episodes": expected_eps,
                "actual_files": sum(1 for _ in complete_candidates),
                "complete": True,
                "path": _sanitize_cd2_path(f"{organized_show_path}/Season {season_num}"),
                "candidate_count": len(complete_candidates),
            }
            season_dir_map[season_num] = complete_candidates
        else:
            sv = {
                "season": season_num,
                "expected_episodes": expected_eps,
                "actual_files": 0,
                "complete": False,
                "path": _sanitize_cd2_path(f"{organized_show_path}/Season {season_num}"),
            }
            incomplete_seasons.append(season_num)
            del season_dir_map[season_num]
            logger.warning(
                "[%s] Season %d: no candidate matches expected %d files — "
                "可能是归档延迟或文件仍在写入中，跳过处理（保留在已完结目录）",
                title, season_num, expected_eps,
            )

        season_validation.append(sv)

    result["details"]["season_validation"] = season_validation

    if incomplete_seasons:
        logger.warning(
            "[%s] %d incomplete season(s) skipped and left in place: %s",
            title, len(incomplete_seasons), incomplete_seasons,
        )

    # ---- 2c. 安全检查：如果没有完整的季，优雅退出 ----
    if not season_dir_map:
        result["stage"] = "no_complete_seasons"
        result["message"] = (
            f"已完结目录中无完整季文件夹 "
            f"(跳过 {len(incomplete_seasons)} 个文件数不匹配的季: {incomplete_seasons})"
        )
        result["success"] = True
        return result

    logger.info(
        "[%s] %d complete season(s) ready to process: %s",
        title, len(season_dir_map), sorted(season_dir_map.keys()),
    )
    # ---- 3. 检测媒体库状态 ----
    # 媒体库目录可能还不存在（首次导入或之前已清理）。
    # get_sub_files 在 NOT_FOUND 时返回 []（而非抛异常），
    # 因此通过检查父目录列表来区分"不存在"和"空目录"。
    # 支持重试以容忍 CD2 缓存延迟。
    media_show_files: list[dict] = []
    media_dir_missing = False
    for _list_attempt in range(3):
        try:
            media_show_files = cd2.get_sub_files(media_show_path)
            if media_show_files:
                break
        except Exception as e:
            logger.warning("[%s] Failed to list media lib dir '%s': %s", title, media_show_path, e)
        if _list_attempt < 2:
            delay = [2, 4][_list_attempt]
            logger.info("[%s] 媒体库目录列表为空 — %ds 后重试…", title, delay)
            time.sleep(delay)

    # ---- 检测媒体库剧集目录是否确实不存在 ----
    # get_sub_files 在"路径不存在"和"空目录"两种情况下都返回 []。
    # 通过检查父目录列表中是否包含该剧集文件夹名来区分两种情况。
    if not media_show_files:
        show_folder_name = media_show_path.rstrip("/").rsplit("/", 1)[-1]
        parent_path = "/".join(media_show_path.rstrip("/").split("/")[:-1])
        try:
            parent_files = cd2.get_sub_files(parent_path)
            parent_names = {f.get("name", "") for f in parent_files}
            if show_folder_name not in parent_names:
                media_dir_missing = True
                logger.info(
                    "[%s] Media library show folder NOT found: '%s'",
                    title, media_show_path,
                )
            else:
                # 目录存在但列表为空（可能是 CD2 缓存问题或真的是空目录）
                logger.info(
                    "[%s] Media library show folder exists but listing is empty: '%s'",
                    title, media_show_path,
                )
        except Exception:
            # 连父目录都列不出来 — 假设不存在
            media_dir_missing = True
            logger.info(
                "[%s] Cannot list media parent dir — assuming show folder "
                "missing: '%s'",
                title, media_show_path,
            )

    media_season_dirs = [f for f in media_show_files if f.get("isDirectory")]

    # ---- 3a. 评估每个媒体库 Season 的完整性 ----
    # 构建: {season_num: [{"name", "path", "file_count", "expected", "complete"}, ...]}
    # 同一 Season 可能有多个版本目录（例如 "Season 1 -WEB-DL" 和 "Season 1 -DV"），
    # 使用 list 存储所有版本，避免同季号多目录时后面的覆盖前面的。
    # 使用 _count_files_in_cd2_dir（仅统计视频文件，与 TMDB episode_count 口径一致），
    # 配合 3 次重试容忍 CD2 缓存延迟。
    # 当 TMDB 无 episode_count 数据时（expected_eps=0），若目录中有文件，
    # 视为"有内容"（files_present=True），不参与残缺判定，避免误删。
    media_season_state: dict[int, list[dict]] = {}
    for mdir in media_season_dirs:
        media_season_name = mdir.get("name", "")
        media_season_path = _sanitize_cd2_path(
            mdir.get("fullPathName") or f"{media_show_path}/{media_season_name}"
        )
        m_se = _SEASON_RE.match(media_season_name)
        if not m_se:
            continue
        sn = int(m_se.group(1))
        season_info = get_tv_season_info(tmdb_id, sn)
        try:
            expected_eps = int(season_info.get("episode_count", 0)) if season_info else 0
        except (TypeError, ValueError):
            expected_eps = 0
        # 统计视频文件数量（与 TMDB episode_count 口径一致），带重试
        actual_files = _count_files_in_cd2_dir(cd2, media_season_path, video_only=True, retries=3)
        # 交叉校验：如果视频文件计数为 0，用递归统计确认目录是否确实为空
        # （防止 CD2 get_sub_files 缓存问题导致误判）
        if actual_files == 0 and expected_eps > 0:
            stats = _get_season_stats(cd2, media_season_path, retries=3)
            if stats["fileCount"] > 0:
                logger.warning(
                    "[%s] Media S%d '%s': 视频文件计数为 0 但递归统计有 %d 个文件，"
                    "可能是 CD2 缓存延迟或文件在子目录中",
                    title, sn, media_season_name, stats["fileCount"],
                )
                # 使用递归统计的文件数作为兜底（可能包含非视频文件，但比 0 准确）
                actual_files = stats["fileCount"]
        # 完整性判定：
        # 1) TMDB 无数据 (expected_eps=0) 但目录有文件 → 保守处理，标记为"有内容"
        #    （files_present=True），不参与残缺判定，避免因 TMDB 数据缺失误删
        # 2) 文件数精确匹配 → 完整
        # 3) 其他情况 → 残缺
        files_present = (expected_eps == 0 and actual_files > 0)
        is_complete = (actual_files == expected_eps and actual_files > 0)
        media_season_state.setdefault(sn, []).append({
            "name": media_season_name,
            "path": media_season_path,
            "file_count": actual_files,
            "expected": expected_eps,
            "complete": is_complete,
            "files_present": files_present,  # TMDB 无数据但目录非空
        })
        if files_present:
            logger.warning(
                "[%s] Media S%d '%s': %d files (TMDB episode_count=0, "
                "保守处理为有内容，不参与残缺判定)",
                title, sn, media_season_name, actual_files,
            )
            _write_action_log(
                db, None, tmdb_id, title,
                ActionType.SKIP_FOLDER.value,
                target_name=media_season_name,
                target_path=media_season_path,
                reason=f"TMDB episode_count=0，目录有 {actual_files} 个文件，保守跳过",
            )
            _write_action_log(
                db, None, tmdb_id, title,
                ActionType.KEEP_MEDIA.value,
                target_name=media_season_name,
                target_path=media_season_path,
                reason=f"TMDB 无 Season {sn} 集数数据，但目录有 {actual_files} 个文件，保留在媒体库",
            )
        else:
            logger.info(
                "[%s] Media S%d '%s': %d/%d files %s",
                title, sn, media_season_name, actual_files, expected_eps,
                "✓ COMPLETE" if is_complete else "✗ INCOMPLETE",
            )

    # 同一 Season 可能有多个版本目录（例如 "Season 1 -WEB-DL" 和 "Season 1 -DV"），
    # 只要某个 Season 的任一版本完整或有文件，该 Season 就不算残缺。
    def _season_has_complete(versions: list[dict]) -> bool:
        return any(v["complete"] for v in versions)

    def _season_has_files_present(versions: list[dict]) -> bool:
        return any(v.get("files_present", False) for v in versions)

    # 判定"所有季均残缺"时，将 TMDB 无数据但目录有文件的季排除
    # （files_present=True），避免因 TMDB 数据缺失导致误入 Case B 整剧删除
    all_media_incomplete = (
        len(media_season_state) > 0
        and all(
            not _season_has_complete(versions) and not _season_has_files_present(versions)
            for versions in media_season_state.values()
        )
    )

    # 按目录版本统计（同一 Season 可能有多个版本目录，例如 "Season 1 -WEB-DL" 和 "Season 1 -DV"）
    total_versions = sum(len(versions) for versions in media_season_state.values())
    complete_count = sum(
        sum(1 for v in versions if v["complete"])
        for versions in media_season_state.values()
    )
    files_present_count = sum(
        sum(1 for v in versions if v.get("files_present"))
        for versions in media_season_state.values()
    )
    incomplete_count = total_versions - complete_count - files_present_count

    logger.info(
        "[%s] 媒体库评估: %d 个目录 — %d 完整, %d 残缺, %d 有文件(TMDB无数据)",
        title, total_versions,
        complete_count, incomplete_count, files_present_count,
    )

    # =====================================================================
    # 决策树 — 四种情况
    # =====================================================================

    # =====================================================================
    # 情况 A: 媒体库目录不存在（首次导入）
    #   或目录存在但没有 Season 子目录。
    #   → 创建目标目录 → 移动 + 校验每个已完结 Season
    #   → 全部通过 → 删除 qB 种子 → COMPLETED
    # =====================================================================
    if media_dir_missing or (not media_season_dirs and len(media_season_state) == 0):
        _cand_count = sum(len(v) for v in season_dir_map.values())
        logger.info(
            "[%s] 情况 A: 媒体库无此剧集 — 将 %d 个完整已完结 Season 候选直接移动并校验"
            "（跳过残缺季）",
            title, _cand_count,
        )

        # 创建目标剧集目录
        target_parent = "/".join(media_show_path.rstrip("/").split("/")[:-1])
        show_name = media_show_path.rstrip("/").rsplit("/", 1)[-1]
        target_show_path = media_show_path

        try:
            existing = cd2.get_sub_files(target_parent)
            existing_names = {f.get("name", "") for f in existing}
            if show_name not in existing_names:
                logger.info("[%s] Creating target show folder: '%s'", title, target_show_path)
                mk_result = cd2.create_folder(parent_path=target_parent, folder_name=show_name)
                if not mk_result.get("success"):
                    result["stage"] = "move_failed"
                    result["message"] = (
                        f"无法创建目标剧集目录: {mk_result.get('errorMessage', '未知错误')}"
                    )
                    return result
                # 通过 _cd2_dir_exists 确认目录已创建（防御 CD2 缓存延迟）
                if not _cd2_dir_exists(cd2, target_show_path):
                    logger.info("[%s] CD2 缓存延迟 — 等待 2s 以便 mkdir 生效…", title)
                    time.sleep(2)
        except Exception as e:
            logger.warning("[%s] Error ensuring target folder: %s", title, e)

        # ---- 预创建 AutoTaskFlow 记录（确保后续所有 action log 都有 task_id） ----
        auto_task = AutoTaskFlow(
            tmdb_id=tmdb_id,
            task_type="AUTO_PROCESS",
            status=TaskStatus.INIT.value,
            context={
                "qb_config_id": qb_config_id,
                "title": title, "year": year, "category": category,
                "media_show_path": target_show_path,
                "organized_show_path": organized_show_path,
                "tmdb_id": tmdb_id, "total_seasons": total_seasons,
            },
        )
        db.add(auto_task)
        db.commit()
        db.refresh(auto_task)

        # 移动 + 校验每个完整 Season 候选
        verify_results = []
        for sn in sorted(season_dir_map.keys()):
            for season_dir in season_dir_map[sn]:
                source_path = (
                    season_dir.get("fullPathName")
                    or f"{organized_show_path}/{season_dir['name']}"
                )
                vr = _verify_season_move(
                    cd2, source_path, target_show_path,
                    season_dir.get("name", ""), title, sn,
                )
                verify_results.append(vr)
                if vr["verified"]:
                    _write_action_log(
                        db, auto_task.id, tmdb_id, title,
                        ActionType.MOVE_FOLDER.value,
                        target_name=season_dir.get("name", ""),
                        target_path=vr.get("dest_path", ""),
                        reason=f"Case A 首次导入: Season {sn} 移动并校验通过",
                        detail={
                            "season": sn,
                            "source_stats": vr["source_stats"],
                            "dest_stats": vr["dest_stats"],
                        },
                    )

        all_verified = all(vr["verified"] for vr in verify_results)
        failed = [vr for vr in verify_results if not vr["verified"]]

        # ---- 情况 A: 最终处理 ----
        if all_verified:
            season_dirs = [sd["name"] for sds in season_dir_map.values() for sd in sds]
            qb_result = _delete_qb_torrents_by_title(qb_config_id, title, season_dir_names=season_dirs)
            logger.info(
                "[%s] 情况 A 完成: %d 个 Season 已移动并校验, %d 个种子已删除",
                title, len(verify_results), qb_result["deleted_count"],
            )

            # 更新 AutoTaskFlow → COMPLETED
            auto_task.status = TaskStatus.COMPLETED.value
            auto_task.context = {
                **auto_task.context,
                "deleted_media_seasons": [],
                "deleted_organized_seasons": [],
                "verify_results": verify_results,
            }
            db.commit()
            db.refresh(auto_task)

            # ---- 记录 DELETE_TORRENT 日志: 每个被删除的种子 ----
            for tname in qb_result.get("deleted_names", []) or []:
                _write_action_log(
                    db, auto_task.id, tmdb_id, title,
                    ActionType.DELETE_TORRENT.value,
                    target_name=tname,
                    reason=f"Case A 首次导入完成，清理种子",
                )
            # ---- 记录 KEEP_TORRENT 日志: 每个被跳过的种子 ----
            for tname in qb_result.get("skipped_incomplete", []) or []:
                _write_action_log(
                    db, auto_task.id, tmdb_id, title,
                    ActionType.KEEP_TORRENT.value,
                    target_name=tname,
                    reason="种子未下载完成，保留",
                )
            for tname in qb_result.get("skipped_version_mismatch", []) or []:
                _write_action_log(
                    db, auto_task.id, tmdb_id, title,
                    ActionType.KEEP_TORRENT.value,
                    target_name=tname,
                    reason="版本关键词不匹配，保留（可能对应其他 Season 版本）",
                )

            result["task_id"] = auto_task.id
            result["stage"] = "completed"
            result["message"] = (
                f"首次导入完成：{len(verify_results)} 个 Season 候选已校验并移至媒体库"
                + (f"，已删除 {qb_result['deleted_count']} 个种子" if qb_result["deleted_count"] else "")
                + (f"（跳过 {len(incomplete_seasons)} 个不完整季）" if incomplete_seasons else "")
            )
            result["success"] = True
        else:
            # 更新 AutoTaskFlow → FAILED
            auto_task.status = TaskStatus.FAILED.value
            auto_task.context = {
                **auto_task.context,
                "verify_results": verify_results,
                "failed_count": len(failed),
            }
            db.commit()
            db.refresh(auto_task)

            logger.critical(
                "[%s] 情况 A 校验失败: %d/%d 个 Season 不匹配 — "
                "种子文件保留，目标目录可能存在不完整数据，请人工排查！",
                title, len(failed), len(verify_results),
            )
            for fv in failed:
                logger.critical(
                    "  S%s '%s': 源 %d 文件/%d 字节 → 目标 %d 文件/%d 字节 — %s"
                    "（重试 %d 次）",
                    str(fv.get("season", "?")), str(fv.get("dir_name", "?")),
                    fv["source_stats"]["fileCount"], fv["source_stats"]["totalSize"],
                    fv["dest_stats"]["fileCount"], fv["dest_stats"]["totalSize"],
                    fv.get("error", "unknown"),
                    fv.get("retry_count", 0),
                )
                _write_action_log(
                    db, auto_task.id, tmdb_id, title,
                    ActionType.KEEP_TORRENT.value,
                    target_name=str(fv.get("dir_name", "未知目录")),
                    reason=(
                        f"Case A 首次导入: Season {fv.get('season', '?')} 移动后校验不一致"
                        f"（源 {fv['source_stats']['fileCount']} 文件"
                        f" → 目标 {fv['dest_stats']['fileCount']} 文件"
                        f"，含 {fv.get('retry_count', 0)} 次缓存重试），"
                        f"种子保留待人工排查"
                    ),
                    detail={
                        "season": fv.get("season"),
                        "dir_name": fv.get("dir_name"),
                        "source_stats": fv["source_stats"],
                        "dest_stats": fv["dest_stats"],
                        "retry_count": fv.get("retry_count", 0),
                    },
                )
            result["stage"] = "verify_failed"
            result["message"] = (
                f"移动后校验失败：{len(failed)}/{len(verify_results)} 个 Season "
                f"文件数量或大小不匹配（含 CD2 缓存容错重试），种子已保留，请人工排查"
            )
            result["details"]["verify_results"] = verify_results
            result["success"] = False
            result["task_id"] = auto_task.id

        result["details"]["verify_results"] = verify_results

        # 更新或插入 TvShowDetail 记录
        try:
            existing = db.query(TvShowDetail).filter(TvShowDetail.tmdb_id == tmdb_id).first()
            if existing:
                existing.title = title
                existing.year = year
                existing.category = category
                existing.total_episodes = total_episodes_all
                db.commit()
            else:
                db.add(TvShowDetail(
                    tmdb_id=tmdb_id, title=title, year=year,
                    category=category, total_episodes=total_episodes_all,
                ))
                db.commit()
        except Exception as e:
            logger.warning(f"[{title}] Failed to upsert TvShowDetail: {e}")

        return result

    # =====================================================================
    # 情况 B: 所有媒体库 Season 均残缺 → 整剧替换
    #   → 删除整个剧集目录
    #   → WAITING_FOR_DELETE_WEBHOOK（移动+校验+删除种子在
    #     Emby 确认删除后的 webhook handler 中完成）
    # =====================================================================
    if all_media_incomplete:
        logger.info(
            "[%s] 情况 B: 全部 %d 个可判定媒体库 Season 均残缺（%d 个有文件但 TMDB 无数据）— "
            "删除整个剧集目录，等待 Emby 删除 webhook",
            title, incomplete_count, files_present_count,
        )

        # 通过 CD2 删除整个剧集目录
        delete_result = cd2.delete_files([media_show_path])
        if not delete_result.get("success"):
            logger.error(
                "[%s] Failed to delete media show directory: %s",
                title, delete_result.get("errorMessage", "unknown"),
            )
            result["stage"] = "delete_failed"
            result["message"] = (
                f"无法删除媒体库剧集目录: {delete_result.get('errorMessage', '未知错误')}"
            )
            return result

        logger.info("[%s] ✓ 已删除整个剧集目录: '%s'", title, media_show_path)

        # 构建 organized seasons 列表供 webhook handler 使用
        organized_seasons_to_move = []
        for sn in sorted(season_dir_map.keys()):
            for season_dir in season_dir_map[sn]:
                organized_seasons_to_move.append({
                    "season": sn,
                    "dir_name": season_dir.get("name", ""),
                    "path": (
                        season_dir.get("fullPathName")
                        or _sanitize_cd2_path(f"{organized_show_path}/{season_dir['name']}")
                    ),
                })

        context = {
            "qb_config_id": qb_config_id,
            "title": title,
            "year": year,
            "category": category,
            "media_show_path": media_show_path,
            "organized_show_path": organized_show_path,
            "tmdb_id": tmdb_id,
            "total_seasons": total_seasons,
            "organized_seasons_to_move": organized_seasons_to_move,
            "deleted_media_seasons": list(media_season_state.keys()),
            "deleted_organized_seasons": [],
        }

        task = AutoTaskFlow(
            tmdb_id=tmdb_id,
            task_type="AUTO_PROCESS",
            status=TaskStatus.WAITING_FOR_DELETE_WEBHOOK.value,
            context=context,
        )
        db.add(task)
        db.commit()
        db.refresh(task)

        # ---- 记录 DELETE_MEDIA 日志: 每个残缺季 ----
        for sn, versions in media_season_state.items():
            for state in versions:
                if state.get("files_present"):
                    continue  # 有文件但 TMDB 无数据的季不记录为删除
                _write_action_log(
                    db, task.id, tmdb_id, title,
                    ActionType.DELETE_MEDIA.value,
                    target_name=state["name"],
                    target_path=state["path"],
                    reason=f"Case B 整剧删除: Season {sn} 残缺 {state['file_count']}/{state['expected']} 文件",
                )

        result["task_id"] = task.id
        result["stage"] = "waiting_for_delete_webhook"
        result["message"] = (
            f"已删除媒体库整剧目录（{incomplete_count} 个残缺季"
            + (f"，{files_present_count} 个有文件但 TMDB 无数据" if files_present_count else "")
            + f"），"
            f"等待 Emby library.deleted 确认后自动移入 "
            f"{len(organized_seasons_to_move)} 个完整 Season 候选"
        )
        result["success"] = True
        result["details"]["organized_seasons_to_move"] = organized_seasons_to_move

        # 更新或插入 TvShowDetail 记录
        try:
            existing = db.query(TvShowDetail).filter(TvShowDetail.tmdb_id == tmdb_id).first()
            if existing:
                existing.title = title
                existing.year = year
                existing.category = category
                existing.total_episodes = total_episodes_all
                db.commit()
            else:
                db.add(TvShowDetail(
                    tmdb_id=tmdb_id, title=title, year=year,
                    category=category, total_episodes=total_episodes_all,
                ))
                db.commit()
        except Exception as e:
            logger.warning(f"[{title}] Failed to upsert TvShowDetail: {e}")

        return result

    # =====================================================================
    # 情况 C & D: 媒体库至少有一个完整 Season
    #   情况 C: 部分残缺 + organized 有不同版本 → 移动+校验
    #   情况 D: 全部完整 + organized 完全相同 → 仅去重
    # =====================================================================
    logger.info(
        "[%s] 媒体库共 %d 个 Season: %d 残缺, %d 完整, %d 有文件(TMDB无数据) — 执行逐季对比",
        title, len(media_season_state),
        incomplete_count, complete_count, files_present_count,
    )

    deleted_media_seasons = []
    deleted_organized_seasons = []
    candidates_to_move = []
    comparison_results = []

    # 辅助函数: 获取文件列表（含名称和大小，用于深度对比）
    def _get_file_list(path: str) -> list[dict]:
        try:
            files = cd2.get_sub_files(path)
            return [
                {"name": f.get("name", ""), "size": f.get("size", 0)}
                for f in files
                if not f.get("isDirectory")
            ]
        except Exception:
            return []

    # 辅助函数: 深度对比两个文件列表
    def _files_identical(list_a: list[dict], list_b: list[dict]) -> bool:
        """检查两个文件列表是否具有完全相同的 (name, size) 对。"""
        if len(list_a) != len(list_b):
            return False
        sorted_a = sorted(list_a, key=lambda x: x["name"])
        sorted_b = sorted(list_b, key=lambda x: x["name"])
        for fa, fb in zip(sorted_a, sorted_b):
            if fa["name"] != fb["name"] or fa["size"] != fb["size"]:
                return False
        return True

    # ---- 预创建 AutoTaskFlow 记录（确保后续所有 action log 都有 task_id） ----
    auto_task = AutoTaskFlow(
        tmdb_id=tmdb_id,
        task_type="AUTO_PROCESS",
        status=TaskStatus.INIT.value,
        context={
            "qb_config_id": qb_config_id,
            "title": title, "year": year, "category": category,
            "media_show_path": media_show_path,
            "organized_show_path": organized_show_path,
            "tmdb_id": tmdb_id, "total_seasons": total_seasons,
        },
    )
    db.add(auto_task)
    db.commit()
    db.refresh(auto_task)

    # ---- 4a. 删除媒体库中残缺的 Season ----
    # 跳过完整季和有文件但 TMDB 无数据的季（files_present），仅删除确认残缺的
    for sn, versions in media_season_state.items():
        for state in versions:
            if state["complete"] or state.get("files_present", False):
                continue
            logger.info(
                "[%s] S%d '%s': %d/%d 文件 — 残缺，从媒体库删除",
                title, sn, state["name"], state["file_count"], state["expected"],
            )
            del_result = cd2.delete_files([state["path"]])
            if del_result.get("success"):
                deleted_media_seasons.append(sn)
                comparison_results.append({
                    "season": sn,
                    "action": "deleted_media_incomplete",
                    "deleted_path": state["path"],
                })
                _write_action_log(
                    db, auto_task.id, tmdb_id, title,
                    ActionType.DELETE_MEDIA.value,
                    target_name=state["name"],
                    target_path=state["path"],
                    reason=f"Case C 残缺季删除: Season {sn} {state['file_count']}/{state['expected']} 文件",
                )
            else:
                comparison_results.append({
                    "season": sn,
                    "action": "delete_media_failed",
                    "error": del_result.get("errorMessage", "unknown"),
                })

    # ---- 4b. 对比媒体库完整季与已完结目录 ----
    for sn, versions in media_season_state.items():
        for state in versions:
            if not state["complete"]:
                continue

            comp = {
                "season": sn,
                "expected_episodes": state["expected"],
                "media_actual_files": state["file_count"],
                "action": "keep_media_complete",
                "media_path": state["path"],
            }

            org_candidates = season_dir_map.get(sn, [])
            if not org_candidates:
                comp["org_status"] = "organized_season_not_found"
                logger.info(
                    "[%s] S%d: 媒体库中完整，已完结目录中无对应 Season 可对比",
                    title, sn,
                )
                _write_action_log(
                    db, auto_task.id, tmdb_id, title,
                    ActionType.KEEP_MEDIA.value,
                    target_name=state["name"],
                    target_path=state["path"],
                    reason=(f"Case C S{sn} 完整 ({state['file_count']}/{state['expected']} 文件)，"
                            "已完结目录无对应 Season，保留在媒体库"),
                )
                comparison_results.append(comp)
                continue

            media_show_actual_name = media_show_path.rstrip("/").rsplit("/", 1)[-1]
            organized_show_name = folder_name
            org_comparisons = []

            for org_dir in org_candidates:
                org_season_path = org_dir.get("fullPathName") or _sanitize_cd2_path(
                    f"{organized_show_path}/{org_dir['name']}"
                )
                oc = {"dir_name": org_dir.get("name", ""), "path": org_season_path}

                org_files = cd2.get_sub_files(org_season_path)
                if not org_files or not any(not f.get("isDirectory") for f in org_files):
                    oc["status"] = "organized_empty"
                    logger.info(
                        "[%s] S%d organized '%s': empty, skipping",
                        title, sn, org_dir.get("name", ""),
                    )
                    org_comparisons.append(oc)
                    continue

                names_match = (organized_show_name == media_show_actual_name)
                org_file_count = _count_files_in_cd2_dir(cd2, org_season_path)
                counts_match = (state["file_count"] == org_file_count)
                media_stats = _get_season_stats(cd2, state["path"])
                org_stats = _get_season_stats(cd2, org_season_path)
                sizes_match = (
                    media_stats["totalSize"] == org_stats["totalSize"]
                    and media_stats["totalSize"] > 0
                )
                media_file_list = _get_file_list(state["path"])
                org_file_list = _get_file_list(org_season_path)
                files_identical = _files_identical(media_file_list, org_file_list)
                all_match = names_match and counts_match and sizes_match and files_identical

                oc["names_match"] = names_match
                oc["file_counts_match"] = counts_match
                oc["total_sizes_match"] = sizes_match
                oc["single_files_match"] = files_identical
                oc["all_conditions_match"] = all_match

                if all_match:
                    # ---- 完全重复 → 删除已完结目录中的副本 ----
                    logger.info(
                        "[%s] S%d '%s': 所有严格条件均满足 — 删除已完结目录中的重复季",
                        title, sn, org_dir.get("name", ""),
                    )
                    del_result = cd2.delete_files([org_season_path])
                    if del_result.get("success"):
                        oc["status"] = "deleted_duplicate"
                        deleted_organized_seasons.append({
                            "season": sn,
                            "dir_name": org_dir.get("name", ""),
                            "path": org_season_path,
                        })
                        _write_action_log(
                            db, auto_task.id, tmdb_id, title,
                            ActionType.DELETE_ORGANIZED.value,
                            target_name=org_dir.get("name", ""),
                            target_path=org_season_path,
                            reason=f"与媒体库版本完全重复 (名称/文件数/大小/单文件均匹配)",
                        )
                    else:
                        oc["status"] = "delete_failed"
                        oc["error"] = del_result.get("errorMessage", "unknown")
                else:
                    # ---- 不同版本 → 保留以移动 ----
                    reasons = []
                    if not names_match: reasons.append("名称")
                    if not counts_match: reasons.append("文件数")
                    if not sizes_match: reasons.append("大小")
                    if not files_identical: reasons.append("具体文件")
                    oc["status"] = "keep_for_move"
                    oc["mismatch_reasons"] = reasons
                    logger.info(
                        "[%s] S%d '%s': 与媒体库版本不同 (%s) — 保留以移动",
                        title, sn, org_dir.get("name", ""),
                        "; ".join(reasons),
                    )
                    _write_action_log(
                        db, auto_task.id, tmdb_id, title,
                        ActionType.KEEP_ORGANIZED.value,
                        target_name=org_dir.get("name", ""),
                        target_path=org_season_path,
                        reason=(f"Case C S{sn} 与媒体库版本不同 ({'; '.join(reasons)})，"
                                "非重复版本不删除，后续将移入媒体库"),
                    )

                org_comparisons.append(oc)

            comp["org_comparisons"] = org_comparisons
            comparison_results.append(comp)

            # 记录 KEEP_MEDIA: 媒体库完整季保留原因
            _write_action_log(
                db, auto_task.id, tmdb_id, title,
                ActionType.KEEP_MEDIA.value,
                target_name=state["name"],
                target_path=state["path"],
                reason=(f"Case C S{sn} 完整 ({state['file_count']}/{state['expected']} 文件)，"
                        f"已完结 {len(org_candidates)} 个候选已对比，保留在媒体库"),
            )

    result["details"]["comparison_results"] = comparison_results

    # ---- 4c. 已完结目录中有但媒体库中没有的 Season ----
    media_season_nums = {comp["season"] for comp in comparison_results}
    for sn in sorted(season_dir_map.keys()):
        if sn not in media_season_nums:
            sv = next((sv for sv in season_validation if sv["season"] == sn), {})
            logger.info(
                "[%s] S%d: 媒体库中缺失此完整 Season — 将移动并校验",
                title, sn,
            )
            comparison_results.append({
                "season": sn,
                "action": "missing_from_media",
                "expected_episodes": sv.get("expected_episodes", 0),
                "media_actual_files": 0,
            })

    # ---- 4d. 收集需要移动的候选 ----
    for cr in comparison_results:
        for oc in cr.get("org_comparisons", []):
            if oc.get("status") == "keep_for_move":
                candidates_to_move.append({
                    "season": cr["season"],
                    "dir_name": oc["dir_name"],
                    "path": oc["path"],
                })

    for cr in comparison_results:
        if cr.get("action") == "missing_from_media":
            sn = cr["season"]
            for org_dir in season_dir_map.get(sn, []):
                candidates_to_move.append({
                    "season": sn,
                    "dir_name": org_dir.get("name", ""),
                    "path": org_dir.get("fullPathName") or _sanitize_cd2_path(
                        f"{organized_show_path}/{org_dir['name']}"
                    ),
                })

    # ---- 4e. 移动 + 校验每个候选 ----
    verify_results = []
    if candidates_to_move:
        # 确保目标剧集目录存在
        target_parent = "/".join(media_show_path.rstrip("/").split("/")[:-1])
        show_name = media_show_path.rstrip("/").rsplit("/", 1)[-1]
        try:
            existing = cd2.get_sub_files(target_parent)
            if show_name not in {f.get("name", "") for f in existing}:
                logger.info(
                    "[%s] Creating target show folder: '%s'", title, media_show_path,
                )
                mk = cd2.create_folder(parent_path=target_parent, folder_name=show_name)
                if not mk.get("success"):
                    logger.error(
                        "[%s] Failed to create target folder: %s",
                        title, mk.get("errorMessage", ""),
                    )
        except Exception as e:
            logger.warning("[%s] Error ensuring target folder: %s", title, e)

        # 去重：同一源路径可能出现在多个 comparison 结果中，只处理一次
        seen_paths = set()
        deduped_candidates = []
        for c in candidates_to_move:
            if c["path"] not in seen_paths:
                seen_paths.add(c["path"])
                deduped_candidates.append(c)
            else:
                logger.debug(
                    "[%s] S%d '%s': 候选路径重复，跳过 '%s'",
                    title, c["season"], c["dir_name"], c["path"],
                )

        for c in deduped_candidates:
            if not _cd2_dir_exists(cd2, c["path"]):
                # 区分原因：检查目标是否已有同名目录
                dest_path = _sanitize_cd2_path(f"{media_show_path}/{c['dir_name']}")
                if _cd2_dir_exists(cd2, dest_path):
                    logger.info(
                        "[%s] S%d '%s': 源目录已被前序候选移动至目标，跳过",
                        title, c["season"], c["dir_name"],
                    )
                else:
                    logger.warning(
                        "[%s] S%d '%s': 源目录已不存在 '%s'（可能已被去重删除），跳过移动",
                        title, c["season"], c["dir_name"], c["path"],
                    )
                continue
            vr = _verify_season_move(
                cd2, c["path"], media_show_path,
                c["dir_name"], title, c["season"],
            )
            verify_results.append(vr)
            if vr["verified"]:
                _write_action_log(
                    db, auto_task.id, tmdb_id, title,
                    ActionType.MOVE_FOLDER.value,
                    target_name=c["dir_name"],
                    target_path=vr.get("dest_path", ""),
                    reason=f"Case C 版本替换: Season {c['season']} 移动并校验通过",
                    detail={
                        "season": c["season"],
                        "source_stats": vr["source_stats"],
                        "dest_stats": vr["dest_stats"],
                    },
                )

    result["details"]["candidates_moved"] = len(verify_results)
    result["details"]["candidates_total"] = len(candidates_to_move)

    # ---- 4f. 清理父目录 ----
    # 如果所有媒体库 Season 都已删除且没有候选需要移动，
    # 清理空的剧集文件夹。
    if deleted_media_seasons and not candidates_to_move:
        try:
            remaining = cd2.get_sub_files(media_show_path)
            remaining_dirs = [f for f in remaining if f.get("isDirectory")]
            if not remaining_dirs:
                logger.info(
                    "[%s] All media lib seasons deleted, removing empty show folder", title,
                )
                cd2.delete_files([media_show_path])
                result["details"]["parent_cleaned"] = True
        except Exception as e:
            logger.warning("[%s] Parent cleanup check failed: %s", title, e)

    # ---- 4g. 最终结果: 校验 + 种子清理 ----
    all_moves_verified = (
        len(verify_results) > 0
        and all(vr["verified"] for vr in verify_results)
    )
    has_actions = bool(
        deleted_media_seasons or deleted_organized_seasons or candidates_to_move
    )
    dup_deleted_count = len(deleted_organized_seasons)

    if not has_actions:
        # ---- 情况 D: 无需操作（全部完整且相同）----
        auto_task.status = TaskStatus.COMPLETED.value
        auto_task.context = {
            **auto_task.context,
            "deleted_media_seasons": deleted_media_seasons,
            "deleted_organized_seasons": deleted_organized_seasons,
            "comparison_results": comparison_results,
        }
        db.commit()
        db.refresh(auto_task)

        result["stage"] = "no_action_needed"
        result["task_id"] = auto_task.id
        result["message"] = "媒体库所有季均完整，已完结与媒体库版本相同，无需处理"
        result["success"] = True
    elif verify_results and not all_moves_verified:
        # ---- 移动+校验失败 — CRITICAL，保留种子 ----
        failed = [vr for vr in verify_results if not vr["verified"]]
        logger.critical(
            "[%s] 校验失败: %d/%d 个 Season 不匹配 — "
            "种子文件保留，请人工排查！",
            title, len(failed), len(verify_results),
        )
        for fv in failed:
            logger.critical(
                "  S%s '%s': 源 %d 文件/%d 字节 → 目标 %d 文件/%d 字节 — %s"
                "（重试 %d 次）",
                str(fv.get("season", "?")), str(fv.get("dir_name", "?")),
                fv["source_stats"]["fileCount"], fv["source_stats"]["totalSize"],
                fv["dest_stats"]["fileCount"], fv["dest_stats"]["totalSize"],
                fv.get("error", "unknown"),
                fv.get("retry_count", 0),
            )
            _write_action_log(
                db, auto_task.id, tmdb_id, title,
                ActionType.KEEP_TORRENT.value,
                target_name=str(fv.get("dir_name", "未知目录")),
                reason=(
                    f"Case C 洗版: Season {fv.get('season', '?')} 移动后校验不一致"
                    f"（源 {fv['source_stats']['fileCount']} 文件"
                    f" → 目标 {fv['dest_stats']['fileCount']} 文件"
                    f"，含 {fv.get('retry_count', 0)} 次缓存重试），"
                    f"种子保留待人工排查"
                ),
                detail={
                    "season": fv.get("season"),
                    "dir_name": fv.get("dir_name"),
                    "source_stats": fv["source_stats"],
                    "dest_stats": fv["dest_stats"],
                    "retry_count": fv.get("retry_count", 0),
                },
            )
        # 更新 AutoTaskFlow → FAILED
        auto_task.status = TaskStatus.FAILED.value
        auto_task.context = {
            **auto_task.context,
            "deleted_media_seasons": deleted_media_seasons,
            "deleted_organized_seasons": deleted_organized_seasons,
            "comparison_results": comparison_results,
            "verify_results": verify_results,
        }
        db.commit()
        db.refresh(auto_task)

        result["stage"] = "verify_failed"
        result["task_id"] = auto_task.id
        result["message"] = (
            f"移动后校验失败：{len(failed)}/{len(verify_results)} 个 Season "
            f"文件数量或大小不匹配（含 CD2 缓存容错重试），种子已保留，请人工排查"
        )
        result["details"]["verify_results"] = verify_results
        result["success"] = False
    else:
        # ---- 全部校验通过 → 删除 qB 种子 ----
        qb_result = {"success": True, "deleted_count": 0}
        if all_moves_verified:
            season_dirs = [sd["name"] for sds in season_dir_map.values() for sd in sds]
            qb_result = _delete_qb_torrents_by_title(qb_config_id, title, season_dir_names=season_dirs)
            logger.info(
                "[%s] 情况 C 完成: %d 个 Season 校验通过, %d 个种子已删除",
                title, len(verify_results), qb_result["deleted_count"],
            )

        # 更新 AutoTaskFlow → COMPLETED
        auto_task.status = TaskStatus.COMPLETED.value
        auto_task.context = {
            **auto_task.context,
            "deleted_media_seasons": deleted_media_seasons,
            "deleted_organized_seasons": deleted_organized_seasons,
            "comparison_results": comparison_results,
            "verify_results": verify_results,
        }
        db.commit()
        db.refresh(auto_task)

        # ---- 记录 DELETE_TORRENT 日志: 每个被删除的种子 ----
        for tname in qb_result.get("deleted_names", []) or []:
            _write_action_log(
                db, auto_task.id, tmdb_id, title,
                ActionType.DELETE_TORRENT.value,
                target_name=tname,
                reason=f"Case C 洗版完成，清理种子",
            )
        # ---- 记录 KEEP_TORRENT 日志: 每个被跳过的种子 ----
        for tname in qb_result.get("skipped_incomplete", []) or []:
            _write_action_log(
                db, auto_task.id, tmdb_id, title,
                ActionType.KEEP_TORRENT.value,
                target_name=tname,
                reason="种子未下载完成，保留",
            )
        for tname in qb_result.get("skipped_version_mismatch", []) or []:
            _write_action_log(
                db, auto_task.id, tmdb_id, title,
                ActionType.KEEP_TORRENT.value,
                target_name=tname,
                reason="版本关键词不匹配，保留（可能对应其他 Season 版本）",
            )

        result["task_id"] = auto_task.id
        result["stage"] = "completed"

        msg_parts = []
        if deleted_media_seasons:
            msg_parts.append(f"已删除媒体库残缺季 S{deleted_media_seasons}")
        if dup_deleted_count:
            msg_parts.append(f"已删除 {dup_deleted_count} 个已完结重复季（与媒体库完全相同）")
        if verify_results:
            msg_parts.append(
                f"已移动并校验 {len(verify_results)} 个不同版本到媒体库"
            )
        if qb_result["deleted_count"]:
            msg_parts.append(f"已删除 {qb_result['deleted_count']} 个种子")

        if not msg_parts:
            msg_parts.append("无需处理")
        result["message"] = "；".join(msg_parts)
        result["details"]["verify_results"] = verify_results if verify_results else None
        result["success"] = True

    # 更新或插入 TvShowDetail 记录
    try:
        existing = db.query(TvShowDetail).filter(TvShowDetail.tmdb_id == tmdb_id).first()
        if existing:
            existing.title = title
            existing.year = year
            existing.category = category
            existing.total_episodes = total_episodes_all
            db.commit()
        else:
            db.add(TvShowDetail(
                tmdb_id=tmdb_id,
                title=title,
                year=year,
                category=category,
                total_episodes=total_episodes_all,
            ))
            db.commit()
    except Exception as e:
        logger.warning(f"[{title}] Failed to upsert TvShowDetail: {e}")

    return result


# ---------------------------------------------------------------------------
# 阶段二：处理 library.deleted webhook → 重建目录 + 移动 + 校验 + 清理种子
# ---------------------------------------------------------------------------

def handle_library_deleted_webhook(payload: dict, db: Session) -> bool:
    """处理 Emby 'library.deleted' webhook（仅整剧删除事件）。

    这是洗版流程的第二阶段（也是最终阶段）。阶段一 (auto_process) 通过 CD2
    删除了媒体库中的整个剧集目录，并将详情保存到 WAITING_FOR_DELETE_WEBHOOK
    任务中。Emby 通过触发此 webhook 确认删除已完成 — 此时可以安全地重建剧集
    目录、将已完结 Season 移入、逐字节校验、并删除 qBittorrent 种子 — 全部
    在 webhook handler 内联完成。

    种子删除由**文件系统校验**（fileCount + totalSize 匹配）决定，
    不再依赖 Emby 的 library.new webhook。
    """
    from utils.path_utils import extract_tmdb_id_from_payload

    tmdb_id = extract_tmdb_id_from_payload(payload)
    if tmdb_id is None:
        logger.debug("library.deleted: 未找到 TMDB ID，跳过")
        return False

    item = payload.get("Item", {})
    if item.get("Type") != "Series":
        logger.debug(f"library.deleted: Type={item.get('Type')}，跳过")
        return False

    # ---- 区分 PARTIAL（部分删除）与 FULL（整剧删除）----
    title = payload.get("Title", "")
    if _PARTIAL_DELETE_RE.search(title):
        logger.info(
            "library.deleted: tmdb=%d — 检测到 PARTIAL 删除 ('%s')，"
            "跳过（等待整剧删除事件）",
            tmdb_id, title[:80],
        )
        return False

    is_full_delete = bool(_FULL_DELETE_RE.search(title))
    if not is_full_delete:
        # 无法识别的标题格式 — 记录日志并安全跳过
        logger.info(
            "library.deleted: tmdb=%d — 无法识别的删除模式 ('%s')，跳过",
            tmdb_id, title[:80],
        )
        return False

    logger.info(
        "library.deleted: tmdb=%d — 确认整剧删除 ('%s')",
        tmdb_id, title[:80],
    )

    # 查找状态为 WAITING_FOR_DELETE_WEBHOOK 的匹配任务
    task = (
        db.query(AutoTaskFlow)
        .filter(
            AutoTaskFlow.tmdb_id == tmdb_id,
            AutoTaskFlow.status == TaskStatus.WAITING_FOR_DELETE_WEBHOOK.value,
        )
        .order_by(desc(AutoTaskFlow.created_at))
        .first()
    )

    if not task:
        logger.info(
            "library.deleted: tmdb=%d — no WAITING_FOR_DELETE_WEBHOOK task", tmdb_id,
        )
        return False

    logger.info(
        "library.deleted: tmdb=%d 任务 #%d → 重建 + 移动 + 校验 + 清理",
        tmdb_id, task.id,
    )

    try:
        context = task.context or {}
        org_show_path = context.get("organized_show_path", "")
        media_show_path = context.get("media_show_path", "")
        organized_seasons_to_move = context.get("organized_seasons_to_move", [])
        qb_config_id = context.get("qb_config_id", "")
        ctx_title = context.get("title", "Unknown")

        if not org_show_path or not media_show_path:
            task.status = TaskStatus.FAILED.value
            task.error_message = "Missing paths in task context"
            task.updated_at = datetime.now()
            db.commit()
            return False

        if not organized_seasons_to_move:
            logger.info("[%s] 无 organized seasons 需要移动，标记 COMPLETED", ctx_title)
            task.status = TaskStatus.COMPLETED.value
            task.updated_at = datetime.now()
            db.commit()
            return True

        cd2 = get_cd2_client()

        # ---- 防抖: Emby 删除后等待 2s 让 CD2 缓存一致 ----
        logger.info("[%s] 等待 2s 以使 CD2 缓存一致…", ctx_title)
        time.sleep(2)

        # ---- 步骤二: 重建目标剧集目录 ----
        target_parent = "/".join(media_show_path.rstrip("/").split("/")[:-1])
        show_name = media_show_path.rstrip("/").rsplit("/", 1)[-1]
        folder_exists = _cd2_dir_exists(cd2, media_show_path)

        if not folder_exists:
            logger.info(
                "[%s] 目标剧集目录不存在 — 重新创建: '%s'",
                ctx_title, media_show_path,
            )
            mk_result = cd2.create_folder(
                parent_path=target_parent, folder_name=show_name,
            )
            if not mk_result.get("success"):
                logger.error(
                    "[%s] 无法重新创建媒体库剧集目录: %s",
                    ctx_title, mk_result.get("errorMessage", "unknown"),
                )
                task.status = TaskStatus.FAILED.value
                task.error_message = (
                    f"无法重新创建媒体库剧集目录: "
                    f"{mk_result.get('errorMessage', 'unknown')}"
                )
                task.updated_at = datetime.now()
                db.commit()
                return False

            # 通过 _cd2_dir_exists 确认（CD2 缓存在 mkdir 后可能过期）
            if not _cd2_dir_exists(cd2, media_show_path):
                logger.info(
                    "[%s] CD2 缓存延迟 — 等待 2s…", ctx_title,
                )
                time.sleep(2)

            logger.info(
                "[%s] ✓ 已重建目标剧集目录: '%s'", ctx_title, media_show_path,
            )
        else:
            logger.info(
                "[%s] 目标剧集目录已存在: '%s'", ctx_title, media_show_path,
            )

        # ---- 步骤三-四: 移动 + 校验每个已完结 Season ----
        verify_results = []
        for season_info in organized_seasons_to_move:
            sn = season_info.get("season", 0)
            dir_name = season_info.get("dir_name", "")
            source_path = season_info.get("path", "")

            if not source_path:
                logger.warning(
                    "[%s] S%d: empty path in organized_seasons_to_move, skipping",
                    ctx_title, sn,
                )
                continue

            # 确认源目录仍然存在
            if not _cd2_dir_exists(cd2, source_path):
                logger.warning(
                    "[%s] S%d '%s': 源目录已不存在 '%s'，跳过",
                    ctx_title, sn, dir_name, source_path,
                )
                continue

            vr = _verify_season_move(
                cd2, source_path, media_show_path,
                dir_name, ctx_title, sn,
            )
            verify_results.append(vr)
            if vr["verified"]:
                _write_action_log(
                    db, task.id, tmdb_id, ctx_title,
                    ActionType.MOVE_FOLDER.value,
                    target_name=dir_name,
                    target_path=vr.get("dest_path", ""),
                    reason=f"Case B 阶段二: Season {sn} 移动并校验通过",
                    detail={
                        "season": sn,
                        "source_stats": vr["source_stats"],
                        "dest_stats": vr["dest_stats"],
                    },
                )

        # ---- 步骤五: 种子清理（由校验结果决定）----
        all_verified = (
            len(verify_results) > 0
            and all(vr["verified"] for vr in verify_results)
        )
        failed = [vr for vr in verify_results if not vr["verified"]]

        if not verify_results:
            # 所有源均缺失 — 没有移动任何内容，但也没有破坏什么
            logger.warning(
                "[%s] 没有移动任何 Season（所有源缺失或列表为空）",
                ctx_title,
            )
            task.status = TaskStatus.COMPLETED.value
            task.error_message = "未移动任何 Season — 源目录可能已被移除"
            task.context = {**context, "verify_results": verify_results}
            task.updated_at = datetime.now()
            db.commit()
            return True

        if all_verified:
            # 全部通过 → 删除 qB 种子
            season_dirs = [s.get("dir_name", "") for s in organized_seasons_to_move]
            qb_result = _delete_qb_torrents_by_title(qb_config_id, ctx_title, season_dir_names=season_dirs)
            logger.info(
                "[%s] ✓ 全部 %d 个 Season 校验通过 — %d 个种子已删除",
                ctx_title, len(verify_results), qb_result["deleted_count"],
            )

            # ---- 记录 DELETE_TORRENT 日志: 每个被删除的种子 ----
            for tname in qb_result.get("deleted_names", []) or []:
                _write_action_log(
                    db, task.id, tmdb_id, ctx_title,
                    ActionType.DELETE_TORRENT.value,
                    target_name=tname,
                    reason=f"Case B 阶段二完成，清理种子",
                )
            # ---- 记录 KEEP_TORRENT 日志: 每个被跳过的种子 ----
            for tname in qb_result.get("skipped_incomplete", []) or []:
                _write_action_log(
                    db, task.id, tmdb_id, ctx_title,
                    ActionType.KEEP_TORRENT.value,
                    target_name=tname,
                    reason="种子未下载完成，保留",
                )
            for tname in qb_result.get("skipped_version_mismatch", []) or []:
                _write_action_log(
                    db, task.id, tmdb_id, ctx_title,
                    ActionType.KEEP_TORRENT.value,
                    target_name=tname,
                    reason="版本关键词不匹配，保留（可能对应其他 Season 版本）",
                )

            task.status = TaskStatus.COMPLETED.value
            task.error_message = None
            task.context = {
                **context,
                "verify_results": verify_results,
                "qb_delete_result": qb_result,
            }
            task.updated_at = datetime.now()
            db.commit()

            logger.info(
                "library.deleted: tmdb=%d 任务 #%d → COMPLETED "
                "（%d 个 Season 校验通过, %d 个种子已删除）",
                tmdb_id, task.id, len(verify_results), qb_result["deleted_count"],
            )
            return True
        else:
            # 校验失败 — CRITICAL，保留种子
            logger.critical(
                "[%s] ✗ 移动后校验失败: %d/%d 个 Season 不匹配 — "
                "种子文件保留，请人工排查！",
                ctx_title, len(failed), len(verify_results),
            )
            for fv in failed:
                logger.critical(
                    "  S%s '%s': 源 %d 文件/%d 字节 → 目标 %d 文件/%d 字节 — %s"
                    "（重试 %d 次）",
                    str(fv.get("season", "?")), str(fv.get("dir_name", "?")),
                    fv["source_stats"]["fileCount"], fv["source_stats"]["totalSize"],
                    fv["dest_stats"]["fileCount"], fv["dest_stats"]["totalSize"],
                    fv.get("error", "unknown"),
                    fv.get("retry_count", 0),
                )
                # ---- 记录 KEEP_TORRENT 日志: 校验失败导致种子保留 ----
                _write_action_log(
                    db, task.id, tmdb_id, ctx_title,
                    ActionType.KEEP_TORRENT.value,
                    target_name=str(fv.get("dir_name", "未知目录")),
                    reason=(
                        f"Season {fv.get('season', '?')} 移动后校验不一致"
                        f"（源 {fv['source_stats']['fileCount']} 文件"
                        f" → 目标 {fv['dest_stats']['fileCount']} 文件"
                        f"，含 {fv.get('retry_count', 0)} 次缓存重试），"
                        f"种子保留待人工排查"
                    ),
                    detail={
                        "season": fv.get("season"),
                        "dir_name": fv.get("dir_name"),
                        "source_stats": fv["source_stats"],
                        "dest_stats": fv["dest_stats"],
                        "retry_count": fv.get("retry_count", 0),
                    },
                )

            task.status = TaskStatus.FAILED.value
            task.error_message = (
                f"移动后校验失败：{len(failed)}/{len(verify_results)} 个 Season "
                f"文件数量或大小不匹配（含 CD2 缓存容错重试），种子已保留"
            )
            task.context = {**context, "verify_results": verify_results}
            task.updated_at = datetime.now()
            db.commit()
            return False

    except Exception as e:
        logger.exception("library.deleted: tmdb=%d error", tmdb_id)
        try:
            task = db.query(AutoTaskFlow).filter(AutoTaskFlow.id == task.id).first()
            if task:
                task.status = TaskStatus.FAILED.value
                task.error_message = str(e)
                task.updated_at = datetime.now()
                db.commit()
        except Exception:
            pass
        return False


# ---------------------------------------------------------------------------
# 已废弃: handle_library_new_webhook
#
# 种子删除现在由文件系统校验（fileCount + totalSize 匹配）驱动，
# 在 CD2 移动完成后立即执行，不再依赖 Emby 的 library.new webhook。
# 此函数保留为空操作以保持向后兼容 — 任何仍引用它的外部调用者
# 不会崩溃，但函数不再执行任何实际工作。
# ---------------------------------------------------------------------------

def handle_library_new_webhook(payload: dict, db: Session) -> bool:
    """已废弃 — 种子删除现在通过文件系统校验内联完成。

    此函数有意保留为空操作。原有的逻辑（验证完整性 → 按名称删除 qB 种子）
    已迁移至：

      - ``_verify_season_move()``             (移动后 fileCount + totalSize 校验)
      - ``_delete_qb_torrents_by_title()``    (qB 种子删除)
      - ``handle_library_deleted_webhook()``  (阶段二 — webhook 后内联执行)
      - ``auto_process_show()``               (阶段一 — 情况 A/C 内联执行)

    保留以保持向后兼容 — 不要删除，因为其他模块可能仍会导入此函数。
    """
    logger.debug(
        "handle_library_new_webhook: 已废弃 — 种子删除现在通过文件系统校验内联完成。"
        "忽略 webhook 事件。"
    )
    return False
