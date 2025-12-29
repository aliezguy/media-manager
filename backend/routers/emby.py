from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from sqlalchemy.orm import Session
from database import get_db
from models import MediaTag
from pydantic import BaseModel
from typing import List, Optional, Dict
import requests
import json
import logging
import traceback
import asyncio  # 👈 必须引入：用于异步延时(防抖)
import re       # 👈 必须引入：用于正则清洗字符串
from openai import OpenAI
from config.settings import load_config
import time

# 引入服务层函数 (确保 services/emby_service.py 也是最新版)
from services.emby_service import get_item_info, update_item_tags

router = APIRouter()
logger = logging.getLogger("uvicorn")

# ==========================================
# 📋 Pydantic 模型定义 (请求体结构)
# ==========================================
class AppConfig(BaseModel):
    emby_host: str = ""
    emby_api_key: str = ""
    emby_user_id: str = ""
    sf_api_key: str = ""

class LibraryItemsRequest(AppConfig):
    library_id: str
    limit: int = 100
    start_index: int = 0

class SearchRequest(AppConfig):
    search_term: str

class AISingleRequest(AppConfig):
    item_id: str
    force_refresh: bool = False

class TagUpdateRequest(AppConfig):
    item_id: str
    tags: List[str]        # 最终要保存的标签列表
    overwrite: bool = True # True=覆盖模式(支持删除), False=合并模式(只增不减)

class AIBatchRequest(AppConfig):
    item_ids: List[str]
    force_refresh: bool = False

# ==========================================
# 🛠️ 全局工具 & 辅助函数
# ==========================================

# 全局任务字典：用于存储正在倒计时的剧集任务
# Key: SeriesId (剧集ID), Value: asyncio.Task (异步任务对象)
SERIES_TASKS: Dict[str, asyncio.Task] = {}

def clean_string(s):
    """
    清洗字符串，去除干扰字符
    Emby 有时会在标题里包含 \u200e (LRM) 等不可见字符，导致 key 匹配失败
    """
    if not s: return ""
    return re.sub(r'[\u200b-\u200f\ufeff]', '', s).strip()

def ask_ai(items, api_key):
    """
    调用 SiliconFlow (DeepSeek) AI 进行分析
    :param items: 包含 name, year, overview 的字典列表
    :return: JSON 格式的标签字典 {"剧名": ["标签1", ...]}
    """
    if not items or not api_key: return {}
    
    client = OpenAI(api_key=api_key, base_url="https://api.siliconflow.cn/v1")
    
    # 构造简化版的数据发给 AI，节省 Token 且提高准确率
    simple_list = []
    for i in items:
        simple_list.append({
            "name": i.get("Name"),
            "year": i.get("ProductionYear"),
            "overview": i.get("Overview", "")[:150] # 截取前150字简介，防止 Token 溢出
        })

    logger.info(f"🤖 [AI请求] 正在请求 AI 分析 {len(simple_list)} 个项目...")

    prompt = f"""
    请为以下影视作品打上 8-10 个精准的中文标签。
    标签范围参考：题材(如科幻,古装), 风格(如悬疑,喜剧), 元素(如穿越,权谋), 受众(如职场,大女主)。
    要求：
    1. 只返回纯 JSON 格式
    2. 不要包含 Markdown 代码块
    3. 格式示例: {{"作品名": ["标签1", "标签2"]}}
    
    数据内容：{json.dumps(simple_list, ensure_ascii=False)}
    """
    try:
        response = client.chat.completions.create(
            model="deepseek-ai/DeepSeek-V3",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2, stream=False
        )
        content = response.choices[0].message.content
        
        # 清理可能存在的 Markdown 标记 (```json ... ```)
        content = content.replace("```json", "").replace("```", "").strip()
        return json.loads(content)
    except Exception as e:
        logger.error(f"❌ AI 解析返回失败: {e}")
        return {}

# ==========================================
# ⏳ 核心逻辑 1: 剧集防抖处理 (Series/Episode)
# ==========================================

