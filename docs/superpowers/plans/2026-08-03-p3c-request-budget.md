# P3-3c 请求预算机制 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增进程级每 Provider 请求预算机制 `services/request_budget.py`（滑动窗口计数令牌桶），在既有冷却之上加强制上限。超限时排队等待（默认 30s 超时），仍超限则跳过并记日志——避免一次 webhook 触发的系列汉化打爆豆瓣/TMDB/Emby。

**Architecture:** `ProviderBudget` 滑动窗口计数（deque 存时间戳，窗口内 ≤ limit 个），模块级单例 `acquire/reset/configure`。配置从 `load_config().request_budget` 读取（`douban_per_series` / `tmdb_per_min` / `emby_writeback_per_series`，缺失自动补默认）。三个接入点各注入一行 `budget_acquire(provider)` 前置检查，返回 False 即跳过并记日志：① `DoubanApi.__invoke/__post`（`douban_api.py`，唯一 Frodo 主通道汇聚点）；② `actor_profile_service._tmdb_request`（唯一 TMDB API 汇聚点）；③ `douban_service._write_back_episode`（模块级 `requests.post`）。fail-open：未知 provider / 未初始化 → 放行。

**Tech Stack:** Python 3.13 / threading / collections.deque / config.settings.load_config / pytest（sqlite :memory: 测试约定，monkeypatch）

## Global Constraints

- 测试运行目录：`cd backend`，venv 解释器 `venv/bin/python -m pytest`。聚焦 `tests/test_request_budget.py -v`；全量 `tests/ -q`。
- **不重写既有冷却**：`DoubanApi._apply_cooldown`（1.5s 类级锁）、`_frodo_get`/`_http_get` 随机 sleep 全部保留，`request_budget` 只在之上叠加 `acquire()`。
- **fail-open**：未知 provider 或预算未初始化 → `acquire` 返回 `True` 放行，绝不因预算机制破坏既有请求行为。
- **超限策略**：排队等待，默认超时 `_DEFAULT_TIMEOUT = 30.0` 秒；仍超限返回 `False`，调用方跳过并记 `logger.warning`。
- **配置节**（`settings.py` 的 `DEFAULT_CONFIG.request_budget`，`load_config` 缺失键自动补默认）：`douban_per_series: 30`、`tmdb_per_min: 60`、`emby_writeback_per_series: 50`。
- **窗口语义（代码常量，非配置）**：key 后缀 `_per_min` → 60s 滚动窗口；`_per_series` → 600s 固定滑动窗口（近似「每系列」突发预算）。`config.json` 只保留设计文档的三个标量键，窗口秒数不进配置。
- **语义对齐唯一标准**：`budget_acquire(provider)` 返回 `False` = 跳过（不发起请求）；`True` = 继续原流程。
- 设计文档指定接入点仅 `DoubanApi.__invoke/__post`、`actor_profile_service` TMDB 抓取、`_write_back_episode` 三处。**`_write_back_emby`（顶层系列回写）不在本计划范围**（设计文档未列，YAGNI），后续如需再接入。

## 设计决策（供 Review 确认颗粒度与语义）

1. **滑动窗口近似「每系列」**：设计文档 `douban_per_series=30` / `emby_writeback_per_series=50` 用 600s 固定滑动窗口实现，而非显式的「系列开始→重置预算」生命周期钩子——因为 `DoubanSinizer.sinicize` 无干净的开始/结束边界，且窗口化天然满足「排队→超时跳过」的防突发意图（窗口满时后续请求排队/跳过）。若 Review 希望真正的 per-series 硬重置，需在 `sinicize` 入口加 `reset("douban")/reset("emby_writeback")`，属追加改动，可提出。
2. **`reset()`/`configure()` 暴露**：模块提供 `reset()` 清空、`configure(limits)` 注入，既服务测试隔离，也留给未来生命周期钩子使用（此时无调用方）。
3. **TMDB 只预算 API 请求**：`_tmdb_request` 是唯一 TMDB API 汇聚点；图片下载 `_download_image`（httpx 通道）不进 `tmdb_per_min` 预算（CDN 不限频，与 API 不同）。

