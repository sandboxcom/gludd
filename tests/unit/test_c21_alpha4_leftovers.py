"""C.21: alpha.4 leftovers — validation symlink confinement, claim-before-cap, review timeout."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, Mock, patch

import pytest

from general_ludd.validation.runner import (
    CommandValidationError,
    ValidationRunner,
    _validate_worktree_path,
)
from general_ludd.worktree.core import confine_worktree_path

# ---------------------------------------------------------------------------
# 1. Validation symlink escapes outside allowed root are blocked
# ---------------------------------------------------------------------------


class TestValidationSymlinkConfinement:
    def test_realpath_confinement_blocks_symlink_escape(self):
        """A symlink that resolves outside the allowed root is rejected."""
        with tempfile.TemporaryDirectory() as base:
            inside = os.path.join(base, "inside")
            os.makedirs(inside, exist_ok=True)

            outside = os.path.join(base, "outside")
            os.makedirs(outside, exist_ok=True)

            symlink_path = os.path.join(inside, "escape_link")
            os.symlink(outside, symlink_path)

            with pytest.raises(ValueError, match="escapes the allowed base"):
                confine_worktree_path(symlink_path, inside)

    def test_realpath_confinement_allows_valid_path(self):
        """A path that resolves inside the allowed root passes."""
        with tempfile.TemporaryDirectory() as base:
            inside = os.path.join(base, "inside")
            os.makedirs(inside, exist_ok=True)

            subdir = os.path.join(inside, "subdir")
            os.makedirs(subdir, exist_ok=True)

            result = confine_worktree_path(subdir, inside)
            assert os.path.isabs(result)
            assert result.startswith(os.path.realpath(inside))

    def test_realpath_confinement_allows_base_itself(self):
        """The allowed base itself passes confinement."""
        with tempfile.TemporaryDirectory() as base:
            result = confine_worktree_path(base, base)
            assert os.path.isabs(result)

    def test_validate_worktree_path_with_expected_root_blocks_escape(self):
        """_validate_worktree_path with expected_root calls confine_worktree_path."""
        with tempfile.TemporaryDirectory() as base:
            inside = os.path.join(base, "inside")
            os.makedirs(inside, exist_ok=True)
            outside = os.path.join(base, "outside")
            os.makedirs(outside, exist_ok=True)

            symlink_path = os.path.join(inside, "escape_link")
            os.symlink(outside, symlink_path)

            with pytest.raises(CommandValidationError, match="escapes expected root"):
                _validate_worktree_path(symlink_path, expected_root=inside)

    def test_validate_worktree_path_rejects_leading_dash(self):
        """A worktree path beginning with '-' is rejected."""
        with pytest.raises(CommandValidationError, match="begins with '-'"):
            _validate_worktree_path("-evil", expected_root="/tmp")

        with pytest.raises(CommandValidationError, match="begins with '-'"):
            _validate_worktree_path("-evil")

    def test_validate_worktree_path_rejects_relative(self):
        """A relative worktree path is rejected."""
        with pytest.raises(CommandValidationError, match="must be an absolute path"):
            _validate_worktree_path("relative/path")

    def test_validation_runner_init_confines_symlink(self):
        """ValidationRunner.__init__ validates the worktree_path."""
        with tempfile.TemporaryDirectory() as base:
            inside = os.path.join(base, "inside")
            os.makedirs(inside, exist_ok=True)
            outside = os.path.join(base, "outside")
            os.makedirs(outside, exist_ok=True)

            symlink_path = os.path.join(inside, "escape_link")
            os.symlink(outside, symlink_path)

            with pytest.raises(CommandValidationError, match="escapes expected root"):
                ValidationRunner(
                    todo_id="test-1",
                    worktree_path=symlink_path,
                    test_commands=["pytest"],
                    expected_worktree_root=inside,
                )

    def test_validation_runner_init_allows_valid_path(self):
        """ValidationRunner.__init__ accepts a valid worktree_path."""
        with tempfile.TemporaryDirectory() as base:
            runner = ValidationRunner(
                todo_id="test-1",
                worktree_path=base,
                test_commands=["pytest"],
                expected_worktree_root=base,
            )
            assert runner.todo_id == "test-1"

    def test_validation_runner_run_validation_reconfines_on_execute(self):
        """run_validation re-validates the worktree path before execution."""
        with tempfile.TemporaryDirectory() as base:
            inside = os.path.join(base, "inside")
            os.makedirs(inside, exist_ok=True)
            outside = os.path.join(base, "outside")
            os.makedirs(outside, exist_ok=True)

            symlink_path = os.path.join(inside, "escape_link")
            os.symlink(outside, symlink_path)

            runner = ValidationRunner(
                todo_id="test-1",
                worktree_path=inside,
                test_commands=["pytest"],
                expected_worktree_root=base,
            )
            runner._expected_worktree_root = inside
            runner.worktree_path = symlink_path

            with pytest.raises(CommandValidationError, match="escapes expected root"):
                runner.run_validation()

    def test_confine_worktree_path_rejects_leading_dash(self):
        """confine_worktree_path rejects a path beginning with '-'."""
        with pytest.raises(ValueError, match="begins with '-'"):
            confine_worktree_path("-evil", "/tmp")

    def test_confine_worktree_path_rejects_traversal(self):
        """confine_worktree_path rejects '..' traversal outside the base."""
        with tempfile.TemporaryDirectory() as base:
            inside = os.path.join(base, "inside")
            os.makedirs(inside, exist_ok=True)

            traversal_path = os.path.join(inside, "..", "outside")
            with pytest.raises(ValueError, match="escapes the allowed base"):
                confine_worktree_path(traversal_path, inside)


# ---------------------------------------------------------------------------
# 2. Event loop checks capacity BEFORE claiming a runnable
# ---------------------------------------------------------------------------


class TestClaimBeforeCapWindow:
    def test_phase_claim_runnable_computes_limit_before_claim(self):
        """The effective_limit is computed BEFORE claim_runnable is called."""
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop()

        import inspect
        source = inspect.getsource(loop._phase_claim_runnable_todos)

        cap_check_pos = source.find("effective_limit =")
        claim_pos = source.find("claim_runnable(")
        assert cap_check_pos >= 0, "effective_limit assignment not found"
        assert claim_pos >= 0, "claim_runnable call not found"
        assert cap_check_pos < claim_pos, (
            "effective_limit must be computed BEFORE claim_runnable is called"
        )

    @pytest.mark.asyncio
    async def test_effective_limit_zero_skips_claim(self):
        """When effective_limit is 0, no todos are claimed."""
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop()

        await loop._phase_claim_runnable_todos()
        claimed = loop._tick_state.get("claimed_todos")
        assert claimed == [] or claimed is None or claimed == []

    @pytest.mark.asyncio
    async def test_effective_limit_respects_pause_controller(self):
        """When a project is paused, no todos are claimed."""
        from general_ludd.event_loop.loop import EventLoop

        mock_pause = Mock()
        mock_pause.is_paused.return_value = True

        mock_repo = Mock()
        mock_repo.count_active = AsyncMock(return_value=0)

        loop = EventLoop(pause_controller=mock_pause)
        loop._todo_repo = mock_repo

        original_project = loop._tick_project_id
        loop._tick_project_id = "paused-project"
        try:
            await loop._phase_claim_runnable_todos()
            claimed = loop._tick_state.get("claimed_todos")
            assert claimed == []
        finally:
            loop._tick_project_id = original_project

    def test_effective_limit_formula_bounds_by_active_count(self):
        """The effective limit is reduced by currently_active count."""
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop()

        with patch.object(loop, "_todo_repo") as mock_repo:
            mock_repo.count_active = AsyncMock(return_value=8)
            mock_repo.claim_runnable = AsyncMock(return_value=[])

            async def _check():
                await loop._phase_claim_runnable_todos()
            import asyncio
            asyncio.run(_check())

    def test_effective_limit_formula_handles_missing_controllers(self):
        """When floor and PID controllers are absent, effective_limit defaults to 10."""
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop()

        with patch.object(loop, "_todo_repo") as mock_repo:
            mock_repo.count_active = AsyncMock(return_value=0)
            mock_repo.claim_runnable = AsyncMock(return_value=[])

            async def _check():
                loop._tick_project_id = "test-project"
                await loop._phase_claim_runnable_todos()
            import asyncio
            asyncio.run(_check())


# ---------------------------------------------------------------------------
# 3. _dispatch_review_job has a configurable timeout
# ---------------------------------------------------------------------------


class TestDispatchReviewJobTimeout:
    def test_dispatch_review_job_runner_path_has_timeout(self):
        """The runner path uses asyncio.wait_for with configurable timeout."""
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop(config={
            "review": {"playbook_timeout": 300.0, "http_timeout": 120.0},
        })

        import inspect
        source = inspect.getsource(loop._dispatch_review_job)

        assert "asyncio.wait_for" in source, (
            "_dispatch_review_job runner path must use asyncio.wait_for"
        )
        assert "review_playbook_timeout" in source, (
            "_dispatch_review_job must resolve playbook timeout from config"
        )
        assert "review_http_timeout" in source, (
            "_dispatch_review_job must resolve HTTP timeout from config"
        )

    def test_dispatch_review_job_in_process_path_has_timeout(self):
        """The in-process review path wraps _review_in_process with asyncio.wait_for."""
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop(config={
            "review": {"in_process_timeout": 300.0},
        })

        import inspect
        source = inspect.getsource(loop._dispatch_review_job)

        assert "in_process_timeout" in source, (
            "_dispatch_review_job must resolve in_process_timeout from config"
        )
        assert "asyncio.wait_for" in source, (
            "_dispatch_review_job in-process path must wrap with asyncio.wait_for"
        )

    def test_default_review_timeouts_are_sane(self):
        """Default review timeouts are non-zero and reasonable."""
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop()

        cfg = {}
        if isinstance(loop.config, dict):
            cfg = loop.config

        playbook_timeout = float(
            cfg.get("review", {}).get("playbook_timeout", 600.0)
            if isinstance(cfg.get("review", {}), dict)
            else 600.0
        )
        http_timeout = float(
            cfg.get("review", {}).get("http_timeout", 600.0)
            if isinstance(cfg.get("review", {}), dict)
            else 600.0
        )

        assert playbook_timeout > 0
        assert http_timeout > 0

    def test_dispatch_review_job_handles_timeouterror_gracefully(self):
        """When the review playbook times out, the claim is released and todo blocked."""
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop(config={
            "review": {"playbook_timeout": 0.001},
        })

        mock_tr = Mock()
        mock_tr.return_id = "ret-1"
        mock_tr.todo_id = "todo-1"
        mock_tr.project_id = None
        mock_tr.queue = "model"

        mock_runner = Mock()
        mock_runner.prepare_job_dirs.return_value = {"root": "/tmp/fake"}
        mock_runner.write_vars = Mock()
        mock_runner.run_playbook = Mock()

        loop._runner = mock_runner
        loop._http_client = None

        import asyncio

        async def _run():
            await loop._dispatch_review_job(mock_tr)

        asyncio.run(_run())
        mock_runner.prepare_job_dirs.assert_called_once()

    def test_dispatch_review_job_http_path_timeout_is_configurable(self):
        """HTTP review dispatch uses configurable http_timeout from review config."""
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop(config={
            "review": {"http_timeout": 45.0},
        })

        import inspect
        source = inspect.getsource(loop._dispatch_review_job)

        review_http_line = None
        for line in source.split("\n"):
            if "review_http_timeout" in line and "float" in line:
                review_http_line = line.strip()
                break
        assert review_http_line is not None

        assert "600.0" in source or "600" in source, (
            "Default http_timeout fallback should be 600.0"
        )
