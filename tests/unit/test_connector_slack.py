"""Structural tests for Slack outbound connector."""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors._errors import SSRFError
from general_ludd.connectors.slack import (
    SlackSource,
    _assert_safe_url,
    _parse_slack_ts,
)


class FakeResponse:
    def __init__(self, status_code: int = 200, body: Any = None) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        return self._body or {}


class FakeTransport:
    def __init__(self, status: int = 200, body: Any = None) -> None:
        self._status = status
        self._body = body
        self.calls: list[dict[str, Any]] = []

    def get(
        self, url: str, *, headers: dict[str, str],
        params: dict[str, object] | None = None, timeout: float = 30.0,
    ) -> FakeResponse:
        self.calls.append({"method": "GET", "url": url, "headers": headers, "params": params})
        return FakeResponse(self._status, self._body)

    def post(
        self, url: str, *, headers: dict[str, str],
        data: dict[str, object] | None = None,
        json: dict[str, object] | None = None, timeout: float = 30.0,
    ) -> FakeResponse:
        self.calls.append({"method": "POST", "url": url, "headers": headers, "json": json})
        return FakeResponse(self._status, self._body)


class BoomTransport:
    def get(self, *args: Any, **kwargs: Any) -> FakeResponse:
        raise RuntimeError("down")

    def post(self, *args: Any, **kwargs: Any) -> FakeResponse:
        raise RuntimeError("down")


@pytest.fixture
def env() -> dict[str, str]:
    return {"SLACK_TOKEN": "xoxb-test-token"}


class TestParseTs:
    def test_valid(self) -> None:
        r = _parse_slack_ts("1736899200.001000")
        assert r is not None
        assert "T" in r
        assert r.endswith("+00:00")

    def test_invalid(self) -> None:
        assert _parse_slack_ts("not-a-number") is None

    def test_none(self) -> None:
        assert _parse_slack_ts(None) is None  # type: ignore[arg-type]


class TestSSRF:
    def test_safe_url_public_ok(self) -> None:
        r = _assert_safe_url("https://slack.com/api/")
        assert r == "https://slack.com/api"

    def test_safe_url_loopback_raises(self) -> None:
        with pytest.raises(SSRFError):
            _assert_safe_url("http://localhost/")

    def test_safe_url_metadata_raises(self) -> None:
        with pytest.raises(SSRFError):
            _assert_safe_url("http://169.254.169.254/")


class TestInit:
    def test_minimal(self, env: dict[str, str]) -> None:
        src = SlackSource(
            {"base_url": "https://slack.com/api", "token_env": "SLACK_TOKEN"},
            transport=FakeTransport(),
            env=env,
        )
        assert src.name == "slack"

    def test_missing_base_url_raises(self) -> None:
        with pytest.raises(ValueError, match="base_url"):
            SlackSource({"token_env": "T"}, transport=FakeTransport())

    def test_missing_token_env_raises(self) -> None:
        with pytest.raises(ValueError, match="token_env"):
            SlackSource({"base_url": "https://s.com/api"}, transport=FakeTransport())

    def test_with_webhook_url(self, env: dict[str, str]) -> None:
        src = SlackSource(
            {"base_url": "https://slack.com/api", "token_env": "SLACK_TOKEN", "webhook_url": "https://hooks.slack.com/xxx"},
            transport=FakeTransport(),
            env=env,
        )
        assert src._webhook_url == "https://hooks.slack.com/xxx"

    def test_with_channel_id(self, env: dict[str, str]) -> None:
        src = SlackSource(
            {"base_url": "https://slack.com/api", "token_env": "SLACK_TOKEN", "channel_id": "C123"},
            transport=FakeTransport(),
            env=env,
        )
        assert src._channel_id == "C123"

    def test_custom_name(self, env: dict[str, str]) -> None:
        src = SlackSource(
            {"base_url": "https://slack.com/api", "token_env": "SLACK_TOKEN"},
            transport=FakeTransport(),
            env=env,
            name="ops-slack",
        )
        assert src.name == "ops-slack"


class TestHealth:
    def test_ok(self, env: dict[str, str]) -> None:
        src = SlackSource(
            {"base_url": "https://slack.com/api", "token_env": "SLACK_TOKEN"},
            transport=FakeTransport(200, {"ok": True}),
            env=env,
        )
        r = src.health()
        assert r["ok"] is True

    def test_transport_error(self, env: dict[str, str]) -> None:
        src = SlackSource(
            {"base_url": "https://slack.com/api", "token_env": "SLACK_TOKEN"},
            transport=BoomTransport(),
            env=env,
        )
        r = src.health()
        assert r["ok"] is False

    def test_auth_failure(self, env: dict[str, str]) -> None:
        src = SlackSource(
            {"base_url": "https://slack.com/api", "token_env": "SLACK_TOKEN"},
            transport=FakeTransport(401, {"ok": False}),
            env=env,
        )
        r = src.health()
        assert r["ok"] is False


