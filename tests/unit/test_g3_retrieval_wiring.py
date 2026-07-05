from __future__ import annotations

import tempfile
from unittest.mock import MagicMock, patch

from general_ludd.execution.engine import (
    ExecutionEngine,
    _build_system_prompt,
    _inject_retrieval_context,
)
from general_ludd.schemas.job import JobSpec

_DEFAULT_PROMPT = "Fix the bug in the event loop"
_DEFAULT_TODO = "TODO-G3"


def _make_job(prompt_text=_DEFAULT_PROMPT, todo_id=_DEFAULT_TODO):
    return JobSpec(
        job_id="JOB-G3",
        todo_id=todo_id,
        playbook="code",
        queue="core",
        work_type="code",
        prompt_text=prompt_text,
    )


class TestInjectRetrievalContext:
    def test_no_searcher_returns_unchanged_prompt(self):
        job = _make_job()
        original = "You are a coding agent."
        result = _inject_retrieval_context(original, job, searcher=None, workspace_path="/tmp")
        assert result == original

    def test_no_workspace_returns_unchanged_prompt(self):
        mock_searcher = MagicMock()
        mock_searcher.search.return_value = [
            {"filepath": "foo.py", "content": "def bar(): pass", "score": 0.9},
        ]
        job = _make_job()
        original = "You are a coding agent."
        result = _inject_retrieval_context(original, job, mock_searcher, workspace_path=None)
        assert result == original
        mock_searcher.search.assert_not_called()

    def test_empty_query_returns_unchanged_prompt(self):
        mock_searcher = MagicMock()
        job = _make_job(prompt_text="", todo_id="")
        original = "You are a coding agent."
        result = _inject_retrieval_context(original, job, mock_searcher, workspace_path="/tmp")
        assert result == original
        mock_searcher.search.assert_not_called()

    def test_searcher_raises_returns_unchanged_prompt(self):
        mock_searcher = MagicMock()
        mock_searcher.search.side_effect = RuntimeError("disk full")
        job = _make_job()
        original = "You are a coding agent."
        result = _inject_retrieval_context(original, job, mock_searcher, workspace_path="/tmp")
        assert result == original

    def test_searcher_returns_empty_returns_unchanged_prompt(self):
        mock_searcher = MagicMock()
        mock_searcher.search.return_value = []
        job = _make_job()
        original = "You are a coding agent."
        result = _inject_retrieval_context(original, job, mock_searcher, workspace_path="/tmp")
        assert result == original

    def test_injects_context_when_results_found(self):
        mock_searcher = MagicMock()
        mock_searcher.search.return_value = [
            {"filepath": "src/loop.py", "content": "def tick(): pass", "score": 0.95},
            {"filepath": "src/daemon.py", "content": "app = FastAPI()", "score": 0.72},
        ]
        job = _make_job()
        original = "You are a coding agent."
        result = _inject_retrieval_context(original, job, mock_searcher, workspace_path="/tmp")

        assert "Relevant Codebase Context:" in result
        assert "src/loop.py" in result
        assert "score: 0.95" in result
        assert "src/daemon.py" in result
        assert "score: 0.72" in result
        assert original in result
        mock_searcher.search.assert_called_once_with("Fix the bug in the event loop", top_k=5)

    def test_falls_back_to_todo_id_when_no_prompt_text(self):
        mock_searcher = MagicMock()
        mock_searcher.search.return_value = [
            {"filepath": "src/util.py", "content": "def helper(): pass", "score": 0.5},
        ]
        job = _make_job(prompt_text=None, todo_id="TODO-fix-logging")
        original = "You are a coding agent."
        result = _inject_retrieval_context(original, job, mock_searcher, workspace_path="/tmp")

        assert "Relevant Codebase Context:" in result
        mock_searcher.search.assert_called_once_with("TODO-fix-logging", top_k=5)

    def test_truncates_long_content(self):
        long_content = "x" * 2000
        mock_searcher = MagicMock()
        mock_searcher.search.return_value = [
            {"filepath": "src/big.py", "content": long_content, "score": 0.8},
        ]
        job = _make_job()
        original = "You are a coding agent."
        result = _inject_retrieval_context(original, job, mock_searcher, workspace_path="/tmp")

        assert "Relevant Codebase Context:" in result
        assert long_content[:1500] in result
        assert "... (truncated)" in result
        assert len(result) < len(original) + len(long_content)


class TestBuildSystemPromptWithRetrieval:
    def test_build_system_prompt_injects_context(self):
        mock_searcher = MagicMock()
        mock_searcher.search.return_value = [
            {"filepath": "src/agent.py", "content": "class Agent: pass", "score": 0.88},
        ]
        job = _make_job(prompt_text="implement agent pooling")

        prompt = _build_system_prompt(
            job, searcher=mock_searcher, workspace_path="/tmp",
        )

        assert "Relevant Codebase Context:" in prompt
        assert "src/agent.py" in prompt
        assert "You are a coding agent." in prompt


class TestExecutionEngineRetrieval:
    def _make_engine(self, mock_gateway=None, searcher=None, workspace_path=None):
        return ExecutionEngine(
            model_gateway=mock_gateway or MagicMock(),
            workspace_path=workspace_path or tempfile.mkdtemp(),
            searcher=searcher,
        )

    def test_engine_stores_searcher(self):
        mock_searcher = MagicMock()
        engine = self._make_engine(searcher=mock_searcher)
        assert engine._searcher is mock_searcher

    def test_engine_defaults_searcher_to_none(self):
        engine = self._make_engine()
        assert engine._searcher is None

    def test_execute_calls_searcher_with_job_query(self):
        mock_gateway = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "FILE: src/fix.py\ndef hello(): pass"
        mock_gateway.call_model.return_value = mock_response

        mock_searcher = MagicMock()
        mock_searcher.search.return_value = [
            {"filepath": "src/bug.py", "content": "def broken(): pass", "score": 0.91},
        ]

        engine = self._make_engine(
            mock_gateway=mock_gateway,
            searcher=mock_searcher,
        )
        job = _make_job(prompt_text="fix the login form")

        with patch(
            "general_ludd.execution.engine._is_git_repo", return_value=False
        ), patch(
            "general_ludd.execution.engine._run_tests", return_value=(0, "ok")
        ):
            result = engine.execute(job)

        assert result.exit_code == 0
        mock_searcher.search.assert_called_once_with("fix the login form", top_k=5)
        system_prompt_content = mock_gateway.call_model.call_args[1]["messages"][0]["content"]
        assert "Relevant Codebase Context:" in system_prompt_content

    def test_execute_works_when_searcher_is_none(self):
        mock_gateway = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "FILE: src/fix.py\ndef hello(): pass"
        mock_gateway.call_model.return_value = mock_response

        engine = self._make_engine(
            mock_gateway=mock_gateway,
            searcher=None,
        )
        job = _make_job()

        with patch(
            "general_ludd.execution.engine._is_git_repo", return_value=False
        ), patch(
            "general_ludd.execution.engine._run_tests", return_value=(0, "ok")
        ):
            result = engine.execute(job)

        assert result.exit_code == 0
        system_prompt_content = mock_gateway.call_model.call_args[1]["messages"][0]["content"]
        assert "Relevant Codebase Context:" not in system_prompt_content
