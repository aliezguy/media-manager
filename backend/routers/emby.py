from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from sqlalchemy.orm import Session
from database import get_db
from models import MediaTag
from pydantic import BaseModel
from typing import List, Optional
import requests
import json
import logging
import traceback
import asyncio
import re          # 👈👈👈 必须补上这一行！
from openai import OpenAI
from config.settings import load_config
import time
from services.emby_service import get_item_info, update_item_tags

router = APIRouter()
logger = logging.getLogger("uvicorn")
# ... 在这里粘贴你原 main.py 里 define 的 Pydantic 模型 (LibraryItemsRequest 等) ...
# --- Pydantic 模型 ---
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
    tags: List[str] # 最终要保存的标签列表
    overwrite: bool = True # 默认为覆盖模式，支持删除

# 批量请求模型
class AIBatchRequest(AppConfig):
    item_ids: List[str] # 接收一组 ID
    force_refresh: bool = False

# ... 在这里粘贴 process_emby_items, ask_ai 等辅助函数 ...
# ----------------------------------------------
# 新增：名称清洗工具函数
# ----------------------------------------------
def clean_string(s):
    if not s: return ""
    # 去除 \u200e (LRM), \u200f (RLM), \ufeff (BOM) 等不可见字符
    # 同时也去除首尾空格
    return re.sub(r'[\u200b-\u200f\ufeff]', '', s).strip()    



async def process_emby_item_added(payload: dict):
    """后台任务：处理 Emby 入库事件 (直接使用 Webhook 数据版)"""
    try:
        # 1. 🔥【新增】打印完整报文，方便调试查看 Emby 到底发了什么
        #logger.info(f"📄 [调试] 收到完整 Webhook 报文: {json.dumps(payload, ensure_ascii=False)}")

        # 2. 基础信息提取
        item = payload.get("Item", {})
        if not item:
            logger.warning("⚠️ Payload 中没有 'Item' 字段，跳过")
            return

        item_id = item.get("Id")
        name = item.get("Name", "")
        year = item.get("ProductionYear", "")
        item_type = item.get("Type")
        
        # 提取 TMDB ID (如果有的话，日志里打印出来确认一下)
        provider_ids = item.get("ProviderIds", {})
        tmdb_id = provider_ids.get("Tmdb")
        
        logger.info(f"   📦 [解析] 媒体: {name} ({year}) | 类型: {item_type} | TMDB: {tmdb_id}")

        # 只处理 Movie 和 Series
        if item_type not in ["Movie", "Series"]:
            return

        # 3. 加载配置
        config = load_config()
        sf_api_key = config.get("sf_api_key")
        if not sf_api_key:
            logger.warning(f"⚠️ 未配置 sf_api_key，无法调用 AI")
            return

        # 4. 🔥【核心修改】直接构造数据传给 AI，不再回查 Emby
        # 我们手动构造一个字典，模仿 get_item_info 返回的格式
        clean_name = clean_string(name)
        
        target_info = {
            "Name": clean_name,           # 清洗后的名字
            "ProductionYear": year,       # 年份
            "ProviderIds": provider_ids,  # ID 信息
            "Overview": item.get("Overview", "") # 简介(如果有)
        }
        
        logger.info(f"   🤖 正在请求 AI 分析: [{clean_name} {year}] ...")

        # 5. 调用 AI 分析
        ai_result = ask_ai([target_info], sf_api_key)
        
        if not ai_result:
            logger.warning("⚠️ AI 未返回任何结果")
            return

        # 6. 解析结果并匹配
        suggested_tags = []
        
        # A. 精确匹配
        if clean_name in ai_result:
            suggested_tags = ai_result[clean_name]
            logger.info(f"   ✅ 精确匹配成功")
        else:
            # B. 模糊匹配
            match_found = False
            for k, v in ai_result.items():
                if clean_string(k) == clean_name or clean_name in k:
                    suggested_tags = v
                    match_found = True
                    logger.info(f"   ✅ 模糊匹配成功: Key=[{k}]")
                    break
            
            # C. 兜底 (AI 只返回了一个结果时)
            if not match_found and len(ai_result) == 1:
                suggested_tags = list(ai_result.values())[0]
                logger.info(f"   ⚠️ 使用唯一结果作为兜底")

        # 7. 执行更新 (这一步还是需要调 Emby 接口，但通常更新接口比查询接口容错率高)
        if suggested_tags:
            logger.info(f"   🏷 准备打标签: {suggested_tags}")
            # 这里稍微延迟 1 秒，给 Emby 数据库一点喘息时间，防止锁死
            await asyncio.sleep(1) 
            success = update_item_tags(item_id, suggested_tags)
            
            if success:
                logger.info(f"   🎉 [完成] 标签更新成功！")
            else:
                logger.error(f"   ❌ 标签更新失败 (可能是 Emby 还没准备好接收写入)")
        else:
            logger.warning(f"   ⚠️ AI 返回了结果，但无法匹配: {ai_result}")

    except Exception as e:
        logger.error(f"❌ 后台任务处理异常: {e}")
        logger.error(traceback.format_exc())

