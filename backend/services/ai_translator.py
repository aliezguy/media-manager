"""
AI 翻译服务 — 通用 LLM 翻译，兼容任意 OpenAI SDK 接口的大模型。

【核心能力：地址级 + 模型级 双重高可用降级】
- 每次调用动态读取系统最新配置（支持热切换，无需重启服务）。
- 「地址级」：每个 Provider 可配置主地址 base_url + 备选地址 alt_base_url，
  发起请求前自动组成 [主地址, 备选地址] 候选列表。主地址纯粹网络连接失败
  （openai.APIConnectionError / httpx.ConnectError，即地址不可达）→ 无缝切换到备选地址；
  业务错误（429 限流 / 5xx，说明地址可达但被拒绝）→ 不试备选，直接交给指数退避 / 模型级降级。
- 「模型级」：配置化读取多个优先级的 Provider（Primary / Secondary / Tertiary …），
  按顺序逐级尝试：当前 Provider 内「地址级降级 → 指数退避重试」全部耗尽后，自动无缝降级到下一个 API。
- 空值防御：某个优先级的配置为空、或缺失 api_key / model_name 等核心字段时，优雅跳过，直接尝试下一个。

【配置 Schema（config.json）】
    "ai_providers": [                       # 有序列表 或 保序字典，优先级 = 顺序（index 0 / 首个键 为首选）
        {
            "name": "硅基流动",              # 可选，仅用于日志标识
            "base_url": "https://api.siliconflow.cn/v1",
            "alt_base_url": "http://host.docker.internal:8000/v1",  # 可选，主地址网络不通时自动切换（本地调试/Docker 兜底）
            "api_key": "sk-xxx",
            "model_name": "deepseek-ai/DeepSeek-V3",
            "timeout": 60,                   # 可选，单次请求超时（秒）
            "max_retries": 2                 # 可选，429/5xx/超时 的指数退避重试次数
        },
        {"name": "OpenAI官方", "base_url": "", "api_key": "sk-yyy", "model_name": "gpt-4o-mini"}
    ],
    # 向下兼容的旧字段（当 ai_providers 为空或全部无效时回退使用）：
    "sf_api_key": "",        "llm_base_url": "https://api.siliconflow.cn/v1", "llm_model_name": "deepseek-ai/DeepSeek-V3"
    # 可选全局调优项：
    # "ai_request_timeout": 60,  "ai_max_retries": 2,  "ai_batch_size": 30,  "ai_request_interval": 1.5,  "ai_max_tokens": 2000

【翻译规则（由 System Prompt 严格约束）】
- 已是中文的保持不变；日文尽量保留原汉字（新垣結衣 → 新垣结衣）。
- 使用国内常见/豆瓣标准的中文影视译名，拒绝生硬直译。
- 批量输入列表，严格返回 JSON Object（键=原文，值=译文），仅输出 JSON，无 Markdown、无多余解释。

【健壮解析】
- 正则剔除 ```json / ``` 围栏标记后再 json.loads。
- 全链路解析失败时返回原文映射（不抛异常）。

【依赖】openai==2.11.0（无新增依赖；未引入 tenacity，改为手写指数退避重试装饰器，
避免给 venv 增加安装负担。OpenAI SDK 自带重试被显式关闭，统一由本模块瀑布流控制，防止叠加长阻塞）。
"""

import json as _json
import logging
import random
import re
import time
from typing import Any, Callable, Dict, List, Optional

from config.settings import load_config

logger = logging.getLogger("uvicorn")

# ---------------------------------------------------------------------------
# 常量与默认值
# ---------------------------------------------------------------------------

# 默认 Provider 模型（仅在旧字段回退时兜底；ai_providers 中的 model_name 必填，不受此影响）
DEFAULT_MODEL_NAME = "deepseek-ai/DeepSeek-V3"

# 单请求超时（秒）与单 Provider 内重试次数
DEFAULT_TIMEOUT = 60
DEFAULT_MAX_RETRIES = 2

# 指数退避参数
DEFAULT_BASE_DELAY = 1.0      # 首次重试等待基础秒数
DEFAULT_MAX_BACKOFF_DELAY = 8.0

# 批量分块与限速
DEFAULT_BATCH_SIZE = 30
DEFAULT_REQUEST_INTERVAL = 1.5
DEFAULT_MAX_TOKENS = 2000

