"""
AI 翻译服务 — 使用 SiliconFlow (DeepSeek) 将英文演员名/角色名翻译为中文。
"""

import logging
from openai import OpenAI
from config.settings import load_config

logger = logging.getLogger("uvicorn")


class AITranslator:
    """轻量级 AI 翻译器。"""

    def __init__(self):
        cfg = load_config()
        self.api_key = cfg.get("sf_api_key", "")
        self.client = None
        if self.api_key:
            self.client = OpenAI(
                api_key=self.api_key,
                base_url="https://api.siliconflow.cn/v1"
            )

    def is_available(self) -> bool:
        return self.client is not None

    def translate_names(self, names: list[str], context: str = "") -> dict[str, str]:
        """批量翻译人名（音译模式）。

        返回 {original_name: translated_name} 映射。
        """
        if not self.client or not names:
            return {}

        prompt = f"""请将以下英文/拼音人名翻译为中文人名。
规则：
1. 使用常见中文译名，不要直译
2. 如果名字已经是中文，保持不变
3. 只返回 JSON 格式: {{"英文名": "中文名", ...}}
4. 不要包含任何解释或 Markdown
"""
        if context:
            prompt += f"\n背景: 这是影视作品《{context}》的演员表。"

        prompt += f"\n\n待翻译人名:\n{chr(10).join(names)}"

        try:
            response = self.client.chat.completions.create(
                model="deepseek-ai/DeepSeek-V3",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=2000,
            )
            content = response.choices[0].message.content or "{}"
            content = content.replace("```json", "").replace("```", "").strip()
            import json
            return json.loads(content)
        except Exception as e:
            logger.warning(f"   ⚠️ [AI翻译] 人名翻译失败: {e}")
            return {}

    def translate_roles(self, roles: list[str], context: str = "") -> dict[str, str]:
        """批量翻译角色名。

        返回 {original_role: translated_role} 映射。
        """
        if not self.client or not roles:
            return {}

        prompt = f"""请将以下英文角色名翻译为中文。
规则:
1. 翻译为自然的中文角色名
2. 如果已经是中文，保持不变
3. 只返回 JSON 格式: {{"英文角色名": "中文角色名", ...}}
4. 不要包含任何解释或 Markdown
"""
        if context:
            prompt += f"\n背景: 这是影视作品《{context}》的角色表。"

        prompt += f"\n\n待翻译角色名:\n{chr(10).join(roles)}"

        try:
            response = self.client.chat.completions.create(
                model="deepseek-ai/DeepSeek-V3",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=2000,
            )
            content = response.choices[0].message.content or "{}"
            content = content.replace("```json", "").replace("```", "").strip()
            import json
            return json.loads(content)
        except Exception as e:
            logger.warning(f"   ⚠️ [AI翻译] 角色翻译失败: {e}")
            return {}


# 全局单例
_translator: AITranslator | None = None


def get_translator() -> AITranslator:
    global _translator
    if _translator is None:
        _translator = AITranslator()
    return _translator
