"""
Organize Service — Torrent name parsing + TMDB lookup.

Used by the TorrentCleanup page to resolve a torrent name into structured
show metadata so the frontend can cross-reference CD2 directories and
filter the "to-be-cleaned" torrent list.
"""

import json
import logging
import re
from typing import Optional

import requests
from openai import OpenAI

from services.ai_translator import get_primary_provider

from config.settings import load_config

# Category resolution (existing project strategy)
from services.category_service import determine_category

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex-based parser
# ---------------------------------------------------------------------------

# Pre-compiled patterns ordered from most-specific to least-specific
_PARSE_PATTERNS = [
    # "剧名(2026) {tmdb=284110}" — CD2 naming convention
    re.compile(
        r"^(?P<title>.+?)\((?P<year>\d{4})\)\s*\{tmdb=(?P<tmdb>\d+)\}",
    ),
    # "剧名(2026)" — year-in-parens
    re.compile(
        r"^(?P<title>.+?)\((?P<year>\d{4})\)",
    ),
    # "The.Name.2026.S01.1080p" / "剧名.2026.S01E01.1080p"
    re.compile(
        r"^(?P<title>.+?)\.(?P<year>(?:19|20)\d{2})\.S(?P<season>\d{1,2})",
        re.IGNORECASE,
    ),
    # "剧名.S01.2026.1080p" — season-then-year (ADWeb / IQ / WEB-DL style)
    re.compile(
        r"^(?P<title>.+?)\.S(?P<season>\d{1,2})\.(?P<year>(?:19|20)\d{2})\b",
        re.IGNORECASE,
    ),
    # "[Group] 剧名 (2026) - S01E01 [1080p]"
    re.compile(
        r"^\[.*?\]\s*(?P<title>.+?)\s*\((?P<year>\d{4})\)\s*-\s*S(?P<season>\d{1,2})E\d+",
        re.IGNORECASE,
    ),
    # "剧名 - S01E01" (no year)
    re.compile(
        r"^(?P<title>.+?)\s*-\s*S(?P<season>\d{1,2})E\d+",
        re.IGNORECASE,
    ),
    # "剧名 第1季" (Chinese season marker)
    re.compile(
        r"^(?P<title>.+?)\s*第\s*(?P<season>\d+)\s*季",
    ),
    # "剧名.S01." (any position)
    re.compile(
        r"^(?P<title>.+?)\.S(?P<season>\d{1,2})\b",
        re.IGNORECASE,
    ),
]


def parse_torrent_name_regex(name: str) -> Optional[dict]:
    """Try to extract (title, year, season) from torrent name using regex.

    Returns ``None`` when no pattern matches.
    """
    for pat in _PARSE_PATTERNS:
        m = pat.search(name)
        if m:
            result = {
                "title": _clean_title(m.group("title")),
                "year": m.group("year") if "year" in m.groupdict() and m.group("year") else None,
                "season": (
                    int(m.group("season"))
                    if "season" in m.groupdict() and m.group("season")
                    else 1
                ),
                "source": "regex",
            }
            # Also capture tmdb_id if present
            if "tmdb" in m.groupdict() and m.group("tmdb"):
                result["tmdb_id"] = int(m.group("tmdb"))
            return result
    return None


def _clean_title(raw: str) -> str:
    """Remove common separators and trailing dots from extracted title."""
    t = raw.strip()
    t = re.sub(r"[._\-]+$", "", t)  # trailing dots/dashes
    t = re.sub(r"\s*[-–—]\s*$", "", t)  # trailing dash
    return t.strip()


# ---------------------------------------------------------------------------
# MoviePilot fallback parser (preferred over AI — faster and TMDB-aware)
# ---------------------------------------------------------------------------

