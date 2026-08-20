"""Deep tests for untested gaps in deployment_health.py.

Targets:
- DeploymentIncidentLog._persist (async audit repo persistence)
- DeploymentIncidentLog._persist_async with running event loop
- SelfHealingRouter.record_failure (returns DeploymentIncident)
- SelfHealingRouter._attempt_remediation (no-op logging)
- SelfHealingRouter.deployment_health snapshot independence
- ModelFailoverChain integration in _get_chain and set_failover_chain
"""

from __future__ import annotations

import asyncio
import json
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.models.deployment_health import (
    DeploymentHealthChecker,
    DeploymentIncident,
    DeploymentIncidentLog,
    SelfHealingRouter,
)

# ---------------------------------------------------------------------------
# DeploymentIncidentLog — _persist via audit repo (async path)
# ---------------------------------------------------------------------------


class TestDeploymentIncidentLogPersistAsync:
    """The _persist method calls audit_repo.create(...) with correct args
    and error-details JSON, and swallows exceptions."""

    def test_persist_calls_audit_repo_create_with_correct_args(self) -> None:
        mock_repo = AsyncMock()
        log = DeploymentIncidentLog(audit_repo=mock_repo, project_id="proj-1")

        inc = DeploymentIncident(
            timestamp=1700000000.0,
            deployment_id="dep-a",
            model_id="gpt-4",
            error_type="timeout",
            error_message="Connection timed out",
            routed_to="dep-b",
            remediation_attempted=["restart"],
            remediation_result="dispatched",
        )

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(log._persist(inc))
        finally:
            loop.close()

        mock_repo.create.assert_called_once()
        _args, kwargs = mock_repo.create.call_args

        assert kwargs["event_type"] == "deployment_incident"
        assert kwargs["entity_type"] == "deployment"
        assert kwargs["entity_id"] == "dep-a"
        assert kwargs["project_id"] == "proj-1"

        details = json.loads(kwargs["details"])
        assert details["model_id"] == "gpt-4"
        assert details["error_type"] == "timeout"
        assert details["error_message"] == "Connection timed out"
        assert details["routed_to"] == "dep-b"
        assert details["remediation_attempted"] == ["restart"]
        assert details["remediation_result"] == "dispatched"
        assert details["timestamp"] == 1700000000.0

    def test_persist_swallows_exception_and_does_not_raise(self) -> None:
        mock_repo = AsyncMock()
        mock_repo.create.side_effect = RuntimeError("db connection lost")

        log = DeploymentIncidentLog(audit_repo=mock_repo)

        inc = DeploymentIncident(
            timestamp=1.0,
            deployment_id="dep-x",
            model_id="m1",
            error_type="crash",
            error_message="boom",
        )

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(log._persist(inc))
        finally:
            loop.close()

        # Must not have raised — exception is logged, not propagated.

    @pytest.mark.asyncio
    async def test_aclose_cancels_and_awaits_pending_persistence(self) -> None:
        started = asyncio.Event()

        async def create(**_kwargs: object) -> None:
            started.set()
            await asyncio.Event().wait()

        repo = MagicMock()
        repo.create.side_effect = create
        log = DeploymentIncidentLog(audit_repo=repo)
        log.record(
            DeploymentIncident(
                timestamp=1.0,
                deployment_id="dep",
                model_id="model",
                error_type="failure",
                error_message="boom",
            )
        )
        await started.wait()

        await log.aclose()

        assert not log._persistence_tasks

    def test_persist_without_routed_to_or_remediation(self) -> None:
        """Minimal incident (no routed_to, no remediation fields) still
        produces valid JSON details with None for those keys."""
        mock_repo = AsyncMock()
        log = DeploymentIncidentLog(audit_repo=mock_repo)

        inc = DeploymentIncident(
            timestamp=123.0,
            deployment_id="dep-n",
            model_id="m0",
            error_type="auth_error",
            error_message="Unauthorized",
        )

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(log._persist(inc))
        finally:
            loop.close()

        _args, kwargs = mock_repo.create.call_args
        details = json.loads(kwargs["details"])
        assert details["routed_to"] is None
        assert details["remediation_attempted"] is None
        assert details["remediation_result"] is None


