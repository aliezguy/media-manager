"""
AI 翻译服务 — 通用 LLM 翻译，兼容任意 OpenAI SDK 接口的大模型。

每次调用时动态读取系统最新配置（API Key / Base URL / Model），
支持热切换模型而无需重启服务。

内置 429 限流智能重试 + 请求间隔（减速带），防止免费 API 被限频。
"""

import json as _json
import logging
import time
import random
from openai import OpenAI
from config.settings import load_config

logger = logging.getLogger("uvicorn")

# ---------------------------------------------------------------------------
# 全局单例（延迟初始化，但每次调用会重新读取 config）
# ---------------------------------------------------------------------------

_translator: "AITranslator | None" = None


def get_translator() -> "AITranslator":
    """获取全局 AITranslator 单例。"""
    global _translator
    if _translator is None:
        _translator = AITranslator()
    return _translator


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _is_rate_limit_error(exc: Exception) -> bool:
    """判断异常是否为 429 限流错误。"""
    msg = str(exc).lower()
    return any(kw in msg for kw in ("429", "rate limit", "rate_limit", "过大", "访问量过大", "too many requests"))


def _rate_limit_sleep(label: str = ""):
    """随机休眠 1.5~3.0 秒，打散请求频率。"""
    delay = random.uniform(1.5, 3.0)
    if label:
        logger.debug("   🐢 [减速] %s休眠 %.1fs", label, delay)
    time.sleep(delay)


# ---------------------------------------------------------------------------
# 翻译器
# ---------------------------------------------------------------------------

class AITranslator:
    """轻量级 AI 翻译器 — 每次调用动态读取系统最新配置。"""

    # ------------------------------------------------------------------
    # 内部 helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_client() -> OpenAI | None:
        """根据当前 config 构建 OpenAI 客户端。

        若 sf_api_key 为空则返回 None（调用方应直接返回原文）。
        """
        cfg = load_config()
        api_key = (cfg.get("sf_api_key") or "").strip()
        if not api_key:
            return None

        base_url = (cfg.get("llm_base_url") or "").strip()
        kwargs: dict = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        return OpenAI(**kwargs)

    def is_available(self) -> bool:
        """当前是否可发起 AI 请求。"""
        return self._build_client() is not None

    # ------------------------------------------------------------------
    # 核心 API 调用（含 429 重试 + 减速）
    # ------------------------------------------------------------------

    @staticmethod
    def _chat_complete_with_retry(
        client: OpenAI,
        model: str,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
    ) -> str:
        """发起 chat.completions.create，遇到 429 自动冷却 5s 并重试一次。

        Returns:
            response.choices[0].message.content (str)，失败抛原始异常。
        """
        last_exc = None
        for attempt in (1, 2):
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                return response.choices[0].message.content or "{}"
            except Exception as e:
                last_exc = e
                if attempt == 1 and _is_rate_limit_error(e):
                    logger.warning(
                        "   ⚠️ [429限流] 请求被限频，冷却 5s 后重试（第 1 次）…"
                    )
                    time.sleep(5.0)
                    continue
                raise last_exc

    # ------------------------------------------------------------------
    # 人名翻译
    # ------------------------------------------------------------------

    def translate_names(
        self, names: list[str], context: str = ""
    ) -> dict[str, str]:
        """批量翻译演员人名（音译优先，已中文保持不变）。

        返回 {original_name: translated_name} 映射。
        失败或 API Key 为空时返回原文（不抛异常）。
        """
        if not names:
            return {}

        client = self._build_client()
        if client is None:
            logger.info("   ℹ️ [AI翻译] 未配置 sf_api_key，跳过人名翻译")
            return {n: n for n in names}

        cfg = load_config()
        model = (cfg.get("llm_model_name") or "").strip() or "deepseek-ai/DeepSeek-V3"

        # 去重，保持顺序
        unique_names = list(dict.fromkeys(names))

        prompt = (
            "你是一个专业的影视翻译助理。请将以下演员人名翻译为中文。\n"
            "要求：\n"
            "1. 已是中文的保持不变。\n"
            "2. 日文尽量保留原汉字。\n"
            "3. 使用常见中文译名，不要直译。\n"
            "4. 仅输出翻译后的名字，绝不输出解释、标点或多余字符。\n"
            "5. 返回 JSON 格式: {\"英文名\": \"中文名\", ...}\n"
        )
        if context:
            prompt += f"\n背景: 这是影视作品《{context}》的演员表。"

        prompt += f"\n\n待翻译人名:\n" + "\n".join(unique_names)

        try:
            content = self._chat_complete_with_retry(
                client=client,
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=2000,
            )
            content = content.replace("```json", "").replace("```", "").strip()
            result = _json.loads(content)
            # 兜底: 未返回的 key 填回原文
            out: dict[str, str] = {}
            for n in names:
                out[n] = result.get(n, n)
            return out
        except Exception as e:
            logger.warning("   ⚠️ [AI翻译] 人名翻译失败: %s", e)
            return {n: n for n in names}
        finally:
            _rate_limit_sleep("[人名翻译]")

    # ------------------------------------------------------------------
    # 角色名翻译
    # ------------------------------------------------------------------

    def translate_roles(
        self, roles: list[str], context: str = ""
    ) -> dict[str, str]:
        """批量翻译角色名。

        返回 {original_role: translated_role} 映射。
        失败或 API Key 为空时返回原文（不抛异常）。
        """
        if not roles:
            return {}

        client = self._build_client()
        if client is None:
            logger.info("   ℹ️ [AI翻译] 未配置 sf_api_key，跳过角色翻译")
            return {r: r for r in roles}

        cfg = load_config()
        model = (cfg.get("llm_model_name") or "").strip() or "deepseek-ai/DeepSeek-V3"

        # 去重，保持顺序
        unique_roles = list(dict.fromkeys(roles))

        prompt = (
            "你是一个专业的影视翻译助理。请将以下角色名翻译为中文。\n"
            "要求：\n"
            "1. 已是中文的保持不变。\n"
            "2. 日文尽量保留原汉字。\n"
            "3. 仅输出翻译后的名字，绝不输出解释、标点或多余字符。\n"
            "4. 返回 JSON 格式: {\"英文角色名\": \"中文角色名\", ...}\n"
        )
        if context:
            prompt += f"\n背景: 这是影视作品《{context}》的角色表。"

        prompt += f"\n\n待翻译角色名:\n" + "\n".join(unique_roles)

        try:
            content = self._chat_complete_with_retry(
                client=client,
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=2000,
            )
            content = content.replace("```json", "").replace("```", "").strip()
            result = _json.loads(content)
            # 兜底: 未返回的 key 填回原文
            out: dict[str, str] = {}
            for r in roles:
                out[r] = result.get(r, r)
            return out
        except Exception as e:
            logger.warning("   ⚠️ [AI翻译] 角色翻译失败: %s", e)
            return {r: r for r in roles}
        finally:
            _rate_limit_sleep("[角色翻译]")
