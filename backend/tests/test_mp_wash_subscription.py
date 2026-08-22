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
