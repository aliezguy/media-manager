"""request_budget 测试 — 滑动窗口计数令牌桶核心 + 配置加载。

P3 3c 请求预算：进程级每 Provider 请求预算，超限排队等待（默认 30s），仍超限跳过。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import services.request_budget as rb
import services.actor_profile_service as aps
from services.douban_api import DoubanApi


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


# ==================== 接入点 1：DoubanApi.__invoke / __post ====================

class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeSession:
    """记录调用并返回固定 payload 的假 session。"""
    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def get(self, *a, **k):
        self.calls.append(("get", a, k))
        return _FakeResp(self._payload)

    def post(self, *a, **k):
        self.calls.append(("post", a, k))
        return _FakeResp(self._payload)


def _make_douban_api(monkeypatch):
    api = DoubanApi()
    monkeypatch.setattr(DoubanApi, "_apply_cooldown", lambda: None)  # 跳过 1.5s 冷却
    return api


def test_douban_invoke_acquires_budget_and_proceeds(monkeypatch):
    import services.douban_api as da
    api = _make_douban_api(monkeypatch)
    fake = _FakeSession({"foo": "bar"})
    monkeypatch.setattr(DoubanApi, "_session", fake)
    calls = []
    monkeypatch.setattr(da, "budget_acquire", lambda provider, timeout=30.0: calls.append(provider) or True)

    result = api._DoubanApi__invoke("/search/weixin", q="测试")

    assert calls == ["douban"]
    assert fake.calls and fake.calls[0][0] == "get"
    assert result == {"foo": "bar"}


def test_douban_invoke_skips_when_budget_exhausted(monkeypatch):
    import services.douban_api as da
    api = _make_douban_api(monkeypatch)
    monkeypatch.setattr(da, "budget_acquire", lambda provider, timeout=30.0: False)

    result = api._DoubanApi__invoke("/search/weixin", q="测试")

    assert result["error"] == "budget_exhausted"


def test_douban_post_acquires_budget_and_proceeds(monkeypatch):
    import services.douban_api as da
    api = _make_douban_api(monkeypatch)
    fake = _FakeSession({"id": 1})
    monkeypatch.setattr(DoubanApi, "_session", fake)
    calls = []
    monkeypatch.setattr(da, "budget_acquire", lambda provider, timeout=30.0: calls.append(provider) or True)

    result = api._DoubanApi__post("/movie/1", name="x")

    assert calls == ["douban"]
    assert fake.calls and fake.calls[0][0] == "post"
    assert result == {"id": 1}


def test_douban_post_skips_when_budget_exhausted(monkeypatch):
    import services.douban_api as da
    api = _make_douban_api(monkeypatch)
    monkeypatch.setattr(da, "budget_acquire", lambda provider, timeout=30.0: False)

    result = api._DoubanApi__post("/movie/1", name="x")

    assert result["error"] == "budget_exhausted"


# ==================== 接入点 2：actor_profile_service._tmdb_request ====================

def test_tmdb_request_acquires_budget_and_proceeds(monkeypatch):
    calls = []
    monkeypatch.setattr(aps, "budget_acquire", lambda provider, timeout=30.0: calls.append(provider) or True)
    fake = _FakeSession({"page": 1})
    monkeypatch.setattr(aps, "_requests", fake)

    resp = aps._tmdb_request("http://tmdb.test/x", {"api_key": "k"})

    assert calls == ["tmdb"]
    assert fake.calls and fake.calls[0][0] == "get"


def test_tmdb_request_skips_when_budget_exhausted(monkeypatch):
    calls = []
    monkeypatch.setattr(aps, "budget_acquire", lambda provider, timeout=30.0: calls.append(provider) or False)

    class _NoGet:
        def get(self, *a, **k):
            raise AssertionError("预算超限不应发起网络请求")

    monkeypatch.setattr(aps, "_requests", _NoGet())

    resp = aps._tmdb_request("http://tmdb.test/x", {"api_key": "k"})

    assert resp is None
    assert calls == ["tmdb"]
