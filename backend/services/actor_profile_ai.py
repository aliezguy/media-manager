"""演员元数据 AI 补全/汉化 — 出生地汉化 + 空值补全（本地 qwen 优先 + 严格防伪 NULL）。

【流程顺序（严格）】由 actor_profile_service.resolve_actor_profile 在 L0 本地 → L0.5 Emby
→ L1 豆瓣 → L2 TMDB 收集完毕之后调用；仍为空 / 非中文的字段才进入本模块：
    出生地: 非空非中文 → 地理翻译；空 → 按演员知识生成（strict-NULL）
    简介:   空 → 生成；非中文 → 翻译；中文 → 原样保留
    生日:   已有 → 保留；空且有简介 → 从简介提取
全部经 ai_translator.chat_null_aware(local_first=True)：本地大模型 qwen2.5 最先尝试，
返回 NULL/失败再无缝降级到其他 Provider。

【防伪红线】LLM 返回空 / "NULL" / "未知" / "无" 一律视为无数据：
- 绝不覆盖已有值（翻译失败保留原值；生成失败保持空）；
- 绝不无中生有（回写判据统一复用 is_valid_chinese_translation，必须含中文字符）。

【冷静期与状态机】enrich_actor_metadata 依据 ActorProfile.llm_check_status：
  - status=2（模型不知道）→ 按 llm_cooldown_days 冷静期拦截（-1 无限期 / 0 无 / N 天）
  - 本轮填入任一字段 → status=1；全空/NULL → status=2；恒更新 llm_last_checked。
"""

import logging
import re
from datetime import datetime
from typing import Optional, Tuple

from config.settings import load_config
from services.ai_translator import get_translator
from services.translation_utils import is_valid_chinese_translation

logger = logging.getLogger("uvicorn")

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

_MAX_TOKENS_SHORT = 500      # 出生地 / 出生日期等短输出任务
_MAX_TOKENS_BIO = 2000       # 简介生成 / 翻译等长输出任务

# 视为「无数据」的输出哨兵（大小写不敏感比较用大写）
_NULL_SENTINELS = {"NULL", "NONE", "UNKNOWN", "N/A", "NA", "未知", "无", "暂无", "不知道"}


def _clean_result(content: Optional[str]) -> Optional[str]:
    """清洗 LLM 纯文本输出：去引号/Markdown 围栏/空白；NULL 哨兵统一转 None。"""
    if not content:
        return None
    s = content.strip()
    # 去首尾引号（模型可能加引号包裹）
    if len(s) >= 2 and s[0] in "\"'“" and s[-1] in "\"'”":
        s = s[1:-1].strip()
    # 去 Markdown 围栏
    s = re.sub(r"^```(?:json|text)?\s*|\s*```$", "", s).strip()
    if not s or s.upper() in _NULL_SENTINELS:
        return None
    return s


def _safe_cooldown_days(cfg: dict) -> int:
    """解析冷静期天数：-1 无限期 / 0 无冷静期 / N>0 天内不重查；非法回退 7。"""
    try:
        v = int(cfg.get("llm_cooldown_days", 7))
    except (TypeError, ValueError):
        return 7
    return v if v in (-1, 0) or v > 0 else 7


def _chat(system_prompt: str, user_prompt: str, temperature: float = 0.1,
          max_tokens: int = _MAX_TOKENS_SHORT, ctx: Optional[dict] = None,
          field: str = "") -> Optional[str]:
    """统一 LLM 调用：本地 qwen 优先（受 actor_ai_local_first 开关控制），NULL 感知降级。

    ctx（可选，同一演员同一轮共享）:
      - ctx["_pending"]: {字段: 候选来源} — LLM 层成功但未经领域函数验收，
        领域函数通过 is_valid_chinese_translation / 日期格式等校验后才经 _commit_source 提交
      - ctx["_skip"]:    返回 NULL/失败的大模型名集合（下一字段自动跳过）
    field: 当前字段标识（birth_place / overview / birth_date），用于 per-field 来源追踪。

    Returns:
        内容字符串（领域函数负责验收与提交来源，_chat 不直接写 _sources/_field_sources）。
    """
    cfg = load_config()
    local_first = bool(cfg.get("actor_ai_local_first", True))
    skip = set(ctx.get("_skip")) if ctx and ctx.get("_skip") else None
    content, source, nulls = get_translator().chat_null_aware(
        system_prompt, user_prompt,
        temperature=temperature, max_tokens=max_tokens,
        local_first=local_first, skip=skip,
    )
    if ctx is not None:
        if nulls:
            ctx.setdefault("_skip", set()).update(nulls)
        if source and field:
            ctx.setdefault("_pending", {})[field] = source
    return content


