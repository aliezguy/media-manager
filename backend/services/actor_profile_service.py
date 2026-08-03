"""
全局演员元数据中心 — 超级漏斗 + 本地标准化落盘 + 全维生平 UPSERT。

数据流（严格优先级）:
  L0   本地磁盘智能嗅探 → people/{首字}/{name}[-tmdb-{id}]/folder.png → 直接返回，零网络
  L0.5 Emby 原生头像优先（受 enable_emby_avatar_first 开关控制）
          → ctx["emby_person_id"] + ctx["emby_image_tag"] → 拼接 Emby 直链 → 下载落盘
  L1   豆瓣优先 → context_info["douban_avatar_url"] 或 douban_id 主动 API → 下载落盘
  L2   TMDB 兜底 → Search Person → Get Details → profile_path + 生平 → 下载落盘

下载后按 Kodi/Emby 标准目录结构落盘:
  people/{首字}/{actor_name}-tmdb-{tmdb_id}/folder.png   (有 TMDB ID)
  people/{首字}/{actor_name}-douban-{douban_id}/folder.png (仅豆瓣 ID)
  people/{首字}/{actor_name}/folder.png                    (无任何 ID)

UPSERT actor_profiles (name 主键)，包含:
  local_image_path, image_url, source, tmdb_id, imdb_id, douban_id,
  birth_date, birth_place, overview

受 enable_emby_avatar_first 配置开关控制。开启时 L0.5 优先通过 Emby 原生 API
获取头像 URL；豆瓣/TMDB 均无头像且 Emby 不可用时，local_image_path 与 image_url 留空。

对外接口:
  resolve_actor_profile(name, db, context_info) → dict | None
  fetch_tmdb_person_details(name)                → dict | None
"""

import os
import logging
import time as _time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import requests as _requests
from requests.exceptions import Timeout, ConnectionError
from config.settings import load_config

logger = logging.getLogger("uvicorn")

# ==========================================
# 常量
# ==========================================
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w185"
_FAKE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# 本地 people 目录 (项目根/people/)
_PEOPLE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "people")
)

# ★ 无头像冷却期（天）：如果演员在 TMDB/豆瓣均无头像，本地文件也丢失，
#   数据库记录在冷却期内直接拦截，跳过所有外部网络请求，避免重复击穿。
_NO_AVATAR_COOLDOWN_DAYS = 7

# ==========================================
# 进程级内存缓存
# ==========================================

# TMDB 搜索缓存: {actor_name_lower: person_search_result_dict}
# 同一演员可能在多集/多剧中重复出现，缓存避免重复 API 调用
_tmdb_search_cache: dict = {}

# TMDB 详情缓存: {actor_name_lower: full_details_dict}
_tmdb_detail_cache: dict = {}

# L0 本地嗅探缓存: {actor_name: local_avatar_path | None}
# None 表示已确认该演员在 people/ 中无本地头像
_local_sniff_cache: dict = {}


def _first_char(name: str) -> str:
    """取姓名首字符作为子目录名，非中文/字母统一归入 '_'。"""
    if not name:
        return "_"
    ch = name[0]
    if "一" <= ch <= "鿿":
        return ch
    if ch.isalpha():
        return ch.upper()
    return "_"


def _safe_get_str(d: dict, key: str, default: str = "") -> str:
    """从字典安全取值并 strip，兼容 None 和缺失 key。"""
    val = d.get(key)
    if val is None:
        return default
    return str(val).strip()


# ==========================================
# ★ L0: 本地磁盘智能嗅探 (最高优先级)
# ==========================================

def _find_local_avatar(actor_name: str) -> str | None:
    """在 people/ 目录中智能嗅探演员本地头像，支持多种图片格式。

    搜索策略（按优先级）:
      1. people/{首字}/ 下寻找名称精确匹配或以 "{actor_name}-" 开头的子文件夹
      2. 若找到且包含 folder.{png,jpg,jpeg,webp} → 返回相对路径
      3. 兜底: 直接在 people/ 根目录下搜索（兼容无首字分组的旧数据）

    多匹配时的优先级:
      - 首字分组 > 根目录直放
      - -tmdb- > -douban- > 无后缀
      - 同一目录多格式共存时: 取文件体积最大（最清晰）的一个

    结果会被缓存到 _local_sniff_cache，同一演员跨媒体项重复出现时直接返回。

    Args:
        actor_name: 演员中文名或英文名

    Returns:
        相对路径如 "张/张译-tmdb-12345/folder.png"，未找到返回 None。
    """
    if not actor_name:
        return None

    # ---- 缓存检查 ----
    if actor_name in _local_sniff_cache:
        return _local_sniff_cache[actor_name]

    logger.info("   🔍 [L0] 正在本地 people 目录嗅探: %s", actor_name)

    VALID_EXTS = (".png", ".jpg", ".jpeg", ".webp")
    first_char = actor_name[0]
    candidates: list[tuple[int, str]] = []  # [(priority, relative_path)]

    def _try_add_candidate(base_prio: int, dir_path: str, rel_prefix: str):
        """在 dir_path 中查找任意 folder.{ext}，同目录多格式时取体积最大的。"""
        best_local = None
        best_size = -1
        for ext in VALID_EXTS:
            f_path = os.path.join(dir_path, f"folder{ext}")
            if os.path.isfile(f_path):
                try:
                    sz = os.path.getsize(f_path)
                    if sz > best_size:
                        best_size = sz
                        best_local = f"{rel_prefix}/folder{ext}"
                except OSError:
                    if best_local is None:
                        best_local = f"{rel_prefix}/folder{ext}"
        if best_local:
            candidates.append((base_prio, best_local))

    # ---- 策略 1: people/{first_char}/ 下搜索 ----
    char_dir = os.path.join(_PEOPLE_DIR, first_char)
    if os.path.isdir(char_dir):
        try:
            for entry in os.listdir(char_dir):
                entry_path = os.path.join(char_dir, entry)
                if not os.path.isdir(entry_path):
                    continue
                # 精确匹配 或 前缀匹配 (name-tmdb-xxx / name-douban-xxx)
                if entry == actor_name or entry.startswith(actor_name + "-"):
                    # 优先级: tmdb(0) > douban(1) > 无后缀(2)
                    if "-tmdb-" in entry:
                        prio = 0
                    elif "-douban-" in entry:
                        prio = 1
                    else:
                        prio = 2
                    _try_add_candidate(prio, entry_path, f"{first_char}/{entry}")
        except OSError as e:
            logger.warning("   ⚠ [L0] 读取首字目录失败 %s: %s", char_dir, e)

    # ---- 策略 2: 兜底 — people/ 根目录下搜索（无首字分组） ----
    try:
        for entry in os.listdir(_PEOPLE_DIR):
            entry_path = os.path.join(_PEOPLE_DIR, entry)
            if not os.path.isdir(entry_path):
                continue
            if entry == actor_name or entry.startswith(actor_name + "-"):
                # 根目录直放优先级低于首字分组，基数 +10
                if "-tmdb-" in entry:
                    prio = 10
                elif "-douban-" in entry:
                    prio = 11
                else:
                    prio = 12
                _try_add_candidate(prio, entry_path, entry)
    except OSError as e:
        logger.warning("   ⚠ [L0] 读取 people 根目录失败: %s", e)

    if candidates:
        candidates.sort(key=lambda c: c[0])
        result = candidates[0][1]
        logger.info("   ✅ [L0] 本地嗅探命中: %s → %s", actor_name, result)
        _local_sniff_cache[actor_name] = result
        return result

    logger.info("   ❌ [L0] 本地未命中: %s", actor_name)
    _local_sniff_cache[actor_name] = None
    return None


