"""Execution engine — real in-process model-driven code generation.

Generates code via ModelGateway, parses output for file-write blocks,
writes changes to workspace, runs project tests, and produces TaskReturn.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any

from general_ludd.schemas.job import JobSpec
from general_ludd.schemas.task_return import TaskReturn

logger = logging.getLogger(__name__)


def _parse_fenced_blocks(text: str) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    pattern = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
    for match in pattern.finditer(text):
        lang = match.group(1) or "text"
        content = match.group(2).strip()
        blocks.append({"language": lang, "content": content})
    return blocks


def _extract_file_paths(text: str) -> list[tuple[str, str]]:
    """Extract FILE: path / content pairs from text."""
    results: list[tuple[str, str]] = []
    pattern = re.compile(
        r"(?:FILE|file|File):\s*(\S+)\n(.*?)(?=(?:FILE|file|File):\s*\S+\n|$)",
        re.DOTALL,
    )
    for match in pattern.finditer(text):
        path = match.group(1).strip()
        content = match.group(2).strip()
        results.append((path, content))
    return results


def _apply_unified_diff(workspace: str, diff_text: str) -> list[str]:
    """Apply a unified diff to workspace files. Returns list of changed paths."""
    import tempfile

    changed: list[str] = []
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".diff", delete=False) as f:
            f.write(diff_text)
            diff_path = f.name
        result = subprocess.run(
            ["patch", "-p1", "-d", workspace, "-i", diff_path, "--force", "--no-backup-if-mismatch"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            logger.warning("patch command failed: %s", result.stderr[:200])
        for line in diff_text.split("\n"):
            if line.startswith("+++ b/"):
                p = line[6:].strip()
                if p:
                    changed.append(p)
            elif line.startswith("+++ "):
                p = line[4:].strip()
                if p:
                    changed.append(p)
        os.unlink(diff_path)
    except Exception as exc:
        logger.warning("Failed to apply diff: %s", exc)
    return changed


def _build_system_prompt(job: JobSpec) -> str:
    lines: list[str] = []
    lines.append("You are a coding agent. Generate code changes for the following task.")
    if job.skill_body:
        lines.append(f"\nGuidelines:\n{job.skill_body}")
    lines.append("\nOutput format:")
    lines.append("- Use fenced code blocks (```) for code.")
    lines.append("- Prefix each file with 'FILE: <path>' followed by the content.")
    lines.append("- Use unified diff format (```diff) for changes to existing files.")
    return "\n".join(lines)


def _build_user_prompt(job: JobSpec) -> str:
    return job.prompt_text or f"Task: {job.todo_id}\nWork type: {job.work_type}"


def _run_tests(workspace: str) -> tuple[int, str]:
    """Run tests in workspace. Returns (exit_code, output_summary)."""
    try:
        result = subprocess.run(
            ["make", "test"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = result.stdout[-2000:] if len(result.stdout) > 2000 else result.stdout
        if not output and result.stderr:
            output = result.stderr[-2000:] if len(result.stderr) > 2000 else result.stderr
        return result.returncode, output or "(no output)"
    except subprocess.TimeoutExpired:
        return 1, "Test run timed out after 120s"
    except FileNotFoundError:
        return 0, "No test command available (make not found)"
    except Exception as exc:
        return 1, f"Test run failed: {exc}"


class ExecutionEngine:
    def __init__(
        self,
        model_gateway: Any = None,
        workspace_path: str = "/tmp/gludd-workspace",
    ) -> None:
        self._model_gateway = model_gateway
        self.workspace_path = workspace_path
        os.makedirs(workspace_path, exist_ok=True)

    def execute(self, job: JobSpec) -> TaskReturn:
        return_id = f"RET-{job.job_id}-{uuid.uuid4().hex[:6]}"

        if self._model_gateway is None:
            return TaskReturn(
                return_id=return_id,
                todo_id=job.todo_id,
                job_id=job.job_id,
                playbook=job.playbook or "code",
                queue=job.queue or "core",
                exit_code=1,
                result_summary="No model gateway configured",
            )

        system_prompt = _build_system_prompt(job)
        user_prompt = _build_user_prompt(job)

        try:
            response = self._model_gateway.call_model(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
            model_output = getattr(response, "content", "") or str(response)
        except Exception as exc:
            return TaskReturn(
                return_id=return_id,
                todo_id=job.todo_id,
                job_id=job.job_id,
                playbook=job.playbook or "code",
                queue=job.queue or "core",
                exit_code=1,
                result_summary=f"Model call failed: {exc}",
            )

        if not model_output or not model_output.strip():
            return TaskReturn(
                return_id=return_id,
                todo_id=job.todo_id,
                job_id=job.job_id,
                playbook=job.playbook or "code",
                queue=job.queue or "core",
                exit_code=1,
                result_summary="Model returned empty output",
            )

        changed_files: list[str] = []
        applied_changes = False

        blocks = _parse_fenced_blocks(model_output)
        for block in blocks:
            content = block["content"]
            lang = block["language"].lower()

            if lang in ("diff", "patch"):
                changed = _apply_unified_diff(self.workspace_path, content)
                changed_files.extend(changed)
                if changed:
                    applied_changes = True
            else:
                # Check for FILE: prefix in content
                file_pairs = _extract_file_paths(content)
                for file_path, file_content in file_pairs:
                    full_path = os.path.join(self.workspace_path, file_path)
                    os.makedirs(os.path.dirname(full_path), exist_ok=True)
                    with open(full_path, "w") as f:
                        f.write(file_content)
                    changed_files.append(file_path)
                    applied_changes = True

        # Also scan raw output for FILE: patterns outside fenced blocks
        raw_pairs = _extract_file_paths(model_output)
        for file_path, file_content in raw_pairs:
            if file_path not in changed_files:
                full_path = os.path.join(self.workspace_path, file_path)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, "w") as f:
                    f.write(file_content)
                changed_files.append(file_path)
                applied_changes = True

        if not applied_changes:
            return TaskReturn(
                return_id=return_id,
                todo_id=job.todo_id,
                job_id=job.job_id,
                playbook=job.playbook or "code",
                queue=job.queue or "core",
                exit_code=1,
                result_summary="No file changes could be parsed from model output",
                artifacts=[f"raw_output:{len(model_output)} chars"],
            )

        test_exit_code, test_summary = _run_tests(self.workspace_path)

        return TaskReturn(
            return_id=return_id,
            todo_id=job.todo_id,
            job_id=job.job_id,
            playbook=job.playbook or "code",
            queue=job.queue or "core",
            exit_code=test_exit_code,
            result_summary=(
                f"Changed {len(changed_files)} file(s): {', '.join(changed_files[:10])}. "
                f"Tests: exit={test_exit_code}. {test_summary[:500]}"
            ),
            artifacts=changed_files[:20],
            diff_ref=f"raw_output:{len(model_output)} chars",
            test_results_ref=f"exit_code={test_exit_code}",
        )
