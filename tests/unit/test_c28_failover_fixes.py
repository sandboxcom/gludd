"""C28 — Failover follow-ups.

Tests for four P1 defects in gateway.py and failover.py:
  (a) All-down error path unused — bare CircuitBreakerOpenError without
      _enrich_all_down_message routes.
  (b) Semaphore no-timeout — blocking acquire can hang a thread-pool worker.
  (c) Transitive cascade unbounded — no depth cap on _walk_fallbacks hops.
  (d) record_failover un-locked list append — race on concurrent appends.
"""

from __future__ import annotations

import concurrent.futures
import threading
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.models.failover import ModelFailoverChain
from general_ludd.models.gateway import (
    CircuitBreakerOpenError,
    ModelGateway,
    ModelProfile,
    _enrich_all_down_message,
)
from general_ludd.models.provider_registry import ProviderRegistry
from general_ludd.secrets.manager import SecretAlias, SecretsManager


def _make_profile(
    pid: str,
    fallback: list[str] | None = None,
    budget: float = 200.0,
    enabled: bool = True,
) -> ModelProfile:
    return ModelProfile(
        model_profile_id=pid,
        enabled=enabled,
        provider="openai",
        provider_package="langchain-openai",
        provider_class_hint="ChatOpenAI",
        model_name=f"model-{pid}",
        credential_alias="openai_key",
        run_budget_usd=budget,
        cost_per_input_token=0.000001,
        cost_per_output_token=0.000002,
        fallback_profiles=fallback or [],
    )


def _make_gateway(
    profiles: list[ModelProfile],
) -> tuple[ModelGateway, ProviderRegistry]:
    reg = ProviderRegistry()
    reg.register_provider("openai", "langchain-openai", "ChatOpenAI")

    fake_secret_client = MagicMock()
    fake_secret_client.secrets.kv.v2.read_secret_version.return_value = {
        "data": {"data": {"value": "sk-test"}}
    }
    secrets = SecretsManager(
        client=fake_secret_client,
        aliases={"openai_key": SecretAlias("openai_key", "keys/openai", "secret")},
    )

    gw = ModelGateway(
        profiles=profiles,
        provider_registry=reg,
        secrets_manager=secrets,
    )
    return gw, reg


def _fake_response(content: str) -> MagicMock:
    return MagicMock(content=content, usage_metadata={})


# ---------------------------------------------------------------------------
# (a) All-down error path unused
# ---------------------------------------------------------------------------

class TestAllDownRaisesEnrichedError:
    def test_all_down_raises_enriched_error(self):
        """When all providers fail, the raised error carries the multi-attempt
        summary from _enrich_all_down_message — NOT a bare CircuitBreakerOpenError."""
        primary = _make_profile("pri_all_down", fallback=["fb1", "fb2"])
        fb1 = _make_profile("fb1", fallback=["fb3"])
        fb2 = _make_profile("fb2")
        fb3 = _make_profile("fb3")
        gw, reg = _make_gateway([primary, fb1, fb2, fb3])

        call_model_errors = {
            "pri_all_down": RuntimeError("primary timeout"),
            "fb1": RuntimeError("fb1 overloaded"),
            "fb2": RuntimeError("fb2 capacity"),
            "fb3": RuntimeError("fb3 down"),
        }

        original_call_model = gw.call_model

        def failing_call_model(profile_id, messages, **kwargs):
            if profile_id in call_model_errors:
                raise call_model_errors[profile_id]
            return original_call_model(profile_id, messages, **kwargs)

        FakeChatModel = MagicMock()
        FakeChatModel.return_value.invoke.side_effect = RuntimeError("should not reach")

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=FakeChatModel),
            patch.object(gw, "call_model", side_effect=failing_call_model),
            pytest.raises(CircuitBreakerOpenError) as exc_info,
        ):
            gw.call_model_with_fallback(
                "pri_all_down",
                [{"role": "user", "content": "hi"}],
            )

        message = str(exc_info.value)
        assert "all providers down" in message
        assert "pri_all_down" in message
        assert "fb1" in message
        assert "fb2" in message
        assert "fb3" in message