@router.post("/webhook/emby")
async def emby_webhook(request: Request, background_tasks: BackgroundTasks):
    logger.info("📨 收到 Emby Webhook 请求")
    
    # 1. 打印 Content-Type 方便调试
    content_type = request.headers.get("content-type", "")
    logger.info(f"   - Content-Type: {content_type}")

    try:
        payload = {}
        
        # 2. 根据类型解析数据
        if "application/json" in content_type:
            payload = await request.json()
            logger.info("   - 已解析 JSON Body")
        elif "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
            # 🔥 这里就是之前报错的地方，安装 python-multipart 后就好了
            form = await request.form()
            data_str = form.get("data")
            if data_str:
                payload = json.loads(data_str)
                logger.info("   - 已解析 Form Data 中的 'data' 字段")
            else:
                logger.warning("   ⚠️ Form Data 中未找到 'data' 字段，尝试直接解析")
                # 有些版本的 Emby 直接把 json 放在 body 里但 header 是 form
                try:
                    body = await request.body()
                    payload = json.loads(body)
                except:
                    pass
        else:
            # 尝试暴力解析 Body
            try:
                payload = await request.json()
                logger.info("   - 强制解析 JSON 成功")
            except:
                logger.warning(f"   ⚠️ 不支持的 Content-Type 且无法解析: {content_type}")
                return {"status": "unsupported_type"}

        # 3. 检查事件类型
        event = payload.get("Event")
        logger.info(f"   - 事件类型: {event}")
        
        # 监听 "item.created" (新入库)
        # 注意：Emby 测试通知的 Event 可能是 "system.notification.test" 或其他
        if event in ["item.created", "library.new"]:
            logger.info("   🚀 触发后台处理任务")
            background_tasks.add_task(process_emby_item_added, payload)
        elif event == "system.notification.test":
             logger.info("   🧪 收到测试通知，连接正常！")
        else:
            logger.info(f"   💤 忽略事件: {event}")
            
        return {"status": "received"}
        
    except Exception as e:
        logger.error(f"❌ Webhook 接收异常: {e}")
        logger.error(traceback.format_exc())
        return {"status": "error"}


# --- 核心逻辑 ---

def ask_ai(items, api_key):
    if not items or not api_key: return {}
    
    client = OpenAI(api_key=api_key, base_url="https://api.siliconflow.cn/v1")
    simple_list = [{"name": i.get('Name'), "year": i.get('ProductionYear')} for i in items]
    
    logger.info(f"🤖 正在请求 AI，剧集信息: {simple_list}")

    prompt = f"""
    请为以下电视剧打上 8-10 个精准的中文标签。
    标签范围包括但不限于：题材(如古装,科幻)、风格(如悬疑,喜剧)、受众(如大女主,职场)、元素(如权谋,穿越)。
    只返回纯JSON格式，不要Markdown格式，不要代码块：{{"剧名": ["标签1", "标签2"]}}
    
    剧集：{json.dumps(simple_list, ensure_ascii=False)}
    """
    try:
        response = client.chat.completions.create(
            model="deepseek-ai/DeepSeek-V3",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2, stream=False
        )
        content = response.choices[0].message.content
        
        # 🔥🔥🔥 调试关键：打印 AI 返回的原始字符串 🔥🔥🔥
        logger.info(f"📦 [DEBUG] AI 原始返回内容: \n{content}")

        # 清理可能的 markdown 标记
        content = content.replace("```json", "").replace("```", "").strip()
        
        return json.loads(content)
    except Exception as e:
        # 🔥🔥🔥 调试关键：打印完整报错堆栈 🔥🔥🔥
        logger.error(f"❌ AI 解析失败: {e}")
        logger.error(traceback.format_exc()) # 打印详细报错位置
        return {}

# --- 业务接口 ---

@router.post("/libraries")
def get_libs(config: AppConfig):
    url = f"{config.emby_host}/emby/Library/VirtualFolders"
    try:
        res = requests.get(url, params={'api_key': config.emby_api_key}, timeout=5)
        res.raise_for_status()
        return res.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

def process_emby_items(items):
    result = []
    for item in items:
        # 混合读取 Tags 和 TagItems
        tags = item.get('Tags', [])
        if not tags and item.get('TagItems'):
            tags = [t.get('Name') for t in item.get('TagItems')]
        
        result.append({
            "id": item['Id'],
            "name": item.get('Name'),
            "year": item.get('ProductionYear'),
            "current_tags": tags,
            "overview": item.get('Overview', '')
        })
    return result

