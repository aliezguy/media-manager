"""translation_utils 测试 — 纯净缓存防伪污染核心判据。"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.translation_utils import (
    is_valid_chinese_translation,
    SOURCE_OFFICIAL, SOURCE_AI_FALLBACK, SOURCE_AI_DIRECT, SOURCE_MANUAL,
    CONFIDENCE_REUSE_THRESHOLD, CONFIDENCE_MANUAL, CONFIDENCE_OFFICIAL,
    CONFIDENCE_AI_FALLBACK, CONFIDENCE_AI_DIRECT, CONFIDENCE_NONE,
)


def test_contains_chinese_returns_true():
    assert is_valid_chinese_translation("张译") is True
    assert is_valid_chinese_translation("沃尔特·怀特") is True
    assert is_valid_chinese_translation("混排 En 中") is True


def test_english_only_returns_false():
    assert is_valid_chinese_translation("Bryan Cranston") is False
    assert is_valid_chinese_translation("Walter White") is False
    assert is_valid_chinese_translation("Sun Hu") is False


def test_empty_and_non_string_return_false():
    assert is_valid_chinese_translation("") is False
    assert is_valid_chinese_translation(None) is False
    assert is_valid_chinese_translation("  ") is False
    assert is_valid_chinese_translation("123!@#") is False


def test_constants_consistency():
    # 置信度阶梯：5手动 > 4官方 > 3AI兜底 > 2AI直出 > 1未翻译
    assert CONFIDENCE_MANUAL == 5
    assert CONFIDENCE_OFFICIAL == 4
    assert CONFIDENCE_AI_FALLBACK == 3
    assert CONFIDENCE_AI_DIRECT == 2
    assert CONFIDENCE_NONE == 1
    # 复用门槛=2：只要翻译过（>=2）就先复用
    assert CONFIDENCE_REUSE_THRESHOLD == 2
    assert SOURCE_MANUAL == "manual"
    assert SOURCE_OFFICIAL == "official"
    assert SOURCE_AI_FALLBACK == "ai_fallback"
    assert SOURCE_AI_DIRECT == "ai_direct"
