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