@router.post("/library_items")
def get_library_items(req: LibraryItemsRequest):
    url = f"{req.emby_host}/emby/Users/{req.emby_user_id}/Items"
    params = {
        'IncludeItemTypes': 'Series,Movie',
        'Recursive': 'true',
        'ParentId': req.library_id,
        'Fields': 'Tags,TagItems,OriginalTitle,ProductionYear,Overview',
        'StartIndex': req.start_index,
        'SortBy': 'DateCreated',
        'SortOrder': 'Descending',
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
    url = f"{req.emby_host}/emby/Users/{req.emby_user_id}/Items"
    params = {
        'IncludeItemTypes': 'Series,Movie',
        'Recursive': 'true',
        'SearchTerm': req.search_term,
        'Fields': 'Tags,TagItems,OriginalTitle,ProductionYear,Overview',
        'api_key': req.emby_api_key
    }
    try:
        res = requests.get(url, params=params)
        res.raise_for_status()
        return {"items": process_emby_items(res.json().get('Items', []))}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))



# 3. 新增：批量分析接口 (放到 ai_analyze_single 附近)
@router.post("/ai_batch")
def ai_analyze_batch(req: AIBatchRequest, db: Session = Depends(get_db)):
    logger.info(f"📦 收到批量 AI 请求，包含 {len(req.item_ids)} 个项目")
    
    # --- 第一步：批量获取 Emby 信息 ---
    items_to_process = []
    id_map = {} # 建立 Name -> ID 的映射，方便回填
    
    for item_id in req.item_ids:
        # 1. 如果不是强制刷新，先查库
        if not req.force_refresh:
            cached = db.query(MediaTag).filter(MediaTag.item_id == item_id).first()
            if cached and cached.tags:
                logger.info(f"⚡️ [Batch] 命中缓存: {cached.name}")
                continue # 已有缓存，跳过 AI

        # 2. 去 Emby 获取详情
        try:
            url = f"{req.emby_host}/emby/Users/{req.emby_user_id}/Items/{item_id}"
            res = requests.get(url, params={'api_key': req.emby_api_key})
            if res.status_code == 200:
                item_data = res.json()
                # 清洗名字
                raw_name = item_data.get('Name', '')
                clean_name = clean_string(raw_name)
                item_data['Name'] = clean_name # 替换为干净名字
                
                items_to_process.append(item_data)
                id_map[clean_name] = item_id # 记录映射关系
        except Exception as e:
            logger.error(f"获取 Emby 项目 {item_id} 失败: {e}")

    if not items_to_process:
        return {"status": "skipped", "message": "所有项目均有缓存或获取失败"}

    # --- 第二步：一次性发给 AI ---
    logger.info(f"🤖 [Batch] 发送 {len(items_to_process)} 部剧集给 AI...")
    ai_results = ask_ai(items_to_process, req.sf_api_key)
    
    # --- 第三步：解析并入库 ---
    success_count = 0
    results_map = {} # 返回给前端更新 UI 用

    for item in items_to_process:
        name = item['Name']
        item_id = id_map.get(name)
        suggested = []

        # 尝试匹配 AI 结果
        if name in ai_results:
            suggested = ai_results[name]
        else:
            # 模糊匹配
            for k in ai_results.keys():
                if clean_string(k) == name or name in k:
                    suggested = ai_results[k]
                    break
        
        if suggested:
            # 写入数据库
            try:
                db_item = db.query(MediaTag).filter(MediaTag.item_id == item_id).first()
                if db_item:
                    db_item.tags = suggested
                    db_item.name = name
                else:
                    db.add(MediaTag(item_id=item_id, name=name, tags=suggested))
                
                results_map[item_id] = suggested
                success_count += 1
            except Exception as e:
                logger.error(f"数据库写入失败: {e}")
        else:
            logger.warning(f"⚠️ [Batch] AI 未返回 [{name}] 的标签")

    try:
        db.commit()
    except:
        db.rollback()

    logger.info(f"✅ [Batch] 批量处理完成，成功入库 {success_count} 个")
    
    # 返回成功的 ID 和标签，供前端更新
    return {"status": "success", "results": results_map}