def _commit_source(ctx: Optional[dict], field: str):
    """领域函数验收通过后，把该字段的候选来源提交到 _sources / _field_sources。

    校验失败（如生日提取返回非日期、翻译不含中文）时调用方不调用本函数，
    _pending 中该字段条目自然被丢弃，避免来源与「实际未填入」不一致。
    """
    if not ctx:
        return
    src = ctx.get("_pending", {}).pop(field, None)
    if src:
        ctx.setdefault("_sources", set()).add(src)
        ctx.setdefault("_field_sources", {})[field] = src


def merge_sources(*parts: str) -> str:
    """合并多个逗号分隔的大模型来源为去重列表（保持出现顺序）。"""
    seen: list = []
    for part in parts:
        for s in str(part or "").split(","):
            s = s.strip()
            if s and s not in seen:
                seen.append(s)
    return ",".join(seen)


def merge_field_sources(*maps) -> dict:
    """合并多个 {字段: 大模型} 映射（后出现的同字段覆盖；容忍 dict 或 JSON 字符串）。"""
    import json as _json
    merged: dict = {}
    for m in maps:
        if not m:
            continue
        if isinstance(m, str):
            s = m.strip()
            if not s:
                continue
            try:
                m = _json.loads(s)
            except Exception:
                continue
        if not isinstance(m, dict):
            continue
        for k, v in m.items():
            if k and v:
                merged[str(k)] = str(v)
    return merged


# ---------------------------------------------------------------------------
# Prompt 模板（源自用户提供的 5 组提示词，严格防伪）
# ---------------------------------------------------------------------------

# 1. 出生地地理翻译（非空非中文 → 简体中文）
_BIRTH_PLACE_PROMPT = """你是一个专业的影视数据本地化（汉化）专家和地理翻译助手。你的任务是将演员的出生地（birth_place）信息，从英语、拼音、罗马音或其他语言，统一翻译并规范化为标准的简体中文。

在翻译时，请严格遵守以下规则：
1. 调整语序（从大到小）：中文表达地理位置的习惯是从大到小（国家 -> 州/省 -> 城市）。例如，输入 "Toronto, Ontario, Canada"，输出应为 "加拿大安大略省多伦多"。
2. 准确还原中国地名（拼音处理）：如果识别到代表中国地名的拼音，请准确还原为对应的中文汉字，并加上"省/市"后缀。例如，输入 "Dalian, Liaoning, China"，输出应为 "中国辽宁省大连市"。
3. 处理常见缩写：准确识别并翻译国家或州的缩写。例如："USA" 翻译为 "美国"，"UK" 翻译为 "英国"。注意识别美国州名的缩写（如 "San Diego, CA" 应翻译为 "美国加利福尼亚州圣地亚哥"）。
4. 统一习惯用语："South Korea" 翻译为 "韩国"，"England" / "United Kingdom" 统一整合为 "英国"。
5. 保持纯净输出：只输出翻译后的最终中文结果，不要输出任何解释性文字，不要带有引号。如果输入为空 (NULL) 或无法识别的无效字符，请输出 "未知" 或保持为空。

【输入】
演员名：{actor_name}
出生地原文：{birth_place}

【输出】
只输出简体中文出生地；无法翻译或不确定时只输出四个大写字母：NULL。"""