# 中文（含日文汉字/繁体）检测范围，与项目内 sync_actions.py 保持一致
_CHINESE_RE = re.compile(r"[一-鿿]")
# Markdown JSON 围栏
_JSON_FENCE_RE = re.compile(r"```(?:json|JSON)?", re.IGNORECASE)

# ---------------------------------------------------------------------------
# 动态导入 OpenAI SDK（guarded）
# ---------------------------------------------------------------------------
try:
    from openai import (OpenAI, APITimeoutError, APIConnectionError,
                        InternalServerError, RateLimitError)
    OPENAI_AVAILABLE = True
    _API_TIMEOUT_EXC: Optional[type] = APITimeoutError
    _API_CONN_EXC: Optional[type] = APIConnectionError
    _INTERNAL_SERVER_EXC: Optional[type] = InternalServerError
    _RATE_LIMIT_EXC: Optional[type] = RateLimitError
except ImportError:  # pragma: no cover
    OPENAI_AVAILABLE = False
    _API_TIMEOUT_EXC = _API_CONN_EXC = _INTERNAL_SERVER_EXC = _RATE_LIMIT_EXC = None

# httpx 底层连接异常（openai SDK 依赖 httpx，通常可用；失败仅影响「网络错误精准识别」降级）
try:
    import httpx
    _HTTPX_CONNECT_EXC: Optional[type] = httpx.ConnectError
except ImportError:  # pragma: no cover
    _HTTPX_CONNECT_EXC = None


# ---------------------------------------------------------------------------
# 解析层
# ---------------------------------------------------------------------------

def _safe_json_loads(text: str) -> Optional[dict]:
    """
    健壮 JSON 解析：能处理常见 AI 返回的脏数据。
    支持 markdown 代码块提取、未闭合 JSON 截断补全；失败返回 None（不抛异常）。
    """
    if not text or not isinstance(text, str):
        return None

    # 0. 先剔除 ```json / ``` 围栏，减少后续修复负担
    cleaned = _JSON_FENCE_RE.sub("", text).strip()
    if not cleaned:
        return None

    try:
        # 1. 直接解析
        result = _json.loads(cleaned)
        return result if isinstance(result, dict) else None
    except _json.JSONDecodeError as e:
        logger.warning(f"[JSON修复] 直接解析失败: {e}，尝试智能修复…")
        logger.debug(f"[JSON修复] 原始文本:\n{cleaned}")

    # 2. 尝试从 markdown 代码块中提取 JSON（防御性，围栏可能含变体）
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if match:
        json_str = match.group(1)
        try:
            result = _json.loads(json_str)
            logger.info("[JSON修复] 成功从 Markdown 代码块提取 JSON")
            return result if isinstance(result, dict) else None
        except _json.JSONDecodeError as inner_e:
            logger.error(f"[JSON修复] 提取出的 JSON 仍解析失败: {inner_e}")
            cleaned = json_str

    # 2.5 仅缺失闭合括号时，补足 } 重试（例如 {"A": "B", "C": "D" → 补 } 完整恢复）
    brace_gap = cleaned.count("{") - cleaned.count("}")
    if brace_gap > 0:
        try:
            result = _json.loads(cleaned + "}" * brace_gap)
            if isinstance(result, dict):
                logger.info("[JSON修复] 补足闭合括号后解析成功")
                return result
        except _json.JSONDecodeError:
            pass  # 仍失败则继续走截断补全

    # 3. 尝试修复未闭合的 JSON（截断补全，返回部分结果）
    last_quote = cleaned.rfind('"')
    last_brace = cleaned.rfind('}')

    if last_brace > last_quote:
        fixed_text = cleaned[:last_brace + 1]
    elif last_quote != -1:
        prev_quote = cleaned.rfind('"', 0, last_quote)
        if prev_quote != -1:
            comma_before = cleaned.rfind(',', 0, prev_quote)
            fixed_text = cleaned[:comma_before] + "\n}" if comma_before != -1 else "{}"
        else:
            fixed_text = cleaned
    else:
        fixed_text = cleaned

    if fixed_text != cleaned:
        logger.info("[JSON修复] 尝试截断补全…")
        try:
            result = _json.loads(fixed_text)
            logger.info("[JSON修复] 补全成功，返回部分结果")
            return result if isinstance(result, dict) else None
        except _json.JSONDecodeError:
            logger.error("[JSON修复] 最终修复失败，放弃解析")

    return None


