import requests
import logging
import traceback
import json
import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any
from config.settings import load_config
from services.tmdb_service import get_tmdb_info
from services.category_service import determine_category
# 引入数据库会话和模型
from database import SessionLocal
from models import WashHistory

logger = logging.getLogger(__name__)

# ===========================
# 1. 基础 MP API 交互
# ===========================

@dataclass(frozen=True)
class MPTokenResult:
    token: str | None = None
    error_code: str | None = None
    http_status: int | None = None
    response_body: str = ""


def get_mp_token() -> MPTokenResult:
    cfg = load_config()
    host = cfg.get("mp_host", "").rstrip('/')
    username = cfg.get("mp_username")
    password = cfg.get("mp_password")
    if not host or not username or not password:
        return MPTokenResult(error_code="config_missing")

    url = f"{host}/api/v1/login/access-token"
    result = MPTokenResult(error_code="login_network_error")
    for attempt in range(3):
        retryable = False
        try:
            resp = requests.post(
                url,
                data={"username": username, "password": password},
                timeout=5,
            )
            response_body = _sanitize_response_body(resp.text)
            if resp.status_code == 200:
                try:
                    response_json = resp.json()
                except (TypeError, ValueError):
                    result = MPTokenResult(
                        error_code="login_response_invalid",
                        http_status=resp.status_code,
                        response_body=response_body,
                    )
                    break
                if not isinstance(response_json, dict):
                    result = MPTokenResult(
                        error_code="login_response_invalid",
                        http_status=resp.status_code,
                        response_body=response_body,
                    )
                    break
                access_token = response_json.get("access_token")
                if access_token:
                    logger.info("✅ [MP登录] Token 获取成功 | HTTP %s", resp.status_code)
                    return MPTokenResult(
                        token=access_token,
                        http_status=resp.status_code,
                        response_body=response_body,
                    )
                result = MPTokenResult(
                    error_code="token_missing",
                    http_status=resp.status_code,
                    response_body=response_body,
                )
                break

            retryable = 500 <= resp.status_code < 600
            result = MPTokenResult(
                error_code="login_server_error" if retryable else "login_http_error",
                http_status=resp.status_code,
                response_body=response_body,
            )
        except requests.exceptions.Timeout as exc:
            retryable = True
            result = MPTokenResult(error_code="login_timeout", response_body=_sanitize_response_body(str(exc)))
        except requests.exceptions.ConnectionError as exc:
            retryable = True
            result = MPTokenResult(error_code="login_network_error", response_body=_sanitize_response_body(str(exc)))
        except requests.exceptions.RequestException as exc:
            result = MPTokenResult(error_code="login_network_error", response_body=_sanitize_response_body(str(exc)))
            break

        if retryable and attempt < 2:
            delay = 1 << attempt
            logger.warning(
                "⚠️ [MP登录] 临时失败，%ss 后重试 (%s/3) | error=%s HTTP=%s body=%s",
                delay,
                attempt + 2,
                result.error_code,
                result.http_status,
                result.response_body,
            )
            time.sleep(delay)
            continue
        break

    logger.error(
        "❌ [MP登录] Token 获取失败 | error=%s HTTP=%s body=%s",
        result.error_code,
        result.http_status,
        result.response_body,
    )
    return result


def _coerce_mp_token_result(value: MPTokenResult | str | None) -> MPTokenResult:
    """兼容旧测试/调用方注入字符串 Token，同时统一内部认证结果。"""
    if isinstance(value, MPTokenResult):
        return value
    if isinstance(value, str) and value:
        return MPTokenResult(token=value)
    return MPTokenResult(error_code="token_missing")

def probe_resource(endpoints, label):
    """智能探测资源"""
    token = _coerce_mp_token_result(get_mp_token()).token
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
    token = _coerce_mp_token_result(get_mp_token()).token
    if not host or not token: return False
    
    if not payload.get("id"):
        return False

    try:
        url = f"{host}/api/v1/subscribe/"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        resp = requests.put(url, json=payload, headers=headers, timeout=30)
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"❌ 更新异常: {e}")
    return False

def get_subscription(sub_id):
    """查询单个订阅详情"""
    cfg = load_config()
    host = cfg.get("mp_host", "").rstrip('/')
    token = _coerce_mp_token_result(get_mp_token()).token
    if not host or not token or not sub_id: return None

    try:
        url = f"{host}/api/v1/subscribe/{sub_id}"
        headers = {"Authorization": f"Bearer {token}"}
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.error(f"⚠️ 查询订阅详情失败: {e}")
    return None

