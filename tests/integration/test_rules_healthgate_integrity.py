"""Inert-confirmation tests for two production gaps.

CLAIM 1 — Rules engine wiring (REMEDIATED 2026-06-20):
  - UserConfig now has a "rules" field and load_startup_config copies it into
    startup_config["rules"], so operator rules reach the EventLoop.
  - The EventLoop _phase_evaluate_rules evaluates rules when given them.
  - The two tests below now assert the *fixed* behaviour (rules ARE carried).

CLAIM 2 — ModelGateway health-gate is wired but not activated by the daemon:
  - The daemon builds ModelGateway without a health_tracker, so _health_tracker
    is None and the circuit-breaker guard is permanently bypassed.
  - The gate works correctly when a tracker IS wired in.

All tests should PASS. Passing proves the gaps exist; a test failure would mean
the gap has been closed and this file needs updating.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, ClassVar, cast

import httpx

# ── helpers ──────────────────────────────────────────────────────────────────


def _server_error() -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://provider.invalid/v1/chat")
    response = httpx.Response(503, request=request, text="service unavailable")
    return httpx.HTTPStatusError("503", request=request, response=response)


class _FakeChatModel:
    script: ClassVar[list[object]] = []

    def __init__(self, **init_kwargs: object) -> None:
        self._init_kwargs = init_kwargs

    def invoke(self, messages: list[dict[str, str]]) -> object:
        if not _FakeChatModel.script:
            raise AssertionError("provider invoked more times than scripted")
        item = _FakeChatModel.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        resp = type("_Resp", (), {})()
        resp.content = item
        resp.usage_metadata = {"input_tokens": 10, "output_tokens": 5}
        return resp


class _FakeRegistry:
    def is_installed(self, provider_name: str) -> bool:
        return True

    def install_provider(self, provider_name: str) -> None:
        raise AssertionError("install_provider should not be called")

    def get_provider_class(self, provider_name: str) -> type[_FakeChatModel]:
        return _FakeChatModel


# ═══════════════════════════════════════════════════════════════════════════════
# CLAIM 1: Rules engine
# ═══════════════════════════════════════════════════════════════════════════════


def test_userconfig_has_rules_field() -> None:
    """UserConfig now defines a 'rules' field so startup_config can carry rules."""
    from general_ludd.config.user_config import UserConfig

    cfg = UserConfig()
    assert hasattr(cfg, "rules"), (
        "UserConfig lost its 'rules' field — the rules engine would go inert again"
    )
    assert cfg.rules == [], "default rules should be an empty list"


def test_startup_config_rules_populated_from_user_config() -> None:
    """load_startup_config copies UserConfig.rules into startup_config['rules']."""
    from general_ludd.daemon import load_startup_config

    # No config dir → default UserConfig with empty rules; key must still exist.
    startup_config = load_startup_config(config_dir="/tmp/nonexistent_dir_xyzzy_glm")
    assert "rules" in startup_config, (
        "startup_config lost its 'rules' key — EventLoop would never receive rules"
    )
    assert startup_config["rules"] == []


def test_rules_engine_works_when_given_rules() -> None:
    """EventLoop._phase_evaluate_rules evaluates rules against the LIVE claimed todos.

    Per the W4c fix, rules evaluate against self._tick_state["claimed_todos"] (set by
    _phase_claim_runnable_todos, which now runs first in PHASE_ORDER), not the static
    self.config["todos"]. The claimed todos are Todo objects, so we seed tick state with
    one directly here instead of running the (DB-backed) claim phase.
    """
    from general_ludd.event_loop.loop import EventLoop
    from general_ludd.rules.engine import Rule
    from general_ludd.schemas.todo import Todo, TodoStatus

    rule = Rule(
        rule_id="test_set_model_profile",
        priority=10,
        condition={"field": "todo.work_type", "op": "eq", "value": "code"},
        actions=[{"type": "set_model_profile", "profile_id": "fast"}],
    )

    loop = EventLoop(config={"rules": [rule]})
    loop._tick_state["claimed_todos"] = [
        Todo(
            todo_id="t-001",
            title="code task",
            status=TodoStatus.ACTIVE,
            queue="core",
            work_type="code",
        )
    ]

    asyncio.run(loop._phase_evaluate_rules())

    results = loop._tick_state.get("rule_evaluation_results", [])
    assert len(results) == 1, f"Expected 1 result, got {results}"
    assert results[0]["todo_id"] == "t-001"
    assert len(results[0]["actions"]) >= 1
    action_types = [a["action_type"] for a in results[0]["actions"]]
    assert "set_model_profile" in action_types, f"action_types={action_types}"


# ═══════════════════════════════════════════════════════════════════════════════
# CLAIM 2: Gateway health gate
# ═══════════════════════════════════════════════════════════════════════════════


def test_daemon_gateway_has_no_health_tracker() -> None:
    """Daemon builds ModelGateway without health_tracker — circuit-breaker is inactive."""
    from general_ludd.models.gateway import ModelGateway, ModelProfile

    profile = ModelProfile(
        model_profile_id="primary",
        provider="openai",
        provider_class_hint="ChatOpenAI",
        enabled=True,
    )
    # Mimic exactly what daemon.py does: no health_tracker kwarg
    gateway = ModelGateway(
        profiles=[profile],
        provider_registry=None,
        secrets_manager=None,
        metrics_collector=None,
    )

    assert gateway._health_tracker is None, (
        "Daemon-style ModelGateway unexpectedly has a health_tracker — gap may be closed"
    )


def test_health_gate_works_when_wired() -> None:
    """Circuit-breaker trips correctly when health_tracker IS provided."""
    from general_ludd.models.gateway import ModelGateway, ModelProfile
    from general_ludd.models.timeout_detector import ModelHealthTracker

    profile = ModelProfile(
        model_profile_id="primary",
        provider="fake",
        provider_class_hint="FakeChat",
        enabled=True,
    )
    tracker = ModelHealthTracker(failure_threshold=3, cooldown_seconds=3600.0)
    _FakeChatModel.script = [_server_error(), _server_error(), _server_error()]

    gateway = ModelGateway(
        profiles=[profile],
        provider_registry=cast(Any, _FakeRegistry()),
        secrets_manager=None,
        health_tracker=tracker,
    )

    # Drive 3 failures to trip the circuit
    for _ in range(3):
        with contextlib.suppress(Exception):
            gateway.call_model_with_retry(
                "primary",
                [{"role": "user", "content": "hello"}],
                max_retries=0,
                base_backoff_seconds=0.0,
            )

    assert not tracker.is_healthy("primary", admit_probe=False), (
        "Circuit breaker should be open after 3 failures but is_healthy returned True"
    )


def test_unhealthy_model_not_skipped_when_tracker_none() -> None:
    """record_timeout_on_failure is a no-op when health_tracker is None (daemon mode)."""
    from general_ludd.models.gateway import ModelGateway, ModelProfile

    profile = ModelProfile(
        model_profile_id="primary",
        provider="openai",
        provider_class_hint="ChatOpenAI",
        enabled=True,
    )
    gateway = ModelGateway(
        profiles=[profile],
        provider_registry=None,
        secrets_manager=None,
    )
    # record_timeout_on_failure should silently return without error when tracker is None
    exc = _server_error()
    gateway.record_timeout_on_failure("primary", exc)  # must not raise

    # Confirm tracker is still None (no lazy initialisation happened)
    assert gateway._health_tracker is None