def parse_torrent_name_mp(name: str) -> Optional[dict]:
    """Use MoviePilot's /api/v1/media/recognize to parse the torrent name.

    MP's recognition engine has built-in TMDB lookup, making it more
    accurate than regex alone for ambiguous titles or years (e.g.
    distinguishing 2025 vs 2026 for a new show).
    """
    try:
        from services.mp_service import recognize_torrent_with_mp
        result = recognize_torrent_with_mp(name)
        if result and result.get("title"):
            result["source"] = "mp"
            return result
        return None
    except Exception as e:
        logger.warning("MP parse failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# AI fallback parser (SiliconFlow / DeepSeek-V3)
# ---------------------------------------------------------------------------

def _get_ai_client():
    """从统一配置解析首选 Provider，返回 (OpenAI client, model_name)。"""
    provider = get_primary_provider()
    if not provider or not provider.get("api_key"):
        raise RuntimeError("AI Provider not configured")
    model = provider.get("model_name") or "deepseek-ai/DeepSeek-V3"
    client = OpenAI(api_key=provider["api_key"], base_url=provider.get("base_url") or None)
    return client, model


AI_PARSE_PROMPT = """请从以下种子/文件名中提取影视剧信息，只返回纯 JSON，不要包含 Markdown 代码块。

种子名：{torrent_name}

要求提取的字段：
- title: 剧名（中文或英文）
- year: 首播年份（4位数字），如果无法确定则填 null
- season: 第几季（数字），默认为 1
- note: 备注，如果是从完整路径或复杂命名中提取的，可以简要说明提取依据

返回格式：{{"title": "...", "year": 2026, "season": 1, "note": "..."}}"""


def parse_torrent_name_ai(name: str) -> Optional[dict]:
    """Use AI (DeepSeek-V3 via SiliconFlow) to extract show metadata from
    a torrent name that regex couldn't handle."""
    try:
        client, model = _get_ai_client()
    except RuntimeError as e:
        logger.warning("AI parse skipped — %s", e)
        return None

    prompt = AI_PARSE_PROMPT.format(torrent_name=name)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            stream=False,
        )
        content = response.choices[0].message.content
        content = content.replace("```json", "").replace("```", "").strip()
        result = json.loads(content)
        result["source"] = "ai"
        # Normalise types
        if result.get("year"):
            result["year"] = str(result["year"])
        if not result.get("season"):
            result["season"] = 1
        else:
            result["season"] = int(result["season"])
        return result
    except Exception as e:
        logger.error("AI parse failed for '%s': %s", name, e)
        return None


def parse_torrent_name(name: str) -> dict:
    """Best-effort parser: regex → MoviePilot → AI.

    Always returns a dict; check ``success`` field.
    """
    # 1 — Try regex (fast, works for well-structured names)
    result = parse_torrent_name_regex(name)
    if result:
        result["success"] = True
        return result

    # 2 — Try MoviePilot recognition (TMDB-aware, handles ambiguous years)
    result = parse_torrent_name_mp(name)
    if result and result.get("title"):
        result["success"] = True
        return result

    # 3 — Regex & MP both failed — try AI as last resort
    result = parse_torrent_name_ai(name)
    if result and result.get("title"):
        result["success"] = True
        return result

    return {
        "success": False,
        "error": f"无法解析种子名称: {name[:60]}",
        "title": name,
        "year": None,
        "season": 1,
        "source": "fallback",
    }


# ---------------------------------------------------------------------------
# TMDB helpers
# ---------------------------------------------------------------------------

import time as _time

# 从配置读取 TMDB base URL，默认 api.tmdb.org（国内访问相对稳定）
def _tmdb_base() -> str:
    cfg = load_config()
    return cfg.get("tmdb_base_url", "") or "https://api.tmdb.org/3"


def _tmdb_key() -> str:
    cfg = load_config()
    key = cfg.get("tmdb_api_key", "")
    if not key:
        raise RuntimeError("tmdb_api_key not configured")
    return key


def _tmdb_get(url: str, params: dict, timeout: int = 10, max_retries: int = 2) -> "requests.Response | None":
    """TMDB HTTP GET with retry + exponential backoff.

    应对国内网络环境下间歇性的 SSL/连接错误。
    - 第 1 次重试：等待 1.5s
    - 第 2 次重试：等待 3s
    """
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            return resp
        except (requests.exceptions.SSLError, requests.exceptions.ConnectionError,
                requests.exceptions.Timeout, requests.exceptions.ChunkedEncodingError) as e:
            last_error = e
            if attempt < max_retries:
                wait = 1.5 * (2 ** attempt)
                logger.warning("TMDB 请求失败 (attempt %d/%d)，%ss 后重试: %s",
                               attempt + 1, max_retries + 1, wait, e)
                _time.sleep(wait)
        except Exception as e:
            last_error = e
            break  # 非网络错误不重试

    logger.error("TMDB 请求最终失败: %s", last_error)
    return None


def search_tmdb_tv(title: str, year: Optional[str] = None) -> Optional[dict]:
    """Search TMDB for a TV show by title + optional year.

    Returns the best-matching show dict (id, name, first_air_date, …) or None.
    """
    key = _tmdb_key()
    params = {"api_key": key, "query": title, "language": "zh-CN"}
    if year:
        params["first_air_date_year"] = year

    try:
        resp = _tmdb_get(f"{_tmdb_base()}/search/tv", params=params)
        if resp is None or resp.status_code != 200:
            if resp:
                logger.warning("TMDB search failed: %s", resp.status_code)
            return None
        data = resp.json()
        results = data.get("results", [])
        if not results:
            logger.info("TMDB search '%s' (%s) → 0 results", title, year)
            return None
        # Pick first result
        best = results[0]
        logger.info(
            "TMDB search '%s' (%s) → '%s' (id=%s)",
            title, year, best.get("name"), best.get("id"),
        )
        return best
    except Exception as e:
        logger.error("TMDB search error: %s", e)
        return None