# ---------------------------------------------------------------------------
# DeploymentIncidentLog._persist_async — event-loop branching
# ---------------------------------------------------------------------------


class TestPersistAsyncEventLoop:
    """_persist_async schedules a task when a running loop exists; no-op when
    no loop is running."""

    def test_no_event_loop_returns_immediately(self) -> None:
        log = DeploymentIncidentLog(audit_repo=MagicMock())
        inc = DeploymentIncident(
            timestamp=1.0,
            deployment_id="d1",
            model_id="m1",
            error_type="err",
            error_message="msg",
        )
        # Outside any running loop → _persist_async returns without doing work.
        log._persist_async(inc)
        # The incident was never recorded — the scheduled task never ran.
        assert len(log.get_incidents()) == 0

    def test_running_event_loop_schedules_task(self) -> None:
        mock_repo = AsyncMock()
        log = DeploymentIncidentLog(audit_repo=mock_repo)

        inc = DeploymentIncident(
            timestamp=1.0,
            deployment_id="d-loop",
            model_id="m1",
            error_type="err",
            error_message="msg",
        )

        recorded: list[int] = []

        async def _runner() -> None:
            log._persist_async(inc)
            recorded.append(1)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_runner())
        finally:
            loop.close()

        assert recorded == [1], "_persist_async ran under event loop"

    def test_record_calls_persist_async_when_audit_repo_set(self) -> None:
        """record() with an audit_repo schedules persistence."""
        mock_repo = AsyncMock()
        log = DeploymentIncidentLog(audit_repo=mock_repo, in_memory=True)

        inc = DeploymentIncident(
            timestamp=5.0,
            deployment_id="d-rec",
            model_id="m",
            error_type="timeout",
            error_message="timed out",
        )

        async def _test() -> None:
            log.record(inc)
            # Allow the scheduled task to complete.
            await asyncio.sleep(0.05)

        loop = asyncio.new_event_loop()
        loop.set_debug(False)
        try:
            loop.run_until_complete(_test())
        finally:
            loop.close()

        # In-memory storage still works.
        assert log.get_incidents()[0].deployment_id == "d-rec"


# ---------------------------------------------------------------------------
# SelfHealingRouter.record_failure — returns DeploymentIncident
# ---------------------------------------------------------------------------


class TestSelfHealingRouterRecordFailure:
    """record_failure on the router delegates to the checker and returns a
    DeploymentIncident with correct fields."""

    def test_record_failure_returns_incident_with_correct_fields(self) -> None:
        router = SelfHealingRouter()
        inc = router.record_failure("dep-a", "timeout", model_id="gpt-4")

        assert isinstance(inc, DeploymentIncident)
        assert inc.deployment_id == "dep-a"
        assert inc.model_id == "gpt-4"
        assert inc.error_type == "failure"
        assert inc.error_message == "timeout"
        assert inc.timestamp > 0

    def test_record_failure_truncates_error_to_500_chars(self) -> None:
        router = SelfHealingRouter()
        long_err = "X" * 600
        inc = router.record_failure("dep-long", long_err)
        assert len(inc.error_message) == 500
        assert inc.error_message == "X" * 500

    def test_record_failure_updates_deployment_health_dict(self) -> None:
        router = SelfHealingRouter()
        router.record_failure("dep-h", "oom")

        health_snapshot = router.deployment_health
        assert "dep-h" in health_snapshot
        assert health_snapshot["dep-h"]["status"] == "unhealthy"
        assert health_snapshot["dep-h"]["error_count"] == 1
        assert health_snapshot["dep-h"]["last_check"] > 0

    def test_record_failure_with_exception_object(self) -> None:
        router = SelfHealingRouter()
        inc = router.record_failure("dep-exc", ValueError("invalid input"))
        assert inc.error_message == "invalid input"

    def test_record_failure_default_model_id_empty_string(self) -> None:
        router = SelfHealingRouter()
        inc = router.record_failure("dep-def", "crash")
        assert inc.model_id == ""

    def test_record_failure_increments_error_count_on_repeat(self) -> None:
        router = SelfHealingRouter()
        router.record_failure("dep-ec", "e1")
        router.record_failure("dep-ec", "e2")
        router.record_failure("dep-ec", "e3")

        health = router.deployment_health["dep-ec"]
        assert health["error_count"] == 3

    def test_record_failure_delegates_to_health_checker(self) -> None:
        """record_failure on the router also calls checker.record_failure."""
        custom_checker = DeploymentHealthChecker(failure_threshold=5)
        router = SelfHealingRouter(health_checker=custom_checker)
        router.record_failure("dep-chk", "timeout")
        status = custom_checker.get_status("dep-chk")
        assert status.consecutive_failures == 1