def _local_file_exists(relative_path: str) -> bool:
    """检查 people/ 下的相对路径文件是否真实存在。

    relative_path 始终为正斜杠格式 (如 "张/张译-tmdb-12345/folder.png")，
    需 split("/") 后在当前 OS 上正确拼接路径。
    """
    if not relative_path:
        return False
    full = os.path.join(_PEOPLE_DIR, *relative_path.split("/"))
    return os.path.isfile(full)


# ==========================================
# L2: TMDB 全维数据抓取（智能优选 + 精准 ID 拦截）
# ==========================================

def _select_best_match(results: list, actor_name: str) -> dict:
    """从 TMDB 搜索结果中智能优选最佳匹配，解决同名冲突。

    绝不允许无脑返回 results[0]。按三级梯队筛选：

    第一梯队（完美匹配）:
      profile_path 不为空 且 known_for_department == 'Acting'
      → 这是真正的演员，优先选择

    第二梯队（有头像即可）:
      profile_path 不为空
      → 至少这个人有头像可用

    第三梯队（无奈兜底）:
      返回 results[0]
      → 所有人都没有头像时，保留第一个结果

    Args:
        results:     TMDB /search/person 返回的 results 数组
        actor_name:  演员名称（用于日志）

    Returns:
        最优匹配的 person dict，或空 dict（results 为空时）
    """
    if not results:
        return {}

    total = len(results)

    # ---- 第一梯队: profile_path + known_for_department == 'Acting' ----
    for r in results:
        profile = (r.get("profile_path") or "").strip()
        department = (r.get("known_for_department") or "").strip()
        if profile and department == "Acting":
            logger.info(
                "   🎯 [TMDB] '%s' 共 %d 个同名结果 → 第一梯队命中 (Acting + 头像) ID=%s",
                actor_name, total, r.get("id"),
            )
            return r

    # ---- 第二梯队: 有 profile_path 即可 ----
    for r in results:
        profile = (r.get("profile_path") or "").strip()
        if profile:
            logger.info(
                "   🥈 [TMDB] '%s' 共 %d 个同名结果 → 第二梯队命中 (有头像, dept=%s) ID=%s",
                actor_name, total, r.get("known_department", "?"), r.get("id"),
            )
            return r

    # ---- 第三梯队: 兜底返回第一个 ----
    logger.warning(
        "   🥉 [TMDB] '%s' 共 %d 个同名结果 → 第三梯队兜底 (全员无头像) ID=%s",
        actor_name, total, results[0].get("id"),
    )
    return results[0]


def _tmdb_request(url: str, params: dict, timeout: int = 20, max_retries: int = 2):
    """TMDB HTTP GET — 带重试 + 延长超时。

    网络波动（ReadTimeout/ConnectionError）时最多重试 max_retries 次，
    每次退避 2 秒。3 次全部失败才返回 None。

    Args:
        url:         请求 URL
        params:      query string 参数
        timeout:     超时秒数（默认 20 秒，是旧版 10 秒的 2 倍）
        max_retries: 额外重试次数（总共 1 + max_retries 次尝试）

    Returns:
        requests.Response 对象，彻底失败返回 None。
    """
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            resp = _requests.get(url, params=params, timeout=timeout)
            return resp
        except (Timeout, ConnectionError, _requests.exceptions.SSLError) as e:
            last_error = e
            if attempt < max_retries:
                logger.warning(
                    "   ⚠ [TMDB] 网络异常 (第 %d/%d 次尝试) 2s 后重试: %s — %s",
                    attempt + 1, max_retries + 1, type(e).__name__, e,
                )
                _time.sleep(2)
        except _requests.exceptions.RequestException as e:
            # 其他网络异常（非超时/连接）不重试，直接失败
            logger.error(
                "   ❌ [TMDB] 网络异常 (%s): %s",
                type(e).__name__, e,
            )
            return None

    logger.error(
        "   ❌ [TMDB] 网络请求彻底失败（%d 次尝试耗尽）: %s",
        max_retries + 1, last_error,
    )
    return None


def _search_tmdb_person(actor_name: str) -> dict:
    """TMDB Search Person — 智能优选，带内存缓存。

    使用 _select_best_match 三级梯队筛选，避免同名编剧/导演抢占演员位置。
    同一演员跨媒体项重复出现时直接返回缓存结果。
    """
    cache_key = actor_name.lower().strip()
    if cache_key in _tmdb_search_cache:
        cached = _tmdb_search_cache[cache_key]
        if cached:
            logger.info("   🔄 [TMDB] 搜索缓存命中: %s", actor_name)
        return cached

    cfg = load_config()
    api_key = cfg.get("tmdb_api_key", "")
    if not api_key:
        _tmdb_search_cache[cache_key] = {}
        return {}

    base_url = cfg.get("tmdb_base_url", "") or "https://api.tmdb.org/3"
    try:
        logger.info("   🌐 [TMDB] 搜索演员: %s", actor_name)
        resp = _tmdb_request(
            f"{base_url}/search/person",
            params={"api_key": api_key, "query": actor_name, "language": "zh-CN"},
        )
        if resp is None:
            # 网络彻底不可达（重试耗尽），_tmdb_request 已打印错误日志
            _tmdb_search_cache[cache_key] = {}
            return {}
        if resp.status_code != 200:
            logger.warning("   ⚠ [TMDB] 搜索 HTTP %d: %s", resp.status_code, actor_name)
            _tmdb_search_cache[cache_key] = {}
            return {}
        data = resp.json()
        results = data.get("results", [])
        result = _select_best_match(results, actor_name)
        _tmdb_search_cache[cache_key] = result
        return result
    except _requests.exceptions.RequestException as e:
        # ★ 网络层异常（ProxyError, ConnectionError, SSLError, Timeout 等）
        logger.error(
            "   ❌ [TMDB] 搜索网络异常 (%s): %s — %s",
            type(e).__name__, actor_name, e,
        )
        _tmdb_search_cache[cache_key] = {}
        return {}
    except Exception:
        logger.error(
            "   ❌ [TMDB] 搜索异常: %s\n%s",
            actor_name, traceback.format_exc(),
        )
        _tmdb_search_cache[cache_key] = {}
        return {}


