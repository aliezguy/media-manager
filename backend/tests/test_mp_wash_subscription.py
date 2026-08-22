"""MoviePilot 洗版 POST 结果记录与回查确认测试。"""
import asyncio
import os
import subprocess
import sys
import textwrap

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


def test_moviepilot_logs_reach_root_file_handler_under_uvicorn(tmp_path):
    log_path = tmp_path / "app.log"
    backend_dir = os.path.dirname(os.path.dirname(mp.__file__))
    script = textwrap.dedent(
        """
        import logging
        import logging.config
        import sys

        from uvicorn.config import LOGGING_CONFIG

        logging.config.dictConfig(LOGGING_CONFIG)
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        handler = logging.FileHandler(sys.argv[1], encoding="utf-8")
        root_logger.addHandler(handler)

        from services import mp_service

        mp_service.logger.info("moviepilot-file-log-probe")
        handler.flush()
        root_logger.removeHandler(handler)
        handler.close()
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script, str(log_path)],
        cwd=backend_dir,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "moviepilot-file-log-probe" in log_path.read_text(encoding="utf-8")


def test_sanitize_response_body_redacts_nested_json_secrets():
    body = '{"success":false,"data":{"access_token":"abc","PASSWORD":"p"}}'
    safe = mp._sanitize_response_body(body)
    assert "abc" not in safe
    assert '"access_token": "[REDACTED]"' in safe
    assert '"PASSWORD": "[REDACTED]"' in safe


def test_sanitize_response_body_redacts_plain_text_secrets():
    safe = mp._sanitize_response_body("token=secret-value")
    assert "secret-value" not in safe
    assert safe == "token=[REDACTED]"


def test_sanitize_response_body_caps_total_length():
    safe = mp._sanitize_response_body("x" * 5000)
    assert safe.endswith("...[TRUNCATED]")
    assert len(safe) == 4096


def test_wash_subscription_result_has_safe_diagnostic_defaults():
    result = mp.WashSubscriptionResult(success=False)
    assert result.http_status is None
    assert result.response_body == ""
    assert result.error is None
    assert result.verified_by_lookup is False
    assert result.subscription_id is None


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
        "name": "地球超新鲜",
        "type": "电视剧",
        "tmdbid": 296202,
        "season": 2,
        "best_version": 1,
        "best_version_full": 1,
        "state": "R",
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
        lambda *args, **kwargs: FakeResponse(
            502, {"message": "bad gateway"}, "bad gateway"
        ),
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


def test_false_code_is_not_treated_as_numeric_zero_success(monkeypatch):
    _configure_mp(monkeypatch)
    monkeypatch.setattr(
        mp.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(
            200, {"success": False, "code": False},
            '{"success":false,"code":false}',
        ),
    )
    normal = _matching_subscription(best_version=0, best_version_full=0)
    monkeypatch.setattr(mp.requests, "get", lambda *a, **k: FakeResponse(200, normal))

    result = mp.add_wash_subscription(_wash_payload())

    assert result.success is False
    assert result.verified_by_lookup is False


def test_lookup_does_not_accept_normal_tracking_subscription(monkeypatch):
    _configure_mp(monkeypatch)
    monkeypatch.setattr(
        mp.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(
            200, {"success": False}, '{"success":false}'
        ),
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
        raise mp.requests.exceptions.Timeout("response lost")

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

    assert saved["status"] == "failed"
    assert saved["message"] == "洗版API请求失败"
    assert saved["details"]["http_status"] == 502
    assert saved["details"]["response_body"] == "bad gateway"
    assert saved["details"]["error"] == "未找到匹配的洗版订阅"
    assert saved["details"]["verified_by_lookup"] is False
