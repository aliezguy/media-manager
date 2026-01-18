from fastapi import APIRouter, Request, BackgroundTasks
import logging
# 引入重构后的 Service
from services.mp_service import (
    get_mp_resources, 
    handle_new_subscription, 
    run_wash_process
)

router = APIRouter()
logger = logging.getLogger("uvicorn")

@router.post("/webhook/moviepilot")
async def mp_webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        payload = await request.json()
        event_type = payload.get("type")
        data = payload.get("data", {})
        
        logger.info(f"--------------- 📨 收到 Webhook: {event_type} ---------------")
        logger.info(f"原始报文 {data} ---------------")
        # 标准化提取 info
        mediainfo = data.get("mediainfo", {})
        subscribe_info = data.get("subscribe_info", {})
        
        # --- 新增：提取判断字段 ---
        # 1. 获取 filter_groups (通常在 subscribe_info 中)
        filter_groups = subscribe_info.get("filter_groups", [])
        # 2. 获取 best_version (通常在 subscribe_info 中, 1 代表已是最佳版本)
        best_version = subscribe_info.get("best_version", 0)

        sub_info = {
            "id": data.get("subscribe_id") or subscribe_info.get("id"),
            "name": mediainfo.get("title") or subscribe_info.get("name") or data.get("name"),
            "tmdbid": mediainfo.get("tmdb_id") or subscribe_info.get("tmdbid"),
            "type": mediainfo.get("type") or subscribe_info.get("type"), 
            "year": mediainfo.get("year") or subscribe_info.get("year"),
            "category": data.get("category") or subscribe_info.get("category"),
            "_raw_data": data
        }

        if not sub_info["name"]:
            return {"status": "skipped"}

        # --- 新增：阻断逻辑 ---
        # 如果包含“洗版”标签 或者 best_version 为 1，则认为是洗版完成，停止后续操作
        is_wash_tag = filter_groups and "洗版" in filter_groups
        is_best_ver = best_version == 1

        if is_wash_tag or is_best_ver:
            reason = "包含洗版标签" if is_wash_tag else "best_version=1"
            logger.info(f"🛑 停止处理: {sub_info['name']} ({reason})，防止洗版死循环。")
            return {"status": "skipped_wash_complete"}
        
        # 分发任务
        if event_type in ["subscribe.added", "subscribe", "subscribe.add"]:
            background_tasks.add_task(handle_new_subscription, sub_info)
            return {"status": "processing_new_sub"}

        elif event_type == "subscribe.complete":
            background_tasks.add_task(run_wash_process, sub_info)
            return {"status": "processing_wash"}
        
        else:
            return {"status": "ignored"}

    except Exception as e:
        logger.error(f"❌ Webhook 处理异常: {e}")
        return {"status": "error"}

@router.get("/resources")
def get_all_resources():
    return get_mp_resources()

@router.get("/sites")
def get_sites_list():
    res = get_mp_resources()
    return res.get("sites", [])