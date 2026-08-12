"""danmu_service 测试 — HTTP 代理封装（鉴权头 / 重试 / 错误包装 / 领域函数）。

T1：弹幕管理页面后端代理层。mock requests 断言 header、错误包装与上游对应关系。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import services.danmu_service as ds


@pytest.fixture(autouse=True)
def _danmu_config(monkeypatch):
    """默认配置好 base_url/api_key，个别用例自行覆盖。"""
    monkeypatch.setattr(ds, "get_danmu_config", lambda: {
        "base_url": "https://danmu.example.com",
        "api_key": "test-key",
    })


class _FakeResp:
    def __init__(self, status_code=200, json_data=None, text="", content=b"{}"):
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}
        self.text = text
        self.content = content

    def json(self):
        return self._json_data


def test_unconfigured_raises_config_missing(monkeypatch):
    monkeypatch.setattr(ds, "is_configured", lambda: False)
    with pytest.raises(ds.DanmuConfigMissing):
        ds.search("天才厨人")


def test_request_sends_api_key_header(monkeypatch):
    captured = {}

    def fake_request(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = kwargs.get("headers", {})
        captured["params"] = kwargs.get("params")
        return _FakeResp(json_data={"searchId": "abc", "results": []})

    monkeypatch.setattr(ds.requests, "request", fake_request)
    ds.search("天才厨人", season=1)
    assert captured["headers"]["X-API-KEY"] == "test-key"
    assert captured["method"] == "GET"
    assert captured["url"] == "https://danmu.example.com/api/control/search"
    assert captured["params"] == {"keyword": "天才厨人", "season": 1}


def test_request_bypasses_system_proxy(monkeypatch):
    """显式禁用系统代理（macOS Clash/Surge 127.0.0.1:1088 会被 trust_env 自动使用）。"""
    captured = {}

    def fake_request(method, url, **kwargs):
        captured["proxies"] = kwargs.get("proxies")
        return _FakeResp(json_data={"searchId": "abc", "results": []})

    monkeypatch.setattr(ds.requests, "request", fake_request)
    ds.get_library()
    assert captured["proxies"] == {"http": None, "https": None}


def test_search_uses_long_timeout(monkeypatch):
    """搜索并发搜多源实测需 ~21s，timeout 必须放宽（其余端点仍 15s）。"""
    captured = {}

    def fake_request(method, url, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return _FakeResp(json_data={"searchId": "abc", "results": []})

    monkeypatch.setattr(ds.requests, "request", fake_request)
    ds.search("斩神")
    assert captured["timeout"] == 45

    # 轻量端点保持默认 15s
    monkeypatch.setattr(ds.requests, "request", fake_request)
    ds.get_library()
    assert captured["timeout"] == 15


def test_non_2xx_raises_upstream_error(monkeypatch):
    monkeypatch.setattr(ds.requests, "request",
                        lambda *a, **k: _FakeResp(404, text="not found", content=b""))
    with pytest.raises(ds.DanmuUpstreamError) as ei:
        ds.get_library()
    assert ei.value.status_code == 404
    assert "404" in str(ei.value)


def test_retries_on_network_error_then_succeeds(monkeypatch):
    """网络错误重试 2 次，第三次成功。"""
    attempts = {"n": 0}

    def flaky(*args, **kwargs):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ds.requests.exceptions.ConnectionError("boom")
        return _FakeResp(json_data={"searchId": "ok", "results": []})

    monkeypatch.setattr(ds.requests, "request", flaky)
    import types
    monkeypatch.setattr(ds, "_time", types.SimpleNamespace(sleep=lambda s: None))  # 不实际 sleep
    ds.search("剧")
    assert attempts["n"] == 3


def test_list_tasks_hits_tasks_endpoint(monkeypatch):
    """任务列表 → GET /api/control/tasks?status=all。"""
    captured = {}

    def fake_request(method, url, **kwargs):
        captured["url"] = url
        captured["params"] = kwargs.get("params")
        return _FakeResp(json_data=[{"taskId": "t1", "title": "导入", "status": "运行中"}])

    monkeypatch.setattr(ds.requests, "request", fake_request)
    items = ds.list_tasks("all")
    assert captured["url"] == "https://danmu.example.com/api/control/tasks"
    assert captured["params"] == {"status": "all"}
    assert items[0]["taskId"] == "t1"


def test_import_edited_builds_body(monkeypatch):
    captured = {}

    def fake_request(method, url, **kwargs):
        captured["json"] = kwargs.get("json")
        return _FakeResp(json_data={"status": "pending", "taskId": "t1"})

    monkeypatch.setattr(ds.requests, "request", fake_request)
    episodes = [{"provider": "iqiyi", "episodeId": "v1", "title": "第1期上", "episodeIndex": 1}]
    ds.import_edited("sid", 0, "天才厨人", episodes, tmdbId=284110)
    body = captured["json"]
    assert body["searchId"] == "sid"
    assert body["result_index"] == 0
    assert body["title"] == "天才厨人"
    assert body["tmdbId"] == 284110
    assert body["episodes"][0]["episodeIndex"] == 1


def test_paths_map_to_upstream(monkeypatch):
    """领域函数 → 上游路径一一对应。"""
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url))
        return _FakeResp(json_data=[] if "episodes" in url or "sources" in url else {"x": 1})

    monkeypatch.setattr(ds.requests, "request", fake_request)
    ds.get_library()
    ds.get_anime_detail(94)
    ds.update_anime(94, {"tmdbId": 284110})
    ds.delete_anime(94)
    ds.get_anime_sources(94)
    ds.delete_source(116)
    ds.get_source_episodes(116)
    ds.delete_episode(123)
    ds.get_task("task-1")
    ds.get_search_episodes("sid", 0)

    expected = [
        ("GET", "/api/control/library"),
        ("GET", "/api/control/library/anime/94"),
        ("PUT", "/api/control/library/anime/94"),
        ("DELETE", "/api/control/library/anime/94"),
        ("GET", "/api/control/library/anime/94/sources"),
        ("DELETE", "/api/control/library/source/116"),
        ("GET", "/api/control/library/source/116/episodes"),
        ("DELETE", "/api/control/library/episode/123"),
        ("GET", "/api/control/tasks/task-1"),
        ("GET", "/api/control/episodes"),
    ]
    for (exp_m, exp_u), (got_m, got_u) in zip(expected, calls):
        assert got_m == exp_m, f"method mismatch for {exp_u}"
        assert got_u.endswith(exp_u), f"path mismatch: {got_u} !~ {exp_u}"
