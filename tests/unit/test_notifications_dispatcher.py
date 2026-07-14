"""Structural + behavioral tests for notifications/dispatcher.py — multi-backend notification dispatch."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest

from general_ludd.notifications.dispatcher import (
    BACKEND_NAMES,
    FALLBACK_NOTIFICATION_CONFIG,
    NOTIFICATION_TEMPLATE,
    PRIORITY_LEVELS,
    HttpTransport,
    NotificationDispatcher,
    logger,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _enabled_config(**overrides: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "enabled": True,
        "backends": {"stdout": {}},
        "min_priority": "low",
    }
    cfg.update(overrides)
    return cfg


def _mock_slack_source(return_value: dict[str, object] | None = None) -> MagicMock:
    src = MagicMock()
    src.send_notification.return_value = return_value or {"ok": True}
    return src


def _mock_transport(status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    transport = MagicMock()
    transport.post.return_value = resp
    return transport


# ---------------------------------------------------------------------------
# structural (existing)
# ---------------------------------------------------------------------------


class TestModuleImports:
    def test_module_can_be_imported(self):
        import general_ludd.notifications.dispatcher

        assert general_ludd.notifications.dispatcher is not None

    def test_logger_exists(self):
        assert isinstance(logger, logging.Logger)
        assert logger.name == "general_ludd.notifications.dispatcher"


class TestConstants:
    def test_backend_names(self):
        assert isinstance(BACKEND_NAMES, frozenset)
        assert "slack" in BACKEND_NAMES
        assert "stdout" in BACKEND_NAMES
        assert "webhook" in BACKEND_NAMES

    def test_priority_levels(self):
        assert isinstance(PRIORITY_LEVELS, dict)
        assert PRIORITY_LEVELS["low"] == 0
        assert PRIORITY_LEVELS["medium"] == 1
        assert PRIORITY_LEVELS["high"] == 2
        assert PRIORITY_LEVELS["urgent"] == 3

    def test_fallback_notification_config(self):
        assert isinstance(FALLBACK_NOTIFICATION_CONFIG, dict)
        assert "enabled" in FALLBACK_NOTIFICATION_CONFIG
        assert "backends" in FALLBACK_NOTIFICATION_CONFIG
        assert "min_priority" in FALLBACK_NOTIFICATION_CONFIG

    def test_notification_template_contains_placeholders(self):
        assert "{id}" in NOTIFICATION_TEMPLATE
        assert "{title}" in NOTIFICATION_TEMPLATE
        assert "{priority}" in NOTIFICATION_TEMPLATE
        assert "{category}" in NOTIFICATION_TEMPLATE


class TestHttpTransport:
    def test_is_runtime_checkable_protocol(self):
        assert hasattr(HttpTransport, "__protocol_attrs__") or callable(HttpTransport)


# ---------------------------------------------------------------------------
# constructor + config
# ---------------------------------------------------------------------------


class TestNotificationDispatcherConstruction:
    def test_defaults_when_empty_config(self):
        d = NotificationDispatcher({})
        assert d._enabled is False
        assert d._backends == {"stdout": {}}
        assert d._min_priority == "high"

    def test_enabled_explicit(self):
        d = NotificationDispatcher({"enabled": True})
        assert d._enabled is True

    def test_min_priority_maps_to_value(self):
        d = NotificationDispatcher({"min_priority": "low"})
        assert d._min_priority == "low"
        assert d._min_priority_val == 0

        d2 = NotificationDispatcher({"min_priority": "urgent"})
        assert d2._min_priority_val == 3

    def test_unknown_min_priority_defaults_to_2(self):
        d = NotificationDispatcher({"min_priority": "bogus"})
        assert d._min_priority_val == 2

    def test_custom_backends(self):
        d = NotificationDispatcher(
            {"backends": {"slack": {"source": "ops"}, "webhook": {"url": "https://h.example"}}}
        )
        assert "slack" in d._backends
        assert "webhook" in d._backends
        assert d._backends["slack"] == {"source": "ops"}

    def test_slack_sources_passed(self):
        d = NotificationDispatcher({}, slack_sources={"ops": object()})
        assert "ops" in d._slack_sources

    def test_transport_passed(self):
        transport = MagicMock()
        d = NotificationDispatcher({}, transport=transport)
        assert d._transport is transport

    def test_env_passed(self):
        d = NotificationDispatcher({}, env={"FOO": "bar"})
        assert d._env == {"FOO": "bar"}


# ---------------------------------------------------------------------------
# message formatting
# ---------------------------------------------------------------------------


class TestFormatMessage:
    def test_full_fields(self):
        d = NotificationDispatcher({"enabled": False})
        msg = d._format_message({
            "id": "todo-1",
            "title": "Fix bug",
            "priority": "high",
            "category": "bug",
            "agent_id": "agent-42",
            "body": "Something is broken",
        })
        assert "todo-1" in msg
        assert "Fix bug" in msg
        assert "high" in msg
        assert "bug" in msg
        assert "agent-42" in msg
        assert "Something is broken" in msg

    def test_missing_id_shows_question_mark(self):
        d = NotificationDispatcher({"enabled": False})
        msg = d._format_message({})
        assert "#?" in msg or "?" in msg.split("\n", 1)[0]

    def test_missing_title_defaults_to_untitled(self):
        d = NotificationDispatcher({"enabled": False})
        msg = d._format_message({"id": "x"})
        assert "untitled" in msg

    def test_missing_priority_defaults_to_medium(self):
        d = NotificationDispatcher({"enabled": False})
        msg = d._format_message({"id": "x", "title": "t"})
        assert "medium" in msg

    def test_empty_todo(self):
        d = NotificationDispatcher({"enabled": False})
        msg = d._format_message({})
        assert isinstance(msg, str)
        assert len(msg) > 0


# ---------------------------------------------------------------------------
# priority threshold
# ---------------------------------------------------------------------------


class TestPriorityMeetsThreshold:
    @pytest.mark.parametrize("threshold,priority,expected", [
        ("low", "low", True),
        ("low", "urgent", True),
        ("medium", "low", False),
        ("medium", "medium", True),
        ("high", "medium", False),
        ("high", "high", True),
        ("urgent", "high", False),
        ("urgent", "urgent", True),
    ])
    def test_combinations(self, threshold, priority, expected):
        d = NotificationDispatcher({"min_priority": threshold})
        assert d._priority_meets_threshold(priority) == expected

    def test_unknown_priority_defaults_to_0(self):
        d = NotificationDispatcher({"min_priority": "low"})
        assert d._min_priority_val == 0
        assert d._priority_meets_threshold("bogus") is True

    def test_unknown_priority_blocked_by_medium_threshold(self):
        d = NotificationDispatcher({"min_priority": "medium"})
        assert d._min_priority_val == 1
        assert d._priority_meets_threshold("bogus") is False


# ---------------------------------------------------------------------------
# stdout backend
# ---------------------------------------------------------------------------


class TestDispatchStdout:
    def test_returns_ok(self):
        d = NotificationDispatcher({"enabled": False})
        result = d._dispatch_stdout("hello")
        assert result == {"ok": True, "backend": "stdout"}

    def test_prints_message(self, capsys):
        d = NotificationDispatcher({"enabled": False})
        d._dispatch_stdout("hello world")
        captured = capsys.readouterr()
        assert "hello world" in captured.out

    def test_empty_message(self, capsys):
        d = NotificationDispatcher({"enabled": False})
        result = d._dispatch_stdout("")
        assert result == {"ok": True, "backend": "stdout"}


# ---------------------------------------------------------------------------
# slack backend
# ---------------------------------------------------------------------------


class TestDispatchSlack:
    def test_no_source_configured(self):
        d = NotificationDispatcher({"enabled": False}, slack_sources={})
        result = d._dispatch_slack("hello", {})
        assert result["ok"] is False
        assert result["backend"] == "slack"
        assert "not found" in str(result["error"])

    def test_source_not_in_sources_dict(self):
        d = NotificationDispatcher(
            {"enabled": False}, slack_sources={"ops": _mock_slack_source()}
        )
        result = d._dispatch_slack("hello", {"source": "alerts"})
        assert result["ok"] is False
        assert "alerts" in str(result["error"])

    def test_sends_via_source(self):
        src = _mock_slack_source()
        d = NotificationDispatcher({"enabled": False}, slack_sources={"ops": src})
        result = d._dispatch_slack("hello world", {"source": "ops"})
        assert result["ok"] is True
        src.send_notification.assert_called_once_with("hello world")

    def test_default_source_name(self):
        src = _mock_slack_source()
        d = NotificationDispatcher({"enabled": False}, slack_sources={"slack": src})
        result = d._dispatch_slack("hello", {})
        assert result["ok"] is True
        src.send_notification.assert_called_once_with("hello")

    def test_exception_caught_and_returned(self):
        src = MagicMock()
        src.send_notification.side_effect = RuntimeError("network down")
        d = NotificationDispatcher({"enabled": False}, slack_sources={"ops": src})
        result = d._dispatch_slack("hello", {"source": "ops"})
        assert result["ok"] is False
        assert result["backend"] == "slack"
        assert "network down" in str(result["error"])

    def test_source_returns_ok_false(self):
        src = _mock_slack_source({"ok": False, "error": "rate limited"})
        d = NotificationDispatcher({"enabled": False}, slack_sources={"ops": src})
        result = d._dispatch_slack("hello", {"source": "ops"})
        assert result["ok"] is False
        assert "rate limited" in str(result.get("error", ""))


# ---------------------------------------------------------------------------
# webhook backend
# ---------------------------------------------------------------------------


class TestDispatchWebhook:
    def test_no_url_configured(self):
        d = NotificationDispatcher({"enabled": False})
        result = d._dispatch_webhook("hello", {})
        assert result["ok"] is False
        assert result["backend"] == "webhook"
        assert "url" in str(result["error"])

    def test_no_transport(self):
        d = NotificationDispatcher({"enabled": False})
        result = d._dispatch_webhook("hello", {"url": "https://example.com"})
        assert result["ok"] is False
        assert "transport" in str(result["error"])

    def test_successful_post(self):
        transport = _mock_transport(200)
        d = NotificationDispatcher({"enabled": False}, transport=transport)
        result = d._dispatch_webhook("hello", {"url": "https://example.com"})
        assert result["ok"] is True
        assert result["backend"] == "webhook"
        assert result["status_code"] == 200
        transport.post.assert_called_once_with(
            "https://example.com",
            headers={"Content-Type": "application/json"},
            json={"text": "hello"},
            timeout=10.0,
        )

    def test_custom_headers_and_timeout(self):
        transport = _mock_transport(200)
        d = NotificationDispatcher({"enabled": False}, transport=transport)
        result = d._dispatch_webhook(
            "hello",
            {"url": "https://example.com", "headers": {"X-API-Key": "secret"}, "timeout": 5},
        )
        assert result["ok"] is True
        transport.post.assert_called_once_with(
            "https://example.com",
            headers={"Content-Type": "application/json", "X-API-Key": "secret"},
            json={"text": "hello"},
            timeout=5.0,
        )

    def test_http_4xx(self):
        transport = _mock_transport(403)
        d = NotificationDispatcher({"enabled": False}, transport=transport)
        result = d._dispatch_webhook("hello", {"url": "https://example.com"})
        assert result["ok"] is False
        assert result["status_code"] == 403

    def test_http_5xx(self):
        transport = _mock_transport(500)
        d = NotificationDispatcher({"enabled": False}, transport=transport)
        result = d._dispatch_webhook("hello", {"url": "https://example.com"})
        assert result["ok"] is False
        assert result["status_code"] == 500

    def test_transport_exception_caught(self):
        transport = MagicMock()
        transport.post.side_effect = ConnectionError("refused")
        d = NotificationDispatcher({"enabled": False}, transport=transport)
        result = d._dispatch_webhook("hello", {"url": "https://example.com"})
        assert result["ok"] is False
        assert "refused" in str(result["error"])

    def test_no_status_code_attribute(self):
        resp = MagicMock(spec=[])  # no status_code attr
        transport = MagicMock()
        transport.post.return_value = resp
        d = NotificationDispatcher({"enabled": False}, transport=transport)
        result = d._dispatch_webhook("hello", {"url": "https://example.com"})
        assert result["ok"] is False
        assert result.get("status_code") is None


# ---------------------------------------------------------------------------
# dispatch routing (integration of dispatch method)
# ---------------------------------------------------------------------------


class TestDispatchRouting:
    def test_disabled_returns_false(self):
        d = NotificationDispatcher({"enabled": False})
        result = d.dispatch({"id": "t1", "priority": "urgent"})
        assert result["ok"] is False
        assert "disabled" in str(result["reason"])

    def test_below_min_priority(self):
        d = NotificationDispatcher({"enabled": True, "min_priority": "high"})
        result = d.dispatch({"id": "t1", "priority": "low"})
        assert result["ok"] is False
        assert "below min_priority" in str(result["reason"])

    def test_single_stdout_backend(self, capsys):
        d = NotificationDispatcher(_enabled_config(backends={"stdout": {}}))
        result = d.dispatch({"id": "t1", "title": "Test", "priority": "urgent"})
        assert result["ok"] is True
        assert result["results"]["stdout"]["ok"] is True
        captured = capsys.readouterr()
        assert "t1" in captured.out

    def test_multiple_backends_all_succeed(self):
        transport = _mock_transport(200)
        src = _mock_slack_source()
        d = NotificationDispatcher(
            _enabled_config(
                backends={
                    "stdout": {},
                    "slack": {"source": "ops"},
                    "webhook": {"url": "https://example.com"},
                }
            ),
            slack_sources={"ops": src},
            transport=transport,
        )
        result = d.dispatch({"id": "t1", "title": "Test", "priority": "urgent"})
        assert result["ok"] is True
        assert result["results"]["stdout"]["ok"] is True
        assert result["results"]["slack"]["ok"] is True
        assert result["results"]["webhook"]["ok"] is True

    def test_ok_is_true_when_any_backend_succeeds(self):
        transport = _mock_transport(200)
        src = MagicMock()
        src.send_notification.side_effect = RuntimeError("fail")
        d = NotificationDispatcher(
            _enabled_config(
                backends={
                    "slack": {"source": "ops"},
                    "webhook": {"url": "https://example.com"},
                }
            ),
            slack_sources={"ops": src},
            transport=transport,
        )
        result = d.dispatch({"id": "t1", "title": "Test", "priority": "urgent"})
        assert result["ok"] is True  # webhook succeeded
        assert result["results"]["slack"]["ok"] is False
        assert result["results"]["webhook"]["ok"] is True

    def test_ok_is_false_when_all_backends_fail(self):
        src = MagicMock()
        src.send_notification.side_effect = RuntimeError("fail")
        d = NotificationDispatcher(
            _enabled_config(backends={"slack": {"source": "ops"}}),
            slack_sources={"ops": src},
        )
        result = d.dispatch({"id": "t1", "title": "Test", "priority": "urgent"})
        assert result["ok"] is False

    def test_unknown_backend_logged_and_reported(self):
        d = NotificationDispatcher(
            _enabled_config(backends={"stdout": {}, "bogus": {}})
        )
        result = d.dispatch({"id": "t1", "title": "Test", "priority": "urgent"})
        assert "bogus" in result["results"]
        assert result["results"]["bogus"]["ok"] is False
        assert "unknown backend" in str(result["results"]["bogus"]["error"])
        # stdout still succeeded, so overall ok is True
        assert result["ok"] is True

    def test_empty_todo(self):
        d = NotificationDispatcher(_enabled_config())
        result = d.dispatch({})
        assert result["ok"] is True  # minimum fields, stdout works
        assert "stdout" in result["results"]

    def test_todo_missing_priority_uses_medium(self):
        d = NotificationDispatcher(
            {"enabled": True, "backends": {"stdout": {}}, "min_priority": "low"}
        )
        result = d.dispatch({"id": "t1", "title": "Test"})
        assert result["ok"] is True

    def test_dispatch_handler_exception_caught(self):
        d = NotificationDispatcher(_enabled_config(backends={"stdout": {}}))
        original = d._dispatch_stdout

        def _broken(message):
            raise RuntimeError("boom")

        d._dispatch_stdout = _broken
        result = d.dispatch({"id": "t1", "priority": "urgent"})
        assert result["ok"] is False
        assert "boom" in str(result["results"]["stdout"]["error"])
        d._dispatch_stdout = original


# ---------------------------------------------------------------------------
# test method
# ---------------------------------------------------------------------------


class TestTestMethod:
    def test_test_dispatches_with_test_todo(self):
        d = NotificationDispatcher(_enabled_config(backends={"stdout": {}}))
        result = d.test()
        assert "results" in result
        assert result["results"]["stdout"]["ok"] is True

    def test_test_when_disabled(self):
        d = NotificationDispatcher({"enabled": False})
        result = d.test()
        assert result["ok"] is False
        assert "disabled" in str(result["reason"])

    def test_test_uses_urgent_priority(self, capsys):
        d = NotificationDispatcher(_enabled_config(backends={"stdout": {}}))
        d.test()
        captured = capsys.readouterr()
        assert "urgent" in captured.out


# ---------------------------------------------------------------------------
# edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_enabled_default_is_false(self):
        d = NotificationDispatcher({})
        assert d._enabled is False

    def test_enabled_from_fallback(self):
        d = NotificationDispatcher({})
        assert d._enabled == FALLBACK_NOTIFICATION_CONFIG["enabled"]

    def test_dispatch_with_no_backends(self):
        d = NotificationDispatcher({"enabled": True, "backends": {}, "min_priority": "low"})
        result = d.dispatch({"id": "t1", "priority": "urgent"})
        assert result["ok"] is False

    def test_priority_case_sensitivity(self):
        d = NotificationDispatcher({"min_priority": "MEDIUM"})
        assert d._min_priority_val == 2  # unknown -> default 2 (high)

    def test_none_values_in_todo(self):
        d = NotificationDispatcher(_enabled_config(backends={"stdout": {}}))
        result = d.dispatch({"id": "t1", "title": None, "priority": "urgent", "body": None})
        assert result["ok"] is True
        assert result["results"]["stdout"]["ok"] is True

    def test_protocol_check_with_mock_has_post(self):
        transport = _mock_transport(200)
        assert hasattr(transport, "post")
