"""Execution engine — real in-process model-driven code generation."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import signal
import subprocess
import uuid
from typing import Any

from general_ludd.git_automation.repo import GitAutomation
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


def _render_skill_body(raw: str, variables: dict[str, object] | None = None) -> str:
    """Render skill body via the shared renderer (W6.5: one renderer, two consumers)."""
    try:
        from general_ludd.skills.renderer import SkillRenderError, render_skill
        return render_skill(raw, variables)
    except SkillRenderError:
        raise
    except Exception:
        # Jinja2 not available or no vars needed — return raw body unchanged
        return raw


def _build_system_prompt(job: JobSpec) -> str:
    lines: list[str] = []
    lines.append(
        "You are a coding agent. Generate code changes for the following task."
    )
    if job.skill_body:
        rendered = _render_skill_body(job.skill_body)
        lines.append(f"\nGuidelines:\n{rendered}")
    lines.append("\nOutput format:")
    lines.append("- Use fenced code blocks for code.")
    lines.append(
        "- Prefix each file with 'FILE: <path>' followed by the content."
    )
    return "\n".join(lines)


def _build_user_prompt(job: JobSpec) -> str:
    return job.prompt_text or f"Task: {job.todo_id}\nWork type: {job.work_type}"


def _run_tests(workspace: str) -> tuple[int, str]:
    # start_new_session=True puts `make test` (and every recipe grandchild it
    # spawns: pytest, xdist workers, etc.) into its OWN process group. On timeout
    # we os.killpg the whole group so no recipe grandchild leaks and keeps running
    # after we've given up — a plain subprocess.run timeout only kills `make`.
    proc: subprocess.Popen[str] | None = None
    try:
        proc = subprocess.Popen(
            ["make", "test"],
            cwd=workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=120)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError, OSError):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            with contextlib.suppress(Exception):
                proc.communicate(timeout=5)
            return 1, "Test run timed out after 120s"
        output = stdout[-2000:] if len(stdout) > 2000 else stdout
        if not output and stderr:
            output = stderr[-2000:] if len(stderr) > 2000 else stderr
        return proc.returncode, output or "(no output)"
    except FileNotFoundError:
        return 0, "No test command available (make not found)"
    except Exception as exc:
        return 1, f"Test run failed: {exc}"


def _is_git_repo(path: str) -> bool:
    """Delegate to GitAutomation.is_repo() — bounded timeout + non-interactive env + lock."""
    return GitAutomation(path).is_repo()


def _git_create_branch(path: str, branch_name: str) -> bool:
    """Delegate to GitAutomation.create_branch(); return False on any failure.

    GitAutomation.create_branch() already rejects dash-leading names via
    _reject_leading_dash and appends a ``--`` end-of-options separator.
    """
    try:
        GitAutomation(path).create_branch(branch_name)
        return True
    except Exception:
        return False


def _git_commit(path: str, message: str) -> str | None:
    """Delegate to GitAutomation.commit(); return 8-char SHA or None on failure.

    GitAutomation.commit() runs add-A -> commit -> rev-parse under a single
    re-entrant lock acquisition — the 3-step lock granularity is acceptable in
    engine's single-threaded execute(). Truncate to 8 chars to match the
    original contract.
    """
    try:
        return GitAutomation(path).commit(message)[:8]
    except Exception:
        return None


async def _git_commit_async(path: str, message: str) -> str | None:
    """Async wrapper: run _git_commit in a thread executor (non-blocking).

    Uses asyncio.get_running_loop() (not the deprecated get_event_loop())
    so this is safe on Python 3.10+ and raises immediately if called
    outside a running loop rather than silently creating a new one.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _git_commit, path, message)


