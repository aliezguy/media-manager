"""
Douban 演员与角色名中文化服务。

从 Emby ItemId 出发，通过豆瓣抓取演员中文名和角色名，
利用拼音降级匹配对齐 Emby 中的英文/拼音人员数据，最后回写 Emby。
"""

import re
import random
import time
import logging
import traceback
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
from services.ai_translator import get_translator, _is_rate_limit_error, _rate_limit_sleep
from database import SessionLocal
from services.db_crud import save_media_to_db, extract_provider_ids
from services.actor_profile_service import ensure_profiles_for_people
from utils.task_manager import task_manager

logger = logging.getLogger("uvicorn")

# 中文字符正则（供分集汉化率校验用）
_EP_CHINESE_RE = re.compile(r'[一-鿿]')


def _count_chinese_roles_ep(people: list) -> tuple:
    """统计分集演员中角色名为中文的数量。"""
    actors = [p for p in people if p.get("Type") in ("Actor", "GuestStar")]
    total = len(actors)
    if total == 0:
        return 0, 0
    chinese_count = sum(
        1 for a in actors
        if a.get("Role") and _EP_CHINESE_RE.search(a.get("Role", ""))
    )
    return chinese_count, total


def _is_chinese_role_synced_ep(people: list) -> bool:
    """判断分集是否已汉化：>= 90% 演员角色名含中文。"""
    chinese_count, total = _count_chinese_roles_ep(people)
    return total > 0 and (chinese_count / total) >= 0.9


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

DOUBAN_FRODO_UA = "api-client/1 com.douban.frodo/7.22.0.beta9(231) Android/23 product/Mate 40 vendor/HUAWEI model/Mate 40 brand/HUAWEI  rom/android  network/wifi  platform/AndroidPad"
DOUBAN_FRODO_BASE = "https://frodo.douban.com/api/v2"
DOUBAN_FRODO_KEY = "0dad551ec0f84ed02907ff5c42e8ec70"
DOUBAN_FRODO_SECRET = "bf7dddc7c9cfe6f7"
DOUBAN_SUBJECT_URL = "https://movie.douban.com/subject/{douban_id}/celebrities"

# 不再截断演员数量 — 全量抓取以构建饱满的中文化词典供分集匹配
MAX_ACTORS = 999


def _truncate_actors(people: list, max_count: int) -> list:
    """截断演员列表：仅限制 Actor/GuestStar 数量，非演员人员不受影响。

    抓取阶段全量获取，回写 Emby 和入库时才截断——保证 douban_match_map
    足够饱满（分集客串可以命中），同时控制回写的演员数。
    """
    if max_count <= 0 or len(people) == 0:
        return people

    actors = []
    non_actors = []
    for p in people:
        if p.get("Type") in ("Actor", "GuestStar"):
            actors.append(p)
        else:
            non_actors.append(p)

    if len(actors) <= max_count:
        return people

    logger.info(
        "   📏 [截断] 演员 %d → %d (max_actors_per_media=%d)",
        len(actors), max_count, max_count,
    )
    return actors[:max_count] + non_actors


