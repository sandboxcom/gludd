"""Unit tests for event loop."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.event_loop.lease import reclaim_expired_leases
from general_ludd.event_loop.loop import EventLoop
from general_ludd.schemas.task_decision import TaskDecision
from general_ludd.schemas.task_return import TaskReturn, TaskReturnStatus
from general_ludd.schemas.todo import Todo, TodoStatus


def _make_loop(**overrides):
    session = AsyncMock()
    db_result = MagicMock()
    db_result.scalars.return_value.all.return_value = []
    session.execute.return_value = db_result
    session.delete = AsyncMock()
    session.flush = AsyncMock()
    # session.add is a SYNC SQLAlchemy method; AsyncMock would make it return a
    # coroutine that is never awaited, leaking a "coroutine was never awaited"
    # RuntimeWarning that, under CI's serial test ordering, surfaced as a hard
    # "Event loop is closed" failure when GC'd during a later test's teardown.
    session.add = MagicMock()
    http_client = AsyncMock()
    todo_repo = AsyncMock()
    task_return_repo = AsyncMock()
    defaults = dict(
        worker_base_url="http://worker:8000",
        config={"tick_interval": 1.0},
        session=session,
        http_client=http_client,
        todo_repo=todo_repo,
        task_return_repo=task_return_repo,
    )
    defaults.update(overrides)
    loop = EventLoop(**defaults)
    return loop, {
        "session": session,
        "http_client": http_client,
        "todo_repo": todo_repo,
        "task_return_repo": task_return_repo,
    }


def _recording_phase(name: str, record: list[str]):
    async def _phase():
        record.append(name)

    return _phase


class TestEventLoop:
    @pytest.mark.asyncio
    async def test_event_loop_tick_runs_all_phases(self):
        loop, _ = _make_loop()
        call_order: list[str] = []
        expected = [
            "load_config_snapshot",
            "claim_unreviewed_task_returns",
            "dispatch_return_review_jobs",
            "evaluate_pid_controllers",
            "refill_task_buckets",
            "claim_runnable_todos",
            "evaluate_rules",
            "dispatch_execute_jobs",
            "reconcile_completed_decisions",
            "emit_tick_metrics",
        ]
        for name in expected:
            setattr(loop, f"_phase_{name}", _recording_phase(name, call_order))
        await loop.tick()
        assert call_order == expected

    @pytest.mark.asyncio
    async def test_event_loop_dispatches_return_review_for_unreviewed_return(self):
        loop, mocks = _make_loop()
        tr = TaskReturn(
            return_id="RET-001",
            job_id="JOB-001",
            playbook="noop.yml",
            queue="core",
            status=TaskReturnStatus.CREATED,
        )
        mocks["task_return_repo"].claim_unreviewed.return_value = [tr]
        mocks["http_client"].post.return_value = MagicMock(status_code=202)
        await loop._phase_claim_unreviewed_task_returns()
        await loop._phase_dispatch_return_review_jobs()
        mocks["http_client"].post.assert_called_once()
        url = mocks["http_client"].post.call_args[0][0]
        assert "return-review" in url

    @pytest.mark.asyncio
    async def test_event_loop_skips_reviewed_return(self):
        loop = EventLoop()
        task_return = TaskReturn(
            return_id="RET-002",
            job_id="JOB-002",
            playbook="noop.yml",
            queue="core",
            status=TaskReturnStatus.REVIEWED,
        )
        result = await loop.dispatch_return_review(task_return)
        assert result["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_event_loop_never_executes_playbook_inline(self):
        loop, mocks = _make_loop()
        mocks["todo_repo"].claim_runnable.return_value = []
        mocks["task_return_repo"].claim_unreviewed.return_value = []
        result = await loop.tick()
        assert isinstance(result, dict)
        assert "phases_completed" in result
        mocks["http_client"].post.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_execute_job_feeds_trace_buffer(self):
        """A real generation dispatch must populate the RecentTracesBuffer.

        Regression for the WIRED-but-inert tracing gap (audit a126ff8b): the
        observability stack (ExecutionTrace -> AutoBenchmarkRecorder.record_from_trace
        -> RecentTracesBuffer) was fully built and wired in the daemon, but the
        EventLoop dispatch path never constructed a trace, so /api/traces always
        reported total_recorded == 0. This asserts a genuine dispatch now feeds
        the buffer (additive to the existing DB benchmark write).
        """
        from general_ludd.observability.recorder import AutoBenchmarkRecorder
        from general_ludd.observability.trace_store import RecentTracesBuffer

        buffer = RecentTracesBuffer()
        # benchmark_repo=None: the buffer append in record_from_trace is
        # unconditional on a populated trace and does NOT require a repo, so this
        # isolates the trace->buffer feed from the DB write.
        recorder = AutoBenchmarkRecorder(benchmark_repo=None, trace_buffer=buffer)

        runner = MagicMock()
        runner.prepare_job_dirs.return_value = {"root": "/tmp/exec-trace-test"}

        loop, _ = _make_loop(
            runner=runner,
            model_gateway=MagicMock(),
        )
        loop._benchmark_recorder = recorder

        todo = Todo(
            title="generate a thing",
            description="please",
            status=TodoStatus.ACTIVE,
            work_type="code",  # a generation work type
        )

        assert buffer.total_recorded == 0  # fail-before guard

        with patch(
            "general_ludd.event_loop.loop.invoke_model_for_generation",
            return_value="GENERATED OUTPUT",
        ):
            await loop._dispatch_execute_job(todo)

        # Drain the fire-and-forget background tasks (record_from_trace runs in one).
        for _t in list(loop._background_tasks):
            await _t

        assert buffer.total_recorded > 0
        snap = buffer.snapshot()
        assert snap["total_recorded"] > 0
        assert snap["count"] > 0
        # The captured span carries the real generation phase + model profile.
        recent = buffer.recent()
        assert recent[0].spans
        assert recent[0].spans[0].phase == "generate"
        assert recent[0].work_type == "code"

    @pytest.mark.asyncio
    async def test_event_loop_claims_runnable_todos(self):
        loop, mocks = _make_loop()
        queued = Todo(title="queued", status=TodoStatus.QUEUED)
        mocks["todo_repo"].claim_runnable.return_value = [queued]
        await loop._phase_claim_runnable_todos()
        assert len(loop._tick_state["claimed_todos"]) == 1

    @pytest.mark.asyncio
    async def test_event_loop_respects_manual_hold(self):
        loop, mocks = _make_loop()
        queued = Todo(title="queued", status=TodoStatus.QUEUED)
        mocks["todo_repo"].claim_runnable.return_value = [queued]
        await loop._phase_claim_runnable_todos()
        for t in loop._tick_state["claimed_todos"]:
            assert t.status != TodoStatus.MANUAL_HOLD

    @pytest.mark.asyncio
    async def test_event_loop_continues_when_manual_hold_exists(self):
        loop, mocks = _make_loop()
        mocks["todo_repo"].claim_runnable.return_value = []
        mocks["task_return_repo"].claim_unreviewed.return_value = []
        result = await loop.tick()
        assert result is not None

    @pytest.mark.asyncio
    async def test_event_loop_reclaims_expired_job_lease(self):
        session = AsyncMock()
        expired = MagicMock()
        expired.expires_at = datetime.now(UTC) - timedelta(seconds=60)
        result_mock = MagicMock()
        result_mock.scalars().all.return_value = [expired]
        session.execute.return_value = result_mock
        session.delete = AsyncMock()
        session.flush = AsyncMock()
        count = await reclaim_expired_leases(session, max_age_seconds=300)
        assert count == 1
        session.delete.assert_called_once_with(expired)

    @pytest.mark.asyncio
    async def test_event_loop_does_not_reclaim_active_lease(self):
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars().all.return_value = []
        session.execute.return_value = result_mock
        session.delete = AsyncMock()
        session.flush = AsyncMock()
        count = await reclaim_expired_leases(session, max_age_seconds=300)
        assert count == 0
        session.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_event_loop_dispatches_execute_jobs(self):
        loop, mocks = _make_loop()
        todo = Todo(
            title="test task",
            todo_id="TODO-001",
            status=TodoStatus.ACTIVE,
            queue="core",
            work_type="code",
            resource_profile="low_resource",
        )
        mocks["http_client"].post.return_value = MagicMock(status_code=202)
        loop._tick_state["claimed_todos"] = [todo]
        await loop._phase_dispatch_execute_jobs()
        mocks["http_client"].post.assert_called_once()
        url = mocks["http_client"].post.call_args[0][0]
        assert "execute" in url

    @pytest.mark.asyncio
    async def test_rules_fire_on_live_claimed_todos(self):
        """Regression: rules must evaluate against the LIVE claimed todos.

        Previously _phase_evaluate_rules only read self.config["todos"] (absent at
        runtime) and ran BEFORE claim_runnable_todos, so rules never fired and
        the model-profile override never reached dispatch. This test drives a real
        claim + rules evaluation in order and asserts the override reaches a todo.
        """
        rule = {
            "rule_id": "force_model_for_code",
            "priority": 10,
            "condition": {"field": "todo.work_type", "op": "eq", "value": "code"},
            "actions": [
                {"type": "set_model_profile", "profile_id": "premium-model"}
            ],
            "audit_message": "force premium model for code todos",
        }
        loop, mocks = _make_loop(config={"tick_interval": 1.0, "rules": [rule]})
        todo = Todo(
            title="code task",
            todo_id="TODO-RULE-1",
            status=TodoStatus.ACTIVE,
            queue="core",
            work_type="code",
        )
        mocks["todo_repo"].claim_runnable.return_value = [todo]

        # Run phases in the production order: claim BEFORE evaluate_rules.
        await loop._phase_claim_runnable_todos()
        await loop._phase_evaluate_rules()

        results = loop._tick_state.get("rule_evaluation_results", [])
        # Results must be keyed by the LIVE todo_id (not an empty config-derived id).
        assert any(r.get("todo_id") == "TODO-RULE-1" for r in results), results

        # And the override must actually resolve for the live todo.
        overrides = loop._get_rule_overrides_for_todo(todo)
        assert overrides.get("model_profile") == "premium-model", overrides

    @pytest.mark.asyncio
    async def test_evaluate_rules_runs_after_claim_in_phase_order(self):
        """The tick PHASE_ORDER must run claim_runnable_todos before evaluate_rules."""
        from general_ludd.event_loop.loop import PHASE_ORDER

        assert PHASE_ORDER.index("claim_runnable_todos") < PHASE_ORDER.index(
            "evaluate_rules"
        ), PHASE_ORDER

    @pytest.mark.asyncio
    async def test_event_loop_reconcile_decision_complete(self):
        loop, mocks = _make_loop()
        todo_model = MagicMock()
        todo_model.todo_id = "TODO-001"
        todo_model.status = "reviewing_return"
        todo_model.version = 1
        decision_row = MagicMock()
        decision_row.return_id = "RET-001"
        decision_row.matched_todo_id = "TODO-001"
        decision_row.decision = "complete"
        decision_row.confidence = 0.95
        result_mock = MagicMock()
        result_mock.scalars().all.return_value = [decision_row]
        mocks["session"].execute.return_value = result_mock
        mocks["todo_repo"].get_by_id.return_value = todo_model
        mocks["todo_repo"].transition = AsyncMock(return_value=todo_model)
        await loop._phase_reconcile_completed_decisions()
        mocks["todo_repo"].transition.assert_called_once_with(
            "TODO-001", TodoStatus.COMPLETE, 1
        )

    @pytest.mark.asyncio
    async def test_event_loop_reconcile_decision_needs_more_work(self):
        loop, mocks = _make_loop()
        todo_model = MagicMock()
        todo_model.todo_id = "TODO-001"
        todo_model.status = "reviewing_return"
        todo_model.version = 1
        decision_row = MagicMock()
        decision_row.return_id = "RET-002"
        decision_row.matched_todo_id = "TODO-001"
        decision_row.decision = "needs_more_work"
        decision_row.confidence = 0.8
        result_mock = MagicMock()
        result_mock.scalars().all.return_value = [decision_row]
        mocks["session"].execute.return_value = result_mock
        mocks["todo_repo"].get_by_id.return_value = todo_model
        mocks["todo_repo"].transition = AsyncMock(return_value=todo_model)
        await loop._phase_reconcile_completed_decisions()
        mocks["todo_repo"].transition.assert_called_once_with(
            "TODO-001", TodoStatus.NEEDS_MORE_WORK, 1
        )

    @pytest.mark.asyncio
    async def test_event_loop_emits_tick_metrics(self):
        loop, mocks = _make_loop()
        mocks["todo_repo"].claim_runnable.return_value = []
        mocks["task_return_repo"].claim_unreviewed.return_value = []
        result = await loop.tick()
        assert "phases_completed" in result
        assert "tick_duration_ms" in result
        assert isinstance(result["tick_duration_ms"], float)
        assert result["phases_completed"] == 11

    @pytest.mark.asyncio
    async def test_run_forever_can_be_stopped(self):
        loop, mocks = _make_loop()
        mocks["todo_repo"].claim_runnable.return_value = []
        mocks["task_return_repo"].claim_unreviewed.return_value = []
        iterations = 0
        original_tick = loop.tick

        async def counting_tick():
            nonlocal iterations
            iterations += 1
            if iterations >= 3:
                loop.stop()
            return await original_tick()

        loop.tick = counting_tick
        await loop.run_forever(interval=0.01)
        assert iterations >= 3

    @pytest.mark.asyncio
    async def test_reconcile_decision_complete_backward_compat(self):
        loop = EventLoop()
        todo = Todo(title="test", status=TodoStatus.REVIEWING_RETURN)
        decision = TaskDecision(
            return_id="RET-001",
            decision="complete",
            confidence=0.95,
        )
        updated = await loop.reconcile_decision(decision, todo)
        assert updated.status == TodoStatus.COMPLETE

    @pytest.mark.asyncio
    async def test_reconcile_decision_needs_more_work_backward_compat(self):
        loop = EventLoop()
        todo = Todo(title="test", status=TodoStatus.REVIEWING_RETURN)
        decision = TaskDecision(
            return_id="RET-001",
            decision="needs_more_work",
            confidence=0.8,
        )
        updated = await loop.reconcile_decision(decision, todo)
        assert updated.status == TodoStatus.NEEDS_MORE_WORK

    @pytest.mark.asyncio
    async def test_review_in_process_calls_review_return_via_to_thread(self):
        """_review_in_process must NOT block the event loop: review_return must be
        invoked through asyncio.to_thread, not called directly."""
        from general_ludd.schemas.task_decision import TaskDecision

        decision = TaskDecision(
            return_id="RET-NB-001",
            matched_todo_id="TODO-NB-001",
            decision="complete",
            confidence=0.99,
        )

        reviewer = MagicMock()
        reviewer.review_return.return_value = decision

        todo_repo = AsyncMock()
        session = AsyncMock()
        session.flush = AsyncMock()
        db_result = MagicMock()
        db_result.scalars.return_value.all.return_value = []
        session.execute.return_value = db_result

        loop = EventLoop(
            reviewer=reviewer,
            todo_repo=todo_repo,
            session=session,
        )

        # Build a minimal tr-like object that _review_in_process accepts.
        tr = MagicMock()
        tr.return_id = "RET-NB-001"
        tr.todo_id = "TODO-NB-001"
        tr.job_id = "JOB-NB-001"
        tr.playbook = "noop.yml"
        tr.queue = "model"
        tr.work_type = "review"
        tr.exit_code = 0
        tr.result_summary = "all good"

        to_thread_calls: list = []

        async def _fake_to_thread(fn, *args, **kwargs):
            to_thread_calls.append((fn, args, kwargs))
            # Actually run the synchronous fn so apply_decision has a real decision.
            return fn(*args, **kwargs)

        with patch("general_ludd.event_loop.loop.asyncio.to_thread", _fake_to_thread), patch(
            "general_ludd.review.decision_applier.apply_decision",
            new_callable=AsyncMock,
        ):
            await loop._review_in_process(tr)

        # Exactly one to_thread call, and it must be review_return.
        assert len(to_thread_calls) == 1, (
            f"Expected 1 asyncio.to_thread call for review_return, got {len(to_thread_calls)}"
        )
        called_fn, _called_args, _called_kwargs = to_thread_calls[0]
        assert called_fn is reviewer.review_return, (
            "review_return was not called via asyncio.to_thread — event loop would block"
        )
