"""全库简介汉化 API — 手动触发 + 后台任务进度。

提供 POST /api/overview/translate：预扫待翻译外文简介数量 →
提交 task_manager 后台任务 → 立即返回 task_id（前端轮询进度）。
可选的 library_ids（空 = 全库）与 media_type 过滤。
"""
import logging
import traceback

from fastapi import APIRouter, Query, BackgroundTasks

from database import SessionLocal
from services.overview_translator import scan_and_translate, count_pending_overviews
from utils.task_manager import task_manager

logger = logging.getLogger("uvicorn")
router = APIRouter()


def _overview_translate_task(task_id: str, media_type=None, library_ids=None):
    """后台任务：全库扫描非中文 overview 并翻译回写。

    逐行走 scan_and_translate（单行 commit，失败 rollback 保留原值），
    成功/失败均 complete_task，前端据此判定任务结束。
    """
    db = SessionLocal()
    _success = False
    _final_msg = "❌ 简介翻译任务失败，请查看服务端日志"
    try:
        stats = scan_and_translate(
            db, media_type=media_type, library_ids=library_ids, task_id=task_id,
        )
        _success = True
        _final_msg = (
            f"✅ 简介翻译完成: 扫描 {stats['total_media']} 条，"
            f"翻译 {stats['translated']} 条，失败 {stats['failed']} 条，"
            f"跳过 {stats['skipped']} 条"
        )
    except Exception as e:
        logger.error("❌ [Overview] 任务崩溃 (task=%s):\n%s", task_id, traceback.format_exc())
        _final_msg = f"❌ 任务崩溃: {str(e)[:200]}"
        try:
            task_manager.update_progress(
                task_id, status="error", message=_final_msg,
            )
        except Exception:
            pass
    finally:
        try:
            task_manager.complete_task(task_id, _final_msg, success=_success)
        except Exception:
            pass
        db.close()


@router.post("/overview/translate")
def translate_overviews(
    background_tasks: BackgroundTasks,
    library_ids: list[str] = Query(default=None),  # 空 = 全库
    media_type: str | None = Query(default=None),  # Movie/Series/Episode，空 = 全部
):
    """手动触发全库简介翻译（异步）：提交后台任务后立即返回 task_id。

    Args:
        library_ids: 目标媒体库 ID 列表（可多个）；不传默认全库
        media_type:  可选过滤媒体类型（Movie / Series / Episode）
    """
    db = SessionLocal()
    try:
        count = count_pending_overviews(
            db, media_type=media_type, library_ids=library_ids,
        )
    finally:
        db.close()

    if count == 0:
        return {"task_id": "", "message": "没有待翻译的外文简介", "count": 0}

    task_id = task_manager.create_task(
        total=count,
        message=f"发现 {count} 条外文简介，准备翻译...",
        metadata={
            "type": "overview_translate",
            "media_type": media_type,
            "library_ids": library_ids,
        },
    )
    background_tasks.add_task(
        _overview_translate_task,
        task_id=task_id, media_type=media_type, library_ids=library_ids,
    )
    logger.info(
        "🚀 [Overview] 触发全库简介翻译: task=%s count=%d media_type=%s libs=%s",
        task_id, count, media_type, library_ids,
    )
    return {
        "task_id": task_id,
        "message": f"简介翻译已启动，共 {count} 条",
        "count": count,
    }