# ===========================
# 🔥 核心：历史记录 & 纯净API
# ===========================

_RESPONSE_BODY_LIMIT = 4096
_TRUNCATION_MARKER = "...[TRUNCATED]"
_SENSITIVE_RESPONSE_KEYS = {
    "token", "access_token", "password", "cookie",
    "api_key", "apikey", "authorization",
}


@dataclass(frozen=True)
class WashSubscriptionResult:
    success: bool
    http_status: int | None = None
    response_body: str = ""
    error: str | None = None
    error_code: str | None = None
    verified_by_lookup: bool = False
    subscription_id: int | None = None


def _redact_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if str(key).lower() in _SENSITIVE_RESPONSE_KEYS
            else _redact_json_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_json_value(item) for item in value]
    return value


def _redact_plain_text(value: str) -> str:
    key_pattern = "|".join(sorted(_SENSITIVE_RESPONSE_KEYS, key=len, reverse=True))
    return re.sub(
        rf"(?i)\b({key_pattern})\b(\s*[:=]\s*)([^\s,;&]+)",
        r"\1\2[REDACTED]",
        value,
    )


def _sanitize_response_body(body: str, limit: int = _RESPONSE_BODY_LIMIT) -> str:
    raw = "" if body is None else str(body)
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        safe = _redact_plain_text(raw)
    else:
        safe = json.dumps(_redact_json_value(parsed), ensure_ascii=False, default=str)

    if len(safe) <= limit:
        return safe
    if limit <= len(_TRUNCATION_MARKER):
        return _TRUNCATION_MARKER[:limit]
    return safe[:limit - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER

def save_history(name, season, tmdb_id, status, msg, details, wash_type="complete"):
    """
    保存历史记录到数据库
    :param wash_type: 'complete'(完结洗版) / 'new_sub'(新增追更) / 'other'
    """
    # 兜底：确保 season 不为 None，避免前端显示空季数
    if not season:
        season = 1
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

def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _subscription_candidates(value: Any):
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                yield item
        return
    if not isinstance(value, dict):
        return
    for key in ("data", "items", "value"):
        nested = value.get(key)
        if isinstance(nested, (dict, list)):
            yield from _subscription_candidates(nested)
            return
    yield value


def _is_matching_wash_subscription(item: dict, tmdb_id: Any, season: Any) -> bool:
    return (
        _as_int(item.get("tmdbid")) == _as_int(tmdb_id)
        and _as_int(item.get("season")) == _as_int(season)
        and _as_int(item.get("best_version")) == 1
        and _as_int(item.get("best_version_full")) == 1
    )


def _lookup_wash_subscription(
    host: str,
    token: str,
    tmdb_id: Any,
    season: Any,
) -> tuple[dict | None, str | None]:
    normalized_season = _as_int(season)
    if _as_int(tmdb_id) is None or normalized_season is None:
        return None, "回查的 tmdbid 或 season 无效"

    headers = {"Authorization": f"Bearer {token}"}
    attempts = (
        (f"{host}/api/v1/subscribe/media/tmdb:{tmdb_id}", {"season": normalized_season}),
        (f"{host}/api/v1/subscribe/", None),
    )
    errors = []
    logger.info(f"      🔎 [洗版回查] TMDB {tmdb_id} | S{normalized_season}")

    for url, params in attempts:
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=30)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            continue
        if not 200 <= resp.status_code < 300:
            errors.append(f"GET {resp.status_code}: {_sanitize_response_body(resp.text)}")
            continue
        try:
            response_payload = resp.json()
        except Exception as exc:
            errors.append(f"GET JSON {type(exc).__name__}: {exc}")
            continue
        for item in _subscription_candidates(response_payload):
            if _is_matching_wash_subscription(item, tmdb_id, normalized_season):
                logger.info(f"      ✅ [洗版回查] 已确认订阅 ID={item.get('id')}")
                return item, None

    error = "; ".join(errors) if errors else "未找到匹配的洗版订阅"
    safe_error = _sanitize_response_body(error)
    logger.warning(f"      ⚠️ [洗版回查] {safe_error}")
    return None, safe_error