---

### Task 1: request_budget.py 核心令牌桶 + 配置加载（TDD）

**Files:**
- Create: `backend/services/request_budget.py`
- Modify: `backend/config/settings.py`（`DEFAULT_CONFIG` 新增 `request_budget` 节）
- Test: `backend/tests/test_request_budget.py`（新建，含核心 + 配置测试）

**Interfaces:**
- Produces（后续 Task 依赖）：
  - `request_budget.acquire(provider: str, timeout: float = 30.0) -> bool` —— provider 为 `"douban"` / `"tmdb"` / `"emby_writeback"`；窗口满排队至 timeout，仍满返回 `False`，未知 provider 返回 `True`。
  - `request_budget.reset() -> None`、`request_budget.configure(limits: dict | None = None) -> None`（测试注入用）。
  - `ProviderBudget`（内部类，单测直接构造：`ProviderBudget(limit, window_seconds)`）。
  - `settings.DEFAULT_CONFIG["request_budget"]`：三个标量键。

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_request_budget.py`（完整内容，一次性写入）：

```python
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
    b = rb.ProviderBudget(limit=1, window=1.0)
    assert b.acquire(timeout=5) is True   # t=0 获取一个额度
    assert b.acquire(timeout=5) is True   # 窗口满 → 等到旧 token 过期后成功
    assert clock["t"] >= 1.0


def test_provider_budget_skips_after_timeout(clock):
    b = rb.ProviderBudget(limit=1, window=60.0)
    assert b.acquire(timeout=5) is True
    assert b.acquire(timeout=1.0) is False  # 窗口 60s ≫ 超时 1s → 跳过
    assert clock["t"] >= 1.0


def test_provider_budget_prunes_old_timestamps(clock):
    b = rb.ProviderBudget(limit=1, window=1.0)
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && venv/bin/python -m pytest tests/test_request_budget.py -v`
Expected: 8 FAIL —— `ModuleNotFoundError: No module named 'services.request_budget'`。

- [ ] **Step 3: 实现 request_budget.py**

创建 `backend/services/request_budget.py`（完整内容）：

```python
"""进程级每 Provider 请求预算（滑动窗口计数令牌桶）。

P3 豆瓣请求治理的底层基建：在既有冷却之上加一道强制上限。不重写现有冷却
（DoubanApi._apply_cooldown / _frodo_get / _http_get 随机 sleep），只在其上叠加
acquire() —— 超限时排队等待（默认 30s 超时），仍超限则返回 False，调用方跳过并记日志，
避免一次 webhook 触发的系列汉化打爆某个 Provider。

配置节（config.json → DEFAULT_CONFIG.request_budget）：
    douban_per_series:          30   一次系列汉化突发窗口(600s)内豆瓣请求上限
    tmdb_per_min:               60   TMDB 滚动窗口(60s)内上限
    emby_writeback_per_series:  50   一次系列汉化突发窗口(600s)内 Emby 回写上限

窗口语义（代码常量，非配置）：key 后缀 _per_min → 60s；_per_series → 600s。
用固定滑动窗口近似「每系列」预算，避免引入显式的系列生命周期钩子。
"""
import logging
import threading
import time
from collections import deque

from config.settings import load_config

logger = logging.getLogger(__name__)

# provider → 滑动窗口秒数
_WINDOW_SECONDS = {
    "douban": 600.0,
    "tmdb": 60.0,
    "emby_writeback": 600.0,
}

# provider → config.json request_budget 节内的键名
_CONFIG_KEYS = {
    "douban": "douban_per_series",
    "tmdb": "tmdb_per_min",
    "emby_writeback": "emby_writeback_per_series",
}