async def analyze_series_finally(series_id: str, series_name: str):
    """
    剧集防抖结束后的最终执行逻辑。
    只有当 15秒 内没有新的集数入库时，才会执行此函数。
    """
    try:
        # 1. 等待防抖时间 (让 Emby 数据库写入完成，等待同一季其他集数入库)
        await asyncio.sleep(15) 
        
        # 任务执行了，从全局字典里把自己移除
        if series_id in SERIES_TASKS:
            del SERIES_TASKS[series_id]

        logger.info(f"⏳ [防抖结束] 开始处理整部剧集: {series_name} (ID: {series_id})")

        # 2. 检查配置
        config = load_config()
        sf_api_key = config.get("sf_api_key")
        if not sf_api_key: return

        # 3. 查询 Emby 获取最新状态
        # (经过 15s 等待，Emby 接口肯定通了，不用担心 404)
        series_info = get_item_info(series_id)
        if not series_info:
            logger.error(f"❌ 无法获取剧集详情: {series_id}")
            return

        # 4. 幂等性检查：如果已经有标签，就不再浪费 AI Token
        current_tags = series_info.get("Tags", [])
        if current_tags:
            logger.info(f"   🛑 [跳过] 剧集《{series_name}》已有标签: {current_tags}")
            return

        # 5. 准备 AI 数据
        clean_name = clean_string(series_info.get("Name", series_name))
        target_info = {
            "Name": clean_name,
            "ProductionYear": series_info.get("ProductionYear"),
            "Overview": series_info.get("Overview", "")
        }
        
        logger.info(f"   🤖 正在请求 AI 分析剧集: [{clean_name}] ...")
        
        # 6. 调用 AI
        ai_result = ask_ai([target_info], sf_api_key)
        
        # 7. 解析 AI 结果与匹配
        suggested_tags = []
        if clean_name in ai_result:
            suggested_tags = ai_result[clean_name] # 精确匹配
        else:
            # 模糊匹配 (防止 AI 返回的名字略有不同)
            for k, v in ai_result.items():
                if clean_string(k) == clean_name or clean_name in k:
                    suggested_tags = v
                    break
            # 兜底 (如果 AI 只返回了一个结果，就默认是它)
            if not suggested_tags and len(ai_result) == 1:
                suggested_tags = list(ai_result.values())[0]

        # 8. 执行更新
        if suggested_tags:
            logger.info(f"   🏷 [AI完成] 为《{clean_name}》打标: {suggested_tags}")
            update_item_tags(series_id, suggested_tags)
        else:
            logger.warning(f"   ⚠️ AI 未返回有效标签: {clean_name}")

    except asyncio.CancelledError:
        # 如果在 sleep 期间被 cancel() 了，说明又有新集数来了
        logger.info(f"   🔄 [重置计时] {series_name} 又有新集数入库，推迟分析...")
        raise
    except Exception as e:
        logger.error(f"❌ 剧集分析任务异常: {e}")

# ==========================================
# 🚀 核心逻辑 2: 入库事件分流 (Movie vs Series)
# ==========================================