# ---------------------------------------------------------------------------
# (b) Semaphore acquire has timeout
# ---------------------------------------------------------------------------

class TestSemaphoreAcquireHasTimeout:
    def test_semaphore_acquire_has_timeout(self):
        """_call_fallback wraps the semaphore acquire in a bounded wait.
        A hung secondary provider must not hold a slot indefinitely."""
        primary = _make_profile("pri_sem", fallback=["fb_sem"])
        fb_sem = _make_profile("fb_sem")
        gw, reg = _make_gateway([primary, fb_sem])

        FakeChatModel = MagicMock()
        fake_instance = MagicMock()
        fake_instance.invoke.return_value = _fake_response("ok")
        FakeChatModel.return_value = fake_instance

        real_sem = threading.Semaphore(0)
        gw._fallback_semaphores["fb_sem"] = real_sem

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=FakeChatModel),
            patch.object(real_sem, "acquire", return_value=False),
            pytest.raises(RuntimeError, match="fallback capacity"),
        ):
            gw._call_fallback("fb_sem", [{"role": "user", "content": "hi"}])


# ---------------------------------------------------------------------------
# (c) Transitive cascade depth capped
# ---------------------------------------------------------------------------

class TestTransitiveCascadeDepthCapped:
    def test_transitive_cascade_depth_capped(self):
        """After max_fallback_depth hops, the cascade stops even if more
        fallback_profiles exist along the chain."""
        a = _make_profile("cascade_a", fallback=["cascade_b"])
        b = _make_profile("cascade_b", fallback=["cascade_c"])
        c = _make_profile("cascade_c", fallback=["cascade_d"])
        d = _make_profile("cascade_d", fallback=["cascade_e"])
        e = _make_profile("cascade_e")
        gw, reg = _make_gateway([a, b, c, d, e])

        gw._max_fallback_depth = 2

        attempted: set[str] = set()

        def failing_all(profile_id, messages, **kwargs):
            attempted.add(profile_id)
            raise RuntimeError(f"{profile_id} error")

        FakeChatModel = MagicMock()
        FakeChatModel.return_value.invoke.side_effect = RuntimeError("unreachable")

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=FakeChatModel),
            patch.object(gw, "call_model", side_effect=failing_all),
            pytest.raises(CircuitBreakerOpenError),
        ):
            gw.call_model_with_fallback(
                "cascade_a",
                [{"role": "user", "content": "hi"}],
            )

        assert "cascade_a" in attempted
        assert "cascade_b" in attempted
        assert "cascade_c" in attempted
        assert "cascade_d" not in attempted
        assert "cascade_e" not in attempted


# ---------------------------------------------------------------------------
# (d) record_failover is thread-safe
# ---------------------------------------------------------------------------

class TestRecordFailoverIsThreadSafe:
    def test_record_failover_is_thread_safe(self):
        """Concurrent appends to _failover_events must not race."""
        chain = ModelFailoverChain(
            primary_profile="primary",
            fallback_profiles=["fb1", "fb2"],
        )

        errors = [f"error_{i}" for i in range(200)]

        def record_one(idx: int) -> None:
            chain.record_failover("from", "to", errors[idx])

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            for i in range(200):
                executor.submit(record_one, i)

        events = chain.get_failover_events()
        assert len(events) == 200, f"Expected 200 events, got {len(events)}"


# ---------------------------------------------------------------------------
# Edge case — _enrich_all_down_message does not crash on empty attempts
# ---------------------------------------------------------------------------

class TestEnrichAllDownMessageEdgeCases:
    def test_enrich_empty_attempts_is_noop(self):
        exc = RuntimeError("original")
        _enrich_all_down_message(exc, [])
        assert str(exc) == "original"

    def test_enrich_with_attempts_modifies_message(self):
        exc = RuntimeError("some error")
        attempts = [
            {"profile_id": "p1", "reason": "timeout"},
            {"profile_id": "p2", "reason": "overloaded"},
        ]
        _enrich_all_down_message(exc, attempts)
        enriched = str(exc)
        assert "all providers down" in enriched
        assert "p1" in enriched
        assert "p2" in enriched
        assert "timeout" in enriched
        assert "overloaded" in enriched