@router.post("/ai_single")
def ai_analyze_single(req: AISingleRequest, db: Session = Depends(get_db)):
    try:
        # 1. 读库
        if not req.force_refresh:
            cached = db.query(MediaTag).filter(MediaTag.item_id == req.item_id).first()
            if cached:
                return {"id": req.item_id, "name": cached.name, "suggested_tags": cached.tags, "source": "database"}

        # 2. 读 Emby
        get_url = f"{req.emby_host}/emby/Users/{req.emby_user_id}/Items/{req.item_id}"
        
        try:
            item_res = requests.get(get_url, params={'api_key': req.emby_api_key})
            item_res.raise_for_status()
            item = item_res.json()
        except Exception as e:
            logger.error(f"Emby 请求失败: {e}")
            raise HTTPException(status_code=400, detail=f"无法获取剧集信息: {str(e)}")

        # 🔥🔥🔥 核心修复：清洗 Emby 返回的名字 🔥🔥🔥
        # Emby 有时会返回带 \u200e 的脏名字，导致 key 匹配失败
        raw_name = item.get('Name', '')
        name = clean_string(raw_name)
        
        # 此时 item['Name'] 还是脏的，为了让 ask_ai 发送干净的名字，我们临时改一下
        item['Name'] = name
        
        logger.info(f"🔍 处理剧集: [{name}] (原始名长度:{len(raw_name)} -> 清洗后:{len(name)})")

        # 3. 问 AI
        ai_res = ask_ai([item], req.sf_api_key)
        
        # 4. 匹配结果
        if name not in ai_res:
            logger.warning(f"⚠️ 精确匹配失败: 期望 [{name}]，AI 返回 {list(ai_res.keys())}")
            
            # 尝试模糊匹配：如果 AI 返回的 key 包含我们的 name，或者反过来
            found_key = None
            for k in ai_res.keys():
                clean_k = clean_string(k)
                if clean_k == name or clean_k in name or name in clean_k:
                    found_key = k
                    break
            
            if found_key:
                logger.info(f"✅ 模糊匹配成功: [{found_key}]")
                suggested = ai_res[found_key]
            elif len(ai_res) == 1:
                # 最后的兜底：只返回了一个结果，那就默认是它
                first_key = list(ai_res.keys())[0]
                logger.info(f"✅ 兜底匹配: 使用唯一结果 [{first_key}]")
                suggested = ai_res[first_key]
            else:
                logger.error(f"❌ 彻底匹配失败，无法确定 AI 返回的是哪部剧。")
                raise HTTPException(status_code=500, detail=f"AI 返回剧名不匹配: {name}")
        else:
            suggested = ai_res[name]
        
        if not suggested: 
            raise HTTPException(status_code=500, detail="AI 返回了空标签列表")

        # 5. 写库
        try:
            db_item = db.query(MediaTag).filter(MediaTag.item_id == req.item_id).first()
            if db_item:
                db_item.tags = suggested
                db_item.name = name
            else:
                db.add(MediaTag(item_id=req.item_id, name=name, tags=suggested))
            db.commit()
        except Exception as e:
            logger.error(f"数据库缓存失败: {e}")

        return {"id": req.item_id, "name": name, "suggested_tags": suggested, "source": "ai"}
        
    except HTTPException as http_ex:
        raise http_ex
    except Exception as e:
        logger.error(f"系统错误: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

# 🔥 核心修改：支持覆盖更新 (实现删除/添加)
@router.post("/save_tags")
def save_tags(req: TagUpdateRequest, db: Session = Depends(get_db)):
    logger.info(f"💾 保存标签 ID: {req.item_id}, 模式: {'覆盖' if req.overwrite else '合并'}")
    
    # 1. 获取元数据
    get_url = f"{req.emby_host}/emby/Users/{req.emby_user_id}/Items/{req.item_id}"
    params = {'api_key': req.emby_api_key, 'Fields': 'Tags,TagItems,LockData,LockedFields'}
    try:
        item_data = requests.get(get_url, params=params).json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 2. 解锁
    if item_data.get('LockData'): item_data['LockData'] = False
    if item_data.get('LockedFields'): item_data['LockedFields'] = []

    # 3. 标签处理
    if req.overwrite:
        # 覆盖模式：前端传什么，就存什么 (支持删除)
        final_tags = req.tags
    else:
        # 合并模式 (旧逻辑)
        existing = item_data.get('Tags', [])
        if not existing and item_data.get('TagItems'):
            existing = [t.get('Name') for t in item_data.get('TagItems')]
        final_tags = list(set(existing) | set(req.tags))

    item_data['Tags'] = final_tags
    
    # 清理干扰字段
    if 'TagItems' in item_data: del item_data['TagItems']
    for k in ['MediaSources', 'PlayUserData', 'SeasonUserData', 'Container', 'Size']:
        if k in item_data: del item_data[k]

    # 4. 写入 Emby
    post_url = f"{req.emby_host}/emby/Items/{req.item_id}?api_key={req.emby_api_key}"
    try:
        res = requests.post(post_url, json=item_data, headers={'Content-Type': 'application/json'})
        if res.status_code not in [200, 204]:
             raise HTTPException(status_code=400, detail=res.text)
        
        # 5. 同步更新本地数据库 (保持缓存一致)
        db_item = db.query(MediaTag).filter(MediaTag.item_id == req.item_id).first()
        if db_item:
            db_item.tags = final_tags
            db.commit()

        time.sleep(1) # 稍微快一点，1秒
        return {"status": "success", "tags": final_tags}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


