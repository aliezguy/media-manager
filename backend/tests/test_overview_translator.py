"""全库简介汉化 — 工具函数单元测试。

覆盖 translation_utils 新增的：
- chinese_char_ratio（中文比例）
- is_already_chinese（全局检测跳过判定）
- is_valid_overview_translation（长文本中文有效性验收，含重复幻觉拦截）
- should_protect_overview / apply_overview_with_guard（防覆盖守卫）

以及 overview_translator 的：
- needs_overview_translation（入队判定）
- translate_overview（本地→云端双重引擎级联）

全部 mock LLM 层，不触网。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from types import SimpleNamespace

import pytest

from services import translation_utils as tu


# ================================================================
# chinese_char_ratio: 中文占「非空白有效字符」比例
# ================================================================

def test_ratio_empty_zero():
    assert tu.chinese_char_ratio("") == 0.0
    assert tu.chinese_char_ratio(None) == 0.0


def test_ratio_pure_english_zero():
    assert tu.chinese_char_ratio("Hello world this is a movie") == 0.0


def test_ratio_pure_chinese_one():
    assert tu.chinese_char_ratio("你好世界") == 1.0


def test_ratio_mixed():
    # "你好 world" → 有效字符 7 个，中文 2 个
    assert abs(tu.chinese_char_ratio("你好 world") - 2 / 7) < 1e-6


# ================================================================
# is_already_chinese: 是否已含足够比例中文（跳过判定）
# ================================================================

def test_already_chinese_empty_false():
    assert tu.is_already_chinese("") is False


def test_already_chinese_pure_chinese_true():
    assert tu.is_already_chinese("这是一部关于侦探的电影") is True


def test_already_chinese_english_false():
    assert tu.is_already_chinese("A hero story") is False


def test_already_chinese_low_ratio_false():
    # 中文占比 2/7 < 0.5 → 判定为未充分中文化
    assert tu.is_already_chinese("你好 world", ratio=0.5) is False


# ================================================================
# is_valid_overview_translation: 长文本中文有效性验收
# ================================================================

def test_valid_pure_english_false():
    assert tu.is_valid_overview_translation(
        "This is a movie about a detective in Tokyo"
    ) is False


def test_valid_pure_chinese_true():
    assert tu.is_valid_overview_translation(
        "这是一部关于东京侦探的精彩电影，讲述了一个悬疑故事。"
    ) is True


def test_valid_english_with_one_hanzi_false():
    # 伪中文：纯英文夹 1 个汉字 → 中文占比过低，拒绝
    low_ratio = (
        "A story about a hero 的 adventures in the city of Tokyo follows the "
        "young man discovering his true identity while fighting the forces of "
        "evil in a dystopian future"
    )
    assert tu.is_valid_overview_translation(low_ratio) is False


def test_valid_repetition_hallucination_false():
    # 同字符连续重复 8+ 次（幻觉标志）
    assert tu.is_valid_overview_translation("好" * 20) is False


def test_valid_block_repetition_false():
    # 12+ 字符块出现 3+ 次（复读幻觉）
    block = "这是一个关于冒险的精彩故事"
    assert tu.is_valid_overview_translation(block * 3) is False


def test_valid_empty_false():
    assert tu.is_valid_overview_translation("") is False
    assert tu.is_valid_overview_translation(None) is False


# ================================================================
# should_protect_overview / apply_overview_with_guard: 防覆盖守卫
# ================================================================

def test_protect_ai_source_vs_english_true():
    assert tu.should_protect_overview(tu.SOURCE_LOCAL_LLM, "An English overview") is True
    assert tu.should_protect_overview(tu.SOURCE_CLOUD_LLM, "An English overview") is True


def test_protect_ai_source_vs_chinese_false():
    assert tu.should_protect_overview(tu.SOURCE_LOCAL_LLM, "这是中文简介") is False


def test_protect_ai_source_vs_empty_true():
    # 空字符串不算中文 → 同样拒绝覆盖
    assert tu.should_protect_overview(tu.SOURCE_LOCAL_LLM, "") is True


def test_protect_non_ai_source_false():
    assert tu.should_protect_overview("", "An English overview") is False
    assert tu.should_protect_overview(tu.SOURCE_OFFICIAL, "An English overview") is False
    assert tu.should_protect_overview(None, "An English overview") is False


def test_apply_guard_blocked():
    rec = SimpleNamespace(overview="这是中文简介", overview_source=tu.SOURCE_LOCAL_LLM)
    assert tu.apply_overview_with_guard(rec, "An English overview") is False
    assert rec.overview == "这是中文简介"
    assert rec.overview_source == tu.SOURCE_LOCAL_LLM


def test_apply_guard_chinese_overwrite_official():
    rec = SimpleNamespace(overview="old english", overview_source="")
    assert tu.apply_overview_with_guard(rec, "这是官方中文简介") is True
    assert rec.overview == "这是官方中文简介"
    assert rec.overview_source == tu.SOURCE_OFFICIAL


def test_apply_guard_english_empty_source():
    rec = SimpleNamespace(overview="old", overview_source="")
    assert tu.apply_overview_with_guard(rec, "English overview") is True
    assert rec.overview == "English overview"
    assert rec.overview_source == ""


# ================================================================
# needs_overview_translation: 入队判定（空/已中文 → 跳过；非中文 → 翻译）
# ================================================================

import services.overview_translator as ot


def test_needs_empty_false():
    assert ot.needs_overview_translation("") is False
    assert ot.needs_overview_translation("   ") is False
    assert ot.needs_overview_translation(None) is False


def test_needs_english_true():
    assert ot.needs_overview_translation("This is a movie about a detective") is True


def test_needs_chinese_false():
    assert ot.needs_overview_translation("这是一部关于侦探的电影") is False


def test_needs_low_ratio_chinese_true():
    # 中文占比不足阈值 → 判定为非充分中文化，仍入队
    assert ot.needs_overview_translation("你好 world", ratio=0.5) is True


# ================================================================
# translate_overview: 本地(qwen) → 云端双重引擎级联 + 来源审计
# ================================================================

def _fake_translator(return_value):
    """构造返回固定结果的 fake get_translator，并记录调用参数。"""
    calls = []

    def fake_get_translator():
        fake = SimpleNamespace()
        fake.chat_null_aware = lambda **kw: calls.append(kw) or return_value
        return fake

    return fake_get_translator, calls


def test_translate_local_success(monkeypatch):
    fake_get, calls = _fake_translator(("这是中文简介", "qwen2.5:7b", set()))
    monkeypatch.setattr(ot, "get_translator", fake_get)
    text, source, nulls = ot.translate_overview("English overview")
    assert text == "这是中文简介"
    assert source == "local_llm"
    assert nulls == set()
    # 关键参数透传：中文验收门 + 本地优先
    assert calls[0]["validator"] is tu.is_valid_overview_translation
    assert calls[0]["local_first"] is True


def test_translate_cloud_fallback_success(monkeypatch):
    fake_get, calls = _fake_translator(("这是中文简介", "deepseek-ai/DeepSeek-V3", {"qwen2.5:7b"}))
    monkeypatch.setattr(ot, "get_translator", fake_get)
    text, source, nulls = ot.translate_overview("English overview")
    assert text == "这是中文简介"
    assert source == "cloud_llm"
    assert nulls == {"qwen2.5:7b"}


def test_translate_all_fail(monkeypatch):
    fake_get, calls = _fake_translator((None, None, {"qwen2.5:7b", "deepseek"}))
    monkeypatch.setattr(ot, "get_translator", fake_get)
    text, source, nulls = ot.translate_overview("English overview")
    assert text is None
    assert source == "failed"
    assert nulls == {"qwen2.5:7b", "deepseek"}


def test_translate_honors_config(monkeypatch):
    fake_get, calls = _fake_translator(("这是中文简介", "qwen2.5:7b", set()))
    monkeypatch.setattr(ot, "get_translator", fake_get)
    cfg = {"overview_local_first": False, "overview_max_tokens": 999}
    ot.translate_overview("English overview", cfg=cfg)
    assert calls[0]["local_first"] is False
    assert calls[0]["max_tokens"] == 999
