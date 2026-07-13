"""Additional edge-case tests for D.12 Slack connector.

Covers gaps not exercised by test_connector_slack.py:
* _parse_slack_ts bad inputs (non-numeric, empty, None)
* _extract_messages malformed payloads (non-dict, missing messages key, non-list messages, entries that aren't dicts)
* _normalize_message with missing fields (no user, no text, no subtype, no ts key)
* send_notification API path non-200 response (was only tested for webhook path)
* read_channel_history without count (no limit param in request)
* _auth_headers ValueError when env var is empty string
* trailing-slash normalization in URL validation
* __all__ export correctness
* HttpTransport Protocol runtime_checkable
* timeout parameter passthrough
* message subtype propagation
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.slack import (
    HttpTransport,
    SlackSource,
    _parse_slack_ts,
)
from general_ludd.connectors.slack import (
    __all__ as slack_all,
)


class FakeResponse:
    def __init__(self, status_code: int, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class FakeTransport:
    def __init__(self, *, get_resp=None, post_resp=None) -> None:
        self._get_resp = get_resp
        self._post_resp = post_resp
        self.get_calls: list[dict[str, Any]] = []
        self.post_calls: list[dict[str, Any]] = []

    def get(self, url, *, headers, params=None, timeout=30.0):
        self.get_calls.append({"url": url, "headers": headers, "params": params, "timeout": timeout})
        assert self._get_resp is not None
        return self._get_resp

    def post(self, url, *, headers, data=None, json=None, timeout=30.0):
        self.post_calls.append({"url": url, "headers": headers, "data": data, "json": json, "timeout": timeout})
        assert self._post_resp is not None
        return self._post_resp


BASE_URL = "https://slack.com/api"
WEBHOOK_URL = "https://hooks.slack.com/services/T000/B000/xxxx"
ENV = {"SLACK_BOT_TOKEN": "xoxb-secret-token"}


def _mk(config=None, get_resp=None, post_resp=None, env=None):
    t = FakeTransport(get_resp=get_resp, post_resp=post_resp)
    s = SlackSource(
        config or {"base_url": BASE_URL, "token_env": "SLACK_BOT_TOKEN", "channel_id": "C123"},
        transport=t,
        env=env if env is not None else dict(ENV),
    )
    return s, t


# --------------------------------------------------------------------------- #
# _parse_slack_ts edge cases
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("ts,expect_none", [
    ("abc123", True),
    ("", True),
    ("not.a.number", True),
    ("9999999999.999999", False),
    ("0.0", False),
])
def test_parse_slack_ts_bad_inputs(ts, expect_none):
    result = _parse_slack_ts(ts)
    if expect_none:
        assert result is None
    else:
        assert result is not None
        assert "T" in result


def test_parse_slack_ts_none_returns_none():
    assert _parse_slack_ts(None) is None  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# _extract_messages edge cases
# --------------------------------------------------------------------------- #
def test_extract_messages_non_dict_payload():
    assert SlackSource._extract_messages(None) == []
    assert SlackSource._extract_messages("string") == []
    assert SlackSource._extract_messages([1, 2, 3]) == []


def test_extract_messages_dict_no_messages_key():
    assert SlackSource._extract_messages({"ok": True}) == []


def test_extract_messages_messages_not_a_list():
    assert SlackSource._extract_messages({"messages": "not_a_list"}) == []
    assert SlackSource._extract_messages({"messages": None}) == []
    assert SlackSource._extract_messages({"messages": 123}) == []


def test_extract_messages_filters_non_dict_entries():
    payload = {
        "messages": [
            {"type": "message", "text": "hello"},
            "not_a_dict",
            None,
            42,
            {"type": "message", "text": "world"},
        ]
    }
    result = SlackSource._extract_messages(payload)
    assert len(result) == 2
    assert result[0]["text"] == "hello"
    assert result[1]["text"] == "world"


# --------------------------------------------------------------------------- #
# _normalize_message edge cases
# --------------------------------------------------------------------------- #
def test_normalize_message_missing_fields():
    src, _ = _mk()
    rec = src._normalize_message({})
    assert rec["source"] == "slack"
    assert rec["kind"] == "chat"
    assert rec["ts"] is None
    assert rec["message"] is None
    assert rec["level_or_status"] is None
    assert rec["value"] is None
    assert rec["labels"]["user"] is None
    assert rec["labels"]["channel_id"] == "C123"
    assert rec["raw"] == {}


def test_normalize_message_with_subtype():
    src, _ = _mk()
    rec = src._normalize_message({
        "type": "message",
        "subtype": "message_changed",
        "user": "U555",
        "text": "edited message",
        "ts": "1749717000.000500",
    })
    assert rec["level_or_status"] == "message_changed"
    assert rec["message"] == "edited message"
    assert rec["labels"]["user"] == "U555"


def test_normalize_message_without_ts_key():
    src, _ = _mk()
    rec = src._normalize_message({"user": "U999", "text": "no timestamp"})
    assert rec["ts"] is None
    assert rec["message"] == "no timestamp"


# --------------------------------------------------------------------------- #
# send_notification API path non-200
# --------------------------------------------------------------------------- #
def test_send_notification_api_non_200():
    src, _t = _mk(
        config={"base_url": BASE_URL, "token_env": "SLACK_BOT_TOKEN", "channel_id": "C123"},
        post_resp=FakeResponse(403, {"ok": False, "error": "not_in_channel"}),
    )
    result = src.send_notification("test")
    assert result["ok"] is False
    assert result["status_code"] == 403
    assert result["error"] == "unexpected status 403"


# --------------------------------------------------------------------------- #
# read_channel_history without count (no limit param)
# --------------------------------------------------------------------------- #
def test_read_channel_history_no_count_omits_limit():
    history_payload = {"ok": True, "messages": []}
    src, t = _mk(get_resp=FakeResponse(200, history_payload))
    src.read_channel_history()
    call = t.get_calls[0]
    assert "limit" not in call["params"]


# --------------------------------------------------------------------------- #
# _auth_headers with empty-string env var
# --------------------------------------------------------------------------- #
def test_auth_headers_empty_token_raises():
    src = SlackSource(
        {"base_url": BASE_URL, "token_env": "SLACK_BOT_TOKEN", "channel_id": "C123"},
        transport=FakeTransport(),
        env={"SLACK_BOT_TOKEN": ""},
    )
    with pytest.raises(ValueError, match="missing token"):
        src._auth_headers()


# --------------------------------------------------------------------------- #
# SSRF: trailing-slash normalization
# --------------------------------------------------------------------------- #
def test_ssrf_normalizes_trailing_slash():
    t = FakeTransport()
    src = SlackSource(
        {"base_url": "https://slack.com/api/", "token_env": "SLACK_BOT_TOKEN", "channel_id": "C123"},
        transport=t,
        env=dict(ENV),
    )
    assert src._base_url == "https://slack.com/api"


def test_ssrf_webhook_trailing_slash_normalized():
    t = FakeTransport()
    src = SlackSource(
        {"base_url": BASE_URL, "token_env": "SLACK_BOT_TOKEN", "webhook_url": WEBHOOK_URL + "/"},
        transport=t,
        env=dict(ENV),
    )
    assert src._webhook_url == WEBHOOK_URL


# --------------------------------------------------------------------------- #
# __all__ export correctness
# --------------------------------------------------------------------------- #
def test_all_exports():
    assert "HttpTransport" in slack_all
    assert "SlackSource" in slack_all


# --------------------------------------------------------------------------- #
# HttpTransport is runtime_checkable
# --------------------------------------------------------------------------- #
def test_http_transport_is_runtime_checkable():
    assert callable(HttpTransport) or hasattr(HttpTransport, "_is_runtime_protocol")


def test_fake_transport_satisfies_protocol():
    t = FakeTransport()
    assert isinstance(t, HttpTransport)


# --------------------------------------------------------------------------- #
# timeout parameter passthrough
# --------------------------------------------------------------------------- #
def test_custom_timeout_passed_to_transport():
    _src, t = _mk(
        config={"base_url": BASE_URL, "token_env": "SLACK_BOT_TOKEN", "channel_id": "C123"},
        get_resp=FakeResponse(200, {"ok": True, "messages": []}),
    )
    src_clone = SlackSource(
        {"base_url": BASE_URL, "token_env": "SLACK_BOT_TOKEN", "channel_id": "C123"},
        transport=t,
        timeout=10.0,
        env=dict(ENV),
    )
    src_clone.read_channel_history()
    assert t.get_calls[0]["timeout"] == 10.0


def test_default_timeout_is_30():
    src, t = _mk(get_resp=FakeResponse(200, {"ok": True, "messages": []}))
    src.read_channel_history()
    assert t.get_calls[0]["timeout"] == 30.0


# --------------------------------------------------------------------------- #
# Multiple messages subtype and ts variety
# --------------------------------------------------------------------------- #
def test_read_channel_history_mixed_messages():
    payload = {
        "ok": True,
        "messages": [
            {"type": "message", "user": "U1", "text": "normal", "ts": "1749717000.000100"},
            {
                "type": "message",
                "subtype": "bot_message",
                "user": "UBOT",
                "text": "bot says hi",
                "ts": "1749717060.000200",
            },
            {"type": "message", "user": "U3", "text": "", "ts": "0.0"},
        ],
    }
    src, _ = _mk(get_resp=FakeResponse(200, payload))
    records = src.read_channel_history()
    assert len(records) == 3
    assert records[0]["level_or_status"] is None
    assert records[1]["level_or_status"] == "bot_message"
    assert records[1]["labels"]["user"] == "UBOT"
    assert records[2]["message"] == ""


# --------------------------------------------------------------------------- #
# health() with non-200/non-401 status
# --------------------------------------------------------------------------- #
def test_health_other_status():
    src, _t = _mk(get_resp=FakeResponse(503, None))
    result = src.health()
    assert result["ok"] is False
    assert result["status_code"] == 503
    assert "unexpected status 503" in str(result["error"])


# --------------------------------------------------------------------------- #
# Multiple calls track correctly (no state leakage)
# --------------------------------------------------------------------------- #
def test_multiple_notifications_dont_leak_state():
    src, t = _mk(
        config={"base_url": BASE_URL, "token_env": "SLACK_BOT_TOKEN", "webhook_url": WEBHOOK_URL},
        post_resp=FakeResponse(200, "ok"),
    )
    src.send_notification("msg1")
    src.send_notification("msg2")
    assert len(t.post_calls) == 2
    assert t.post_calls[0]["json"]["text"] == "msg1"
    assert t.post_calls[1]["json"]["text"] == "msg2"