def _fetch_person_by_tmdb_id(tmdb_id: str, actor_name: str = "") -> dict | None:
    """通过已知的 TMDB Person ID 直接获取全维详情，跳过名字搜索。

    这是最高优先级的路径 — 当 Emby 提供了演员的 ProviderIds.Tmdb 时，
    完全绕过名字搜索，直接定位到正确的演员，100% 避免同名冲突。

    Args:
        tmdb_id:    TMDB 人物 ID
        actor_name: 演员名称（仅用于日志和缓存 key）

    Returns:
        同 fetch_tmdb_person_details 的返回格式，失败返回 None。
    """
    cfg = load_config()
    api_key = cfg.get("tmdb_api_key", "")
    if not api_key:
        return None

    base_url = cfg.get("tmdb_base_url", "") or "https://api.tmdb.org/3"

    try:
        logger.info(
            "   🎯 [TMDB] 精准 ID 拦截: %s → 直接请求 person/%s",
            actor_name or f"id={tmdb_id}", tmdb_id,
        )
        detail_resp = _tmdb_request(
            f"{base_url}/person/{tmdb_id}",
            params={
                "api_key": api_key,
                "language": "zh-CN",
                "append_to_response": "external_ids",
            },
        )
        if detail_resp is None:
            return None
        if detail_resp.status_code != 200:
            logger.warning(
                "   ⚠ [TMDB] 精准 ID 请求 HTTP %d (person_id=%s)",
                detail_resp.status_code, tmdb_id,
            )
            return None

        detail = detail_resp.json()

        profile_path = (detail.get("profile_path") or "").strip()

        # 提取 IMDb ID
        imdb_id = ""
        ext_ids = detail.get("external_ids") or {}
        if isinstance(ext_ids, dict):
            imdb_id = (ext_ids.get("imdb_id") or "").strip()

        result = {
            "tmdb_id": str(tmdb_id),
            "profile_path": profile_path,
            "image_url": f"{TMDB_IMAGE_BASE}{profile_path}" if profile_path else "",
            "birth_date": (detail.get("birthday") or "").strip(),
            "birth_place": (detail.get("place_of_birth") or "").strip(),
            "overview": (detail.get("biography") or "").strip(),
            "imdb_id": imdb_id,
        }

        logger.info(
            "   🎯 [TMDB] 精准 ID 命中: %s (tmdb=%s, imdb=%s, has_avatar=%s)",
            actor_name or f"id={tmdb_id}",
            result["tmdb_id"],
            result["imdb_id"] or "-",
            bool(profile_path),
        )
        return result

    except _requests.exceptions.RequestException as e:
        # ★ 网络层异常（ProxyError, ConnectionError, SSLError, Timeout 等）
        logger.error(
            "   ❌ [TMDB] 精准 ID 网络异常 (%s): %s (id=%s) — %s",
            type(e).__name__, actor_name, tmdb_id, e,
        )
        return None
    except Exception:
        logger.error(
            "   ❌ [TMDB] 精准 ID 请求异常: %s (id=%s)\n%s",
            actor_name, tmdb_id, traceback.format_exc(),
        )
        return None


def fetch_tmdb_person_details(
    actor_name: str,
    tmdb_id: str = "",
) -> dict | None:
    """获取演员 TMDB 全维详情（含生平、生日、出生地、IMDb ID）。

    两条路径（按优先级）:
      1. ★ 精准 ID 拦截: 若 tmdb_id 非空 → 直接 GET /person/{id}
         （完全跳过名字搜索，100% 避免同名冲突）
      2. 名字搜索:   GET /search/person（智能优选） → GET /person/{id}

    带内存缓存（按 actor_name），同一演员跨媒体项重复出现时直接返回。

    Args:
        actor_name: 演员名称
        tmdb_id:    已知的 TMDB Person ID（来自 Emby ProviderIds），可选

    Returns:
        {
            "tmdb_id": str, "profile_path": str, "image_url": str,
            "birth_date": str, "birth_place": str, "overview": str,
            "imdb_id": str,
        }
        失败返回 None。
    """
    cache_key = actor_name.lower().strip()
    if cache_key in _tmdb_detail_cache:
        cached = _tmdb_detail_cache[cache_key]
        if cached:
            logger.info("   🔄 [TMDB] 详情缓存命中: %s", actor_name)
        return cached

    cfg = load_config()
    api_key = cfg.get("tmdb_api_key", "")
    if not api_key:
        _tmdb_detail_cache[cache_key] = None
        return None

    base_url = cfg.get("tmdb_base_url", "") or "https://api.tmdb.org/3"

    try:
        # ================================================================
        # ★ 精准 ID 拦截: 如果已知 TMDB ID，直接请求详情，跳过搜索
        # ================================================================
        if tmdb_id:
            result = _fetch_person_by_tmdb_id(tmdb_id, actor_name)
            if result:
                _tmdb_detail_cache[cache_key] = result
                return result
            # 精准 ID 失败时继续走名字搜索（可能是 ID 过期或错误）
            logger.warning(
                "   ⚠ [TMDB] 精准 ID %s 失败，降级为名字搜索: %s",
                tmdb_id, actor_name,
            )

        # ================================================================
        # 名字搜索路径（智能优选）
        # ================================================================
        person = _search_tmdb_person(actor_name)
        if not person:
            logger.info("   🔍 [TMDB] 搜索 '%s' 无结果", actor_name)
            _tmdb_detail_cache[cache_key] = None
            return None

        person_id = person.get("id")
        if not person_id:
            _tmdb_detail_cache[cache_key] = None
            return None

        profile_path = (person.get("profile_path") or "").strip()

        # Step 2: 获取详情 + external_ids
        logger.info("   🌐 [TMDB] 获取详情: %s (person_id=%s)", actor_name, person_id)
        detail_resp = _tmdb_request(
            f"{base_url}/person/{person_id}",
            params={
                "api_key": api_key,
                "language": "zh-CN",
                "append_to_response": "external_ids",
            },
        )
        if detail_resp is None:
            _tmdb_detail_cache[cache_key] = None
            return None
        if detail_resp.status_code != 200:
            logger.warning(
                "   ⚠ [TMDB] 详情 HTTP %d (person_id=%s)",
                detail_resp.status_code, person_id,
            )
            _tmdb_detail_cache[cache_key] = None
            return None

        detail = detail_resp.json()

        # 提取 IMDb ID
        imdb_id = ""
        ext_ids = detail.get("external_ids") or {}
        if isinstance(ext_ids, dict):
            imdb_id = (ext_ids.get("imdb_id") or "").strip()

        result = {
            "tmdb_id": str(person_id),
            "profile_path": profile_path,
            "image_url": f"{TMDB_IMAGE_BASE}{profile_path}" if profile_path else "",
            "birth_date": (detail.get("birthday") or "").strip(),
            "birth_place": (detail.get("place_of_birth") or "").strip(),
            "overview": (detail.get("biography") or "").strip(),
            "imdb_id": imdb_id,
        }

        logger.info(
            "   🖼️ [TMDB] 全维数据: %s (tmdb=%s, imdb=%s)",
            actor_name, result["tmdb_id"], result["imdb_id"] or "-",
        )
        _tmdb_detail_cache[cache_key] = result
        return result

    except _requests.exceptions.RequestException as e:
        # ★ 网络层异常（ProxyError, ConnectionError, SSLError, Timeout 等）
        logger.error(
            "   ❌ [TMDB] 详情网络异常 (%s): %s — %s",
            type(e).__name__, actor_name, e,
        )
        _tmdb_detail_cache[cache_key] = None
        return None
    except Exception:
        logger.error(
            "   ❌ [TMDB] 详情异常: %s\n%s",
            actor_name, traceback.format_exc(),
        )
        _tmdb_detail_cache[cache_key] = None
        return None


