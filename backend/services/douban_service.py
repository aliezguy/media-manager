"""
Douban 演员与角色名中文化服务。

从 Emby ItemId 出发，通过豆瓣抓取演员中文名和角色名，
利用拼音降级匹配对齐 Emby 中的英文/拼音人员数据，最后回写 Emby。
"""

import re
import random
import time
import logging
import requests
from typing import Optional
from urllib.parse import quote, urlencode, urlparse
import hmac
import hashlib
import base64
from datetime import datetime

from bs4 import BeautifulSoup
from pypinyin import lazy_pinyin

from config.settings import load_config
from services.ai_translator import get_translator

logger = logging.getLogger("uvicorn")

# ---------------------------------------------------------------------------
# User-Agent 池 — 基础防爬
# ---------------------------------------------------------------------------
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
]

DOUBAN_SEARCH_URL = "https://www.douban.com/search"
DOUBAN_SUGGEST_URL = "https://movie.douban.com/j/subject_suggest"
DOUBAN_API_KEY = "0ab215a8b1977939201640fa14c66bab"
DOUBAN_IMDB_URL = "https://api.douban.com/v2/movie/imdb/{}"
DOUBAN_FRODO_UA = "api-client/1 com.douban.frodo/7.22.0.beta9(231) Android/23 product/Mate 40 vendor/HUAWEI model/Mate 40 brand/HUAWEI  rom/android  network/wifi  platform/AndroidPad"
DOUBAN_FRODO_BASE = "https://frodo.douban.com/api/v2"
DOUBAN_FRODO_KEY = "0dad551ec0f84ed02907ff5c42e8ec70"
DOUBAN_FRODO_SECRET = "bf7dddc7c9cfe6f7"
DOUBAN_SUBJECT_URL = "https://movie.douban.com/subject/{douban_id}/celebrities"

MAX_ACTORS = 30