async def process_emby_item_added(payload: dict):
    """
    后台任务：处理 Emby Webhook 入库事件
    根据类型分流：电影直通车 vs 剧集防抖池
    """
    try:
        # 1. 基础信息提取
        item = payload.get("Item", {})
        if not item: return

        item_id = item.get("Id")
        name = item.get("Name", "")
        item_type = item.get("Type")
        
        # -------------------------------------------------------
        # 分支 A: 电影 (Movie) -> 立即执行，无延迟
        # -------------------------------------------------------
        if item_type == "Movie":
            logger.info(f"🎬 [电影入库] {name}，立即开始 AI 分析...")
            
            # 检查配置
            config = load_config()
            sf_api_key = config.get("sf_api_key")
            if not sf_api_key: return

            # 直接利用 Webhook 数据构造请求 (不回查 Emby，防止 404)
            clean_name = clean_string(name)
            target_info = {
                "Name": clean_name,
                "ProductionYear": item.get("ProductionYear"),
                "Overview": item.get("Overview", ""),
                "ProviderIds": item.get("ProviderIds", {})
            }
            
            # 调用 AI
            ai_result = ask_ai([target_info], sf_api_key)
            
            # 解析匹配逻辑
            suggested_tags = []
            if clean_name in ai_result:
                suggested_tags = ai_result[clean_name]
            else:
                # 模糊匹配
                for k, v in ai_result.items():
                    if clean_string(k) == clean_name or clean_name in k:
                        suggested_tags = v
                        break
                # 兜底
                if not suggested_tags and len(ai_result) == 1:
                    suggested_tags = list(ai_result.values())[0]

            # 更新 Emby
            if suggested_tags:
                logger.info(f"   🏷 准备打标签: {suggested_tags}")
                await asyncio.sleep(1) # 小睡1秒，防止 Emby 数据库被锁
                update_item_tags(item_id, suggested_tags)
            return

        # -------------------------------------------------------
        # 分支 B: 剧集/单集 (Series/Episode) -> 进入防抖池
        # -------------------------------------------------------
        target_series_id = None
        target_series_name = ""

        # 提取剧集 ID (无论是单集还是整季，都归并到 SeriesID)
        if item_type == "Series":
            target_series_id = item_id
            target_series_name = name
        elif item_type == "Episode":
            target_series_id = item.get("SeriesId") or item.get("ParentId")
            target_series_name = item.get("SeriesName", "")

        # 如果能提取到 SeriesId，进入防抖队列
        if target_series_id:
            # 如果已有任务在跑，取消它（相当于重置计时器）
            if target_series_id in SERIES_TASKS:
                SERIES_TASKS[target_series_id].cancel()
            
            # 创建新任务：15秒后执行
            logger.info(f"   ⏱ [防抖计时] {target_series_name} (ID: {target_series_id}) - 15秒后执行")
            task = asyncio.create_task(analyze_series_finally(target_series_id, target_series_name))
            SERIES_TASKS[target_series_id] = task
        
    except Exception as e:
        logger.error(f"❌ 后台任务异常: {e}")
        logger.error(traceback.format_exc())

# ==========================================
# 📡 接口: Webhook 接收
# ==========================================

@router.post("/webhook/emby")
async def emby_webhook(request: Request, background_tasks: BackgroundTasks):
    content_type = request.headers.get("content-type", "")
    try:
        payload = {}
        # 兼容性解析：支持 JSON 和 Form 表单
        if "application/json" in content_type:
            payload = await request.json()
        elif "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
            form = await request.form()
            data_str = form.get("data")
            if data_str:
                payload = json.loads(data_str)
            else:
                try: payload = json.loads(await request.body())
                except: pass
        else:
            try: payload = await request.json()
            except: return {"status": "unsupported"}

        event = payload.get("Event")
        
        # 监听 item.created (单集入库) 和 library.new (整季入库)
        if event in ["item.created", "library.new"]:
            background_tasks.add_task(process_emby_item_added, payload)
        
        return {"status": "received"}
        
    except Exception as e:
        logger.error(f"❌ Webhook 接收错误: {e}")
        return {"status": "error"}

# ==========================================
# 💾 接口: 手动保存标签 (健壮版)
# ==========================================