def add_wash_subscription(payload: dict) -> WashSubscriptionResult:
    """新增 MoviePilot 洗版订阅；结果不明确时按 TMDB 与 Season 回查。"""
    cfg = load_config()
    host = cfg.get("mp_host", "").rstrip("/")
    auth_result = _coerce_mp_token_result(get_mp_token())
    token = auth_result.token
    if not host or not token:
        error_code = "config_missing" if not host else (auth_result.error_code or "token_missing")
        return WashSubscriptionResult(
            success=False,
            http_status=auth_result.http_status,
            response_body=auth_result.response_body,
            error=error_code,
            error_code=error_code,
        )

    payload.setdefault("username", "AI自动洗版")
    logger.info(f"      🚀 [API新增] Payload: {json.dumps(payload, ensure_ascii=False)}")

    http_status = None
    response_body = ""
    post_error = None
    post_error_code = None
    for post_attempt in range(2):
        try:
            resp = requests.post(
                f"{host}/api/v1/subscribe/",
                json=payload,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                timeout=30,
            )
            http_status = resp.status_code
            response_body = _sanitize_response_body(resp.text)
            logger.info(f"      📥 [洗版POST] HTTP {http_status} | Body: {response_body}")

            if http_status in (401, 403) and post_attempt == 0:
                refreshed_auth = _coerce_mp_token_result(get_mp_token())
                if refreshed_auth.token:
                    token = refreshed_auth.token
                    logger.warning("      ⚠️ [洗版POST] Token 已失效，重新登录后重试")
                    continue
                post_error = (
                    f"POST HTTP {http_status}; 重新认证失败: "
                    f"{refreshed_auth.error_code or 'token_missing'}"
                )
                post_error_code = refreshed_auth.error_code or "post_reauth_failed"
                break

            try:
                response_json = resp.json()
            except Exception as exc:
                post_error = f"响应 JSON 解析失败: {type(exc).__name__}: {exc}"
                post_error_code = "wash_post_failed"
            else:
                if 200 <= http_status < 300 and isinstance(response_json, dict):
                    response_code = response_json.get("code")
                    code_is_zero = type(response_code) is int and response_code == 0
                    if response_json.get("success") is True or code_is_zero:
                        return WashSubscriptionResult(
                            success=True,
                            http_status=http_status,
                            response_body=response_body,
                        )
                post_error = f"POST 未明确成功: HTTP {http_status}"
                post_error_code = "wash_post_failed"
            break
        except Exception as exc:
            post_error = f"{type(exc).__name__}: {exc}"
            post_error_code = "wash_post_exception"
            logger.error(f"      ❌ [洗版POST异常] {_sanitize_response_body(post_error)}")
            break

    tmdb_id = payload.get("tmdbid")
    season = payload.get("season")
    matched = None
    lookup_error = None
    if tmdb_id is not None and season is not None:
        matched, lookup_error = _lookup_wash_subscription(
            host=host,
            token=token,
            tmdb_id=tmdb_id,
            season=season,
        )
    else:
        lookup_error = "回查缺少 tmdbid 或 season"

    safe_post_error = _sanitize_response_body(post_error or "") or None
    if matched:
        return WashSubscriptionResult(
            success=True,
            http_status=http_status,
            response_body=response_body,
            error=safe_post_error,
            error_code=post_error_code,
            verified_by_lookup=True,
            subscription_id=_as_int(matched.get("id")),
        )

    combined_error = "; ".join(filter(None, (safe_post_error, lookup_error))) or None
    return WashSubscriptionResult(
        success=False,
        http_status=http_status,
        response_body=response_body,
        error=_sanitize_response_body(combined_error or "") or None,
        error_code=post_error_code,
    )

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
        year = sub_info.get("year")
        tmdb_id = sub_info.get("tmdbid")
        sub_id = sub_info.get("id")
        season = sub_info.get("season") or None  # None 表示未获取到
        media_type = sub_info.get("type")

        # 兜底：subscribe.added 事件不带 subscribe_info，需从 MP API 查季数
        if not season and sub_id:
            existing = get_subscription(sub_id)
            if existing:
                season = existing.get("season")
                logger.info(f"   🔍 [季数兜底] 从 MP 订阅查到 season={season}")
        if not season:
            season = 1  # 最终兜底

        # 1. 防止循环：检查是否为洗版
        if sub_id:
            full_info = get_subscription(sub_id)
            if full_info:
                data_node = full_info.get("data") if "data" in full_info else full_info
                is_best = data_node.get("best_version") == 1
                remark = str(data_node.get("remark") or data_node.get("note") or "")
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
        exsit_sub = get_subscription(sub_id)
        if exsit_sub:
            final_payload = exsit_sub
        #logger.info(f"exsit_sub     ==={exsit_sub}")
        #logger.info(f"sub_info     ==={sub_info}")
        has_changes = False
        
        # 2. 获取 TMDB 数据 (用于自动分类 + 修复总集数)
        current_category = sub_info.get("category") or (exsit_sub.get("media_category") if exsit_sub else None)
        current_total_ep = sub_info.get("total_episode") # 获取当前总集数
        
        # 判断是否需要请求 TMDB：缺分类 OR (是剧集且缺总集数)
        need_tmdb = False
        if tmdb_id:
            if not current_category: need_tmdb = True
            if not current_total_ep and media_type in ['tv', '电视剧']: need_tmdb = True
        
        if need_tmdb:
            logger.info(f"   1️⃣ [补充信息] 查询 TMDB (ID: {tmdb_id})...")
            tmdb_data = get_tmdb_info(tmdb_id, media_type)
            if tmdb_data:
                # A. 自动分类逻辑
                if not current_category:
                    new_category = determine_category(tmdb_data, media_type)
                    if new_category:
                        final_payload["media_category"] = new_category
                        current_category = new_category
                        has_changes = True
                        logger.info(f"      ✅ 计算出分类: 【{new_category}】")
                
                # B. 修复总集数逻辑 (解决 -24/0 显示错误问题)
                if not current_total_ep and media_type in ['tv', '电视剧']:
                    try:
                        target_season = int(season) if season else 1
                        # TMDB 详情里 seasons 是个列表，需找到对应季
                        seasons_list = tmdb_data.get("seasons", [])
                        for s in seasons_list:
                            if s.get("season_number") == target_season:
                                ep_count = s.get("episode_count")
                                if ep_count and ep_count > 0:
                                    final_payload["total_episode"] = ep_count
                                    has_changes = True
                                    logger.info(f"      ✅ 修复总集数: {ep_count}")
                                    break
                    except Exception as e:
                        logger.warning(f"      ⚠️ 修复总集数失败: {e}")
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



