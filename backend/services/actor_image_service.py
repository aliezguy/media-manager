"""
演员头像解析服务 — 双源漏斗策略（Douban → TMDB → 空）。

核心逻辑: 将豆瓣 avatar 外链、TMDB profile_path 拼接统一为单一入口。
已废弃 Emby PrimaryImageTag 拼接逻辑。

优先级:
  L1 豆瓣外链优先 — 来自 Frodo API 的 avatar.large 绝对直链
  L2 TMDB 外链兜底 — Search Person API → profile_path → image.tmdb.org
  L3 空值       — 以上均不可用时返回 ""（坚决不使用 Emby 原生头像）
"""

import logging
import requests as _requests
from config.settings import load_config

logger = logging.getLogger("uvicorn")

# TMDB 图片 CDN 基础 URL（w185 适合头像尺寸）
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w185"

# 进程级内存缓存: {actor_name_lower: resolved_url}
# TMDB 搜索成本高，同一演员可能跨多集重复出现
_image_cache: dict = {}


def resolve_actor_image_url(
    actor_name: str = "",
    *,
    douban_avatar_url: str = "",
) -> str:
    """双源漏斗：返回最佳可用的演员头像绝对 URL。

    调用方只需传入已有的上下文信息，函数按优先级短路求值：
    - 豆瓣外链不为空 → 直接返回（不触发 TMDB 请求）
    - TMDB 命中 → 写入缓存后返回
    - 全部失败 → 返回 ""（不使用 Emby 原生头像）

    Args:
        actor_name:        演员名称（用于 TMDB 搜索 & 缓存 key）
        douban_avatar_url: 豆瓣头像直链 (e.g. https://img9.doubanio.com/...)

    Returns:
        最佳图片 URL 或空字符串。
    """
    # ---- L1: 豆瓣外链优先（短路，不产生任何网络请求） ----
    if douban_avatar_url:
        return douban_avatar_url

    if not actor_name:
        return ""

    cache_key = actor_name.lower().strip()

    # ---- 检查 TMDB 缓存 ----
    if cache_key in _image_cache:
        return _image_cache[cache_key]

    # ---- L2: TMDB 外链兜底 ----
    tmdb_url = _resolve_via_tmdb(actor_name)
    if tmdb_url:
        _image_cache[cache_key] = tmdb_url
        return tmdb_url

    # ---- L3: 兜底空值（不使用 Emby 原生头像） ----
    _image_cache[cache_key] = ""
    return ""


def _resolve_via_tmdb(actor_name: str) -> str:
    """通过 TMDB Search Person API 查找演员，拼接 profile_path 为绝对外链。"""
    cfg = load_config()
    api_key = cfg.get("tmdb_api_key", "")
    if not api_key:
        logger.debug("   🔍 [ActorImage] 未配置 tmdb_api_key，跳过 TMDB 搜索")
        return ""

    base_url = cfg.get("tmdb_base_url", "") or "https://api.tmdb.org/3"

    try:
        search_url = f"{base_url}/search/person"
        resp = _requests.get(search_url, params={
            "api_key": api_key,
            "query": actor_name,
            "language": "zh-CN",
        }, timeout=10)

        if resp.status_code != 200:
            logger.debug(
                "   🔍 [ActorImage] TMDB 搜索 '%s' HTTP %d",
                actor_name, resp.status_code,
            )
            return ""

        data = resp.json()
        results = data.get("results", [])
        if not results:
            logger.debug("   🔍 [ActorImage] TMDB 搜索 '%s' 无结果", actor_name)
            return ""

        person = results[0]
        profile_path = (person.get("profile_path") or "").strip()

        if profile_path:
            url = f"{TMDB_IMAGE_BASE}{profile_path}"
            logger.debug("   🖼️ [ActorImage] TMDB: %s → %s", actor_name, url)
            return url

        logger.debug("   🔍 [ActorImage] TMDB '%s' 无 profile_path", actor_name)
        return ""

    except Exception:
        logger.debug(
            "   ⚠ [ActorImage] TMDB 搜索异常: %s", actor_name, exc_info=True,
        )
        return ""


def resolve_batch(
    people: list,
) -> list:
    """对一批 People 字典批量解析头像 URL，原地修改每个 dict 的 ImageUrl 字段。

    从 person dict 中提取上下文信息（DoubanAvatarUrl），
    调用 resolve_actor_image_url 后写回 person["ImageUrl"]。

    Args:
        people: Emby People 格式的字典列表（会被原地修改）

    Returns:
        同 people（原地修改后的同一列表），方便链式调用。
    """
    for p in people:
        person_type = p.get("Type", "Actor")
        if person_type not in ("Actor", "GuestStar"):
            continue

        name = (p.get("Name") or "").strip()
        if not name:
            continue

        douban_avatar = p.get("DoubanAvatarUrl", "") or ""

        resolved = resolve_actor_image_url(
            actor_name=name,
            douban_avatar_url=douban_avatar,
        )

        if resolved:
            p["ImageUrl"] = resolved

    return people