_DEFAULT_LIMITS = {
    "douban": 30,
    "tmdb": 60,
    "emby_writeback": 50,
}

_DEFAULT_TIMEOUT = 30.0   # 排队等待上限（秒），超时则跳过
_POLL_INTERVAL = 0.2      # 等待期间轮询间隔（秒）


def _clock() -> float:
    """单调时钟，测试可 monkeypatch。"""
    return time.monotonic()


def _sleep(seconds: float) -> None:
    """真实等待，测试可 monkeypatch 为推进假时钟。"""
    time.sleep(seconds)


class ProviderBudget:
    """单个 Provider 的滑动窗口额度。线程安全。

    窗口内已记录 limit 个时间戳即视为满；满了排队等待（acquire 的 timeout），
    等待期间最早的时间戳滑出窗口后放行，超时仍未放行则返回 False。
    """

    def __init__(self, limit: int, window_seconds: float):
        self.limit = limit
        self.window = window_seconds
        self._timestamps: deque = deque()
        self._lock = threading.Lock()

    def acquire(self, timeout: float = _DEFAULT_TIMEOUT) -> bool:
        deadline = _clock() + timeout
        while True:
            now = _clock()
            wait = 0.0
            with self._lock:
                while self._timestamps and now - self._timestamps[0] >= self.window:
                    self._timestamps.popleft()
                if len(self._timestamps) < self.limit:
                    self._timestamps.append(now)
                    return True
                wait = self._timestamps[0] + self.window - now
            remaining = deadline - now
            if remaining <= 0:
                return False
            _sleep(min(wait, remaining, _POLL_INTERVAL))


_budgets: dict = {}


def _build_limits() -> dict:
    try:
        cfg = load_config()
        rb_cfg = cfg.get("request_budget") or {}
    except Exception:
        logger.exception("request_budget 读取配置失败，回退默认预算")
        rb_cfg = {}
    return {
        provider: int(rb_cfg.get(_CONFIG_KEYS[provider], _DEFAULT_LIMITS[provider]))
        for provider in _CONFIG_KEYS
    }


def _ensure_initialized() -> None:
    if not _budgets:
        for provider, limit in _build_limits().items():
            _budgets[provider] = ProviderBudget(limit, _WINDOW_SECONDS[provider])


def reset() -> None:
    """清空预算（测试隔离用）。"""
    _budgets.clear()


def configure(limits: dict | None = None) -> None:
    """按给定 limits 重建预算（测试注入用）；None 时从 config 读取。"""
    reset()
    src = limits if limits is not None else _build_limits()
    for provider, limit in src.items():
        _budgets[provider] = ProviderBudget(limit, _WINDOW_SECONDS[provider])


def acquire(provider: str, timeout: float = _DEFAULT_TIMEOUT) -> bool:
    """尝试获取一个请求额度。

    未知 provider / 未初始化 → 放行（fail-open，不破坏既有行为）。
    窗口满 → 排队等待至 timeout；仍满 → False（调用方跳过并记日志）。
    """
    _ensure_initialized()
    budget = _budgets.get(provider)
    if budget is None:
        return True
    return budget.acquire(timeout)
```

- [ ] **Step 4: 新增 DEFAULT_CONFIG.request_budget**

`backend/config/settings.py` 的 `DEFAULT_CONFIG` 中，在 `"max_actors_per_media": 50,` 之后插入：

```python
    # ★ 请求预算（P3 豆瓣请求治理）— 进程级每 Provider 令牌桶上限
    "request_budget": {
        "douban_per_series": 30,
        "tmdb_per_min": 60,
        "emby_writeback_per_series": 50,
    },
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd backend && venv/bin/python -m pytest tests/test_request_budget.py -v`
Expected: 8 PASS。

- [ ] **Step 6: 提交**

```bash
cd /Users/jiangkai/project/emby-ai-manager && git add backend/services/request_budget.py backend/config/settings.py backend/tests/test_request_budget.py
git commit -m "feat: 新增 request_budget 滑动窗口令牌桶（每 Provider 请求预算）
Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: DoubanApi 接入（__invoke / __post）

