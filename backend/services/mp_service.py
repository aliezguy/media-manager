import requests
import logging
import json
from datetime import datetime
from config.settings import load_config
from database import SessionLocal
from models import WashHistory

logger = logging.getLogger("uvicorn")

def get_mp_token():
    """获取 Token"""
    cfg = load_config()
    host = cfg.get("mp_host")
    username = cfg.get("mp_username")
    password = cfg.get("mp_password")

    if not host or not username:
        return None

    host = host.rstrip('/')
    login_url = f"{host}/api/v1/login/access-token"
    
    try:
        resp = requests.post(login_url, data={"username": username, "password": password}, timeout=5)
        if resp.status_code == 200:
            return resp.json().get("access_token")
    except Exception as e:
        logger.error(f"❌ 登录异常: {e}")
    return None

def save_history(name, season, tmdb_id, status, message, params):
    """写历史记录"""
    db = SessionLocal()
    try:
        history = WashHistory(
            name=name, season=season, tmdb_id=str(tmdb_id),
            status=status, message=message, wash_params=params, created_at=datetime.now()
        )
        db.add(history)
        db.commit()
    except: pass
    finally: db.close()

# --- Emby 辅助查询 ---
def find_emby_library_name(tmdb_id):
    cfg = load_config()
    host, api_key = cfg.get("emby_host"), cfg.get("emby_api_key")
    if not host or not api_key: return None
    try:
        search_url = f"{host}/emby/Items"
        params = { "api_key": api_key, "Recursive": "true", "AnyProviderIdEquals": f"tmdb.{tmdb_id}", "IncludeItemTypes": "Series,Movie", "Fields": "ParentId" }
        res = requests.get(search_url, params=params, timeout=5)
        if res.status_code == 200 and res.json().get("Items"):
            item_id = res.json()["Items"][0]['Id']
            return scan_libraries_for_item(host, api_key, item_id)
    except: pass
    return None

def scan_libraries_for_item(host, api_key, target_id):
    try:
        libs = requests.get(f"{host}/emby/Library/VirtualFolders", params={"api_key": api_key}).json()
        for lib in libs:
            check = requests.get(f"{host}/emby/Items", params={"api_key": api_key, "Recursive": "true", "ParentId": lib.get("ItemId"), "Ids": target_id})
            if check.status_code == 200 and check.json().get("TotalRecordCount", 0) > 0:
                return lib.get("Name")
    except: pass
    return None

# --- 核心洗版逻辑 (纯净版) ---
def match_scheme(name, library_name, schemes):
    target_str = f"{name} {library_name or ''}".lower()
    
    # 1. 关键词匹配
    for scheme in schemes:
        if not scheme.get("active", True): continue
        keywords = scheme.get("keywords", [])
        if not keywords: continue 
        for kw in keywords:
            if kw.lower() in target_str: return scheme

    # 2. 兜底匹配
    for scheme in schemes:
        if not scheme.get("keywords") and scheme.get("active", True): return scheme
            
    # 3. 硬兜底
    return { "name": "系统兜底", "filter_groups": ["完结洗版"], "downloader": "qb完结", "quality": "WEB-DL", "sites": [] }

def run_wash_process(name, tmdb_id, season, year, library_name=None):
    cfg = load_config()
    host = cfg.get("mp_host").rstrip('/')
    schemes = cfg.get("wash_schemes", [])

    # 智能补充库名
    if not library_name and tmdb_id:
        library_name = find_emby_library_name(tmdb_id)

    # 1. 匹配
    matched_scheme = match_scheme(name, library_name, schemes)
    log_msg = f"🎯 [策略匹配] 《{name}》"
    if library_name: log_msg += f" (库: {library_name})"
    logger.info(f"{log_msg} -> [{matched_scheme['name']}]")

    # 2. 获取参数 (无需类型转换，信任前端及配置文件的 List 结构)
    filter_groups = matched_scheme.get("filter_groups", [])
    downloader = matched_scheme.get("downloader")
    quality = matched_scheme.get("quality")
    sites = matched_scheme.get("sites", [])

    # 3. 构造请求
    token = get_mp_token()
    if not token: return

    url = f"{host}/api/v1/subscribe/"
    headers = {"Authorization": f"Bearer {token}"}

    payload = {
        "name": name, "type": "电视剧", "tmdbid": int(tmdb_id),
        "season": int(season) if season else 1, "year": str(year),
        "quality": quality, "filter_groups": filter_groups,
        "best_version": True, "downloader": downloader,
        "remark": f"AI洗版-{matched_scheme['name']}"
    }
    if sites: payload["sites"] = sites

    try:
        logger.info(f"📦 [Payload] {json.dumps(payload, ensure_ascii=False)}")
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        
        # 结果处理与日志
        status, msg = "failed", f"HTTP {resp.status_code}"
        if resp.status_code == 200:
            res_json = resp.json()
            if res_json.get("success") is True or res_json.get("code") == 0:
                status, msg = "success", f"ID: {res_json.get('data', {}).get('id')}"
                logger.info("✅ [洗版成功]")
            else:
                msg = str(res_json)
                logger.error(f"❌ [业务拒绝] {msg}")
        else:
            logger.error(f"❌ [请求失败] {msg}")
            
        save_history(name, season, tmdb_id, status, msg, {
            "scheme": matched_scheme['name'], "filters": filter_groups, 
            "dl": downloader, "sites": sites
        })

    except Exception as e:
        logger.error(f"❌ 执行异常: {e}")

# --- 资源获取 (适配新版 MP 接口) ---
def get_mp_resources():
    cfg = load_config()
    host = cfg.get("mp_host")
    if host: host = host.rstrip('/')
    token = get_mp_token()
    
    if not host or not token: return {}

    headers = {"Authorization": f"Bearer {token}"}
    
    def probe_resource(endpoints, label):
        for ep in endpoints:
            try:
                url = f"{host}{ep}"
                resp = requests.get(url, headers=headers, params={"page": 1, "size": 1000}, timeout=5)
                if resp.status_code == 200:
                    json_data = resp.json()
                    # 智能解析 data.value 或 data.items
                    items = []
                    if isinstance(json_data, list): items = json_data
                    elif isinstance(json_data, dict):
                        if isinstance(json_data.get("data"), list): items = json_data["data"]
                        elif isinstance(json_data.get("data"), dict):
                            inner = json_data.get("data")
                            if "value" in inner: items = inner["value"]
                            elif "items" in inner: items = inner["items"]
                    
                    result = []
                    for i in items:
                        name = i.get("name") or i.get("alias") or i.get("rule_name") or i.get("client_name")
                        uid = i.get("id") or name
                        if name: result.append({"id": uid, "name": name})
                    
                    if result: logger.info(f"✅ [{label}] 获取 {len(result)} 条")
                    return result
            except: pass
        logger.warning(f"⚠️ [{label}] 获取失败")
        return []

    return {
        "sites": probe_resource(["/api/v1/site/"], "站点"),
        "filters": probe_resource(["/api/v1/system/setting/UserFilterRuleGroups", "/api/v1/filter/", "/api/v1/rule/"], "规则组"),
        "downloaders": probe_resource(["/api/v1/system/setting/Downloaders", "/api/v1/downloader/"], "下载器")
    }