"""ai_translator 提示词测试 — 4 条 prompt 强化 + year 作品消歧。"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.ai_translator import AITranslator


# ---- _build_prompt: 通用规则 ----

def test_names_prompt_contains_pinyin_rule():
    p = AITranslator._build_prompt("names", "")
    assert '拼音条目（如 "Zhang San"）应译为汉字（"张三"）' in p


def test_names_prompt_handles_incomplete_names():
    p = AITranslator._build_prompt("names", "")
    assert '不完整的名字或仅含首字母的名字（如 "Peter J."）' in p


def test_prompt_enforces_simplified_chinese_guard():
    p = AITranslator._build_prompt("names", "")
    assert "无论原始名字是什么语言" in p
    assert "最终输出必须是简体中文" in p


# ---- _build_prompt: 角色语境判断 ----

def test_roles_prompt_contains_work_context_rule():
    p = AITranslator._build_prompt("roles", "梦比优斯奥特曼")
    assert "【角色语境判断】" in p
    assert "这部特定作品" in p
    # 无背景时（context 为空）不注入语境规则，避免孤立的"作品"引用
    p2 = AITranslator._build_prompt("roles", "")
    assert "【角色语境判断】" not in p2


# ---- _build_prompt: year 消歧渲染 ----

def test_context_with_year_renders_parenthesized():
    p = AITranslator._build_prompt("names", "梦比优斯奥特曼", "2006")
    assert "【背景】这是影视作品《梦比优斯奥特曼（2006）》的演员表。" in p


def test_context_without_year_keeps_plain_name():
    p = AITranslator._build_prompt("names", "梦比优斯奥特曼", "")
    assert "【背景】这是影视作品《梦比优斯奥特曼》的演员表。" in p


def test_roles_context_rule_appended_with_year():
    p = AITranslator._build_prompt("roles", "梦比优斯奥特曼", "2006")
    assert "《梦比优斯奥特曼（2006）》" in p
    assert "【角色语境判断】" in p


# ---- 向后兼容：translate_names / translate_roles 接受 year 参数 ----

def test_translate_names_accepts_year_kwarg_no_items():
    """空列表直接返回 {}，不触网，验证签名向后兼容。"""
    t = AITranslator()
    assert t.translate_names([], context="梦比优斯奥特曼", year="2006") == {}
    assert t.translate_roles([], context="梦比优斯奥特曼", year="2006") == {}


def test_translate_names_backward_compat_no_year():
    """不传 year（旧调用方式）仍可用。"""
    t = AITranslator()
    assert t.translate_names([], context="梦比优斯奥特曼") == {}
    assert t.translate_roles([]) == {}


# ================================================================
# chat_null_aware: validator 中文有效性门控（本地→云端降级）
# ================================================================

from types import SimpleNamespace


def _make_fake_provider(model_name, is_local=False):
    return {
        "name": model_name,
        "model_name": model_name,
        "base_url": "https://api.test/v1",
        "alt_base_url": None,
        "api_key": "x",
        "timeout": 10,
        "max_retries": 0,
        "is_local": is_local,
    }


def _make_resp(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _valid_chinese(text):
    """测试用替身校验器：仅接受含「中文」字样的内容。"""
    return isinstance(text, str) and "中文" in text


def test_chat_null_aware_validator_falls_to_next(monkeypatch):
    """第一个 Provider 返回英文（validator 拒绝）→ 无缝降级到下一个 Provider。"""
    t = AITranslator()
    local_p = _make_fake_provider("qwen2.5:7b", is_local=True)
    cloud_p = _make_fake_provider("deepseek-ai/DeepSeek-V3")
    monkeypatch.setattr(
        AITranslator, "_resolve_providers", lambda self, **kw: [local_p, cloud_p]
    )

    def fake_chat(self, *, provider, messages, temperature, max_tokens, require_json=False):
        if provider["model_name"].startswith("qwen"):
            return _make_resp("This is still English overview")
        return _make_resp("这是一部中文简介")

    monkeypatch.setattr(AITranslator, "_chat_with_address_fallback", fake_chat)
    content, model, nulls = t.chat_null_aware("sys", "user", validator=_valid_chinese)
    assert content == "这是一部中文简介"
    assert model == "deepseek-ai/DeepSeek-V3"
    assert "qwen2.5:7b" in nulls


def test_chat_null_aware_validator_accepts_valid_first(monkeypatch):
    """第一个 Provider 即通过 validator → 直接返回，不降级。"""
    t = AITranslator()
    p = _make_fake_provider("qwen2.5:7b", is_local=True)
    monkeypatch.setattr(AITranslator, "_resolve_providers", lambda self, **kw: [p])
    monkeypatch.setattr(
        AITranslator,
        "_chat_with_address_fallback",
        lambda self, **kw: _make_resp("这是一部中文简介"),
    )
    content, model, nulls = t.chat_null_aware("sys", "user", validator=_valid_chinese)
    assert content == "这是一部中文简介"
    assert model == "qwen2.5:7b"
    assert nulls == set()


def test_chat_null_aware_no_validator_accepts_first(monkeypatch):
    """不带 validator（默认 None）→ 首个非空结果直接接受（向后兼容回归）。"""
    t = AITranslator()
    p = _make_fake_provider("qwen2.5:7b", is_local=True)
    monkeypatch.setattr(AITranslator, "_resolve_providers", lambda self, **kw: [p])
    monkeypatch.setattr(
        AITranslator,
        "_chat_with_address_fallback",
        lambda self, **kw: _make_resp("This is English overview"),
    )
    content, model, nulls = t.chat_null_aware("sys", "user")
    assert content == "This is English overview"
    assert model == "qwen2.5:7b"
