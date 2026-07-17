"""
Douban 演员中文化 API 路由。

提供对指定 Emby Item 触发演员名和角色名中文化的接口。
"""

import logging
from fastapi import APIRouter
from pydantic import BaseModel

from services.douban_service import DoubanSinizer

router = APIRouter()
logger = logging.getLogger("uvicorn")


class SinicizeRequest(BaseModel):
    item_id: str


@router.post("/douban/sinicize")
def sinicize_actors(req: SinicizeRequest):
    """对指定 Emby Item 执行演员与角色名中文化。

    请求体:
        {"item_id": "xxxx"}

    返回:
        {
            "success": true/false,
            "matched": 5,
            "total_actors": 20,
            "details": [
                {
                    "index": 0,
                    "emby_name": "Sun Hu",
                    "douban_name": "孙虎",
                    "old_name": "Sun Hu",
                    "new_name": "孙虎",
                    "old_role": "Zhang San",
                    "new_role": "张三",
                    "matched": true,
                    "level": "拼音匹配"
                },
                ...
            ]
        }
    """
    logger.info(f"🎬 [Douban中文化] 收到请求: ItemId={req.item_id}")
    sinizer = DoubanSinizer()
    result = sinizer.sinicize(req.item_id)
    return result
