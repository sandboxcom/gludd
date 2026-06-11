"""Execution engine — real in-process model-driven code generation."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import uuid
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


def _build_system_prompt(job: JobSpec) -> str:
    lines: list[str] = []
    lines.append(
        "You are a coding agent. Generate code changes for the following task."
    )
    if job.skill_body:
        lines.append(f"\nGuidelines:\n{job.skill_body}")
    lines.append("\nOutput format:")
    lines.append("- Use fenced code blocks for code.")
    lines.append(
        "- Prefix each file with 'FILE: <path>' followed by the content."
    )
    return "\n".join(lines)


def _build_user_prompt(job: JobSpec) -> str:
    return job.prompt_text or f"Task: {job.todo_id}\nWork type: {job.work_type}"


def _run_tests(workspace: str) -> tuple[int, str]:
    try:
        result = subprocess.run(
            ["make", "test"],
            cwd=workspace, capture_output=True, text=True, timeout=120,
        )
        output = (
            result.stdout[-2000:] if len(result.stdout) > 2000
            else result.stdout
        )
        if not output and result.stderr:
            output = (
                result.stderr[-2000:] if len(result.stderr) > 2000
                else result.stderr
            )
        return result.returncode, output or "(no output)"
    except subprocess.TimeoutExpired:
        return 1, "Test run timed out after 120s"
    except FileNotFoundError:
        return 0, "No test command available (make not found)"
    except Exception as exc:
        return 1, f"Test run failed: {exc}"


def _is_git_repo(path: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=path, capture_output=True, text=True,
        )
        return result.returncode == 0
    except Exception:
        return False


def _git_create_branch(path: str, branch_name: str) -> bool:
    try:
        subprocess.run(
            ["git", "checkout", "-b", branch_name],
            cwd=path, capture_output=True, text=True, check=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def _git_commit(path: str, message: str) -> str | None:
    try:
        subprocess.run(["git", "add", "-A"], cwd=path, capture_output=True, check=True)
        result = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=path, capture_output=True, text=True,
        )
        if result.returncode == 0:
            sha_result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=path, capture_output=True, text=True,
            )
            return sha_result.stdout.strip()[:8]
        return None
    except Exception:
        return None


def _git_current_branch(path: str) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=path, capture_output=True, text=True,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def _slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len]


class ExecutionEngine:
    def __init__(
        self,
        model_gateway: Any = None,
        workspace_path: str = "/tmp/gludd-workspace",
        benchmark_recorder: Any = None,
        metrics_collector: Any = None,
    ) -> None:
        self._model_gateway = model_gateway
        self.workspace_path = workspace_path
        self._benchmark_recorder = benchmark_recorder
        self._metrics_collector = metrics_collector
        self._background_tasks: set[asyncio.Task[Any]] = set()
        os.makedirs(workspace_path, exist_ok=True)

    def _record_metrics(self, job: JobSpec, success: bool, tokens: int = 0) -> None:
        if self._metrics_collector is None:
            return
        try:
            wtype = job.work_type or "code"
            self._metrics_collector.record_model_call(
                model_profile=getattr(job, "model_profile", "unknown") or "unknown",
                work_type=wtype,
                success=success,
                input_tokens=tokens,
                output_tokens=0,
                cost_usd=0.0,
            )
        except Exception:
            pass

    def execute(self, job: JobSpec) -> TaskReturn:
        return_id = f"RET-{job.job_id}-{uuid.uuid4().hex[:6]}"

        if self._model_gateway is None:
            return TaskReturn(
                return_id=return_id, todo_id=job.todo_id, job_id=job.job_id,
                playbook=job.playbook or "code", queue=job.queue or "core",
                exit_code=1, result_summary="No model gateway configured",
            )

        is_git = _is_git_repo(self.workspace_path)

        title_slug = _slugify(job.prompt_text or job.todo_id or "untitled")
        branch_name = f"gludd/{job.todo_id}-{title_slug}"
        if is_git:
            _git_create_branch(self.workspace_path, branch_name)

        system_prompt = _build_system_prompt(job)
        user_prompt = _build_user_prompt(job)

        try:
            response = self._model_gateway.call_model(
                system_prompt=system_prompt, user_prompt=user_prompt,
            )
            model_output = getattr(response, "content", "") or str(response)
            self._record_metrics(job, success=True, tokens=len(model_output) // 4)
        except Exception as exc:
            self._record_metrics(job, success=False)
            return TaskReturn(
                return_id=return_id, todo_id=job.todo_id, job_id=job.job_id,
                playbook=job.playbook or "code", queue=job.queue or "core",
                exit_code=1, result_summary=f"Model call failed: {exc}",
            )

        if not model_output or not model_output.strip():
            return TaskReturn(
                return_id=return_id, todo_id=job.todo_id, job_id=job.job_id,
                playbook=job.playbook or "code", queue=job.queue or "core",
                exit_code=1, result_summary="Model returned empty output",
            )

        changed_files: list[str] = []
        applied_changes = False
        blocks = _parse_fenced_blocks(model_output)
        for block in blocks:
            content = block["content"]
            lang = block["language"].lower()
            if lang in ("diff", "patch"):
                changed = self._apply_unified_diff(content)
                changed_files.extend(changed)
                if changed:
                    applied_changes = True
            else:
                for file_path, file_content in _extract_file_paths(content):
                    self._write_file(file_path, file_content)
                    changed_files.append(file_path)
                    applied_changes = True
        for file_path, file_content in _extract_file_paths(model_output):
            if file_path not in changed_files:
                self._write_file(file_path, file_content)
                changed_files.append(file_path)
                applied_changes = True

        commit_sha = None
        if applied_changes and is_git:
            commit_msg = (
                f"[gludd] {job.todo_id}: "
                f"{job.prompt_text or 'code change'}\n\n"
                f"Work type: {job.work_type}\n"
                f"Changed files: {', '.join(changed_files[:10])}"
            )
            commit_sha = _git_commit(self.workspace_path, commit_msg)

        if not applied_changes:
            return TaskReturn(
                return_id=return_id, todo_id=job.todo_id, job_id=job.job_id,
                playbook=job.playbook or "code", queue=job.queue or "core",
                exit_code=1, result_summary="No changes parsed from model output",
                artifacts=[f"raw_output:{len(model_output)} chars"],
            )

        test_exit_code, test_summary = _run_tests(self.workspace_path)
        evidence_refs: list[str] = list(changed_files[:20])
        if commit_sha:
            evidence_refs.append(f"commit:{commit_sha}")
            evidence_refs.append(f"branch:{_git_current_branch(self.workspace_path)}")

        summary_parts: list[str] = [
            f"Changed {len(changed_files)} file(s): "
            f"{', '.join(changed_files[:10])}.",
        ]
        if not is_git:
            summary_parts.append(
                "WARNING: Workspace is not a git repository."
            )
        if commit_sha:
            summary_parts.append(f"Committed as {commit_sha}.")
        summary_parts.append(
            f"Tests: exit={test_exit_code}. {test_summary[:500]}"
        )

        result = TaskReturn(
            return_id=return_id, todo_id=job.todo_id, job_id=job.job_id,
            playbook=job.playbook or "code", queue=job.queue or "core",
            exit_code=test_exit_code, result_summary=" ".join(summary_parts),
            artifacts=evidence_refs,
            diff_ref=(
                f"commit:{commit_sha}" if commit_sha
                else f"raw_output:{len(model_output)} chars"
            ),
            test_results_ref=f"exit_code={test_exit_code}",
        )

        if self._benchmark_recorder is not None:
            try:
                from general_ludd.event_loop.benchmark import record_job_benchmark
                task = asyncio.create_task(
                    record_job_benchmark(
                        self._benchmark_recorder,
                        model_profile=getattr(job, "model_profile", None),
                        prompt_profile=getattr(job, "prompt_profile", None),
                        work_type=job.work_type or "code",
                        success=test_exit_code == 0,
                        input_tokens=len(model_output) // 4,
                    )
                )
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)
            except Exception:
                pass

        return result

    def _write_file(self, file_path: str, content: str) -> None:
        full_path = os.path.join(self.workspace_path, file_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w") as f:
            f.write(content)

    def _apply_unified_diff(self, diff_text: str) -> list[str]:
        import tempfile
        changed: list[str] = []
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".diff", delete=False
            ) as f:
                f.write(diff_text)
                diff_path = f.name
            subprocess.run(
                [
                    "patch", "-p1", "-d", self.workspace_path, "-i",
                    diff_path, "--force", "--no-backup-if-mismatch",
                ],
                capture_output=True, text=True, timeout=30,
            )
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