# ==========================================
# 图片下载器
# ==========================================

# 有效图片魔数签名 — 下载守卫与清洗脚本共用的唯一判据。
# 只认魔数，不依赖 Content-Type 头（CDN 可能省略或谎报）。
_IMAGE_MAGIC_CHECKS = (
    (b"\xff\xd8\xff", "jpeg"),               # JFIF/EXIF
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"RIFF", "webp"),                        # 需二次验 RIFF....WEBP
    (b"GIF8", "gif"),
)


def is_image_content(content: bytes) -> bool:
    """校验响应体是否确为真实图片。

    根治「CDN 反爬返回 HTML 错误页却被落盘成 folder.jpg」的脏数据源头：
    反爬页通常是 ~1KB 的 HTML 文本，魔数校验会直接判负，拒绝落盘。
    """
    if not content or len(content) < 4:
        return False
    for magic, _name in _IMAGE_MAGIC_CHECKS:
        if content.startswith(magic):
            if magic == b"RIFF":
                return content[8:12] == b"WEBP"
            return True
    return False


def _download_image(url: str, save_path: str, connect_timeout: float = 10.0, read_timeout: float = 30.0) -> bool:
    """下载图片到本地，带伪装 UA 和自定义超时保护。

    所有网络请求均强制 timeout，绝对不允许无限等待。

    Args:
        url:             图片外链
        save_path:       本地绝对路径
        connect_timeout: TCP 连接超时秒数（默认 10.0，L0.5 熔断用 2.0）
        read_timeout:    读取超时秒数（默认 30.0，L0.5 熔断用 3.0）

    Returns:
        True 当下载内容经魔数校验确认为真实图片且成功写入。
        False 当网络失败 / 非 200 / 空内容 / 非图片内容（如 CDN 反爬 HTML 页）。
    """
    if not url:
        return False

    # 确保父目录存在
    os.makedirs(os.path.dirname(save_path), exist_ok=True)

    # 防盗链 Headers: 豆瓣(doubanio.com) 需要 Referer 才能跨过 403
    _DOWNLOAD_HEADERS = {
        "User-Agent": _FAKE_UA,
        "Referer": "https://movie.douban.com/",
    }

    try:
        # 尝试 httpx，不可用时降级为 requests
        try:
            import httpx
            with httpx.Client(
                headers=_DOWNLOAD_HEADERS,
                timeout=httpx.Timeout(connect_timeout, read=read_timeout),
                follow_redirects=True,
            ) as client:
                resp = client.get(url)
                content = resp.content
                if (
                    resp.status_code == 200
                    and len(content) > 0
                    and is_image_content(content)   # ★ 魔数守卫：拒绝 HTML 冒充
                ):
                    with open(save_path, "wb") as f:
                        f.write(content)
                    logger.info("   💾 [Profile] 下载成功 (httpx): %s", save_path)
                    return True
                else:
                    logger.warning(
                        "   ⚠ [Profile] httpx 下载失败 HTTP %d / 非图片内容 %d bytes: %s",
                        resp.status_code, len(content), url[:80],
                    )
        except ImportError:
            pass
        except Exception:
            logger.warning(
                "   ⚠ [Profile] httpx 下载异常，降级 requests: %s",
                traceback.format_exc(),
            )

        # fallback: requests（强制 timeout + Referer 防盗链）
        resp = _requests.get(
            url,
            headers=_DOWNLOAD_HEADERS,
            timeout=(connect_timeout, read_timeout),  # (connect_timeout, read_timeout)
        )
        content = resp.content
        if (
            resp.status_code == 200
            and len(content) > 0
            and is_image_content(content)   # ★ 魔数守卫：拒绝 HTML 冒充
        ):
            with open(save_path, "wb") as f:
                f.write(content)
            logger.info("   💾 [Profile] 下载成功 (requests): %s", save_path)
            return True

        logger.warning(
            "   ⚠ [Profile] 下载失败 HTTP %d / 非图片内容 %d bytes: %s",
            resp.status_code, len(content), url[:80],
        )
        return False

    except Exception:
        logger.error(
            "   ❌ [Profile] 下载异常: %s\n%s",
            url[:80], traceback.format_exc(),
        )
        return False


# ==========================================
# 标准化落盘路径构造
# ==========================================

def _build_standard_path(
    actor_name: str,
    tmdb_id: str = "",
    douban_id: str = "",
) -> tuple[str, str]:
    """按 Kodi/Emby 标准构造本地存储路径。

    目录命名规则:
      - 有 TMDB ID:  {actor_name}-tmdb-{tmdb_id}
      - 仅豆瓣 ID:   {actor_name}-douban-{douban_id}
      - 无任何 ID:   {actor_name}

    Args:
        actor_name: 演员名
        tmdb_id:    TMDB 人物 ID
        douban_id:  豆瓣人物 ID

    Returns:
        (relative_path, absolute_dir) 元组:
          relative_path: "首字/目录名/folder.png" (正斜杠)
          absolute_dir:  /absolute/path/to/people/首字/目录名/
    """
    first_char = actor_name[0]

    if tmdb_id:
        dir_name = f"{actor_name}-tmdb-{tmdb_id}"
    elif douban_id:
        dir_name = f"{actor_name}-douban-{douban_id}"
    else:
        dir_name = actor_name

    relative = f"{first_char}/{dir_name}/folder.png"
    absolute_dir = os.path.join(_PEOPLE_DIR, first_char, dir_name)

    return relative, absolute_dir


# ==========================================
# ★ 超级漏斗主入口 (重构版)
# ==========================================

