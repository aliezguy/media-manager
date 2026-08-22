# MoviePilot 洗版结果回查 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 MoviePilot 已创建洗版订阅但 Emby AI Manager 将任务历史误记为失败的问题，并同时记录脱敏、截断后的 HTTP 状态与响应正文。

**Architecture:** 在 `mp_service.py` 中以不可变结构化结果替代原有布尔返回值。POST 明确成功时直接返回；其他已发出 POST 的结果通过只读 MoviePilot 查询按 TMDB、Season 与洗版标志验证最终状态，再由 `run_wash_process()` 将统一结果写入日志和 `wash_params`。

**Tech Stack:** Python 3.13、requests、FastAPI 服务层、SQLAlchemy JSON 字段、pytest/monkeypatch。

## Global Constraints

- HTTP 状态与响应正文必须同时写入后端日志和 `wash_history.wash_params`。
- 响应正文最多 4096 个字符，截断标记也必须包含在该上限内。
- `token`、`access_token`、`password`、`cookie`、`api_key`、`apikey`、`authorization` 键大小写不敏感地脱敏。
- POST 未明确成功时只做 GET 回查，不再次 POST。
- 回查成功必须同时匹配 TMDB ID、Season、`best_version == 1` 与 `best_version_full == 1`。
- 不修改数据库表结构，不改变策略匹配、站点、下载器与质量参数。
- 自动化测试不得连接真实 MoviePilot、TMDB 或数据库服务。

## File Structure

- Create: `backend/tests/test_mp_wash_subscription.py` — 覆盖响应脱敏、POST 判定、回查验证和历史写入的回归测试。
- Modify: `backend/services/mp_service.py` — 定义结构化结果、响应处理、只读回查以及历史集成。
- Reference: `docs/superpowers/specs/2026-08-22-moviepilot-wash-result-verification-design.md` — 已批准的行为规格。

---

### Task 1: 结构化结果与响应正文安全处理

**Files:**
- Create: `backend/tests/test_mp_wash_subscription.py`
- Modify: `backend/services/mp_service.py:1-12`
- Modify: `backend/services/mp_service.py:149-185`

**Interfaces:**
- Produces: `WashSubscriptionResult(success: bool, http_status: int | None = None, response_body: str = "", error: str | None = None, verified_by_lookup: bool = False, subscription_id: int | None = None)`
- Produces: `_sanitize_response_body(body: str, limit: int = 4096) -> str`
- Consumes: Python 标准库 `dataclasses`, `re`, `json`。

- [ ] **Step 1: 写响应安全处理的失败测试**

Create `backend/tests/test_mp_wash_subscription.py` with:

```python
"""MoviePilot 洗版 POST 结果记录与回查确认测试。"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import mp_service as mp


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=None, json_error=None):
        self.status_code = status_code
        self._json_data = {} if json_data is None else json_data
        self._json_error = json_error
        self.text = text if text is not None else ""

    def json(self):
        if self._json_error:
            raise self._json_error
        return self._json_data


def test_sanitize_response_body_redacts_nested_json_secrets():
    body = '{"success":false,"data":{"access_token":"abc","PASSWORD":"p"}}'
    safe = mp._sanitize_response_body(body)
    assert "abc" not in safe
    assert '"access_token": "[REDACTED]"' in safe
    assert '"PASSWORD": "[REDACTED]"' in safe


def test_sanitize_response_body_redacts_plain_text_and_caps_total_length():
    body = "token=secret-value " + ("x" * 5000)
    safe = mp._sanitize_response_body(body)
    assert "secret-value" not in safe
    assert "[REDACTED]" in safe
    assert safe.endswith("...[TRUNCATED]")
    assert len(safe) == 4096


def test_wash_subscription_result_has_safe_diagnostic_defaults():
    result = mp.WashSubscriptionResult(success=False)
    assert result.http_status is None
    assert result.response_body == ""
    assert result.error is None
    assert result.verified_by_lookup is False
    assert result.subscription_id is None
```

- [ ] **Step 2: 运行测试并确认按预期失败**

Run:

```bash
python3 -m pytest backend/tests/test_mp_wash_subscription.py -v
```