def get_tv_details(tmdb_id: int) -> Optional[dict]:
    """Fetch full TV show details including genres, origin_country etc.

    These fields are required by ``determine_category``.
    """
    key = _tmdb_key()
    url = f"{_tmdb_base()}/tv/{tmdb_id}"
    params = {"api_key": key, "language": "zh-CN"}

    try:
        resp = _tmdb_get(url, params=params)
        if resp is None or resp.status_code != 200:
            if resp:
                logger.warning("TMDB tv/%d failed: %s", tmdb_id, resp.status_code)
            return None
        return resp.json()
    except Exception as e:
        logger.error("TMDB tv/%d error: %s", tmdb_id, e)
        return None


def resolve_category(tmdb_id: int) -> Optional[str]:
    """Determine the show's category (国产剧, 欧美剧, etc.) using the project's
    existing category.yaml rules."""
    details = get_tv_details(tmdb_id)
    if not details:
        return None
    return determine_category(details, "电视剧")


def get_tv_season_info(tmdb_id: int, season: int = 1) -> Optional[dict]:
    """Fetch season details from TMDB, including ``episode_count``."""
    key = _tmdb_key()
    url = f"{_tmdb_base()}/tv/{tmdb_id}/season/{season}"
    params = {"api_key": key, "language": "zh-CN"}

    try:
        resp = _tmdb_get(url, params=params)
        if resp is None or resp.status_code != 200:
            if resp:
                logger.warning("TMDB season %d for tv/%d failed: %s", season, tmdb_id, resp.status_code)
            return None
        data = resp.json()
        # Use `or []` to guarantee len() always receives a list,
        # avoiding the short-circuit pitfall where `[] and len([])` → `[]` (not 0).
        episodes = data.get("episodes") or []
        return {
            "season_number": data.get("season_number"),
            "episode_count": len(episodes),
            "name": data.get("name", ""),
            "air_date": data.get("air_date"),
            "poster_path": data.get("poster_path"),
        }
    except Exception as e:
        logger.error("TMDB season error: %s", e)
        return None


# ---------------------------------------------------------------------------
# High-level orchestration
# ---------------------------------------------------------------------------

def analyze_torrent(name: str) -> dict:
    """Full pipeline: parse → TMDB search → season info.

    Returns::

        {
          "success": True/False,
          "title": "...",
          "year": "2026" or None,
          "season": 1,
          "total_episodes": 24 or None,
          "tmdb_id": 284110 or None,
          "tmdb_name": "..." or None,
          "source": "regex" | "ai" | "fallback",
          "error": "..." (only when success=False),
        }
    """
    # 1 — Parse
    parsed = parse_torrent_name(name)
    if not parsed.get("success"):
        return parsed  # already has error

    title = parsed.get("title", "")
    year = parsed.get("year")
    season = parsed.get("season", 1)

    result = {
        "success": True,
        "title": title,
        "year": year,
        "season": season,
        "total_episodes": None,
        "tmdb_id": parsed.get("tmdb_id"),
        "tmdb_name": None,
        "resolved_category": None,
        "source": parsed.get("source", "regex"),
        "error": None,
    }

    # 2 — TMDB search (skip if already have tmdb_id from regex)
    if not result["tmdb_id"]:
        show = search_tmdb_tv(title, year)
        if show:
            result["tmdb_id"] = show.get("id")
            result["tmdb_name"] = show.get("name")
            if not year and show.get("first_air_date"):
                result["year"] = show["first_air_date"][:4]
        else:
            result["success"] = False
            result["error"] = f"TMDB 未找到匹配剧集: {title} ({year or '未知年份'})"
            return result

    # 3 — Category resolution (uses existing category.yaml rules)
    if result["tmdb_id"]:
        category = resolve_category(result["tmdb_id"])
        if category:
            result["resolved_category"] = category
            logger.info("Resolved category for '%s': %s", title, category)

    # 4 — Season episode count
    if result["tmdb_id"]:
        season_info = get_tv_season_info(result["tmdb_id"], season)
        if season_info:
            result["total_episodes"] = season_info.get("episode_count")

    return result