def resolve_actor_profile(
    actor_name: str,
    db,
    context_info: dict | None = None,
    force_refresh: bool = False,
) -> dict | None:
    """超级漏斗：L0 本地 → L0.5 Emby → L1 豆瓣 → L2 TMDB → UPSERT。

    严格按优先级执行:
      L0   — 本地 people/ 磁盘嗅探（最高优先级，命中则零网络请求）
      L0.5 — Emby 原生头像优先（受 enable_emby_avatar_first 开关控制）
      L1   — 豆瓣 avatar 下载（短路，优先）
      L2   — TMDB 搜索 + 详情 + 下载（仅 L1 失败时触发）
      最后 — UPSERT actor_profiles

    Emby 原生头像受 enable_emby_avatar_first 配置手动开关控制，默认关闭。
    所有外部源均无头像时 local_image_path 与 image_url 直接留空。

    Args:
        actor_name:   演员中文名（匹配 actor_profiles.name）
        db:           SQLAlchemy Session（调用者管理 commit）
        context_info: 外部上下文，可包含:
                        - douban_avatar_url: 豆瓣头像直链
                        - douban_id: 豆瓣人物 ID
                        - emby_person_id: Emby 人员 ID（用于 L0.5 拼接头像 URL）
                        - emby_image_tag: Emby 人员头像标签（PrimaryImageTag）

    Returns:
        成功时返回 dict:
            {
                "name": str,
                "local_image_path": str,      # 如 "张/张译-tmdb-12345/folder.png"
                "image_url": str,             # 外部直链兜底
                "local_image_url": str,       # 完整 HTTP URL（需调用方拼接 host）
                "source": str,
                "tmdb_id": str, "imdb_id": str, "douban_celebrity_id": str,
                "birth_date": str, "birth_place": str, "overview": str,
            }
        失败返回 None（无可用图片源且无本地缓存）。
    """
    from models import ActorProfile

    if not actor_name:
        return None

    logger.info("🚀 [Profile] 开始解析演员: %s", actor_name)

    ctx = context_info or {}
    douban_avatar = (ctx.get("douban_avatar_url") or "").strip()
    douban_id = (ctx.get("douban_id") or "").strip()

    # ★ 精准 ID 拦截: 从 Emby ProviderIds 中深挖 douban_id 和 TMDB Person ID
    #   - DoubanCelebrityId / Douban → douban_id（独立刷新不漏豆瓣）
    #   - Tmdb / tmdb → provider_tmdb_id（L2 精准查询）
    #   也可能以 tmdb_id / person_tmdb_id 等字段直接传递。
    provider_tmdb_id = ""
    provider_ids = ctx.get("ProviderIds") or {}
    if isinstance(provider_ids, dict):
        # ★ 核心修复：从 ProviderIds 深挖 douban_id（独立刷新 + 批量修复）
        if not douban_id:
            douban_id = (
                _safe_get_str(provider_ids, "DoubanCelebrityId")
                or _safe_get_str(provider_ids, "Douban")
            )
        provider_tmdb_id = (
            _safe_get_str(provider_ids, "Tmdb")
            or _safe_get_str(provider_ids, "tmdb")
        )
    if not provider_tmdb_id:
        provider_tmdb_id = (
            _safe_get_str(ctx, "tmdb_id")
            or _safe_get_str(ctx, "person_tmdb_id")
        )

    # ================================================================
    # L0: 终极极速本地缓存拦截 (数据库优先 -> 物理硬盘兜底)
    #    force_refresh=True 时跳过 L0 缓存拦截，直接进入 L0.5/L1/L2
    # ================================================================

    # 1. 【极速查询】优先查询数据库 — 始终执行（后续 UPSERT 需要 existing 引用）
    existing = db.query(ActorProfile).filter(ActorProfile.name == actor_name).first()

    if not force_refresh:
        # 2. 【最快命中路径】库里有记录，且记录了本地路径，且物理文件确实存在
        if existing and existing.local_image_path and _local_file_exists(existing.local_image_path):
            logger.info(
                "   🏠 [Profile] L0 数据库极速命中: %s → %s",
                actor_name, existing.local_image_path,
            )
            return {
                "name": existing.name,
                "local_image_path": existing.local_image_path,
                "image_url": existing.image_url or "",
                "local_image_url": "",  # 调用方拼接 host
                "source": existing.source or "local",
                "tmdb_id": existing.tmdb_id or "",
                "imdb_id": existing.imdb_id or "",
                "douban_celebrity_id": existing.douban_celebrity_id or "",
                "birth_date": existing.birth_date or "",
                "birth_place": existing.birth_place or "",
                "overview": existing.overview or "",
            }

        # 3. 【硬盘嗅探兜底】库里没有，或者文件丢失了（兼容用户手动放入 people/ 文件夹的场景）
        local_avatar_path = _find_local_avatar(actor_name)
        if local_avatar_path:
            # 从路径提取 ID
            tmdb_id_from_path = ""
            douban_id_from_path = douban_id
            if "-tmdb-" in local_avatar_path:
                try:
                    tmdb_id_from_path = local_avatar_path.split("/")[-2].split("-tmdb-")[-1]
                except (IndexError, ValueError):
                    pass
            elif "-douban-" in local_avatar_path:
                try:
                    douban_id_from_path = local_avatar_path.split("/")[-2].split("-douban-")[-1]
                except (IndexError, ValueError):
                    pass

            # 更新/创建 DB 记录
            try:
                if existing is None:
                    existing = ActorProfile(name=actor_name)
                    db.add(existing)

                existing.local_image_path = local_avatar_path
                existing.source = existing.source or "local"
                existing.tmdb_id = tmdb_id_from_path or existing.tmdb_id or ""
                existing.douban_celebrity_id = douban_id_from_path or existing.douban_celebrity_id or ""
                existing.update_time = datetime.now()
                db.flush()

                logger.info(
                    "   🏠 [Profile] L0 物理硬盘嗅探命中: %s → %s",
                    actor_name, local_avatar_path,
                )
                return {
                    "name": existing.name,
                    "local_image_path": existing.local_image_path,
                    "image_url": existing.image_url or "",
                    "local_image_url": "",
                    "source": existing.source or "local",
                    "tmdb_id": existing.tmdb_id or "",
                    "imdb_id": existing.imdb_id or "",
                    "douban_celebrity_id": existing.douban_celebrity_id or "",
                    "birth_date": existing.birth_date or "",
                    "birth_place": existing.birth_place or "",
                    "overview": existing.overview or "",
                }
            except Exception:
                logger.error(
                    "   ❌ [Profile] L0 UPSERT 失败: %s\n%s",
                    actor_name, traceback.format_exc(),
                )
                # DB 失败依然返回文件信息
                return {
                    "name": actor_name,
                    "local_image_path": local_avatar_path,
                    "image_url": "",
                    "local_image_url": "",
                    "source": "local",
                    "tmdb_id": tmdb_id_from_path,
                    "imdb_id": "",
                    "douban_celebrity_id": douban_id_from_path,
                    "birth_date": "",
                    "birth_place": "",
                    "overview": "",
                }

        # 4. 【网络拦截保护】硬盘上确实没有，检查是否在防击穿冷却期内
        if existing:
            cooldown_threshold = datetime.now() - timedelta(days=_NO_AVATAR_COOLDOWN_DAYS)
            if existing.update_time and existing.update_time > cooldown_threshold:
                logger.info(
                    "   ⏳ [Profile] 缓存保护期内 (跳过网络请求): %s (上次更新: %s)",
                    actor_name,
                    existing.update_time.strftime("%Y-%m-%d"),
                )
                return {
                    "name": existing.name,
                    "local_image_path": "",
                    "image_url": existing.image_url or "",
                    "local_image_url": "",
                    "source": existing.source or "",
                    "tmdb_id": existing.tmdb_id or "",
                    "imdb_id": existing.imdb_id or "",
                    "douban_celebrity_id": existing.douban_celebrity_id or "",
                    "birth_date": existing.birth_date or "",
                    "birth_place": existing.birth_place or "",
                    "overview": existing.overview or "",
                }

            logger.info(
                "   🔓 [Profile] 冷却期已过 (>%d天)，重新尝试网络请求: %s",
                _NO_AVATAR_COOLDOWN_DAYS, actor_name,
            )
    else:
        logger.info(
            "   ⚡ [Profile] force_refresh=True — 跳过 L0 缓存拦截，强制穿透至 L0.5/L1/L2: %s",
            actor_name,
        )

    # ================================================================
    # 上半场：纯身份与元数据确立（绝不触碰/下载头像）
    # ================================================================
    logger.info("   🌐 [Profile] 本地未命中，准备外部请求: %s", actor_name)

    # 准备 UPSERT 数据容器
    profile_data = {
        "douban_celebrity_id": douban_id,
        "tmdb_id": "",
        "imdb_id": "",
        "birth_date": "",
        "birth_place": "",
        "overview": "",
    }

    download_url = ""
    source = ""
    local_path = ""
    tmdb_avatar_bak = ""  # ★ TMDB 头像备份，仅在上半场暂存，下半场才决定是否使用

    cfg = load_config()

    # ★ 提取豆瓣 Cookie 用于 API 鉴权，避免 need_login 流控
    douban_cookie = (
        cfg.get("douban_cookie")
        or cfg.get("DOUBAN_COOKIE")
        or os.getenv("DOUBAN_COOKIE", "")
    )
    if douban_cookie:
        logger.info(
            "   🍪 [Profile] 已加载豆瓣 Cookie (长度=%d): %s...",
            len(douban_cookie), douban_cookie[:40],
        )
    else:
        logger.warning(
            "   ⚠ [Profile] 未配置豆瓣 Cookie，豆瓣 API 可能触发 need_login 流控"
        )

    # ★ 豆瓣 API 总开关（配置项 douban_enabled，封控严重时关闭）
    douban_enabled = cfg.get("douban_enabled", True)
    if not douban_enabled:
        logger.info(
            "   ⛔ [Profile] 豆瓣 API 已全局关闭 (douban_enabled=false)，跳过所有豆瓣请求"
        )

    # ---- Step 1: 老演员 ID 继承 ----
    if existing:
        provider_tmdb_id = provider_tmdb_id or (existing.tmdb_id or "")
        douban_id = douban_id or (existing.douban_celebrity_id or "")
        # 继承已有元数据，减少不必要的网络请求
        profile_data["overview"] = existing.overview or ""
        profile_data["birth_date"] = existing.birth_date or ""
        profile_data["birth_place"] = existing.birth_place or ""

    # ---- Step 2: 前置豆瓣影人盲搜与作品溯源（仅获取 ID，不调详情/不下载图片） ----
    if douban_enabled and not douban_id and False:
        try:
            from services.douban_api import DoubanApi
            douban_api = DoubanApi(user_cookie=douban_cookie)
            logger.info(
                "   🔍 [Profile] 上半场 豆瓣影人名字盲搜 (仅取ID): %s",
                actor_name,
            )
            search_res = douban_api.search(actor_name, count=20)

            if not search_res:
                logger.debug("   ⚠ [Profile] 盲搜无任何返回 (None)")
            elif search_res.get("error"):
                logger.debug("   ⚠ [Profile] 盲搜返回错误: %s", search_res)
            else:
                items = search_res.get("items", [])
                logger.debug(
                    "   🐛 [Debug] 盲搜获取到 %d 个 items (keyword=%s)",
                    len(items), actor_name,
                )

                # 阶段 1：直接寻找影人卡片
                for idx, item_obj in enumerate(items):
                    target = item_obj.get("target", {})
                    target_type = item_obj.get("target_type")
                    title = target.get("title") or target.get("name") or ""
                    item_id = target.get("id")

                    if idx < 10:
                        logger.debug(
                            "      ➜ [%d] type=%s title=%s id=%s",
                            idx, target_type, title, item_id,
                        )

                    if (
                        target_type in ("celebrity", "person")
                        and title.strip() == actor_name
                    ):
                        douban_id = str(item_id).strip()
                        logger.info(
                            "   🎯 [Profile] 上半场 豆瓣影人直搜命中: %s → douban_id=%s",
                            actor_name, douban_id,
                        )
                        break

                # 阶段 2：曲线救国 — 作品溯源反查
                if not douban_id:
                    logger.info(
                        "   🔄 [Profile] 直搜未命中影人卡片，启动作品溯源反查: %s",
                        actor_name,
                    )
                    media_items = [
                        i for i in items
                        if i.get("target_type") in ("movie", "tv")
                    ][:3]

                    for m_item in media_items:
                        m_target = m_item.get("target", {})
                        m_type = m_item.get("target_type")
                        m_id = m_target.get("id")
                        m_title = m_target.get("title") or m_target.get("name") or "?"

                        if not m_id:
                            continue

                        logger.debug(
                            "      🔗 尝试从作品《%s》(%s/%s) 反查演员表...",
                            m_title, m_type, m_id,
                        )

                        try:
                            if m_type == "tv":
                                cast_data = douban_api.tv_celebrities(m_id)
                            else:
                                cast_data = douban_api.movie_celebrities(m_id)

                            if cast_data and not cast_data.get("error"):
                                persons = (
                                    cast_data.get("celebrities")
                                    or cast_data.get("actors")
                                    or []
                                )
                                directors = cast_data.get("directors") or []
                                candidates = []

                                for p in (persons + directors):
                                    p_name = str(p.get("name") or "").lower().strip()
                                    p_latin = str(p.get("latin_name") or "").lower().strip()
                                    p_en = str(p.get("name_en") or "").lower().strip()
                                    found_id = str(p.get("id", "")).strip()
                                    target_name = actor_name.lower().strip()

                                    logger.debug(
                                        "         👉 [Debug-溯源] id=%s | name=%s | latin=%s | en=%s",
                                        found_id, p_name, p_latin, p_en,
                                    )

                                    if (
                                        target_name in p_name
                                        or target_name in p_latin
                                        or target_name in p_en
                                    ):
                                        if found_id and found_id.isdigit() and int(found_id) > 0:
                                            candidates.append(int(found_id))
                                            logger.debug(
                                                "         📌 [Debug-溯源] 候选命中: id=%s (name=%s)",
                                                found_id, p.get("name"),
                                            )

                                if candidates:
                                    douban_id = str(max(candidates))
                                    logger.info(
                                        "   🎯 [Profile] 作品溯源成功！"
                                        " 在《%s》中抓到演员: %s → douban_id=%s"
                                        " (候选 %d 个: %s)",
                                        m_title, actor_name, douban_id,
                                        len(candidates), sorted(candidates),
                                    )
                        except Exception as e:
                            logger.debug(
                                "      ⚠ 溯源作品 %s 失败: %s",
                                m_title, e,
                            )

                        if douban_id:
                            break

                if not douban_id:
                    logger.info(
                        "   ❌ [Profile] 盲搜及溯源均未找到影人: %s (共 %d 条)",
                        actor_name, len(items),
                    )

        except Exception as e:
            error_msg = str(e).lower()
            logger.warning(
                "   ⚠ [Profile] 上半场 豆瓣影人盲搜异常: %s - %s",
                actor_name, e,
            )
            if "need_login" in error_msg or "403" in error_msg or "too many requests" in error_msg:
                logger.error(
                    "   ⛔ [Profile] 触发豆瓣严重流控！强制退避休眠 5 秒保护 IP..."
                )
                _time.sleep(5)

    # ---- Step 3: 前置 TMDB 元数据查询（仅取元数据，头像暂存备份绝不截留） ----
    has_overview = bool(profile_data["overview"])
    needs_tmdb_meta = not provider_tmdb_id or not has_overview

    if force_refresh or needs_tmdb_meta:
        if provider_tmdb_id:
            logger.info(
                "   🎯 [Profile] 上半场 TMDB 精准 ID 拦截: %s → tmdb=%s",
                actor_name, provider_tmdb_id,
            )
        else:
            logger.info("   🌐 [Profile] 上半场 TMDB 查询: %s", actor_name)

        tmdb_data = fetch_tmdb_person_details(actor_name, tmdb_id=provider_tmdb_id)
        if tmdb_data:
            # ★ 提取元数据（绝不赋值给 download_url）
            profile_data["tmdb_id"] = tmdb_data.get("tmdb_id", "")
            profile_data["imdb_id"] = tmdb_data.get("imdb_id", "")
            profile_data["birth_date"] = tmdb_data.get("birth_date", "") or profile_data["birth_date"]
            profile_data["birth_place"] = tmdb_data.get("birth_place", "") or profile_data["birth_place"]
            profile_data["overview"] = tmdb_data.get("overview", "") or profile_data["overview"]

            # ★ 核心安全线：头像仅存入备份变量，绝不截留给 download_url
            if tmdb_data.get("image_url"):
                tmdb_avatar_bak = tmdb_data["image_url"]
                logger.info(
                    "   📋 [Profile] 上半场 TMDB 元数据已获取 (头像已备份): %s (tmdb=%s)",
                    actor_name, profile_data["tmdb_id"],
                )
            else:
                logger.info(
                    "   📋 [Profile] 上半场 TMDB 元数据已获取 (无头像): %s (tmdb=%s)",
                    actor_name, profile_data["tmdb_id"],
                )

    # ---- Step 4: 统一更新身份证（final_tmdb_id 100% 确立） ----
    final_tmdb_id = profile_data["tmdb_id"] or provider_tmdb_id

    # ================================================================
    # 下半场：严格优先级头像下载漏斗（阻断式设计）
    # 此时所有路径计算均可安全使用 final_tmdb_id 拼出规范路径
    # ================================================================

    # ---- 顺位 1: L0.5 Emby 原生头像优先（严格受 enable_emby_avatar_first 开关控制） ----
    enable_emby_first = cfg.get("enable_emby_avatar_first", False)
    if enable_emby_first:
        emby_person_id = ctx.get("emby_person_id")
        emby_image_tag = ctx.get("emby_image_tag")
        emby_server = cfg.get("emby_host", "").rstrip("/")
        emby_api_key = cfg.get("emby_api_key", "")

        if emby_person_id and emby_image_tag and emby_server and emby_api_key:
            emby_url = (
                f"{emby_server}/emby/Items/{emby_person_id}/Images/Primary"
                f"?tag={emby_image_tag}&api_key={emby_api_key}"
            )
            logger.info(
                "   🛡️ [Profile] 下半场 L0.5 Emby 优先头像: %s",
                actor_name,
            )

            # ★ 使用 final_tmdb_id 计算规范路径（路径 100% 正确，不再需要末端改名）
            relative, absolute_dir = _build_standard_path(
                actor_name,
                tmdb_id=final_tmdb_id,
                douban_id=douban_id,
            )
            absolute_file = os.path.join(absolute_dir, "folder.png")

            # ★ 即时网络连通性 + 极速熔断试探
            # connect_timeout=2.0 / read_timeout=3.0
            # 成功 → 阻断后续豆瓣/TMDB 头像获取
            # 失败/超时/异常 → 降级到 L1/L2
            try:
                if _download_image(emby_url, absolute_file, connect_timeout=2.0, read_timeout=3.0):
                    download_url = emby_url
                    local_path = relative
                    source = "emby"
                    logger.info(
                        "   ✅ [Profile] L0.5 Emby 优先命中并下载成功: %s → %s",
                        actor_name, relative,
                    )
                else:
                    logger.warning(
                        "   ⚠ [Profile] L0.5 Emby 头像获取失败"
                        " (超时/无图)，降级到 L1/L2"
                    )
            except Exception as e:
                logger.warning(
                    "   ⚠ [Profile] L0.5 Emby 熔断 (请求异常): %s — %s",
                    type(e).__name__, e,
                )

    # ---- 顺位 2: L1 豆瓣头像（上下文自带直链 + API 详情提取） ----
    if douban_enabled and not download_url:
        # 2a: 上下文自带头像直链
        if douban_avatar:
            download_url = douban_avatar
            source = "douban"
            logger.info(
                "   🥇 [Profile] L1 豆瓣 (上下文自带): %s → %s",
                actor_name, download_url[:80],
            )

    if douban_enabled and douban_id and (not download_url or force_refresh) and False:
        logger.info(
            "   🌐 [Profile] L1 豆瓣 (调 Frodo API 提取详情): %s (douban_id=%s)",
            actor_name, douban_id,
        )
        try:
            from services.douban_api import DoubanApi
            douban_api = DoubanApi(user_cookie=douban_cookie)
            douban_details = douban_api.celebrity_details(douban_id)

            if douban_details and not douban_details.get("error"):
                # ★ 元数据总是提取（不受头像状态影响）
                profile_data["overview"] = (
                    (douban_details.get("info") or "").strip()
                    or profile_data["overview"]
                )
                profile_data["birth_place"] = (
                    (douban_details.get("born_place") or "").strip()
                    or profile_data["birth_place"]
                )
                if douban_details.get("birthday"):
                    profile_data["birth_date"] = (
                        (douban_details.get("birthday") or "").strip()
                        or profile_data["birth_date"]
                    )

                # ★ 头像仅在没有 Emby/上下文自带时从豆瓣获取
                if not download_url:
                    avatar_obj = douban_details.get("avatar") or douban_details.get("cover_url")
                    douban_img = ""
                    if isinstance(avatar_obj, dict):
                        douban_img = avatar_obj.get("large") or avatar_obj.get("normal") or ""
                    elif isinstance(avatar_obj, str):
                        douban_img = avatar_obj

                    if douban_img:
                        download_url = douban_img
                        source = "douban"
                        logger.info(
                            "   🥇 [Profile] L1 豆瓣命中头像: %s → %s",
                            actor_name, download_url[:80],
                        )
                else:
                    logger.info(
                        "   📋 [Profile] L1 豆瓣仅更新文字元数据（头像已由 L0.5 接管）: %s",
                        actor_name,
                    )
        except Exception as e:
            error_msg = str(e).lower()
            logger.warning(
                "   ⚠ [Profile] L1 豆瓣 API 详情请求失败: %s - %s",
                actor_name, e,
            )
            if "need_login" in error_msg or "403" in error_msg or "too many requests" in error_msg:
                logger.error(
                    "   ⛔ [Profile] 触发豆瓣严重流控！强制退避休眠 5 秒保护 IP..."
                )
                _time.sleep(5)

    # ---- 顺位 3: L2 TMDB 头像最终兜底 ----
    if not download_url and tmdb_avatar_bak:
        download_url = tmdb_avatar_bak
        source = "tmdb"
        logger.info(
            "   🥈 [Profile] L2 TMDB 头像兜底: %s → %s",
            actor_name, download_url[:80],
        )

    # ================================================================
    # 统一下载 & 标准化落盘（顺位 2/3 的头像 URL 在此统一落盘）
    # ================================================================
    if download_url and not local_path:
        # _build_standard_path 返回 (完整相对路径, 绝对目录)，如:
        #   ("曲/曲靖-tmdb-123/folder.png", "/abs/people/曲/曲靖-tmdb-123")
        _ref_relative, absolute_dir = _build_standard_path(
            actor_name,
            tmdb_id=final_tmdb_id,
            douban_id=douban_id,
        )
        # 从完整相对路径中提取目录部分（去掉内置的 folder.png 文件名）
        relative_dir = "/".join(_ref_relative.split("/")[:-1])

        # ★ 动态解析网络直链的后缀名
        from urllib.parse import urlparse

        parsed_url = urlparse(download_url)
        # 去掉 query 参数（如 ?tag=xxx），只取路径部分的扩展名
        ext = os.path.splitext(parsed_url.path)[1].lower()

        valid_exts = (".jpg", ".jpeg", ".png", ".webp")
        if ext not in valid_exts:
            ext = ".png"

        file_name = f"folder{ext}"
        absolute_file = os.path.join(absolute_dir, file_name)
        relative_path = f"{relative_dir}/{file_name}"

        logger.info(
            "   📥 [Profile] 准备落盘: %s → %s (ext=%s)",
            actor_name, relative_path, ext,
        )

        # ★ 清场逻辑：删除目标目录下可能存在的旧格式头像
        if os.path.exists(absolute_dir):
            for old_ext in valid_exts:
                old_file = os.path.join(absolute_dir, f"folder{old_ext}")
                if os.path.exists(old_file) and old_file != absolute_file:
                    try:
                        os.remove(old_file)
                        logger.debug(
                            "   🧹 [Profile] 清除同目录冗余旧格式: %s",
                            old_file,
                        )
                    except OSError:
                        pass

        if _download_image(download_url, absolute_file):
            local_path = relative_path
        else:
            logger.warning(
                "   ⚠ [Profile] 下载失败，保留外部链接兜底: %s",
                actor_name,
            )

    # 如果既没有本地文件也没有外部链接，检查是否有元数据需要保存
    has_meta = any([
        profile_data["tmdb_id"], profile_data["imdb_id"],
        profile_data["birth_date"], profile_data["birth_place"],
        profile_data["overview"],
    ])
    if not local_path and not download_url and not has_meta and not existing:
        logger.info("   ❌ [Profile] 无任何可用数据: %s", actor_name)
        return None

    # ================================================================
    # UPSERT actor_profiles（路径已 100% 规范化，无需 shutil.move 兜底）
    # ================================================================
    try:
        if existing is None:
            existing = ActorProfile(name=actor_name)
            db.add(existing)

        existing.local_image_path = local_path or (existing.local_image_path or "")
        existing.image_url = download_url or (existing.image_url or "")
        existing.source = source or (existing.source or "")
        existing.tmdb_id = profile_data["tmdb_id"] or (existing.tmdb_id or "")
        existing.imdb_id = profile_data["imdb_id"] or (existing.imdb_id or "")
        existing.douban_celebrity_id = douban_id or (existing.douban_celebrity_id or "")
        existing.birth_date = profile_data["birth_date"] or (existing.birth_date or "")
        existing.birth_place = profile_data["birth_place"] or (existing.birth_place or "")
        existing.overview = profile_data["overview"] or (existing.overview or "")
        existing.update_time = datetime.now()

        db.flush()

        logger.info(
            "   💾 [Profile] UPSERT 完成: %s | local=%s | source=%s | tmdb=%s",
            actor_name, existing.local_image_path or "-",
            existing.source or "-", existing.tmdb_id or "-",
        )

        return {
            "name": existing.name,
            "local_image_path": existing.local_image_path or "",
            "image_url": existing.image_url or "",
            "local_image_url": "",  # 调用方拼接 host
            "source": existing.source or "",
            "tmdb_id": existing.tmdb_id or "",
            "imdb_id": existing.imdb_id or "",
            "douban_celebrity_id": existing.douban_celebrity_id or "",
            "birth_date": existing.birth_date or "",
            "birth_place": existing.birth_place or "",
            "overview": existing.overview or "",
        }

    except Exception:
        logger.error(
            "   ❌ [Profile] UPSERT 失败: %s\n%s",
            actor_name, traceback.format_exc(),
        )
        return None