# 2. 空出生地按演员知识生成 + 严格 NULL 红线
_BIRTH_PLACE_KNOWLEDGE_PROMPT = """你是一个极其严格的底层数据处理程序，没有人类的情感，也没有猜测的能力。你的最高优先级原则是：【绝对保证数据的真实性与准确性，禁止任何形式的幻觉和猜测】。

任务：请给出演员「{actor_name}」的出生地（统一为简体中文，格式如：中国黑龙江省哈尔滨市 / 韩国首尔）。

在执行任务时，请严格遵守以下红线：
1. 若你对结果没有 100% 的把握，或者知识库中缺少确切信息。
2. 若输入的数据模糊、残缺或无法准确翻译/提取。
3. 若你需要依靠"猜测"、"推断"或"编造"才能得出结果。

只要触发以上任意一点，你必须、且只能返回四个大写字母：NULL。

【禁止行为清单】：
- 绝对不要编造数据。
- 绝对不要输出任何解释说明。
- 绝对不要输出任何标点符号、换行符或多余的空格。
- 绝对不要道歉或回复对话式语言。

如果你知道答案，仅输出最终的简体中文出生地；如果你不知道，仅输出 NULL。"""

# 3. 中文简介生成（strict-NULL）
_BIO_GENERATE_PROMPT = """请为演员「{actor_name}」生成一段专业的中文简介。
如果你在知识库中找不到该演员的确切生平信息，或者该演员非常冷门导致你无法 100% 确认事实，请直接输出 NULL。绝对不要猜测或编造任何经历、作品或日期。仅输出简介文本，或输出 NULL。"""

# 4. 外文简介翻译为简体中文
_BIO_TRANSLATE_PROMPT = """你是一个专业的影视数据汉化专家。你的任务是将以下外文演员简介翻译为流畅、专业的简体中文。

规则：
1. 保持人物传记的客观语气，避免夸张的修辞。
2. 准确翻译影视剧作品名称，如果不确定中文译名，请保留英文原名。
3. 如果输入为空或包含无效内容，请直接返回 "无"。
4. 仅返回翻译后的中文结果，不要包含任何解释性文本或原始文本。

输入简介：
{overview}"""

# 5. 出生日期提取（YYYY-MM-DD / YYYY / NULL）
_BIRTH_DATE_EXTRACT_PROMPT = """你是一个数据提取器。请从提供的文本中提取演员的出生日期，并将其转换为标准的 YYYY-MM-DD 格式。

规则：
1. 如果文本中明确包含出生日期，提取并转换为 YYYY-MM-DD 格式（例如：1993-05-16）。
2. 如果文本中仅包含年份，请输出 YYYY 格式（例如：1993）。
3. 如果文本中没有包含任何出生日期信息，或者输入为空，请严格输出单词 "NULL"。
4. 绝对不要猜测或编造日期。仅根据提供的文本进行提取。
5. 只输出最终的日期字符串或 "NULL"，不要包含任何其他字符。

输入文本：
{text}"""


# ---------------------------------------------------------------------------
# 核心函数（strict-NULL）
# ---------------------------------------------------------------------------

def translate_birth_place(raw: str, actor_name: str = "", ctx: Optional[dict] = None) -> str:
    """非空非中文出生地 → 简体中文地理翻译。

    - 空输入 → ""（无可翻译内容，不做无中生有）
    - 已含中文 → 原样保留
    - LLM 翻译成功（含中文字符）→ 返回中文
    - LLM 返回 NULL/空/失败 → 保留原值（不伪造不销毁）
    - ctx: 可选共享上下文（_sources 记录成功大模型 / _skip 记录 NULL 模型）
    """
    raw = (raw or "").strip()
    if not raw:
        return ""
    if is_valid_chinese_translation(raw):
        return raw

    user_prompt = (
        f"演员名：{actor_name}\n"
        f"出生地原文：{raw}\n\n"
        "【输出】只输出简体中文出生地。"
    )
    try:
        content = _chat(_BIRTH_PLACE_PROMPT, user_prompt, max_tokens=_MAX_TOKENS_SHORT,
                        ctx=ctx, field="birth_place")
    except Exception as e:
        logger.error("   ❌ [ActorAI] 出生地翻译异常 %r: %s", raw, e)
        return raw
    translated = _clean_result(content)
    if translated and is_valid_chinese_translation(translated):
        _commit_source(ctx, "birth_place")
        return translated
    logger.warning("   ⚠ [ActorAI] 出生地翻译返回 NULL/无效，保留原值: %r", raw)
    return raw