class TestSendNotification:
    def test_webhook_post(self, env: dict[str, str]) -> None:
        t = FakeTransport(200)
        src = SlackSource(
            {"base_url": "https://slack.com/api", "token_env": "SLACK_TOKEN", "webhook_url": "https://hooks.slack.com/xxx"},
            transport=t, env=env,
        )
        r = src.send_notification("hello")
        assert r["ok"] is True
        assert t.calls[0]["method"] == "POST"
        assert t.calls[0]["json"]["text"] == "hello"

    def test_api_post(self, env: dict[str, str]) -> None:
        t = FakeTransport(200)
        src = SlackSource(
            {"base_url": "https://slack.com/api", "token_env": "SLACK_TOKEN", "channel_id": "C123"},
            transport=t, env=env,
        )
        r = src.send_notification("hello")
        assert r["ok"] is True
        assert "chat.postMessage" in t.calls[0]["url"]

    def test_webhook_fail_soft(self, env: dict[str, str]) -> None:
        t = FakeTransport(500)
        src = SlackSource(
            {"base_url": "https://slack.com/api", "token_env": "SLACK_TOKEN", "webhook_url": "https://hooks.slack.com/xxx"},
            transport=t, env=env,
        )
        r = src.send_notification("hello")
        assert r["ok"] is False

    def test_no_webhook_or_channel_raises(self, env: dict[str, str]) -> None:
        src = SlackSource(
            {"base_url": "https://slack.com/api", "token_env": "SLACK_TOKEN"},
            transport=FakeTransport(), env=env,
        )
        with pytest.raises(ValueError):
            src.send_notification("hello")

    def test_transport_error_fail_soft(self, env: dict[str, str]) -> None:
        src = SlackSource(
            {"base_url": "https://slack.com/api", "token_env": "SLACK_TOKEN", "webhook_url": "https://hooks.slack.com/xxx"},
            transport=BoomTransport(), env=env,
        )
        r = src.send_notification("hello")
        assert r["ok"] is False

    def test_callable_transport_compatibility(self, env: dict[str, str]) -> None:
        calls: list[dict[str, Any]] = []

        def transport(
            method: str,
            url: str,
            **kwargs: Any,
        ) -> tuple[int, object]:
            calls.append({"method": method, "url": url, **kwargs})
            return 200, {"ok": True}

        src = SlackSource(
            {
                "base_url": "https://slack.com/api",
                "token_env": "SLACK_TOKEN",
                "webhook_url": "https://hooks.slack.com/xxx",
            },
            transport=transport,
            env=env,
        )

        result = src.send_notification("hello")

        assert result["ok"] is True
        assert calls[0]["method"] == "POST"
        assert calls[0]["json"] == {"text": "hello"}


class TestReadChannelHistory:
    def test_returns_messages(self, env: dict[str, str]) -> None:
        body = {"messages": [{"ts": "1736899200.001", "text": "hi", "user": "U1"}]}
        t = FakeTransport(200, body)
        src = SlackSource(
            {"base_url": "https://slack.com/api", "token_env": "SLACK_TOKEN", "channel_id": "C123"},
            transport=t, env=env,
        )
        records = src.read_channel_history(count=5)
        assert len(records) == 1
        assert records[0]["kind"] == "chat"
        assert records[0]["message"] == "hi"
        assert records[0]["labels"]["user"] == "U1"

    def test_no_channel_raises(self, env: dict[str, str]) -> None:
        src = SlackSource(
            {"base_url": "https://slack.com/api", "token_env": "SLACK_TOKEN"},
            transport=FakeTransport(), env=env,
        )
        with pytest.raises(ValueError):
            src.read_channel_history()

    def test_transport_error_returns_empty(self, env: dict[str, str]) -> None:
        src = SlackSource(
            {"base_url": "https://slack.com/api", "token_env": "SLACK_TOKEN", "channel_id": "C123"},
            transport=BoomTransport(), env=env,
        )
        assert src.read_channel_history() == []

    def test_bad_status_returns_empty(self, env: dict[str, str]) -> None:
        t = FakeTransport(500)
        src = SlackSource(
            {"base_url": "https://slack.com/api", "token_env": "SLACK_TOKEN", "channel_id": "C123"},
            transport=t, env=env,
        )
        assert src.read_channel_history() == []

    def test_non_dict_payload(self, env: dict[str, str]) -> None:
        t = FakeTransport(200, "plain string")
        src = SlackSource(
            {"base_url": "https://slack.com/api", "token_env": "SLACK_TOKEN", "channel_id": "C123"},
            transport=t, env=env,
        )
        records = src.read_channel_history()
        assert records == []
