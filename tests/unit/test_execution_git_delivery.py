from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.execution.engine import ExecutionEngine
from general_ludd.schemas.job import JobSpec


def _init_git_repo(path: str) -> None:
    subprocess.run(["git", "init", path], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "agent@test"],
        cwd=path, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test Agent"],
        cwd=path, check=True, capture_output=True,
    )
    (Path(path) / "README.md").write_text("# Test Repo\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, capture_output=True)


def _branches(path: str) -> list[str]:
    result = subprocess.run(
        ["git", "branch"], cwd=path, capture_output=True, text=True
    )
    return [b.strip().lstrip("* ") for b in result.stdout.splitlines() if b.strip()]


def _last_commit_subject(path: str) -> str:
    result = subprocess.run(
        ["git", "log", "-1", "--format=%s"], cwd=path, capture_output=True, text=True
    )
    return result.stdout.strip()


class TestExecutionGitDelivery:
    def test_engine_creates_branch_and_commits(self):
        mock_gateway = MagicMock()
        code = "print('hello from gludd')\n"
        mock_gateway.call_model = MagicMock(return_value=MagicMock(
            content=f"```\nFILE: src/main.py\n{code}\n```"
        ))

        with tempfile.TemporaryDirectory() as ws:
            _init_git_repo(ws)
            engine = ExecutionEngine(
                model_gateway=mock_gateway,
                workspace_path=ws,
            )

            job = JobSpec(
                job_id="JOB-G1",
                todo_id="TODO-G1",
                playbook="code",
                queue="core",
                work_type="code",
                prompt_text="Create main.py",
            )

            result = engine.execute(job)
            assert result.job_id == "JOB-G1"

            branches = _branches(ws)
            gludd_branches = [b for b in branches if b.startswith("gludd/")]
            assert len(gludd_branches) >= 1

    def test_no_changes_no_commit(self):
        mock_gateway = MagicMock()
        mock_gateway.call_model = MagicMock(return_value=MagicMock(
            content="I don't see any changes needed."
        ))

        with tempfile.TemporaryDirectory() as ws:
            _init_git_repo(ws)
            initial_commits = subprocess.run(
                ["git", "rev-list", "--count", "HEAD"],
                cwd=ws, capture_output=True, text=True,
            ).stdout.strip()

            engine = ExecutionEngine(model_gateway=mock_gateway, workspace_path=ws)
            job = JobSpec(
                job_id="JOB-G2", todo_id="TODO-G2",
                playbook="code", queue="core", work_type="code",
                prompt_text="Do nothing",
            )
            result = engine.execute(job)
            assert result.exit_code != 0

            final_commits = subprocess.run(
                ["git", "rev-list", "--count", "HEAD"],
                cwd=ws, capture_output=True, text=True,
            ).stdout.strip()
            assert initial_commits == final_commits

    def test_non_repo_workspace_fails_explicitly(self):
        mock_gateway = MagicMock()
        mock_gateway.call_model = MagicMock(return_value=MagicMock(
            content="```\nFILE: x.py\nprint('ok')\n```"
        ))

        with tempfile.TemporaryDirectory() as ws:
            engine = ExecutionEngine(model_gateway=mock_gateway, workspace_path=ws)
            job = JobSpec(
                job_id="JOB-G3", todo_id="TODO-G3",
                playbook="code", queue="core", work_type="code",
                prompt_text="Write x.py",
            )
            result = engine.execute(job)
            assert result.exit_code != 0
            assert "git" in result.result_summary.lower() or "not a git" in result.result_summary.lower()

    def test_commit_message_includes_todo_info(self):
        mock_gateway = MagicMock()
        code = "print('hello')\n"
        mock_gateway.call_model = MagicMock(return_value=MagicMock(
            content=f"```\nFILE: src/app.py\n{code}\n```"
        ))

        with tempfile.TemporaryDirectory() as ws:
            _init_git_repo(ws)
            engine = ExecutionEngine(model_gateway=mock_gateway, workspace_path=ws)
            job = JobSpec(
                job_id="JOB-G4", todo_id="TODO-G4",
                playbook="code", queue="core", work_type="code",
                prompt_text="Create app.py",
            )
            result = engine.execute(job)

            subject = _last_commit_subject(ws)
            assert "TODO-G4" in subject
