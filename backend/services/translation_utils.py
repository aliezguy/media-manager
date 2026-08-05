"""中文化文本校验工具 — 纯净缓存防伪污染判据。

防止「伪中文（英文原名）」污染缓存：官方 API / AI 返回的译名，
必须经 is_valid_chinese_translation 校验（含至少一个中文字符）才允许回写。
"""
import re

# 中文字符（CJK 统一表意文字，含繁体），与项目现有 [一-鿿] 范围一致
_CHINESE_RE = re.compile(r"[一-鿿]")

# ---- 翻译来源 ----
SOURCE_MANUAL = "manual"           # UI 手动修改（预留）
SOURCE_OFFICIAL = "official"       # TMDB / 豆瓣 官方 API（且包含中文）
SOURCE_AI_FALLBACK = "ai_fallback" # 官方查了但无中文/是拼音，靠 AI 兜底
SOURCE_AI_DIRECT = "ai_direct"     # 无官方数据，直接扔给 AI 翻译
SOURCE_LOCAL_LLM = "local_llm"     # 本地大模型(qwen2.5)翻译（全库简介汉化）
SOURCE_CLOUD_LLM = "cloud_llm"     # 云端 API 兜底翻译（全库简介汉化）

# AI 翻译来源集合 — 防覆盖守卫只在存在这些标记时生效
AI_SOURCES = (SOURCE_LOCAL_LLM, SOURCE_CLOUD_LLM)

# ---- 置信度（值越大越可信；只有更高值才能覆盖更低值） ----
CONFIDENCE_MANUAL = 5        # 手动修改（最高信任）
CONFIDENCE_OFFICIAL = 4      # 官方 API 且含中文
CONFIDENCE_AI_FALLBACK = 3   # 官方无中文后 AI 兜底
CONFIDENCE_AI_DIRECT = 2     # 纯 AI 直出（无官方数据）
CONFIDENCE_NONE = 1          # 未执行翻译（已是中文/无来源）

# 跨剧集/跨集直接复用的最低门槛：只要翻译过（>=2）就先复用，
# 低置信度随时可被更高置信度覆盖升级
CONFIDENCE_REUSE_THRESHOLD = 2


def is_valid_chinese_translation(text) -> bool:
    """判断字符串是否包含至少一个中文字符。

    官方 API / AI 的译名在回写前必须通过此校验：
    全部为英文或非中文字符（说明官方无中文 / AI 未正确汉化）时返回 False，
    调用方应【直接丢弃】该结果并继续降级。
    """
    if not text or not isinstance(text, str):
        return False
    return bool(_CHINESE_RE.search(text))


# ---------------------------------------------------------------------------
# 全库简介汉化 — 中文比例检测 + 长文本有效性验收 + 防覆盖守卫
# ---------------------------------------------------------------------------

def chinese_char_ratio(text) -> float:
    """中文(CJK)字符占「非空白有效字符」比例。

    空串 / 非字符串 → 0.0。分母只统计非空白字符，避免长串英文/数字
    让中文占比被稀释失真。
    """
    if not text or not isinstance(text, str):
        return 0.0
    meaningful = [c for c in text if not c.isspace()]
    if not meaningful:
        return 0.0
    han = sum(1 for c in meaningful if _CHINESE_RE.fullmatch(c))
    return han / len(meaningful)


def is_already_chinese(text, ratio: float = 0.5) -> bool:
    """判定字符串是否已含「足够比例」的中文字符 → 全局检测跳过用。

    空 / 非字符串 → False（空内容由调用方另行跳过，不视为已中文）。
    中文占比 ≥ ratio 才判定为已中文化；纯英文夹个别汉字不算。
    """
    if not text or not isinstance(text, str):
        return False
    return chinese_char_ratio(text) >= ratio


# 重复幻觉模式：
# 1. 同一字符连续 8+ 次（如 "好好好好好好好好…"）
# 2. 12+ 字符的块出现 3+ 次（如某句简介被模型复读）
_REPETITION_CHAR_RE = re.compile(r"(.)\1{7,}")
_REPETITION_BLOCK_RE = re.compile(r"(.{12,}).*?\1.*?\1", re.DOTALL)


def is_valid_overview_translation(text, min_ratio: float = 0.2) -> bool:
    """长文本中文有效性验收 — 翻译输出回写前的最终闸门。

    三重判据（全部满足才算有效）：
    1. is_valid_chinese_translation 成立（≥1 中文字符）；
    2. 中文占比 ≥ min_ratio —— 拦截「纯英文夹 1 个汉字」的伪中文；
    3. 无重复幻觉 —— 拦截同字符连续复读 / 整块复读（大模型常见幻觉）。

    Returns:
        True: 有效中文简介，允许回写；False: 仍是外语/乱码/幻觉，调用方应丢弃并降级。
    """
    if not is_valid_chinese_translation(text):
        return False
    if chinese_char_ratio(text) < min_ratio:
        return False
    if _REPETITION_CHAR_RE.search(text):
        return False
    if _REPETITION_BLOCK_RE.search(text):
        return False
    return True


def should_protect_overview(existing_source, incoming_overview) -> bool:
    """防覆盖守卫判据 — 已入库的 AI 中文简介禁止被非中文新值覆盖。

    - existing_source ∈ (local_llm, cloud_llm)：说明库里的中文是本地/云端 AI 翻译产物；
    - incoming_overview 非中文（未通过 is_valid_chinese_translation）：
      说明官方（Emby/TMDB）这次推来的仍是外文，覆盖会毁掉已汉化的简介。
    两者同时成立 → True（拒绝覆盖）。
    其余情况（官方推纯中文、来源非 AI、或空来源）→ False（允许正常同步覆盖）。
    """
    if existing_source not in AI_SOURCES:
        return False
    return not is_valid_chinese_translation(incoming_overview)


def apply_overview_with_guard(rec, incoming_overview) -> bool:
    """对 SQLAlchemy 行执行带守卫的 overview 写入。

    - 被守卫拦截 → 返回 False，rec.overview / rec.overview_source 保持原值不变；
    - 允许写入 → rec.overview = incoming；rec.overview_source = SOURCE_OFFICIAL
      （incoming 为有效中文，官方同步产物）否则 ""（仍是外文官方数据）。
    返回是否真正写入。

    rec 只需具备 overview / overview_source 两个可写属性（duck-typed，
    不依赖具体 ORM，便于单测用 SimpleNamespace 模拟）。
    """
    incoming = (incoming_overview or "").strip()
    existing_source = (getattr(rec, "overview_source", None) or "")
    if should_protect_overview(existing_source, incoming):
        return False
    rec.overview = incoming
    rec.overview_source = SOURCE_OFFICIAL if is_valid_chinese_translation(incoming) else ""
    return True