**Files:**
- Modify: `backend/services/douban_api.py`（`__invoke` :121、`__post` :164、模块 import 区）
- Test: `backend/tests/test_request_budget.py`（末尾追加 DoubanApi 集成测试段）

**Interfaces:**
- Consumes（来自 Task 1）：`request_budget.acquire(provider, timeout=30.0) -> bool`。
- Produces：`DoubanApi` 在 `_apply_cooldown()` 之后、`_ensure_session()` 之前调用 `budget_acquire("douban")`；返回 `False` 时 `__invoke/__post` 返回 `self._make_error_dict("budget_exhausted", "豆瓣请求预算超限，排队超时跳过")`（形状 `{"error": "budget_exhausted", "message": ...}`，与既有错误分支一致）。

- [ ] **Step 1: 追加失败测试**

在 `backend/tests/test_request_budget.py` 末尾追加（并把顶部 import 区补上 `from services.douban_api import DoubanApi`）：

```python
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
```

> 说明：`__invoke`/`__post` 是私有方法（Python 名称改写），测试用 `api._DoubanApi__invoke(...)` 直接测接入点，不依赖公开方法的中间分支。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && venv/bin/python -m pytest tests/test_request_budget.py -v`
Expected: 新增 4 个 FAIL —— `AttributeError: module 'services.douban_api' has no attribute 'budget_acquire'`。

- [ ] **Step 3: 实现 douban_api.py 接入**

`backend/services/douban_api.py` 顶部 import 区（约 :12 `import requests` 之后）加：

```python
from services.request_budget import acquire as budget_acquire
```

`__invoke`（:121）—— 在 `DoubanApi._apply_cooldown()` 之后、`DoubanApi._ensure_session()` 之前插入：

```python
    def __invoke(self, url: str, **kwargs) -> Dict[str, Any]:
        DoubanApi._apply_cooldown()
        if not budget_acquire("douban"):
            logger.warning("  ➜ 豆瓣请求预算超限（排队超时），本次 GET 跳过: %s", url)
            return self._make_error_dict("budget_exhausted", "豆瓣请求预算超限，排队超时跳过")
        DoubanApi._ensure_session()
```

`__post`（:164）—— 同样位置插入：

```python
    def __post(self, url: str, **kwargs) -> Dict[str, Any]:
        DoubanApi._apply_cooldown()
        if not budget_acquire("douban"):
            logger.warning("  ➜ 豆瓣请求预算超限（排队超时），本次 POST 跳过: %s", url)
            return self._make_error_dict("budget_exhausted", "豆瓣请求预算超限，排队超时跳过")
        DoubanApi._ensure_session()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && venv/bin/python -m pytest tests/test_request_budget.py -v`
Expected: 12 PASS（8 核心 + 4 DoubanApi）。

- [ ] **Step 5: 提交**

```bash
cd /Users/jiangkai/project/emby-ai-manager && git add backend/services/douban_api.py backend/tests/test_request_budget.py
git commit -m "feat: DoubanApi.__invoke/__post 接入 request_budget（douban_per_series 上限）
Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: TMDB 接入（_tmdb_request）

**Files:**
- Modify: `backend/services/actor_profile_service.py`（`_tmdb_request` :277、模块 import 区 :35 附近）
- Test: `backend/tests/test_request_budget.py`（末尾追加 TMDB 集成测试段）

**Interfaces:**
- Consumes（来自 Task 1）：`request_budget.acquire(provider, timeout=30.0) -> bool`。
- Produces：`_tmdb_request` 顶部调用 `budget_acquire("tmdb")`；返回 `False` 时直接 `return None`（与既有网络失败返回 None 的契约一致，调用方已把 None 当「无数据」）。

