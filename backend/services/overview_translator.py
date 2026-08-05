"""全库媒体简介（Overview）本地化翻译 — 双重引擎降级 + 全库扫描。

【处理链路】
1. 全局语言检测（needs_overview_translation）：空 / 已含足够比例中文 → 跳过；
   其余非中文（英/日/韩/小语种）→ 入队翻译。
2. 一级引擎：本地大模型（ollama qwen2.5）优先，经 ai_translator.chat_null_aware
   （local_first=True + validator 中文验收门）调用；请求含严格超时（SDK timeout）
   与异常捕获，长文本推理不会卡死主线程。
3. 二级引擎：本地返回 NULL / 空 / 连接失败 / 超时 / 未过中文校验（仍是外语、乱码、
   重复幻觉）→ 自动无缝降级到云端 API（DeepSeek / 智谱等，按 ai_providers 顺序）。
4. 状态回写：翻译成功并通过 is_valid_overview_translation → 更新 media_metadata.overview，
   并记录 overview_source = local_llm / cloud_llm 审计标记（另由防覆盖守卫保护
   不被 Emby/TMDB 重新同步的非中文新值覆盖）。

【防伪红线】翻译输出必须过 is_valid_overview_translation（中文占比 + 无重复幻觉），
不满足一律视为失败并继续降级，绝不回写「伪中文」。
"""
import logging
from datetime import datetime
from typing import Optional, Tuple

from config.settings import load_config
from services.ai_translator import AITranslator, get_translator
from services.translation_utils import (
    is_already_chinese,
    is_valid_overview_translation,
)

logger = logging.getLogger("uvicorn")

# 用户指定的专业翻译 System Prompt
_OVERVIEW_SYSTEM_PROMPT = (
    "你是一个专业的影视翻译。请将以下影视简介翻译为流畅的中文，保持原意，"
    "要求信达雅。不要输出任何多余的解释、标点或外语原文。"
)

_DEFAULT_MAX_TOKENS = 1500


# ---------------------------------------------------------------------------
# 全局语言检测拦截（无视媒体分类）
# ---------------------------------------------------------------------------

def needs_overview_translation(text, ratio: float = 0.5) -> bool:
    """判定 overview 是否进入待翻译队列。

    - 空 / 纯空白 / 非字符串 → False（跳过，不生成简介）；
    - 已含足够比例中文 → False（跳过）；
    - 其余非中文（英/日/韩等）→ True（入队翻译）。
    """
    if not text or not isinstance(text, str):
        return False
    if not text.strip():
        return False
    return not is_already_chinese(text, ratio=ratio)


# ---------------------------------------------------------------------------
# 双重引擎翻译（本地 qwen → 云端兜底）
# ---------------------------------------------------------------------------

def translate_overview(text, cfg=None, skip: Optional[set] = None) -> Tuple[Optional[str], str, set]:
    """翻译一条外文简介：本地大模型优先，中文校验不过 → 云端 API 兜底。

    Args:
        text: 待翻译的 overview 原文
        cfg:  配置 dict（读 overview_local_first / overview_max_tokens）；
              默认 load_config()
        skip: 按 model_name 跳过 Provider（本批已「无效/NULL」的模型不重复试）

    Returns:
        (translated, source, null_models)
        - translated: 通过中文校验的译文；全引擎失败 → None
        - source:     "local_llm"（本地 qwen）/ "cloud_llm"（云端兜底）/ "failed"
        - null_models: 本轮尝试但失败/无效的 model_name 集合（调用方可回传为 skip）
    """
    cfg = cfg if cfg is not None else load_config()
    content, model, nulls = get_translator().chat_null_aware(
        system_prompt=_OVERVIEW_SYSTEM_PROMPT,
        user_prompt=text,  # 用户提示即原文，无多余包装
        temperature=0.3,
        max_tokens=cfg.get("overview_max_tokens", _DEFAULT_MAX_TOKENS),
        local_first=bool(cfg.get("overview_local_first", True)),
        skip=skip,
        validator=is_valid_overview_translation,  # 中文有效性验收门（本地→云端降级依据）
    )
    if not content:
        return None, "failed", nulls
    # 来源审计：按成功模型名判本地/云端（复用 ai_translator 本地标记判据）
    source = (
        "local_llm" if AITranslator._is_local_provider({"model_name": model})
        else "cloud_llm"
    )
    return content, source, nulls