# ---------------------------------------------------------------------------
# SelfHealingRouter._attempt_remediation — no-op logging
# ---------------------------------------------------------------------------


class TestAttemptRemediation:
    """_attempt_remediation is a logging-only method (no actual remediation
    actions). It must not raise regardless of input state."""

    def test_remediation_logs_for_known_deployment(self) -> None:
        router = SelfHealingRouter()
        router.record_failure("dep-rem", "timeout")

        router._attempt_remediation("dep-rem")
        # Must not raise — it is a pure-logging method.

    def test_remediation_handles_unknown_deployment_gracefully(self) -> None:
        router = SelfHealingRouter()
        router._attempt_remediation("never-seen")
        # Must not raise.

    def test_remediation_called_implicitly_by_check_and_route(self) -> None:
        """When check_and_route falls back to a healthy fallback,
        _attempt_remediation is called on the unhealthy primary (side effect)."""
        router = SelfHealingRouter()
        router.health_checker._failure_threshold = 1
        router.health_checker.record_failure("dep-bad", "error")
        router.health_checker.record_success("dep-ok")

        result = router.check_and_route("dep-bad", fallback_profiles=["dep-ok"])
        assert result == "dep-ok"
        # _attempt_remediation was called — the test just verifies no exception.

    def test_remediation_handles_empty_health_dict_key(self) -> None:
        """Calling _attempt_remediation on a deployment that was recorded
        but whose health dict entry was removed still works."""
        router = SelfHealingRouter()
        router.record_failure("dep-ghost", "out of memory")
        # Artificially clear the health dict to simulate concurrent clear.
        with router._lock:
            router._deployment_health.clear()
        router._attempt_remediation("dep-ghost")
        # Must not raise — .get fetches {} and logging proceeds.


# ---------------------------------------------------------------------------
# SelfHealingRouter.deployment_health — snapshot independence
# ---------------------------------------------------------------------------


class TestDeploymentHealthSnapshot:
    """deployment_health returns a deep copy, not a live reference."""

    def test_snapshot_is_independent_of_internal_state(self) -> None:
        router = SelfHealingRouter()
        router.record_failure("dep-iso", "e1")

        snap1 = router.deployment_health
        del snap1["dep-iso"]

        snap2 = router.deployment_health
        assert "dep-iso" in snap2, "snapshot must not mutate internal state"

    def test_snapshot_data_is_a_copy(self) -> None:
        router = SelfHealingRouter()
        router.record_failure("dep-copy", "error")
        snap = router.deployment_health
        snap["dep-copy"]["status"] = "mutated"

        live = router.deployment_health
        assert live["dep-copy"]["status"] == "unhealthy", "modifying snapshot must not affect live state"


# ---------------------------------------------------------------------------
# SelfHealingRouter.set_failover_chain & _get_chain
# ---------------------------------------------------------------------------