def _git_current_branch(path: str) -> str:
    """Delegate to GitAutomation.current_branch(); returns 'unknown' on any failure."""
    return GitAutomation(path).current_branch()


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
        budget_guard: Any = None,
    ) -> None:
        self._model_gateway = model_gateway
        self.workspace_path = workspace_path
        self._benchmark_recorder = benchmark_recorder
        self._metrics_collector = metrics_collector
        self._budget_guard = budget_guard
        self._background_tasks: set[asyncio.Task[Any]] = set()
        os.makedirs(workspace_path, exist_ok=True)

    def _budget_pre_check(self, guard: Any) -> str | None:
        """Run budget pre-check; return denial string or None (allowed).

        Fail-CLOSED: any non-dict result, missing 'allowed' key, or unknown
        guard interface returns a denial string. Only guard=None is an
        intentional no-op (returns None = allowed).
        """
        if guard is None:
            return None
        if hasattr(guard, "check_all_limits"):
            try:
                verdict = guard.check_all_limits(estimated_cost=0.0)
            except Exception as exc:
                return f"budget check raised: {exc}"
            if not isinstance(verdict, dict):
                return "budget check returned non-dict"
            if not verdict.get("allowed", False):
                return str(verdict.get("reason", "budget exhausted"))
            return None
        if hasattr(guard, "try_charge"):
            try:
                verdict = guard.try_charge(cost=0.0)
            except Exception as exc:
                return f"budget check raised: {exc}"
            if not isinstance(verdict, dict):
                return "budget check returned non-dict"
            if not verdict.get("allowed", False):
                return str(verdict.get("reason", "budget exhausted"))
            return None
        return "budget guard has unknown interface"

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

    def defer_commit(self, path: str, message: str) -> None:
        """Schedule a git commit as a background asyncio task (non-blocking).

        FIX: the done-callback inspects task.exception() and logs at ERROR so
        commit failures are observable rather than silently swallowed.
        """
        try:
            task: asyncio.Task[str | None] = asyncio.create_task(
                _git_commit_async(path, message)
            )
            self._background_tasks.add(task)

            def _on_commit_done(t: asyncio.Task[str | None]) -> None:
                self._background_tasks.discard(t)
                exc = t.exception() if not t.cancelled() else None
                if exc is not None:
                    logger.error(
                        "defer_commit: background commit failed: %s", exc
                    )

            task.add_done_callback(_on_commit_done)
        except Exception:
            pass

    async def execute_async(self, job: JobSpec) -> TaskReturn:
        """Async variant of execute(): defers the git commit step to background."""
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

        denial = self._budget_pre_check(self._budget_guard)
        if denial is not None:
            return TaskReturn(
                return_id=return_id, todo_id=job.todo_id, job_id=job.job_id,
                playbook=job.playbook or "code", queue=job.queue or "core",
                exit_code=1, result_summary=f"Budget check failed: {denial}",
            )

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

        if not applied_changes:
            return TaskReturn(
                return_id=return_id, todo_id=job.todo_id, job_id=job.job_id,
                playbook=job.playbook or "code", queue=job.queue or "core",
                exit_code=1, result_summary="No changes parsed from model output",
                artifacts=[f"raw_output:{len(model_output)} chars"],
            )

        # Deferred (non-blocking) commit: fires in background, doesn't stall caller
        if applied_changes and is_git:
            commit_msg = (
                f"[gludd] {job.todo_id}: "
                f"{job.prompt_text or 'code change'}\n\n"
                f"Work type: {job.work_type}\n"
                f"Changed files: {', '.join(changed_files[:10])}"
            )
            self.defer_commit(self.workspace_path, commit_msg)

        test_exit_code, test_summary = _run_tests(self.workspace_path)
        evidence_refs: list[str] = list(changed_files[:20])

        summary_parts: list[str] = [
            f"Changed {len(changed_files)} file(s): "
            f"{', '.join(changed_files[:10])}.",
        ]
        if not is_git:
            summary_parts.append("WARNING: Workspace is not a git repository.")
        summary_parts.append("Commit deferred to background.")
        summary_parts.append(f"Tests: exit={test_exit_code}. {test_summary[:500]}")

        return TaskReturn(
            return_id=return_id, todo_id=job.todo_id, job_id=job.job_id,
            playbook=job.playbook or "code", queue=job.queue or "core",
            exit_code=test_exit_code, result_summary=" ".join(summary_parts),
            artifacts=evidence_refs,
            diff_ref=f"raw_output:{len(model_output)} chars",
            test_results_ref=f"exit_code={test_exit_code}",
        )

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

        denial = self._budget_pre_check(self._budget_guard)
        if denial is not None:
            return TaskReturn(
                return_id=return_id, todo_id=job.todo_id, job_id=job.job_id,
                playbook=job.playbook or "code", queue=job.queue or "core",
                exit_code=1, result_summary=f"Budget check failed: {denial}",
            )

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

    def _resolve_in_workspace(self, file_path: str) -> str:
        """Resolve ``file_path`` against the workspace, refusing any escape.

        The model supplies these paths. An absolute path (``/etc/passwd``) or a
        ``../`` traversal would otherwise let a write/patch land OUTSIDE the
        workspace. We jail it: resolve the realpath of both the workspace base and
        the candidate, and refuse unless the candidate is contained in the base
        (via os.path.commonpath). Returns the safe absolute path.
        """
        base = os.path.realpath(self.workspace_path)
        # join() makes an absolute file_path REPLACE base — exactly the escape we
        # must catch — so the containment check below (not join alone) is the gate.
        full = os.path.realpath(os.path.join(base, file_path))
        try:
            common = os.path.commonpath([base, full])
        except ValueError:
            # Different drives / mixed abs+rel on Windows -> not contained.
            common = ""
        if common != base:
            raise ValueError(
                f"refusing path that escapes the workspace: {file_path!r} "
                f"(resolved to {full!r}, base {base!r})"
            )
        return full

    def _write_file(self, file_path: str, content: str) -> None:
        full_path = self._resolve_in_workspace(file_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w") as f:
            f.write(content)

    def _diff_target_paths(self, diff_text: str) -> list[str]:
        """Extract every path a unified diff could write, as ``patch -p1`` sees it.

        ``patch`` reads BOTH the ``---`` (source/old) and ``+++`` (target/new)
        header lines and, with ``--force``, can fall back to the source path when
        the target is absent. So an attacker could hide an escaping path on the
        ``---`` line behind a benign ``+++``. We therefore validate both.

        ``patch -p1`` strips one leading path component, so ``--- a/foo`` and
        ``+++ b/foo`` both target ``foo``. We mirror that strip here so
        containment is checked against the same path patch will actually touch.
        """
        targets: list[str] = []
        for line in diff_text.split("\n"):
            if line.startswith("+++ ") or line.startswith("--- "):
                raw = line[4:].strip()
                # Drop a trailing tab-timestamp if present.
                raw = raw.split("\t", 1)[0].strip()
                if raw in ("/dev/null", ""):
                    continue
                # -p1 strips the first component (e.g. the "a/"/"b/" prefix).
                parts = raw.split("/", 1)
                stripped = parts[1] if len(parts) == 2 else parts[0]
                if stripped:
                    targets.append(stripped)
        return targets

    def _diff_changed_files(self, diff_text: str) -> list[str]:
        """Files a ``+++`` line names (post ``-p1`` strip), de-duplicated.

        Used to report which files were touched AFTER patch succeeds. Distinct
        from :meth:`_diff_target_paths`, which also covers ``---`` for the jail
        check.
        """
        changed: list[str] = []
        for line in diff_text.split("\n"):
            if not line.startswith("+++ "):
                continue
            raw = line[4:].strip().split("\t", 1)[0].strip()
            if raw in ("/dev/null", ""):
                continue
            parts = raw.split("/", 1)
            stripped = parts[1] if len(parts) == 2 else parts[0]
            if stripped and stripped not in changed:
                changed.append(stripped)
        return changed

    def _apply_unified_diff(self, diff_text: str) -> list[str]:
        import tempfile
        # Containment jail (model-supplied diff): refuse to apply ANY hunk whose
        # source (---) OR target (+++) escapes the workspace via absolute or ../
        # path. We validate every header path BEFORE invoking patch, so a single
        # escaping path aborts the whole diff rather than letting patch write
        # outside the workspace.
        targets = self._diff_target_paths(diff_text)
        if not targets:
            return []
        for target in targets:
            try:
                self._resolve_in_workspace(target)
            except ValueError as exc:
                logger.warning("Refusing diff with escaping target: %s", exc)
                return []
        # Run patch confined to the REALPATH jail base — the same path the jail
        # check above resolved against — so a symlinked workspace can't make
        # patch's working directory diverge from what we validated.
        jail = os.path.realpath(self.workspace_path)
        diff_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".diff", delete=False
            ) as f:
                f.write(diff_text)
                diff_path = f.name
            # argv is list-form (never a shell string): diff_path and jail are
            # caller/LLM-adjacent, so list-form keeps them out of shell parsing.
            result = subprocess.run(
                [
                    "patch", "-p1", "-d", jail, "-i",
                    diff_path, "--force", "--no-backup-if-mismatch",
                ],
                capture_output=True, text=True, timeout=30,
            )
            # Only report files as changed when patch actually succeeded; a
            # rejected/failed patch must not be advertised as an applied change.
            if result.returncode != 0:
                logger.warning(
                    "patch exited %s; treating diff as not applied: %s",
                    result.returncode, (result.stderr or result.stdout)[:500],
                )
                return []
            return self._diff_changed_files(diff_text)
        except Exception as exc:
            logger.warning("Failed to apply diff: %s", exc)
            return []
        finally:
            if diff_path is not None:
                with contextlib.suppress(OSError):
                    os.unlink(diff_path)
