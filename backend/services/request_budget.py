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