- [ ] **Step 1: 追加失败测试**

在 `backend/tests/test_request_budget.py` 末尾追加（并把顶部 import 区补上 `import services.actor_profile_service as aps`）：

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && venv/bin/python -m pytest tests/test_request_budget.py -v`
Expected: 新增 2 个 FAIL —— `AttributeError: module 'services.actor_profile_service' has no attribute 'budget_acquire'`。

- [ ] **Step 3: 实现 actor_profile_service.py 接入**

`backend/services/actor_profile_service.py` 顶部 import 区（约 :35 `import requests as _requests` 之后）加：

```python
from services.request_budget import acquire as budget_acquire
```

`_tmdb_request`（:277）—— 函数体最顶部（`last_error = None` 之前）插入：

```python
    if not budget_acquire("tmdb"):
        logger.warning("   ⚠ [TMDB] 请求预算超限（排队超时），本次请求跳过: %s", url)
        return None
    last_error = None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && venv/bin/python -m pytest tests/test_request_budget.py -v`
Expected: 14 PASS（8 核心 + 4 DoubanApi + 2 TMDB）。

- [ ] **Step 5: 提交**

```bash
cd /Users/jiangkai/project/emby-ai-manager && git add backend/services/actor_profile_service.py backend/tests/test_request_budget.py
git commit -m "feat: TMDB _tmdb_request 接入 request_budget（tmdb_per_min 上限）
Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: Emby 回写接入（_write_back_episode）

**Files:**
- Modify: `backend/services/douban_service.py`（`_write_back_episode` :2075、模块 import 区 :13 附近）
- Test: `backend/tests/test_request_budget.py`（末尾追加 Emby 回写集成测试段）

**Interfaces:**
- Consumes（来自 Task 1）：`request_budget.acquire(provider, timeout=30.0) -> bool`。
- Produces：`_write_back_episode` 在 `try:`/`requests.post` 之前调用 `budget_acquire("emby_writeback")`；返回 `False` 时 `return False`（与既有回写失败返回 False 的契约一致）。

- [ ] **Step 1: 追加失败测试**

在 `backend/tests/test_request_budget.py` 末尾追加（并把顶部 import 区补上 `import services.douban_service as ds` 与 `from services.douban_service import DoubanSinizer`）：

```python
# ==================== 接入点 3：douban_service._write_back_episode ====================

def _make_sinizer():
    s = object.__new__(DoubanSinizer)
    s.emby_host = "http://emby.test"
    s.emby_api_key = "k"
    return s


def test_write_back_episode_acquires_budget_and_proceeds(monkeypatch):
    calls = []
    monkeypatch.setattr(ds, "budget_acquire", lambda provider, timeout=30.0: calls.append(provider) or True)

    class _OkResp:
        status_code = 200

    class _Requests:
        def post(self, *a, **k):
            return _OkResp()

    monkeypatch.setattr(ds, "requests", _Requests())
    s = _make_sinizer()

    ok = s._write_back_episode("e1", {"Name": "x"}, [])

    assert ok is True
    assert calls == ["emby_writeback"]


def test_write_back_episode_skips_when_budget_exhausted(monkeypatch):
    calls = []
    monkeypatch.setattr(ds, "budget_acquire", lambda provider, timeout=30.0: calls.append(provider) or False)

    class _NoPost:
        def post(self, *a, **k):
            raise AssertionError("预算超限不应回写 Emby")

    monkeypatch.setattr(ds, "requests", _NoPost())
    s = _make_sinizer()

    ok = s._write_back_episode("e1", {"Name": "x"}, [])

    assert ok is False
    assert calls == ["emby_writeback"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && venv/bin/python -m pytest tests/test_request_budget.py -v`
Expected: 新增 2 个 FAIL —— `AttributeError: module 'services.douban_service' has no attribute 'budget_acquire'`。