def _parse_result_dict(content: Optional[str]) -> Optional[dict]:
    """
    三层防御解析：去围栏 → 智能修复 → 提取首个 {...} 片段。
    成功返回 dict，失败返回 None（调用方负责兜底原文映射）。
    """
    if not content or not isinstance(content, str) or not content.strip():
        return None

    # 第 1 层：正则剔除 Markdown 围栏后直接解析
    cleaned = _JSON_FENCE_RE.sub("", content).strip()
    try:
        result = _json.loads(cleaned)
        if isinstance(result, dict):
            return result
    except _json.JSONDecodeError:
        pass

    # 第 2 层：智能修复解析（代码块提取 / 截断补全）
    result = _safe_json_loads(cleaned)
    if isinstance(result, dict):
        return result

    # 第 3 层：兜底提取首个 {...} 片段（容忍前导解释文字）
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if m:
        try:
            result = _json.loads(m.group(0))
            if isinstance(result, dict):
                return result
        except _json.JSONDecodeError:
            pass

    return None


# ---------------------------------------------------------------------------
# 错误分类
# ---------------------------------------------------------------------------

def _is_rate_limit_error(exc: Exception) -> bool:
    """判断异常是否为 429 限流错误（保留导出，供 douban_service 复用）。"""
    if _RATE_LIMIT_EXC is not None and isinstance(exc, _RATE_LIMIT_EXC):
        return True
    msg = str(exc).lower()
    return any(kw in msg for kw in (
        "429", "rate limit", "rate_limit", "too many requests",
        "过大", "访问量过大", "请求过快", "频率限制", "频率控制", "frequency",
    ))


def _is_retryable_error(exc: Exception) -> bool:
    """429 限流 / 5xx 服务端错误 / 网络超时 / 断连 → 属于可重试的瞬时故障。"""
    if _is_rate_limit_error(exc):
        return True
    if _API_TIMEOUT_EXC is not None and isinstance(exc, _API_TIMEOUT_EXC):
        return True
    if _API_CONN_EXC is not None and isinstance(exc, _API_CONN_EXC):
        return True
    if _INTERNAL_SERVER_EXC is not None and isinstance(exc, _INTERNAL_SERVER_EXC):
        return True
    msg = str(exc).lower()
    return any(kw in msg for kw in (
        "timeout", "timed out", "connection reset", "connectionerror",
        "connection refused", "network", "remote end closed connection",
        "500", "501", "502", "503", "504",
        "internal server", "bad gateway", "service unavailable", "server error",
        "temporarily unavailable", "read timed out", "connect timed out",
    ))


def _is_connection_error(exc: Exception) -> bool:
    """是否为纯粹的「网络连接错误」（地址不可达），用于地址级降级：主地址不通 → 无缝试备选地址。

    覆盖 openai.APIConnectionError（openai SDK 对底层传输错误统一包装）与 httpx.ConnectError
    （含 ConnectTimeout 等子类）；另对少数自建兼容端点直接抛出的裸 socket 错误做关键词兜底。
    """
    if _API_CONN_EXC is not None and isinstance(exc, _API_CONN_EXC):
        return True
    if _HTTPX_CONNECT_EXC is not None and isinstance(exc, _HTTPX_CONNECT_EXC):
        return True
    msg = str(exc).lower()
    return any(kw in msg for kw in (
        "connection refused", "failed to connect", "cannot connect",
        "getaddrinfo", "name resolution", "could not resolve",
        "network unreachable", "host unreachable", "connecterror",
    ))


def _is_retryable_business_error(exc: Exception) -> bool:
    """
    可重试的业务瞬时故障（429 限流 / 5xx / 超时 / 断连），但显式排除「网络连接错误」。

    设计意图：网络连接错误意味着「当前地址不可达」，在同一地址上做指数退避是无意义的，
    统一交由地址级降级（主地址 → 备选地址）处理；业务错误则说明地址可达、只是被拒绝，
    才适合在同一地址上指数退避重试。
    """
    if _is_connection_error(exc):
        return False
    return _is_retryable_error(exc)


