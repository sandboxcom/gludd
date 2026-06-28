from __future__ import annotations

import tempfile
from unittest.mock import MagicMock

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


class TestExecutionEngine:
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
            "```\n--- a/src/main.py\n+++ b/src/main.py\n"
            "@@ -1 +1 @@\n-print('hello')\n+print('hello world')\n```"
        )
        mock_gateway.call_model = MagicMock(
            return_value=MagicMock(content=diff_output)
        )
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

        result = engine.execute(job)
        assert result.job_id == "JOB-001"
        assert result.todo_id == "TODO-001"

    def test_malformed_output_returns_failed_task_return(self):
        mock_gateway = MagicMock()
        mock_gateway.call_model = MagicMock(return_value=MagicMock(
            content="I cannot generate code for this request."
        ))
        engine = self._make_engine(mock_gateway)

        job = JobSpec(
            job_id="JOB-002",
            todo_id="TODO-002",
            playbook="code",
            queue="core",
            work_type="code",
            prompt_text="Make a change",
        )

        result = engine.execute(job)
        assert result.exit_code != 0

    def test_execute_writes_files_to_workspace(self):
        mock_gateway = MagicMock()
        code = "def hello():\n    return 'world'\n"
        mock_gateway.call_model = MagicMock(return_value=MagicMock(
            content=f"```\nFILE: src/hello.py\n{code}\n```"
        ))
        engine = self._make_engine(mock_gateway)

        job = JobSpec(
            job_id="JOB-003",
            todo_id="TODO-003",
            playbook="code",
            queue="core",
            work_type="code",
            prompt_text="Create hello.py",
        )

        result = engine.execute(job)
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

        result = engine.execute(job)
        assert result.exit_code != 0

    def test_skill_body_included_in_prompt(self):
        mock_gateway = MagicMock()
        mock_gateway.call_model = MagicMock(return_value=MagicMock(
            content="```\nFILE: x.py\nprint('ok')\n```"
        ))
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

        engine.execute(job)
        mock_gateway.call_model.assert_called_once()
        call_kwargs = mock_gateway.call_model.call_args
        prompt_arg = str(call_kwargs)
        assert "Use TDD patterns" in prompt_arg
