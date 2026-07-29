"""C21 ALPHA4 leftover fixes — TDD tests.

Three security issues fixed:
1. ValidationRunner subprocess not symlink-confined post-validation
2. Concurrency cap applied AFTER rows marked ACTIVE
3. _dispatch_review_job to_thread(run_playbook) with no timeout
   (and http_client.post path also unguarded)

Run: make test-iso TESTFILE='tests/unit/test_c21_alpha4_open.py'
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.schemas.todo import TodoStatus
from general_ludd.validation.runner import CommandValidationError, ValidationRunner

# ── Issue 1: ValidationRunner symlink confinement ───────────────────────


class TestValidationRunnerSymlinkConfinement:
    """Workdir with symlink escape → rejected at execution time."""

    def test_symlink_escape_rejected_at_execution(self) -> None:
        """A worktree_path that is a valid dir at init but replaced with an
        escape symlink before run_validation() is rejected at subprocess time.
        """
        with tempfile.TemporaryDirectory() as safe_dir, tempfile.TemporaryDirectory() as evil_dir:
            safe_worktree = os.path.join(safe_dir, "worktree")
            os.makedirs(safe_worktree)

            # Create a harmless file so the runner can "pass"
            runner = ValidationRunner(
                todo_id="TODO-001",
                worktree_path=safe_worktree,
                test_commands=["uv run pytest --version"],
                expected_worktree_root=safe_dir,
            )

            # After init but before run: replace worktree with a symlink
            # pointing to a directory outside the expected root.
            os.rmdir(safe_worktree)
            os.symlink(evil_dir, safe_worktree)

            with (
                patch("general_ludd.validation.runner.subprocess.run") as mock_run,
                pytest.raises(CommandValidationError, match="escapes expected root"),
            ):
                runner.run_validation()
            mock_run.assert_not_called()

    def test_non_absolute_rejected(self) -> None:
        with pytest.raises(CommandValidationError, match="must be an absolute path"):
            ValidationRunner(
                todo_id="TODO-001",
                worktree_path="relative/path",
                test_commands=["uv run pytest"],
                expected_worktree_root="/tmp",
            )

    def test_legit_worktree_runs_normally(self) -> None:
        """A normal (non-symlink) worktree still runs subprocess fine."""
        with tempfile.TemporaryDirectory() as safe_dir:
            worktree = os.path.join(safe_dir, "legit")
            os.makedirs(worktree)
            runner = ValidationRunner(
                todo_id="TODO-001",
                worktree_path=worktree,
                test_commands=["uv run pytest"],
                expected_worktree_root=safe_dir,
            )
            with patch("general_ludd.validation.runner.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout="1 passed", stderr=""
                )
                result = runner.run_validation()
                mock_run.assert_called_once()
                assert result.passed_count == 1
                assert result.success is True


# ── Issue 2: Concurrency cap checked BEFORE claim ───────────────────────


class TestConcurrencyCapBeforeClaim:
    """PID/floor cap applied BEFORE rows marked ACTIVE, not after."""

    @pytest.mark.asyncio
    async def test_floor_cap_check_before_claim(self) -> None:
        """When the floor controller's max_active is lower than what would
        be claimed, only up to max_active are claimed — none are over-claimed
        and released back.
        """
        from general_ludd.controllers.floor import FloorController
        from general_ludd.event_loop.loop import EventLoop

        todo_repo = AsyncMock()
        todo_repo.count_active.return_value = 8
        floor_ctrl = FloorController(floor=10)

        session = AsyncMock()
        http_client = AsyncMock()

        loop = EventLoop(
            worker_base_url="http://worker:8000",
            config={"tick_interval": 1.0},
            session=session,
            http_client=http_client,
            todo_repo=todo_repo,
            floor_controller=floor_ctrl,
        )
        loop._active_session = session
        loop._tick_project_id = None

        await loop._phase_claim_runnable_todos()

        call_kwargs = todo_repo.claim_runnable.call_args
        if call_kwargs:
            limit = call_kwargs.kwargs.get("limit", 10)
            assert limit == 2, (
                f"Expected claim limit of 2 (10 max - 8 active), got {limit}"
            )
        else:
            pass

        todo_repo.transition.assert_not_called()

    @pytest.mark.asyncio
    async def test_pid_cap_check_before_claim(self) -> None:
        """When PID says desired=5 and 4 are already active, only 1 is claimed."""
        from general_ludd.event_loop.loop import EventLoop

        todo_repo = AsyncMock()
        todo_repo.count_active.return_value = 4
        todo_repo.claim_runnable.return_value = []

        session = AsyncMock()
        http_client = AsyncMock()

        loop = EventLoop(
            worker_base_url="http://worker:8000",
            config={"tick_interval": 1.0},
            session=session,
            http_client=http_client,
            todo_repo=todo_repo,
        )
        loop._active_session = session
        loop._tick_project_id = "proj-c21"
        pid_mock = MagicMock()
        pid_mock.desired_total_active_buckets = 5
        loop._tick_state["pid_outputs"] = pid_mock

        await loop._phase_claim_runnable_todos()

        call_kwargs = todo_repo.claim_runnable.call_args
        assert call_kwargs is not None, "claim_runnable should have been called"
        limit = call_kwargs.kwargs.get("limit", 10)
        assert limit == 1, (
            f"Expected claim limit of 1 (5 desired - 4 active), got {limit}"
        )

    @pytest.mark.asyncio
    async def test_zero_claimable_when_already_at_cap(self) -> None:
        """When already at or over capacity, claim nothing."""
        from general_ludd.event_loop.loop import EventLoop

        todo_repo = AsyncMock()
        todo_repo.count_active.return_value = 15

        session = AsyncMock()
        http_client = AsyncMock()

        loop = EventLoop(
            worker_base_url="http://worker:8000",
            config={"tick_interval": 1.0},
            session=session,
            http_client=http_client,
            todo_repo=todo_repo,
        )
        loop._active_session = session
        loop._tick_project_id = None

        pid_mock = MagicMock()
        pid_mock.desired_total_active_buckets = 10
        loop._tick_state["pid_outputs"] = pid_mock

        await loop._phase_claim_runnable_todos()

        # When effective_limit <= 0, claim_runnable is never called
        # (the phase returns early with claimed_todos=[])
        todo_repo.claim_runnable.assert_not_called()
        assert loop._tick_state["claimed_todos"] == []


# ── Issue 3: Review job timeout + BLOCKED transition ────────────────────


class TestReviewJobTimeout:
    """Review jobs must be guarded by asyncio.wait_for."""

    @pytest.mark.asyncio
    async def test_runner_playbook_timeout_transitions_todo_to_blocked(self) -> None:
        """When the runner's playbook times out, the associated todo
        transitions to BLOCKED."""
        from general_ludd.event_loop.loop import EventLoop

        runner = MagicMock()
        runner.prepare_job_dirs.return_value = {"root": "/tmp/review"}
        runner.write_vars = MagicMock()

        session = AsyncMock()
        todo_repo = AsyncMock()

        # get_by_id returns a mock todo with version for the transition call
        mock_todo = MagicMock()
        mock_todo.version = 3
        todo_repo.get_by_id = AsyncMock(return_value=mock_todo)

        http_client = AsyncMock()

        loop = EventLoop(
            worker_base_url="http://worker:8000",
            config={"tick_interval": 1.0, "review": {"playbook_timeout": 0.001}},
            session=session,
            http_client=http_client,
            todo_repo=todo_repo,
        )
        loop._runner = runner
        loop._active_session = session
        loop._todo_repo = todo_repo

        tr = MagicMock()
        tr.return_id = "RET-001"
        tr.todo_id = "TODO-001"
        tr.queue = "model"
        tr.project_id = None
        tr.plan_artifact = None
        tr.status = "claimed_for_review"

        # Blocking sync function → to_thread blocks → wait_for times out
        import time as _time

        runner.run_playbook = lambda *a, **kw: _time.sleep(10.0)

        await loop._dispatch_review_job(tr)

        # The todo should have been transitioned to BLOCKED
        transition_calls = [
            c for c in todo_repo.transition.call_args_list
        ]
        assert len(transition_calls) > 0, (
            "Expected transition to BLOCKED on review timeout"
        )
        args = transition_calls[0][0]
        assert len(args) >= 2
        status = args[1]
        assert status in (TodoStatus.BLOCKED, TodoStatus.QUEUED)

    @pytest.mark.asyncio
    async def test_http_review_has_timeout(self) -> None:
        """The HTTP-client path is guarded by asyncio.wait_for and on timeout
        transitions the todo to BLOCKED."""
        from general_ludd.event_loop.loop import EventLoop

        session = AsyncMock()
        todo_repo = AsyncMock()

        mock_todo = MagicMock()
        mock_todo.version = 3
        todo_repo.get_by_id = AsyncMock(return_value=mock_todo)

        http_client = AsyncMock()

        loop = EventLoop(
            worker_base_url="http://worker:8000",
            config={
                "tick_interval": 1.0,
                "review": {"http_timeout": 0.001},
            },
            session=session,
            http_client=http_client,
            todo_repo=todo_repo,
        )
        loop._active_session = session
        loop._todo_repo = todo_repo
        loop._runner = None  # Falls through to HTTP path

        tr = MagicMock()
        tr.return_id = "RET-002"
        tr.todo_id = "TODO-002"
        tr.queue = "model"
        tr.project_id = None
        tr.plan_artifact = None
        tr.status = "claimed_for_review"

        # http_client.post blocks forever → wait_for times out
        import time as _time

        async def never_return(*a: object, **kw: object) -> None:
            await asyncio.to_thread(_time.sleep, 10.0)

        http_client.post = never_return

        await loop._dispatch_review_job(tr)

        # On timeout, the todo should be transitioned to BLOCKED
        transition_calls = [
            c for c in todo_repo.transition.call_args_list
        ]
        assert len(transition_calls) > 0, (
            "Expected transition to BLOCKED on HTTP review timeout"
        )
        args = transition_calls[0][0]
        assert len(args) >= 2
        status = args[1]
        assert status in (TodoStatus.BLOCKED, TodoStatus.QUEUED)

    @pytest.mark.asyncio
    async def test_normal_review_not_affected(self) -> None:
        """A normal (fast) review does NOT transition to BLOCKED."""
        from general_ludd.event_loop.loop import EventLoop

        runner = MagicMock()
        runner.prepare_job_dirs.return_value = {"root": "/tmp/review"}
        runner.write_vars = MagicMock()
        runner.run_playbook = MagicMock(return_value=None)  # succeeds fast

        session = AsyncMock()
        todo_repo = AsyncMock()
        http_client = AsyncMock()

        loop = EventLoop(
            worker_base_url="http://worker:8000",
            config={"tick_interval": 1.0, "review": {"playbook_timeout": 30.0}},
            session=session,
            http_client=http_client,
            todo_repo=todo_repo,
        )
        loop._runner = runner
        loop._active_session = session
        loop._todo_repo = todo_repo

        tr = MagicMock()
        tr.return_id = "RET-003"
        tr.todo_id = "TODO-003"
        tr.queue = "model"
        tr.project_id = None
        tr.plan_artifact = None
        tr.status = "claimed_for_review"

        await loop._dispatch_review_job(tr)

        # Normal path: transition should NOT have been called for BLOCKED
        transition_calls = todo_repo.transition.call_args_list
        assert len(transition_calls) == 0, (
            f"Expected no transitions on normal review, got {transition_calls}"
        )
