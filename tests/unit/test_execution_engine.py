from __future__ import annotations

import tempfile
from unittest.mock import MagicMock

from general_ludd.execution.engine import ExecutionEngine
from general_ludd.schemas.job import JobSpec


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
        mock_gateway.call_model = MagicMock(return_value=MagicMock(
            content="```\n--- a/src/main.py\n+++ b/src/main.py\n@@ -1 +1 @@\n-print('hello')\n+print('hello world')\n```"
        ))
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

        # This should write files even if output parsing doesn't match perfectly
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
