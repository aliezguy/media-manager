"""
Douban Frodo API 客户端 — 封装豆瓣 API v2 签名、鉴权与数据抓取。

提供:
  - celebrity_details: 获取演员/名人详细信息（含高清头像）
  - search: 豆瓣全站搜索
  - imdbid: IMDb ID → 豆瓣条目
  - match_info: 名称/IMDb ID 智能匹配豆瓣条目
  - get_acting: 获取影视条目演职员表
"""

import requests  # type: ignore
from typing import Optional, Dict, Any, List
import logging
import json
import re
import base64
import hashlib
import hmac
import time
from urllib import parse
from datetime import datetime
from random import choice
import threading

logger = logging.getLogger("uvicorn")


def _clean_character_name(name: str) -> str:
    """清洗豆瓣角色名字符串。

    豆瓣角色名可能是 "张三 / 李四" 格式（一人饰多角），
    统一取第一个角色名，去除括号内备注。
    """
    if not name:
        return ""
    # 取第一个 / 之前的部分
    parts = name.split("/")
    first = parts[0].strip()
    # 去除括号备注，如 "张三(青年)" → "张三"
    first = re.sub(r'\([^)]*\)', '', first)
    first = re.sub(r'（[^）]*）', '', first)
    return first.strip()


class DoubanApi:
    _session: Optional[requests.Session] = None
    _session_lock = threading.Lock()
    _cooldown_seconds: float = 1.5
    _last_request_time: float = 0.0
    _cooldown_lock = threading.Lock()
    _user_cookie: Optional[str] = None

    _urls = {
        "search": "/search/weixin", "imdbid": "/movie/imdb/%s",
        "movie_detail": "/movie/", "tv_detail": "/tv/",
        "movie_celebrities": "/movie/%s/celebrities", "tv_celebrities": "/tv/%s/celebrities",
        "celebrity_detail": "/celebrity/%s",
    }
    _user_agents = [
        "api-client/1 com.douban.frodo/7.22.0.beta9(231) Android/23 product/Mate 40 vendor/HUAWEI model/Mate 40 brand/HUAWEI  rom/android  network/wifi  platform/AndroidPad",
        "api-client/1 com.douban.frodo/7.18.0(230) Android/22 product/MI 9 vendor/Xiaomi model/MI 9 brand/Android  rom/miui6  network/wifi  platform/mobile nd/1",
    ]
    _api_secret_key = "bf7dddc7c9cfe6f7"
    _api_key = "0dad551ec0f84ed02907ff5c42e8ec70"
    _api_key2 = "0ab215a8b1977939201640fa14c66bab"
    _base_url = "https://frodo.douban.com/api/v2"
    _api_url = "https://api.douban.com/v2"
    _default_timeout = 15

    def __init__(self, cooldown_seconds: Optional[float] = None, user_cookie: Optional[str] = None):
        if DoubanApi._session is None:
            with DoubanApi._session_lock:
                if DoubanApi._session is None:
                    DoubanApi._session = requests.Session()
                    logger.debug("DoubanApi requests.Session 已初始化。")

        if cooldown_seconds is not None and cooldown_seconds > 0:
            DoubanApi._cooldown_seconds = cooldown_seconds
            logger.debug("  ➜ 豆瓣Api 已设置请求冷却时间为: %s 秒。", DoubanApi._cooldown_seconds)
        if user_cookie:
            DoubanApi._user_cookie = user_cookie
            logger.debug("  ➜ DoubanApi 已加载用户登录 Cookie。")

    @classmethod
    def _apply_cooldown(cls):
        """在每次API请求前应用冷却等待，线程安全。"""
        with cls._cooldown_lock:
            now = time.time()
            elapsed = now - cls._last_request_time

            if elapsed < cls._cooldown_seconds:
                wait_time = cls._cooldown_seconds - elapsed
                logger.debug("  ➜ 豆瓣 API 冷却中... 等待 %.2f 秒。", wait_time)
                time.sleep(wait_time)

            cls._last_request_time = time.time()

    @classmethod
    def _ensure_session(cls):
        """确保 requests.Session 已初始化。线程安全。"""
        if cls._session is None:
            with cls._session_lock:
                if cls._session is None:
                    cls._session = requests.Session()
                    logger.debug("DoubanApi: requests.Session 已重新初始化 (ensure_session)。")

    @classmethod
    def _sign(cls, url: str, ts: str, method='GET') -> str:
        url_path = parse.urlparse(url).path
        raw_sign = '&'.join([method.upper(), parse.quote(url_path, safe=''), ts])
        return base64.b64encode(hmac.new(cls._api_secret_key.encode(), raw_sign.encode(), hashlib.sha1).digest()).decode()

    def _make_error_dict(self, error_code: str, message: str, original_response: Optional[Dict] = None) -> Dict[str, Any]:
        """辅助函数，创建统一的错误返回字典"""
        err_dict = {"error": error_code, "message": message}
        if original_response and isinstance(original_response, dict) and original_response.get("code"):
            err_dict["douban_code"] = original_response.get("code")
        return err_dict

    def __invoke(self, url: str, **kwargs) -> Dict[str, Any]:
        DoubanApi._apply_cooldown()
        DoubanApi._ensure_session()
        if DoubanApi._session is None:
            return self._make_error_dict("session_not_initialized", "Session未初始化")
        req_url = DoubanApi._base_url + url
        params: Dict[str, Any] = {'apiKey': DoubanApi._api_key, **kwargs}
        ts = params.pop('_ts', datetime.strftime(datetime.now(), '%Y%m%d'))
        params.update({'os_rom': 'android', '_ts': ts, '_sig': DoubanApi._sign(url=req_url, ts=ts)})
        headers = {'User-Agent': choice(DoubanApi._user_agents)}
        if DoubanApi._user_cookie:
            headers['Cookie'] = DoubanApi._user_cookie
        resp = None
        try:
            resp = DoubanApi._session.get(req_url, params=params, headers=headers, timeout=DoubanApi._default_timeout)
            resp.raise_for_status()
            response_json = resp.json()
            if response_json.get("code") == 1080:
                msg = response_json.get('msg', "豆瓣API速率限制")
                logger.warning("  ➜ GET触发豆瓣速率限制: %s", msg)
                return self._make_error_dict("rate_limit", msg, response_json)
            return response_json
        except requests.exceptions.HTTPError as e:
            msg = str(e)
            if e.response is not None:
                try:
                    error_json = e.response.json()
                    if error_json.get("code") == 1001 or "need_login" in error_json.get("msg", ""):
                        msg = "need_login"
                        logger.error("  ➜ 豆瓣API请求失败: 需要登录。请在设置中配置有效的豆瓣Cookie。")
                    else:
                        msg = error_json.get("msg", str(e))
                except json.JSONDecodeError:
                    msg = f"{str(e)} (响应非JSON: {e.response.text[:100]})"
            logger.error("HTTP error on GET %s: %s", req_url, msg, exc_info=False)
            return self._make_error_dict("http_error", msg, getattr(e.response, 'json', lambda: None)())
        except requests.exceptions.RequestException as e:
            logger.error("Request failed on GET %s: %s", req_url, e, exc_info=True)
            return self._make_error_dict("request_exception", str(e))
        except json.JSONDecodeError as e:
            logger.error("JSONDecodeError on GET %s: %s. Response text: %s", req_url, e, resp.text[:200] if resp else 'N/A', exc_info=True)
            return self._make_error_dict("json_decode_error", "无效的JSON响应")

    def __post(self, url: str, **kwargs) -> Dict[str, Any]:
        DoubanApi._apply_cooldown()
        DoubanApi._ensure_session()
        if DoubanApi._session is None:
            return self._make_error_dict("session_not_initialized", "Session未初始化")
        req_url = DoubanApi._api_url + url
        data_payload: Dict[str, Any] = {'apikey': DoubanApi._api_key2, **kwargs}
        if '_ts' in data_payload:
            data_payload.pop('_ts')
        headers = {'User-Agent': choice(DoubanApi._user_agents), "Content-Type": "application/x-www-form-urlencoded; charset=utf-8"}
        if DoubanApi._user_cookie:
            headers['Cookie'] = DoubanApi._user_cookie
        resp = None
        try:
            resp = DoubanApi._session.post(req_url, data=data_payload, headers=headers, timeout=DoubanApi._default_timeout)
            resp.raise_for_status()
            response_json = resp.json()
            if response_json.get("code") == 1080:
                msg = response_json.get('msg', "豆瓣API速率限制")
                logger.warning("  ➜ POST触发豆瓣速率限制: %s", msg)
                return self._make_error_dict("rate_limit", msg, response_json)
            return response_json
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                logger.warning("  ➜ 请求的资源未找到 (404 Not Found)，URL: %s", req_url)
                return self._make_error_dict("movie_not_found", f"IMDb ID 在豆瓣中未找到")
            msg = str(e)
            if e.response is not None:
                try:
                    error_json = e.response.json()
                    if error_json.get("code") == 1001 or "need_login" in error_json.get("msg", ""):
                        msg = "need_login"
                        logger.error("  ➜ 豆瓣API请求失败: 需要登录。请在设置中配置有效的豆瓣Cookie。")
                    else:
                        msg = error_json.get("msg", str(e))
                except json.JSONDecodeError:
                    msg = f"{str(e)} (响应非JSON: {e.response.text[:100]})"
            logger.error("HTTP error on POST %s: %s", req_url, msg, exc_info=True)
            return self._make_error_dict("http_error", msg, getattr(e.response, 'json', lambda: None)())
        except requests.exceptions.RequestException as e:
            logger.error("Request failed on POST %s: %s", req_url, e, exc_info=True)
            return self._make_error_dict("request_exception", str(e))
        except json.JSONDecodeError as e:
            logger.error("JSONDecodeError on POST %s: %s. Response text: %s", req_url, e, resp.text[:200] if resp else 'N/A', exc_info=True)
            return self._make_error_dict("json_decode_error", "无效的JSON响应 (POST)")

    def imdbid(self, imdbid: str, ts: Optional[str] = None) -> Dict[str, Any]:
        params = {}
        if ts:
            params['_ts'] = ts
        return self.__post(DoubanApi._urls["imdbid"] % imdbid, **params)

    def search(self, keyword: str, start: int = 0, count: int = 20, ts: Optional[str] = None) -> Dict[str, Any]:
        if ts is None:
            ts = datetime.strftime(datetime.now(), '%Y%m%d')
        return self.__invoke(DoubanApi._urls["search"], q=keyword, start=start, count=count, _ts=ts)

    def _get_subject_details(self, subject_id: str, subject_type: str = "movie") -> Dict[str, Any]:
        if not subject_id or not str(subject_id).isdigit():
            return self._make_error_dict("invalid_param", f"无效的豆瓣 subject_id: {subject_id}")
        url_key = f"{subject_type}_detail"
        if url_key not in DoubanApi._urls:
            return self._make_error_dict("invalid_param", f"未知的 subject_type for detail: {subject_type}")
        detail_url = DoubanApi._urls[url_key] + subject_id
        logger.info("  ➜ 通过豆瓣ID获取详情: %s", detail_url)
        details = self.__invoke(detail_url)
        if details.get("error"):
            logger.warning("  ➜ 获取豆瓣ID %s (%s) 详情失败: %s", subject_id, subject_type, details.get('message'))
        return details

    def match_info(self, name: str, imdbid: Optional[str] = None, mtype: Optional[str] = None,
                   year: Optional[str] = None, season: Optional[int] = None) -> Dict[str, Any]:
        if imdbid and imdbid.strip().startswith("tt"):
            actual_imdbid = imdbid.strip()
            logger.debug("  ➜ 尝试通过IMDBID %s (使用统一接口) 查询豆瓣信息...", actual_imdbid)

            result_from_imdb = self.imdbid(actual_imdbid)

            if result_from_imdb.get("error"):
                logger.warning("  ➜ IMDBID %s 查询失败: %s", actual_imdbid, result_from_imdb.get('message'))
            elif result_from_imdb.get("id"):
                douban_id_url = str(result_from_imdb.get("id"))
                match = re.search(r'/(movie|tv)/(\d+)/?$', douban_id_url)
                if match:
                    _, actual_douban_id = match.groups()

                    final_mtype = 'tv' if mtype and mtype.lower() in ['series', 'tv'] else 'movie'

                    logger.debug("  ➜ IMDBID '%s' -> 豆瓣ID: %s。将使用传入的类型: '%s'", actual_imdbid, actual_douban_id, final_mtype)

                    title = result_from_imdb.get("title", result_from_imdb.get("alt_title", name))
                    original_title = result_from_imdb.get("original_title")
                    year_from_api = str(result_from_imdb.get("year", "")).strip()

                    return {"id": actual_douban_id, "title": title, "original_title": original_title,
                            "year": year_from_api or year, "type": final_mtype, "source": "imdb_lookup"}
                else:
                    logger.warning("  ➜ IMDBID %s 查询到的豆瓣ID URL '%s' 无法解析。", actual_imdbid, douban_id_url)
            else:
                logger.warning("  ➜ IMDBID %s 查询结果无效或无ID。", actual_imdbid)

        logger.info("  ➜ IMDb查询失败或未提供ID，回退到名称搜索: '%s'", name)
        return self._search_by_name_for_match_info(name, mtype, year, season)

    def _search_by_name_for_match_info(self, name: str, mtype: Optional[str],
                                       year: Optional[str] = None, season: Optional[int] = None) -> Dict[str, Any]:
        logger.info("  ➜ 开始使用名称 '%s'%s%s 匹配豆瓣信息 ...", name,
                    (', 年份: ' + year) if year else '',
                    (', 类型: ' + mtype) if mtype else '')

        normalized_mtype = mtype
        if mtype and mtype.lower() == 'series':
            normalized_mtype = 'tv'
            logger.debug("  ➜ 将传入的媒体类型 'Series' 规范化为 'tv'。")

        effective_year_in_query = year
        if year:
            year_pattern = re.compile(r'\((\d{4})\)')
            match = year_pattern.search(name)
            if match and match.group(1) == year:
                effective_year_in_query = ''
                logger.debug("  ➜ 名称 '%s' 中已包含年份 '%s'，搜索查询中将不重复年份。", name, year)

        search_query = f"{name} {effective_year_in_query or ''}".strip()

        if not search_query:
            return self._make_error_dict("invalid_param", "搜索关键词为空")

        logger.debug("  ➜ 最终豆瓣搜索查询: '%s'", search_query)
        search_result = self.search(search_query)
        logger.debug("  ➜ 名称搜索 '%s' 原始结果: %s", search_query, search_result)

        if search_result.get("error"):
            logger.warning("  ➜ 豆瓣名称搜索 '%s' 返回错误: %s", search_query, search_result.get('message'))
            return search_result

        items = search_result.get("items")
        if not items or not isinstance(items, list):
            logger.warning("  ➜ 豆瓣名称搜索 '%s' 未找到条目或格式错误。", search_query)
            return self._make_error_dict("no_items_found", f"豆瓣名称搜索 '{search_query}' 未找到条目或格式错误。")

        candidates = []
        exact_match = None
        for item_obj in items:
            if not isinstance(item_obj, dict):
                logger.debug("  ➜ 跳过无效的搜索结果条目 (非字典): %s", item_obj)
                continue
            target = item_obj.get("target", {})
            if not isinstance(target, dict):
                logger.debug("  ➜ 跳过无效的搜索结果条目 (target 非字典): %s", item_obj)
                continue

            api_item_type = item_obj.get("target_type")
            if api_item_type not in ["movie", "tv"]:
                logger.debug("  ➜ 跳过不相关的类型 '%s' for item: %s", api_item_type, target.get('title'))
                continue

            if normalized_mtype and normalized_mtype != api_item_type:
                logger.debug("  ➜ 跳过类型不匹配的条目。请求类型: '%s', API类型: '%s' for item: %s",
                             normalized_mtype, api_item_type, target.get('title'))
                continue

            title_from_api = target.get("title")
            douban_id = str(target.get("id", "")).strip()

            if not isinstance(title_from_api, str) or not title_from_api.strip() or not douban_id.isdigit():
                logger.debug("_search_by_name_for_match_info: 跳过无效条目，title='%s' (类型: %s), douban_id='%s'",
                             title_from_api, type(title_from_api).__name__, douban_id)
                continue

            title_str = title_from_api
            api_item_year = str(target.get("year", "")).strip()

            year_match_status = "N/A"
            if not year:
                year_match = True
                year_match_status = "请求未提供年份，默认匹配"
            elif api_item_year and api_item_year.isdigit():
                try:
                    year_diff = abs(int(api_item_year) - int(year))
                    year_match = year_diff <= 1
                    year_match_status = f"请求年份: {year}, API年份: {api_item_year}, 差异: {year_diff}, 匹配: {year_match}"
                except ValueError:
                    year_match = False
                    year_match_status = f"API年份 '{api_item_year}' 或请求年份 '{year}' 无效，无法比较。"
            else:
                year_match = False
                year_match_status = f"API未提供年份或年份无效 ('{api_item_year}')。"

            logger.debug("处理条目 '%s' (%s, ID: %s, 类型: %s). 年份匹配状态: %s",
                         title_str, api_item_year, douban_id, api_item_type, year_match_status)

            if year_match:
                candidate_info = {"id": douban_id, "title": title_str, "original_title": target.get("original_title"),
                                  "year": api_item_year, "type": api_item_type, "source": "name_search_candidate"}

                name_to_compare = str(name).strip()

                if title_str.lower().strip() == name_to_compare.lower() and (not year or api_item_year == year):
                    exact_match = candidate_info
                    exact_match["source"] = "name_search_exact"
                    logger.debug("  ➜ 找到精确匹配: %s", exact_match)
                    break
                candidates.append(candidate_info)
                logger.debug("  ➜ 添加候选匹配项: %s", candidate_info)

        if exact_match:
            return exact_match
        if candidates:
            if len(candidates) == 1:
                logger.info("  ➜ 找到唯一候选匹配项: %s", candidates[0])
                return candidates[0]

            if year:
                year_exact_candidates = [c for c in candidates if c.get("year") == year]
                if year_exact_candidates:
                    logger.info("  ➜ 找到多个年份精确匹配的候选，返回第一个。Candidates: %s", year_exact_candidates)
                    return year_exact_candidates[0]

            logger.info("  ➜ 找到多个候选匹配项 for '%s', 返回第一个。 Candidates: %s", name, candidates)
            return candidates[0]

        logger.warning("  ➜ 豆瓣名称搜索未能为 '%s' 找到合适的匹配项。", name)
        return self._make_error_dict("no_suitable_match", f"豆瓣名称搜索未能为 '{name}' 找到合适的匹配项。")

    def get_acting(self, name: str, imdbid: Optional[str] = None, mtype: Optional[str] = None,
                   year: Optional[str] = None, season: Optional[int] = None,
                   douban_id_override: Optional[str] = None) -> Dict[str, Any]:
        douban_subject_id = None
        final_mtype = mtype

        if douban_id_override and str(douban_id_override).isdigit():
            douban_subject_id = str(douban_id_override)
            logger.info("  ➜ 使用提供的豆瓣ID覆盖: %s", douban_subject_id)
            if not final_mtype:
                details_movie = self._get_subject_details(douban_subject_id, "movie")
                if details_movie and not details_movie.get("error") and details_movie.get("type"):
                    final_mtype = details_movie.get("type")
                else:
                    details_tv = self._get_subject_details(douban_subject_id, "tv")
                    if details_tv and not details_tv.get("error") and details_tv.get("type"):
                        final_mtype = details_tv.get("type")
                if final_mtype:
                    logger.debug("推断豆瓣ID %s 类型为: %s", douban_subject_id, final_mtype)
                else:
                    return self._make_error_dict("type_inference_failed", f"无法为豆瓣ID '{douban_subject_id}' 推断媒体类型", {"cast": []})
        else:
            match_info_result = self.match_info(name=name, imdbid=imdbid, mtype=mtype, year=year, season=season)
            if match_info_result.get("error"):
                return {**match_info_result, "cast": []}
            if match_info_result.get("id") and str(match_info_result.get("id")).isdigit():
                douban_subject_id = str(match_info_result.get("id"))
                if not final_mtype and match_info_result.get("type"):
                    final_mtype = match_info_result.get("type")
            else:
                return self._make_error_dict("no_match_id_found", f"未能为 '{name}' 匹配到豆瓣ID", {"cast": []})

        if not douban_subject_id or not final_mtype:
            return self._make_error_dict("missing_id_or_type", f"获取演职员信息前豆瓣ID或类型无效 (ID: {douban_subject_id}, Type: {final_mtype})", {"cast": []})

        logger.info("  ➜ 获取豆瓣ID '%s' (类型: %s) 的演职员信息...", douban_subject_id, final_mtype)
        response = None
        if final_mtype == "tv":
            response = self.tv_celebrities(douban_subject_id)
        elif final_mtype == "movie":
            response = self.movie_celebrities(douban_subject_id)
        else:
            return self._make_error_dict("unknown_media_type", f"未知的媒体类型 '{final_mtype}'", {"cast": []})

        if not response or response.get("error"):
            err_msg = response.get("message", "获取演职员信息失败") if response else "获取演职员信息无响应"
            return self._make_error_dict(response.get("error", "api_error") if response else "no_response", err_msg, {"cast": []})

        data: Dict[str, List[Dict[str, Any]]] = {"cast": []}
        actors_list = response.get("celebrities", response.get("actors", []))
        if actors_list is None:
            actors_list = []

        for idx, item in enumerate(actors_list):
            if not isinstance(item, dict):
                continue
            character_str_raw = item.get("character", "")
            if not character_str_raw and item.get("attrs", {}).get("role"):
                roles = item.get("attrs").get("role")
                if isinstance(roles, list) and roles:
                    character_str_raw = " / ".join(r for r in roles if isinstance(r, str))

            cleaned_char_name = _clean_character_name(character_str_raw)
            actor_id_str = str(item.get("id", "")).strip()
            actor_id_int = int(actor_id_str) if actor_id_str.isdigit() else None
            profile_img_obj = item.get("avatar", item.get("cover_url"))
            profile_path_val = None
            if isinstance(profile_img_obj, dict):
                profile_path_val = profile_img_obj.get("large", profile_img_obj.get("normal"))
            elif isinstance(profile_img_obj, str):
                profile_path_val = profile_img_obj

            data["cast"].append({
                "name": item.get("name"), "character": cleaned_char_name, "id": actor_id_int,
                "original_name": item.get("latin_name", item.get("name_en")),
                "profile_path": profile_path_val, "order": item.get("rank", idx)
            })
        return data

    def movie_celebrities(self, subject_id: str) -> Dict[str, Any]:
        return self.__invoke(DoubanApi._urls["movie_celebrities"] % subject_id)

    def tv_celebrities(self, subject_id: str) -> Dict[str, Any]:
        return self.__invoke(DoubanApi._urls["tv_celebrities"] % subject_id)

    def close(self):
        with DoubanApi._session_lock:
            if DoubanApi._session:
                try:
                    DoubanApi._session.close()
                    logger.debug("DoubanApi requests.Session 已关闭。")
                except Exception as e:
                    logger.error("关闭 DoubanApi session 时出错: %s", e)
                finally:
                    DoubanApi._session = None

    # ✨✨✨ 演员详细信息方法 ✨✨✨
    def celebrity_details(self, celebrity_id: str) -> Dict[str, Any]:
        """获取单个名人（演员/导演）的详细信息。

        Frodo API: GET /celebrity/{celebrity_id}

        返回中包含:
          - avatar: {large, normal, small} — 高清头像
          - name, name_en, latin_name — 中英文名
          - born_place — 出生地
          - info — 生平简介
          - gender — 性别
          - birth_date — 出生日期

        Args:
            celebrity_id: 豆瓣名人 ID

        Returns:
            完整的 celebrity 详情字典，失败时包含 "error" 字段。
        """
        if not celebrity_id or not str(celebrity_id).isdigit():
            return self._make_error_dict("invalid_param", f"无效的名人 celebrity_id: {celebrity_id}")

        detail_url = DoubanApi._urls["celebrity_detail"] % celebrity_id
        logger.debug("  ➜ 获取豆瓣演员详情: %s", detail_url)
        details = self.__invoke(detail_url)
        return details

    def get_details_from_douban_link(self, douban_link: str, mtype: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """通过豆瓣链接获取其完整的详情信息字典。"""
        logger.debug("  ➜ 专用函数：尝试从豆瓣链接 '%s' 获取完整详情...", douban_link)
        match = re.search(r'/(?:movie|tv|subject)/(\d+)', douban_link)
        if not match:
            logger.warning("  ➜ 无法从链接中解析出豆瓣ID。")
            return None

        douban_id = match.group(1)

        primary_type = 'tv' if mtype and mtype.lower() in ['series', 'tv'] else 'movie'
        secondary_type = 'movie' if primary_type == 'tv' else 'tv'

        details = self._get_subject_details(douban_id, primary_type)
        if details.get("error"):
            logger.debug("  ➜ 使用主类型 '%s' 获取详情失败，尝试备用类型 '%s'...", primary_type, secondary_type)
            details = self._get_subject_details(douban_id, secondary_type)

        if details.get("error"):
            logger.error("  ➜ 无法获取豆瓣ID '%s' 的详情: %s", douban_id, details.get('message'))
            return None
        return details
