"""Deep tests for notification and alerting system.

Covers:
  - Webhook delivery with retry backoff
  - Rate limiting (duplicate/scheduled webhook guard)
  - Template rendering (formatting, edge cases, Unicode)
  - Multi-channel routing (all backends in parallel)
  - Delivery audit trail (HookTriggeredEvent publishing)
  - Grafana OnCall alert normalization
  - Budget alert threshold mechanics
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.connectors.grafana_oncall import (
    GrafanaOnCallQuerySpec,
    GrafanaOnCallSource,
    _auth_header,
    _safe_err,
)
from general_ludd.connectors.slack import SlackSource
from general_ludd.controllers.budget_manager import BudgetManager
from general_ludd.events.hooks import (
    HookSystem,
    SSRFBlockedError,
    WebhookConfig,
    _redact_payload,
)
from general_ludd.notifications.dispatcher import (
    NotificationDispatcher,
)

# =============================================================================
# 1. Webhook delivery with retry backoff
# =============================================================================


class TestWebhookRetryBackoff:
    def test_clamped_retry_count_at_registration(self):
        hs = HookSystem()
        hs.register_webhook("retry_evt", "http://example.com", retry_count=9999)
        hook = hs.list_hooks()[0]
        assert hook.webhook_config.retry_count == 5

    def test_retry_count_1_makes_exactly_one_attempt(self):
        attempts = []

        async def counting_post(self, url, **kwargs):
            attempts.append(1)
            raise ConnectionError("fail")

        class AlwaysFailClient:
            post = counting_post

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        hs = HookSystem()
        hs.register_webhook("single_evt", "http://example.com", retry_count=1)
        with patch(
            "general_ludd.events.hooks.httpx.AsyncClient",
            return_value=AlwaysFailClient(),
        ):
            hs.fire("single_evt", {"x": 1})

        assert len(attempts) == 1

    def test_retry_count_3_exhausts_all_attempts(self):
        attempts = []

        async def counting_post(self, url, **kwargs):
            attempts.append(1)
            raise ConnectionError("fail")

        class AlwaysFailClient:
            post = counting_post

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        hs = HookSystem()
        hs.register_webhook("triple_evt", "http://example.com", retry_count=3)
        with patch(
            "general_ludd.events.hooks.httpx.AsyncClient",
            return_value=AlwaysFailClient(),
        ):
            hs.fire("triple_evt", {"y": 2})

        assert len(attempts) == 3

    def test_backoff_success_on_second_attempt(self):
        attempts = []

        async def succeed_on_second(self, url, **kwargs):
            attempts.append(1)
            if len(attempts) < 2:
                raise ConnectionError("first fail")
            return MagicMock(status_code=200, raise_for_status=lambda: None)

        class SecondTimeClient:
            post = succeed_on_second

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        hs = HookSystem()
        hs.register_webhook("success_evt", "http://example.com", retry_count=3)
        with patch(
            "general_ludd.events.hooks.httpx.AsyncClient",
            return_value=SecondTimeClient(),
        ):
            hs.fire("success_evt", {"z": 3})

        assert len(attempts) == 2

    def test_retry_clamp_at_fire_time_too(self):
        """The _fire_webhook method re-clamps retry_count from the config."""
        hs = HookSystem()
        hs.register_webhook("fire_clamp_evt", "http://example.com", retry_count=3)
        hook = hs.list_hooks()[0]
        hook.webhook_config.retry_count = 9999

        attempts = []

        async def counting_post(self, url, **kwargs):
            attempts.append(1)
            raise ConnectionError("fail")

        class FailingClient:
            post = counting_post

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        hs2 = HookSystem()
        hs2._hooks["fire_clamp_evt"] = [hook]
        with patch(
            "general_ludd.events.hooks.httpx.AsyncClient",
            return_value=FailingClient(),
        ):
            hs2.fire("fire_clamp_evt", {"a": 1})

        assert len(attempts) <= 5


# =============================================================================
# 2. Rate limiting — duplicate webhook deduplication
# =============================================================================


class TestWebhookRateLimitDedup:
    def test_duplicate_webhook_not_fired_twice(self):
        """A hook already in _scheduled_webhooks is skipped."""
        attempts = []

        async def record(self, url, **kwargs):
            attempts.append(url)

        class RecordingClient:
            post = record

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        hs = HookSystem()
        hs.register_webhook("dup_evt", "http://example.com/a", retry_count=1)

        with patch(
            "general_ludd.events.hooks.httpx.AsyncClient",
            return_value=RecordingClient(),
        ):
            hs.fire("dup_evt", {"x": 1})
            hs.fire("dup_evt", {"x": 2})

        assert len(attempts) == 1

    def test_different_hooks_same_event_are_both_fired(self):
        attempts = []

        async def record(self, url, **kwargs):
            attempts.append(url)

        class RecordingClient:
            post = record

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        hs = HookSystem()
        hs.register_webhook("multi_evt", "http://example.com/a", retry_count=1)
        hs.register_webhook("multi_evt", "http://example.com/b", retry_count=1)

        with patch(
            "general_ludd.events.hooks.httpx.AsyncClient",
            return_value=RecordingClient(),
        ):
            hs.fire("multi_evt", {"x": 1})

        assert len(attempts) == 2


# =============================================================================
# 3. Template rendering
# =============================================================================


class TestTemplateRendering:
    def test_template_all_fields_rendered(self):
        d = NotificationDispatcher({"enabled": False})
        msg = d._format_message(
            {
                "id": "42",
                "title": "Disk full",
                "priority": "urgent",
                "category": "ops",
                "agent_id": "agent-7",
                "body": "Root partition at 99%",
            }
        )
        assert "[gludd] urgent human-todo #42: Disk full" in msg
        assert "Category: ops" in msg
        assert "Agent: agent-7" in msg
        assert "Root partition at 99%" in msg

    def test_template_renders_with_unicode(self):
        d = NotificationDispatcher({"enabled": False})
        msg = d._format_message(
            {
                "id": "u1",
                "title": "Déployer",
                "priority": "élevée",
                "category": "réseau",
                "agent_id": "agent-alpha",
                "body": "Node 节点 5 down",
            }
        )
        assert "Déployer" in msg
        assert "节点" in msg
        assert "agent-alpha" in msg

    def test_template_renders_with_special_characters(self):
        d = NotificationDispatcher({"enabled": False})
        msg = d._format_message(
            {
                "id": "s1",
                "title": "Error in <script>alert('xss')</script>",
                "priority": "low",
                "category": "security",
                "agent_id": "bot",
                "body": "{json} && || test",
            }
        )
        assert "{json}" in msg
        assert "<script>" in msg

    def test_template_with_long_fields(self):
        d = NotificationDispatcher({"enabled": False})
        long_body = "x" * 10000
        msg = d._format_message(
            {
                "id": "L1",
                "title": "Huge payload",
                "priority": "medium",
                "category": "data",
                "agent_id": "agent-1",
                "body": long_body,
            }
        )
        assert long_body in msg
        assert len(msg) >= 10000

    def test_template_renders_missing_priority_correctly(self):
        d = NotificationDispatcher({"enabled": False})
        msg = d._format_message({"id": "m1", "title": "No priority"})
        assert "medium" in msg

    def test_template_renders_none_fields(self):
        d = NotificationDispatcher({"enabled": False})
        msg = d._format_message(
            {
                "id": None,
                "title": None,
                "priority": None,
                "category": None,
                "agent_id": None,
                "body": None,
            }
        )
        assert isinstance(msg, str)
        assert len(msg) > 0


# =============================================================================
# 4. Multi-channel routing
# =============================================================================


class TestMultiChannelRouting:
    def test_all_three_backends_routed_in_parallel(self):
        transport = MagicMock()
        resp = MagicMock(status_code=200)
        transport.post.return_value = resp
        slack_src = MagicMock()
        slack_src.send_notification.return_value = {"ok": True}

        d = NotificationDispatcher(
            {
                "enabled": True,
                "backends": {
                    "stdout": {},
                    "slack": {"source": "ops"},
                    "webhook": {"url": "https://hooks.example.com/n"},
                },
                "min_priority": "low",
            },
            slack_sources={"ops": slack_src},
            transport=transport,
        )
        result = d.dispatch({"id": "mc1", "title": "Multi", "priority": "urgent"})

        assert result["ok"] is True
        assert "stdout" in result["results"]
        assert "slack" in result["results"]
        assert "webhook" in result["results"]
        assert result["results"]["stdout"]["ok"] is True
        assert result["results"]["slack"]["ok"] is True
        assert result["results"]["webhook"]["ok"] is True
        slack_src.send_notification.assert_called_once()
        transport.post.assert_called_once()

    def test_partial_failure_slack_only(self):
        slack_src = MagicMock()
        slack_src.send_notification.return_value = {"ok": False, "error": "timeout"}

        d = NotificationDispatcher(
            {
                "enabled": True,
                "backends": {
                    "slack": {"source": "ops"},
                    "stdout": {},
                },
                "min_priority": "low",
            },
            slack_sources={"ops": slack_src},
        )
        result = d.dispatch({"id": "pf1", "title": "Partial", "priority": "urgent"})

        assert result["ok"] is True
        assert result["results"]["slack"]["ok"] is False
        assert result["results"]["stdout"]["ok"] is True

    def test_all_fail_returns_ok_false(self):
        slack_src = MagicMock()
        slack_src.send_notification.side_effect = RuntimeError("fail")

        d = NotificationDispatcher(
            {
                "enabled": True,
                "backends": {"slack": {"source": "ops"}},
                "min_priority": "low",
            },
            slack_sources={"ops": slack_src},
        )
        result = d.dispatch({"id": "af1", "title": "All fail", "priority": "urgent"})
        assert result["ok"] is False

    def test_disabled_blocks_all_channels(self):
        d = NotificationDispatcher({"enabled": False})
        result = d.dispatch({"id": "d1", "title": "Disabled", "priority": "urgent"})
        assert result["ok"] is False
        assert "results" not in result

    def test_priority_below_threshold_blocks_all_channels(self):
        d = NotificationDispatcher({"enabled": True, "backends": {"stdout": {}}, "min_priority": "urgent"})
        result = d.dispatch({"id": "p1", "title": "Low pri", "priority": "low"})
        assert result["ok"] is False
        assert "results" not in result

    def test_urgent_always_passes_minimum(self):
        d = NotificationDispatcher({"enabled": True, "backends": {"stdout": {}}, "min_priority": "urgent"})
        result = d.dispatch({"id": "u2", "title": "Urgent", "priority": "urgent"})
        assert result["ok"] is True

    def test_unknown_priority_blocked_by_high_threshold(self):
        d = NotificationDispatcher({"enabled": True, "backends": {"stdout": {}}, "min_priority": "high"})
        result = d.dispatch({"id": "ub1", "title": "Bogus pri", "priority": "bogus"})
        assert result["ok"] is False


# =============================================================================
# 5. Delivery audit trail
# =============================================================================


class TestDeliveryAuditTrail:
    def test_hook_system_fire_returns_success_count(self):
        hs = HookSystem()
        hs.register_webhook("audit_evt", "http://example.com", retry_count=1)

        with patch(
            "general_ludd.events.hooks.httpx.AsyncClient",
            return_value=_make_delivery_client(),
        ):
            count = hs.fire("audit_evt", {"data": "ok"})
        assert count == 1

    def test_event_bus_receives_hook_triggered_on_fire(self):
        bus_mock = MagicMock()
        hs = HookSystem(event_bus=bus_mock)
        hs.register_webhook("bus_evt", "http://example.com", retry_count=1)

        with patch(
            "general_ludd.events.hooks.httpx.AsyncClient",
            return_value=_make_delivery_client(),
        ):
            hs.fire("bus_evt", {"data": "ok"})

        bus_mock.publish.assert_called_once()

    def test_event_bus_published_with_correct_succeeded_count(self):
        bus_mock = MagicMock()
        hs = HookSystem(event_bus=bus_mock)
        hs.register_webhook("count_evt", "http://example.com/a", retry_count=1)
        hs.register_webhook("count_evt", "http://example.com/b", retry_count=1)

        with patch(
            "general_ludd.events.hooks.httpx.AsyncClient",
            return_value=_make_delivery_client(),
        ):
            hs.fire("count_evt", {"data": "ok"})

        call_args = bus_mock.publish.call_args[0][0].payload
        assert call_args.get("succeeded") == 2
        assert call_args.get("failed") == 0

    def test_event_bus_published_with_failed_count(self):
        bus_mock = MagicMock()
        hs = HookSystem(event_bus=bus_mock)

        def failing_callback(_payload):
            raise RuntimeError("callback dropped")

        hs.register_callback("fail_evt", failing_callback)

        hs.fire("fail_evt", {"data": "bad"})

        call_args = bus_mock.publish.call_args[0][0].payload
        assert call_args.get("succeeded") == 0
        assert call_args.get("failed") == 1

    def test_no_event_bus_does_not_error(self):
        hs = HookSystem()
        with patch(
            "general_ludd.events.hooks.httpx.AsyncClient",
            return_value=_make_delivery_client(),
        ):
            count = hs.fire("nobus_evt", {"data": "ok"})
        assert count == 0

    def test_hook_fire_logs_failure_count_but_does_not_raise(self):
        hs = HookSystem()

        def failing_callback(_payload):
            raise RuntimeError("callback exploded")

        hs.register_callback("explode_evt", failing_callback)
        count = hs.fire("explode_evt", {"data": "bad"})
        assert count == 0

    def test_scheduled_webhook_skip_produces_0_success_count(self):
        hs = HookSystem()
        hook_id = hs.register_webhook("skip_evt", "http://example.com", retry_count=1)
        hs._scheduled_webhooks.add(hook_id)

        with patch(
            "general_ludd.events.hooks.httpx.AsyncClient",
            return_value=_make_delivery_client(),
        ):
            count = hs.fire("skip_evt", {"data": "x"})
        assert count == 0


# =============================================================================
# 6. SSRF protection (at registration and fire time)
# =============================================================================


class TestSSRFDeep:
    def test_blocked_url_at_registration_raises(self):
        hs = HookSystem()
        with pytest.raises(SSRFBlockedError):
            hs.register_webhook("bad_evt", "http://127.0.0.1/admin")

    def test_public_url_passable(self):
        hs = HookSystem()
        hook_id = hs.register_webhook("good_evt", "https://hooks.example.com")
        assert hook_id.startswith("hook-wh-")

    def test_empty_url_rejected(self):
        hs = HookSystem()
        with pytest.raises(SSRFBlockedError):
            hs.register_webhook("empty_evt", "")

    def test_disallowed_scheme_rejected(self):
        hs = HookSystem()
        with pytest.raises((SSRFBlockedError, ValueError)):
            hs.register_webhook("file_evt", "file:///etc/passwd")

    def test_link_local_rejected(self):
        hs = HookSystem()
        with pytest.raises(SSRFBlockedError):
            hs.register_webhook("link_evt", "http://169.254.1.1/api")


# =============================================================================
# 7. Redaction depth and edge cases
# =============================================================================


class TestRedactionDeep:
    def test_top_level_redaction_field_set(self):
        result = _redact_payload(
            {
                "api_key": "sk-secret",
                "token": "bearer-xyz",
                "secret": "s3cret",
                "password": "p4ss",
                "credential": "cred",
                "authorization": "Bearer auth",
                "safe_key": "visible",
            }
        )
        assert "api_key" not in result
        assert "token" not in result
        assert "secret" not in result
        assert "password" not in result
        assert "credential" not in result
        assert "authorization" not in result
        assert result["safe_key"] == "visible"

    def test_case_insensitive_redaction(self):
        result = _redact_payload({"API_KEY": "upper", "Token": "mixed", "Secret": "cap"})
        assert "API_KEY" not in result
        assert "Token" not in result
        assert "Secret" not in result

    def test_depth_cap_prevents_infinite_recursion(self):
        d: dict[str, Any] = {"key": "val"}
        for _ in range(20):
            d = {"nested": d, "token": "danger"}
        result = _redact_payload(d)
        assert "token" not in result

    def test_list_elements_redacted(self):
        result = _redact_payload(
            {
                "items": [
                    {"name": "a", "api_key": "k1"},
                    {"name": "b", "token": "t2"},
                ]
            }
        )
        items = result["items"]
        assert "api_key" not in items[0]
        assert items[0]["name"] == "a"
        assert "token" not in items[1]
        assert items[1]["name"] == "b"


# =============================================================================
# 8. Grafana OnCall alert normalization
# =============================================================================


class TestGrafanaOnCallAlertNormalization:
    def test_normalize_alert_group(self):
        src = GrafanaOnCallSource({"base_url": "https://oncall.example.com", "allow_private": True})
        group = {
            "id": "AG-42",
            "state": "acknowledged",
            "title": "High CPU on node-5",
            "created_at": "2026-01-15T10:30:00Z",
            "integration": "Prometheus",
            "team": "SRE",
            "acknowledged_by": "alice",
        }
        record = src._normalize(group)
        assert record["source"] == "grafana_oncall"
        assert record["kind"] == "incidents"
        assert record["message"] == "High CPU on node-5"
        assert record["level_or_status"] == "acknowledged"
        assert record["labels"]["state"] == "acknowledged"
        assert record["labels"]["team"] == "SRE"
        assert record["labels"]["integration"] == "Prometheus"

    def test_query_filters_by_state(self):
        captured_urls = []

        def record(method, url, **kwargs):
            captured_urls.append((url, kwargs.get("params", {})))
            return 200, {
                "results": [{"id": "A1", "state": "firing", "title": "CPU", "created_at": "2026-01-01T00:00:00Z"}]
            }

        src = GrafanaOnCallSource(
            {"base_url": "https://oncall.example.com", "token_env": "FAKE_TOKEN", "allow_private": True},
            transport=record,
        )
        with patch.dict("os.environ", {"FAKE_TOKEN": "test-token"}):
            results = src.query(GrafanaOnCallQuerySpec(state="firing"))
        assert len(results) == 1
        assert results[0]["level_or_status"] == "firing"

    def test_query_no_spec_uses_default_limit(self):
        captured = []

        def record(method, url, **kwargs):
            captured.append(url)
            return 200, {"results": []}

        src = GrafanaOnCallSource(
            {"base_url": "https://oncall.example.com", "token_env": "FAKE_TOKEN", "allow_private": True},
            transport=record,
        )
        with patch.dict("os.environ", {"FAKE_TOKEN": "test-token"}):
            src.query()
        assert "perpage=100" in captured[0]

    def test_health_returns_ok_when_reachable(self):
        def record(method, url, **kwargs):
            return 200, {"results": []}

        src = GrafanaOnCallSource(
            {"base_url": "https://oncall.example.com", "token_env": "FAKE_TOKEN", "allow_private": True},
            transport=record,
        )
        with patch.dict("os.environ", {"FAKE_TOKEN": "test-token"}):
            health = src.health()
        assert health["ok"] is True

    def test_health_returns_not_ok_on_401(self):
        def record(method, url, **kwargs):
            return 401, {"error": "unauthorized"}

        src = GrafanaOnCallSource(
            {"base_url": "https://oncall.example.com", "token_env": "FAKE_TOKEN", "allow_private": True},
            transport=record,
        )
        with patch.dict("os.environ", {"FAKE_TOKEN": "test-token"}):
            health = src.health()
        assert health["ok"] is False

    def test_health_missing_token(self):
        src = GrafanaOnCallSource(
            {"base_url": "https://oncall.example.com", "token_env": "MISSING_TOKEN", "allow_private": True}
        )
        with patch.dict("os.environ", {}, clear=True):
            health = src.health()
        assert health["ok"] is False
        assert "MissingToken" in str(health.get("detail", ""))

    def test_missing_base_url_raises(self):
        with pytest.raises(ValueError, match="base_url"):
            GrafanaOnCallSource({})

    def test_query_extracts_from_results_or_data(self):
        def record(method, url, **kwargs):
            return 200, {
                "data": [{"id": "A2", "state": "resolved", "title": "Mem", "created_at": "2026-01-01T00:00:00Z"}]
            }

        src = GrafanaOnCallSource(
            {"base_url": "https://oncall.example.com", "token_env": "FAKE_TOKEN", "allow_private": True},
            transport=record,
        )
        with patch.dict("os.environ", {"FAKE_TOKEN": "test-token"}):
            results = src.query()
        assert len(results) == 1
        assert results[0]["message"] == "Mem"

    def test_auth_header_uses_raw_token(self):
        header = _auth_header("my-token")
        assert header["Authorization"] == "my-token"

    def test_safe_err_returns_class_name(self):
        assert _safe_err(ValueError("bad stuff")) == "ValueError"


# =============================================================================
# 9. SlackSource notification delivery
# =============================================================================


class TestSlackNotificationDelivery:
    def test_send_via_webhook_returns_ok(self):
        def transport(method, url, **kwargs):
            return 200, {"ok": True}

        src = SlackSource(
            {
                "base_url": "https://slack.com/api",
                "token_env": "SLACK_TOKEN",
                "webhook_url": "https://hooks.slack.com/services/x/y/z",
            },
            transport=transport,
        )
        result = src.send_notification("Hello from gludd")
        assert result["ok"] is True
        assert result["status_code"] == 200

    def test_send_via_webhook_returns_error(self):
        def transport(method, url, **kwargs):
            return 500, "internal error"

        src = SlackSource(
            {
                "base_url": "https://slack.com/api",
                "token_env": "SLACK_TOKEN",
                "webhook_url": "https://hooks.slack.com/services/x/y/z",
            },
            transport=transport,
        )
        result = src.send_notification("Hello")
        assert result["ok"] is False
        assert result.get("status_code") == 500

    def test_send_via_api_returns_ok(self):
        def transport(method, url, **kwargs):
            return 200, {"ok": True}

        env = {"SLACK_TOKEN": "xoxb-test"}
        src = SlackSource(
            {"base_url": "https://slack.com/api", "token_env": "SLACK_TOKEN", "channel_id": "C123"},
            transport=transport,
            env=env,
        )
        result = src.send_notification("Hello via API")
        assert result["ok"] is True

    def test_no_webhook_no_channel_raises(self):
        def transport(method, url, **kwargs):
            return 200, {"ok": True}

        src = SlackSource(
            {"base_url": "https://slack.com/api", "token_env": "SLACK_TOKEN"},
            transport=transport,
        )
        with pytest.raises(ValueError, match="webhook_url or channel_id"):
            src.send_notification("nope")

    def test_webhook_transport_exception_fail_soft(self):
        def transport(method, url, **kwargs):
            raise ConnectionError("refused")

        src = SlackSource(
            {
                "base_url": "https://slack.com/api",
                "token_env": "SLACK_TOKEN",
                "webhook_url": "https://hooks.slack.com/services/x/y/z",
            },
            transport=transport,
        )
        result = src.send_notification("fail test")
        assert result["ok"] is False
        assert "error" in result


# =============================================================================
# 10. Budget manager alert threshold
# =============================================================================


class TestBudgetAlertThreshold:
    def test_alert_fires_above_threshold(self):
        bm = BudgetManager(daily_limit_usd=100.0, alert_threshold_pct=50.0)
        assert bm._alert_pct == 50.0
        result = bm.check_daily_budget(60.0)
        assert result["allowed"] is True

    def test_alert_does_not_fire_below_threshold(self):
        bm = BudgetManager(daily_limit_usd=100.0, alert_threshold_pct=80.0)
        result = bm.check_daily_budget(40.0)
        assert result["allowed"] is True

    def test_alert_at_exact_threshold(self):
        bm = BudgetManager(daily_limit_usd=100.0, alert_threshold_pct=80.0)
        result = bm.check_daily_budget(80.0)
        assert result["allowed"] is True

    def test_alert_above_threshold(self):
        bm = BudgetManager(daily_limit_usd=100.0, alert_threshold_pct=80.0)
        result = bm.check_daily_budget(85.0)
        assert result["allowed"] is True

    def test_alert_with_custom_threshold(self):
        bm = BudgetManager(daily_limit_usd=100.0, alert_threshold_pct=60.0)
        result = bm.check_daily_budget(70.0)
        assert result["allowed"] is True

    def test_alert_threshold_default_is_80(self):
        bm = BudgetManager(daily_limit_usd=100.0)
        assert bm._alert_pct == 80.0

    def test_alert_limit_exceeded_pauses(self):
        bm = BudgetManager(daily_limit_usd=100.0, alert_threshold_pct=50.0)
        r1 = bm.check_daily_budget(60.0)
        assert r1["allowed"] is True
        r2 = bm.check_daily_budget(50.0)
        assert r2["allowed"] is False

    def test_alert_unlimited_daily_no_breach(self):
        bm = BudgetManager(daily_limit_usd=float("inf"), alert_threshold_pct=80.0)
        result = bm.check_daily_budget(999_999.0)
        assert result["allowed"] is True


# =============================================================================
# 11. WebhookConfig invariants
# =============================================================================


class TestWebhookConfigInvariants:
    def test_config_stores_all_fields(self):
        cfg = WebhookConfig(
            url="https://example.com/hook",
            headers={"X-Custom": "val"},
            retry_count=2,
            timeout_seconds=15,
        )
        assert cfg.url == "https://example.com/hook"
        assert cfg.headers == {"X-Custom": "val"}
        assert cfg.retry_count == 2
        assert cfg.timeout_seconds == 15

    def test_config_default_retry_is_1(self):
        cfg = WebhookConfig(url="https://example.com")
        assert cfg.retry_count == 1

    def test_config_default_timeout_is_10(self):
        cfg = WebhookConfig(url="https://example.com")
        assert cfg.timeout_seconds == 10

    def test_headers_are_not_in_repr(self):
        cfg = WebhookConfig(url="https://example.com", headers={"Authorization": "secret"})
        r = repr(cfg)
        assert "Authorization" not in r


# =============================================================================
# helpers
# =============================================================================


def _make_delivery_client():
    async def _post(self, url, **kwargs):
        class Resp:
            status_code = 200

            def raise_for_status(self):
                pass

        return Resp()

    class _Client:
        post = _post

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    return _Client()