- [ ] **Step 3: 实现 douban_service.py 接入**

`backend/services/douban_service.py` 顶部 import 区（约 :13 `import requests` 之后）加：

```python
from services.request_budget import acquire as budget_acquire
```

`_write_back_episode`（:2075）—— 在 `try:` 之前插入：

```python
        if not budget_acquire("emby_writeback"):
            logger.warning("   ⚠ [Douban/Episode] Emby 回写预算超限（排队超时），跳过回写 Episode %s", episode_id)
            return False

        try:
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && venv/bin/python -m pytest tests/test_request_budget.py -v`
Expected: 16 PASS。

- [ ] **Step 5: 全量回归 + 提交**

Run: `cd backend && venv/bin/python -m pytest tests/ -q`
Expected: 全部 PASS（含既有 P1 测试与 `test_actor_items_sync_status.py` 等）。

```bash
cd /Users/jiangkai/project/emby-ai-manager && git add backend/services/douban_service.py backend/tests/test_request_budget.py
git commit -m "feat: _write_back_episode 接入 request_budget（emby_writeback_per_series 上限）
Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## 风险与注意事项

1. **滑动窗口 vs 显式 per-series**：`douban_per_series=30` 在 600s 窗口内最多 30 次豆瓣请求；若一次系列汉化持续超过 10 分钟，窗口会滚动放行后续请求（每 10 分钟 30 次），非硬性「每系列 30」。这是有意的近似（见「设计决策 1」），若需硬重置请 Review 提出。
2. **`_write_back_emby` 未接入**：设计文档只列 `_write_back_episode`。顶层系列回写（`:1442`）暂不受 `emby_writeback_per_series` 约束，后续如需再接入（同样模式一行）。
3. **budget 错误与既有错误分支共存**：`DoubanApi.__invoke/__post` 返回 `{"error": "budget_exhausted", ...}` 后，调用方 `_find_douban_id` 等已按 `result.get("error")` 处理（与 rate_limit/http_error 同级），不会崩溃。
4. **模块级 `budget_acquire` 别名**：三个接入文件都 `from services.request_budget import acquire as budget_acquire`，测试直接 patch 各模块的 `budget_acquire` 别名（与 repo 既有 `ds.requests` patch 风格一致）。若未来要全局 patch 可改 patch `request_budget.acquire` 并改用局部 import，本期不做。

## 自检（Self-Review）

- **Spec 覆盖**：设计文档 Phase 3c 全部要求——新 `services/request_budget.py` ✓（Task 1）；config 节 `request_budget` 三标量键 ✓（Task 1 Step 4）；三个接入点 `DoubanApi.__invoke/__post`、`actor_profile_service` TMDB 抓取、`_write_back_episode` ✓（Task 2/3/4）；超限排队等待（30s 超时）→ 仍超时跳过并记日志 ✓（`ProviderBudget.acquire` 超时返回 False + 各接入点 `logger.warning`）；不重写现有冷却 ✓（`_apply_cooldown`/随机 sleep 未动）。✓
- **Placeholder 扫描**：所有步骤含完整代码与命令，无 TBD/TODO。✓
- **类型一致性**：provider 名 `"douban"`/`"tmdb"`/`"emby_writeback"` 在 `_WINDOW_SECONDS`/`_CONFIG_KEYS`/`_DEFAULT_LIMITS` 与三个接入点 `budget_acquire(...)` 调用完全一致；`acquire` 签名（`provider, timeout=30.0`）在测试 patch 的 lambda 与实现一致；`_make_error_dict("budget_exhausted", ...)` 形状与既有错误分支一致。✓
- **测试有效性**：Task 1 核心测试用假时钟推进（`_sleep` 推进假时间）避免真实等待；集成测试 patch 各模块 `budget_acquire` 别名，skip 测试断言「不发起网络请求」（`_NoGet`/`_NoPost` 触发即 AssertionError），非 false-green。✓