def _is_response_format_unsupported(exc: Exception) -> bool:
    """部分 OpenAI 兼容 API 不支持 response_format 参数（400），需要降级为无格式约束重试。"""
    msg = str(exc).lower()
    return any(kw in msg for kw in (
        "response_format", "response format", "json_object", "json object",
        "unsupported response format", "response format is not supported",
        "parameter.*response_format", "format.*not supported",
    ))


def _rate_limit_sleep(label: str = ""):
    """随机休眠 1.5~3.0 秒，打散请求频率（保留导出，供 douban_service 复用）。"""
    delay = random.uniform(1.5, 3.0)
    if label:
        logger.debug("   🐢 [减速] %s 休眠 %.1fs", label, delay)
    time.sleep(delay)


# ---------------------------------------------------------------------------
# 手写指数退避重试
# ---------------------------------------------------------------------------

def _retry_with_backoff(
    fn: Callable[[], Any],
    *,
    max_retries: int,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_BACKOFF_DELAY,
    should_retry: Callable[[Exception], bool],
    on_retry: Optional[Callable[[int, Exception, float], None]] = None,
) -> Any:
    """
    对 fn 在 should_retry(exc) 条件下执行指数退避重试，重试之间加入随机 jitter 打散。
    重试耗尽后抛出最后一次异常（由调用方捕获并降级到下一个 Provider）。
    max_retries=0 时表示不重试，首次失败立即抛出。
    """
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as exc:
            attempt += 1
            if attempt > max_retries or not should_retry(exc):
                raise
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay) + random.uniform(0, 0.5)
            if on_retry:
                on_retry(attempt, exc, delay)
            time.sleep(delay)


# ---------------------------------------------------------------------------
# 翻译器
# ---------------------------------------------------------------------------

# System Prompt 模板（kind: names / roles）
_TRANSLATION_TEMPLATES = {
    "names": {
        "subject": "影视演员人名",
        "rule_jp": (
            "2. 日文名字尽量保留原汉字并转为简体（例如：新垣結衣 → 新垣结衣；石原さとみ → 石原里美），\n"
            "   仅在无法用汉字表达时才使用通用音译。"
        ),
        "table_note": "演员表",
    },
    "roles": {
        "subject": "影视角色名",
        "rule_jp": (
            "2. 日文角色名尽量保留原汉字并转为简体；其余外语使用国内观众熟悉的通用译名。"
        ),
        "table_note": "角色表",
    },
}


