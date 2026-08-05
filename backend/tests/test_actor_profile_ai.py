"""演员元数据 AI 补全/汉化测试 — 出生地汉化 + 空值补全 + strict-NULL + 冷静期状态机。

全部 mock LLM 层（actor_profile_ai._chat / ai_translator 内部），不触网。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import ActorProfile
import services.actor_profile_ai as apa
import services.actor_profile_service as aps
from services.ai_translator import AITranslator


def _mem_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def _cfg_with_ai():
    return {
        "douban_enabled": True, "enable_emby_avatar_first": False,
        "douban_cookie": "", "tmdb_api_key": "",
        "sf_api_key": "x", "llm_base_url": "https://api.test/v1", "llm_model_name": "m",
        "actor_ai_enabled": True, "actor_ai_local_first": True, "llm_cooldown_days": 7,
    }


# ================================================================
# translate_birth_place: 出生地地理翻译
# ================================================================

def test_translate_birth_place_empty_returns_empty(monkeypatch):
    called = []
    monkeypatch.setattr(apa, "_chat", lambda *a, **k: called.append(1) or "X")
    assert apa.translate_birth_place("", "张译") == ""
    assert called == [], "空输入不应触发 LLM"


def test_translate_birth_place_chinese_kept(monkeypatch):
    called = []
    monkeypatch.setattr(apa, "_chat", lambda *a, **k: called.append(1) or "X")
    assert apa.translate_birth_place("中国辽宁省大连市", "张译") == "中国辽宁省大连市"
    assert called == [], "已是中文不应触发 LLM"


def test_translate_birth_place_english_translated(monkeypatch):
    monkeypatch.setattr(apa, "_chat", lambda *a, **k: "加拿大安大略省多伦多")
    assert apa.translate_birth_place("Toronto, Ontario, Canada", "ActorA") == "加拿大安大略省多伦多"


def test_translate_birth_place_null_keeps_original(monkeypatch):
    monkeypatch.setattr(apa, "_chat", lambda *a, **k: "NULL")
    assert apa.translate_birth_place("Toronto, Canada", "ActorA") == "Toronto, Canada"


def test_translate_birth_place_garbage_keeps_original(monkeypatch):
    # 模型返回非中文（未正确汉化）→ 绝不写入，保留原值
    monkeypatch.setattr(apa, "_chat", lambda *a, **k: "Toronto Canada!!")
    assert apa.translate_birth_place("Toronto, Canada", "ActorA") == "Toronto, Canada"


# ================================================================
# fill_birth_place: 空出生地知识生成（strict-NULL）
# ================================================================

def test_fill_birth_place_knowledge_hit(monkeypatch):
    monkeypatch.setattr(apa, "_chat", lambda *a, **k: "中国黑龙江省哈尔滨市")
    assert apa.fill_birth_place("张译") == "中国黑龙江省哈尔滨市"


def test_fill_birth_place_null_returns_empty(monkeypatch):
    monkeypatch.setattr(apa, "_chat", lambda *a, **k: "NULL")
    assert apa.fill_birth_place("冷门演员") == ""


def test_fill_birth_place_empty_name(monkeypatch):
    called = []
    monkeypatch.setattr(apa, "_chat", lambda *a, **k: called.append(1) or "X")
    assert apa.fill_birth_place("") == ""
    assert called == []


# ================================================================
# ensure_actor_overview: 简介 空→生成 / 非中文→翻译 / 中文→原样
# ================================================================

def test_overview_empty_generated(monkeypatch):
    monkeypatch.setattr(apa, "_chat", lambda *a, **k: "张译，中国内地男演员……")
    assert apa.ensure_actor_overview("张译", "") == "张译，中国内地男演员……"


def test_overview_empty_null_returns_empty(monkeypatch):
    monkeypatch.setattr(apa, "_chat", lambda *a, **k: "NULL")
    assert apa.ensure_actor_overview("冷门演员", "") == ""


def test_overview_english_translated(monkeypatch):
    monkeypatch.setattr(apa, "_chat", lambda *a, **k: "出生于北京的著名演员。")
    out = apa.ensure_actor_overview("张译", "Famous actor born in Beijing.")
    assert out == "出生于北京的著名演员。"


def test_overview_chinese_kept_no_llm(monkeypatch):
    called = []
    monkeypatch.setattr(apa, "_chat", lambda *a, **k: called.append(1) or "X")
    assert apa.ensure_actor_overview("张译", "已是中文简介。") == "已是中文简介。"
    assert called == [], "中文简介不应触发 LLM"


def test_overview_translate_failure_keeps_english(monkeypatch):
    monkeypatch.setattr(apa, "_chat", lambda *a, **k: "NULL")
    assert apa.ensure_actor_overview("A", "English bio here.") == "English bio here."


# ================================================================
# extract_birth_date: 出生日期提取
# ================================================================

def test_extract_birth_date_already_present_no_llm(monkeypatch):
    called = []
    monkeypatch.setattr(apa, "_chat", lambda *a, **k: called.append(1) or "X")
    assert apa.extract_birth_date("A", "1993-05-16", "some bio") == "1993-05-16"
    assert called == []


def test_extract_birth_date_no_overview_no_llm(monkeypatch):
    called = []
    monkeypatch.setattr(apa, "_chat", lambda *a, **k: called.append(1) or "X")
    assert apa.extract_birth_date("A", "", "") == ""
    assert called == []


def test_extract_birth_date_from_overview(monkeypatch):
    monkeypatch.setattr(apa, "_chat", lambda *a, **k: "1993-05-16")
    assert apa.extract_birth_date("A", "", "演员出生于1993年5月16日……") == "1993-05-16"


def test_extract_birth_date_null_returns_empty(monkeypatch):
    monkeypatch.setattr(apa, "_chat", lambda *a, **k: "NULL")
    assert apa.extract_birth_date("A", "", "一段没有生日的简介") == ""


def test_extract_birth_date_year_only(monkeypatch):
    monkeypatch.setattr(apa, "_chat", lambda *a, **k: "1993")
    assert apa.extract_birth_date("A", "", "出生于1993年。") == "1993"


def test_extract_birth_date_garbage_rejected(monkeypatch):
    monkeypatch.setattr(apa, "_chat", lambda *a, **k: "五月十六日")
    assert apa.extract_birth_date("A", "", "某简介") == ""


# ================================================================
# _clean_result: NULL 哨兵
# ================================================================

@pytest.mark.parametrize("raw", [
    None, "", "NULL", "null", "未知", "无", "N/A", "None", "  NULL  ",
])
def test_clean_result_treats_null_sentinels(raw):
    assert apa._clean_result(raw) is None


def test_clean_result_strips_quotes_and_fences():
    assert apa._clean_result('"中国辽宁省大连市"') == "中国辽宁省大连市"
    assert apa._clean_result("```\n韩国首尔\n```") == "韩国首尔"


# ================================================================
# enrich_actor_metadata: 冷静期 + 状态机
# ================================================================

def _profile(bp="", bd="", ov=""):
    return {"birth_place": bp, "birth_date": bd, "overview": ov}


def _cfg(cooldown=7):
    return {"llm_cooldown_days": cooldown, "actor_ai_local_first": True}


def test_enrich_all_chinese_no_work(monkeypatch):
    called = []
    monkeypatch.setattr(apa, "_chat", lambda *a, **k: called.append(1) or "X")
    data = _profile(bp="中国辽宁省大连市", bd="1993-05-16", ov="中文简介")
    out, status, last, _src, _fs = apa.enrich_actor_metadata("A", data, SimpleNamespace(llm_check_status=0), _cfg())
    assert status is None and last is None
    assert called == [], "全字段已就绪不应触发 LLM"


def test_enrich_status2_cooldown_blocks(monkeypatch):
    called = []
    monkeypatch.setattr(apa, "_chat", lambda *a, **k: called.append(1) or "X")
    existing = SimpleNamespace(llm_check_status=2, llm_last_checked=datetime.now())
    out, status, last, _src, _fs = apa.enrich_actor_metadata("A", _profile(), existing, _cfg(cooldown=7))
    assert status is None and last is None
    assert called == [], "冷静期内不得触发 LLM"


def test_enrich_status2_cooldown_infinite_blocks(monkeypatch):
    called = []
    monkeypatch.setattr(apa, "_chat", lambda *a, **k: called.append(1) or "X")
    existing = SimpleNamespace(llm_check_status=2, llm_last_checked=datetime.now())
    out, status, last, _src, _fs = apa.enrich_actor_metadata("A", _profile(), existing, _cfg(cooldown=-1))
    assert status is None
    assert called == [], "无限期冷静期(-1)不得重查"


def test_enrich_status2_cooldown_expired_retries(monkeypatch):
    monkeypatch.setattr(apa, "_chat", lambda *a, **k: "中国黑龙江省哈尔滨市")
    existing = SimpleNamespace(llm_check_status=2, llm_last_checked=datetime.now() - timedelta(days=8))
    out, status, last, _src, _fs = apa.enrich_actor_metadata("A", _profile(), existing, _cfg(cooldown=7))
    assert out["birth_place"] == "中国黑龙江省哈尔滨市"
    assert status == 1 and last is not None


def test_enrich_status2_cooldown_zero_always_retries(monkeypatch):
    monkeypatch.setattr(apa, "_chat", lambda *a, **k: "韩国首尔")
    existing = SimpleNamespace(llm_check_status=2, llm_last_checked=datetime.now())
    out, status, last, _src, _fs = apa.enrich_actor_metadata("A", _profile(), existing, _cfg(cooldown=0))
    assert out["birth_place"] == "韩国首尔"
    assert status == 1


def test_enrich_empty_all_null_sets_status2(monkeypatch):
    monkeypatch.setattr(apa, "_chat", lambda *a, **k: "NULL")
    out, status, last, _src, _fs = apa.enrich_actor_metadata("冷门演员", _profile(), SimpleNamespace(llm_check_status=0), _cfg())
    assert out["birth_place"] == "" and out["overview"] == ""
    assert status == 2 and last is not None, "全 NULL → 模型不知道 status=2"


def test_enrich_english_birthplace_translated_status1(monkeypatch):
    monkeypatch.setattr(apa, "_chat", lambda *a, **k: "加拿大安大略省多伦多")
    data = _profile(bp="Toronto, Ontario, Canada", ov="中文简介已有", bd="2000-01-01")
    out, status, last, _src, _fs = apa.enrich_actor_metadata("A", data, SimpleNamespace(llm_check_status=0), _cfg())
    assert out["birth_place"] == "加拿大安大略省多伦多"
    assert status == 1


def test_enrich_birthdate_extracted_from_generated_bio(monkeypatch):
    """出生地为空但模型不知道(NULL)，简介生成成功，再从未知简介中提取出生日期。"""
    def fake_chat(system_prompt, user_prompt, **k):
        if "生成一段专业的中文简介" in system_prompt:
            return "一段介绍演员的生平……出生于1988年……"
        if "提取演员的出生日期" in system_prompt:
            return "1988-06-22"
        return "NULL"  # 出生地知识生成 → 模型不知道
    monkeypatch.setattr(apa, "_chat", fake_chat)
    data = _profile()
    out, status, last, _src, _fs = apa.enrich_actor_metadata("A", data, SimpleNamespace(llm_check_status=0), _cfg())
    assert out["birth_place"] == "", "模型不知道出生地 → 保持为空，绝不编造"
    assert out["overview"].startswith("一段介绍")
    assert out["birth_date"] == "1988-06-22"
    assert status == 1


def test_enrich_no_provider_guard_returns_unchanged(monkeypatch):
    """无 LLM 调用且无工作 → (原数据, None, None)，不误判 status。"""
    called = []
    monkeypatch.setattr(apa, "_chat", lambda *a, **k: called.append(1) or "X")
    data = _profile(bp="中国北京", bd="1990-01-01", ov="中文")
    out, status, last, _src, _fs = apa.enrich_actor_metadata("A", data, SimpleNamespace(llm_check_status=0), _cfg())
    assert status is None
    assert called == []


# ================================================================
# chat_null_aware: 本地优先 + NULL 感知降级
# ================================================================

class _FakeResp:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeMessage:
    def __init__(self, content):
        self.content = content


def _fake_resolve(providers):
    def _resolve(cls, log_invalid=True):
        return providers
    return classmethod(_resolve)


def test_providers_local_first_reorders():
    local = {"name": "qwen", "model_name": "qwen2.5", "base_url": "http://localhost:11434/v1"}
    cloud1 = {"name": "deepseek", "model_name": "v3", "base_url": "https://api.siliconflow.cn/"}
    cloud2 = {"name": "gemini", "model_name": "x", "base_url": ""}
    out = AITranslator._providers_local_first([cloud1, local, cloud2])
    assert [p["name"] for p in out] == ["qwen", "deepseek", "gemini"]


def test_is_local_provider_heuristics():
    assert AITranslator._is_local_provider({"name": "本地qwen", "model_name": "qwen2.5"})
    assert AITranslator._is_local_provider({"base_url": "http://host.docker.internal:11434/v1"})
    assert AITranslator._is_local_provider({"is_local": True})
    assert not AITranslator._is_local_provider({"name": "deepseek", "base_url": "https://api.siliconflow.cn/"})


def test_chat_null_aware_local_first_then_cloud(monkeypatch):
    """cloud 排在前面，但本地 qwen 因 local_first 被调到最先；qwen 返回 NULL → 降级 cloud。"""
    t = AITranslator()
    cloud_first = [
        {"name": "cloud", "model_name": "gpt", "base_url": ""},
        {"name": "本地qwen", "model_name": "qwen2.5", "base_url": "http://localhost:11434/v1"},
    ]
    monkeypatch.setattr(AITranslator, "_resolve_providers", _fake_resolve(cloud_first))
    calls = []

    def fake_chat(*, provider, messages, temperature, max_tokens, require_json=True):
        calls.append(provider["name"])
        assert require_json is False, "null-aware 应纯文本输出（require_json=False）"
        if provider["name"] == "本地qwen":
            return _FakeResp("NULL")
        return _FakeResp("加拿大安大略省多伦多")

    monkeypatch.setattr(t, "_chat_with_address_fallback", fake_chat)
    out, src, nulls = t.chat_null_aware("sys", "user")
    assert calls == ["本地qwen", "cloud"], "本地优先 + NULL 后降级 cloud"
    assert out == "加拿大安大略省多伦多"
    assert src == "gpt", "应返回成功的大模型名"
    assert nulls == {"qwen2.5"}, "qwen 返回 NULL 应记入 null_models"


def test_chat_null_aware_all_null_returns_none(monkeypatch):
    t = AITranslator()
    monkeypatch.setattr(AITranslator, "_resolve_providers", _fake_resolve([
        {"name": "a", "model_name": "m1", "base_url": ""},
        {"name": "b", "model_name": "m2", "base_url": ""},
    ]))
    monkeypatch.setattr(
        t, "_chat_with_address_fallback",
        lambda *, provider, messages, temperature, max_tokens, require_json=True: _FakeResp("NULL"),
    )
    out, src, nulls = t.chat_null_aware("sys", "user")
    assert out is None and src is None
    assert nulls == {"m1", "m2"}, "全 NULL 时 null_models 应包含两个模型"


def test_chat_null_aware_no_providers_returns_none(monkeypatch):
    t = AITranslator()
    monkeypatch.setattr(AITranslator, "_resolve_providers", _fake_resolve([]))
    out, src, nulls = t.chat_null_aware("sys", "user")
    assert out is None and src is None and nulls == set()


def test_chat_null_aware_skip_skips_null_models(monkeypatch):
    """skip 参数：已「不知道」的模型在下一轮直接跳过（冷门演员优化）。"""
    t = AITranslator()
    providers = [
        {"name": "本地qwen", "model_name": "qwen2.5", "base_url": "http://localhost:11434/v1"},
        {"name": "cloud", "model_name": "gpt", "base_url": ""},
    ]
    monkeypatch.setattr(AITranslator, "_resolve_providers", _fake_resolve(providers))
    calls = []
    monkeypatch.setattr(
        t, "_chat_with_address_fallback",
        lambda *, provider, messages, temperature, max_tokens, require_json=True:
            calls.append(provider["model_name"]) or _FakeResp("某结果"),
    )
    out, src, nulls = t.chat_null_aware("sys", "user", skip={"qwen2.5"})
    assert calls == ["gpt"], "skip={'qwen2.5'} 应直接跳过本地 qwen"
    assert src == "gpt"


# ================================================================
# 翻译来源记录 + 跨字段 NULL 跳过优化
# ================================================================

def test_chat_threads_ctx_sources_and_skip(monkeypatch):
    """_chat 把成功大模型记入 ctx._sources、把 NULL 模型记入 ctx._skip，并传给下一轮。"""
    calls = []

    class _FakeTranslator:
        def chat_null_aware(self, system_prompt, user_prompt, temperature=0.1,
                            max_tokens=1000, local_first=True, skip=None):
            calls.append(skip)
            if skip is None:
                # 第一轮: qwen/deepseek NULL → gemini 成功
                return "中国黑龙江省哈尔滨市", "gemini-2.5-flash", {"qwen2.5", "deepseek-ai/DeepSeek-V3"}
            return "某简介", "gemini-2.5-flash", set()

    monkeypatch.setattr(apa, "get_translator", lambda: _FakeTranslator())
    monkeypatch.setattr(apa, "load_config", lambda: {"actor_ai_local_first": True})

    ctx = {}
    r1 = apa._chat("p1", "u1", ctx=ctx, field="birth_place")
    r2 = apa._chat("p2", "u2", ctx=ctx, field="overview")
    assert r1 == "中国黑龙江省哈尔滨市"
    assert r2 == "某简介"
    assert ctx["_pending"] == {
        "birth_place": "gemini-2.5-flash", "overview": "gemini-2.5-flash",
    }, "LLM 层只暂存候选来源，验收后提交"
    assert ctx["_skip"] == {"qwen2.5", "deepseek-ai/DeepSeek-V3"}
    assert calls[1] == {"qwen2.5", "deepseek-ai/DeepSeek-V3"}, "第二轮应带上首轮 NULL 模型作为 skip"

    # 领域函数验收提交后，才进入 _sources / _field_sources
    apa._commit_source(ctx, "birth_place")
    apa._commit_source(ctx, "overview")
    assert ctx["_sources"] == {"gemini-2.5-flash"}
    assert ctx["_field_sources"] == {
        "birth_place": "gemini-2.5-flash", "overview": "gemini-2.5-flash",
    }


def _ctx_aware_chat(system_prompt, user_prompt, temperature=0.1, max_tokens=1000, ctx=None, field=""):
    """模拟真实 _chat：NULL 由 qwen2.5 返回（记入 _skip），成功由 gemini 产出（暂存 _pending）。"""
    if "提取演员的出生日期" in system_prompt:
        content, source = "1988-06-22", "gemini-2.5-flash"
    elif "生成一段专业的中文简介" in system_prompt:
        content, source = "某演员的简介。", "gemini-2.5-flash"
    else:  # 出生地知识生成 → qwen 不认识
        content, source = "NULL", None
    if ctx is not None:
        if source and field:
            ctx.setdefault("_pending", {})[field] = source
        if content is None or str(content).strip().upper() == "NULL":
            ctx.setdefault("_skip", set()).add("qwen2.5")
    return content


def test_enrich_records_translation_source(monkeypatch):
    monkeypatch.setattr(apa, "_chat", _ctx_aware_chat)
    data = _profile()
    out, status, last, src, fs = apa.enrich_actor_metadata(
        "冷门演员", data, SimpleNamespace(llm_check_status=0), _cfg(),
    )
    assert status == 1
    assert src == "gemini-2.5-flash", "应记录成功产出数据的大模型"
    assert fs == {"overview": "gemini-2.5-flash", "birth_date": "gemini-2.5-flash"}
    assert out["overview"].startswith("某演员")
    assert out["birth_date"] == "1988-06-22"
    assert out["birth_place"] == "", "出生地 qwen 不认识 → 保持空，绝不编造"


def test_enrich_per_field_mixed_models(monkeypatch):
    """三个字段由三个不同模型产出 → per-field 映射精确记录。"""
    def fake_chat(system_prompt, user_prompt, temperature=0.1, max_tokens=1000, ctx=None, field=""):
        if field == "birth_place":
            if ctx is not None:
                ctx.setdefault("_pending", {})["birth_place"] = "qwen2.5"
            return "中国黑龙江省哈尔滨市"
        if field == "overview":
            if ctx is not None:
                ctx.setdefault("_pending", {})["overview"] = "gemini-2.5-flash"
            return "某演员简介。"
        if ctx is not None:
            ctx.setdefault("_pending", {})["birth_date"] = "deepseek-ai/DeepSeek-V3"
        return "1990-01-01"
    monkeypatch.setattr(apa, "_chat", fake_chat)
    data = _profile()
    out, status, last, src, fs = apa.enrich_actor_metadata(
        "张译", data, SimpleNamespace(llm_check_status=0), _cfg(),
    )
    assert status == 1
    assert src == "deepseek-ai/DeepSeek-V3,gemini-2.5-flash,qwen2.5"
    assert fs == {
        "birth_place": "qwen2.5",
        "overview": "gemini-2.5-flash",
        "birth_date": "deepseek-ai/DeepSeek-V3",
    }, "每个字段精确记录其翻译模型"


def test_merge_field_sources_merges_and_overrides():
    merged = apa.merge_field_sources(
        {"birth_place": "qwen2.5", "overview": "gemini-2.5-flash"},
        '{"overview": "deepseek-ai/DeepSeek-V3"}',
    )
    assert merged == {"birth_place": "qwen2.5", "overview": "deepseek-ai/DeepSeek-V3"}
    assert apa.merge_field_sources(None, {}) == {}


def test_enrich_reuses_skip_across_fields(monkeypatch):
    """优化验证：同一演员同一轮，前字段 NULL 的模型不会在后字段重复尝试。"""
    skipped = []

    class _FakeTranslator:
        def chat_null_aware(self, system_prompt, user_prompt, temperature=0.1,
                            max_tokens=1000, local_first=True, skip=None):
            skipped.append(skip)
            if "出生地" in system_prompt and skip is None:
                # 出生地：qwen 不认识 → gemini 成功，nulls 上报 qwen2.5
                return "中国黑龙江省哈尔滨市", "gemini-2.5-flash", {"qwen2.5"}
            if skip is not None:
                assert "qwen2.5" in skip, "后续字段应跳过已 NULL 的 qwen2.5"
            return "某简介", "gemini-2.5-flash", set()

    monkeypatch.setattr(apa, "get_translator", lambda: _FakeTranslator())
    monkeypatch.setattr(apa, "load_config", lambda: {"actor_ai_local_first": True})

    data = _profile()
    out, status, last, src, fs = apa.enrich_actor_metadata(
        "冷门演员", data, SimpleNamespace(llm_check_status=0), _cfg(),
    )
    assert status == 1
    assert any(s is not None and "qwen2.5" in s for s in skipped), "后续字段必须携带 skip=qwen2.5"
    assert src == "gemini-2.5-flash"


# ================================================================
# resolve_actor_profile 集成：提前返回路径也能汉化存量英文出生地
# ================================================================

def _ctx_bp_chat(system_prompt, user_prompt, temperature=0.1, max_tokens=1000, ctx=None, field=""):
    """模拟真实 _chat：产出中文出生地，并把成功大模型暂存到 ctx._pending。"""
    if ctx is not None:
        if field:
            ctx.setdefault("_pending", {})[field] = "gemini-2.5-flash"
    return "中国黑龙江省哈尔滨市"


def test_resolve_cache_hit_translates_birthplace(monkeypatch):
    """L0 数据库极速命中（有本地头像）→ 演员库显式 skip_llm_enrich=False 时仍汉化英文出生地并落库来源。

    默认 config（actor_bio_inline_enabled=False）下汉化/审计不再内联补简介（见 test_bio_skip.py）；
    本测试显式走演员库路径（skip_llm_enrich=False），验证早返回路径的补全机制本身仍然工作。
    """
    Session = _mem_db()
    db = Session()
    db.add(ActorProfile(
        name="Zhang Yi", local_image_path="张/张译/folder.png",
        birth_place="Harbin, Heilongjiang, China", source="tmdb",
        update_time=datetime.now(),
    ))
    db.flush()
    monkeypatch.setattr(aps, "load_config", lambda: _cfg_with_ai())
    monkeypatch.setattr(aps, "_local_file_exists", lambda p: True)
    monkeypatch.setattr(apa, "_chat", _ctx_bp_chat)

    prof = aps.resolve_actor_profile(
        "Zhang Yi", db, context_info={}, light_mode=True, skip_llm_enrich=False,
    )
    assert prof["birth_place"] == "中国黑龙江省哈尔滨市"
    row = db.query(ActorProfile).filter(ActorProfile.name == "Zhang Yi").first()
    assert row.llm_check_status == 1 and row.llm_last_checked is not None
    assert row.llm_translation_source == "gemini-2.5-flash", "应记录成功的大模型来源"
    # birth_place 与 overview 被验收填入；birth_date 返回的「中国黑龙江省哈尔滨市」非日期 → 被拒绝不记录
    assert row.llm_field_sources == {
        "birth_place": "gemini-2.5-flash", "overview": "gemini-2.5-flash",
    }, "应记录 per-field 来源 JSON（只含验收通过字段）"
    db.close()


def test_resolve_cooldown_translates_birthplace(monkeypatch):
    """头像冷却期内（无本地文件，最近更新过）→ 演员库显式 skip_llm_enrich=False 时仍汉化英文出生地。"""
    Session = _mem_db()
    db = Session()
    db.add(ActorProfile(
        name="Zhang Yi", birth_place="Harbin, Heilongjiang, China",
        update_time=datetime.now(),
    ))
    db.flush()
    monkeypatch.setattr(aps, "load_config", lambda: _cfg_with_ai())
    monkeypatch.setattr(aps, "_find_local_avatar", lambda name: None)
    monkeypatch.setattr(apa, "_chat", lambda *a, **k: "中国黑龙江省哈尔滨市")

    prof = aps.resolve_actor_profile(
        "Zhang Yi", db, context_info={}, light_mode=True, skip_llm_enrich=False,
    )
    assert prof["birth_place"] == "中国黑龙江省哈尔滨市"
    db.close()


def test_resolve_cooldown_status2_skips_llm(monkeypatch):
    """LLM 冷静期拦截：status=2 且 7 天内查过 → 不调用 LLM，保留原值。"""
    Session = _mem_db()
    db = Session()
    db.add(ActorProfile(
        name="Zhang Yi", birth_place="Harbin, Canada",
        llm_check_status=2, llm_last_checked=datetime.now(),
        update_time=datetime.now(),
    ))
    db.flush()
    called = []
    monkeypatch.setattr(aps, "load_config", lambda: _cfg_with_ai())
    monkeypatch.setattr(aps, "_find_local_avatar", lambda name: None)
    monkeypatch.setattr(apa, "_chat", lambda *a, **k: called.append(1) or "X")

    prof = aps.resolve_actor_profile("Zhang Yi", db, context_info={}, light_mode=True)
    assert prof["birth_place"] == "Harbin, Canada"
    assert called == [], "冷静期内不得触发 LLM"
    db.close()


def test_resolve_no_ai_config_skips_llm(monkeypatch):
    """无 AI Provider 配置 → 提前返回路径整体跳过 LLM（回归 test_light_mode 场景）。"""
    Session = _mem_db()
    db = Session()
    db.add(ActorProfile(
        name="Zhang Yi", birth_place="Harbin, Canada",
        update_time=datetime.now(),
    ))
    db.flush()
    called = []
    monkeypatch.setattr(aps, "load_config", lambda: {
        "douban_enabled": True, "enable_emby_avatar_first": False,
        "douban_cookie": "", "tmdb_api_key": "",
    })
    monkeypatch.setattr(aps, "_find_local_avatar", lambda name: None)
    monkeypatch.setattr(apa, "_chat", lambda *a, **k: called.append(1) or "X")

    prof = aps.resolve_actor_profile("Zhang Yi", db, context_info={}, light_mode=True)
    assert prof["birth_place"] == "Harbin, Canada"
    assert called == []
    db.close()
