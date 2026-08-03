"""request_budget 测试 — 滑动窗口计数令牌桶核心 + 配置加载。

P3 3c 请求预算：进程级每 Provider 请求预算，超限排队等待（默认 30s），仍超限跳过。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import services.request_budget as rb


@pytest.fixture
def clock(monkeypatch):
    """假时钟：_sleep 按秒推进假时间，_clock 读假时间，避免真实等待。"""
    state = {"t": 0.0}
    monkeypatch.setattr(rb, "_clock", lambda: state["t"])
    monkeypatch.setattr(rb, "_sleep", lambda s: state.__setitem__("t", state["t"] + s))
    rb.reset()
    yield state
    rb.reset()


def test_acquire_allows_up_to_limit(clock):
    rb.configure({"douban": 2, "tmdb": 2, "emby_writeback": 2})
    assert rb.acquire("douban", timeout=0) is True
    assert rb.acquire("douban", timeout=0) is True
    # 超限且 timeout=0 → 立即跳过
    assert rb.acquire("douban", timeout=0) is False


def test_provider_budget_waits_for_window_expiry(clock):
    b = rb.ProviderBudget(limit=1, window_seconds=1.0)
    assert b.acquire(timeout=5) is True   # t=0 获取一个额度
    assert b.acquire(timeout=5) is True   # 窗口满 → 等到旧 token 过期后成功
    assert clock["t"] >= 1.0


def test_provider_budget_skips_after_timeout(clock):
    b = rb.ProviderBudget(limit=1, window_seconds=60.0)
    assert b.acquire(timeout=5) is True
    assert b.acquire(timeout=1.0) is False  # 窗口 60s ≫ 超时 1s → 跳过
    assert clock["t"] >= 1.0


def test_provider_budget_prunes_old_timestamps(clock):
    b = rb.ProviderBudget(limit=1, window_seconds=1.0)
    assert b.acquire(timeout=5) is True
    clock["t"] += 2.0
    assert b.acquire(timeout=5) is True  # 旧时间戳滑出窗口，预算恢复


def test_acquire_unknown_provider_fail_open(clock):
    assert rb.acquire("nonexistent_provider") is True


def test_build_limits_defaults_when_config_missing(monkeypatch):
    monkeypatch.setattr(rb, "load_config", lambda: {})
    assert rb._build_limits() == {"douban": 30, "tmdb": 60, "emby_writeback": 50}


def test_build_limits_partial_overrides(monkeypatch):
    monkeypatch.setattr(rb, "load_config", lambda: {"request_budget": {"tmdb_per_min": 5}})
    limits = rb._build_limits()
    assert limits["tmdb"] == 5
    assert limits["douban"] == 30
    assert limits["emby_writeback"] == 50


def test_build_limits_falls_back_on_config_error(monkeypatch):
    def _boom():
        raise RuntimeError("config 损坏")
    monkeypatch.setattr(rb, "load_config", _boom)
    assert rb._build_limits() == {"douban": 30, "tmdb": 60, "emby_writeback": 50}