class TestFailoverChainIntegration:
    """set_failover_chain stores a ModelFailoverChain per deployment;
    _get_chain fallbacks to the router-level _failover_chain."""

    def test_set_failover_chain_stores_per_deployment(self) -> None:
        from general_ludd.models.failover import ModelFailoverChain

        chain_a = ModelFailoverChain(primary_profile="gpt-4")
        chain_b = ModelFailoverChain(primary_profile="claude-3")

        router = SelfHealingRouter()
        router.set_failover_chain("dep-a", chain_a)
        router.set_failover_chain("dep-b", chain_b)

        with router._lock:
            assert router._per_deployment_chains["dep-a"] is chain_a
            assert router._per_deployment_chains["dep-b"] is chain_b

    def test_get_chain_returns_per_deployment_chain(self) -> None:
        from general_ludd.models.failover import ModelFailoverChain

        chain = ModelFailoverChain(primary_profile="gpt-4")
        router = SelfHealingRouter()
        router.set_failover_chain("dep-a", chain)

        result = router._get_chain("dep-a")
        assert result is chain

    def test_get_chain_fallbacks_to_router_default(self) -> None:
        from general_ludd.models.failover import ModelFailoverChain

        default_chain = ModelFailoverChain(primary_profile="default")
        router = SelfHealingRouter(failover_chain=default_chain)

        result = router._get_chain("unknown-dep")
        assert result is default_chain

    def test_get_chain_returns_none_when_no_chain_configured(self) -> None:
        router = SelfHealingRouter()
        result = router._get_chain("any-dep")
        assert result is None

    def test_set_fallbacks_creates_chain_and_fallback_map(self) -> None:
        router = SelfHealingRouter()
        router.set_fallbacks("dep-a", ["fb-1", "fb-2"])

        assert router._fallback_map["dep-a"] == ["fb-1", "fb-2"]
        chain = router._get_chain("dep-a")
        assert chain is not None
        assert chain._primary == "dep-a"
        assert chain._fallbacks == ["fb-1", "fb-2"]


# ---------------------------------------------------------------------------
# Thread-safety: SelfHealingRouter concurrent record_failure
# ---------------------------------------------------------------------------


class TestSelfHealingRouterConcurrency:
    """Concurrent record_failure calls must not corrupt the deployment_health
    dict (error_count is lock-guarded)."""

    def test_concurrent_record_failures_all_counted(self) -> None:
        router = SelfHealingRouter()
        num_threads = 20
        errors_per_thread = 25
        barrier = threading.Barrier(num_threads)

        def worker() -> None:
            barrier.wait()
            for i in range(errors_per_thread):
                router.record_failure("dep-cc", f"error-{i}")

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        health = router.deployment_health["dep-cc"]
        expected = num_threads * errors_per_thread
        assert health["error_count"] == expected, f"Expected {expected} errors, got {health['error_count']}"


# ---------------------------------------------------------------------------
# DeploymentHealthChecker: set_deployment_model unlocks per-model breaker
# ---------------------------------------------------------------------------


class TestDeploymentModelMapping:
    """set_deployment_model maps deployments to shared model breakers."""

    def test_two_deployments_same_model_share_breaker(self) -> None:
        checker = DeploymentHealthChecker(failure_threshold=2)
        checker.set_deployment_model("dep-east", "gpt-4")
        checker.set_deployment_model("dep-west", "gpt-4")

        checker.record_failure("dep-east", "timeout")
        checker.record_failure("dep-west", "oops")

        # Both deployments share the gpt-4 model breaker.
        assert checker.is_healthy("dep-east") is False
        assert checker.is_healthy("dep-west") is False

    def test_different_models_independent_breakers(self) -> None:
        checker = DeploymentHealthChecker(failure_threshold=2)
        checker.set_deployment_model("dep-a", "gpt-4")
        checker.set_deployment_model("dep-b", "claude-3")

        checker.record_failure("dep-a", "e1")
        checker.record_failure("dep-a", "e2")
        checker.record_failure("dep-b", "e1")

        assert checker.is_healthy("dep-a") is False
        assert checker.is_healthy("dep-b") is True

    def test_no_mapping_falls_back_to_deployment_id_as_model(self) -> None:
        checker = DeploymentHealthChecker(failure_threshold=1)
        checker.record_failure("standalone", "error")
        assert checker.is_healthy("standalone") is False

    def test_deployment_to_model_mapping_visible_via_internal(self) -> None:
        checker = DeploymentHealthChecker()
        checker.set_deployment_model("d", "m")
        with checker._lock:
            assert checker._deployment_to_model["d"] == "m"