async def delayed_handle_new_subscription(sub_info: dict):
    """
    后台任务包装器：延迟 30 秒后执行订阅添加
    """
    logger.info(f"⏳ 收到任务 {sub_info['name']}，将在 30 秒后执行添加订阅...")
    await asyncio.sleep(30)  # 异步等待，不阻塞主线程
    logger.info(f"⏰ 延迟结束，开始处理订阅: {sub_info['name']}")
    await handle_new_subscription(sub_info)
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
        sub_id = sub_info.get("id")

        # 兜底：如果 webhook 没给出季数，从 MP 订阅查询
        if not season and sub_id:
            existing = get_subscription(sub_id)
            if existing:
                season = existing.get("season")
                logger.info(f"   🔍 [季数兜底] 从 MP 订阅查到 season={season}")

        # 如果仍然没有季数，告警并跳过（避免错误地洗错季）
        if not season:
            logger.error(f"❌ [洗版阻断] 《{name}》未获取到季数，跳过洗版防止洗错季")
            return

        logger.info(f"▶️ [洗版检查] 开始: 《{name}》S{season}")

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
                "year": str(year) if year else None,
                "best_version": 1,
                "best_version_full": 1,  # 全集洗版：只下载整包，不按单集拆包
                "username": "AI自动洗版",
                "note": f"AI洗版策略-[{scheme_name}]"
            }

            # 携带分类信息
            if current_category:
                new_sub_payload["media_category"] = current_category

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
            wash_result = add_wash_subscription(new_sub_payload)

            # 5. 🔥 在这里写历史：完结洗版 (wash_type="complete")
            status_str = "success" if wash_result.success else "failed"
            if wash_result.success and wash_result.verified_by_lookup:
                msg_str = "洗版订阅已创建（回查确认）"
            elif wash_result.success:
                msg_str = "已触发洗版重订阅"
            elif wash_result.error_code == "config_missing":
                msg_str = "MoviePilot 配置缺失"
            elif wash_result.error_code == "login_timeout":
                msg_str = "MoviePilot 登录超时"
            elif wash_result.error_code == "login_server_error":
                msg_str = "MoviePilot 服务暂时异常"
            elif wash_result.error_code in {
                "login_network_error",
                "login_http_error",
                "login_response_invalid",
                "token_missing",
            }:
                msg_str = "MoviePilot 登录失败"
            else:
                msg_str = "洗版API请求失败"

            # 🔥 记录历史 (增强 details)
            save_history(
                name, season, tmdb_id, status_str, msg_str,
                {
                    "scheme": scheme_name,
                    "downloader": matched_scheme.get("downloader"),
                    "filter_groups": matched_scheme.get("filter_groups"),
                    "quality": matched_scheme.get("quality"),
                    "sites": matched_scheme.get("sites"),
                    "keywords": matched_scheme.get("keywords"),
                    "http_status": wash_result.http_status,
                    "response_body": wash_result.response_body,
                    "error": wash_result.error,
                    "error_code": wash_result.error_code,
                    "verified_by_lookup": wash_result.verified_by_lookup,
                    "subscription_id": wash_result.subscription_id,
                },
                wash_type="complete",
            )
            
        else:
            logger.info("   ⏹ 未命中任何洗版策略 (且无兜底)")

    except Exception as e:
        logger.error(f"❌ 洗版流程异常: {e}")
        logger.error(traceback.format_exc())