class AITranslator:
    """
    多级 API 瀑布流翻译器 — 每次调用动态读取系统最新配置。
    返回格式：{原文: 译文}；任意失败都兜底返回原文映射，不抛异常。
    """

    # ------------------------------------------------------------------
    # 配置解析（数据驱动 + 向后兼容）
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_positive_int(value: Any, default: int, allow_zero: bool = False) -> int:
        try:
            parsed = int(value)
            if parsed > 0 or (allow_zero and parsed == 0):
                return parsed
        except (TypeError, ValueError):
            pass
        return default

    @staticmethod
    def _safe_positive_float(value: Any, default: float) -> float:
        try:
            parsed = float(value)
            if parsed > 0:
                return parsed
        except (TypeError, ValueError):
            pass
        return default

    @classmethod
    def _sanitize_provider(cls, raw: Any, default_timeout: int, default_retries: int) -> Optional[dict]:
        """
        校验单个 Provider 配置。
        缺失核心字段（api_key / model_name）或类型非法 → 返回 None（调用方优雅跳过）。
        """
        if not isinstance(raw, dict):
            return None
        api_key = str(raw.get("api_key") or "").strip()
        model_name = str(raw.get("model_name") or "").strip()
        if not api_key or not model_name:
            return None
        base_url = str(raw.get("base_url") or "").strip()
        alt_base_url = str(raw.get("alt_base_url") or "").strip()
        name = str(raw.get("name") or "").strip() or model_name
        return {
            "name": name,
            "base_url": base_url or None,          # 空串 → None（走 OpenAI 默认端点）
            "alt_base_url": alt_base_url or None,  # 可选，主地址网络不通时自动切换（地址级降级）
            "api_key": api_key,
            "model_name": model_name,
            "timeout": cls._safe_positive_int(raw.get("timeout"), default_timeout, allow_zero=False),
            "max_retries": cls._safe_positive_int(raw.get("max_retries"), default_retries, allow_zero=True),
        }

    @classmethod
    def _resolve_providers(cls, cfg: Optional[dict] = None, log_invalid: bool = True) -> List[dict]:
        """
        按优先级解析可用的 Provider 列表（有序，index 0 为首选）。

        规则：
        1. 优先读取数据驱动的 ai_providers 列表，逐个校验，无效项跳过并 log warning；
        2. 若列表为空或全部无效，回退到旧字段 sf_api_key / llm_base_url / llm_model_name；
        3. 仍无可用项 → 返回空列表（调用方跳过翻译，不抛异常）。
        """
        cfg = cfg if cfg is not None else load_config()
        default_timeout = cls._safe_positive_int(cfg.get("ai_request_timeout"), DEFAULT_TIMEOUT, allow_zero=False)
        default_retries = cls._safe_positive_int(cfg.get("ai_max_retries"), DEFAULT_MAX_RETRIES, allow_zero=True)

        providers: List[dict] = []
        raw_list = cfg.get("ai_providers")
        # 兼容两种形态：有序列表 [ {...}, {...} ] 或保序字典 { "primary": {...}, "secondary": {...} }
        if isinstance(raw_list, dict):
            raw_list = list(raw_list.values())
        if isinstance(raw_list, list):
            for i, raw in enumerate(raw_list):
                p = cls._sanitize_provider(raw, default_timeout, default_retries)
                if p:
                    providers.append(p)
                elif log_invalid:
                    logger.warning(
                        "   ⚠️ [AI翻译] Provider 配置 #%d 无效（缺失 api_key 或 model_name），已跳过",
                        i + 1,
                    )

        # 向后兼容回退：列表为空或全部无效时，用旧字段构建单 Provider
        if not providers:
            legacy = cls._sanitize_provider(
                {
                    "name": "Legacy(旧字段)",
                    "base_url": cfg.get("llm_base_url"),
                    "api_key": cfg.get("sf_api_key"),
                    "model_name": cfg.get("llm_model_name"),
                },
                default_timeout,
                default_retries,
            )
            if legacy:
                if log_invalid:
                    logger.info(
                        "   ℹ️ [AI翻译] 未配置 ai_providers，回退使用旧字段 sf_api_key/llm_base_url/llm_model_name"
                    )
                providers.append(legacy)

        return providers

    @staticmethod
    def _build_address_list(provider: dict) -> List[str]:
        """
        组装当前 Provider 待尝试的「地址级」候选列表（主地址在前，备选地址在后）。
        空串 "" 表示 OpenAI 默认端点（base_url 留空时的语义）。
        - base_url 留空 → 主地址即 OpenAI 默认端点；
        - alt_base_url 留空 或 与主地址相同 → 自动去重跳过（保证至少返回 1 个候选）。
        """
        primary = (provider.get("base_url") or "").strip()
        alt = (provider.get("alt_base_url") or "").strip()
        addresses: List[str] = [primary]
        if alt and alt != primary:
            addresses.append(alt)
        return addresses

    @staticmethod
    def _build_client(provider: dict, base_url: Optional[str] = None) -> Optional[OpenAI]:
        """
        根据单个 Provider 构建 OpenAI client。
        显式关闭 SDK 自带重试（max_retries=0），统一由本模块瀑布流控制，避免重试叠加导致长阻塞。

        base_url 参数：地址级降级时覆盖 provider["base_url"]；
        传 None 表示沿用 Provider 配置的主地址，传 "" 表示使用 OpenAI 默认端点。
        """
        if not OPENAI_AVAILABLE:
            logger.error("   ❌ [AI翻译] openai SDK 未安装，无法发起翻译请求")
            return None
        kwargs: dict = {"api_key": provider["api_key"]}
        url = provider.get("base_url") if base_url is None else base_url
        if url:
            kwargs["base_url"] = url
        kwargs["timeout"] = provider.get("timeout", DEFAULT_TIMEOUT)
        kwargs["max_retries"] = 0
        try:
            return OpenAI(**kwargs)
        except Exception as e:
            logger.error(f"   ❌ [AI翻译] Provider[{provider.get('name')}] 客户端构建失败: {e}")
            return None

    # ------------------------------------------------------------------
    # 核心瀑布流调用
    # ------------------------------------------------------------------

    def _chat_once(self, client: OpenAI, provider: dict, messages: List[dict],
                   temperature: float, max_tokens: int):
        """
        单次 chat.completions.create。
        若当前 API 不支持 response_format（部分兼容端点返回 400），
        自动降级为无格式约束重试一次；其余异常原样上抛由重试/降级层处理。
        """
        kwargs = dict(
            model=provider["model_name"],
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=provider.get("timeout", DEFAULT_TIMEOUT),
        )
        try:
            return client.chat.completions.create(**kwargs, response_format={"type": "json_object"})
        except Exception as exc:
            if _is_response_format_unsupported(exc):
                logger.info(
                    "   ℹ️ [AI翻译] Provider[%s] 不支持 response_format，降级为无格式约束重试",
                    provider["name"],
                )
                return client.chat.completions.create(**kwargs)
            raise

    def _chat_with_address_fallback(self, *, provider: dict, messages: List[dict],
                                    temperature: float, max_tokens: int):
        """
        【地址级降级核心】同一 Provider 内，主地址 → 备选地址 逐个尝试。

        候选列表由 _build_address_list 组装：[base_url(主), alt_base_url(备)]，空串 = OpenAI 默认端点。
        精准异常分流：
        - 纯粹的「网络连接错误」（APIConnectionError / httpx.ConnectError，地址不可达）→
          logger.warning 后 continue 无缝尝试下一个地址；
        - 业务错误（429 限流 / 5xx / 超时，说明地址可达但被拒绝）→ 不试备选地址，直接上抛，
          由 _retry_with_backoff 已经完成的指数退避结果 + 模型级瀑布降级逻辑接管；
        - 主备地址全部连接失败 → 抛最后一次连接异常，触发降级到下一个 Provider。
        """
        pname = provider["name"]
        addresses = self._build_address_list(provider)
        total = len(addresses)
        last_conn_exc: Optional[Exception] = None

        for i, addr in enumerate(addresses):
            addr_display = addr if addr else "OpenAI默认端点"
            addr_tag = "主地址" if i == 0 else "备选地址"
            logger.info(
                "   🚀 [AI翻译] Provider[%s] 正在尝试%s %s（%d/%d）…",
                pname, addr_tag, addr_display, i + 1, total,
            )

            client = self._build_client(provider, base_url=addr)
            if client is None:
                logger.warning("   ⚠️ [AI翻译] Provider[%s] %s 客户端构建失败，跳过该地址", pname, addr_display)
                continue

            try:
                resp = _retry_with_backoff(
                    lambda: self._chat_once(client, provider, messages, temperature, max_tokens),
                    max_retries=provider["max_retries"],
                    base_delay=DEFAULT_BASE_DELAY,
                    max_delay=DEFAULT_MAX_BACKOFF_DELAY,
                    should_retry=_is_retryable_business_error,
                    on_retry=lambda attempt, exc, delay, pname=pname: logger.warning(
                        "   ⚠️ [AI翻译] Provider[%s] 第 %d 次指数退避重试（%.1fs 后）：%s",
                        pname, attempt, delay, exc,
                    ),
                )
                if i > 0:
                    logger.info("   ✅ [AI翻译] Provider[%s] 备选地址请求成功，翻译继续", pname)
                return resp
            except Exception as exc:
                if _is_connection_error(exc):
                    # 纯网络错误：地址不通 → 无缝继续尝试备选地址（不做指数退避，浪费时间）
                    last_conn_exc = exc
                    logger.warning(
                        "   ⚠️ [AI翻译] Provider[%s] %s 网络连接失败（%s）%s",
                        pname, addr_display, exc,
                        "，正在尝试备选地址…" if i < total - 1 else "，主备地址均已尝试",
                    )
                    continue
                # 业务错误（429/5xx/超时）：地址可达但被拒绝 → 不试备选地址，直接上抛走模型级降级
                logger.warning(
                    "   ⚠️ [AI翻译] Provider[%s] %s 可达但业务报错（%s），跳过备选地址，交由指数退避/模型级降级",
                    pname, addr_display, exc,
                )
                raise

        # 主备地址全部连接失败 → 抛最后一次连接异常，触发降级到下一个 Provider
        logger.error(
            "   ❌ [AI翻译] Provider[%s] 全部 %d 个地址均网络连接失败，降级到下一个 Provider，最后异常: %s",
            pname, total, last_conn_exc,
        )
        if last_conn_exc is not None:
            raise last_conn_exc
        raise RuntimeError(f"Provider[{pname}] 无可用地址")

    def _chat_complete_with_fallback(self, *, system_prompt: str, user_prompt: str,
                                     temperature: float, max_tokens: int) -> Optional[str]:
        """
        【瀑布流核心】按优先级遍历 Provider：
        单 Provider 内「地址级网络降级（主→备）」→ 指数退避重试 → 耗尽后捕获异常、log warning →
        无缝切换下一个 API。全部失败 → log error 并返回 None（调用方兜底原文映射）。
        """
        providers = self._resolve_providers(log_invalid=True)
        if not providers:
            logger.info("   ℹ️ [AI翻译] 未配置任何可用的 AI Provider，跳过翻译")
            return None

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        last_exc: Optional[Exception] = None
        for idx, provider in enumerate(providers):
            pname = provider["name"]

            logger.info(
                "   🚀 [AI翻译] 尝试 Provider[%s] (model=%s) …",
                pname, provider["model_name"],
            )
            try:
                resp = self._chat_with_address_fallback(
                    provider=provider,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                content = (resp.choices[0].message.content or "").strip()
                if content:
                    return content
                # 返回空内容视为失败，继续降级
                last_exc = RuntimeError(f"Provider[{pname}] 返回空内容")
                logger.warning("   ⚠️ [AI翻译] Provider[%s] 返回空内容，降级到下一个 API", pname)
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "   ⚠️ [AI翻译] Provider[%s] 地址降级/重试均耗尽，降级到下一个 API: %s",
                    pname, exc,
                )

        logger.error(
            "   ❌ [AI翻译] 全部 %d 个 Provider 均失败，最后一次异常: %s",
            len(providers), last_exc,
        )
        return None

    # ------------------------------------------------------------------
    # 对外 API（与既有调用方完全兼容）
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """是否存在至少一个有效 Provider（不构建客户端、不刷无效配置告警）。"""
        return bool(self._resolve_providers(log_invalid=False))

    def translate_names(self, names: List[str], context: str = "") -> Dict[str, str]:
        """批量翻译演员人名。返回 {原文: 译文}；失败或未配置时返回原文映射（不抛异常）。"""
        return self._translate_batch(
            items=names,
            kind="names",
            context=context,
            temperature=0.1,
        )

    def translate_roles(self, roles: List[str], context: str = "") -> Dict[str, str]:
        """批量翻译角色名。返回 {原文: 译文}；失败或未配置时返回原文映射（不抛异常）。"""
        return self._translate_batch(
            items=roles,
            kind="roles",
            context=context,
            temperature=0.1,
        )

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _translate_batch(self, items: List[str], kind: str, context: str,
                         temperature: float) -> Dict[str, str]:
        """分块批量翻译：块间限速 + 每块走瀑布流 + 最终原文兜底。"""
        if not items:
            return {}

        # 去空白、去重（保持首次出现顺序）
        unique_items: List[str] = []
        for it in items:
            s = str(it).strip()
            if s and s not in unique_items:
                unique_items.append(s)
        if not unique_items:
            return {str(it): str(it) for it in items}

        cfg = load_config()
        providers = self._resolve_providers(cfg=cfg, log_invalid=True)
        if not providers:
            logger.info("   ℹ️ [AI翻译] 未配置任何可用的 AI Provider，跳过%s翻译", kind)
            return {str(it): str(it) for it in items}

        chunk_size = self._safe_positive_int(cfg.get("ai_batch_size"), DEFAULT_BATCH_SIZE, allow_zero=False)
        request_interval = self._safe_positive_float(cfg.get("ai_request_interval"), DEFAULT_REQUEST_INTERVAL)
        max_tokens = self._safe_positive_int(cfg.get("ai_max_tokens"), DEFAULT_MAX_TOKENS, allow_zero=False)

        system_prompt = self._build_prompt(kind, context)
        chunks = [unique_items[i:i + chunk_size] for i in range(0, len(unique_items), chunk_size)]
        total_chunks = len(chunks)

        if total_chunks > 1:
            logger.info(
                "   📦 [AI翻译] %s翻译 数据量较大，已分块：共 %d 条 → %d 批，每批最多 %d 条",
                kind, len(unique_items), total_chunks, chunk_size,
            )
        else:
            logger.info("   📦 [AI翻译] %s翻译 开始处理 %d 条（%d 个可用 Provider）", kind, len(unique_items), len(providers))

        all_results: Dict[str, str] = {}
        for i, chunk in enumerate(chunks):
            if total_chunks > 1:
                logger.info("   📦 [AI翻译] %s翻译 正在处理批次 %d/%d", kind, i + 1, total_chunks)

            user_prompt = _json.dumps(chunk, ensure_ascii=False)
            content = self._chat_complete_with_fallback(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            chunk_result = _parse_result_dict(content)
            if chunk_result:
                all_results.update(chunk_result)
                logger.info(
                    "   ✅ [AI翻译] %s翻译 本批命中 %d/%d 条",
                    kind, len(chunk_result), len(chunk),
                )
            else:
                logger.warning(
                    "   ⚠️ [AI翻译] %s翻译 批次 %d/%d 解析失败，该批回退原文",
                    kind, i + 1, total_chunks,
                )

            if i < total_chunks - 1:
                logger.debug("   🐢 [间隔] %s翻译 批次间隔 %.1fs", kind, request_interval)
                time.sleep(request_interval)

        # 兜底：未翻译到的 key 填回原文；对大小写/首尾空格差异做容错匹配
        out: Dict[str, str] = {}
        for it in items:
            out[str(it)] = self._lookup_translation(all_results, str(it))
        return out

    @staticmethod
    def _lookup_translation(result: Dict[str, str], key: str) -> str:
        """从 LLM 结果中取译文；缺失或非字符串时兜底原文，并对大小写/空白差异做容错。"""
        if not isinstance(result, dict):
            return key
        if key in result:
            val = result[key]
            return val if isinstance(val, str) and val.strip() else key
        key_l = key.strip().lower()
        for k, v in result.items():
            if isinstance(k, str) and isinstance(v, str) and v.strip() and k.strip().lower() == key_l:
                return v
        return key

    @staticmethod
    def _build_prompt(kind: str, context: str) -> str:
        """根据翻译类型组装严格的 System Prompt。"""
        tpl = _TRANSLATION_TEMPLATES.get(kind, _TRANSLATION_TEMPLATES["names"])
        system_prompt = (
            f"你是一位专业的影视翻译助理，负责把{tpl['subject']}翻译成简体中文。\n\n"
            "【核心翻译规则】\n"
            "1. 已经是简体中文的名字，原样保留，禁止修改。\n"
            f"{tpl['rule_jp']}\n"
            "3. 优先使用国内观众熟悉、豆瓣等影视平台通行的中文译名，拒绝生硬直译。\n"
            "4. 每个名字只输出一个译名，禁止列出多个候选。\n\n"
            "【严格输出格式】\n"
            "- 只输出一个 JSON 对象，键为「原始名字」，值为「中文译名」。\n"
            '- 示例：{"Zhao Wenlong": "赵文龙", "新垣結衣": "新垣结衣"}\n'
            "- 必须为输入的每一个名字都给出译文。\n"
            "- 禁止输出 Markdown 代码块标记（```、```json）、注释、解释或任何多余字符。"
        )
        if context:
            system_prompt += f"\n\n【背景】这是影视作品《{context}》的{tpl['table_note']}。"
        return system_prompt


# ---------------------------------------------------------------------------
# 全局单例（延迟初始化；每次调用会重新读取 config，支持热切换）
# ---------------------------------------------------------------------------

_translator: Optional[AITranslator] = None


def get_translator() -> AITranslator:
    """获取全局 AITranslator 单例。"""
    global _translator
    if _translator is None:
        _translator = AITranslator()
    return _translator


def get_primary_provider(cfg: Optional[dict] = None) -> Optional[dict]:
    """
    返回首选（优先级最高）的有效 Provider dict，作为全系统 AI 的单一数据源。
    解析顺序：ai_providers 列表/字典 → 旧字段（sf_api_key / llm_base_url / llm_model_name）。
    无任何可用配置时返回 None（调用方据此跳过 AI 功能）。
    供 emby(打标) / douban_service(推理) / organize_service(解析) 等单模型调用方使用。
    """
    providers = AITranslator._resolve_providers(cfg=cfg, log_invalid=False)
    return providers[0] if providers else None
