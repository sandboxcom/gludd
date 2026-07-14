"""Structural tests for the Slack connector."""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.slack import (
    SlackSource,
    _parse_slack_ts,
)


class _FakeHttpResponse:
    def __init__(self, status_code: int = 200, body: Any = None) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        return self._body or {}


class _FakeTransport:
    def __init__(self, status: int = 200, body: Any = None) -> None:
        self._status = status
        self._body = body
        self.calls: list[dict[str, Any]] = []

    def get(
        self, url: str, *, headers: dict[str, str],
        params: dict[str, object] | None = None, timeout: float = 30.0,
    ) -> _FakeHttpResponse:
        self.calls.append({"method": "GET", "url": url, "headers": headers, "params": params})
        return _FakeHttpResponse(self._status, self._body)

    def post(
        self, url: str, *, headers: dict[str, str],
        data: dict[str, object] | None = None,
        json: dict[str, object] | None = None, timeout: float = 30.0,
    ) -> _FakeHttpResponse:
        self.calls.append({"method": "POST", "url": url, "headers": headers, "json_body": json})
        return _FakeHttpResponse(self._status, self._body)


@pytest.fixture
def fake_env() -> dict[str, str]:
    return {"SLACK_BOT_TOKEN": "xoxb-test-token"}


class TestParseSlackTs:
    def test_valid(self) -> None:
        result = _parse_slack_ts("1736899200.001000")
        assert result is not None
        assert "T" in result
        assert result.endswith("+00:00")

    def test_invalid(self) -> None:
        assert _parse_slack_ts("not-a-number") is None

    def test_none(self) -> None:
        assert _parse_slack_ts(None) is None  # type: ignore[arg-type]


class TestSlackSourceInit:
    def test_init_minimal(self, fake_env: dict[str, str]) -> None:
        src = SlackSource(
            {"base_url": "https://slack.com/api", "token_env": "SLACK_BOT_TOKEN"},
            transport=_FakeTransport(),
            env=fake_env,
        )
        assert src.name == "slack"

    def test_init_missing_base_url_raises(self) -> None:
        with pytest.raises(ValueError, match="base_url"):
            SlackSource({"token_env": "TOKEN"}, transport=_FakeTransport())

    def test_init_missing_token_env_raises(self) -> None:
        with pytest.raises(ValueError, match="token_env"):
            SlackSource({"base_url": "https://slack.com/api"}, transport=_FakeTransport())

    def test_init_with_webhook_url(self, fake_env: dict[str, str]) -> None:
        src = SlackSource(
            {
                "base_url": "https://slack.com/api",
                "token_env": "SLACK_BOT_TOKEN",
                "webhook_url": "https://hooks.slack.com/services/T/B/Q",
            },
            transport=_FakeTransport(),
            env=fake_env,
        )
        assert src._webhook_url == "https://hooks.slack.com/services/T/B/Q"

    def test_init_with_channel_id(self, fake_env: dict[str, str]) -> None:
        src = SlackSource(
            {
                "base_url": "https://slack.com/api",
                "token_env": "SLACK_BOT_TOKEN",
                "channel_id": "C123",
            },
            transport=_FakeTransport(),
            env=fake_env,
        )
        assert src._channel_id == "C123"

    def test_init_custom_name(self, fake_env: dict[str, str]) -> None:
        src = SlackSource(
            {"base_url": "https://slack.com/api", "token_env": "SLACK_BOT_TOKEN"},
            transport=_FakeTransport(),
            name="my-slack",
            env=fake_env,
        )
        assert src.name == "my-slack"

    def test_init_ssrf_blocks_loopback(self) -> None:
        from general_ludd.connectors._errors import SSRFError

        with pytest.raises(SSRFError):
            SlackSource(
                {"base_url": "http://127.0.0.1/api", "token_env": "TOKEN"},
                transport=_FakeTransport(),
                env={"TOKEN": "x"},
            )


class TestHealth:
    def test_health_ok(self, fake_env: dict[str, str]) -> None:
        t = _FakeTransport(200, {"ok": True})
        src = SlackSource(
            {"base_url": "https://slack.com/api", "token_env": "SLACK_BOT_TOKEN"},
            transport=t, env=fake_env,
        )
        result = src.health()
        assert result["ok"] is True

    def test_health_auth_failed(self, fake_env: dict[str, str]) -> None:
        t = _FakeTransport(401)
        src = SlackSource(
            {"base_url": "https://slack.com/api", "token_env": "SLACK_BOT_TOKEN"},
            transport=t, env=fake_env,
        )
        result = src.health()
        assert result["ok"] is False
        assert result["error"] == "authentication failed"

    def test_health_unexpected_status(self, fake_env: dict[str, str]) -> None:
        t = _FakeTransport(500)
        src = SlackSource(
            {"base_url": "https://slack.com/api", "token_env": "SLACK_BOT_TOKEN"},
            transport=t, env=fake_env,
        )
        result = src.health()
        assert result["ok"] is False
        assert "500" in str(result["error"])

    def test_health_never_raises(self, fake_env: dict[str, str]) -> None:
        class _BoomTransport:
            def get(self, url: str, **kwargs: Any) -> None:
                raise RuntimeError("boom")

        src = SlackSource(
            {"base_url": "https://slack.com/api", "token_env": "SLACK_BOT_TOKEN"},
            transport=_BoomTransport(), env=fake_env,
        )
        result = src.health()
        assert result["ok"] is False