Expected: tests are collected, then FAIL with `AttributeError` because
`services.mp_service` has no `_sanitize_response_body` or
`WashSubscriptionResult` yet.

- [ ] **Step 3: 实现最小结构化结果与安全处理**

Add imports near the top of `backend/services/mp_service.py`:

```python
import re
from dataclasses import dataclass
from typing import Any
```

Add before `save_history()`:

```python
_RESPONSE_BODY_LIMIT = 4096
_TRUNCATION_MARKER = "...[TRUNCATED]"
_SENSITIVE_RESPONSE_KEYS = {
    "token", "access_token", "password", "cookie",
    "api_key", "apikey", "authorization",
}


@dataclass(frozen=True)
class WashSubscriptionResult:
    success: bool
    http_status: int | None = None
    response_body: str = ""
    error: str | None = None
    verified_by_lookup: bool = False
    subscription_id: int | None = None


def _redact_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if str(key).lower() in _SENSITIVE_RESPONSE_KEYS
            else _redact_json_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_json_value(item) for item in value]
    return value


def _redact_plain_text(value: str) -> str:
    key_pattern = "|".join(sorted(_SENSITIVE_RESPONSE_KEYS, key=len, reverse=True))
    return re.sub(
        rf"(?i)\b({key_pattern})\b(\s*[:=]\s*)([^\s,;&]+)",
        r"\1\2[REDACTED]",
        value,
    )


def _sanitize_response_body(body: str, limit: int = _RESPONSE_BODY_LIMIT) -> str:
    raw = "" if body is None else str(body)
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        safe = _redact_plain_text(raw)
    else:
        safe = json.dumps(_redact_json_value(parsed), ensure_ascii=False, default=str)

    if len(safe) <= limit:
        return safe
    if limit <= len(_TRUNCATION_MARKER):
        return _TRUNCATION_MARKER[:limit]
    return safe[:limit - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER
```

- [ ] **Step 4: 运行 Task 1 测试并确认通过**

Run:

```bash
python3 -m pytest backend/tests/test_mp_wash_subscription.py -v
```

Expected: 3 passed.

- [ ] **Step 5: 提交 Task 1**

```bash
git add backend/services/mp_service.py backend/tests/test_mp_wash_subscription.py
git commit -m "test: 覆盖洗版响应诊断信息"
```

---

### Task 2: POST 不确定结果的 TMDB + Season 回查

**Files:**
- Modify: `backend/tests/test_mp_wash_subscription.py`
- Modify: `backend/services/mp_service.py:149-185`

**Interfaces:**
- Consumes: `WashSubscriptionResult` and `_sanitize_response_body()` from Task 1.
- Produces: `_is_matching_wash_subscription(item: dict, tmdb_id: int | str, season: int | str) -> bool`
- Produces: `_lookup_wash_subscription(host: str, token: str, tmdb_id: int | str, season: int | str) -> tuple[dict | None, str | None]`
- Changes: `add_wash_subscription(payload: dict) -> WashSubscriptionResult`

- [ ] **Step 1: 写明确成功与回查成功的失败测试**

Append to `backend/tests/test_mp_wash_subscription.py`:

```python
def _configure_mp(monkeypatch):
    monkeypatch.setattr(mp, "load_config", lambda: {"mp_host": "http://mp"})
    monkeypatch.setattr(mp, "get_mp_token", lambda: "token")


def _wash_payload():
    return {
        "name": "地球超新鲜",
        "type": "电视剧",
        "tmdbid": 296202,
        "season": 2,
        "best_version": 1,
        "best_version_full": 1,
    }


def _matching_subscription(**overrides):
    item = {
        "id": 485,
        "tmdbid": 296202,
        "season": 2,
        "best_version": 1,
        "best_version_full": 1,
    }
    item.update(overrides)
    return item


def test_add_wash_subscription_explicit_success_does_not_lookup(monkeypatch):
    _configure_mp(monkeypatch)
    monkeypatch.setattr(
        mp.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(
            200, {"success": True}, '{"success":true}'
        ),
    )
    get_calls = []
    monkeypatch.setattr(mp.requests, "get", lambda *a, **k: get_calls.append((a, k)))

    result = mp.add_wash_subscription(_wash_payload())

    assert result.success is True
    assert result.http_status == 200
    assert result.response_body == '{"success": true}'
    assert result.verified_by_lookup is False
    assert get_calls == []


def test_add_wash_subscription_unknown_200_is_confirmed_by_lookup(monkeypatch):
    _configure_mp(monkeypatch)
    monkeypatch.setattr(
        mp.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(200, {"id": 485}, '{"id":485}'),
    )
    monkeypatch.setattr(
        mp.requests,
        "get",
        lambda *args, **kwargs: FakeResponse(200, _matching_subscription()),
    )

    result = mp.add_wash_subscription(_wash_payload())

    assert result.success is True
    assert result.http_status == 200
    assert result.verified_by_lookup is True
    assert result.subscription_id == 485


def test_add_wash_subscription_non_2xx_is_confirmed_by_list_fallback(monkeypatch):
    _configure_mp(monkeypatch)
    monkeypatch.setattr(
        mp.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(502, {"message": "bad gateway"}, "bad gateway"),
    )
    responses = iter([
        FakeResponse(404, text="not found"),
        FakeResponse(200, [_matching_subscription()]),
    ])
    monkeypatch.setattr(mp.requests, "get", lambda *args, **kwargs: next(responses))

    result = mp.add_wash_subscription(_wash_payload())

    assert result.success is True
    assert result.http_status == 502
    assert result.response_body == "bad gateway"
    assert result.verified_by_lookup is True
    assert result.subscription_id == 485
```

- [ ] **Step 2: 运行新增测试并确认按预期失败**

Run:

```bash
python3 -m pytest backend/tests/test_mp_wash_subscription.py -v
```

Expected: FAIL because `add_wash_subscription()` still returns `bool` and no lookup GET is issued.

- [ ] **Step 3: 实现严格匹配、直接查询与列表回退**

Add before `add_wash_subscription()` in `backend/services/mp_service.py`:

```python
def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _subscription_candidates(value: Any):
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                yield item
        return
    if not isinstance(value, dict):
        return
    for key in ("data", "items", "value"):
        nested = value.get(key)
        if isinstance(nested, (dict, list)):
            yield from _subscription_candidates(nested)
            return
    yield value


def _is_matching_wash_subscription(item: dict, tmdb_id: Any, season: Any) -> bool:
    return (
        _as_int(item.get("tmdbid")) == _as_int(tmdb_id)
        and _as_int(item.get("season")) == _as_int(season)
        and _as_int(item.get("best_version")) == 1
        and _as_int(item.get("best_version_full")) == 1
    )


def _lookup_wash_subscription(
    host: str,
    token: str,
    tmdb_id: Any,
    season: Any,
) -> tuple[dict | None, str | None]:
    headers = {"Authorization": f"Bearer {token}"}
    attempts = (
        (f"{host}/api/v1/subscribe/media/tmdb:{tmdb_id}", {"season": int(season)}),
        (f"{host}/api/v1/subscribe/", None),
    )
    errors = []
    logger.info(f"      🔎 [洗版回查] TMDB {tmdb_id} | S{season}")

    for url, params in attempts:
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=30)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            continue
        if not 200 <= resp.status_code < 300:
            errors.append(f"GET {resp.status_code}: {_sanitize_response_body(resp.text)}")
            continue
        try:
            payload = resp.json()
        except Exception as exc:
            errors.append(f"GET JSON {type(exc).__name__}: {exc}")
            continue
        for item in _subscription_candidates(payload):
            if _is_matching_wash_subscription(item, tmdb_id, season):
                logger.info(f"      ✅ [洗版回查] 已确认订阅 ID={item.get('id')}")
                return item, None

    error = "; ".join(errors) if errors else "未找到匹配的洗版订阅"
    logger.warning(f"      ⚠️ [洗版回查] {error}")
    return None, _sanitize_response_body(error)
```

- [ ] **Step 4: 将 POST 改为结构化返回并接入回查**

Replace `add_wash_subscription()` with:

