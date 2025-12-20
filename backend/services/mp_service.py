import requests
import logging
import traceback
import json
from config.settings import load_config
from services.tmdb_service import get_tmdb_info
from services.category_service import determine_category
# 引入数据库会话和模型
from database import SessionLocal
from models import WashHistory

logger = logging.getLogger("uvicorn")

# ===========================
# 1. 基础 MP API 交互
# ===========================

def get_mp_token():
    cfg = load_config()
    host = cfg.get("mp_host", "").rstrip('/')
    username = cfg.get("mp_username")
    password = cfg.get("mp_password")
    if not host or not username or not password: return None
    try:
        url = f"{host}/api/v1/login/access-token"
        resp = requests.post(url, data={"username": username, "password": password}, timeout=5)
        if resp.status_code == 200: return resp.json().get("access_token")
    except: pass
    return None

def probe_resource(endpoints, label):
    """智能探测资源"""
    token = get_mp_token()
    cfg = load_config()
    host = cfg.get("mp_host", "").rstrip('/')
    if not token or not host: return []

    headers = {"Authorization": f"Bearer {token}"}

    for ep in endpoints:
        try:
            url = f"{host}{ep}"
            resp = requests.get(url, headers=headers, params={"page": 1, "size": 1000}, timeout=5)
            
            if resp.status_code == 200:
                json_data = resp.json()
                items = []
                if isinstance(json_data, list):
                    items = json_data
                elif isinstance(json_data, dict):
                    if "data" in json_data:
                        inner = json_data["data"]
                        if isinstance(inner, list): items = inner
                        elif isinstance(inner, dict):
                            if "value" in inner: items = inner["value"]
                            elif "items" in inner: items = inner["items"]
                            else: items = [inner]
                
                if not items: continue

                result = []
                for i in items:
                    if not isinstance(i, dict): continue
                    name = i.get("name") or i.get("alias") or i.get("rule_name") or i.get("client_name")
                    uid = i.get("id")
                    if uid is None: uid = name 
                    if name: result.append({"id": uid, "name": name})
                
                logger.info(f"✅ [{label}] 探测成功: {url} | 获取到 {len(result)} 条数据")
                return result
        except Exception as e:
            pass
    return []

def get_mp_resources():
    return {
        "sites": probe_resource(["/api/v1/site/", "/api/v1/site/rss"], "站点"),
        "filter_groups": probe_resource(["/api/v1/system/setting/UserFilterRuleGroups", "/api/v1/filter/", "/api/v1/rule/"], "规则组"),
        "downloaders": probe_resource(["/api/v1/system/setting/Downloaders", "/api/v1/downloader/"], "下载器")
    }

def update_subscription(payload):
    """PUT 更新订阅"""
    cfg = load_config()
    host = cfg.get("mp_host", "").rstrip('/')
    token = get_mp_token()
    if not host or not token: return False
    
    if not payload.get("id"):
        return False

    try:
        url = f"{host}/api/v1/subscribe/"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        resp = requests.put(url, json=payload, headers=headers, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"❌ 更新异常: {e}")
    return False

def get_subscription(sub_id):
    """查询单个订阅详情"""
    cfg = load_config()
    host = cfg.get("mp_host", "").rstrip('/')
    token = get_mp_token()
    if not host or not token or not sub_id: return None

    try:
        url = f"{host}/api/v1/subscribe/{sub_id}"
        headers = {"Authorization": f"Bearer {token}"}
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.error(f"⚠️ 查询订阅详情失败: {e}")
    return None

# ===========================
# 🔥 核心：历史记录 & 纯净API
# ===========================

def save_history(name, season, tmdb_id, status, msg, details, wash_type="complete"):
    """
    保存历史记录到数据库
    :param wash_type: 'complete'(完结洗版) / 'new_sub'(新增追更) / 'other'
    """
    logger.info(f"📝 [历史-{wash_type}] {name} S{season} | {status}: {msg}")
    try:
        db = SessionLocal()
        record = WashHistory(
            name=name,
            season=season,
            tmdb_id=tmdb_id,
            status=status,
            message=msg,
            wash_params=details,
            wash_type=wash_type  # 🔥 写入类型
        )
        db.add(record)
        db.commit()
        db.close()
    except Exception as e:
        logger.error(f"❌ 写入数据库失败: {e}")

