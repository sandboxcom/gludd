"""Unit tests for the self-contained Slack connector.

All HTTP is mocked -- no network, no real Slack. We exercise:

* outbound ``send_notification()`` via webhook and via chat.postMessage API,
* inbound ``read_channel_history()`` via conversations.history,
* health() via auth.test (OK on 200, not-ok on 401, never raises),
* SSRF: internal/loopback/metadata base_url and webhook_url rejected at construction,
* auth header carries the Bot token sourced from the named env var,
* missing config fields raise cleanly,
* normalized record contract keys.
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.slack import SlackSource, SSRFError


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #
class FakeResponse:
    def __init__(self, status_code: int, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class FakeTransport:
    def __init__(
        self,
        *,
        get_response: FakeResponse | None = None,
        post_response: FakeResponse | None = None,
    ) -> None:
        self._get_response = get_response
        self._post_response = post_response
        self.get_calls: list[dict[str, Any]] = []
        self.post_calls: list[dict[str, Any]] = []

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> FakeResponse:
        self.get_calls.append(
            {"url": url, "headers": headers, "params": params, "timeout": timeout}
        )
        assert self._get_response is not None, "unexpected GET"
        return self._get_response

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        data: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> FakeResponse:
        self.post_calls.append(
            {"url": url, "headers": headers, "data": data, "json": json, "timeout": timeout}
        )
        assert self._post_response is not None, "unexpected POST"
        return self._post_response


# Canned Slack API payloads
AUTH_TEST_OK: dict[str, Any] = {
    "ok": True,
    "url": "https://example.slack.com/",
    "team": "Example Team",
    "user": "bot",
    "team_id": "T123",
    "user_id": "U456",
}

CHANNEL_HISTORY_PAYLOAD: dict[str, Any] = {
    "ok": True,
    "messages": [
        {
            "type": "message",
            "user": "U111",
            "text": "production error in api-gateway",
            "ts": "1749717000.000100",
        },
        {
            "type": "message",
            "user": "U222",
            "text": "disk usage above 90% on db-02",
            "ts": "1749717060.000200",
        },
        {
            "type": "message",
            "user": "U333",
            "text": "deploy v2.3.1 complete",
            "ts": "1749717120.000300",
        },
    ],
    "has_more": False,
}

SEND_MESSAGE_OK: dict[str, Any] = {
    "ok": True,
    "channel": "C123",
    "ts": "1749717200.000400",
}

WEBHOOK_OK_BODY = "ok"


BASE_URL = "https://slack.com/api"
WEBHOOK_URL = "https://hooks.slack.com/services/T000/B000/xxxx"
GOOD_CONFIG: dict[str, Any] = {
    "base_url": BASE_URL,
    "token_env": "SLACK_BOT_TOKEN",
    "channel_id": "C123",
    "webhook_url": WEBHOOK_URL,
}
ENV = {"SLACK_BOT_TOKEN": "xoxb-secret-token"}


def _make_source(
    *,
    get_response: FakeResponse | None = None,
    post_response: FakeResponse | None = None,
    config: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
) -> tuple[SlackSource, FakeTransport]:
    transport = FakeTransport(get_response=get_response, post_response=post_response)
    src = SlackSource(
        config or GOOD_CONFIG,
        transport=transport,
        env=env if env is not None else dict(ENV),
    )
    return src, transport


# --------------------------------------------------------------------------- #
# Contract / construction
# --------------------------------------------------------------------------- #
def test_kind_is_chat() -> None:
    src, _ = _make_source()
    assert src.kind == "chat"
    assert SlackSource.kind == "chat"


def test_name_defaults_to_slack_and_is_overridable() -> None:
    src, _ = _make_source()
    assert src.name == "slack"

    transport = FakeTransport()
    named = SlackSource(GOOD_CONFIG, transport=transport, name="prod-slack", env=dict(ENV))
    assert named.name == "prod-slack"


def test_missing_config_fields_raise() -> None:
    transport = FakeTransport()
    with pytest.raises(ValueError):
        SlackSource({"token_env": "X"}, transport=transport, env=dict(ENV))
    with pytest.raises(ValueError):
        SlackSource({"base_url": BASE_URL}, transport=transport, env=dict(ENV))


def test_webhook_url_optional() -> None:
    transport = FakeTransport()
    src = SlackSource(
        {"base_url": BASE_URL, "token_env": "SLACK_BOT_TOKEN"},
        transport=transport,
        env=dict(ENV),
    )
    assert src._webhook_url is None


def test_channel_id_optional() -> None:
    transport = FakeTransport()
    src = SlackSource(
        {"base_url": BASE_URL, "token_env": "SLACK_BOT_TOKEN"},
        transport=transport,
        env=dict(ENV),
    )
    assert src._channel_id is None


# --------------------------------------------------------------------------- #
# SSRF: literal-host blocking, no DNS
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "bad_url",
    [
        "http://localhost:8089",
        "http://127.0.0.1:8089",
        "https://10.0.0.5",
        "https://192.168.1.10:8089",
        "https://172.16.4.4",
        "http://169.254.169.254",
        "http://metadata.google.internal",
        "https://[::1]:8089",
        "https://[fd00::1]",
        "http://0.0.0.0",
    ],
)
def test_ssrf_internal_base_url_rejected(bad_url: str) -> None:
    transport = FakeTransport()
    with pytest.raises(SSRFError):
        SlackSource(
            {"base_url": bad_url, "token_env": "SLACK_BOT_TOKEN"},
            transport=transport,
            env=dict(ENV),
        )


@pytest.mark.parametrize(
    "bad_webhook",
    [
        "http://localhost:8089/services/T000/B000",
        "http://127.0.0.1:8089/services/T000/B000",
        "https://10.0.0.5/services/T000/B000",
        "http://169.254.169.254/services/T000/B000",
    ],
)
def test_ssrf_internal_webhook_url_rejected(bad_webhook: str) -> None:
    transport = FakeTransport()
    with pytest.raises(SSRFError):
        SlackSource(
            {
                "base_url": BASE_URL,
                "token_env": "SLACK_BOT_TOKEN",
                "webhook_url": bad_webhook,
            },
            transport=transport,
            env=dict(ENV),
        )


def test_ssrf_rejects_non_http_scheme() -> None:
    transport = FakeTransport()
    with pytest.raises(SSRFError):
        SlackSource(
            {"base_url": "ftp://slack.com/api", "token_env": "SLACK_BOT_TOKEN"},
            transport=transport,
            env=dict(ENV),
        )


def test_ssrf_allows_public_host() -> None:
    src, _ = _make_source(
        config={"base_url": "https://slack.com/api", "token_env": "SLACK_BOT_TOKEN"}
    )
    assert src.name == "slack"


def test_ssrf_does_not_resolve_dns() -> None:
    src, _ = _make_source(
        config={
            "base_url": "https://internal-looking.example.org",
            "token_env": "SLACK_BOT_TOKEN",
        }
    )
    assert src.name == "slack"


# --------------------------------------------------------------------------- #
# health()
# --------------------------------------------------------------------------- #
def test_health_ok_on_200() -> None:
    src, transport = _make_source(get_response=FakeResponse(200, AUTH_TEST_OK))
    result = src.health()
    assert result["ok"] is True
    assert result["status_code"] == 200
    assert result["kind"] == "chat"
    assert transport.get_calls[0]["url"].endswith("/auth.test")


def test_health_not_ok_on_401_and_never_raises() -> None:
    src, _ = _make_source(get_response=FakeResponse(401, None))
    result = src.health()
    assert result["ok"] is False
    assert result["status_code"] == 401
    assert "auth" in result["error"].lower()


def test_health_never_raises_on_transport_error() -> None:
    class BoomTransport(FakeTransport):
        def get(self, *a: Any, **k: Any) -> FakeResponse:
            raise ConnectionError("network down")

    src = SlackSource(GOOD_CONFIG, transport=BoomTransport(), env=dict(ENV))
    result = src.health()
    assert result["ok"] is False
    assert "error" in result


def test_health_uses_bearer_token_from_env() -> None:
    src, transport = _make_source(get_response=FakeResponse(200, AUTH_TEST_OK))
    src.health()
    auth = transport.get_calls[0]["headers"]["Authorization"]
    assert auth == "Bearer xoxb-secret-token"


def test_missing_env_token_makes_health_not_ok() -> None:
    src = SlackSource(GOOD_CONFIG, transport=FakeTransport(), env={})
    result = src.health()
    assert result["ok"] is False
    assert "error" in result


# --------------------------------------------------------------------------- #
# send_notification() — webhook path
# --------------------------------------------------------------------------- #
def test_send_notification_via_webhook() -> None:
    src, transport = _make_source(
        config={
            "base_url": BASE_URL,
            "token_env": "SLACK_BOT_TOKEN",
            "webhook_url": WEBHOOK_URL,
        },
        post_response=FakeResponse(200, WEBHOOK_OK_BODY),
    )
    result = src.send_notification("deploy succeeded")
    assert result["ok"] is True
    assert transport.post_calls[0]["url"] == WEBHOOK_URL
    assert transport.post_calls[0]["json"] == {"text": "deploy succeeded"}


def test_send_notification_via_chat_postMessage() -> None:
    src, transport = _make_source(
        config={
            "base_url": BASE_URL,
            "token_env": "SLACK_BOT_TOKEN",
            "channel_id": "C123",
        },
        post_response=FakeResponse(200, SEND_MESSAGE_OK),
    )
    result = src.send_notification("deploy succeeded")
    assert result["ok"] is True
    call = transport.post_calls[0]
    assert call["url"] == f"{BASE_URL}/chat.postMessage"
    assert call["json"]["channel"] == "C123"
    assert call["json"]["text"] == "deploy succeeded"
    assert call["headers"]["Authorization"] == "Bearer xoxb-secret-token"


def test_send_notification_prefers_webhook_over_api() -> None:
    src, transport = _make_source(
        config={
            "base_url": BASE_URL,
            "token_env": "SLACK_BOT_TOKEN",
            "channel_id": "C123",
            "webhook_url": WEBHOOK_URL,
        },
        post_response=FakeResponse(200, WEBHOOK_OK_BODY),
    )
    src.send_notification("hello")
    assert transport.post_calls[0]["url"] == WEBHOOK_URL


def test_send_notification_no_target_raises() -> None:
    src, _ = _make_source(
        config={"base_url": BASE_URL, "token_env": "SLACK_BOT_TOKEN"}
    )
    with pytest.raises(ValueError, match="webhook_url or channel_id"):
        src.send_notification("hello")


def test_send_notification_never_raises_on_transport_error() -> None:
    class BoomTransport(FakeTransport):
        def post(self, *a: Any, **k: Any) -> FakeResponse:
            raise ConnectionError("network down")

    src = SlackSource(GOOD_CONFIG, transport=BoomTransport(), env=dict(ENV))
    result = src.send_notification("hello")
    assert result["ok"] is False
    assert "error" in result


def test_send_notification_non_200_returns_error() -> None:
    src, _ = _make_source(
        config={"base_url": BASE_URL, "token_env": "SLACK_BOT_TOKEN", "webhook_url": WEBHOOK_URL},
        post_response=FakeResponse(404, {"ok": False, "error": "channel_not_found"}),
    )
    result = src.send_notification("hello")
    assert result["ok"] is False
    assert result["status_code"] == 404


# --------------------------------------------------------------------------- #
# read_channel_history()
# --------------------------------------------------------------------------- #
def test_read_channel_history_normalizes_records() -> None:
    src, transport = _make_source(
        get_response=FakeResponse(200, CHANNEL_HISTORY_PAYLOAD),
        config={
            "base_url": BASE_URL,
            "token_env": "SLACK_BOT_TOKEN",
            "channel_id": "C123",
        },
    )
    records = src.read_channel_history(count=10)
    assert len(records) == 3
    call = transport.get_calls[0]
    assert call["url"] == f"{BASE_URL}/conversations.history"
    assert call["params"]["channel"] == "C123"
    assert call["params"]["limit"] == 10


def test_read_channel_history_requires_channel_id() -> None:
    src, _ = _make_source(
        config={"base_url": BASE_URL, "token_env": "SLACK_BOT_TOKEN"},
    )
    with pytest.raises(ValueError, match="channel_id"):
        src.read_channel_history()


def test_read_channel_history_first_record_normalized() -> None:
    src, _ = _make_source(
        get_response=FakeResponse(200, CHANNEL_HISTORY_PAYLOAD),
        config={
            "base_url": BASE_URL,
            "token_env": "SLACK_BOT_TOKEN",
            "channel_id": "C123",
        },
    )
    records = src.read_channel_history()
    rec = records[0]
    assert rec["kind"] == "chat"
    assert rec["source"] == "slack"
    assert rec["message"] == "production error in api-gateway"
    assert rec["labels"]["user"] == "U111"
    assert rec["labels"]["channel_id"] == "C123"


def test_read_channel_history_uses_bearer_token() -> None:
    src, transport = _make_source(
        get_response=FakeResponse(200, CHANNEL_HISTORY_PAYLOAD),
        config={
            "base_url": BASE_URL,
            "token_env": "SLACK_BOT_TOKEN",
            "channel_id": "C123",
        },
    )
    src.read_channel_history()
    assert transport.get_calls[0]["headers"]["Authorization"] == "Bearer xoxb-secret-token"


def test_read_channel_history_non_200_raises() -> None:
    src, _ = _make_source(
        get_response=FakeResponse(503, None),
        config={
            "base_url": BASE_URL,
            "token_env": "SLACK_BOT_TOKEN",
            "channel_id": "C123",
        },
    )
    with pytest.raises(RuntimeError):
        src.read_channel_history()


def test_read_channel_history_empty_ok() -> None:
    src, _ = _make_source(
        get_response=FakeResponse(200, {"ok": True, "messages": []}),
        config={
            "base_url": BASE_URL,
            "token_env": "SLACK_BOT_TOKEN",
            "channel_id": "C123",
        },
    )
    assert src.read_channel_history() == []


def test_read_channel_history_transport_error_never_raises() -> None:
    class BoomTransport(FakeTransport):
        def get(self, *a: Any, **k: Any) -> FakeResponse:
            raise OSError("timeout")

    src = SlackSource(
        {"base_url": BASE_URL, "token_env": "SLACK_BOT_TOKEN", "channel_id": "C123"},
        transport=BoomTransport(),
        env=dict(ENV),
    )
    records = src.read_channel_history()
    assert records == []


# --------------------------------------------------------------------------- #
# Normalized record contract
# --------------------------------------------------------------------------- #
def test_normalized_record_has_all_contract_keys() -> None:
    src, _ = _make_source(
        get_response=FakeResponse(200, CHANNEL_HISTORY_PAYLOAD),
        config={
            "base_url": BASE_URL,
            "token_env": "SLACK_BOT_TOKEN",
            "channel_id": "C123",
        },
    )
    rec = src.read_channel_history()[0]
    for key in ("ts", "source", "kind", "level_or_status", "message", "value", "labels", "raw"):
        assert key in rec


def test_read_channel_history_preserves_raw() -> None:
    src, _ = _make_source(
        get_response=FakeResponse(200, CHANNEL_HISTORY_PAYLOAD),
        config={
            "base_url": BASE_URL,
            "token_env": "SLACK_BOT_TOKEN",
            "channel_id": "C123",
        },
    )
    records = src.read_channel_history()
    assert records[0]["raw"]["ts"] == "1749717000.000100"
    assert records[0]["raw"]["type"] == "message"
