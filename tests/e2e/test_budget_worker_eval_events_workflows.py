"""E2E end-to-end workflow tests for budget, worker, eval, and events subsystems.

Covers end-to-end integration across:
  - budget: CombinedCostTracker, BudgetEnvelope, PerAgent/PerTask/PerTool envelopes,
    BudgetManager, CreditTracker
  - worker: FastAPI app creation, /healthz, /ping, /jobs endpoints, auth middleware
  - eval: EvalHarness, ModelEvaluator, EvalCase, EvalResult, scorers
  - events: EventBus publish/subscribe, HookSystem callback/webhook, event types
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from general_ludd.budget.credit_tracker import CreditTracker
from general_ludd.budget.envelope import (
    BudgetEnvelope,
    BudgetManager,
    PerAgentEnvelope,
    PerTaskEnvelope,
    PerToolEnvelope,
)
from general_ludd.eval.harness import EvalHarness
from general_ludd.eval.model import ModelEvaluator
from general_ludd.eval.schema import EvalCase, EvalResult
from general_ludd.eval.scorers import (
    check_assertions,
    composite_eval_score,
    compute_patch_similarity,
)
from general_ludd.events.bus import EventBus
from general_ludd.events.hooks import (
    HookRegistration,
    HookSystem,
    _redact_payload,
    is_safe_fetch_url,
)
from general_ludd.events.types import (
    CustomEvent,
    Event,
    EventType,
    HookTriggeredEvent,
    ModelAddedEvent,
    ModelRemovedEvent,
    PlaybookRegisteredEvent,
    ReloadCompletedEvent,
    ReloadRequestedEvent,
)
from general_ludd.worker.app import create_app
from general_ludd.worker.heartbeat import handle_ping, make_ping

# ── Budget: BudgetEnvelope E2E ──────────────────────────────────────────────

class TestBudgetEnvelopeE2E:
    def test_envelope_deduct_and_exhaust(self):
        env = BudgetEnvelope(name="agent:opus", limit=5.0)
        assert env.spent == pytest.approx(0.0)
        assert env.remaining == pytest.approx(5.0)
        assert not env.is_exhausted

        r = env.try_spend(3.0)
        assert r["allowed"] is True
        assert env.spent == pytest.approx(3.0)
        assert env.remaining == pytest.approx(2.0)

        r = env.try_spend(2.5)
        assert r["allowed"] is False
        assert "budget exceeded" in str(r["reason"])
        assert env.spent == pytest.approx(3.0)

    def test_envelope_infinite_limit(self):
        env = BudgetEnvelope(name="unlimited", limit=float("inf"))
        r = env.try_spend(1_000_000.0)
        assert r["allowed"] is True
        assert env.remaining == float("inf")

    def test_envelope_rejects_negative_amount(self):
        env = BudgetEnvelope(name="strict", limit=10.0)
        r = env.try_spend(-5.0)
        assert r["allowed"] is False
        assert "non-finite or negative" in str(r["reason"])
        assert env.spent == pytest.approx(0.0)

    def test_envelope_rejects_nan(self):
        env = BudgetEnvelope(name="strict", limit=10.0)
        r = env.try_spend(float("nan"))
        assert r["allowed"] is False

    def test_envelope_record_spend_no_gate(self):
        env = BudgetEnvelope(name="external", limit=100.0)
        env.record_spend(80.0)
        assert env.spent == pytest.approx(80.0)
        with pytest.raises(ValueError):
            env.record_spend(-1.0)

    def test_envelope_reset(self):
        env = BudgetEnvelope(name="resettable", limit=10.0)
        env.try_spend(10.0)
        assert env.is_exhausted
        env.reset()
        assert env.spent == pytest.approx(0.0)
        assert not env.is_exhausted

    def test_envelope_get_status(self):
        env = BudgetEnvelope(name="status-test", limit=7.5)
        env.try_spend(2.5)
        s = env.get_status()
        assert s["name"] == "status-test"
        assert s["limit"] == pytest.approx(7.5)
        assert s["spent"] == pytest.approx(2.5)
        assert s["remaining"] == pytest.approx(5.0)
        assert s["exhausted"] is False


# ── Budget: PerAgentEnvelope E2E ────────────────────────────────────────────

class TestPerAgentEnvelopeE2E:
    def test_unconfigured_agent_always_allowed(self):
        pa = PerAgentEnvelope()
        r = pa.try_spend("sonnet", 100.0)
        assert r["allowed"] is True
        assert r["remaining"] == float("inf")

    def test_agent_limit_blocks_excess(self):
        pa = PerAgentEnvelope()
        pa.set_limit("opus", 2.0)
        r = pa.try_spend("opus", 1.5)
        assert r["allowed"] is True
        r = pa.try_spend("opus", 1.0)
        assert r["allowed"] is False

    def test_agent_total_spent_and_reset(self):
        pa = PerAgentEnvelope()
        pa.set_limit("haiku", 10.0)
        pa.set_limit("sonnet", 20.0)
        pa.try_spend("haiku", 3.0)
        pa.try_spend("sonnet", 7.0)
        assert pa.total_spent() == pytest.approx(10.0)
        pa.reset_all()
        assert pa.total_spent() == pytest.approx(0.0)

    def test_agent_get_status(self):
        pa = PerAgentEnvelope()
        pa.set_limit("sonnet", 5.0)
        pa.try_spend("sonnet", 2.0)
        status = pa.get_status()
        assert "sonnet" in status


# ── Budget: PerTaskEnvelope E2E ─────────────────────────────────────────────

class TestPerTaskEnvelopeE2E:
    def test_default_limit_creates_implicit_envelope(self):
        pt = PerTaskEnvelope(default_limit=1.0)
        r = pt.try_spend("task-1", 0.8)
        assert r["allowed"] is True
        r = pt.try_spend("task-1", 0.3)
        assert r["allowed"] is False

    def test_explicit_limit_overrides_default(self):
        pt = PerTaskEnvelope(default_limit=1.0)
        pt.set_limit("task-2", 5.0)
        r = pt.try_spend("task-2", 4.0)
        assert r["allowed"] is True

    def test_no_default_no_limit_allows_all(self):
        pt = PerTaskEnvelope(default_limit=float("inf"))
        r = pt.try_spend("any-task", 999.0)
        assert r["allowed"] is True
        assert r["remaining"] == float("inf")

    def test_task_total_spent_and_reset(self):
        pt = PerTaskEnvelope(default_limit=10.0)
        pt.try_spend("a", 2.0)
        pt.try_spend("b", 3.0)
        assert pt.total_spent() == pytest.approx(5.0)
        pt.reset_all()
        assert pt.total_spent() == pytest.approx(0.0)


# ── Budget: PerToolEnvelope E2E ─────────────────────────────────────────────

class TestPerToolEnvelopeE2E:
    def test_unconfigured_tool_always_allowed(self):
        pt = PerToolEnvelope()
        r = pt.try_spend("bash", 999.0)
        assert r["allowed"] is True

    def test_tool_limit_blocks(self):
        pt = PerToolEnvelope()
        pt.set_limit("write", 0.5)
        pt.try_spend("write", 0.3)
        r = pt.try_spend("write", 0.3)
        assert r["allowed"] is False

    def test_tool_total_spent_and_reset(self):
        pt = PerToolEnvelope()
        pt.set_limit("task", 10.0)
        pt.set_limit("write", 5.0)
        pt.try_spend("task", 3.0)
        pt.try_spend("write", 2.0)
        assert pt.total_spent() == pytest.approx(5.0)
        pt.reset_all()
        assert pt.total_spent() == pytest.approx(0.0)


# ── Budget: BudgetManager E2E ───────────────────────────────────────────────

class TestBudgetManagerE2E:
    def test_check_all_allows_when_nothing_configured(self):
        mgr = BudgetManager()
        r = mgr.check_all(agent_type="sonnet", amount=100.0)
        assert r.allowed is True

    def test_check_all_blocks_at_tool_layer_first(self):
        pt = PerToolEnvelope()
        pt.set_limit("bash", 0.1)
        mgr = BudgetManager(per_tool=pt)
        r = mgr.check_all(tool_type="bash", amount=0.2)
        assert r.allowed is False
        assert r.details["layer"] == "tool"

    def test_check_all_blocks_at_task_layer(self):
        ptask = PerTaskEnvelope(default_limit=1.0)
        mgr = BudgetManager(per_task=ptask)
        # spend up first
        ptask.try_spend("t1", 0.9)
        r = mgr.check_all(task_id="t1", amount=0.2)
        assert r.allowed is False
        assert r.details["layer"] == "task"

    def test_check_all_blocks_at_agent_layer(self):
        pa = PerAgentEnvelope()
        pa.set_limit("opus", 1.0)
        mgr = BudgetManager(per_agent=pa)
        pa.try_spend("opus", 0.9)
        r = mgr.check_all(agent_type="opus", amount=0.2)
        assert r.allowed is False
        assert r.details["layer"] == "agent"

    def test_get_status_aggregates_all_layers(self):
        mgr = BudgetManager()
        mgr.per_agent.set_limit("sonnet", 10.0)
        mgr.per_task.set_limit("t1", 5.0)
        mgr.per_tool.set_limit("read", 2.0)
        status = mgr.get_status()
        assert "agents" in status
        assert "tasks" in status
        assert "tools" in status

    def test_total_spent_aggregates_all_layers(self):
        mgr = BudgetManager()
        mgr.per_agent.set_limit("sonnet", 10.0)
        mgr.per_tool.set_limit("write", 5.0)
        mgr.per_agent.try_spend("sonnet", 2.0)
        mgr.per_tool.try_spend("write", 1.0)
        assert mgr.total_spent() == pytest.approx(3.0)

    def test_reset_all_clears_all_layers(self):
        mgr = BudgetManager()
        mgr.per_agent.set_limit("sonnet", 10.0)
        mgr.per_agent.try_spend("sonnet", 5.0)
        mgr.reset_all()
        assert mgr.total_spent() == pytest.approx(0.0)


# ── Budget: CreditTracker E2E ───────────────────────────────────────────────

class TestCreditTrackerE2E:
    def test_construction_defaults(self):
        ct = CreditTracker()
        assert ct.get_balance_threshold("deepseek") == pytest.approx(1.0)
        assert ct.get_balance_threshold("openai") == pytest.approx(5.0)

    def test_custom_thresholds(self):
        ct = CreditTracker(thresholds={"deepseek": 0.5})
        assert ct.get_balance_threshold("deepseek") == pytest.approx(0.5)

    def test_check_balance_unknown_service_raises(self):
        ct = CreditTracker()
        with pytest.raises(ValueError, match="Unsupported service"):
            ct.check_balance("nonexistent")

    def test_set_spend_limit_unsupported_provider(self):
        ct = CreditTracker()
        r = ct.set_spend_limit("deepseek", 10.0)
        assert r["supported"] is False
        assert r["applied"] is False

    def test_recommend_refill_no_history(self):
        ct = CreditTracker(thresholds={"deepseek": 2.0})
        assert ct.recommend_refill_amount("deepseek") == pytest.approx(4.0)

    def test_recommend_refill_with_history(self):
        ct = CreditTracker(
            thresholds={"openai": 5.0},
            historical_spend_rates={"openai": 3.0},
        )
        assert ct.recommend_refill_amount("openai") == pytest.approx(21.0)

    def test_last_balance_none_initially(self):
        ct = CreditTracker()
        assert ct.last_balance("deepseek") is None

    def test_unsupported_service_raises_for_threshold(self):
        ct = CreditTracker()
        with pytest.raises(ValueError, match="Unsupported service"):
            ct.get_balance_threshold("bogus")


# ── Worker E2E ──────────────────────────────────────────────────────────────

class TestWorkerAppE2E:
    @pytest.fixture
    def unauthed_client(self):
        import os as _os
        _os.environ["GLUDD_PSK_DISABLE"] = "1"
        app = create_app(gateway=None, dispatcher=None)
        return TestClient(app)

    def test_healthz_returns_healthy(self, unauthed_client):
        resp = unauthed_client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"

    def test_ping_returns_pong(self, unauthed_client):
        resp = unauthed_client.post("/ping")
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "worker_pong"
        assert "correlation_id" in data

    def test_validate_job_returns_501(self, unauthed_client):
        from general_ludd.schemas.job import JobSpec
        job = JobSpec(
            job_id="j-1", todo_id="t-1", queue="default",
            playbook="validate", work_type="analysis", prompt_text="test",
        )
        resp = unauthed_client.post("/jobs/validate", json=job.model_dump())
        assert resp.status_code == 501

    def test_policy_validate_returns_501(self, unauthed_client):
        from general_ludd.schemas.job import JobSpec
        job = JobSpec(
            job_id="j-2", todo_id="t-2", queue="default",
            playbook="policy-validate", work_type="analysis", prompt_text="test",
        )
        resp = unauthed_client.post("/jobs/policy-validate", json=job.model_dump())
        assert resp.status_code == 501

    def test_reload_request_returns_501(self, unauthed_client):
        from general_ludd.schemas.job import JobSpec
        job = JobSpec(
            job_id="j-3", todo_id="t-3", queue="default",
            playbook="reload-request", work_type="analysis", prompt_text="test",
        )
        resp = unauthed_client.post("/jobs/reload-request", json=job.model_dump())
        assert resp.status_code == 501

    def test_return_review_returns_ack(self, unauthed_client):
        from general_ludd.schemas.job import JobSpec
        job = JobSpec(
            job_id="j-4", todo_id="t-4", queue="default",
            playbook="return-review", work_type="review", prompt_text="test",
        )
        resp = unauthed_client.post("/jobs/return-review", json=job.model_dump())
        assert resp.status_code == 200
        assert resp.json()["status"] == "ack"


class TestWorkerHeartbeatE2E:
    def test_make_ping(self):
        ping = make_ping()
        assert ping.type == EventType.WORKER_PING
        assert ping.event_id

    def test_handle_ping_produces_correlated_pong(self):
        ping = make_ping()
        pong = handle_ping(ping, worker_id="worker-alpha")
        assert pong.type == EventType.WORKER_PONG
        assert pong.payload["worker_id"] == "worker-alpha"
        assert pong.correlation_id == ping.event_id


# ── Eval: Harness + Model E2E ───────────────────────────────────────────────

class TestEvalHarnessE2E:
    def test_harness_no_evaluator_reports_failure(self):
        harness = EvalHarness(model="sonnet")
        cases = [EvalCase(id="c1", description="test", input_files={}, expected_patch="x")]
        results = harness.run_benchmark(cases)
        assert len(results) == 1
        assert not results[0].passed
        assert "no evaluator" in results[0].errors[0]

    def test_harness_ready_false_without_evaluator(self):
        harness = EvalHarness(model="sonnet")
        assert harness.ready is False

    def test_harness_ready_true_with_evaluator(self):
        mock_gw = MagicMock()
        evaluator = ModelEvaluator(gateway=mock_gw, profile_id="sonnet")
        harness = EvalHarness(model="sonnet", evaluator=evaluator)
        assert harness.ready is True

    def test_run_single_returns_result(self):
        mock_gw = MagicMock()
        mock_gw.call_model.return_value = MagicMock(content="patch output")
        evaluator = ModelEvaluator(gateway=mock_gw, profile_id="sonnet", dry_run=False)
        case = EvalCase(
            id="c2", description="add tests",
            input_files={"a.py": "x=1"},
            expected_patch="patch output",
            assertions={"patch_contains": "patch"},
        )
        harness = EvalHarness(evaluator=evaluator)
        result = harness.run_single(case)
        assert isinstance(result, EvalResult)
        assert result.case_id == "c2"

    def test_run_single_exception_produces_error_result(self):
        mock_gw = MagicMock()
        mock_gw.call_model.side_effect = RuntimeError("boom")
        evaluator = ModelEvaluator(gateway=mock_gw, profile_id="sonnet")
        case = EvalCase(id="c3", description="boom", input_files={}, expected_patch="")
        harness = EvalHarness(evaluator=evaluator)
        results = harness.run_benchmark([case])
        assert not results[0].passed
        assert "boom" in results[0].errors[0]

    def test_last_results_preserved(self):
        mock_gw = MagicMock()
        mock_gw.call_model.return_value = MagicMock(content="yy")
        evaluator = ModelEvaluator(gateway=mock_gw, profile_id="sonnet", dry_run=True)
        harness = EvalHarness(evaluator=evaluator)
        cases = [EvalCase(
            id="c4", description="d", input_files={}, expected_patch="zz",
        )]
        harness.run_benchmark(cases)
        assert len(harness.last_results) == 1


class TestModelEvaluatorE2E:
    def test_dry_run_returns_prompt_not_call(self):
        mock_gw = MagicMock()
        evaluator = ModelEvaluator(gateway=mock_gw, profile_id="sonnet", dry_run=True)
        case = EvalCase(
            id="d1", description="make tests",
            input_files={"x.py": "pass"},
            expected_patch="patch",
        )
        patch = evaluator.generate_patch(case)
        assert "make tests" in patch
        assert "x.py" in patch
        mock_gw.call_model.assert_not_called()

    def test_live_call_delegates_to_gateway(self):
        mock_gw = MagicMock()
        mock_gw.call_model.return_value = MagicMock(content="live patch")
        evaluator = ModelEvaluator(gateway=mock_gw, profile_id="opus")
        case = EvalCase(id="d2", description="fix", input_files={}, expected_patch="")
        patch = evaluator.generate_patch(case)
        assert patch == "live patch"
        mock_gw.call_model.assert_called_once()


# ── Eval: Scorers E2E ───────────────────────────────────────────────────────

class TestEvalScorersE2E:
    def test_patch_similarity_identical(self):
        assert compute_patch_similarity("abc", "abc") == pytest.approx(1.0)

    def test_patch_similarity_completely_different(self):
        assert compute_patch_similarity("abc", "xyz") == pytest.approx(0.0)

    def test_patch_similarity_both_empty(self):
        assert compute_patch_similarity("", "") == pytest.approx(1.0)

    def test_patch_similarity_one_empty(self):
        assert compute_patch_similarity("abc", "") == pytest.approx(0.0)

    def test_check_assertions_patch_contains(self):
        r = check_assertions({"patch_contains": "def foo"}, "def foo():\n  pass")
        assert r["patch_contains"] is True

    def test_check_assertions_patch_contains_missing(self):
        r = check_assertions({"patch_contains": "def bar"}, "def foo():\n  pass")
        assert r["patch_contains"] is False

    def test_check_assertions_line_count_min(self):
        r = check_assertions({"line_count_min": "3"}, "a\nb\nc\nd")
        assert r["line_count_min"] is True

    def test_check_assertions_line_count_min_fails(self):
        r = check_assertions({"line_count_min": "5"}, "a\nb")
        assert r["line_count_min"] is False

    def test_check_assertions_invalid_value(self):
        r = check_assertions({"line_count_min": "not-a-number"}, "a\nb")
        assert r["line_count_min"] is False

    def test_composite_eval_score_perfect(self):
        case = EvalCase(id="p1", description="", input_files={},
                        expected_patch="orig", assertions={"patch_contains": "orig"})
        r = composite_eval_score(case, "orig", tokens_used=100, duration_ms=500)
        assert r.passed is True
        assert r.score == pytest.approx(1.0)

    def test_composite_eval_score_total_mismatch(self):
        case = EvalCase(id="p2", description="", input_files={},
                        expected_patch="good", assertions={"patch_contains": "good"})
        r = composite_eval_score(case, "bad zzz", tokens_used=0, duration_ms=0)
        assert r.passed is False
        assert "low_similarity" in r.errors[0]
        assert "assertion_failed:patch_contains" in r.errors[1]


# ── Events: EventBus E2E ────────────────────────────────────────────────────

class TestEventBusE2E:
    def test_subscribe_and_publish_sync(self):
        bus = EventBus()
        received = []

        def handler(event):
            received.append(event)

        bus.subscribe(EventType.MODEL_ADDED, handler)
        evt = ModelAddedEvent(model_id="m1", profile={})
        delivered = bus.publish(evt)
        assert delivered == 1
        assert len(received) == 1
        assert received[0].payload["model_id"] == "m1"

    def test_wildcard_subscriber_receives_all(self):
        bus = EventBus()
        received = []

        def catch_all(event):
            received.append(event.type)

        bus.subscribe("*", catch_all)
        bus.publish(ModelAddedEvent(model_id="a", profile={}))
        bus.publish(ModelRemovedEvent(model_id="b"))
        assert received == [EventType.MODEL_ADDED, EventType.MODEL_REMOVED]

    def test_unsubscribe_removes_handler(self):
        bus = EventBus()
        received = []

        def handler(event):
            received.append(event)

        sid = bus.subscribe(EventType.CONFIG_RELOADED, handler)
        bus.unsubscribe(sid)
        bus.publish(ReloadRequestedEvent(scope="all"))
        assert len(received) == 0

    def test_subscriber_exception_does_not_block_others(self):
        bus = EventBus()

        def failer(event):
            raise RuntimeError("boom")

        good = []

        def good_handler(event):
            good.append(event.event_id)

        bus.subscribe(EventType.PLAYBOOK_REGISTERED, failer)
        bus.subscribe(EventType.PLAYBOOK_REGISTERED, good_handler)
        evt = PlaybookRegisteredEvent(playbook="test")
        delivered = bus.publish(evt)
        assert delivered >= 1
        assert len(good) == 1

    def test_history_records_events(self):
        bus = EventBus(history_size=3)
        bus.publish(ReloadCompletedEvent(scope="config"))
        bus.publish(ReloadRequestedEvent(scope="plugins"))
        bus.publish(ReloadCompletedEvent(scope="secrets"))
        bus.publish(ReloadRequestedEvent(scope="models"))
        hist = bus.get_history()
        assert len(hist) == 3
        assert hist[-1].payload["scope"] == "models"

    def test_clear_removes_subscribers(self):
        bus = EventBus()
        bus.subscribe(EventType.CUSTOM, lambda e: None)
        bus.clear()
        evt = CustomEvent(name="test")
        delivered = bus.publish(evt)
        assert delivered == 0

    def test_publish_with_string_event_type(self):
        bus = EventBus()
        got = []

        def h(e):
            got.append(e)

        bus.subscribe("my.custom.event", h)
        evt = Event(type="my.custom.event", payload={"k": "v"})
        delivered = bus.publish(evt)
        assert delivered == 1

    def test_async_subscriber_scheduled(self):
        bus = EventBus()

        async def async_handler(event):
            pass

        bus.subscribe(EventType.CUSTOM, async_handler)
        evt = CustomEvent(name="async-test")
        delivered = bus.publish(evt)
        assert delivered >= 1


# ── Events: HookSystem E2E ──────────────────────────────────────────────────

class TestHookSystemE2E:
    def test_register_callback_and_fire(self):
        hs = HookSystem()
        results = []

        def cb(payload):
            results.append(payload)

        hs.register_callback("task.completed", cb)
        count = hs.fire("task.completed", {"task_id": "t1", "result": "ok"})
        assert count == 1
        assert len(results) == 1
        assert results[0]["task_id"] == "t1"

    def test_unregister_removes_callback(self):
        hs = HookSystem()
        called = []

        def cb(payload):
            called.append(True)

        hid = hs.register_callback("build.done", cb)
        hs.unregister(hid)
        count = hs.fire("build.done", {})
        assert count == 0
        assert len(called) == 0

    def test_fire_respects_priority_ordering(self):
        hs = HookSystem()
        order = []

        def lo(p):
            order.append("lo")
        def hi(p):
            order.append("hi")

        hs.register_callback("deploy", lo, priority=200)
        hs.register_callback("deploy", hi, priority=10)
        hs.fire("deploy", {})
        assert order[0] == "hi"
        assert order[1] == "lo"

    def test_failing_callback_does_not_block_others(self):
        hs = HookSystem()
        ok_called = []

        def failer(p):
            raise RuntimeError("boom")
        def okay(p):
            ok_called.append(True)

        hs.register_callback("test", failer)
        hs.register_callback("test", okay)
        count = hs.fire("test", {"x": 1})
        assert len(ok_called) == 1
        assert count == 1

    def test_fire_publishes_hook_triggered_event(self):
        bus = EventBus()
        hs = HookSystem(event_bus=bus)
        events = []

        bus.subscribe(EventType.HOOK_TRIGGERED, lambda e: events.append(e))
        hs.register_callback("deploy", lambda p: None)
        hs.fire("deploy", {"env": "prod"})
        assert len(events) == 1
        assert events[0].payload["event_name"] == "deploy"

    def test_list_hooks_returns_all_registrations(self):
        hs = HookSystem()
        hs.register_callback("a", lambda p: None)
        hs.register_callback("b", lambda p: None, priority=50)
        hooks = hs.list_hooks()
        assert len(hooks) == 2

    def test_redact_payload_strips_secrets(self):
        payload = {
            "api_key": "sk-abc123",
            "user": "bob",
            "nested": {"token": "secret", "data": "visible"},
        }
        redacted = _redact_payload(payload)
        assert "api_key" not in redacted
        assert "user" in redacted
        assert "token" not in redacted["nested"]
        assert redacted["nested"]["data"] == "visible"

    def test_is_safe_fetch_url_rejects_internal(self):
        assert is_safe_fetch_url("http://127.0.0.1:8080/hook") is False
        assert is_safe_fetch_url("http://localhost/hook") is False

    def test_is_safe_fetch_url_allows_public(self):
        assert is_safe_fetch_url("https://hooks.example.com/webhook") is True

    def test_is_safe_fetch_url_rejects_bad_inputs(self):
        assert is_safe_fetch_url("") is False
        assert is_safe_fetch_url(None) is False  # type: ignore[arg-type]

    def test_hook_registration_id_format(self):
        reg = HookRegistration(hook_id="h1", event_name="e1", hook_type="callback")
        assert reg.hook_id == "h1"
        assert reg.event_name == "e1"


# ── Events: Event Types E2E ─────────────────────────────────────────────────

class TestEventTypesE2E:
    def test_custom_event(self):
        evt = CustomEvent(name="my.event", payload={"k": "v"})
        assert evt.type == EventType.CUSTOM
        assert evt.payload["name"] == "my.event"
        assert evt.payload["k"] == "v"

    def test_event_base_has_fields(self):
        evt = Event(type=EventType.CUSTOM, payload={}, source="src",
                     correlation_id="corr-1")
        assert evt.event_id
        assert evt.timestamp > 0
        assert evt.source == "src"
        assert evt.correlation_id == "corr-1"

    def test_hook_triggered_event(self):
        evt = HookTriggeredEvent(event_name="deploy.done")
        assert evt.type == EventType.HOOK_TRIGGERED
        assert evt.payload["event_name"] == "deploy.done"

    def test_event_type_is_string_compatible(self):
        assert EventType.MODEL_ADDED.value == "model_added"
        assert EventType.WORKER_PING.value == "worker_ping"


# ── Cross-Subsystem: Budget + Events E2E ────────────────────────────────────

class TestCrossSubsystemBudgetEventsE2E:
    def test_budget_spend_triggers_event(self):
        bus = EventBus()
        events = []

        bus.subscribe("*", lambda e: events.append(e))
        mgr = BudgetManager()
        mgr.per_agent.set_limit("sonnet", 5.0)
        result = mgr.check_all(agent_type="sonnet", amount=2.0)
        assert result.allowed is True
        bus.publish(CustomEvent(name="budget.charged", payload={
            "agent": "sonnet", "amount": 2.0,
        }))
        assert len(events) == 1


# ── E2E: EventBus + HookSystem integration ──────────────────────────────────

class TestEventBusHookSystemIntegration:
    def test_hook_fire_mirrors_to_event_bus(self):
        bus = EventBus()
        hs = HookSystem(event_bus=bus)
        bus_events = []

        bus.subscribe(EventType.HOOK_TRIGGERED, lambda e: bus_events.append(e))
        hs.register_callback("notify", lambda p: None)
        hs.fire("notify", {"msg": "hello"})
        assert len(bus_events) == 1
        assert bus_events[0].payload["event_name"] == "notify"
        assert bus_events[0].payload["succeeded"] == 1