def add_wash_subscription(payload):
    """
    🔥 纯净API调用：只负责 POST 新增订阅，不负责写历史
    :return: Boolean (成功/失败)
    """
    cfg = load_config()
    host = cfg.get("mp_host", "").rstrip('/')
    token = get_mp_token()
    if not host or not token: return False

    try:
        # 自动注入 username 标记
        if "username" not in payload:
            payload["username"] = "AI自动洗版"

        logger.info(f"      🚀 [API新增] Payload: {json.dumps(payload, ensure_ascii=False)}")
        
        url = f"{host}/api/v1/subscribe/"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        
        # 判断结果
        if resp.status_code == 200:
            res_json = resp.json()
            # 兼容不同版本 MP 的成功标识
            if isinstance(res_json, dict):
                if res_json.get("success") is True or res_json.get("code") == 0:
                    return True
            # 如果直接返回列表或空字典也可能表示成功（视版本而定），但通常有 success 字段
            return False
        else:
            logger.error(f"      ❌ [API失败] HTTP {resp.status_code} - {resp.text}")
            return False
            
    except Exception as e:
        logger.error(f"      ❌ [API异常] {e}")
    return False

# ===========================
# 2. 核心通用逻辑
# ===========================

def _find_best_scheme(title, category, schemes, scheme_type="策略"):
    if not schemes: return None
    logger.info(f"      🔍 [开始匹配{scheme_type}] 标题:[{title}] | 分类:[{category}] | 规则数:{len(schemes)}")

    specific_match = None
    fallback_match = None

    for scheme in schemes:
        if not scheme.get('active', True) and not scheme.get('enable', True): continue

        raw_keywords = scheme.get('keywords')
        is_empty = False
        if raw_keywords is None: is_empty = True
        elif isinstance(raw_keywords, str) and not raw_keywords.strip(): is_empty = True
        elif isinstance(raw_keywords, list) and len(raw_keywords) == 0: is_empty = True
            
        if is_empty:
            if not fallback_match: fallback_match = scheme
            continue

        keywords = raw_keywords if isinstance(raw_keywords, list) else str(raw_keywords).replace('，', ',').split(',')
        for kw in keywords:
            kw = str(kw).strip()
            if not kw: continue
            
            match_title = kw in title
            match_category = False
            if category:
                if isinstance(category, list): match_category = kw in category
                else: match_category = (kw == str(category).strip())

            if match_title or match_category:
                specific_match = scheme
                logger.info(f"      ✅ [{scheme_type}命中] 规则:[{scheme.get('name')}] | 匹配词:[{kw}]")
                return specific_match 

    if fallback_match:
        logger.info(f"      ⚠️ [兜底命中] 使用兜底策略: [{fallback_match.get('name')}]")
        return fallback_match
    return None

# ===========================
# 3. 业务流程：新增订阅 (追更)
# ===========================

async def handle_new_subscription(sub_info):
    try:
        name = sub_info.get("name")
        tmdb_id = sub_info.get("tmdbid")
        sub_id = sub_info.get("id")
        season = sub_info.get("season", 1)
        media_type = sub_info.get("type")

        # 1. 防止循环：检查是否为洗版
        if sub_id:
            full_info = get_subscription(sub_id)
            if full_info:
                data_node = full_info.get("data") if "data" in full_info else full_info
                is_best = data_node.get("best_version") == 1
                remark = str(data_node.get("remark", ""))
                if is_best or "AI洗版" in remark:
                    logger.info(f"⚪ [忽略新增] 检测到洗版标记 (BestVersion=1)，跳过追更策略: 《{name}》")
                    return

        logger.info(f"▶️ [新增订阅] 处理开始: 《{name}》 (ID: {sub_id})")

        cfg = load_config()
        schemes = cfg.get("subscribe_schemes", []) or cfg.get("subscribe_rules", [])
        if not schemes:
            logger.warning("      ⚠️ 未配置 'subscribe_schemes'")
            return

        final_payload = {"id": sub_id} if sub_id else {}
        has_changes = False
        
        # 2. 自动分类
        current_category = sub_info.get("category")
        if tmdb_id and not current_category:
            logger.info(f"   1️⃣ [自动分类] 查询 TMDB...")
            tmdb_data = get_tmdb_info(tmdb_id, media_type)
            if tmdb_data:
                new_category = determine_category(tmdb_data, media_type)
                if new_category:
                    final_payload["category"] = new_category 
                    current_category = new_category
                    has_changes = True
                    logger.info(f"      ✅ 计算出分类: 【{new_category}】")

        # 3. 匹配追更策略
        logger.info(f"   2️⃣ [追更策略] 开始匹配...")
        matched_scheme = _find_best_scheme(name, current_category, schemes, "追更策略")

        if matched_scheme:
            f_groups = matched_scheme.get("filter_groups")
            if f_groups:
                final_payload["filter_groups"] = f_groups if isinstance(f_groups, list) else [f_groups]
                has_changes = True
            dl = matched_scheme.get("downloader")
            if dl:
                final_payload["downloader"] = dl
                has_changes = True
            sites = matched_scheme.get("sites")
            if sites:
                final_payload["sites"] = sites
                has_changes = True
            
            logger.info(f"      ✅ 策略应用成功")

        # 4. 提交更改 & 写历史
        if has_changes and sub_id:
            success = update_subscription(final_payload)
            if success and matched_scheme:
                save_history(
                    name, season, tmdb_id, "success", 
                    f"匹配策略: [{matched_scheme.get('name')}]", 
                    {
                        "scheme": matched_scheme.get("name"),
                        "downloader": matched_scheme.get("downloader"),
                        "filter_groups": matched_scheme.get("filter_groups"),
                        "quality": matched_scheme.get("quality"),
                        "sites": matched_scheme.get("sites"), # 新增站点
                        "keywords": matched_scheme.get("keywords") # 新增匹配关键词
                    },
                    wash_type="new_sub"
                )
        else:
            logger.info(f"   💤 无需更新或缺少ID")

    except Exception as e:
        logger.error(f"❌ 新增订阅处理异常: {e}")
        logger.error(traceback.format_exc())

