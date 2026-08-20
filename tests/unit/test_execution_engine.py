from __future__ import annotations

import asyncio
import tempfile
from unittest.mock import MagicMock, patch

from general_ludd.controllers.spend_limiter import SpendLimiter
from general_ludd.execution.engine import ExecutionEngine
from general_ludd.schemas.job import JobSpec


class TestRecordMetricsGap30:
    """GAP-30 regression: ``_record_metrics`` must invoke ``record_model_call``
    with its real signature ``(agent_id, model_id, input_tokens, output_tokens,
    success)``.

    Before the fix the engine passed ``model_profile=``/``work_type=``/
    ``cost_usd=`` and OMITTED the two required positional args (agent_id,
    model_id), so every call raised ``TypeError`` that the bare ``except`` ate —
    no model call (success OR failure) was ever recorded. The existing engine
    tests missed this because they construct the engine with no metrics
    collector, so ``_record_metrics`` returns early.
    """

    def _engine_with_collector(self, collector):
        engine = ExecutionEngine(
            model_gateway=MagicMock(),
            workspace_path=tempfile.mkdtemp(),
        )
        # Set directly so the test does not depend on the constructor kwarg name.
        engine._metrics_collector = collector
        return engine

    def _job(self):
        return JobSpec(
            job_id="JOB-M1",
            todo_id="TODO-M1",
            playbook="code",
            queue="core",
            work_type="code",
            prompt_text="x",
        )

    def test_record_metrics_records_success_and_failure(self):
        from general_ludd.metrics.collector import MetricsCollector

        collector = MetricsCollector()
        engine = self._engine_with_collector(collector)
        job = self._job()

        # One success, one failure. Pre-fix BOTH raised TypeError (swallowed) so
        # the collector stayed empty.
        engine._record_metrics(job, success=True, tokens=120)
        engine._record_metrics(job, success=False)

        usage = collector.get_global_model_usage()
        assert usage, (
            "record_model_call never executed — _record_metrics still calls it "
            "with the wrong signature (GAP-30 regression)"
        )
        # model_profile is unset on this JobSpec → engine falls back to "unknown".
        model_id = next(iter(usage))
        # 1 success + 1 failure recorded → success_rate is exactly 0.5.
        assert usage[model_id].success_rate == 0.5
        # The failing call must have landed in the per-model failure ring.
        assert len(collector.get_recent_failures(model_id)) == 1


class TestSpendLimiterDispatch:
    @staticmethod
    def _job() -> JobSpec:
        return JobSpec(
            job_id="JOB-SPEND-1",
            todo_id="TODO-SPEND-1",
            playbook="code",
            queue="core",
            work_type="code",
            prompt_text="Generate one small change",
            project_id="project-spend",
        )

    @staticmethod
    def _gateway(*, cost_estimate: object = 0.03) -> MagicMock:
        gateway = MagicMock()
        gateway.get_profile.return_value = MagicMock(
            model_name="test-model",
            max_input_tokens=1_000,
            max_output_tokens=1_000,
        )
        gateway.call_model.return_value = MagicMock(
            content="",
            cost_estimate=cost_estimate,
        )
        return gateway

    def test_configured_spend_limit_fails_closed_when_projection_is_unknown(self) -> None:
        limiter = SpendLimiter(limit_usd=1.0, window_seconds=3_600)
        gateway = self._gateway()
        gateway.get_profile.return_value = None
        engine = ExecutionEngine(
            model_gateway=gateway,
            workspace_path=tempfile.mkdtemp(),
            spend_limiter=limiter,
        )

        result = asyncio.run(engine.execute_async(self._job()))

        assert result.exit_code == 1
        assert result.result_summary.startswith("Spend check failed:")
        gateway.call_model.assert_not_called()

    def test_successful_call_commits_reservation_at_actual_cost(self) -> None:
        limiter = SpendLimiter(limit_usd=1.0, window_seconds=3_600)
        gateway = self._gateway(cost_estimate=0.03)
        engine = ExecutionEngine(
            model_gateway=gateway,
            workspace_path=tempfile.mkdtemp(),
            spend_limiter=limiter,
        )

        with patch.object(limiter, "token_cost_usd", return_value=0.05):
            asyncio.run(engine.execute_async(self._job()))

        assert abs(limiter.window_spend() - 0.03) < 1e-9
        assert [record[2] for record in limiter.unflushed_records()] == [0.03]
        gateway.call_model.assert_called_once()

    def test_missing_actual_cost_keeps_conservative_reservation(self) -> None:
        limiter = SpendLimiter(limit_usd=1.0, window_seconds=3_600)
        gateway = self._gateway(cost_estimate=None)
        engine = ExecutionEngine(
            model_gateway=gateway,
            workspace_path=tempfile.mkdtemp(),
            spend_limiter=limiter,
        )

        with patch.object(limiter, "token_cost_usd", return_value=0.05):
            asyncio.run(engine.execute_async(self._job()))

        assert abs(limiter.window_spend() - 0.05) < 1e-9

    def test_structured_actual_cost_keeps_conservative_reservation(self) -> None:
        limiter = SpendLimiter(limit_usd=1.0, window_seconds=3_600)
        gateway = self._gateway(cost_estimate={"amount": 0.03})
        engine = ExecutionEngine(
            model_gateway=gateway,
            workspace_path=tempfile.mkdtemp(),
            spend_limiter=limiter,
        )

        with patch.object(limiter, "token_cost_usd", return_value=0.05):
            asyncio.run(engine.execute_async(self._job()))

        assert abs(limiter.window_spend() - 0.05) < 1e-9

    def test_reservation_error_fails_closed(self) -> None:
        limiter = MagicMock()
        limiter.cap_configured = True
        limiter.token_cost_usd.return_value = 0.05
        limiter.reserve.side_effect = RuntimeError("limiter unavailable")
        gateway = self._gateway()
        engine = ExecutionEngine(
            model_gateway=gateway,
            workspace_path=tempfile.mkdtemp(),
            spend_limiter=limiter,
        )

        result = asyncio.run(engine.execute_async(self._job()))

        assert result.exit_code == 1
        assert "reservation failed" in result.result_summary
        gateway.call_model.assert_not_called()

    def test_failed_model_call_releases_reservation(self) -> None:
        limiter = SpendLimiter(limit_usd=1.0, window_seconds=3_600)
        gateway = self._gateway()
        gateway.call_model.side_effect = RuntimeError("gateway unavailable")
        engine = ExecutionEngine(
            model_gateway=gateway,
            workspace_path=tempfile.mkdtemp(),
            spend_limiter=limiter,
        )

        with patch.object(limiter, "token_cost_usd", return_value=0.05):
            result = asyncio.run(engine.execute_async(self._job()))

        assert result.exit_code == 1
        assert limiter.window_spend() == 0.0


