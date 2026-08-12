"""Execution engine — real in-process model-driven code generation."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import os
import re
import subprocess
import uuid
from typing import Any, cast

from general_ludd.agents.behavior import AgentBehavior, BehaviorRenderer
from general_ludd.controllers.spend_limiter import SpendLimiter
from general_ludd.git_automation.repo import GitAutomation
from general_ludd.project_runner import (
    ProjectCommandRunner,
    ProjectProfileError,
    load_project_profile,
)
from general_ludd.schemas.job import JobSpec
from general_ludd.schemas.task_return import TaskReturn
from general_ludd.security.state import project_state, secure_directory

logger = logging.getLogger(__name__)

_UNKNOWN_MODEL_COST_PER_1K = 0.01

# Jinja2 SSTI patterns — must never appear in user-supplied variable values
_JINJA2_EXPR = re.compile(r"\{\{.*?\}\}", re.DOTALL)
_JINJA2_BLOCK = re.compile(r"\{%[\s\S]*?%\}")
_JINJA2_COMMENT = re.compile(r"\{#.*?#\}", re.DOTALL)


def validate_extra_vars_safe(
    vars_dict: dict[str, object],
    allow_jinja2_in_extravars: bool = False,
) -> None:
    """Validate that extra_vars do not contain Jinja2 SSTI injection patterns.

    Blocks {{...}}, {%...%}, and {#...#} Jinja2 syntax patterns in variable
    VALUES (recursively). These are Jinja2 template expressions that should
    never appear in user-supplied variable values — they should be literal
    strings.

    Args:
        vars_dict: The extra_vars dictionary to validate.
        allow_jinja2_in_extravars: If True, skip validation (documented risk).

    Raises:
        ValueError: If Jinja2 injection patterns are detected.
    """
    if allow_jinja2_in_extravars:
        return

    def _check_value(value: object, path: str) -> None:
        if isinstance(value, str):
            if _JINJA2_EXPR.search(value):
                raise ValueError(
                    f"Jinja2 SSTI pattern detected in extra_vars at "
                    f"'{path}': found {{ expression }} syntax. Set "
                    f"allow_jinja2_in_extravars=True only if you understand "
                    f"the SSTI->RCE risk."
                )
            if _JINJA2_BLOCK.search(value):
                raise ValueError(
                    f"Jinja2 SSTI pattern detected in extra_vars at "
                    f"'{path}': found {{% block %}} syntax. Set "
                    f"allow_jinja2_in_extravars=True only if you understand "
                    f"the SSTI->RCE risk."
                )
            if _JINJA2_COMMENT.search(value):
                raise ValueError(
                    f"Jinja2 SSTI pattern detected in extra_vars at "
                    f"'{path}': found {{# comment #}} syntax. Set "
                    f"allow_jinja2_in_extravars=True only if you understand "
                    f"the SSTI->RCE risk."
                )
        elif isinstance(value, dict):
            for k, v in value.items():
                _check_value(v, f"{path}.{k}" if path else str(k))
        elif isinstance(value, (list, tuple)):
            for i, item in enumerate(value):
                _check_value(item, f"{path}[{i}]")

    _check_value(vars_dict, "")


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
    except ImportError:
        logger.warning("jinja2 not available — skill body will be included as raw text (no template rendering applied)")
        return raw


def _inject_retrieval_context(
    system_prompt: str,
    job: JobSpec,
    searcher: Any | None,
    workspace_path: str | None,
    *,
    top_k: int = 5,
) -> str:
    if searcher is None or workspace_path is None:
        return system_prompt

    query = job.prompt_text or job.todo_id or ""
    if not query or not query.strip():
        return system_prompt

    try:
        results = searcher.search(query, top_k=top_k)
    except Exception:
        logger.debug("semantic search failed", exc_info=True)
        return system_prompt

    if not results:
        return system_prompt

    lines: list[str] = []
    lines.append("\nRelevant Codebase Context:")
    for i, result in enumerate(results, 1):
        filepath = result.get("filepath", "unknown")
        content = result.get("content", "")
        score = result.get("score", 0.0)
        lines.append(f"\n[{i}] {filepath} (score: {score:.2f}):")
        preview = content[:1500]
        if len(content) > 1500:
            preview = preview + "\n... (truncated)"
        lines.append(preview)

    context_block = "\n".join(lines)
    return system_prompt + context_block


def _build_system_prompt(
    job: JobSpec,
    behavior: AgentBehavior | None = None,
    searcher: Any | None = None,
    workspace_path: str | None = None,
) -> str:
    lines: list[str] = []
    lines.append("You are a coding agent. Generate code changes for the following task.")
    if job.skill_body:
        rendered = _render_skill_body(job.skill_body)
        lines.append(f"\nGuidelines:\n{rendered}")
    lines.append("\nOutput format:")
    lines.append("- Use fenced code blocks for code.")
    lines.append("- Prefix each file with 'FILE: <path>' followed by the content.")
    base = "\n".join(lines)
    if behavior is not None:
        from general_ludd.retrieval.agentic_context import AgenticContextInjector

        renderer = BehaviorRenderer(prompt_enhancer=AgenticContextInjector())
        behavior_block = renderer.render(behavior)
        base = behavior_block + "\n\n" + base

    base = _inject_retrieval_context(base, job, searcher, workspace_path)
    return base


def _build_user_prompt(job: JobSpec) -> str:
    return job.prompt_text or f"Task: {job.todo_id}\nWork type: {job.work_type}"


def _run_tests(workspace: str) -> tuple[int, str]:
    """Run the workspace's declared test command via ProjectCommandRunner.

    Migrated (WP-E2) from a hardcoded ``["make", "test"]`` to the
    ProjectCommandRunner adapter so a polyglot target project runs its own
    declared test command (pytest / npm / cargo / go test…) instead of
    assuming make. Zero behavior change for gludd's own workspace: its
    project.yml declares ``test: make test``, so the resolved argv is still
    ``['make', 'test']``.

    The 120s timeout + own-process-group kill semantics are preserved by
    forwarding ``timeout_s=120`` to ``ProjectCommandRunner.run``, which itself
    uses ``start_new_session`` + ``os.killpg`` on timeout (see
    ``project_runner/runner.py``). The ``tuple[int, str]`` return shape is
    preserved because both call sites unpack it.
    """
    try:
        profile = load_project_profile(workspace)
        runner = ProjectCommandRunner(workspace, profile)
        result = runner.run("test", timeout_s=120)
    except ProjectProfileError as exc:
        return 0, f"No test command available ({exc})"
    except Exception as exc:
        return 1, f"Test run failed: {exc}"
    if result.timed_out:
        return 1, "Test run timed out after 120s"
    if result.error:
        if "not found" in result.error.lower():
            return 0, f"No test command available ({result.error})"
        return 1, f"Test run failed: {result.error}"
    exit_code = result.exit_code if result.exit_code is not None else 1
    output = result.stdout_tail or result.stderr_tail or "(no output)"
    return exit_code, output[-2000:]


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
        workspace_path: str | None = None,
        benchmark_recorder: Any = None,
        metrics_collector: Any = None,
        budget_guard: Any = None,
        behavior: AgentBehavior | None = None,
        searcher: Any = None,
        sandbox_enforcer: Any = None,
        spend_limiter: SpendLimiter | None = None,
    ) -> None:
        self._model_gateway = model_gateway
        workspace = (
            project_state().directory("execution", "workspace")
            if workspace_path is None
            else secure_directory(workspace_path)
        )
        self.workspace_path = str(workspace)
        self._benchmark_recorder = benchmark_recorder
        self._metrics_collector = metrics_collector
        self._budget_guard = budget_guard
        self._behavior = behavior
        self._searcher = searcher
        self._sandbox_enforcer = sandbox_enforcer
        self._spend_limiter = spend_limiter
        self._sandbox_verified = False
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._commit_lock: asyncio.Lock = asyncio.Lock()

    def _verify_sandbox(self) -> str | None:
        if self._sandbox_enforcer is None:
            return None
        try:
            self._sandbox_enforcer.verify_ready()
        except Exception:
            logger.warning("Sandbox readiness verification failed", exc_info=True)
            return "Sandbox enforcement failed: sandbox unavailable"

        try:
            confined_workspace = self._sandbox_enforcer.confine_path(self.workspace_path)
        except Exception:
            logger.warning(
                "Execution workspace is outside the configured sandbox jail",
                exc_info=True,
            )
            return "Sandbox enforcement failed: workspace is outside configured jail"

        self.workspace_path = str(confined_workspace)
        self._sandbox_verified = True
        return None

    def _projected_cost(self) -> float:
        """Compute per-call projected cost from the default model profile.

        Mirrors the daemon ``_gateway_executor`` projection
        (daemon.py ~1839-1852): SpendLimiter's ``token_cost_usd`` when the
        guard exposes it (PricingCatalog primary), static
        ``infra.pricing.token_cost_usd()`` fallback otherwise.

        Security finding #14: the engine pre-check must NOT pass a hardcoded
        ``0.0`` — that makes the gate reactive-only (it cannot block the call
        whose own projection crosses the cap, only react to prior calls'
        cumulative spend). Returning a positive projection makes the engine
        pre-check proactive, matching the daemon path.

        Returns:
            Projected USD cost, or ``0.0`` when no gateway / no profile is
            available (legacy callers that construct ``ExecutionEngine``
            without a model_gateway keep the documented 0.0 semantics).
        """
        if self._model_gateway is None:
            return 0.0
        try:
            profile = self._model_gateway.get_profile("default")
        except Exception:
            logger.debug("_projected_cost: get_profile raised", exc_info=True)
            return 0.0
        if profile is None:
            return 0.0
        try:
            model = getattr(profile, "model_name", None) or "__default__"
            proj_in = min(int(getattr(profile, "max_input_tokens", 1000) or 1000), 1000)
            proj_out = int(getattr(profile, "max_output_tokens", 0) or 0)
        except (TypeError, ValueError):
            logger.debug(
                "_projected_cost: profile attribute coercion failed; using conservative defaults",
                exc_info=True,
            )
            model = "__default__"
            proj_in = 1000
            proj_out = 0

        cost_sources = (self._spend_limiter, self._budget_guard)
        for cost_source in cost_sources:
            source_cost = getattr(cost_source, "token_cost_usd", None)
            if callable(source_cost):
                try:
                    return float(source_cost(model, proj_in, proj_out))
                except Exception:
                    logger.debug(
                        "_projected_cost: configured token_cost_usd raised; trying fallback",
                        exc_info=True,
                    )
        try:
            from general_ludd.infra.pricing import token_cost_usd as _static

            return float(_static(model, proj_in, proj_out))
        except Exception:
            logger.debug(
                "_projected_cost: pricing lookup failed; using conservative default %.6f per 1k tokens",
                _UNKNOWN_MODEL_COST_PER_1K,
                exc_info=True,
            )

        return (proj_in + proj_out) / 1000.0 * _UNKNOWN_MODEL_COST_PER_1K

    def _spend_reserve(self, projected_cost_usd: float | None) -> tuple[str | None, str | None]:
        """Atomically reserve projected spend; return ``(token, denial)``.

        A configured cap fails closed when projection, limiter state, or the
        reservation operation is not trustworthy. An uncapped limiter records
        actual usage after the call without reserving.
        """
        limiter = self._spend_limiter
        if limiter is None:
            return None, None
        try:
            cap_configured = limiter.cap_configured
        except Exception:
            logger.warning("SpendLimiter cap state unavailable", exc_info=True)
            return None, "spend limiter state unavailable"
        if not cap_configured:
            return None, None
        if (
            not isinstance(projected_cost_usd, (int, float))
            or not math.isfinite(projected_cost_usd)
            or projected_cost_usd <= 0
        ):
            return None, "projected spend is unknown under a configured cap"
        try:
            token = limiter.reserve(float(projected_cost_usd))
        except Exception:
            logger.warning("SpendLimiter reservation failed", exc_info=True)
            return None, "spend reservation failed"
        if not isinstance(token, str) or not token:
            try:
                remaining = limiter.remaining()
            except Exception:
                return None, "spend limit exceeded"
            return None, f"spend limit exceeded: remaining=${remaining:.6f}"
        return token, None

    def _spend_record_actual(
        self,
        actual_cost_usd: object,
        *,
        reservation_token: str | None,
        projected_cost_usd: float | None,
        model: str | None,
        project_id: str | None,
    ) -> None:
        """Commit actual spend, conservatively retaining the projection.

        Records remain unflushed on the shared limiter so the daemon's single
        EventLoop watermark writer persists them exactly once.
        """
        limiter = self._spend_limiter
        if limiter is None:
            return
        try:
            actual = float(cast(Any, actual_cost_usd))
        except (TypeError, ValueError):
            actual = math.nan
        if not math.isfinite(actual) or actual < 0:
            if (
                isinstance(projected_cost_usd, (int, float))
                and math.isfinite(projected_cost_usd)
                and projected_cost_usd > 0
            ):
                actual = float(projected_cost_usd)
                logger.warning("Model cost unavailable; retaining projected spend reservation")
            else:
                logger.warning("Model cost unavailable and no safe projection exists; skipping record")
                return
        if reservation_token is not None:
            try:
                committed = limiter.commit(
                    reservation_token,
                    actual,
                    kind="token",
                    model=model,
                    project_id=project_id,
                )
            except Exception:
                logger.error(
                    "SpendLimiter commit failed; projected reservation remains charged",
                    exc_info=True,
                )
                return
            if not committed:
                logger.error("SpendLimiter rejected an active reservation commit")
            return
        try:
            limiter.record(
                actual,
                kind="token",
                model=model,
                project_id=project_id,
            )
        except Exception:
            logger.error("SpendLimiter actual-cost record failed", exc_info=True)

    def _spend_release(self, reservation_token: str | None) -> None:
        if self._spend_limiter is None or reservation_token is None:
            return
        try:
            if not self._spend_limiter.release(reservation_token):
                logger.error("SpendLimiter rejected reservation release")
        except Exception:
            logger.error("SpendLimiter reservation release failed", exc_info=True)

    def _budget_pre_check(self, guard: Any, projected_cost: float | None = None) -> str | None:
        """Run budget pre-check; return denial string or None (allowed).

        Fail-CLOSED: any non-dict result, missing 'allowed' key, or unknown
        guard interface returns a denial string. Only guard=None is an
        intentional no-op (returns None = allowed).

        Security finding #14: thread ``projected_cost`` (computed from the
        default model profile when not supplied) so the pre-check is
        proactive — blocking the call whose own projection crosses the cap,
        not just reactive to prior calls' cumulative spend. A 0.0 projection
        (no gateway / no profile) keeps the documented reactive-only
        semantics: it only blocks when already over the cap.
        """
        if guard is None:
            return None
        if projected_cost is None:
            projected_cost = self._projected_cost()
        if hasattr(guard, "check_all_limits"):
            try:
                verdict = guard.check_all_limits(estimated_cost=projected_cost)
            except Exception as exc:
                return f"budget check raised: {exc}"
            if not isinstance(verdict, dict):
                return "budget check returned non-dict"
            if not verdict.get("allowed", False):
                return str(verdict.get("reason", "budget exhausted"))
            return None
        if hasattr(guard, "try_charge"):
            try:
                verdict = guard.try_charge(cost=projected_cost)
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
            # GAP-30: the previous call passed model_profile=/work_type=/cost_usd=
            # and omitted the two REQUIRED positional args (agent_id, model_id),
            # so every call raised TypeError that the bare except swallowed — no
            # model call (success OR failure) was ever recorded. Pass the real
            # signature exactly: record_model_call(agent_id, model_id,
            # input_tokens, output_tokens, success, error=None). No extra kwargs:
            # they are forwarded into ModelUsage(**kwargs) / AgentMetrics and an
            # unknown field there would raise TypeError all over again.
            self._metrics_collector.record_model_call(
                agent_id=getattr(job, "agent_id", None) or "engine",
                model_id=getattr(job, "model_profile", None) or "unknown",
                input_tokens=tokens,
                output_tokens=0,
                success=success,
            )
        except Exception:
            # Metrics must never break execution, but do NOT swallow silently —
            # a silent swallow is exactly what hid GAP-30 for so long.
            logger.debug("metrics record_model_call failed", exc_info=True)

    def defer_commit(self, path: str, message: str) -> None:
        """Schedule a git commit as a background asyncio task (non-blocking).

        FIX: the done-callback inspects task.exception() and logs at ERROR so
        commit failures are observable rather than silently swallowed.

        Concurrent background commits are serialized via asyncio.Lock so git
        index operations never race.
        """

        async def _commit_with_lock() -> str | None:
            async with self._commit_lock:
                return await _git_commit_async(path, message)

        try:
            task: asyncio.Task[str | None] = asyncio.create_task(_commit_with_lock())
            self._background_tasks.add(task)

            def _on_commit_done(t: asyncio.Task[str | None]) -> None:
                self._background_tasks.discard(t)
                exc = t.exception() if not t.cancelled() else None
                if exc is not None:
                    logger.error("defer_commit: background commit failed: %s", exc)

            task.add_done_callback(_on_commit_done)
        except RuntimeError:
            pass  # No running event loop

    async def shutdown(self) -> None:
        """Cancel and await all pending background tasks.

        Must be called before the engine is discarded so no task
        silently leaks its exception or outlives the event loop.
        """
        if self._background_tasks:
            for task in list(self._background_tasks):
                if not task.done():
                    task.cancel()
            await asyncio.gather(
                *self._background_tasks,
                return_exceptions=True,
            )
            self._background_tasks.clear()

    async def execute_async(self, job: JobSpec) -> TaskReturn:
        """Async variant of execute(): defers the git commit step to background."""
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

        sandbox_error = self._verify_sandbox()
        if sandbox_error is not None:
            return TaskReturn(
                return_id=return_id,
                todo_id=job.todo_id,
                job_id=job.job_id,
                playbook=job.playbook or "code",
                queue=job.queue or "core",
                exit_code=1,
                result_summary=sandbox_error,
            )

        is_git = _is_git_repo(self.workspace_path)
        title_slug = _slugify(job.prompt_text or job.todo_id or "untitled")
        branch_name = f"gludd/{job.todo_id}-{title_slug}"
        if is_git:
            _git_create_branch(self.workspace_path, branch_name)

        system_prompt = _build_system_prompt(
            job,
            behavior=self._behavior,
            searcher=self._searcher,
            workspace_path=self.workspace_path,
        )
        user_prompt = _build_user_prompt(job)

        projected_cost = self._projected_cost()
        denial = self._budget_pre_check(self._budget_guard, projected_cost)
        if denial is not None:
            return TaskReturn(
                return_id=return_id,
                todo_id=job.todo_id,
                job_id=job.job_id,
                playbook=job.playbook or "code",
                queue=job.queue or "core",
                exit_code=1,
                result_summary=f"Budget check failed: {denial}",
            )

        reservation_token, spend_denial = self._spend_reserve(projected_cost)
        if spend_denial is not None:
            return TaskReturn(
                return_id=return_id,
                todo_id=job.todo_id,
                job_id=job.job_id,
                playbook=job.playbook or "code",
                queue=job.queue or "core",
                exit_code=1,
                result_summary=f"Spend check failed: {spend_denial}",
            )

        try:
            profile_id = getattr(job, "model_profile", None) or "default"
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            response = await asyncio.to_thread(
                self._model_gateway.call_model,
                profile_id,
                messages=messages,
                work_type=job.work_type or "code",
            )
            try:
                actual_cost: object = getattr(response, "cost_estimate", None)
            except Exception:
                actual_cost = None
            self._spend_record_actual(
                actual_cost,
                reservation_token=reservation_token,
                projected_cost_usd=projected_cost,
                model=profile_id,
                project_id=getattr(job, "project_id", None),
            )
            reservation_token = None
            model_output = getattr(response, "content", "") or str(response)
            self._record_metrics(job, success=True, tokens=len(model_output) // 4)
        except Exception as exc:
            self._spend_release(reservation_token)
            self._record_metrics(job, success=False)
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
        try:
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
                fallback_files, fallback_applied = self._fallback_extract_code(job, blocks)
                if fallback_applied:
                    changed_files.extend(fallback_files)
                    applied_changes = True
        except ValueError:
            logger.warning(
                "Rejected unsafe model-supplied path for job %s",
                job.job_id,
            )
            return TaskReturn(
                return_id=return_id,
                todo_id=job.todo_id,
                job_id=job.job_id,
                playbook=job.playbook or "code",
                queue=job.queue or "core",
                exit_code=1,
                result_summary="Model output rejected by workspace/sandbox policy",
            )

        if not applied_changes:
            return TaskReturn(
                return_id=return_id,
                todo_id=job.todo_id,
                job_id=job.job_id,
                playbook=job.playbook or "code",
                queue=job.queue or "core",
                exit_code=1,
                result_summary="No changes parsed from model output",
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

        test_exit_code, test_summary = await asyncio.to_thread(_run_tests, self.workspace_path)
        evidence_refs: list[str] = list(changed_files[:20])

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

                def _on_benchmark_done(t: asyncio.Task[Any]) -> None:
                    self._background_tasks.discard(t)
                    exc = t.exception() if not t.cancelled() else None
                    if exc is not None:
                        logger.error("benchmark background task failed: %s", exc)

                task.add_done_callback(_on_benchmark_done)
            except RuntimeError:
                pass  # No running event loop

        summary_parts: list[str] = [
            f"Changed {len(changed_files)} file(s): {', '.join(changed_files[:10])}.",
        ]
        if not is_git:
            summary_parts.append("WARNING: Workspace is not a git repository.")
        summary_parts.append("Commit deferred to background.")
        summary_parts.append(f"Tests: exit={test_exit_code}. {test_summary[:500]}")

        return TaskReturn(
            return_id=return_id,
            todo_id=job.todo_id,
            job_id=job.job_id,
            playbook=job.playbook or "code",
            queue=job.queue or "core",
            exit_code=test_exit_code,
            result_summary=" ".join(summary_parts),
            artifacts=evidence_refs,
            diff_ref=f"raw_output:{len(model_output)} chars",
            test_results_ref=f"exit_code={test_exit_code}",
        )

    # sync execute() removed — migration residue with zero prod callers (C-ENGINE)

    def _fallback_extract_code_not_a_method(self, job: JobSpec) -> TaskReturn:
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

        sandbox_denial = self._verify_sandbox()
        if sandbox_denial is not None:
            return TaskReturn(
                return_id=return_id,
                todo_id=job.todo_id,
                job_id=job.job_id,
                playbook=job.playbook or "code",
                queue=job.queue or "core",
                exit_code=1,
                result_summary=sandbox_denial,
            )

        is_git = _is_git_repo(self.workspace_path)

        title_slug = _slugify(job.prompt_text or job.todo_id or "untitled")
        branch_name = f"gludd/{job.todo_id}-{title_slug}"
        if is_git:
            _git_create_branch(self.workspace_path, branch_name)

        system_prompt = _build_system_prompt(
            job,
            behavior=self._behavior,
            searcher=self._searcher,
            workspace_path=self.workspace_path,
        )
        user_prompt = _build_user_prompt(job)

        denial = self._budget_pre_check(self._budget_guard)
        if denial is not None:
            return TaskReturn(
                return_id=return_id,
                todo_id=job.todo_id,
                job_id=job.job_id,
                playbook=job.playbook or "code",
                queue=job.queue or "core",
                exit_code=1,
                result_summary=f"Budget check failed: {denial}",
            )

        try:
            profile_id = getattr(job, "model_profile", None) or "default"
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            response = self._model_gateway.call_model(profile_id, messages=messages, work_type=job.work_type or "code")
            model_output = getattr(response, "content", "") or str(response)
            self._record_metrics(job, success=True, tokens=len(model_output) // 4)
        except Exception as exc:
            self._record_metrics(job, success=False)
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
            fallback_files, fallback_applied = self._fallback_extract_code(job, blocks)
            if fallback_applied:
                changed_files.extend(fallback_files)
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
                return_id=return_id,
                todo_id=job.todo_id,
                job_id=job.job_id,
                playbook=job.playbook or "code",
                queue=job.queue or "core",
                exit_code=1,
                result_summary="No changes parsed from model output",
                artifacts=[f"raw_output:{len(model_output)} chars"],
            )

        test_exit_code, test_summary = _run_tests(self.workspace_path)
        evidence_refs: list[str] = list(changed_files[:20])
        if commit_sha:
            evidence_refs.append(f"commit:{commit_sha}")
            evidence_refs.append(f"branch:{_git_current_branch(self.workspace_path)}")

        summary_parts: list[str] = [
            f"Changed {len(changed_files)} file(s): {', '.join(changed_files[:10])}.",
        ]
        if not is_git:
            summary_parts.append("WARNING: Workspace is not a git repository.")
        if commit_sha:
            summary_parts.append(f"Committed as {commit_sha}.")
        summary_parts.append(f"Tests: exit={test_exit_code}. {test_summary[:500]}")

        result = TaskReturn(
            return_id=return_id,
            todo_id=job.todo_id,
            job_id=job.job_id,
            playbook=job.playbook or "code",
            queue=job.queue or "core",
            exit_code=test_exit_code,
            result_summary=" ".join(summary_parts),
            artifacts=evidence_refs,
            diff_ref=(f"commit:{commit_sha}" if commit_sha else f"raw_output:{len(model_output)} chars"),
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

                def _on_benchmark_done(t: asyncio.Task[Any]) -> None:
                    self._background_tasks.discard(t)
                    exc = t.exception() if not t.cancelled() else None
                    if exc is not None:
                        logger.error("benchmark background task failed: %s", exc)

                task.add_done_callback(_on_benchmark_done)
            except RuntimeError:
                pass  # No running event loop

        return result

    def _fallback_extract_code(self, job: JobSpec, blocks: list[dict[str, str]]) -> tuple[list[str], bool]:
        """Fallback: extract code from fenced Python blocks when no FILE: markers exist.

        When a model (e.g. DeepSeek) doesn't emit ``FILE: <path>`` markers but
        generates valid Python code in fenced blocks, this auto-assigns a file
        path and writes the code so the job doesn't silently fail.
        """
        python_blocks: list[dict[str, str]] = [
            b for b in blocks if b["language"].lower() in ("python", "py", "") and b["content"].strip()
        ]
        if not python_blocks:
            return [], False

        logger.warning(
            "Model output contains %d Python fenced block(s) but no FILE: "
            "markers. Falling back to auto-assigned file path.",
            len(python_blocks),
        )

        target_path: str | None = None
        workspace = os.path.realpath(self.workspace_path)
        if os.path.isdir(workspace):
            for root, dirs, files in os.walk(workspace):
                dirs[:] = [
                    d
                    for d in dirs
                    if not d.startswith(".")
                    and d
                    not in (
                        "__pycache__",
                        "node_modules",
                        ".git",
                        ".venv",
                        "venv",
                        "dist",
                    )
                ]
                for f in files:
                    if f.endswith(".py") and not f.startswith("."):
                        target_path = os.path.relpath(os.path.join(root, f), workspace)
                        break
                if target_path:
                    break

        if target_path is None:
            title = job.prompt_text or job.todo_id or "generated"
            target_path = _slugify(title, max_len=30) + ".py"

        first_block = python_blocks[0]
        self._write_file(target_path, first_block["content"])
        return [target_path], True

    def _resolve_in_workspace(self, file_path: str) -> str:
        """Resolve ``file_path`` against the workspace, refusing any escape.

        The model supplies these paths. An absolute path (``/etc/passwd``) or a
        ``../`` traversal would otherwise let a write/patch land OUTSIDE the
        workspace. We jail it: resolve the realpath of both the workspace base and
        the candidate, and refuse unless the candidate is contained in the base
        (via os.path.commonpath). Returns the safe absolute path.

        When a sandbox enforcer is active and verified, the resolved path is
        additionally confined to the sandbox jail directory (fail-closed).
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
                f"refusing path that escapes the workspace: {file_path!r} (resolved to {full!r}, base {base!r})"
            )

        if self._sandbox_enforcer is not None and self._sandbox_verified:
            try:
                confined = self._sandbox_enforcer.confine_path(full)
                return cast(str, confined)
            except Exception as exc:
                raise ValueError(f"refusing path that escapes the sandbox: {file_path!r}: {exc}") from exc

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
            with tempfile.NamedTemporaryFile(mode="w", suffix=".diff", delete=False) as f:
                f.write(diff_text)
                diff_path = f.name
            # argv is list-form (never a shell string): diff_path and jail are
            # caller/LLM-adjacent, so list-form keeps them out of shell parsing.
            result = subprocess.run(
                [
                    "patch",
                    "-p1",
                    "-d",
                    jail,
                    "-i",
                    diff_path,
                    "--force",
                    "--no-backup-if-mismatch",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            # Only report files as changed when patch actually succeeded; a
            # rejected/failed patch must not be advertised as an applied change.
            if result.returncode != 0:
                logger.warning(
                    "patch exited %s; treating diff as not applied: %s",
                    result.returncode,
                    (result.stderr or result.stdout)[:500],
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