@router.post("/save_tags")
def save_tags(req: TagUpdateRequest, db: Session = Depends(get_db)):
    """
    前端手动点击'保存'时调用此接口
    包含逻辑：解锁元数据、清理只读字段、覆盖/合并标签、同步数据库
    """
    logger.info(f"💾 [保存标签] ID: {req.item_id}, 模式: {'覆盖' if req.overwrite else '合并'}")
    
    # 1. 验证配置
    if not req.emby_host or not req.emby_api_key:
        raise HTTPException(status_code=400, detail="未配置 Emby Host 或 API Key")

    headers = {"X-Emby-Token": req.emby_api_key, "Content-Type": "application/json"}
    
    # 2. 获取详情 (显式请求 LockData, Tags 字段)
    get_url = f"{req.emby_host}/emby/Users/{req.emby_user_id}/Items/{req.item_id}"
    params = {'api_key': req.emby_api_key, 'Fields': 'Tags,TagItems,LockData,LockedFields'}
    
    try:
        res = requests.get(get_url, params=params, headers=headers)
        if res.status_code != 200:
             raise HTTPException(status_code=400, detail=f"无法获取物品: {res.text}")
        item_data = res.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 3. 计算最终标签
    current_tags = item_data.get('Tags', []) or []
    if req.overwrite:
        final_tags = req.tags  # 覆盖模式：完全信任前端传来的列表
    else:
        final_tags = list(set(current_tags + req.tags)) # 合并模式

    # 4. 准备写入数据
    item_data['Tags'] = final_tags
    
    # 🔥 关键：强制解锁元数据，否则无法写入
    if item_data.get('LockData'): item_data['LockData'] = False
    if item_data.get('LockedFields'): item_data['LockedFields'] = []

    # 🔥 关键：清理只读字段 (发送这些回 Emby 会报错)
    for k in ['MediaSources', 'PlayUserData', 'SeasonUserData', 'Container', 'Size', 'TagItems']:
        if k in item_data: del item_data[k]

    # 5. 提交更新
    post_url = f"{req.emby_host}/emby/Items/{req.item_id}"
    try:
        update_res = requests.post(post_url, json=item_data, headers=headers, params={'api_key': req.emby_api_key})
        if update_res.status_code not in [200, 204]:
             raise HTTPException(status_code=400, detail=update_res.text)
        
        # 6. 同步本地数据库缓存
        db_item = db.query(MediaTag).filter(MediaTag.item_id == req.item_id).first()
        if db_item:
            db_item.tags = final_tags
            if item_data.get("Name"): db_item.name = item_data.get("Name")
        else:
            db.add(MediaTag(item_id=req.item_id, name=item_data.get("Name","Unknown"), tags=final_tags))
        db.commit()

        return {"status": "success", "tags": final_tags}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==========================================
# 🤖 接口: AI 单个分析 (前端点击 'AI分析' 按钮)
# ==========================================