class TestExecutionEngine:
    def test_sync_migration_residue_is_not_exposed(self) -> None:
        assert not hasattr(ExecutionEngine, "_fallback_extract_code_not_a_method")

    def _make_engine(self, mock_gateway=None, workspace=None):
        return ExecutionEngine(
            model_gateway=mock_gateway or MagicMock(),
            workspace_path=workspace or tempfile.mkdtemp(),
        )

    def test_engine_created_with_gateway_and_workspace(self):
        engine = self._make_engine()
        assert engine._model_gateway is not None
        assert engine.workspace_path is not None

    def test_execute_parses_file_write_blocks(self):
        mock_gateway = MagicMock()
        diff_output = (
            "```\n--- a/src/main.py\n+++ b/src/main.py\n@@ -1 +1 @@\n-print('hello')\n+print('hello world')\n```"
        )
        mock_gateway.call_model = MagicMock(return_value=MagicMock(content=diff_output))
        engine = self._make_engine(mock_gateway)

        job = JobSpec(
            job_id="JOB-001",
            todo_id="TODO-001",
            playbook="code",
            queue="core",
            work_type="code",
            prompt_text="Fix the print statement",
            project_id="proj-1",
        )

        result = asyncio.run(engine.execute_async(job))
        assert result.job_id == "JOB-001"
        assert result.todo_id == "TODO-001"

    def test_malformed_output_returns_failed_task_return(self):
        mock_gateway = MagicMock()
        mock_gateway.call_model = MagicMock(return_value=MagicMock(content="I cannot generate code for this request."))
        engine = self._make_engine(mock_gateway)

        job = JobSpec(
            job_id="JOB-002",
            todo_id="TODO-002",
            playbook="code",
            queue="core",
            work_type="code",
            prompt_text="Make a change",
        )

        result = asyncio.run(engine.execute_async(job))
        assert result.exit_code != 0

    def test_execute_writes_files_to_workspace(self):
        mock_gateway = MagicMock()
        code = "def hello():\n    return 'world'\n"
        mock_gateway.call_model = MagicMock(return_value=MagicMock(content=f"```\nFILE: src/hello.py\n{code}\n```"))
        engine = self._make_engine(mock_gateway)

        job = JobSpec(
            job_id="JOB-003",
            todo_id="TODO-003",
            playbook="code",
            queue="core",
            work_type="code",
            prompt_text="Create hello.py",
        )

        result = asyncio.run(engine.execute_async(job))
        assert result.job_id == "JOB-003"

    def test_no_model_output_produces_failure(self):
        mock_gateway = MagicMock()
        mock_gateway.call_model = MagicMock(return_value=MagicMock(content=""))
        engine = self._make_engine(mock_gateway)

        job = JobSpec(
            job_id="JOB-004",
            todo_id="TODO-004",
            playbook="code",
            queue="core",
            work_type="code",
            prompt_text="Do something",
        )

        result = asyncio.run(engine.execute_async(job))
        assert result.exit_code != 0

    def test_fallback_extracts_python_fenced_block_without_file_markers(self):
        """When a model outputs ```python blocks but no FILE: markers,
        the fallback auto-assigns a path and writes the code."""
        mock_gateway = MagicMock()
        code = "def hello():\n    return 'world'\n"
        mock_gateway.call_model = MagicMock(
            return_value=MagicMock(content=f"Here is the code:\n\n```python\n{code}\n```")
        )
        engine = self._make_engine(mock_gateway)

        job = JobSpec(
            job_id="JOB-FB1",
            todo_id="TODO-FB1",
            playbook="code",
            queue="core",
            work_type="code",
            prompt_text="Create a hello function",
        )

        result = asyncio.run(engine.execute_async(job))
        assert result.exit_code != 1, (
            f"Expected exit_code != 1 (fallback should extract code), got {result.exit_code}: {result.result_summary}"
        )
        assert result.job_id == "JOB-FB1"

    def test_fallback_not_triggered_when_no_code_blocks(self):
        """No fenced blocks + no FILE markers should still fail (no fallback)."""
        mock_gateway = MagicMock()
        mock_gateway.call_model = MagicMock(
            return_value=MagicMock(content="I cannot generate any code for this request.")
        )
        engine = self._make_engine(mock_gateway)

        job = JobSpec(
            job_id="JOB-FB2",
            todo_id="TODO-FB2",
            playbook="code",
            queue="core",
            work_type="code",
            prompt_text="Make a change",
        )

        result = asyncio.run(engine.execute_async(job))
        assert result.exit_code != 0

    def test_fallback_uses_default_filename_when_no_existing_py(self):
        """When workspace has no .py files, fallback creates a slug-based name."""
        import os
        import tempfile

        mock_gateway = MagicMock()
        code = "print('hello world')"
        mock_gateway.call_model = MagicMock(return_value=MagicMock(content=f"```python\n{code}\n```"))
        empty_workspace = tempfile.mkdtemp()
        engine = self._make_engine(mock_gateway, workspace=empty_workspace)

        job = JobSpec(
            job_id="JOB-FB3",
            todo_id="TODO-FB3",
            playbook="code",
            queue="core",
            work_type="code",
            prompt_text="Write a hello world printer",
        )

        result = asyncio.run(engine.execute_async(job))
        assert result.exit_code != 1, f"Expected exit_code != 1, got {result.exit_code}: {result.result_summary}"
        generated_path = os.path.join(empty_workspace, "write-a-hello-world-printer.py")
        assert os.path.isfile(generated_path), f"Expected generated file at {generated_path}"
        with open(generated_path) as f:
            assert f.read() == code

    def test_fallback_logs_warning_for_missing_file_markers(self):
        """Fallback emits a WARNING log when FILE: markers are absent."""
        import logging

        mock_gateway = MagicMock()
        code = "x = 1\n"
        mock_gateway.call_model = MagicMock(return_value=MagicMock(content=f"```python\n{code}\n```"))
        engine = self._make_engine(mock_gateway)

        job = JobSpec(
            job_id="JOB-FB4",
            todo_id="TODO-FB4",
            playbook="code",
            queue="core",
            work_type="code",
            prompt_text="Set x to 1",
        )

        platform_logger = logging.getLogger("general_ludd.execution.engine")
        with patch.object(platform_logger, "warning", wraps=platform_logger.warning) as mock_warning:
            result = asyncio.run(engine.execute_async(job))

        assert result.exit_code != 1
        warning_texts = [str(a) for call in mock_warning.mock_calls if call.args for a in call.args]
        assert any("no FILE:" in t for t in warning_texts), f"Expected FILE: marker warning, got: {warning_texts}"

    def test_skill_body_included_in_prompt(self):
        mock_gateway = MagicMock()
        mock_gateway.call_model = MagicMock(return_value=MagicMock(content="```\nFILE: x.py\nprint('ok')\n```"))
        engine = self._make_engine(mock_gateway)

        job = JobSpec(
            job_id="JOB-005",
            todo_id="TODO-005",
            playbook="code",
            queue="core",
            work_type="code",
            prompt_text="Write code",
            skill_body="Use TDD patterns",
        )

        asyncio.run(engine.execute_async(job))
        mock_gateway.call_model.assert_called_once()
        call_kwargs = mock_gateway.call_model.call_args
        prompt_arg = str(call_kwargs)
        assert "Use TDD patterns" in prompt_arg