```python
def add_wash_subscription(payload: dict) -> WashSubscriptionResult:
    """新增 MoviePilot 洗版订阅；结果不明确时按 TMDB 与 Season 回查。"""
    cfg = load_config()
    host = cfg.get("mp_host", "").rstrip("/")
    token = get_mp_token()
    if not host or not token:
        return WashSubscriptionResult(success=False, error="MoviePilot Host 或 Token 未配置")

    payload.setdefault("username", "AI自动洗版")
    logger.info(f"      🚀 [API新增] Payload: {json.dumps(payload, ensure_ascii=False)}")

    http_status = None
    response_body = ""
    post_error = None
    try:
        resp = requests.post(
            f"{host}/api/v1/subscribe/",
            json=payload,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=30,
        )
        http_status = resp.status_code
        response_body = _sanitize_response_body(resp.text)
        logger.info(f"      📥 [洗版POST] HTTP {http_status} | Body: {response_body}")
        try:
            response_json = resp.json()
        except Exception as exc:
            post_error = f"响应 JSON 解析失败: {type(exc).__name__}: {exc}"
        else:
            if 200 <= http_status < 300 and isinstance(response_json, dict):
                if response_json.get("success") is True or response_json.get("code") == 0:
                    return WashSubscriptionResult(
                        success=True,
                        http_status=http_status,
                        response_body=response_body,
                    )
            post_error = f"POST 未明确成功: HTTP {http_status}"
    except Exception as exc:
        post_error = f"{type(exc).__name__}: {exc}"
        logger.error(f"      ❌ [洗版POST异常] {_sanitize_response_body(post_error)}")

    tmdb_id = payload.get("tmdbid")
    season = payload.get("season")
    matched = None
    lookup_error = None
    if tmdb_id is not None and season is not None:
        matched, lookup_error = _lookup_wash_subscription(
            host=host,
            token=token,
            tmdb_id=tmdb_id,
            season=season,
        )
    else:
        lookup_error = "回查缺少 tmdbid 或 season"

    safe_post_error = _sanitize_response_body(post_error or "") or None
    if matched:
        return WashSubscriptionResult(
            success=True,
            http_status=http_status,
            response_body=response_body,
            error=safe_post_error,
            verified_by_lookup=True,
            subscription_id=_as_int(matched.get("id")),
        )

    combined_error = "; ".join(filter(None, (safe_post_error, lookup_error))) or None
    return WashSubscriptionResult(
        success=False,
        http_status=http_status,
        response_body=response_body,
        error=_sanitize_response_body(combined_error or "") or None,
    )
```

- [ ] **Step 5: 运行 Task 2 测试并确认通过**

Run:

```bash
python3 -m pytest backend/tests/test_mp_wash_subscription.py -v
```

Expected: 6 passed.

- [ ] **Step 6: 写严格失败与异常回查的失败测试**

Append to `backend/tests/test_mp_wash_subscription.py`:

```python
def test_lookup_does_not_accept_normal_tracking_subscription(monkeypatch):
    _configure_mp(monkeypatch)
    monkeypatch.setattr(
        mp.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(200, {"success": False}, '{"success":false}'),
    )
    normal = _matching_subscription(best_version=0, best_version_full=0)
    monkeypatch.setattr(mp.requests, "get", lambda *a, **k: FakeResponse(200, normal))

    result = mp.add_wash_subscription(_wash_payload())

    assert result.success is False
    assert result.verified_by_lookup is False
    assert result.subscription_id is None
    assert "未找到匹配的洗版订阅" in result.error


def test_lookup_rejects_wrong_season_and_wrong_tmdb(monkeypatch):
    _configure_mp(monkeypatch)
    monkeypatch.setattr(
        mp.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(500, text="failed"),
    )
    responses = iter([
        FakeResponse(200, _matching_subscription(season=1)),
        FakeResponse(200, [_matching_subscription(tmdbid=131040)]),
    ])
    monkeypatch.setattr(mp.requests, "get", lambda *a, **k: next(responses))

    result = mp.add_wash_subscription(_wash_payload())

    assert result.success is False
    assert result.http_status == 500
    assert result.verified_by_lookup is False


def test_post_exception_can_be_confirmed_by_lookup(monkeypatch):
    _configure_mp(monkeypatch)

    def raise_timeout(*args, **kwargs):
        raise mp.requests.Timeout("response lost")

    monkeypatch.setattr(mp.requests, "post", raise_timeout)
    monkeypatch.setattr(
        mp.requests,
        "get",
        lambda *a, **k: FakeResponse(200, {"data": _matching_subscription()}),
    )

    result = mp.add_wash_subscription(_wash_payload())

    assert result.success is True
    assert result.http_status is None
    assert result.verified_by_lookup is True
    assert "Timeout" in result.error
```