def fill_birth_place(actor_name: str, ctx: Optional[dict] = None) -> str:
    """空出生地 → 按演员知识生成（strict-NULL）。模型不确定返回 NULL → ""。"""
    if not actor_name:
        return ""
    try:
        content = _chat(
            _BIRTH_PLACE_KNOWLEDGE_PROMPT, f"演员名：{actor_name}",
            max_tokens=_MAX_TOKENS_SHORT, ctx=ctx, field="birth_place",
        )
    except Exception as e:
        logger.error("   ❌ [ActorAI] 出生地知识生成异常 %s: %s", actor_name, e)
        return ""
    result = _clean_result(content)
    if result and is_valid_chinese_translation(result):
        _commit_source(ctx, "birth_place")
        return result
    return ""


def ensure_actor_overview(actor_name: str, overview: str, ctx: Optional[dict] = None) -> str:
    """简介：空 → 生成（strict-NULL）；非中文 → 翻译；中文 → 原样保留。"""
    overview = (overview or "").strip()
    try:
        if not overview:
            content = _chat(
                _BIO_GENERATE_PROMPT, f"演员名：{actor_name}",
                max_tokens=_MAX_TOKENS_BIO, ctx=ctx, field="overview",
            )
            result = _clean_result(content)
            if result and is_valid_chinese_translation(result):
                _commit_source(ctx, "overview")
                return result
            return ""
        if is_valid_chinese_translation(overview):
            return overview
        content = _chat(_BIO_TRANSLATE_PROMPT, overview, max_tokens=_MAX_TOKENS_BIO,
                        ctx=ctx, field="overview")
        result = _clean_result(content)
        if result and is_valid_chinese_translation(result):
            _commit_source(ctx, "overview")
            return result
        return overview  # 翻译失败保留原英文简介
    except Exception as e:
        logger.error("   ❌ [ActorAI] 简介补全异常 %s: %s", actor_name, e)
        return overview or ""


def extract_birth_date(actor_name: str, birth_date: str, overview_text: str,
                       ctx: Optional[dict] = None) -> str:
    """生日：已有 → 保留；空且有简介 → 从简介提取（strict-NULL，只认日期/年份格式）；无简介 → ""。"""
    birth_date = (birth_date or "").strip()
    if birth_date:
        return birth_date
    overview_text = (overview_text or "").strip()
    if not overview_text:
        return ""
    try:
        content = _chat(
            _BIRTH_DATE_EXTRACT_PROMPT, overview_text, max_tokens=_MAX_TOKENS_SHORT,
            ctx=ctx, field="birth_date",
        )
    except Exception as e:
        logger.error("   ❌ [ActorAI] 生日提取异常 %s: %s", actor_name, e)
        return ""
    result = _clean_result(content)
    if result and re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", result):
        _commit_source(ctx, "birth_date")
        return result
    return ""


# ---------------------------------------------------------------------------
# 汇总入口（冷静期 + 状态机）
# ---------------------------------------------------------------------------

