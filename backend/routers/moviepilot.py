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