- [ ] **Step 7: 运行新增严格匹配测试并确认结果**

Run:

```bash
python3 -m pytest backend/tests/test_mp_wash_subscription.py -v
```

Expected: 9 passed. If a strict-failure test fails, change production matching or error composition, not the assertions.

- [ ] **Step 8: 提交 Task 2**

```bash
git add backend/services/mp_service.py backend/tests/test_mp_wash_subscription.py
git commit -m "fix: 回查确认 MoviePilot 洗版订阅"
```

---

### Task 3: 将 HTTP 与回查结果写入洗版历史

**Files:**
- Modify: `backend/tests/test_mp_wash_subscription.py`
- Modify: `backend/services/mp_service.py:453-472`

**Interfaces:**
- Consumes: `add_wash_subscription(payload) -> WashSubscriptionResult` from Task 2.
- Changes: `run_wash_process(sub_info)` writes `http_status`, `response_body`, `error`, `verified_by_lookup`, and `subscription_id` into `wash_params`.
- Preserves: Existing scheme fields and `wash_type="complete"`.

- [ ] **Step 1: 写历史集成的失败测试**

Append to `backend/tests/test_mp_wash_subscription.py`:

```python
def _configure_wash_process(monkeypatch):
    scheme = {
        "name": "综艺",
        "active": True,
        "keywords": ["综艺"],
        "filter_groups": ["综艺洗版"],
        "downloader": "qb完结",
        "quality": "WEB-DL",
        "sites": [1, 16],
    }
    monkeypatch.setattr(mp, "load_config", lambda: {"wash_schemes": [scheme]})
    monkeypatch.setattr(mp, "_find_best_scheme", lambda *args, **kwargs: scheme)


def test_run_wash_process_saves_lookup_confirmed_http_diagnostics(monkeypatch):
    _configure_wash_process(monkeypatch)
    result = mp.WashSubscriptionResult(
        success=True,
        http_status=200,
        response_body='{"id": 485}',
        error="POST 未明确成功: HTTP 200",
        verified_by_lookup=True,
        subscription_id=485,
    )
    monkeypatch.setattr(mp, "add_wash_subscription", lambda payload: result)
    saved = {}
    monkeypatch.setattr(
        mp,
        "save_history",
        lambda name, season, tmdb_id, status, msg, details, wash_type="complete": saved.update(
            name=name,
            season=season,
            tmdb_id=tmdb_id,
            status=status,
            message=msg,
            details=details,
            wash_type=wash_type,
        ),
    )

    asyncio.run(mp.run_wash_process({
        "name": "地球超新鲜",
        "tmdbid": 296202,
        "season": 2,
        "type": "电视剧",
        "year": 2025,
        "category": "综艺",
    }))

    assert saved["status"] == "success"
    assert saved["message"] == "洗版订阅已创建（回查确认）"
    assert saved["wash_type"] == "complete"
    assert saved["details"]["scheme"] == "综艺"
    assert saved["details"]["http_status"] == 200
    assert saved["details"]["response_body"] == '{"id": 485}'
    assert saved["details"]["error"] == "POST 未明确成功: HTTP 200"
    assert saved["details"]["verified_by_lookup"] is True
    assert saved["details"]["subscription_id"] == 485


def test_run_wash_process_saves_final_failure_diagnostics(monkeypatch):
    _configure_wash_process(monkeypatch)
    result = mp.WashSubscriptionResult(
        success=False,
        http_status=502,
        response_body="bad gateway",
        error="未找到匹配的洗版订阅",
    )
    monkeypatch.setattr(mp, "add_wash_subscription", lambda payload: result)
    saved = {}
    monkeypatch.setattr(
        mp,
        "save_history",
        lambda name, season, tmdb_id, status, msg, details, wash_type="complete": saved.update(
            status=status, message=msg, details=details
        ),
    )

    asyncio.run(mp.run_wash_process({
        "name": "地球超新鲜",
        "tmdbid": 296202,
        "season": 2,
        "type": "电视剧",
        "year": 2025,
        "category": "综艺",
    }))

    assert saved["status"] == "failed"
    assert saved["message"] == "洗版API请求失败"
    assert saved["details"]["http_status"] == 502
    assert saved["details"]["response_body"] == "bad gateway"
    assert saved["details"]["verified_by_lookup"] is False
```

