"""Structural tests for notifications/dispatcher.py — multi-backend notification dispatch."""

from __future__ import annotations

import logging


class TestModuleImports:
    def test_module_can_be_imported(self):
        import general_ludd.notifications.dispatcher

        assert general_ludd.notifications.dispatcher is not None

    def test_logger_exists(self):
        from general_ludd.notifications.dispatcher import logger

        assert isinstance(logger, logging.Logger)
        assert logger.name == "general_ludd.notifications.dispatcher"


class TestConstants:
    def test_backend_names(self):
        from general_ludd.notifications.dispatcher import BACKEND_NAMES

        assert isinstance(BACKEND_NAMES, frozenset)
        assert "slack" in BACKEND_NAMES
        assert "stdout" in BACKEND_NAMES
        assert "webhook" in BACKEND_NAMES

    def test_priority_levels(self):
        from general_ludd.notifications.dispatcher import PRIORITY_LEVELS

        assert isinstance(PRIORITY_LEVELS, dict)
        assert PRIORITY_LEVELS["low"] == 0
        assert PRIORITY_LEVELS["medium"] == 1
        assert PRIORITY_LEVELS["high"] == 2
        assert PRIORITY_LEVELS["urgent"] == 3

    def test_fallback_notification_config(self):
        from general_ludd.notifications.dispatcher import FALLBACK_NOTIFICATION_CONFIG

        assert isinstance(FALLBACK_NOTIFICATION_CONFIG, dict)
        assert "enabled" in FALLBACK_NOTIFICATION_CONFIG
        assert "backends" in FALLBACK_NOTIFICATION_CONFIG
        assert "min_priority" in FALLBACK_NOTIFICATION_CONFIG

    def test_notification_template_contains_placeholders(self):
        from general_ludd.notifications.dispatcher import NOTIFICATION_TEMPLATE

        assert "{id}" in NOTIFICATION_TEMPLATE
        assert "{title}" in NOTIFICATION_TEMPLATE
        assert "{priority}" in NOTIFICATION_TEMPLATE
        assert "{category}" in NOTIFICATION_TEMPLATE


class TestHttpTransport:
    def test_is_runtime_checkable_protocol(self):

        from general_ludd.notifications.dispatcher import HttpTransport

        assert hasattr(HttpTransport, "__protocol_attrs__") or callable(HttpTransport)


class TestNotificationDispatcher:
    def test_constructor_defaults(self):
        from general_ludd.notifications.dispatcher import NotificationDispatcher

        d = NotificationDispatcher({})
        assert d._enabled is False

    def test_constructor_with_enabled(self):
        from general_ludd.notifications.dispatcher import NotificationDispatcher

        d = NotificationDispatcher({"enabled": True})
        assert d._enabled is True

    def test_constructor_with_min_priority(self):
        from general_ludd.notifications.dispatcher import NotificationDispatcher

        d = NotificationDispatcher({"min_priority": "low"})
        assert d._min_priority == "low"
        assert d._min_priority_val == 0

    def test_format_message_includes_id(self):
        from general_ludd.notifications.dispatcher import NotificationDispatcher

        d = NotificationDispatcher({"enabled": False})
        msg = d._format_message({"id": "todo-1", "title": "Fix bug"})
        assert "todo-1" in msg
        assert "Fix bug" in msg

    def test_format_message_defaults_missing_fields(self):
        from general_ludd.notifications.dispatcher import NotificationDispatcher

        d = NotificationDispatcher({"enabled": False})
        msg = d._format_message({})
        assert "?" in msg
        assert "untitled" in msg

    def test_priority_meets_threshold(self):
        from general_ludd.notifications.dispatcher import NotificationDispatcher

        d = NotificationDispatcher({"min_priority": "medium"})
        assert d._priority_meets_threshold("high") is True
        assert d._priority_meets_threshold("low") is False

    def test_dispatch_stdout_returns_ok(self):
        from general_ludd.notifications.dispatcher import NotificationDispatcher

        d = NotificationDispatcher({"enabled": False})
        result = d._dispatch_stdout("hello")
        assert result == {"ok": True, "backend": "stdout"}

    def test_dispatch_webhook_no_url(self):
        from general_ludd.notifications.dispatcher import NotificationDispatcher

        d = NotificationDispatcher({"enabled": False})
        result = d._dispatch_webhook("hello", {})
        assert result["ok"] is False
        assert "url" in str(result["error"])

    def test_dispatch_webhook_no_transport(self):
        from general_ludd.notifications.dispatcher import NotificationDispatcher

        d = NotificationDispatcher({"enabled": False})
        result = d._dispatch_webhook("hello", {"url": "https://example.com"})
        assert result["ok"] is False
        assert "transport" in str(result["error"])

    def test_dispatch_disabled_returns_false(self):
        from general_ludd.notifications.dispatcher import NotificationDispatcher

        d = NotificationDispatcher({"enabled": False})
        result = d.dispatch({"id": "t1", "priority": "urgent"})
        assert result["ok"] is False
        assert "disabled" in str(result["reason"])

    def test_dispatch_below_threshold(self):
        from general_ludd.notifications.dispatcher import NotificationDispatcher

        d = NotificationDispatcher({"enabled": True, "min_priority": "high"})
        result = d.dispatch({"id": "t1", "priority": "low"})
        assert result["ok"] is False
        assert "below min_priority" in str(result["reason"])

    def test_dispatch_with_stdout_enabled(self):
        from general_ludd.notifications.dispatcher import NotificationDispatcher

        d = NotificationDispatcher(
            {"enabled": True, "backends": {"stdout": {}}, "min_priority": "low"}
        )
        result = d.dispatch({"id": "t1", "title": "Test", "priority": "urgent"})
        assert "results" in result
        assert "stdout" in result["results"]

    def test_test_method_exists(self):
        from general_ludd.notifications.dispatcher import NotificationDispatcher

        d = NotificationDispatcher({"enabled": True, "backends": {"stdout": {}}})
        assert callable(d.test)

    def test_dispatch_slack_no_source(self):
        from general_ludd.notifications.dispatcher import NotificationDispatcher

        d = NotificationDispatcher({"enabled": False}, slack_sources={})
        result = d._dispatch_slack("hello", {})
        assert result["ok"] is False
        assert "not found" in str(result["error"])