# ---------------------------------------------------------------------------
# 全库扫描 + 回写
# ---------------------------------------------------------------------------

def _select_rows(db, media_type=None, library_ids=None):
    """按 media_type / library_ids 过滤出 media_metadata 全部行。

    - media_type: 可选（Movie / Series / Episode）；None = 全部
    - library_ids: 可选媒体库过滤；经 MediaSyncStatus.library_id 桥接
      （media_metadata 无 library_id 列）；None/空 = 全库
    """
    from models import MediaMetadata, MediaSyncStatus

    q = db.query(MediaMetadata)
    if media_type:
        q = q.filter(MediaMetadata.media_type == media_type)
    if library_ids:
        lib_ids = [str(x) for x in library_ids if str(x).strip()]
        if lib_ids:
            sub = db.query(MediaSyncStatus.emby_item_id).filter(
                MediaSyncStatus.library_id.in_(lib_ids)
            )
            q = q.filter(MediaMetadata.emby_item_id.in_(sub))
    return q.all()


def count_pending_overviews(db, media_type=None, library_ids=None) -> int:
    """预扫统计：当前过滤条件下有多少条 overview 待翻译（供任务进度 total）。"""
    return sum(
        1 for r in _select_rows(db, media_type=media_type, library_ids=library_ids)
        if needs_overview_translation(r.overview)
    )


def scan_and_translate(db, media_type=None, library_ids=None, task_id=None) -> dict:
    """全库扫描非中文 overview 并翻译回写（仅 media_metadata 单表）。

    - media_type: 可选过滤（Movie / Series / Episode）；None = 全部
    - library_ids: 可选媒体库过滤；经 MediaSyncStatus.library_id 桥接
    - task_id: 给定时上报 task_manager 进度
    - 逐行翻译，单行 commit（失败 rollback 保留原值，绝不影响其他行）

    Returns:
        {"total_media", "targeted", "translated", "skipped", "failed"}
    """
    from utils.task_manager import task_manager

    cfg = load_config()

    rows = _select_rows(db, media_type=media_type, library_ids=library_ids)
    targets = [r for r in rows if needs_overview_translation(r.overview)]
    total_media = len(rows)
    targeted = len(targets)

    stats = {
        "total_media": total_media,
        "targeted": targeted,
        "translated": 0,
        "skipped": total_media - targeted,
        "failed": 0,
    }

    if task_id:
        task_manager.update_progress(
            task_id, total=targeted,
            message=f"发现 {targeted} 条非中文简介，开始翻译...",
        )
    if targeted == 0:
        logger.info("   ℹ️ [Overview] 全库扫描无待翻译简介（%d 条媒体）", total_media)
        return stats

    logger.info(
        "   🚀 [Overview] 全库扫描: 共 %d 条媒体，%d 条待翻译", total_media, targeted,
    )
    for idx, rec in enumerate(targets):
        current = idx + 1
        try:
            translated, source, _nulls = translate_overview(rec.overview, cfg=cfg)
            if translated:
                rec.overview = translated
                rec.overview_source = source
                rec.overview_updated_at = datetime.now()
                db.commit()
                stats["translated"] += 1
                logger.info(
                    "   ✅ [Overview] %s (%s) → source=%s",
                    rec.emby_item_id, rec.media_type, source,
                )
            else:
                db.rollback()
                stats["failed"] += 1
                logger.warning(
                    "   ⚠️ [Overview] %s 全部引擎失败/未过中文校验，保留原值", rec.emby_item_id,
                )
        except Exception:
            db.rollback()
            stats["failed"] += 1
            logger.error(
                "   ❌ [Overview] %s 翻译异常，保留原值", rec.emby_item_id, exc_info=True,
            )
        if task_id:
            task_manager.update_progress(
                task_id, current=current, message=f"翻译中 {current}/{targeted}: {rec.title or rec.emby_item_id}",
            )

    logger.info("   📊 [Overview] 扫描完成: %s", stats)
    return stats