- [ ] **Step 2: 运行历史集成测试并确认按预期失败**

Run:

```bash
python3 -m pytest backend/tests/test_mp_wash_subscription.py -v
```

Expected: the two new tests FAIL because `run_wash_process()` still treats the result as a boolean and omits diagnostic fields.

- [ ] **Step 3: 将结构化结果合并到历史详情**

Replace the result handling block after the POST in `run_wash_process()` with:

```python
            wash_result = add_wash_subscription(new_sub_payload)

            status_str = "success" if wash_result.success else "failed"
            if wash_result.success and wash_result.verified_by_lookup:
                msg_str = "洗版订阅已创建（回查确认）"
            elif wash_result.success:
                msg_str = "已触发洗版重订阅"
            else:
                msg_str = "洗版API请求失败"

            save_history(
                name, season, tmdb_id, status_str, msg_str,
                {
                    "scheme": scheme_name,
                    "downloader": matched_scheme.get("downloader"),
                    "filter_groups": matched_scheme.get("filter_groups"),
                    "quality": matched_scheme.get("quality"),
                    "sites": matched_scheme.get("sites"),
                    "keywords": matched_scheme.get("keywords"),
                    "http_status": wash_result.http_status,
                    "response_body": wash_result.response_body,
                    "error": wash_result.error,
                    "verified_by_lookup": wash_result.verified_by_lookup,
                    "subscription_id": wash_result.subscription_id,
                },
                wash_type="complete",
            )
```

- [ ] **Step 4: 运行全部洗版回归测试**

Run:

```bash
python3 -m pytest backend/tests/test_mp_wash_subscription.py -v
```

Expected: 11 passed.

- [ ] **Step 5: 提交 Task 3**

```bash
git add backend/services/mp_service.py backend/tests/test_mp_wash_subscription.py
git commit -m "feat: 记录洗版 HTTP 与回查结果"
```

---

### Task 4: 全量验证与差异审计

**Files:**
- Verify: `backend/services/mp_service.py`
- Verify: `backend/tests/test_mp_wash_subscription.py`

**Interfaces:**
- Consumes: Tasks 1–3 的最终代码。
- Produces: 无新接口；提供完成声明所需的验证证据。

- [ ] **Step 1: 运行目标回归测试**

```bash
python3 -m pytest backend/tests/test_mp_wash_subscription.py -v
```

Expected: 11 passed, 0 failed.

- [ ] **Step 2: 运行后端完整测试集**

```bash
python3 -m pytest backend/tests -q
```

Expected: 0 failed；仅允许仓库已有且有明确原因的 skip。

- [ ] **Step 3: 做 Python 编译检查**

```bash
python3 -m py_compile backend/services/mp_service.py backend/tests/test_mp_wash_subscription.py
```

Expected: exit 0, no output.

- [ ] **Step 4: 审计最终差异**

```bash
git diff HEAD~3 --check
git diff HEAD~3 -- backend/services/mp_service.py backend/tests/test_mp_wash_subscription.py
git status --short
```

Expected: no whitespace errors；差异只涉及计划内文件；工作树无未提交的计划外变更。

- [ ] **Step 5: 对照验收标准人工核对**

Confirm from the tests and diff:

```text
地球超新鲜场景由 unknown-200 + matching TMDB/Season wash subscription 测试覆盖。
普通追更、错误 TMDB、错误 Season 均无法转为成功。
POST 状态、正文、错误、回查标记和订阅 ID 同时进入 wash_params。
正文脱敏与 4096 字符总上限均有回归测试。
验证过程没有向真实 MoviePilot 发送 POST。
```