@router.post("/ai_single")
def ai_analyze_single(req: AISingleRequest, db: Session = Depends(get_db)):
    try:
        # 1. 优先查库 (除非强制刷新)
        if not req.force_refresh:
            cached = db.query(MediaTag).filter(MediaTag.item_id == req.item_id).first()
            if cached:
                return {"id": req.item_id, "name": cached.name, "suggested_tags": cached.tags, "source": "database"}

        # 2. 查 Emby 获取详情
        get_url = f"{req.emby_host}/emby/Users/{req.emby_user_id}/Items/{req.item_id}"
        try:
            item_res = requests.get(get_url, params={'api_key': req.emby_api_key})
            item_res.raise_for_status()
            item = item_res.json()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Emby Error: {str(e)}")

        # 清洗名字
        raw_name = item.get('Name', '')
        name = clean_string(raw_name)
        item['Name'] = name # 替换给 AI，提高准确度

        # 3. 调用 AI
        ai_res = ask_ai([item], req.sf_api_key)
        
        # 4. 匹配结果
        if name in ai_res:
            suggested = ai_res[name]
        else:
            # 模糊匹配
            found_key = None
            for k in ai_res.keys():
                if clean_string(k) == name or name in clean_string(k):
                    found_key = k
                    break
            suggested = ai_res[found_key] if found_key else (list(ai_res.values())[0] if len(ai_res)==1 else [])

        if not suggested:
            raise HTTPException(status_code=500, detail="AI 返回空结果")

        # 5. 写入数据库缓存
        db_item = db.query(MediaTag).filter(MediaTag.item_id == req.item_id).first()
        if db_item:
            db_item.tags = suggested
            db_item.name = name
        else:
            db.add(MediaTag(item_id=req.item_id, name=name, tags=suggested))
        db.commit()

        return {"id": req.item_id, "name": name, "suggested_tags": suggested, "source": "ai"}

    except Exception as e:
        logger.error(f"AI Single Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ==========================================
# 📚 其他基础接口 (列表、搜索等)
# ==========================================

@router.post("/libraries")
def get_libs(config: AppConfig):
    """获取 Emby 媒体库列表"""
    url = f"{config.emby_host}/emby/Library/VirtualFolders"
    try:
        res = requests.get(url, params={'api_key': config.emby_api_key}, timeout=5)
        res.raise_for_status()
        return res.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

def process_emby_items(items):
    """处理 Emby 返回的项目列表 (格式化)"""
    result = []
    for item in items:
        # 兼容 Tags 和 TagItems
        tags = item.get('Tags', [])
        if not tags and item.get('TagItems'):
            tags = [t.get('Name') for t in item.get('TagItems')]
        result.append({
            "id": item['Id'], "name": item.get('Name'),
            "year": item.get('ProductionYear'), "current_tags": tags,
            "overview": item.get('Overview', '')
        })
    return result

@router.post("/library_items")
def get_library_items(req: LibraryItemsRequest):
    """获取指定库下的媒体项"""
    url = f"{req.emby_host}/emby/Users/{req.emby_user_id}/Items"
    params = {
        'IncludeItemTypes': 'Series,Movie', 'Recursive': 'true',
        'ParentId': req.library_id, 'Fields': 'Tags,TagItems,OriginalTitle,ProductionYear,Overview',
        'StartIndex': req.start_index, 'SortBy': 'DateCreated', 'SortOrder': 'Descending',
        'api_key': req.emby_api_key
    }
    if req.limit != -1: params['Limit'] = req.limit
    try:
        res = requests.get(url, params=params)
        res.raise_for_status()
        return {"items": process_emby_items(res.json().get('Items', [])), "total": res.json().get('TotalRecordCount')}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/search_items")
def search_items(req: SearchRequest):
    """搜索媒体项"""
    url = f"{req.emby_host}/emby/Users/{req.emby_user_id}/Items"
    params = {
        'IncludeItemTypes': 'Series,Movie', 'Recursive': 'true',
        'SearchTerm': req.search_term, 'Fields': 'Tags,TagItems,OriginalTitle,ProductionYear,Overview',
        'api_key': req.emby_api_key
    }
    try:
        res = requests.get(url, params=params)
        res.raise_for_status()
        return {"items": process_emby_items(res.json().get('Items', []))}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/ai_batch")
def ai_analyze_batch(req: AIBatchRequest, db: Session = Depends(get_db)):
    """批量 AI 分析"""
    logger.info(f"📦 批量 AI: {len(req.item_ids)} 个")
    items_to_process = []
    id_map = {}
    
    # 1. 筛选需要分析的项目 (无缓存或强制刷新)
    for item_id in req.item_ids:
        if not req.force_refresh:
            cached = db.query(MediaTag).filter(MediaTag.item_id == item_id).first()
            if cached and cached.tags: continue

        try:
            url = f"{req.emby_host}/emby/Users/{req.emby_user_id}/Items/{item_id}"
            res = requests.get(url, params={'api_key': req.emby_api_key})
            if res.status_code == 200:
                d = res.json()
                clean_name = clean_string(d.get('Name'))
                d['Name'] = clean_name
                items_to_process.append(d)
                id_map[clean_name] = item_id
        except: pass

    if not items_to_process: return {"status": "skipped"}

    # 2. 批量调用 AI
    ai_results = ask_ai(items_to_process, req.sf_api_key)
    success_count = 0
    results_map = {}

    # 3. 匹配结果并入库
    for item in items_to_process:
        name = item['Name']
        item_id = id_map.get(name)
        suggested = ai_results.get(name) or []
        
        if not suggested:
             # 简单模糊匹配
             for k, v in ai_results.items():
                 if name in k or k in name:
                     suggested = v
                     break

        if suggested:
            db_item = db.query(MediaTag).filter(MediaTag.item_id == item_id).first()
            if db_item:
                db_item.tags = suggested
                db_item.name = name
            else:
                db.add(MediaTag(item_id=item_id, name=name, tags=suggested))
            results_map[item_id] = suggested
            success_count += 1
            
    db.commit()
    return {"status": "success", "results": results_map}