# ==========================================
# 批量便捷函数
# ==========================================

def ensure_profiles_for_people(
    db,
    people: list,
) -> dict:
    """为一组 People 字典批量确保 ActorProfile 存在。

    遍历 people 列表，对每个 Actor/GuestStar 调用 resolve_actor_profile。
    常用于 save_media_to_db 前的预处理阶段。

    关键安全措施:
    - 每个演员独立 try/except，单条失败不影响其他演员
    - 使用 db.begin_nested() (Savepoint) 隔离每条演员入库操作，
      确保单条异常只回滚当前 Savepoint，绝不影响外层父事务（如电视剧/剧情入库）
    - 记录总体统计信息

    Args:
        db:      SQLAlchemy Session
        people:  Emby People 格式的字典列表

    Returns:
        {actor_name: profile_dict}  映射，profile_dict 同 resolve_actor_profile 返回值。
    """
    result = {}
    actors = []
    for p in people:
        person_type = p.get("Type", "Actor")
        if person_type not in ("Actor", "GuestStar"):
            continue
        name = (p.get("Name") or "").strip()
        if not name or name in result:
            continue
        actors.append((name, p))

    if not actors:
        return result

    logger.info(
        "👥 [Profile] 开始批量解析 %d 位演员 (%d 位已跳过)",
        len(actors), len(people) - len(actors),
    )

    success_count = 0
    fail_count = 0
    local_hit_count = 0

    for name, p in actors:
        try:
            # 开启嵌套事务，确保单条演员失败不会炸毁外层父事务（如电视剧/剧情的入库）
            with db.begin_nested():
                ctx = {
                    "douban_avatar_url": (p.get("DoubanAvatarUrl") or "").strip(),
                    "douban_celebrity_id": (p.get("DoubanCelebrityId") or "").strip(),
                    "ProviderIds": p.get("ProviderIds", {}),
                    "tmdb_id": _safe_get_str(p, "Tmdb") or _safe_get_str(p, "tmdb"),
                    "emby_person_id": p.get("Id"),
                    "emby_image_tag": p.get("PrimaryImageTag") or (
                        p.get("ImageTags", {}).get("Primary") if isinstance(p.get("ImageTags"), dict) else None
                    ),
                }
                profile = resolve_actor_profile(name, db, context_info=ctx)
                if profile:
                    result[name] = profile
                    success_count += 1
                    if profile.get("source") == "local" or (
                        profile.get("local_image_path") and not profile.get("image_url")
                    ):
                        local_hit_count += 1
                else:
                    fail_count += 1
                    logger.warning("   ⚠ [Profile] 解析失败 (无数据源): %s", name)

                # 注意：with db.begin_nested() 块成功结束时，会自动触发 flush，无需手动调用

        except Exception:
            fail_count += 1
            # 注意：with 块捕获到异常时，会自动 rollback 到 begin_nested() 的切入点，
            # 绝不会回滚外层事务。因此这里【绝对不要】再调用 db.rollback()！
            logger.error(
                "   ❌ [Profile] 处理演员异常: %s\n%s",
                name, traceback.format_exc(),
            )

    logger.info(
        "👥 [Profile] 批量解析完成: %d/%d 成功 (本地命中 %d) | %d 失败",
        success_count, len(actors), local_hit_count, fail_count,
    )

    return result