async def delayed_run_wash_process(sub_info: dict):
    """
    后台任务包装器：延迟 30 秒后执行订阅添加
    """
    logger.info(f"⏳ 收到任务 {sub_info['name']}，将在 30 秒后执行添加订阅...")
    await asyncio.sleep(30)  # 异步等待，不阻塞主线程
    logger.info(f"⏰ 延迟结束，开始处理订阅: {sub_info['name']}")
    await run_wash_process(sub_info)


# ===========================
# 5. 媒体识别（种子名称 → 结构化元数据）
# ===========================

def recognize_torrent_with_mp(torrent_name: str) -> dict | None:
    """Use MoviePilot's built-in media recognition to parse a torrent name.

    Calls ``GET /api/v1/media/recognize?title=...`` which leverages MP's
    own TMDB integration and recognition engine — significantly more
    accurate than regex heuristics for ambiguous titles/years.

    Returns a dict with keys matching the organize_service format, or
    ``None`` if MP is unavailable or recognition fails.
    """
    cfg = load_config()
    host = cfg.get("mp_host", "").rstrip("/")
    if not host:
        logger.debug("MP recognize skipped: mp_host not configured")
        return None

    token = _coerce_mp_token_result(get_mp_token()).token
    if not token:
        logger.debug("MP recognize skipped: unable to obtain MP token")
        return None

    try:
        url = f"{host}/api/v1/media/recognize"
        headers = {"Authorization": f"Bearer {token}"}
        resp = requests.get(
            url,
            headers=headers,
            params={"title": torrent_name},
            timeout=30,
        )
        if resp.status_code != 200:
            logger.debug("MP recognize HTTP %d for '%s'", resp.status_code, torrent_name[:60])
            return None

        data = resp.json()
        logger.info("MP recognize raw response for '%s': %s", torrent_name[:60], str(data)[:300])

        # MP wraps recognition result in "meta_info" (v2.x).
        # Also handle older versions that might use "data" or return a flat object.
        media = data if isinstance(data, dict) else {}
        if "meta_info" in media:
            media = media["meta_info"]
        elif "data" in media:
            media = media["data"]
        if not media or not isinstance(media, dict):
            return None

        # Title: prefer cn_name (clean show name).
        # MP's "title" field contains the full original torrent name,
        # not the extracted show name.
        title = (
            media.get("cn_name")
            or media.get("name")
            or media.get("title")
        )
        if not title:
            return None

        # Skip when MP can't determine the media type (title alone without
        # season/episode markers is not enough for reliable recognition).
        media_type = media.get("type") or "电视剧"
        if media_type == "未知":
            logger.debug(
                "MP recognize: type=未知 for '%s' — skipping (insufficient info)",
                torrent_name[:60],
            )
            return None

        year = media.get("year")
        if year:
            try:
                year = str(int(year))
            except (ValueError, TypeError):
                year = None

        # MP uses "begin_season" for the detected season number.
        season = (
            media.get("begin_season")
            or media.get("season")
            or media.get("season_number")
        )
        if season is not None and season != 0:
            try:
                season = int(season)
            except (ValueError, TypeError):
                season = 1
        else:
            season = 1

        # MP's /media/recognize does NOT return tmdb_id.
        # That requires a separate TMDB search (done later in organize_service).
        tmdb_id = media.get("tmdbid") or media.get("tmdb_id") or media.get("tmdbId") or None
        if tmdb_id:
            try:
                tmdb_id = int(tmdb_id)
            except (ValueError, TypeError):
                tmdb_id = None

        result = {
            "title": str(title),
            "year": year,
            "season": season,
            "tmdb_id": tmdb_id,
            "tmdb_name": title,
            "source": "mp",
            "media_type": str(media_type),
            "success": True,
        }
        logger.info(
            "MP recognize: '%s' → title=%s year=%s tmdb=%s season=%s type=%s",
            torrent_name[:60], title, year, tmdb_id, season, media_type,
        )
        return result

    except Exception as e:
        logger.warning("MP recognize failed for '%s': %s", torrent_name[:60], e)
        return None