class DoubanSinizer:
    """媒体库演员与角色名中文化处理器。"""

    def __init__(self):
        cfg = load_config()
        self.emby_host = cfg.get("emby_host", "").rstrip("/")
        self.emby_api_key = cfg.get("emby_api_key", "")
        self.emby_user_id = cfg.get("emby_user_id", "")
        self.max_actors_per_media = cfg.get("max_actors_per_media", 50)
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

    def sinicize(self, item_id: str, task_id: str = None) -> dict:
        """主流程：读取 Emby → 抓取豆瓣 → 匹配 → 回写。

        返回 {"success": bool, "matched": int, "total_actors": int, "details": [...]}

        Args:
            item_id: Emby Item ID
            task_id: 可选，后台任务 ID（用于分集循环内颗粒度进度反馈）
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
        # ★ 提取 library_id：优先使用 Emby 返回的 ParentId（即媒体库 ID）
        series_library_id = item_data.get("ParentId", "") or ""

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

        # 2. 提取 IMDB / TMDB ID → 豆瓣 ID（精准 Frodo API 匹配）
        provider_ids = item_data.get("ProviderIds", {}) or {}
        douban_id = self._find_douban_id(
            provider_ids,
            title=item_name,
            mtype=item_data.get("Type", ""),
            year=str(item_data.get("ProductionYear", "")),
        )
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

        # 5b. ★ AI 批量推理缺失角色名
        # 收集 Role 为空/占位符的演员，利用 LLM 影视知识精准推理
        missing_role_actors = []
        for a in updated_actors:
            role = (a.get("Role") or "").strip()
            if not role or role in ("演员", "(无)", "未知", "actor", "actress"):
                name = (a.get("Name") or "").strip()
                if name:
                    missing_role_actors.append(name)

        if missing_role_actors:
            logger.info(
                "   🧠 [AI推理] %d 位演员角色缺失，请求 LLM 批量推理...",
                len(missing_role_actors),
            )
            year = item_data.get("ProductionYear", "")
            inferred_roles = self._infer_missing_roles_via_ai(
                item_name, year, missing_role_actors,
            )
            # 回填推理结果到 updated_actors → 后续自动流入 Emby 回写 + 分集缓存
            if inferred_roles:
                backfill_count = 0
                for a in updated_actors:
                    name = (a.get("Name") or "").strip()
                    if name in inferred_roles:
                        a["Role"] = inferred_roles[name]
                        backfill_count += 1
                        logger.info(
                            "   🧠 [AI推理] %s → %s",
                            name, inferred_roles[name],
                        )
                logger.info(
                    "   ✅ [AI推理] 已回填 %d 个角色到 Series 演员列表",
                    backfill_count,
                )

        # ★ 截断回写：抓取阶段已全量构建 douban_match_map，回写时按配置截断
        updated_actors = _truncate_actors(updated_actors, self.max_actors_per_media)

        # 统计修正：匹配数基于截断后的实际列表
        result["total_actors"] = len([a for a in updated_actors if a.get("Type") == "Actor"])
        result["matched"] = sum(
            1 for a in updated_actors
            if a.get("Type") == "Actor" and (a.get("Role") or "").strip()
        )

        # 6. 回写 Emby
        all_people = updated_actors + non_actor_people
        write_ok = self._write_back_emby(item_id, item_data, all_people)
        result["success"] = write_ok

        # ==========================================
        # 持久化中文化结果到 SQLite（三表联动）
        # ==========================================
        db = SessionLocal()
        try:
            pids = extract_provider_ids(item_data)
            save_media_to_db(
                db,
                emby_item=item_data,
                provider_ids=pids,
                images=None,          # 自动从 item_data 提取
                people=all_people,    # 中文化后的完整人员列表
                library_id=series_library_id,
                status="synced" if write_ok else "failed",
                matched_actors=result["matched"],
                total_actors=result["total_actors"],
                error_message="" if write_ok else "Emby 回写失败",
            )
            db.commit()
            logger.info(
                "   💾 [DB] 已同步入库: status=%s matched=%d/%d",
                "synced" if write_ok else "failed",
                result["matched"], result["total_actors"],
            )
        except Exception as e:
            db.rollback()
            logger.warning("   ⚠️ [DB] 同步入库失败: %s\n%s", e, traceback.format_exc())
        finally:
            db.close()

        # ==========================================
        # 7. ★ 分集递归中文化 (仅 Series)
        # ==========================================
        item_type = item_data.get("Type", "")
        total_episodes_processed = 0
        total_episodes_synced = 0

        if write_ok and item_type == "Series":
            logger.info("   📺 [Douban/Series] 开始递归处理分集: %s", item_name)

            # 7a. 构建豆瓣匹配映射表
            douban_match_map = self._build_douban_match_map(douban_actors, actors)

            # ★ 打通翻译缓存闭环：将 Series 层已汉化的最终结果（含 AI 翻译）
            # 全部注入 douban_match_map，确保分集能 100% 继承复用
            for a in updated_actors:
                name = (a.get("Name") or "").strip()
                role = (a.get("Role") or "").strip()
                if not name:
                    continue
                key = name.lower()
                if key not in douban_match_map:
                    douban_match_map[key] = {"name": name, "role": role}
                else:
                    # 已有条目时，用 Series 层的中文角色名覆盖可能为空的 role
                    existing_role = douban_match_map[key].get("role", "")
                    if role and (not existing_role or self._is_chinese(role)):
                        douban_match_map[key]["role"] = role
            logger.info(
                "   📋 [Douban/Series] 映射表共 %d 条（含 Series 层汉化结果）",
                len(douban_match_map),
            )

            # 7b. 抓取全部分集
            episodes = self._fetch_episodes(item_id)
            if not episodes:
                logger.info("   ⚠ [Douban/Series] %s 无分集数据", item_name)
            else:
                logger.info(
                    "   📺 [Douban/Series] %s 共 %d 个分集，开始逐集中文化...",
                    item_name, len(episodes),
                )

                # ★★★ 前置去重批处理（核心性能优化）★★★
                # 在分集循环之前，一次性提取全剧所有分集的演员，
                # 按 Name 去重后统一送入本地 L0-L2 超级漏斗。
                # 这样每位演员只触发一次漏斗，避免分集循环中重复 TMDB/Douban API 调用。
                all_ep_people_map: dict[str, dict] = {}  # {name_lower: person_dict}
                for ep in episodes:
                    for p in (ep.get("People", []) or []):
                        if p.get("Type") not in ("Actor", "GuestStar"):
                            continue
                        name = (p.get("Name") or "").strip()
                        if not name:
                            continue
                        key = name.lower()
                        if key not in all_ep_people_map:
                            # 先应用 douban_match_map 中的中文名/角色翻译，
                            # 确保传入漏斗的 person dict 携带正确的 Name、DoubanAvatarUrl 和 DoubanCelebrityId
                            if key in douban_match_map:
                                info = douban_match_map[key]
                                enriched = dict(p)
                                enriched["DoubanAvatarUrl"] = info.get("avatar", "")
                                # ★ 注入豆瓣演员 ID，使 L1 漏斗能精准调用 celebrity_details
                                douban_id_str = str(info.get("douban_id", "") or "")
                                if douban_id_str:
                                    enriched["DoubanCelebrityId"] = douban_id_str
                                if info.get("name"):
                                    enriched["Name"] = info["name"]
                                all_ep_people_map[key] = enriched
                            else:
                                all_ep_people_map[key] = dict(p)

                if all_ep_people_map:
                    logger.info(
                        "   🚀 [Douban/Series] 前置批处理: %d 位唯一演员 → 一次性送入漏斗",
                        len(all_ep_people_map),
                    )
                    unique_people = list(all_ep_people_map.values())

                    ep_db = SessionLocal()
                    try:
                        ensure_profiles_for_people(ep_db, unique_people)
                        ep_db.commit()
                        logger.info(
                            "   ✅ [Douban/Series] 前置批处理完成: %d 位演员已过漏斗",
                            len(all_ep_people_map),
                        )
                    except Exception as e:
                        ep_db.rollback()
                        logger.warning(
                            "   ⚠ [Douban/Series] 前置批处理异常: %s", e,
                        )
                    finally:
                        ep_db.close()

                # ★ 分集循环：逐集隔离 + 静音写回（skip_profiles=True）
                ep_db = SessionLocal()
                try:
                    for i, ep in enumerate(episodes):
                        ep_id = ep.get("Id", "")
                        ep_name = ep.get("Name", "")
                        season_num = ep.get("ParentIndexNumber")
                        ep_num = ep.get("IndexNumber")

                        if not ep_id:
                            continue

                        # ★ 颗粒度进度反馈：防止大剧集分集循环导致前端看似卡死
                        if task_id:
                            try:
                                task_manager.update_progress(
                                    task_id,
                                    message=f"正在高速回写分集: {ep_name or '未命名'} ({i+1}/{len(episodes)})",
                                )
                            except Exception:
                                pass  # 进度上报失败不能影响主流程

                        # ★★★ 逐集 try/except 隔离：脏数据/单集异常绝不牵连整部剧 ★★★
                        try:
                            ep_people = ep.get("People", []) or []

                            # 7c. 中文化分集演员（含 AI 兜底 + 动态缓存）
                            localized_people = self._localize_episode_people(
                                ep_people, douban_match_map, series_name=item_name,
                            )

                            # ★ 分集也应用截断
                            localized_people = _truncate_actors(
                                localized_people, self.max_actors_per_media,
                            )

                            # 只写回有变更的分集（节省 API 调用）
                            if localized_people != ep_people:
                                write_ep_ok = self._write_back_episode(
                                    ep_id, ep, localized_people,
                                )
                                if write_ep_ok:
                                    total_episodes_synced += 1
                                    logger.debug(
                                        "   ✅ [Episode] S%02dE%02d '%s' 回写成功",
                                        season_num or 0, ep_num or 0, ep_name,
                                    )
                            else:
                                # 无变更也计入（演员无需汉化或已是中文）
                                total_episodes_synced += 1

                            # 7d. 持久化分集数据到 SQLite（skip_profiles=True 静音写入）
                            ep_pids = extract_provider_ids(ep)
                            chinese_ep, total_ep = _count_chinese_roles_ep(localized_people)
                            ep_status = "synced" if _is_chinese_role_synced_ep(localized_people) else "pending"

                            save_media_to_db(
                                ep_db,
                                emby_item=ep,
                                provider_ids=ep_pids,
                                images=None,
                                people=localized_people,
                                library_id=series_library_id,
                                status=ep_status,
                                matched_actors=chinese_ep,
                                total_actors=total_ep,
                                parent_id=item_id,
                                skip_profiles=True,  # ★ 已在循环外前置批处理，分集静音写入
                            )
                            ep_db.flush()
                            # ★ 逐集提交：单集失败不影响已成功的其他分集
                            ep_db.commit()
                            total_episodes_processed += 1

                        except Exception:
                            # ★★★ 逐集异常隔离：回滚当前分集，继续处理下一个 ★★★
                            logger.warning(
                                "   ⚠ [Douban/Series] 分集 '%s' (S%02dE%02d, ID=%s) 处理异常，跳过:\n%s",
                                ep_name or "?", season_num or 0, ep_num or 0,
                                ep_id, traceback.format_exc(),
                            )
                            try:
                                ep_db.rollback()
                            except Exception:
                                pass
                            continue

                    logger.info(
                        "   ✅ [Douban/Series] 分集处理完成: %d/%d 个分集已入库 (%d 已回写 Emby)",
                        total_episodes_processed, len(episodes), total_episodes_synced,
                    )
                except Exception as e:
                    # ★ 外层兜底：Session 创建失败等基础设施异常
                    try:
                        ep_db.rollback()
                    except Exception:
                        pass
                    logger.warning(
                        "   ⚠ [Douban/Series] 分集批量处理基础设施异常: %s\n%s",
                        e, traceback.format_exc(),
                    )
                finally:
                    ep_db.close()

        if write_ok:
            logger.info(
                "✅ [Douban中文化] 完成！共匹配 %d/%d 位演员 (顶层) | %d 个分集已处理",
                result["matched"], result["total_actors"], total_episodes_processed,
            )
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
    # 2. 豆瓣 ID 查找（★ 废弃 HTML 爬虫，全面接入 Frodo API）
    # ------------------------------------------------------------------

    def _find_douban_id(
        self, provider_ids: dict, title: str = "",
        mtype: str = "", year: str = "",
    ) -> Optional[str]:
        """利用 IMDb ID / TMDB→IMDb 桥接 / 名称搜索 精准匹配豆瓣条目 ID。

        三级漏斗策略（全部通过 DoubanApi.match_info Frodo API）:
          1. IMDb ID 直查 — 最精准，零歧义
          2. TMDB → IMDb 桥接 — 国产剧救星（无 IMDb 只有 TMDB 时）
          3. 名称 + 年份 + 类型搜索 — 兜底方案

        彻底废弃旧的 douban.com/search HTML 网页抓取逻辑。
        """
        from services.douban_api import DoubanApi

        imdb_id = provider_ids.get("Imdb", "").strip()
        tmdb_id = provider_ids.get("Tmdb", "").strip()

        cfg = load_config()
        tmdb_api_key = cfg.get("tmdb_api_key", "")
        tmdb_base = cfg.get("tmdb_base_url", "") or "https://api.tmdb.org/3"

        douban_api = DoubanApi()

        # ---- 策略 1: IMDb ID 直查 ----
        if imdb_id and imdb_id.startswith("tt"):
            logger.info("   🔍 [DoubanID] IMDb 直查: %s", imdb_id)
            try:
                result = douban_api.match_info(
                    name=title, imdbid=imdb_id, mtype=mtype, year=year,
                )
                if result.get("id"):
                    logger.info("   ✅ [DoubanID] IMDb → 豆瓣: %s", result["id"])
                    return result["id"]
                logger.warning(
                    "   ⚠ [DoubanID] IMDb %s 未匹配到豆瓣条目 (error=%s)",
                    imdb_id, result.get("error", "unknown"),
                )
            except Exception as e:
                logger.warning("   ⚠ [DoubanID] IMDb 直查异常: %s", e)

        # ---- 策略 2: TMDB → IMDb 桥接 ----
        if tmdb_id and tmdb_api_key:
            logger.info("   🔍 [DoubanID] TMDB→IMDb 桥接: tmdb=%s", tmdb_id)
            try:
                media_type = "tv" if mtype == "Series" else "movie"
                resp = requests.get(
                    f"{tmdb_base}/{media_type}/{tmdb_id}/external_ids",
                    params={"api_key": tmdb_api_key},
                    timeout=10,
                )
                if resp.status_code == 200:
                    ext = resp.json()
                    bridged_imdb = (ext.get("imdb_id") or "").strip()
                    if bridged_imdb and bridged_imdb.startswith("tt"):
                        logger.info(
                            "   🌉 [DoubanID] TMDB %s → IMDb %s",
                            tmdb_id, bridged_imdb,
                        )
                        result = douban_api.match_info(
                            name=title, imdbid=bridged_imdb,
                            mtype=mtype, year=year,
                        )
                        if result.get("id"):
                            logger.info(
                                "   ✅ [DoubanID] IMDb(桥接) → 豆瓣: %s",
                                result["id"],
                            )
                            return result["id"]
                        logger.warning(
                            "   ⚠ [DoubanID] 桥接 IMDb %s 未匹配到豆瓣条目",
                            bridged_imdb,
                        )
                    else:
                        logger.info(
                            "   ℹ️ [DoubanID] TMDB %s 无 IMDb 关联", tmdb_id,
                        )
                else:
                    logger.warning(
                        "   ⚠ [DoubanID] TMDB external_ids HTTP %d",
                        resp.status_code,
                    )
            except Exception as e:
                logger.warning("   ⚠ [DoubanID] TMDB→IMDb 桥接异常: %s", e)

        # ---- 策略 3: 名称 + 年份 + 类型搜索 ----
        if title:
            logger.info(
                "   🔍 [DoubanID] 名称搜索: %s (type=%s, year=%s)",
                title, mtype or "?", year or "?",
            )
            try:
                result = douban_api.match_info(
                    name=title, imdbid=None, mtype=mtype, year=year,
                )
                if result.get("id"):
                    logger.info(
                        "   ✅ [DoubanID] 名称搜索 → 豆瓣: %s (来源: %s)",
                        result["id"], result.get("source", "?"),
                    )
                    return result["id"]
                logger.warning(
                    "   ⚠ [DoubanID] 名称搜索未匹配: %s (error=%s)",
                    title, result.get("error", "unknown"),
                )
            except Exception as e:
                logger.warning("   ⚠ [DoubanID] 名称搜索异常: %s", e)

        logger.warning(
            "   ❌ [DoubanID] 三级策略均失败: title=%s imdb=%s tmdb=%s",
            title, imdb_id or "-", tmdb_id or "-",
        )
        return None

    def _fetch_douban_actors(self, douban_id: str) -> list[dict]:
        """抓取豆瓣演职员数据，优先用 Frodo API。"""
        # 策略 A: Frodo API (与参考程序一致)
        actors = self._fetch_actors_frodo(douban_id)
        if actors:
            return actors[:MAX_ACTORS] if MAX_ACTORS < 900 else actors

        # 策略 B: 网页抓取 (fallback)
        url = DOUBAN_SUBJECT_URL.format(douban_id=douban_id)
        logger.info(f"   🌐 [fallback] 正在请求豆瓣页面: {url}")
        resp = self._http_get(url)
        if not resp or resp.status_code != 200:
            logger.error(f"❌ [豆瓣抓取] HTTP {resp.status_code if resp else 'N/A'}")
            return []
        soup = BeautifulSoup(resp.text, "lxml")
        actors = self._parse_celebrities_page(soup)
        return actors[:MAX_ACTORS] if MAX_ACTORS < 900 else actors

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
                    # ★ 提取豆瓣演员 ID，用于后续 L1 漏斗精准查询
                    celebrity_id = str(item.get("id", "") or "")
                    # 角色名 — 参考程序用 clean_character_name_static
                    role = item.get("character", "")
                    if not role and item.get("attrs", {}).get("role"):
                        roles = item["attrs"]["role"]
                        if isinstance(roles, list) and roles:
                            role = " / ".join(r for r in roles if isinstance(r, str))
                    role = self._clean_role(role) if role else ""
                    # ★ 提取豆瓣头像外链（avatar.large → avatar.normal 降级）
                    avatar_url = ""
                    avatar_data = item.get("avatar") or {}
                    if isinstance(avatar_data, dict):
                        avatar_url = avatar_data.get("large") or avatar_data.get("normal") or ""
                    actors.append({
                        "name": name, "role": role,
                        "avatar": avatar_url,
                        "id": celebrity_id,  # ★ 豆瓣演员 ID（关键锚点）
                    })
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
        """从单个演员 DOM 节点中提取中文名、角色名和豆瓣 ID。"""
        name = None
        role = None
        douban_id = ""

        name_el = (
            item.select_one(".name a, .name")
            or item.select_one("a.name")
            or item.select_one("span.name")
            or item.select_one("a[href*='/celebrity/']")
            or item.select_one(".info .name")
        )
        if name_el:
            name = name_el.get_text(strip=True)
            # ★ 从链接中提取豆瓣演员 ID
            if name_el.name == "a" and name_el.get("href"):
                import re as _re
                m = _re.search(r'/celebrity/(\d+)', name_el.get("href", ""))
                if m:
                    douban_id = m.group(1)

        if not name:
            link = item.select_one("a[href*='/celebrity/']")
            if link:
                name = link.get("title") or link.get_text(strip=True)
                import re as _re
                m = _re.search(r'/celebrity/(\d+)', link.get("href", ""))
                if m:
                    douban_id = m.group(1)

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

        return {"name": name, "role": role or "", "id": douban_id}

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
                # ★ 注入豆瓣头像外链，供后续 actor_profile_service 超级漏斗短路使用
                new_entry["DoubanAvatarUrl"] = matched_da.get("avatar", "")
                # ★ 注入豆瓣演员 ID（关键锚点），供后续 L1 漏斗精准调用 celebrity_details
                douban_id_str = str(matched_da.get("id", "") or "")
                if douban_id_str:
                    new_entry["DoubanCelebrityId"] = douban_id_str
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
                "DoubanAvatarUrl": da.get("avatar", ""),
            }
            # ★ 注入豆瓣演员 ID，供 L1 漏斗使用
            douban_id_str = str(da.get("id", "") or "")
            if douban_id_str:
                new_actor["DoubanCelebrityId"] = douban_id_str
            updated.append(new_actor)
            logger.info(f"   ➕ [新增] {da['name']} | 角色: {da['role']}")
            details.append({
                "index": len(details), "emby_name": "", "douban_name": da["name"],
                "old_name": "", "new_name": da["name"], "old_role": "", "new_role": da["role"],
                "matched": True, "level": "豆瓣新增",
            })

        # ★ 空角色强制兜底：Series 层所有匹配/AI 翻译完成后，
        # Role 仍为空的 Actor 统一设为 "演员"
        for a in updated:
            if a.get("Type") == "Actor" and not (a.get("Role") or "").strip():
                a["Role"] = "演员"

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
    # 5b. AI 批量推理缺失角色名
    # ------------------------------------------------------------------

    def _infer_missing_roles_via_ai(
        self, title: str, year, actors_list: list,
    ) -> dict:
        """利用 LLM 影视知识批量推理缺失的角色名。

        当豆瓣缺失角色信息时，大模型本身具备丰富的影视知识储备。
        通过"剧名 + 年份 + 演员列表"向 AI 发起批量询问，精准推理角色名。

        Args:
            title:       剧名
            year:        年份 (ProductionYear)
            actors_list: 缺失角色名的演员名字列表

        Returns:
            {actor_name: role_name} 字典。推理失败或不确定时返回 {}。
        """
        if not actors_list or not title:
            return {}

        cfg = load_config()
        api_key = (cfg.get("sf_api_key") or "").strip()
        if not api_key:
            logger.info("   ℹ️ [AI推理] 未配置 sf_api_key，跳过角色推理")
            return {}

        from openai import OpenAI as OpenAIClient
        base_url = (cfg.get("llm_base_url") or "").strip()
        model = (cfg.get("llm_model_name") or "").strip() or "deepseek-ai/DeepSeek-V3"
        client_kwargs = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        ai_client = OpenAIClient(**client_kwargs)

        # 年份弱化为模糊参考，避免元数据错误（如未来年份）误导模型检索
        year_ref = f"{year}年前后" if year else ""

        # ★ 防幻觉 Prompt：允许说"未知"，但严禁编造
        system_msg = (
            "你是一个严谨的中文影视剧资料库专家。"
            "你必须严格基于你的确切知识回答，绝对不允许编造或猜测角色名。"
            "如果你十分确定演员在剧中的角色，给出具体角色名；"
            "如果你不知道、不确定、或该剧超出你的知识库范围，请务必返回\"未知\"。"
            "你必须只输出一个合法的 JSON 对象，不要包含任何解释、Markdown 或额外文字。"
        )
        user_msg = (
            f"请回忆电视剧《{title}》"
            + (f"（参考年份：{year_ref}）" if year_ref else "")
            + f"的演员表。\n"
            f"我需要你告诉我以下 {len(actors_list)} 位演员在该剧中分别饰演了什么角色。\n\n"
            f"【严重警告】请严格基于你的确切知识回答。\n"
            f"如果你十分确定，请给出具体的剧中角色名；\n"
            f"如果你不知道、不确定，或者该剧超出你的知识库范围，请务必返回 \"未知\"，\n"
            f"绝对不允许编造或猜测角色名！\n\n"
            f"待查询演员列表：\n"
            + "\n".join(f"  {i+1}. {name}" for i, name in enumerate(actors_list))
            + "\n\n请严格仅返回合法的 JSON 格式数据，键为演员原名，值为剧中角色名（或\"未知\"）："
        )

        import json as _json

        # ★ 打印完整请求，方便排查 LLM 为什么推理不到角色
        #logger.info(
         #   "   🧠 [AI推理] 发送请求 → model=%s | 剧名=%s | 演员(%d人): %s",
        #    "deepseek-ai/DeepSeek-V3", title, len(actors_list),
        #    ", ".join(actors_list[:10]) + (f" ...等{len(actors_list)}人" if len(actors_list) > 10 else ""),
        #)
        #logger.debug("   🧠 [AI推理] system: %s", system_msg[:200])
        #logger.debug("   🧠 [AI推理] user: %s", user_msg[:500])

        content = ""
        try:
            # ★ 429 限流智能重试：遇限频自动冷却 5s 再试一次
            last_exc = None
            for attempt in (1, 2):
                try:
                    response = ai_client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": system_msg},
                            {"role": "user", "content": user_msg},
                        ],
                        temperature=0.2,
                        max_tokens=2000,
                    )
                    content = response.choices[0].message.content or "{}"
                    break
                except Exception as e:
                    last_exc = e
                    if attempt == 1 and _is_rate_limit_error(e):
                        logger.warning(
                            "   ⚠️ [429限流] AI推理请求被限频，冷却 5s 后重试（第 1 次）…"
                        )
                        time.sleep(5.0)
                        continue
                    raise last_exc
            # ★ 打印 AI 原始返回
            #logger.info("   🧠 [AI推理] 原始返回(前500字): %s", content[:500])
            # 清理可能包裹的 Markdown 代码块
            cleaned = content.replace("```json", "").replace("```", "").strip()

            # 尝试多种解析策略
            result = None
            # 策略 1: 直接 JSON 解析
            try:
                result = _json.loads(cleaned)
            except _json.JSONDecodeError:
                pass

            # 策略 2: 尝试从文本中提取 JSON 对象
            if result is None or not isinstance(result, dict):
                import re as _re
                m = _re.search(r'\{[^{}]*\}', cleaned)
                if m:
                    try:
                        result = _json.loads(m.group())
                    except _json.JSONDecodeError:
                        pass

            # 策略 3: 逐行解析 "演员名：角色名" 格式
            if result is None or not isinstance(result, dict):
                result = {}
                for line in cleaned.split("\n"):
                    line = line.strip().strip("- ").strip()
                    for sep in ("：", ":", "→", "->", " - ", "  "):
                        if sep in line:
                            parts = line.split(sep, 1)
                            key = parts[0].strip().strip("\"'")
                            val = parts[1].strip().strip("\"'")
                            if key and val:
                                result[key] = val
                            break

            if not isinstance(result, dict):
                logger.warning(
                    "   ⚠ [AI推理] 无法解析为字典 | 原始返回(前300字): %s",
                    content[:300],
                )
                return {}

            # ★ 过滤返回值："未知" 绝不入库，留给空角色兜底逻辑赋 "演员"
            filtered = {}
            skipped_unknown = 0
            for k, v in result.items():
                if not isinstance(v, str):
                    continue
                v_stripped = v.strip().strip("。，. ,\"'")
                if not v_stripped:
                    continue
                if v_stripped in ("未知", "演员", "无", "(无)", "不详", "暂无", "N/A", "null"):
                    skipped_unknown += 1
                    continue
                filtered[k.strip()] = v_stripped

            if skipped_unknown:
                logger.info(
                    "   🧠 [AI推理] %d 个演员 AI 返回\"未知\" → 交由空角色兜底赋 \"演员\"",
                    skipped_unknown,
                )

            if filtered:
                logger.info(
                    "   🧠 [AI推理] 成功推断 %d/%d 个角色: %s",
                    len(filtered), len(actors_list),
                    ", ".join(
                        f"{k}→{v}"
                        for k, v in list(filtered.items())[:10]
                    ),
                )
            else:
                # ★ 打印原始返回，方便排查
                logger.warning(
                    "   ⚠ [AI推理] %d 个演员均无法推断角色（%d 个为\"未知\"）| AI 原始返回(前500字): %s",
                    len(actors_list), skipped_unknown, content[:500],
                )

            return filtered

        except _json.JSONDecodeError as e:
            logger.warning(
                "   ⚠ [AI推理] JSON 解析失败: %s | 原始返回(前300字): %s",
                e, content[:300] if content else 'N/A',
            )
            return {}
        except Exception as e:
            logger.warning(
                "   ⚠ [AI推理] 异常: %s | 原始返回(前300字): %s",
                e, content[:300] if content else 'N/A',
            )
            return {}
        finally:
            _rate_limit_sleep("[AI推理]")

    # ------------------------------------------------------------------
    # 5c. 分集 (Episode) 抓取与中文化
    # ------------------------------------------------------------------

    def _fetch_episodes(self, series_id: str) -> list:
        """获取指定 Series 下的所有分集（含分页保护）。

        学习自 emby-toolkit 的 get_series_children() 模式:
        - 使用 Recursive=true 一次性获取所有层级的分集
        - Limit=10000 避免大剧集被截断
        """
        base = f"{self.emby_host}/emby/Users/{self.emby_user_id}/Items" if self.emby_user_id else f"{self.emby_host}/emby/Items"
        all_episodes = []
        start_index = 0
        page_size = 100

        while True:
            params = {
                "api_key": self.emby_api_key,
                "ParentId": series_id,
                "IncludeItemTypes": "Episode",
                "Recursive": "true",
                "Fields": "People,ProviderIds,Overview,ProductionYear,RecursiveItemCount,ParentIndexNumber,IndexNumber,LockData,LockedFields",
                "StartIndex": start_index,
                "Limit": page_size,
            }
            try:
                resp = requests.get(base, params=params, timeout=30)
                if resp.status_code != 200:
                    logger.warning(
                        "   ⚠ [Douban/Episodes] 获取 Series %s 分集失败: HTTP %d",
                        series_id, resp.status_code,
                    )
                    break
                data = resp.json()
                items = data.get("Items", [])
                if not items:
                    break
                all_episodes.extend(items)
                total = data.get("TotalRecordCount", 0)
                if start_index + page_size >= total:
                    break
                start_index += page_size
            except Exception as e:
                logger.warning(
                    "   ⚠ [Douban/Episodes] 获取 Series %s 分集异常: %s",
                    series_id, e,
                )
                break

        return all_episodes

    def _localize_episode_people(
        self, ep_people: list, douban_match_map: dict,
        series_name: str = "",
    ) -> list:
        """对分集演员列表中文化替换（含 AI 兜底 + 动态缓存）。

        三级漏斗策略：
        a. 全量字典匹配：先查 douban_match_map（豆瓣全量演员）
        b. AI 兜底翻译：未命中时调 AI translate_names / translate_roles
        c. 动态缓存学习：AI 成功结果即时写入 douban_match_map

        Args:
            ep_people:        Emby 分集的 People 列表
            douban_match_map: {emby_name_lower: {"name": "豆瓣中文名", "role": "豆瓣角色"}}
                              此字典会被原地修改（引用传递），用于跨分集缓存
            series_name:      剧集名称，作为 AI 翻译上下文

        Returns:
            中文化后的 People 列表
        """
        # 收集本轮需要 AI 翻译的未命中项
        _pending_ai_names = {}
        _pending_ai_roles = {}

        localized = []
        for p in ep_people:
            person_type = p.get("Type", "Actor")
            if person_type not in ("Actor", "GuestStar"):
                localized.append(p)
                continue

            emby_name = (p.get("Name") or "").strip()
            emby_role = (p.get("Role") or "").strip()
            lookup_key = emby_name.lower()

            if lookup_key in douban_match_map:
                # ---- 漏斗 a: 全量字典命中 ----
                info = douban_match_map[lookup_key]
                new_p = dict(p)
                # ★ 注入豆瓣头像外链，供 actor_profile_service 超级漏斗短路使用
                new_p["DoubanAvatarUrl"] = info.get("avatar", "")
                # ★ 注入豆瓣演员 ID，使 L1 漏斗能精准调用 celebrity_details
                douban_id_str = str(info.get("douban_id", "") or "")
                if douban_id_str:
                    new_p["DoubanCelebrityId"] = douban_id_str
                db_name = info.get("name", "")
                if db_name and not self._is_chinese(emby_name):
                    new_p["Name"] = db_name
                db_role = info.get("role", "")
                if db_role and db_role not in ("演员", "配音", "actor", "actress"):
                    new_p["Role"] = db_role
                localized.append(new_p)
            else:
                # ---- 漏斗 b: 标记待 AI 翻译 ----
                if emby_name and not self._is_chinese(emby_name):
                    _pending_ai_names[lookup_key] = emby_name
                if emby_role and not self._is_chinese(emby_role):
                    _pending_ai_roles[lookup_key] = emby_role
                localized.append(p)

        # ---- 漏斗 b/c: AI 批量翻译 + 缓存回写 ----
        if _pending_ai_names or _pending_ai_roles:
            translator = get_translator()
            if translator.is_available():
                # b1. 批量翻译人名
                if _pending_ai_names:
                    unique_names = list(set(_pending_ai_names.values()))
                    try:
                        name_map = translator.translate_names(unique_names, context=series_name)
                        for lookup_key, original_name in _pending_ai_names.items():
                            translated = name_map.get(original_name, "")
                            if translated and self._is_chinese(translated):
                                douban_match_map.setdefault(lookup_key, {})["name"] = translated
                                logger.debug(
                                    "   🤖 [AI缓存] 人名: %s → %s", original_name, translated,
                                )
                    except Exception:
                        logger.debug("   ⚠ [AI] 批量人名翻译异常，跳过")

                # b2. 批量翻译角色名
                if _pending_ai_roles:
                    unique_roles = list(set(_pending_ai_roles.values()))
                    unique_roles = [
                        r for r in unique_roles
                        if r and r.lower() not in ("actor", "actress", "guest", "guest star", "unknown")
                    ]
                    if unique_roles:
                        try:
                            role_map = translator.translate_roles(unique_roles, context=series_name)
                            for lookup_key, original_role in _pending_ai_roles.items():
                                translated = role_map.get(original_role, "")
                                if translated and self._is_chinese(translated):
                                    douban_match_map.setdefault(lookup_key, {})["role"] = translated
                                    logger.debug(
                                        "   🤖 [AI缓存] 角色: %s → %s", original_role, translated,
                                    )
                        except Exception:
                            logger.debug("   ⚠ [AI] 批量角色翻译异常，跳过")

                # b3. 用更新后的 douban_match_map 重新应用翻译
                if _pending_ai_names or _pending_ai_roles:
                    for i, p in enumerate(localized):
                        person_type = p.get("Type", "Actor")
                        if person_type not in ("Actor", "GuestStar"):
                            continue
                        emby_name = (p.get("Name") or "").strip()
                        lookup_key = emby_name.lower()
                        if lookup_key in douban_match_map:
                            info = douban_match_map[lookup_key]
                            new_p = dict(p)
                            # ★ 注入豆瓣头像外链
                            new_p["DoubanAvatarUrl"] = info.get("avatar", "")
                            # ★ 注入豆瓣演员 ID，使 L1 漏斗能精准调用 celebrity_details
                            douban_id_str = str(info.get("douban_id", "") or "")
                            if douban_id_str:
                                new_p["DoubanCelebrityId"] = douban_id_str
                            db_name = info.get("name", "")
                            if db_name and not self._is_chinese(emby_name):
                                new_p["Name"] = db_name
                            db_role = info.get("role", "")
                            if db_role and db_role not in ("演员", "配音", "actor", "actress"):
                                new_p["Role"] = db_role
                            localized[i] = new_p

        # ★ 空角色强制兜底：所有处理完毕后，仍为空的 Role 默认赋 "演员"
        for i, p in enumerate(localized):
            if p.get("Type", "") in ("Actor", "GuestStar"):
                if not (p.get("Role") or "").strip():
                    new_p = dict(p)
                    new_p["Role"] = "演员"
                    localized[i] = new_p

        return localized

    def _build_douban_match_map(
        self, douban_actors: list, emby_actors: list
    ) -> dict:
        """从豆瓣匹配结果构建 {emby_name_lower: douban_info} 映射表。

        匹配策略（与 _match_and_update 一致）：
        1. 直接中文名相等
        2. 拼音归一化匹配
        3. 拼音子串匹配

        Returns:
            {emby_name_lower: {"name": "豆瓣名", "role": "豆瓣角色"}}
        """
        actor_map = {}
        douban_names = {da.get("name", "") for da in douban_actors}
        used_douban = set()

        for ea in emby_actors:
            emby_name = (ea.get("Name") or "").strip()
            if not emby_name:
                continue

            matched_da = None

            # Level 1: 直接中文名匹配
            if emby_name in douban_names:
                for da in douban_actors:
                    if da.get("name") == emby_name and da["name"] not in used_douban:
                        matched_da = da
                        break

            # Level 2: 拼音降级匹配
            if not matched_da:
                emby_key = self._normalize_english_name(emby_name)
                for da in douban_actors:
                    if da.get("name") in used_douban:
                        continue
                    py_key = self._to_pinyin_key(da.get("name", ""))
                    if emby_key == py_key:
                        matched_da = da
                        break

                # Level 3: 拼音子串匹配
                if not matched_da:
                    for da in douban_actors:
                        if da.get("name") in used_douban:
                            continue
                        py_key = self._to_pinyin_key(da.get("name", ""))
                        if len(emby_key) >= 4 and len(py_key) >= 4:
                            if emby_key in py_key or py_key in emby_key:
                                matched_da = da
                                break

            if matched_da:
                used_douban.add(matched_da["name"])
                actor_map[emby_name.lower()] = {
                    "name": matched_da.get("name", ""),
                    "role": matched_da.get("role", ""),
                    "avatar": matched_da.get("avatar", ""),
                    "douban_id": str(matched_da.get("id", "") or ""),
                }

        return actor_map

    def _write_back_episode(
        self, episode_id: str, episode_data: dict, people: list
    ) -> bool:
        """将中文化后的 People 写回单个 Emby 分集。

        与 _write_back_emby 逻辑一致，但针对分集 Item。
        """
        url = f"{self.emby_host}/emby/Items/{episode_id}"
        headers = {
            "X-Emby-Token": self.emby_api_key,
            "Content-Type": "application/json",
        }

        update_data = dict(episode_data)
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

        try:
            resp = requests.post(
                url, json=update_data, headers=headers,
                params={"api_key": self.emby_api_key}, timeout=15,
            )
            if resp.status_code in (200, 204):
                return True
            else:
                logger.warning(
                    "   ⚠ [Douban/Episode] 回写 Episode %s 失败: HTTP %d",
                    episode_id, resp.status_code,
                )
                return False
        except Exception as e:
            logger.warning(
                "   ⚠ [Douban/Episode] 回写 Episode %s 异常: %s",
                episode_id, e,
            )
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
