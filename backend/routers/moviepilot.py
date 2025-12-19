from fastapi import APIRouter, Request, BackgroundTasks
import logging
# 引入业务逻辑
from services.mp_service import run_wash_process, get_mp_resources 

router = APIRouter()
logger = logging.getLogger("uvicorn")

@router.post("/webhook/moviepilot")
async def mp_webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        payload = await request.json()
        event_type = payload.get("type")
        
        if event_type == "subscribe.complete":
            data = payload.get("data", {}).get("subscribe_info", {})
            
            # 提取基础信息
            tmdb_id = data.get("tmdbid") or data.get("tmdb_id")
            name = data.get("name")
            season = data.get("season")
            year = data.get("year")
            
            # 🔥🔥🔥 关键修改：提取 MP 的分类 (category) 🔥🔥🔥
            # 如果你在 MP 订阅时选了“国产剧”分类，这里就会有值
            category = data.get("category") 

            if tmdb_id:
                logger.info(f"📩 [收到通知] 《{name}》({year}) 订阅完成 | 分类: {category or '未指定'}")
                # 将 category 作为 library_name 传入
                background_tasks.add_task(run_wash_process, name, tmdb_id, season, year, category)
            else:
                logger.warning("⚠️ 数据包中未找到 tmdb_id")

        return {"status": "success"}
    except Exception as e:
        logger.error(f"Webhook 解析错误: {e}")
        return {"status": "error"}

@router.get("/resources")
def get_all_resources():
    return get_mp_resources()

# 前端获取站点列表的接口 (保留兼容)
@router.get("/sites")
def get_sites_list():
    res = get_mp_resources()
    return res.get("sites", [])