class TestSendNotification:
    def test_webhook_success(self, fake_env: dict[str, str]) -> None:
        t = _FakeTransport(200, {"ok": True})
        src = SlackSource(
            {
                "base_url": "https://slack.com/api",
                "token_env": "SLACK_BOT_TOKEN",
                "webhook_url": "https://hooks.slack.com/services/A/B/C",
            },
            transport=t, env=fake_env,
        )
        result = src.send_notification("hello")
        assert result["ok"] is True

    def test_no_webhook_or_channel_raises(self, fake_env: dict[str, str]) -> None:
        src = SlackSource(
            {"base_url": "https://slack.com/api", "token_env": "SLACK_BOT_TOKEN"},
            transport=_FakeTransport(), env=fake_env,
        )
        with pytest.raises(ValueError, match="webhook_url or channel_id"):
            src.send_notification("hello")

    def test_api_post_success(self, fake_env: dict[str, str]) -> None:
        t = _FakeTransport(200, {"ok": True})
        src = SlackSource(
            {
                "base_url": "https://slack.com/api",
                "token_env": "SLACK_BOT_TOKEN",
                "channel_id": "C123",
            },
            transport=t, env=fake_env,
        )
        result = src.send_notification("hello via api")
        assert result["ok"] is True

    def test_api_post_non_200(self, fake_env: dict[str, str]) -> None:
        t = _FakeTransport(500)
        src = SlackSource(
            {"base_url": "https://slack.com/api", "token_env": "SLACK_BOT_TOKEN", "channel_id": "C123"},
            transport=t, env=fake_env,
        )
        result = src.send_notification("boom")
        assert result["ok"] is False
        assert result["status_code"] == 500


class TestReadChannelHistory:
    def test_success(self, fake_env: dict[str, str]) -> None:
        messages = [{"user": "U1", "text": "hello", "ts": "1736899200.001000"}]
        t = _FakeTransport(200, {"messages": messages})
        src = SlackSource(
            {"base_url": "https://slack.com/api", "token_env": "SLACK_BOT_TOKEN", "channel_id": "C123"},
            transport=t, env=fake_env,
        )
        results = src.read_channel_history(count=10)
        assert len(results) == 1
        assert results[0]["source"] == "slack"
        assert results[0]["kind"] == "chat"
        assert results[0]["message"] == "hello"
        assert results[0]["labels"]["user"] == "U1"

    def test_no_channel_id_raises(self, fake_env: dict[str, str]) -> None:
        src = SlackSource(
            {"base_url": "https://slack.com/api", "token_env": "SLACK_BOT_TOKEN"},
            transport=_FakeTransport(), env=fake_env,
        )
        with pytest.raises(ValueError, match="channel_id"):
            src.read_channel_history()

    def test_transport_error_returns_empty(self, fake_env: dict[str, str]) -> None:
        class _BoomTransport:
            def get(self, *args: Any, **kwargs: Any) -> None:
                raise RuntimeError("boom")
            def post(self, *args: Any, **kwargs: Any) -> None:
                raise RuntimeError("boom")

        src = SlackSource(
            {"base_url": "https://slack.com/api", "token_env": "SLACK_BOT_TOKEN", "channel_id": "C123"},
            transport=_BoomTransport(), env=fake_env,
        )
        assert src.read_channel_history() == []

    def test_non_200_returns_empty(self, fake_env: dict[str, str]) -> None:
        t = _FakeTransport(404)
        src = SlackSource(
            {"base_url": "https://slack.com/api", "token_env": "SLACK_BOT_TOKEN", "channel_id": "C123"},
            transport=t, env=fake_env,
        )
        assert src.read_channel_history() == []


class TestExtractMessages:
    def test_valid(self) -> None:
        assert len(SlackSource._extract_messages({"messages": [{"a": 1}, {"b": 2}]})) == 2

    def test_not_a_dict(self) -> None:
        assert SlackSource._extract_messages(None) == []
        assert SlackSource._extract_messages("string") == []
        assert SlackSource._extract_messages([]) == []

    def test_messages_not_a_list(self) -> None:
        assert SlackSource._extract_messages({"messages": "string"}) == []

    def test_filters_non_dicts(self) -> None:
        payload = {"messages": [{"a": 1}, "string", None, {"b": 2}]}
        assert len(SlackSource._extract_messages(payload)) == 2


class TestNormalizeMessage:
    def test_normalize(self, fake_env: dict[str, str]) -> None:
        src = SlackSource(
            {"base_url": "https://slack.com/api", "token_env": "SLACK_BOT_TOKEN", "channel_id": "C123"},
            transport=_FakeTransport(), env=fake_env,
        )
        msg = {"user": "U1", "text": "hello world", "ts": "1736899200.001000", "subtype": "bot_message"}
        result = src._normalize_message(msg)
        assert result["source"] == "slack"
        assert result["kind"] == "chat"
        assert result["message"] == "hello world"
        assert result["labels"]["user"] == "U1"
        assert result["labels"]["channel_id"] == "C123"
        assert result["level_or_status"] == "bot_message"
        expected_raw = {"user": "U1", "text": "hello world", "ts": "1736899200.001000", "subtype": "bot_message"}
        assert result["raw"] == expected_raw
