"""Unit tests for event loop."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.event_loop.lease import reclaim_expired_leases
from general_ludd.event_loop.loop import PHASE_ORDER, EventLoop, _compute_todo_estimate
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
            "run_scheduler",
            "sdlc_gate",
            "claim_runnable_todos",
            "evaluate_rules",
            "dispatch_execute_jobs",
            "reconcile_completed_decisions",
            "refresh_model_performance",
            "check_compute_utilization",
            "self_improve",
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
    async def test_dispatch_review_job_playbook_timeout_releases_claim(self):
        """ALPHA4 residual: _dispatch_review_job's runner-path playbook run
        had no timeout, so a hung ``run_playbook`` blocked that return
        forever AND left it stuck at 'claimed_for_review' (no reaper
        re-claims that status; claim_unreviewed only selects 'created').

        Proof: a run_playbook that outlives a tiny configured
        ``review.playbook_timeout`` must not raise out of
        _dispatch_review_job, and must release the claim back to 'created'
        (with the session flushed) so a later claim_unreviewed can re-claim
        it — the documented failure state instead of a permanent stall.
        """
        import time as _time

        runner = MagicMock()
        runner.prepare_job_dirs.return_value = {"root": "/tmp/review-timeout-job"}
        runner.write_vars.return_value = None

        def _slow_run_playbook(*, playbook_name, private_data_dir):
            _time.sleep(0.05)
            return {"rc": 0}

        runner.run_playbook.side_effect = _slow_run_playbook

        loop, mocks = _make_loop(
            runner=runner,
            config={"tick_interval": 1.0, "review": {"playbook_timeout": 0.005}},
        )
        tr = TaskReturn(
            return_id="RET-TIMEOUT",
            job_id="JOB-TIMEOUT",
            playbook="noop.yml",
            queue="core",
            status=TaskReturnStatus.CLAIMED_FOR_REVIEW,
        )

        await loop._dispatch_review_job(tr)

        assert tr.status == "created"
        mocks["session"].flush.assert_awaited()

    @pytest.mark.asyncio
    async def test_dispatch_review_job_runner_path_completes_within_timeout(self):
        """Sanity companion: a normal (fast) runner-path playbook still
        completes successfully under the new asyncio.wait_for wrapper —
        the timeout guard must not interfere with the happy path."""
        runner = MagicMock()
        runner.prepare_job_dirs.return_value = {"root": "/tmp/review-ok-job"}
        runner.write_vars.return_value = None
        runner.run_playbook.return_value = {"rc": 0}

        loop, _mocks = _make_loop(
            runner=runner,
            config={"tick_interval": 1.0, "review": {"playbook_timeout": 5.0}},
        )
        tr = TaskReturn(
            return_id="RET-OK",
            job_id="JOB-OK",
            playbook="noop.yml",
            queue="core",
            status=TaskReturnStatus.CLAIMED_FOR_REVIEW,
        )

        await loop._dispatch_review_job(tr)

        runner.run_playbook.assert_called_once()
        # Happy path must not touch/release the claim.
        assert tr.status == TaskReturnStatus.CLAIMED_FOR_REVIEW

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
            return_value=("GENERATED OUTPUT", None),
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
    async def test_dispatch_execute_job_attributes_project_id_to_trace(self):
        """A generation dispatch for a project todo must stamp the trace's project_id.

        Task #19 tenant-attribution regression: the EventLoop built the
        ExecutionTrace with NO project_id, so every real trace recorded
        project_id=None and was EXCLUDED by the tenant-boundary filter in
        RecentTracesBuffer.recent()/snapshot(). A project-scoped /api/traces
        caller therefore saw none of its own traces. This asserts the trace now
        carries the todo's project_id AND that a scoped read includes it while a
        different project's scoped read excludes it.
        """
        from general_ludd.observability.recorder import AutoBenchmarkRecorder
        from general_ludd.observability.trace_store import RecentTracesBuffer

        buffer = RecentTracesBuffer()
        recorder = AutoBenchmarkRecorder(benchmark_repo=None, trace_buffer=buffer)

        runner = MagicMock()
        runner.prepare_job_dirs.return_value = {"root": "/tmp/exec-trace-proj-test"}

        loop, _ = _make_loop(runner=runner, model_gateway=MagicMock())
        loop._benchmark_recorder = recorder

        todo = Todo(
            title="generate a thing",
            description="please",
            status=TodoStatus.ACTIVE,
            work_type="code",
            project_id="proj-alpha",
        )

        with patch(
            "general_ludd.event_loop.loop.invoke_model_for_generation",
            return_value=("GENERATED OUTPUT", None),
        ):
            await loop._dispatch_execute_job(todo)

        # Drain the fire-and-forget background tasks (record_from_trace runs in one).
        for _t in list(loop._background_tasks):
            await _t

        # 1) The recorded ExecutionTrace carries the todo's project_id.
        recent = buffer.recent()
        assert recent, "trace buffer must have captured a trace"
        assert recent[0].project_id == "proj-alpha"

        # 2) A project-scoped read INCLUDES the trace (previously excluded).
        scoped = buffer.snapshot(project_id="proj-alpha")
        assert scoped["count"] > 0
        assert all(
            r["project_id"] == "proj-alpha" for r in scoped["recent"]
        ), scoped["recent"]

        # 3) A DIFFERENT project's scoped read EXCLUDES the trace (tenant boundary).
        other = buffer.snapshot(project_id="proj-beta")
        assert other["count"] == 0, other["recent"]

    @pytest.mark.asyncio
    async def test_dispatch_execute_job_trace_project_none_when_unscoped(self):
        """Regression: a todo with no project still records a trace with project_id None.

        None stays valid for genuinely project-less traces — an unscoped/global
        caller still sees it, but a scoped caller does not.
        """
        from general_ludd.observability.recorder import AutoBenchmarkRecorder
        from general_ludd.observability.trace_store import RecentTracesBuffer

        buffer = RecentTracesBuffer()
        recorder = AutoBenchmarkRecorder(benchmark_repo=None, trace_buffer=buffer)

        runner = MagicMock()
        runner.prepare_job_dirs.return_value = {"root": "/tmp/exec-trace-none-test"}

        loop, _ = _make_loop(runner=runner, model_gateway=MagicMock())
        loop._benchmark_recorder = recorder

        todo = Todo(
            title="generate a thing",
            description="please",
            status=TodoStatus.ACTIVE,
            work_type="code",
        )  # no project_id

        with patch(
            "general_ludd.event_loop.loop.invoke_model_for_generation",
            return_value=("GENERATED OUTPUT", None),
        ):
            await loop._dispatch_execute_job(todo)

        for _t in list(loop._background_tasks):
            await _t

        recent = buffer.recent()
        assert recent, "trace buffer must have captured a trace"
        assert recent[0].project_id is None
        # Unscoped/global caller still sees the None-project trace.
        assert buffer.snapshot()["count"] > 0
        # A scoped caller does NOT see the unattributed trace.
        assert buffer.snapshot(project_id="proj-alpha")["count"] == 0

    @pytest.mark.asyncio
    async def test_event_loop_claims_runnable_todos(self):
        loop, mocks = _make_loop()
        loop._tick_project_id = "proj-test"
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
        loop._tick_project_id = "proj-test"
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
    async def test_event_loop_reconcile_decision_complete(self, tmp_path):
        import json as _json

        # Create a real artifact file so the evidence gate can verify it.
        artifact = tmp_path / "proof.txt"
        artifact.write_text("verified")

        loop, mocks = _make_loop(
            config={"tick_interval": 1.0, "repo_root": str(tmp_path)}
        )
        todo_model = MagicMock()
        todo_model.todo_id = "TODO-001"
        todo_model.status = "reviewing_return"
        todo_model.version = 1
        todo_model.project_id = None
        decision_row = MagicMock()
        decision_row.return_id = "RET-001"
        decision_row.matched_todo_id = "TODO-001"
        decision_row.decision = "complete"
        decision_row.confidence = 0.95
        decision_row.project_id = None
        decision_row.evidence_refs = _json.dumps(["artifact:proof.txt"])
        decision_row.audit_notes = "[]"
        result_mock = MagicMock()
        result_mock.scalars().all.return_value = [decision_row]
        mocks["session"].execute.return_value = result_mock
        mocks["todo_repo"].get_by_ids = AsyncMock(return_value={"TODO-001": todo_model})
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
        mocks["todo_repo"].get_by_ids = AsyncMock(return_value={"TODO-001": todo_model})
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
        assert result["phases_completed"] == len(PHASE_ORDER)

    @pytest.mark.asyncio
    async def test_event_loop_serializes_concurrent_ticks(self):
        loop, _ = _make_loop()
        active = 0
        peak_active = 0
        entered = asyncio.Event()
        release = asyncio.Event()

        async def blocking_phases():
            nonlocal active, peak_active
            active += 1
            peak_active = max(peak_active, active)
            entered.set()
            await release.wait()
            active -= 1

        loop._run_phases = blocking_phases  # type: ignore[method-assign]
        first = asyncio.create_task(loop.tick())
        await entered.wait()
        second = asyncio.create_task(loop.tick())
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert peak_active == 1
        release.set()
        await asyncio.gather(first, second)

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
        session.add = MagicMock()
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


class TestOneProjectPerTick:
    """W3.14: claim and review phases share the same project selection per tick."""

    @pytest.mark.asyncio
    async def test_select_project_called_once_per_tick(self):
        """_select_tick_project_id() must be called at most once per tick, not once per phase."""
        from unittest.mock import patch

        loop, _ = _make_loop()
        call_count = 0

        def _fake_select() -> str:
            nonlocal call_count
            call_count += 1
            return "project-A"

        with patch.object(loop, "_select_tick_project_id", side_effect=_fake_select):
            await loop.tick()

        # Must have been called exactly once — not once per phase
        assert call_count <= 1, (
            f"_select_tick_project_id called {call_count} times in one tick; "
            "must be called once and the result shared across phases (W3.14)"
        )

    @pytest.mark.asyncio
    async def test_claim_and_review_use_same_project(self):
        """Claim and review phases must operate on the same project in one tick."""
        from unittest.mock import patch

        loop, _ = _make_loop()
        seen_projects: list[str | None] = []

        async def _fake_claim_phase() -> None:
            seen_projects.append(loop._tick_project_id)

        async def _fake_review_phase() -> None:
            seen_projects.append(loop._tick_project_id)

        with patch.object(loop, "_phase_claim_runnable_todos", side_effect=_fake_claim_phase), \
             patch.object(loop, "_phase_claim_unreviewed_task_returns", side_effect=_fake_review_phase), \
             patch.object(loop, "_select_tick_project_id", return_value="proj-X"):
            await loop.tick()

        # Both phases must have seen the same project (or both saw None)
        assert len(set(seen_projects)) <= 1, (
            f"Claim and review phases saw different projects: {seen_projects} (W3.14 violated)"
        )

    @pytest.mark.asyncio
    async def test_tick_project_resets_after_tick(self):
        """_tick_project_id must be reset to None after each tick completes."""
        from unittest.mock import patch

        loop, _ = _make_loop()

        with patch.object(loop, "_select_tick_project_id", return_value="proj-Y"):
            await loop.tick()

        assert loop._tick_project_id is None, (
            "_tick_project_id must be reset to None after tick completes (W3.14)"
        )

    @pytest.mark.asyncio
    async def test_select_project_invoked_once_even_when_weighting_varies(self):
        """M14/W3.14: the underlying project_manager.select_project() must be
        invoked exactly ONCE per tick, and the SAME selected project must reach
        both the claim phase and the review phase.

        This stubs select_project() to return a DIFFERENT project on every call
        (the worst case for a random/weighted selector): if any phase called
        select_project() independently, the two phases would target different
        projects in the same tick — the cross-project incoherence W3.14 fixes.
        Asserting exactly one call + a single shared project proves selection is
        hoisted once per tick and threaded down, not re-rolled per phase.
        """
        # A project_manager whose select_project() yields a fresh project each
        # call, so any second invocation would change the selected project.
        projects = [MagicMock(project_id=f"proj-{i}") for i in range(10)]
        project_manager = MagicMock()
        project_manager.select_project.side_effect = projects

        loop, _ = _make_loop(project_manager=project_manager)

        seen_projects: list[str | None] = []

        async def _record_claim_phase() -> None:
            seen_projects.append(loop._tick_project_id)

        async def _record_review_phase() -> None:
            seen_projects.append(loop._tick_project_id)

        with patch.object(
            loop, "_phase_claim_runnable_todos", side_effect=_record_claim_phase
        ), patch.object(
            loop,
            "_phase_claim_unreviewed_task_returns",
            side_effect=_record_review_phase,
        ):
            await loop.tick()

        # select_project() called exactly once for the whole tick — never re-rolled
        # per phase (which, given the side_effect, would have produced proj-1, etc.).
        assert project_manager.select_project.call_count == 1, (
            "project_manager.select_project() must be called exactly once per tick; "
            f"got {project_manager.select_project.call_count} (W3.14: select once, share)"
        )
        # Both phases observed the SAME selected project (the first one).
        assert seen_projects == ["proj-0", "proj-0"], (
            "claim and review phases must share the single per-tick project; "
            f"got {seen_projects} (W3.14 cross-project incoherence)"
        )
    @pytest.mark.asyncio
    async def test_projectless_tick_claims_unscoped_todos(self):
        loop, mocks = _make_loop(project_manager=None)
        mocks["todo_repo"].claim_runnable.return_value = []

        await loop._phase_claim_runnable_todos()

        mocks["todo_repo"].claim_runnable.assert_awaited_once_with(limit=10, project_id=None)
        assert loop._tick_state["claimed_todos"] == []


class TestSpendLimiterCharges:
    """Bug 1: SpendLimiter must record spend (try_charge), not just check (would_exceed)."""

    @pytest.mark.asyncio
    async def test_dispatch_records_charge_in_spend_window(self):
        """After a successful dispatch, window_spend() must be nonzero."""
        from general_ludd.controllers.spend_limiter import SpendLimiter

        limiter = SpendLimiter(limit_usd=10.0, window_seconds=60, clock=lambda: 0.0)
        runner = MagicMock()
        runner.prepare_job_dirs.return_value = {"root": "/tmp/test-spend"}

        loop, _ = _make_loop(runner=runner, model_gateway=MagicMock(), spend_limiter=limiter)

        todo = Todo(
            title="charge test",
            todo_id="TODO-CHARGE-1",
            status=TodoStatus.ACTIVE,
            work_type="code",
        )

        assert limiter.window_spend() == 0.0, "pre-condition: window must be empty"

        with patch(
            "general_ludd.event_loop.loop.invoke_model_for_generation",
            return_value=("OUTPUT", None),
        ):
            await loop._dispatch_execute_job(todo)

        assert limiter.window_spend() > 0.0, (
            "SpendLimiter.window_spend() must be nonzero after a successful dispatch "
            "(try_charge must record; would_exceed alone does not)"
        )

    @pytest.mark.asyncio
    async def test_dispatch_skipped_when_spend_cap_reached(self):
        """Dispatch must be skipped when try_charge() refuses (cap already exhausted)."""
        from general_ludd.controllers.spend_limiter import SpendLimiter

        # Tiny cap: pre-fill so any additional charge exceeds it.
        limiter = SpendLimiter(limit_usd=0.00001, window_seconds=60, clock=lambda: 0.0)
        limiter.record(0.00001, kind="token", at=0.0)

        runner = MagicMock()
        runner.prepare_job_dirs.return_value = {"root": "/tmp/test-cap"}

        loop, _ = _make_loop(runner=runner, model_gateway=MagicMock(), spend_limiter=limiter)

        todo = Todo(
            title="over-cap test",
            todo_id="TODO-OVERCAP-1",
            status=TodoStatus.ACTIVE,
            work_type="code",
        )

        with patch(
            "general_ludd.event_loop.loop.invoke_model_for_generation",
            return_value=("OUTPUT", None),
        ) as mock_invoke:
            await loop._dispatch_execute_job(todo)

        assert not mock_invoke.called, (
            "invoke_model_for_generation must NOT be called when the spend cap is exhausted"
        )


class TestPidCapRelease:
    """C21: PID cap is now applied BEFORE the CAS claim in
    _phase_claim_runnable_todos, not after in _phase_dispatch_execute_jobs.
    When cap is set, only cap-many todos are claimed; none are over-claimed
    and released back."""

    @pytest.mark.asyncio
    async def test_pid_cap_checked_before_claim_not_after_dispatch(self):
        """PID cap clamp happens at claim time: limit passed to claim_runnable
        reflects (desired - currently_active)."""
        loop, mocks = _make_loop()
        loop._tick_project_id = "proj-test"

        mocks["todo_repo"].count_active.return_value = 2

        pid_outputs = MagicMock()
        pid_outputs.desired_total_active_buckets = 3
        loop._tick_state["pid_outputs"] = pid_outputs

        await loop._phase_claim_runnable_todos()

        call = mocks["todo_repo"].claim_runnable.call_args
        assert call is not None, "claim_runnable should have been called"
        limit = call.kwargs.get("limit", 10)
        assert limit == 1, (
            f"Expected claim limit=1 (3 desired - 2 active), got {limit}"
        )


class TestLedgerBounds:
    """Bug 3: _applied_decisions and _active_traces must stay bounded."""

    @pytest.mark.asyncio
    async def test_applied_decisions_pruned_after_cap(self):
        """Filling _applied_decisions past _MAX_LEDGER_SIZE then reconciling one
        new decision must leave the set at or below _MAX_LEDGER_SIZE."""
        loop, mocks = _make_loop()

        # Pre-fill beyond the cap. P3: _applied_decisions is now an OrderedDict
        # used as a bounded LRU set, so seed via _ledger_add (which evicts as it
        # goes); to force an overfull pre-condition we bypass the helper and fill
        # the OrderedDict directly so the reconcile insert is what trims it.
        cap = loop._MAX_LEDGER_SIZE
        for i in range(cap + 1):
            loop._applied_decisions[f"fake-id:{i}"] = None

        assert len(loop._applied_decisions) > cap, "pre-condition: ledger must be overfull"

        # Build a fresh decision row not already in the set.
        # evidence_refs="[]" (empty JSON list): verify_completion downgrades to
        # needs_more_work, but the decision is still processed and _ledger_add fires
        # so the overfull ledger gets pruned — which is what this test verifies.
        decision_row = MagicMock()
        decision_row.id = 99999
        decision_row.matched_todo_id = "TODO-PRUNE-1"
        decision_row.decision = "complete"
        decision_row.return_id = "RET-PRUNE-1"
        decision_row.project_id = None
        decision_row.evidence_refs = "[]"
        decision_row.audit_notes = "[]"

        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [decision_row]
        mocks["session"].execute.return_value = result_mock

        todo_model = MagicMock()
        todo_model.todo_id = "TODO-PRUNE-1"
        todo_model.status = TodoStatus.REVIEWING_RETURN.value
        todo_model.version = 1
        todo_model.project_id = None
        mocks["todo_repo"].get_by_ids = AsyncMock(return_value={"TODO-PRUNE-1": todo_model})
        mocks["todo_repo"].transition = AsyncMock(return_value=MagicMock())

        await loop._phase_reconcile_completed_decisions()

        assert len(loop._applied_decisions) <= cap, (
            f"_applied_decisions must be pruned to <= {cap}; "
            f"got {len(loop._applied_decisions)}"
        )

    @pytest.mark.asyncio
    async def test_active_traces_pruned_after_cap(self):
        """Filling _active_traces past _MAX_ACTIVE_TRACES then dispatching one job
        must leave the dict at or below _MAX_ACTIVE_TRACES."""
        from general_ludd.observability.recorder import AutoBenchmarkRecorder
        from general_ludd.observability.trace_store import RecentTracesBuffer

        buffer = RecentTracesBuffer()
        recorder = AutoBenchmarkRecorder(benchmark_repo=None, trace_buffer=buffer)

        runner = MagicMock()
        runner.prepare_job_dirs.return_value = {"root": "/tmp/test-trace-prune"}

        loop, _ = _make_loop(runner=runner, model_gateway=MagicMock())
        loop._benchmark_recorder = recorder

        # Pre-fill beyond the cap.
        trace_cap = loop._MAX_ACTIVE_TRACES
        for i in range(trace_cap + 5):
            loop._active_traces[f"fake-trace-{i}"] = MagicMock()

        assert len(loop._active_traces) > trace_cap, "pre-condition: traces must be overfull"

        todo = Todo(
            title="prune traces test",
            todo_id="TODO-TRACE-PRUNE-1",
            status=TodoStatus.ACTIVE,
            work_type="code",
        )

        with patch(
            "general_ludd.event_loop.loop.invoke_model_for_generation",
            return_value=("GENERATED OUTPUT", None),
        ):
            await loop._dispatch_execute_job(todo)

        assert len(loop._active_traces) <= trace_cap, (
            f"_active_traces must be pruned to <= {trace_cap}; "
            f"got {len(loop._active_traces)}"
        )

    def test_ledger_add_stays_bounded_and_keeps_recent_lru(self):
        """P3: driving many inserts through _ledger_add must keep the ledger at
        or below _MAX_LEDGER_SIZE AND retain the MOST RECENT ids (LRU), never an
        arbitrary subset — so the idempotency guarantee holds across the window."""
        loop, _ = _make_loop()
        cap = loop._MAX_LEDGER_SIZE
        ledger = loop._applied_decisions

        # Drive cap + extra insertions of distinct keys.
        extra = 250
        for i in range(cap + extra):
            loop._ledger_add(ledger, f"dec:{i}")

        # Bound holds exactly: never exceeds the cap.
        assert len(ledger) == cap, (
            f"ledger must stay == cap ({cap}) after {cap + extra} inserts; "
            f"got {len(ledger)}"
        )

        # LRU semantics: the most-recent `cap` keys survive; the oldest `extra`
        # were evicted FIFO (this is the correctness win over an unordered set
        # that could evict a still-recent id and re-open the re-apply window).
        for i in range(extra):  # the oldest `extra` keys must be gone
            assert f"dec:{i}" not in ledger, f"oldest key dec:{i} should be evicted"
        for i in range(extra, cap + extra):  # the most-recent `cap` keys survive
            assert f"dec:{i}" in ledger, f"recent key dec:{i} must survive"

    def test_ledger_add_reinsert_refreshes_recency(self):
        """P3: re-inserting an existing key moves it to the MRU end so it is not
        evicted as 'oldest' — protecting a still-touched idempotency id."""
        loop, _ = _make_loop()
        cap = loop._MAX_LEDGER_SIZE
        ledger = loop._pushed_work

        # Fill to exactly the cap.
        for i in range(cap):
            loop._ledger_add(ledger, f"work:{i}")
        # Re-touch the OLDEST key -> it should become most-recent and survive the
        # next eviction; instead the second-oldest (work:1) is dropped.
        loop._ledger_add(ledger, "work:0")
        loop._ledger_add(ledger, "work:new")

        assert len(ledger) == cap
        assert "work:0" in ledger, "re-touched key must be refreshed, not evicted"
        assert "work:new" in ledger, "newest key must be present"
        assert "work:1" not in ledger, "the now-oldest key must be the eviction victim"

    @pytest.mark.asyncio
    async def test_self_update_applies_audit_list_stays_bounded(self):
        """P3: the daemon-state self_update_applies audit list must not grow
        without bound — driving many successful self_update applies must leave it
        at or below _MAX_SELF_UPDATE_APPLIES with the most recent entries kept."""
        daemon_state: dict = {}
        loop, mocks = _make_loop(daemon_state=daemon_state)
        mocks["todo_repo"].transition = AsyncMock()

        cap = loop._MAX_SELF_UPDATE_APPLIES
        extra = 30

        # A successful reload verdict so each apply records an audit entry.
        # _apply_self_update_code reads getattr(reload_result, "status", "") and
        # treats "success" as ok -> appends to the audit list.
        ok_reload = MagicMock()
        ok_reload.status = "success"

        with patch(
            "general_ludd.event_loop.loop.SelfImprovementWorkflow"
        ) as MockWorkflow:
            instance = MockWorkflow.return_value
            instance.reload_if_needed.return_value = ok_reload

            for i in range(cap + extra):
                todo = Todo(
                    title=f"self-update {i}",
                    todo_id=f"SU-{i}",
                    status=TodoStatus.ACTIVE,
                    queue="self_update",
                    work_type="code",
                    version=1,
                    tags=[f"module:mod{i}", f"candidate:/tmp/cand{i}.py"],
                )
                await loop._apply_self_update_code(todo)

        applies = daemon_state["self_update_applies"]
        assert len(applies) == cap, (
            f"self_update_applies must be bounded to {cap}; got {len(applies)}"
        )
        # Most-recent entries survive; the oldest `extra` were trimmed from front.
        kept_ids = {entry["todo_id"] for entry in applies}
        assert f"SU-{cap + extra - 1}" in kept_ids, "newest apply must be retained"
        assert "SU-0" not in kept_ids, "oldest apply must be trimmed"


class TestBackgroundTaskTracking:
    """A3: _background_tasks must be mutated race-safely.

    The fire-and-forget benchmark/trace writes register via
    _track_background_task (strong-ref add + add_done_callback(discard)) so no
    running task is GC'd and the set drains to empty when they finish. Shutdown
    cancels + awaits a SNAPSHOT so a concurrent discard cannot raise
    "set changed size during iteration".
    """

    @pytest.mark.asyncio
    async def test_tracked_tasks_drain_to_empty_when_complete(self):
        """Many concurrently-registered tasks: none lost, set drains to empty."""
        import asyncio

        loop = EventLoop()

        async def _work(i: int) -> int:
            await asyncio.sleep(0)
            return i

        tasks = [asyncio.ensure_future(_work(i)) for i in range(50)]
        for t in tasks:
            loop._track_background_task(t)

        # All references held while pending (no GC drop).
        assert len(loop._background_tasks) == 50

        results = await asyncio.gather(*tasks)
        # Let the done-callbacks (set.discard) run.
        await asyncio.sleep(0)

        assert results == list(range(50))
        assert len(loop._background_tasks) == 0, (
            "every completed task must discard itself; set must drain to empty"
        )

    @pytest.mark.asyncio
    async def test_already_done_task_is_tracked_then_discarded(self):
        """Registering an already-completed task must not leak it (callback fires)."""
        import asyncio

        loop = EventLoop()

        async def _instant() -> str:
            return "done"

        task = asyncio.ensure_future(_instant())
        await task  # complete BEFORE tracking
        loop._track_background_task(task)
        # add_done_callback on an already-done task schedules the callback soon.
        await asyncio.sleep(0)

        assert task not in loop._background_tasks

    @pytest.mark.asyncio
    async def test_shutdown_cancels_and_drains_inflight_tasks(self):
        """shutdown() must cancel + await in-flight tasks, iterating a snapshot."""
        import asyncio

        loop = EventLoop()
        loop._running = True

        async def _long() -> None:
            await asyncio.sleep(3600)  # would hang without cancellation

        tasks = [asyncio.ensure_future(_long()) for _ in range(20)]
        for t in tasks:
            loop._track_background_task(t)

        assert len(loop._background_tasks) == 20

        # Must return promptly (cancels rather than waiting out the sleeps).
        await asyncio.wait_for(loop.shutdown(), timeout=5.0)

        assert loop._running is False
        assert all(t.cancelled() for t in tasks)
        await asyncio.sleep(0)
        assert len(loop._background_tasks) == 0

    @pytest.mark.asyncio
    async def test_drain_safe_under_concurrent_discard(self):
        """A drain must not raise even if tasks settle (discard) mid-iteration.

        This is the "set changed size during iteration" guard: the drain reads a
        snapshot under the lock, so callbacks discarding from the live set while
        we gather can never corrupt the iteration.
        """
        import asyncio

        loop = EventLoop()

        async def _quick(i: int) -> int:
            # Stagger completion so some tasks finish (and discard) while the
            # drain is in flight.
            await asyncio.sleep(0.001 * (i % 5))
            return i

        tasks = [asyncio.ensure_future(_quick(i)) for i in range(40)]
        for t in tasks:
            loop._track_background_task(t)

        # No exception (e.g. RuntimeError: set changed size during iteration).
        await asyncio.wait_for(loop._drain_background_tasks(cancel=False), timeout=5.0)
        await asyncio.sleep(0)
        assert len(loop._background_tasks) == 0


class TestHttpDispatchSkillBody:
    """W3.1: the HTTP-dispatched JobSpec must carry the resolved skill_body.

    The in-process runner path already threads ``skill_body`` into the job vars
    (loop.py ~1348). The HTTP-dispatch path (``self._runner is None``, daemon
    POSTs a JobSpec to the worker) must thread the SAME resolved variable into
    the JobSpec, or the worker calls the model with ``skill_body=None`` and gets
    a degraded prompt (missing the skill's system turn).
    """

    @pytest.mark.asyncio
    async def test_http_dispatch_jobspec_includes_skill_body(self):
        from general_ludd.skills.skill import Skill

        skill_reg = MagicMock()
        skill = Skill(
            name="tdd",
            body="Always write tests first",
            trigger_patterns=["test", "tdd"],
        )
        skill_reg.match_trigger.return_value = [skill]

        loop, mocks = _make_loop(skill_registry=skill_reg)
        # No in-process runner -> the HTTP-dispatch branch builds the JobSpec.
        assert loop._runner is None
        todo = Todo(
            title="Add TDD support for feature X",
            todo_id="TODO-SKILL-1",
            status=TodoStatus.ACTIVE,
            queue="core",
            work_type="code",
        )
        mocks["http_client"].post.return_value = MagicMock(status_code=202)
        loop._tick_state["claimed_todos"] = [todo]
        await loop._phase_dispatch_execute_jobs()
        call_args = mocks["http_client"].post.call_args
        payload = call_args[1]["json"]
        assert payload["skill_body"] == "Always write tests first"

    @pytest.mark.asyncio
    async def test_http_dispatch_jobspec_skill_body_none_without_match(self):
        skill_reg = MagicMock()
        skill_reg.match_trigger.return_value = []

        loop, mocks = _make_loop(skill_registry=skill_reg)
        assert loop._runner is None
        todo = Todo(
            title="Refactor database layer",
            todo_id="TODO-SKILL-2",
            status=TodoStatus.ACTIVE,
            queue="core",
            work_type="code",
        )
        mocks["http_client"].post.return_value = MagicMock(status_code=202)
        loop._tick_state["claimed_todos"] = [todo]
        await loop._phase_dispatch_execute_jobs()
        call_args = mocks["http_client"].post.call_args
        payload = call_args[1]["json"]
        assert payload["skill_body"] is None


class TestResolveRepoRoot:
    """Tests for EventLoop._resolve_repo_root — the per-project repo_root resolver
    that feeds verify_completion so the evidence gate VERIFIES instead of always-blocking."""

    def test_no_workspace_no_config_returns_none(self) -> None:
        loop = EventLoop(config={})
        assert loop._resolve_repo_root(None) is None
        assert loop._resolve_repo_root("proj-x") is None

    def test_config_repo_root_fallback(self, tmp_path) -> None:
        loop = EventLoop(config={"repo_root": str(tmp_path)})
        result = loop._resolve_repo_root(None)
        assert result == str(tmp_path)

    def test_config_repo_root_used_when_no_workspace_match(self, tmp_path) -> None:
        loop = EventLoop(config={"repo_root": str(tmp_path)})
        result = loop._resolve_repo_root("proj-unknown")
        assert result == str(tmp_path)

    def test_workspace_repo_dir_used_when_exists(self, tmp_path) -> None:
        ws = MagicMock()
        ws.repo_dir = tmp_path / "repo"
        (tmp_path / "repo").mkdir()

        loop = EventLoop(
            config={"repo_root": str(tmp_path / "fallback")},
            project_workspace={"proj-a": ws},
        )
        result = loop._resolve_repo_root("proj-a")
        assert result == str(tmp_path / "repo")

    def test_workspace_repo_dir_not_dir_falls_back_to_config(self, tmp_path) -> None:
        ws = MagicMock()
        ws.repo_dir = tmp_path / "repo_nonexistent"  # does not exist

        loop = EventLoop(
            config={"repo_root": str(tmp_path)},
            project_workspace={"proj-a": ws},
        )
        result = loop._resolve_repo_root("proj-a")
        assert result == str(tmp_path)

    def test_wrong_project_id_falls_back_to_config(self, tmp_path) -> None:
        ws = MagicMock()
        ws.repo_dir = tmp_path / "repo"
        (tmp_path / "repo").mkdir()

        loop = EventLoop(
            config={"repo_root": str(tmp_path / "cfg_root")},
            project_workspace={"proj-a": ws},
        )
        result = loop._resolve_repo_root("proj-b")  # different project
        assert result == str(tmp_path / "cfg_root")


class TestPhaseRefreshModelPerformance:
    """Tests for ``_phase_refresh_model_performance``."""

    @pytest.mark.asyncio
    async def test_skips_when_no_repo(self):
        loop, _ = _make_loop()
        repo = getattr(loop, "_model_perf_repo", None)
        assert repo is None
        # Should not raise.
        await loop._phase_refresh_model_performance()

    @pytest.mark.asyncio
    async def test_skips_on_wrong_tick(self):
        loop, _ = _make_loop()
        loop._model_perf_repo = AsyncMock()
        loop._model_performance_interval = 5
        loop._total_ticks = 2  # 2 % 5 != 0
        await loop._phase_refresh_model_performance()
        loop._model_perf_repo.refresh_recent_stats.assert_not_called()

    @pytest.mark.asyncio
    async def test_refreshes_on_correct_tick(self):
        loop, _mocks = _make_loop()
        session = _mocks["session"]
        factory = MagicMock()
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        factory.return_value = ctx
        loop._session_factory = factory
        repo = AsyncMock()
        repo.refresh_recent_stats.return_value = 3
        loop._model_perf_repo = repo
        loop._model_performance_interval = 5
        loop._total_ticks = 10  # 10 % 5 == 0
        await loop._phase_refresh_model_performance()
        repo.refresh_recent_stats.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handles_refresh_error_gracefully(self):
        loop, _mocks = _make_loop()
        session = _mocks["session"]
        factory = MagicMock()
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        factory.return_value = ctx
        loop._session_factory = factory
        repo = AsyncMock()
        repo.refresh_recent_stats.side_effect = RuntimeError("db fail")
        loop._model_perf_repo = repo
        loop._model_performance_interval = 1
        loop._total_ticks = 5
        await loop._phase_refresh_model_performance()
        repo.refresh_recent_stats.assert_awaited_once()


class TestComputeTodoEstimate:
    def test_low_resource_no_confidence(self):
        todo = type("Todo", (), {"resource_profile": "low_resource", "confidence": None})()
        assert _compute_todo_estimate(todo) == 0.05

    def test_high_resource_confidence_08(self):
        todo = type("Todo", (), {"resource_profile": "high_resource", "confidence": 0.8})()
        assert _compute_todo_estimate(todo) == 0.7

    def test_medium_resource_confidence_10(self):
        todo = type("Todo", (), {"resource_profile": "medium_resource", "confidence": 1.0})()
        assert _compute_todo_estimate(todo) == 0.125