# ===========================
# 4. 业务流程：订阅完成 (洗版)
# ===========================

async def run_wash_process(sub_info):
    try:
        cfg = load_config()
        schemes = cfg.get("wash_schemes", [])
        
        name = sub_info.get("name", "未知")
        tmdb_id = sub_info.get("tmdbid")
        media_type = sub_info.get("type", "电视剧")
        season = sub_info.get("season")
        year = sub_info.get("year")
        current_category = sub_info.get("category")

        logger.info(f"▶️ [洗版检查] 开始: 《{name}》")

        if not schemes:
            logger.info("   ⏹ 未配置洗版策略，跳过")
            return

        # 1. 补充分类
        if not current_category and tmdb_id:
             tmdb_data = get_tmdb_info(tmdb_id, media_type)
             if tmdb_data:
                 current_category = determine_category(tmdb_data, media_type)
                 logger.info(f"   ✅ 补充分类: {current_category}")

        # 2. 匹配洗版策略
        matched_scheme = _find_best_scheme(name, current_category, schemes, "洗版策略")
        
        if matched_scheme:
            scheme_name = matched_scheme.get('name')
            logger.info(f"   🚀 [命中洗版] 策略: {scheme_name}，执行洗版流程...")
            
            # 3. 构造 Payload (POST)
            new_sub_payload = {
                "name": name,
                "type": media_type,
                "tmdbid": tmdb_id,
                "season": int(season) if season else 1,
                "year": year,
                "best_version": 1, 
                "username": "AI自动洗版",
                "remark": f"AI洗版策略-[{scheme_name}]"
            }

            f_groups = matched_scheme.get("filter_groups")
            if f_groups:
                new_sub_payload["filter_groups"] = f_groups if isinstance(f_groups, list) else [f_groups]
            dl = matched_scheme.get("downloader")
            if dl:
                new_sub_payload["downloader"] = dl
            sites = matched_scheme.get("sites")
            if sites:
                new_sub_payload["sites"] = sites
            qual = matched_scheme.get("quality")
            if qual:
                new_sub_payload["quality"] = qual

            # 4. 调用纯净 API
            is_ok = add_wash_subscription(new_sub_payload)
            
            # 5. 🔥 在这里写历史：完结洗版 (wash_type="complete")
            status_str = "success" if is_ok else "failed"
            msg_str = "已触发洗版重订阅" if is_ok else "洗版API请求失败"
            
            # 🔥 记录历史 (增强 details)
            save_history(
                name, season, tmdb_id, status_str, msg_str,
                {
                    "scheme": scheme_name,
                    "downloader": matched_scheme.get("downloader"),
                    "filter_groups": matched_scheme.get("filter_groups"),
                    "quality": matched_scheme.get("quality"),
                    "sites": matched_scheme.get("sites"), # 新增站点
                    "keywords": matched_scheme.get("keywords") # 新增匹配关键词
                },
                wash_type="complete"
            )
            
        else:
            logger.info("   ⏹ 未命中任何洗版策略 (且无兜底)")

    except Exception as e:
        logger.error(f"❌ 洗版流程异常: {e}")
        logger.error(traceback.format_exc())