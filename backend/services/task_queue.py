"""
演职员中文化 — 异步任务队列引擎（带限流保护）。

- asyncio.Queue 管理任务
- 每个任务间 3-5 秒随机延迟（反爬）
- 与 sync_state 全局状态联动
- 单个任务失败不崩溃整个 worker
"""

import asyncio
import random
import logging
import traceback
import requests
from datetime import datetime

from database import SessionLocal
from models import MediaSyncStatus
from config.settings import load_config
from services.sync_state import (
    start_sync, update_progress, finish_sync, GLOBAL_SYNC_STATE
)
from services.db_crud import save_media_to_db

logger = logging.getLogger("uvicorn")

# 全局任务队列
_sync_queue: asyncio.Queue = asyncio.Queue()


def enqueue_item(item_id: str, item_name: str = ""):
    """将一个媒体项放入汉化队列（同步接口，供 FastAPI 路由调用）。"""
    _sync_queue.put_nowait({"item_id": item_id, "item_name": item_name})


def queue_size() -> int:
    """返回当前队列中待处理的任务数。"""
    return _sync_queue.qsize()


def _fetch_all_item_ids(library_id: str) -> list[dict]:
    """从 Emby 获取指定媒体库下所有 Item 的 ID 和名称。"""
    cfg = load_config()
    host = cfg.get("emby_host", "").rstrip("/")
    api_key = cfg.get("emby_api_key", "")
    user_id = cfg.get("emby_user_id", "")

    if not host or not api_key:
        logger.error("❌ [TaskQueue] 缺少 Emby 配置")
        return []

    base_url = f"{host}/emby/Users/{user_id}/Items" if user_id else f"{host}/emby/Items"
    all_items = []
    start_index = 0
    page_size = 200

    while True:
        params = {
            "api_key": api_key,
            "ParentId": library_id,
            "IncludeItemTypes": "Series,Movie",
            "Recursive": "true",
            "Fields": "ProviderIds",
            "SortBy": "DateCreated",
            "SortOrder": "Descending",
            "StartIndex": start_index,
            "Limit": page_size,
        }
        try:
            resp = requests.get(base_url, params=params, timeout=30)
            if resp.status_code != 200:
                break
            data = resp.json()
            items = data.get("Items", [])
            if not items:
                break
            for it in items:
                all_items.append({
                    "id": it.get("Id", ""),
                    "name": it.get("Name", ""),
                })
            total = data.get("TotalRecordCount", 0)
            if start_index + page_size >= total:
                break
            start_index += page_size
        except Exception as e:
            logger.error(f"❌ [TaskQueue] 获取 Item 列表失败: {e}")
            break

    return all_items


async def _process_single_item(item_id: str, item_name: str):
    """处理单个媒体项：调 douban sinicize 并记录状态。"""
    from services.douban_service import DoubanSinizer

    try:
        sinizer = DoubanSinizer()
        result = sinizer.sinicize(item_id)
        logger.info(
            f"   [TaskQueue] {item_name or item_id} -> "
            f"{'✅' if result.get('success') else '❌'}"
            f" (匹配 {result.get('matched', 0)}/{result.get('total_actors', 0)})"
        )
        return result
    except Exception as e:
        logger.error(f"❌ [TaskQueue] {item_name or item_id} 处理异常: {e}")
        logger.error(traceback.format_exc())

        # 标记失败状态（三表联动）
        db = SessionLocal()
        try:
            save_media_to_db(
                db,
                emby_item={"Id": item_id, "Name": item_name, "Type": ""},
                provider_ids={},
                images={},
                people=None,  # 失败时无演员数据
                library_id="",
                status="failed",
                matched_actors=0,
                total_actors=0,
                error_message=str(e)[:500],
            )
            db.commit()
        except Exception:
            logger.error("❌ [TaskQueue] 写入失败状态异常:\n%s", traceback.format_exc())
        finally:
            db.close()
        return None


async def process_sync_queue():
    """后台常驻消费者 — 持续从队列取任务并执行（带限流延迟）。"""
    logger.info("🚀 [TaskQueue] 后台任务队列 Worker 已启动")

    while True:
        try:
            task = await _sync_queue.get()
            item_id = task.get("item_id", "")
            item_name = task.get("item_name", "")

            # 更新全局状态
            state = GLOBAL_SYNC_STATE
            state["progress"] = state.get("progress", 0) + 1
            state["current_task"] = item_name or item_id

            # 执行汉化
            await _process_single_item(item_id, item_name)

            # 队列空 → 结束
            if _sync_queue.empty():
                finish_sync()

            _sync_queue.task_done()

            # ★ 核心限流：3-5 秒随机延迟 ★
            delay = random.uniform(3, 5)
            logger.debug(f"   [TaskQueue] 冷却 {delay:.1f}s ...")
            await asyncio.sleep(delay)

        except asyncio.CancelledError:
            logger.info("🛑 [TaskQueue] Worker 被取消")
            break
        except Exception as e:
            logger.error(f"❌ [TaskQueue] Worker 异常: {e}")
            await asyncio.sleep(1)


def start_full_sync(library_id: str) -> tuple[bool, str]:
    """触发全量汉化（由 sync_actions 路由调用）。

    返回 (success, message)。
    """
    if GLOBAL_SYNC_STATE.get("is_running"):
        return False, "当前已有汉化任务正在后台执行中，请稍后再试"

    items = _fetch_all_item_ids(library_id)
    if not items:
        return False, "该媒体库下没有找到任何媒体项"

    total = len(items)
    start_sync(total=total, library_id=library_id)

    for it in items:
        _sync_queue.put_nowait({
            "item_id": it["id"],
            "item_name": it["name"],
        })

    logger.info(f"📦 [TaskQueue] 全量同步已启动: {total} 个任务已下发 (库ID={library_id})")
    return True, f"全量同步任务已启动，共下发 {total} 个任务"