def enrich_actor_metadata(
    actor_name: str,
    profile_data: dict,
    existing,
    cfg: dict,
) -> Tuple[dict, Optional[int], Optional[datetime]]:
    """对 profile_data 的 birth_place / birth_date / overview 做 LLM 补全/汉化。

    Args:
        actor_name:     演员名
        profile_data:   {birth_date, birth_place, overview, ...} 收集完毕的元数据（原地修改）
        existing:       ActorProfile ORM 行（可为 None，用于读冷静期状态）
        cfg:            配置（读 llm_cooldown_days / actor_ai_local_first）

    Returns:
        (profile_data, llm_check_status, llm_last_checked, llm_translation_source, llm_field_sources)
        - 本轮真正调用了 LLM → 返回 (补全后的 profile_data, 1|2, now, 来源集合, {字段:模型})
        - 冷静期拦截 / 无工作 / 无可用 Provider → 返回 (profile_data, None, None, "", {})
        （None 状态表示「未触发 LLM」，调用方不得写入 status/时间，避免误判）
    """
    bp = (profile_data.get("birth_place") or "").strip()
    bd = (profile_data.get("birth_date") or "").strip()
    ov = (profile_data.get("overview") or "").strip()

    need_bp_translate = bool(bp) and not is_valid_chinese_translation(bp)
    need_bp_fill = not bp
    need_ov_gen = not ov
    need_ov_translate = bool(ov) and not is_valid_chinese_translation(ov)
    need_bd_extract = not bd and bool(ov)

    has_work = (
        need_bp_translate or need_bp_fill
        or need_ov_gen or need_ov_translate
        or need_bd_extract
    )
    if not has_work:
        return profile_data, None, None, "", {}

    # 冷静期拦截：仅 status=2（模型不知道）时生效
    if existing is not None and getattr(existing, "llm_check_status", 0) == 2:
        cooldown = _safe_cooldown_days(cfg)
        if cooldown == -1:
            logger.info(
                "   ⏳ [ActorAI] %s: LLM 曾返回 NULL 且冷静期为无限期(-1)，跳过补全", actor_name,
            )
            return profile_data, None, None, "", {}
        last = getattr(existing, "llm_last_checked", None)
        if last is not None and cooldown > 0:
            elapsed_days = (datetime.now() - last).total_seconds() / 86400
            if elapsed_days < cooldown:
                logger.info(
                    "   ⏳ [ActorAI] %s: LLM 冷静期内（%.1f/<%d 天），跳过补全",
                    actor_name, elapsed_days, cooldown,
                )
                return profile_data, None, None, "", {}

    # 共享上下文：记录成功大模型(_sources) + 返回 NULL/失败的模型(_skip)
    ctx: dict = {}
    filled_any = False

    # 1. 出生地：非空非中文 → 翻译；空 → 知识生成
    if need_bp_translate:
        new_bp = translate_birth_place(bp, actor_name, ctx=ctx)
        if is_valid_chinese_translation(new_bp) and new_bp != bp:
            profile_data["birth_place"] = new_bp
            filled_any = True
    elif need_bp_fill:
        new_bp = fill_birth_place(actor_name, ctx=ctx)
        if is_valid_chinese_translation(new_bp):
            profile_data["birth_place"] = new_bp
            filled_any = True

    # 2. 简介：空 → 生成；非中文 → 翻译
    ov_now = (profile_data.get("overview") or "").strip()
    if need_ov_gen:
        new_ov = ensure_actor_overview(actor_name, "", ctx=ctx)
        if new_ov and is_valid_chinese_translation(new_ov):
            profile_data["overview"] = new_ov
            filled_any = True
    elif need_ov_translate:
        new_ov = ensure_actor_overview(actor_name, ov, ctx=ctx)
        if is_valid_chinese_translation(new_ov) and new_ov != ov:
            profile_data["overview"] = new_ov
            filled_any = True

    # 3. 生日：空且有最终简介 → 从简介提取（基于刚生成/翻译的简介）
    bd_now = (profile_data.get("birth_date") or "").strip()
    ov_final = (profile_data.get("overview") or "").strip()
    if not bd_now and ov_final:
        new_bd = extract_birth_date(actor_name, "", ov_final, ctx=ctx)
        if new_bd:
            profile_data["birth_date"] = new_bd
            filled_any = True

    llm_source = merge_sources(*sorted(ctx.get("_sources") or ()))
    field_sources = ctx.get("_field_sources") or {}
    now = datetime.now()
    status = 1 if filled_any else 2
    logger.info(
        "   🤖 [ActorAI] %s: LLM 补全完成 status=%d（%s）来源=%s 字段映射=%s",
        actor_name, status, "已填入数据" if filled_any else "模型不知道(NULL)",
        llm_source or "-", field_sources or "-",
    )
    return profile_data, status, now, llm_source, field_sources