class DoubanSinizer:
    """媒体库演员与角色名中文化处理器。"""

    def __init__(self):
        cfg = load_config()
        self.emby_host = cfg.get("emby_host", "").rstrip("/")
        self.emby_api_key = cfg.get("emby_api_key", "")
        self.emby_user_id = cfg.get("emby_user_id", "")
        self.session = requests.Session()
        # 复用系统代理设置（与 requests.get() 行为一致）
        proxy_url = cfg.get("http_proxy") or cfg.get("proxy_url") or ""
        if proxy_url:
            self.session.proxies = {"http": proxy_url, "https": proxy_url}
            logger.info(f"   [Douban] 已配置代理: {proxy_url}")
        else:
            logger.info(f"   [Douban] 未配置代理，使用直连")

    # ------------------------------------------------------------------
    # 公共入口
    # ------------------------------------------------------------------

    def sinicize(self, item_id: str) -> dict:
        """主流程：读取 Emby → 抓取豆瓣 → 匹配 → 回写。

        返回 {"success": bool, "matched": int, "total_actors": int, "details": [...]}
        """
        result = {"success": False, "matched": 0, "total_actors": 0, "details": []}

        if not self.emby_host or not self.emby_api_key:
            logger.error("❌ [Douban中文化] 未配置 emby_host 或 emby_api_key")
            return result

        # 1. 读取 Emby Item
        logger.info(f"📖 [Douban中文化] 正在读取 Emby Item: {item_id}")
        item_data = self._get_emby_item(item_id)
        if not item_data:
            logger.error(f"❌ [Douban中文化] 无法获取 Emby Item: {item_id}")
            return result

        item_name = item_data.get("Name", "未知")
        people = item_data.get("People", []) or []

        # 只处理 Type="Actor" 的人员，截取前 MAX_ACTORS 位
        actors = [p for p in people if p.get("Type") == "Actor"]
        non_actor_people = [p for p in people if p.get("Type") != "Actor"]
        if len(actors) > MAX_ACTORS:
            actors = actors[:MAX_ACTORS]

        if not actors:
            logger.warning(f"⚠️ [Douban中文化] 该 Item 没有 Actor 数据: {item_name}")
            return result

        result["total_actors"] = len(actors)
        logger.info(f"   👥 Emby 中前 {len(actors)} 位演员待处理 (共 {len(people)} 位人员)")

        # 2. 提取 IMDB / TMDB ID
        provider_ids = item_data.get("ProviderIds", {}) or {}
        douban_id = self._find_douban_id(provider_ids, title=item_name)
        if not douban_id:
            logger.warning(f"⚠️ [Douban中文化] 无法从 ProviderIds 找到豆瓣条目: Imdb={provider_ids.get('Imdb')}, Tmdb={provider_ids.get('Tmdb')}")
            return result

        logger.info(f"   🆔 豆瓣条目 ID: {douban_id}")

        # 3. 抓取豆瓣演员列表
        douban_actors = self._fetch_douban_actors(douban_id)
        if not douban_actors:
            logger.warning(f"⚠️ [Douban中文化] 豆瓣演员列表为空")
            return result

        logger.info(f"   🎬 豆瓣抓取到 {len(douban_actors)} 位演员")

        # 4. 匹配并更新
        updated_actors, match_details = self._match_and_update(actors, douban_actors)
        result["details"] = match_details
        result["matched"] = sum(1 for d in match_details if d["matched"])

        # 5. AI 翻译兜底: 翻译仍未汉化的英文角色名（不翻译人名字）
        translator = get_translator()
        if translator.is_available():
            roles_to_translate = []
            for a in updated_actors:
                role = a.get("Role", "")
                # 角色不含中文且不是占位符则需要翻译
                if role and not self._is_chinese(role) and role not in ("演员", "配音", "actor", "actress"):
                    roles_to_translate.append(role)

            if roles_to_translate:
                logger.info(f"   🤖 [AI翻译] 翻译 {len(roles_to_translate)} 个角色名...")
                role_map = translator.translate_roles(roles_to_translate, context=item_name)
                # 常见英文名名单，翻译后如果变成了音译中文（如 Jason→杰森），回退为原名
                common_english_names = {
                    "jason", "linda", "michael", "david", "tom", "jack",
                    "john", "mary", "robert", "james", "william", "emma",
                    "olivia", "sarah", "anna", "lisa", "chris", "mike",
                    "peter", "paul", "george", "henry", "sam", "alex",
                }
                for a in updated_actors:
                    old_role = a.get("Role", "")
                    if old_role in role_map:
                        new_role = role_map[old_role]
                        # 如果原名是常见英文名但 AI 翻译成了中文（音译），保留原名
                        if old_role.lower() in common_english_names and self._is_chinese(new_role):
                            logger.info(f"   🤖 [AI翻译] 角色 {old_role} (常见英文名，保持原样)")
                        else:
                            a["Role"] = new_role
                            logger.info(f"   🤖 [AI翻译] 角色 {old_role} → {new_role}")
        else:
            logger.info(f"   ℹ️ [AI翻译] 未配置 sf_api_key，跳过翻译")

        # 6. 回写 Emby
        all_people = updated_actors + non_actor_people
        write_ok = self._write_back_emby(item_id, item_data, all_people)
        result["success"] = write_ok

        if write_ok:
            logger.info(f"✅ [Douban中文化] 完成！共匹配 {result['matched']}/{result['total_actors']} 位演员")
        else:
            logger.error(f"❌ [Douban中文化] Emby 回写失败")

        return result

    # ------------------------------------------------------------------
    # 1. Emby 读取
    # ------------------------------------------------------------------

    def _get_emby_item(self, item_id: str) -> Optional[dict]:
        """获取 Emby Item 详情，包含 People 和 ProviderIds。"""
        if self.emby_user_id:
            url = f"{self.emby_host}/emby/Users/{self.emby_user_id}/Items/{item_id}"
        else:
            url = f"{self.emby_host}/emby/Items/{item_id}"
        params = {
            "api_key": self.emby_api_key,
            "Fields": "People,ProviderIds,LockData,LockedFields",
        }
        try:
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            logger.error(f"❌ [Emby读取] HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            logger.error(f"❌ [Emby读取] 请求异常: {e}")
        return None

    # ------------------------------------------------------------------
    # 2. 豆瓣 ID 查找
    # ------------------------------------------------------------------

    def _find_douban_id(self, provider_ids: dict, title: str = "") -> Optional[str]:
        """利用 IMDB ID (优先) / TMDB ID / 标题查找豆瓣条目 ID。"""
        imdb_id = provider_ids.get("Imdb", "").strip()
        tmdb_id = provider_ids.get("Tmdb", "").strip()

        if imdb_id:
            logger.info(f"   🔍 尝试用 IMDB ID 查找豆瓣条目: {imdb_id}")
            douban_id = self._search_douban_by_imdb(imdb_id)
            if douban_id:
                return douban_id

        if tmdb_id:
            logger.info(f"   🔍 尝试用 TMDB ID 查找豆瓣条目: {tmdb_id}")
            douban_id = self._search_douban_by_imdb(f"tmdb{tmdb_id}")
            if douban_id:
                return douban_id
            douban_id = self._search_douban_by_imdb(f"tmdb:{tmdb_id}")
            if douban_id:
                return douban_id

        # 策略 C: 用标题搜索豆瓣
        if title:
            logger.info(f"   🔍 尝试用标题搜索豆瓣: {title}")
            douban_id = self._search_douban_by_imdb(title)
            if douban_id:
                return douban_id

        return None

    def _search_douban_by_imdb(self, query: str) -> Optional[str]:
        """在豆瓣搜索中查找条目 ID。"""
        # 策略 A: Frodo API — POST /v2/movie/imdb/{id} (与参考程序一致)
        if query.lower().startswith("tt"):
            try:
                url = DOUBAN_IMDB_URL.format(query)
                headers = {
                    "User-Agent": DOUBAN_FRODO_UA,
                    "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
                }
                data = {"apikey": DOUBAN_API_KEY}
                logger.info(f"      [Douban] POST {url}")
                resp = self.session.post(url, data=data, headers=headers, timeout=15)
                logger.info(f"      [Douban] POST response: {resp.status_code}")
                if resp is not None and resp.status_code == 200:
                    result = resp.json()
                    logger.info(f"      [Douban] Frodo API response: {str(result)[:200]}")
                    douban_id_url = str(result.get("id", ""))
                    # 参考程序用的正则: /(movie|tv)/(\d+)/?$
                    m = re.search(r"/(?:movie|tv|subject)/(\d+)/?$", douban_id_url)
                    if m:
                        douban_id = m.group(1)
                        logger.info(f"   ✅ 豆瓣 Frodo API 返回 ID: {douban_id}")
                        return douban_id
                    # 如果不是 URL 格式，可能是纯数字
                    if douban_id_url.isdigit():
                        logger.info(f"   ✅ 豆瓣 Frodo API 返回 ID: {douban_id_url}")
                        return douban_id_url
                    # 再试 /subject/ 格式
                    m = re.search(r"/subject/(\d+)", douban_id_url)
                    if m:
                        douban_id = m.group(1)
                        logger.info(f"   ✅ 豆瓣 Frodo API 返回 ID: {douban_id}")
                        return douban_id
                elif resp is not None:
                    logger.warning(f"      [Douban] Frodo API HTTP {resp.status_code}")
                else:
                    logger.warning("      [Douban] Frodo API request failed (no response)")
            except requests.exceptions.ConnectionError as e:
                logger.warning(f"      [Douban] Frodo API 连接失败: {e}")
            except requests.exceptions.Timeout as e:
                logger.warning(f"      [Douban] Frodo API 超时: {e}")
            except Exception as e:
                logger.warning(f"      [Douban] Frodo API 异常: {type(e).__name__}: {e}")

        # 策略 B: 搜索页 HTML 解析
        try:
            params = {"cat": "1002", "q": query}
            logger.info(f"      [Douban] Trying search page with query: {query}")
            resp = self._http_get(DOUBAN_SEARCH_URL, params=params)
            if resp is None:
                logger.warning("      [Douban] Search page request failed (no response)")
                return None
            if resp.status_code != 200:
                logger.warning(f"      [Douban] HTTP {resp.status_code} from search page")
                return None

            # Strategy B1: 尝试从 JavaScript 中提取 sid
            sid_match = re.search(r'sid:\s*(\d+)', resp.text)
            if sid_match:
                douban_id = sid_match.group(1)
                logger.info(f"   ✅ 豆瓣搜索页 sid 返回 ID: {douban_id}")
                return douban_id

            # Strategy B2: HTML 解析 <a> 标签
            soup = BeautifulSoup(resp.text, "lxml")
            result = soup.select_one(".result-list .result .title a, .search-result .item .title a, .result .title a")
            if not result:
                result = soup.select_one("a[href*='/subject/']")
            if not result:
                # Strategy B3: 全页面扫描所有 subject 链接
                for a_tag in soup.select("a[href*='/subject/']"):
                    href = a_tag.get("href", "")
                    m = re.search(r"/subject/(\d+)/", href)
                    if m:
                        douban_id = m.group(1)
                        logger.info(f"   ✅ 豆瓣搜索页链接返回 ID: {douban_id}")
                        return douban_id
                return None

            href = result.get("href", "")
            m = re.search(r"/subject/(\d+)/", href)
            if m:
                douban_id = m.group(1)
                logger.info(f"   ✅ 豆瓣搜索页返回 ID: {douban_id}")
                return douban_id
            return None
        except Exception as e:
            logger.warning(f"   ⚠️ 豆瓣搜索解析失败: {e}")
            return None

    def _fetch_douban_actors(self, douban_id: str) -> list[dict]:
        """抓取豆瓣演职员数据，优先用 Frodo API。"""
        # 策略 A: Frodo API (与参考程序一致)
        actors = self._fetch_actors_frodo(douban_id)
        if actors:
            return actors[:MAX_ACTORS]

        # 策略 B: 网页抓取 (fallback)
        url = DOUBAN_SUBJECT_URL.format(douban_id=douban_id)
        logger.info(f"   🌐 [fallback] 正在请求豆瓣页面: {url}")
        resp = self._http_get(url)
        if not resp or resp.status_code != 200:
            logger.error(f"❌ [豆瓣抓取] HTTP {resp.status_code if resp else 'N/A'}")
            return []
        soup = BeautifulSoup(resp.text, "lxml")
        actors = self._parse_celebrities_page(soup)
        return actors[:MAX_ACTORS]

    def _fetch_actors_frodo(self, douban_id: str) -> list[dict]:
        """通过 Frodo API 获取演职员信息。"""
        # 先试 movie，再试 tv
        for media_type in ("tv", "movie"):
            try:
                url = f"/{media_type}/{douban_id}/celebrities"
                data = self._frodo_get(url)
                if not data or data.get("error"):
                    continue
                celebs = data.get("celebrities", data.get("actors", []))
                if not celebs:
                    continue
                actors = []
                for item in celebs:
                    if not isinstance(item, dict):
                        continue
                    name = item.get("name", "")
                    if not name:
                        continue
                    # 角色名 — 参考程序用 clean_character_name_static
                    role = item.get("character", "")
                    if not role and item.get("attrs", {}).get("role"):
                        roles = item["attrs"]["role"]
                        if isinstance(roles, list) and roles:
                            role = " / ".join(r for r in roles if isinstance(r, str))
                    role = self._clean_role(role) if role else ""
                    actors.append({"name": name, "role": role})
                if actors:
                    logger.info(f"   ✅ Frodo API 获取 {len(actors)} 位演员 (type={media_type})")
                    return actors
            except Exception as e:
                logger.warning(f"      [Douban] Frodo celebrities {media_type} 异常: {e}")
        return []

    def _frodo_get(self, path: str) -> Optional[dict]:
        """带签名的 Frodo API GET 请求。"""
        url = DOUBAN_FRODO_BASE + path
        ts = datetime.now().strftime("%Y%m%d")
        # 签名: HMAC-SHA1(method&url_path&ts) → base64
        url_path = urlparse(url).path
        raw_sign = "&".join(["GET", quote(url_path, safe=""), ts])
        sig = base64.b64encode(hmac.new(
            DOUBAN_FRODO_SECRET.encode(),
            raw_sign.encode(),
            hashlib.sha1
        ).digest()).decode()
        params = {
            "apiKey": DOUBAN_FRODO_KEY,
            "os_rom": "android",
            "_ts": ts,
            "_sig": sig,
        }
        headers = {"User-Agent": DOUBAN_FRODO_UA}
        try:
            time.sleep(random.uniform(0.3, 0.8))
            logger.info(f"      [Douban] Frodo GET {url}")
            resp = self.session.get(url, params=params, headers=headers, timeout=15)
            logger.info(f"      [Douban] Frodo GET response: {resp.status_code}")
            if resp.status_code == 200:
                return resp.json()
            else:
                logger.warning(f"      [Douban] Frodo GET HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            logger.warning(f"      [Douban] Frodo GET 异常: {type(e).__name__}: {e}")
        return None

    def _parse_celebrities_page(self, soup: BeautifulSoup) -> list[dict]:
        """解析豆瓣演职员页面 HTML，提取演员名和角色名。"""
        result = []

        # 定位"演员"区块
        actor_section = None
        celebrities_div = soup.find("div", id="celebrities")
        if celebrities_div:
            actor_section = celebrities_div
        else:
            for heading in soup.find_all(["h2", "h3", "h4"]):
                if "演员" in heading.get_text():
                    actor_section = heading.find_parent("div") or heading.parent
                    break

        if not actor_section:
            items = soup.select(".celebrity, li.celebrity, .list-wrapper .celebrity")
            if not items:
                items = soup.select("[class*='celebrity']")
            for item in items:
                parsed = self._parse_celebrity_item(item)
                if parsed:
                    result.append(parsed)
            return result

        items = actor_section.select(
            ".celebrity, li.celebrity, .list-wrapper > div, .list-wrapper > a, "
            "ul.celebrity-list li, div.celebrity, .info, .bd"
        )
        if not items:
            items = actor_section.select("li, .item, .list-item, [class*='celebrity']")

        for item in items:
            parsed = self._parse_celebrity_item(item)
            if parsed:
                result.append(parsed)

        return result

    def _parse_celebrity_item(self, item) -> Optional[dict]:
        """从单个演员 DOM 节点中提取中文名和角色名。"""
        name = None
        role = None

        name_el = (
            item.select_one(".name a, .name")
            or item.select_one("a.name")
            or item.select_one("span.name")
            or item.select_one("a[href*='/celebrity/']")
            or item.select_one(".info .name")
        )
        if name_el:
            name = name_el.get_text(strip=True)

        if not name:
            link = item.select_one("a[href*='/celebrity/']")
            if link:
                name = link.get("title") or link.get_text(strip=True)

        role_el = (
            item.select_one(".role")
            or item.select_one("span.role")
            or item.select_one(".roles")
            or item.select_one("dd.roles")
            or item.select_one(".info .role")
        )
        if role_el:
            role = role_el.get_text(strip=True)

        if not role:
            info = item.select_one(".info, .bd")
            if info:
                text = info.get_text(" ", strip=True)
                m = re.search(r"饰[:\s]*(.+?)(?:\.{3,}|$)", text)
                if m:
                    role = m.group(1).strip()

        if not name:
            return None

        if role:
            role = self._clean_role(role)

        return {"name": name, "role": role or ""}

    def _clean_role(self, raw_role: str) -> str:
        """清洗角色名：去除'饰'、'配音'等前缀、括号备注、多余空格。"""
        role = raw_role.strip()
        role = re.sub(r"^(饰[：:\s]*|配音[：:\s]*|声演[：:\s]*)+", "", role)
        role = re.sub(r"\s*[（(][^)）]*[)）]\s*$", "", role)
        role = re.sub(r"\.{3,}\s*$", "", role)
        role = re.sub(r"\s*\.{3,}.*$", "", role)
        role = role.strip()
        return role

    # ------------------------------------------------------------------
    # 4. 多级匹配算法
    # ------------------------------------------------------------------

    def _match_and_update(
        self, emby_actors: list[dict], douban_actors: list[dict]
    ) -> tuple[list[dict], list[dict]]:
        """将 Emby 演员与豆瓣演员进行对齐，返回更新后的列表和匹配详情。"""
        updated = []
        details = []
        douban_names = {da["name"] for da in douban_actors}
        used_douban = set()

        for i, ea in enumerate(emby_actors):
            emby_name = (ea.get("Name") or "").strip()
            emby_role = (ea.get("Role") or "").strip()
            matched_da = None
            match_level = ""

            if not emby_name:
                updated.append(ea)
                details.append({
                    "index": i, "emby_name": "", "douban_name": "",
                    "old_name": "", "new_name": "", "old_role": emby_role, "new_role": "",
                    "matched": False, "reason": "Emby名称为空",
                })
                continue

            # 级别 1: 直接中文名匹配
            if emby_name in douban_names:
                for da in douban_actors:
                    if da["name"] == emby_name and da["name"] not in used_douban:
                        matched_da = da
                        match_level = "直接匹配"
                        break

            # 级别 2: 拼音降级匹配
            if not matched_da:
                emby_key = self._normalize_english_name(emby_name)
                for da in douban_actors:
                    if da["name"] in used_douban:
                        continue
                    py_key = self._to_pinyin_key(da["name"])
                    if emby_key == py_key:
                        matched_da = da
                        match_level = "拼音匹配"
                        break

                # 级别 3: 拼音部分匹配（如 "sunhu" vs "sunhuge"）
                if not matched_da:
                    for da in douban_actors:
                        if da["name"] in used_douban:
                            continue
                        py_key = self._to_pinyin_key(da["name"])
                        if len(emby_key) >= 4 and len(py_key) >= 4:
                            if emby_key in py_key or py_key in emby_key:
                                matched_da = da
                                match_level = "拼音部分匹配"
                                break

            # 构建更新后的条目
            if matched_da:
                used_douban.add(matched_da["name"])
                new_entry = dict(ea)
                is_chinese = self._is_chinese(emby_name)
                old_name = emby_name

                if not is_chinese:
                    new_entry["Name"] = matched_da["name"]

                douban_role = matched_da["role"]
                # 豆瓣返回"演员"/"配音"等占位符时不覆盖 Emby 原有角色名
                if douban_role and douban_role not in ("演员", "配音", "actor", "actress"):
                    new_entry["Role"] = douban_role

                updated.append(new_entry)

                log_msg = (
                    f"   ✅ [{match_level}] {old_name}"
                    + (f" → {matched_da['name']}" if not is_chinese else " (已是中文)")
                    + (f" | 角色: {emby_role or '(无)'} → {douban_role}" if douban_role and douban_role not in ('演员','配音','actor','actress') else (f" | 角色: {emby_role or '(无)'} (豆瓣返回'{douban_role}'已跳过)" if douban_role else ""))
                )
                logger.info(log_msg)

                details.append({
                    "index": i,
                    "emby_name": old_name,
                    "douban_name": matched_da["name"],
                    "old_name": old_name,
                    "new_name": matched_da["name"] if not is_chinese else old_name,
                    "old_role": emby_role,
                    "new_role": matched_da.get("role", emby_role),
                    "matched": True,
                    "level": match_level,
                })
            else:
                updated.append(ea)
                logger.info(f"   ⏭ [未匹配] {emby_name}")
                details.append({
                    "index": i,
                    "emby_name": emby_name,
                    "douban_name": "",
                    "old_name": emby_name,
                    "new_name": emby_name,
                    "old_role": emby_role,
                    "new_role": emby_role,
                    "matched": False,
                    "reason": "未能在豆瓣演员中找到匹配",
                })

        # 追加未匹配的豆瓣演员
        for da in douban_actors:
            if da["name"] in used_douban:
                continue
            new_actor = {
                "Name": da["name"],
                "Role": da["role"],
                "Type": "Actor",
            }
            updated.append(new_actor)
            logger.info(f"   ➕ [新增] {da['name']} | 角色: {da['role']}")
            details.append({
                "index": len(details), "emby_name": "", "douban_name": da["name"],
                "old_name": "", "new_name": da["name"], "old_role": "", "new_role": da["role"],
                "matched": True, "level": "豆瓣新增",
            })

        return updated, details

    # ------------------------------------------------------------------
    # 5. Emby 回写
    # ------------------------------------------------------------------

    def _write_back_emby(
        self, item_id: str, item_data: dict, people: list[dict]
    ) -> bool:
        """将更新后的 People 数组写回 Emby。"""
        if self.emby_user_id:
            url = f"{self.emby_host}/emby/Items/{item_id}"
        else:
            url = f"{self.emby_host}/emby/Items/{item_id}"
        headers = {
            "X-Emby-Token": self.emby_api_key,
            "Content-Type": "application/json",
        }

        update_data = dict(item_data)
        update_data["People"] = people

        if update_data.get("LockData"):
            update_data["LockData"] = False
        if update_data.get("LockedFields"):
            update_data["LockedFields"] = []

        readonly_keys = [
            "MediaSources", "PlayUserData", "SeasonUserData",
            "Container", "Size", "TagItems", "GenreItems", "Studios",
        ]
        for k in readonly_keys:
            update_data.pop(k, None)

        logger.info(f"   💾 正在写回 Emby: {len(people)} 位人员...")

        try:
            resp = requests.post(
                url, json=update_data, headers=headers,
                params={"api_key": self.emby_api_key}, timeout=15,
            )
            if resp.status_code in (200, 204):
                logger.info(f"   ✅ Emby 回写成功")
                return True
            else:
                logger.error(f"   ❌ Emby 回写失败: HTTP {resp.status_code} — {resp.text[:300]}")
                return False
        except Exception as e:
            logger.error(f"   ❌ Emby 回写异常: {e}")
            return False

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def _http_get(
        self, url: str, params: dict = None, is_api: bool = False
    ) -> Optional[requests.Response]:
        """带 UA 轮换和短暂延迟的 HTTP GET。"""
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "application/json" if is_api else "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": "https://www.douban.com/",
            "Connection": "keep-alive",
            "Cache-Control": "max-age=0",
            "Upgrade-Insecure-Requests": "1",
        }
        try:
            time.sleep(random.uniform(0.5, 1.5))
            logger.info(f"      [Douban] GET {url} params={params}")
            resp = self.session.get(url, params=params, headers=headers, timeout=15)
            logger.info(f"      [Douban] GET response: {resp.status_code} len={len(resp.text)}")
            if resp.status_code != 200:
                logger.warning(f"      [Douban] GET body[:200]: {resp.text[:200]}")
            return resp
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"   ⚠️ [Douban] GET 连接失败 [{url}]: {e}")
        except requests.exceptions.Timeout as e:
            logger.warning(f"   ⚠️ [Douban] GET 超时 [{url}]: {e}")
        except Exception as e:
            logger.warning(f"   ⚠️ [Douban] GET 异常 [{url}]: {type(e).__name__}: {e}")
        return None

    @staticmethod
    def _normalize_english_name(name: str) -> str:
        """归一化英文名：去空格、转小写、去掉点号。"""
        return re.sub(r"[^a-z]", "", name.lower())

    @staticmethod
    def _to_pinyin_key(chinese_name: str) -> str:
        """将中文名转为拼音 key (无空格、小写)。"""
        pinyin_list = lazy_pinyin(chinese_name)
        return "".join(pinyin_list).lower()

    @staticmethod
    def _is_chinese(text: str) -> bool:
        """检查字符串是否包含中文字符。"""
        return bool(re.search(r"[\u4e00-\u9fff]", text))


# ---------------------------------------------------------------------------
# 便捷工厂函数
# ---------------------------------------------------------------------------

def sinicize_actors(item_id: str) -> dict:
    """对指定 Emby Item 执行演员中文化。"""
    sinizer = DoubanSinizer()
    return sinizer.sinicize(item_id)
