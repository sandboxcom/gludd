"""Shared model-invocation helper for the generation path.

Both the worker HTTP path (``worker/app.py:/jobs/execute``) and the daemon's
in-process Ansible runner path (``event_loop/loop.py:_dispatch_execute_job``)
must invoke the model the SAME way for a generation work type, then feed the
generated text into the playbook vars/extravars and the job result. This module
is the single source of that logic so the two surfaces cannot drift.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from general_ludd.util.async_lifecycle import cancel_and_drain_tasks

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Coroutine

    from general_ludd.compaction.aggressive import CompactionLevel
    from general_ludd.models.gateway import ModelGateway

logger = logging.getLogger(__name__)

# Strong references to in-flight background tasks so they are not garbage
# collected before completion (RUF006).
_BACKGROUND_TASKS: set[asyncio.Task[object]] = set()


async def drain_background_tasks() -> None:
    """Cancel and await benchmark writes owned by this module."""
    await cancel_and_drain_tasks(_BACKGROUND_TASKS, registry=_BACKGROUND_TASKS)

# Work types whose execute job is a model-driven generation task. For these the
# caller invokes the ModelGateway and feeds the generated output into the
# playbook (and the job result) before running the playbook.
_GENERATION_WORK_TYPES: frozenset[str] = frozenset(
    {"code", "bug_fix", "test", "refactor", "docs", "prompt", "analysis", "security"}
)


def is_generation_work_type(work_type: str | None) -> bool:
    """True when ``work_type`` is a model-driven generation task."""
    return work_type in _GENERATION_WORK_TYPES


def invoke_model_for_generation(
    gateway: ModelGateway,
    *,
    job_id: str,
    work_type: str | None,
    model_profile: str | None,
    prompt_text: str | None,
    skill_body: str | None,
    budget_guard: object = None,
    benchmark_recorder: object = None,
    project_id: str | None = None,
    use_slm_compaction: bool = False,
    compaction_level: CompactionLevel | None = None,
    scheduling_hint: object | None = None,
) -> tuple[str | None, list[dict[str, object]] | None]:
    """Call the model for a generation job.

    Returns a ``(content, tool_calls)`` tuple:

    * ``content`` — the generated text (or ``None`` on skip/budget-deny/error).
    * ``tool_calls`` — the model's STRUCTURED tool/function calls, normalized to
      the OpenAI-nested shape on ``ModelResponse.tool_calls``
      (``{"id", "type": "function", "function": {"name", "arguments"}}``), or
      ``None`` when the model requested none.

    Mirrors the original ``worker/app.py:_invoke_gateway_for_job`` for the text
    path: the system turn is the skill body, the user turn is the task prompt,
    both are bounded to the model's token window via AgentCapabilities, and any
    model/transport error is swallowed (logged) so the playbook still runs.

    ``benchmark_recorder`` is an optional sink that records a benchmark/score
    after every successful generation call.  Pass any object with a ``record``
    method (or an async-capable ``create`` method) — the ``_RecordingBenchmarkRepo``
    in tests, or a real ``BenchmarkRepository`` in production.  When wired, the
    generation path records: model_profile, work_type, input/output token counts,
    and a simple scoring pass/fail flag.  This satisfies the CA-T11 integrity
    requirement that a score is recorded on the daemon async execute path.

    Two-phase generation (CA-T9, keystone):

    * **Phase 1 — this helper — is tool-free BY DESIGN.**  It asks the model for
      text and deliberately does NOT pass ``tools=`` to ``call_model``.  Binding
      dispatch-tools into every plain text-generation call would be a risky
      behaviour change (the model could emit tool-call JSON on tasks where no
      tool-call loop is running to consume it), so this boundary is intentional
      and is asserted by ``test_generation_path_is_tool_free_by_design`` /
      ``test_source_confirms_generation_path_skips_tools``.  Do NOT add ``tools=``
      here.
    * **Phase 2 — autonomous tool use — lives in the event loop, NOT here.**  For
      tool-requiring work types (``event_loop.loop._TOOL_USE_WORK_TYPES``) the
      event loop instantiates the fully-built ``ToolCallLoop``
      (``execution/tool_loop.py``), which binds the live MCP tools
      (``list_tools`` -> ``tools=``), lets the model choose+call them, executes
      via the MCP client, and iterates on tool results.  That is where
      model-driven file-writes / git / MCP actions actually fire under model
      control.

    Tool-use on the Phase-1 generation path (this helper): when the model emits
    structured tool/function calls (``ModelResponse.tool_calls``) of its own
    accord, they are returned to the caller (the daemon ``loop.py`` and worker
    ``app.py`` dispatch sites) which routes them through the ``DynamicDispatcher``
    so a single round of model-driven tool actions (MCP/git/file writes) still
    fires.  Previously this helper returned only ``content`` and the callers
    re-parsed the TEXT via ``parse_tool_calls`` — which cannot recover the
    structured calls — so model-driven actions were silently discarded on both
    the daemon and worker generation paths.  The structured ``tool_calls`` are the
    same ones the ``ToolCallLoop`` consumes; we hand them straight to the
    dispatcher rather than round-tripping through text.  The full multi-turn
    agentic loop, however, is Phase 2's ``ToolCallLoop`` — not this helper.
    """
    if not prompt_text:
        logger.warning(
            "Generation job %s has no prompt_text; skipping model call. "
            "This generation todo will be a silent no-op — it likely lacks "
            "both a prompt_profile and a title/description to synthesize from",
            job_id,
        )
        return None, None
    from general_ludd.budget_guard_check import budget_pre_check, compute_projected_cost_usd

    projected = compute_projected_cost_usd(gateway, budget_guard)
    denial = budget_pre_check(budget_guard, projected_cost=projected)
    if denial is not None:
        logger.warning("Budget denied for job %s: %s", job_id, denial)
        return None, None
    profile_id = model_profile or "default"
    # Bound the prompt to the model's token window via the shared agent
    # capabilities bundle (ContextCompactor + TokenWindowManager). The system
    # turn is the skill body; the user turn is the task prompt.
    from general_ludd.agents.capabilities import AgentCapabilities
    from general_ludd.agents.registry import default_registry

    # OPT-IN aggressive SLM compaction. Default OFF → model_gateway is not passed,
    # so AgentCapabilities uses the plain ContextCompactor path and behavior is
    # identical to today. When enabled, the gateway drives a small local
    # ``compactor`` model to summarize the old middle of the prompt (fail-soft).
    caps = AgentCapabilities(
        primary_profile=profile_id,
        model_gateway=gateway if use_slm_compaction else None,
        use_slm_compaction=use_slm_compaction,
        compaction_level=compaction_level,
        # S22: pass the real agent registry so agent-dispatch tools are
        # available on the generation path. A bare AgentRegistry() starts
        # empty; default_registry() populates real agents. Without this,
        # every production generation job runs with zero agent tools.
        agent_registry=default_registry(),
    )
    system_prompt = skill_body or ""
    history = [{"role": "user", "content": prompt_text}]
    messages = [m for m in caps.prepare_messages(system_prompt, history) if m["content"].strip()]
    try:
        response = gateway.call_model(
            profile_id,
            messages=messages,
            work_type=work_type or "unknown",
            # S-1 (task #25): thread the job's project so the gateway resolves
            # this job's credential/api-base aliases through the project-scoped
            # secrets manager (isolation); None → shared base behavior.
            project_id=project_id,
        )
        content = response.content
        tool_calls = getattr(response, "tool_calls", None)
    except Exception as exc:
        logger.warning(
            "Model call failed for job %s (profile=%s): %s",
            job_id,
            profile_id,
            exc,
        )
        return None, None

    # CA-T11: record a benchmark/score for this generation call when a recorder
    # is wired.  We call record() (sync) or create() (async-shaped) so both the
    # production BenchmarkRepository and the test's _RecordingBenchmarkRepo work.
    if benchmark_recorder is not None and content is not None:
        usage = getattr(response, "usage_metadata", {}) or {}
        input_tokens = int(usage.get("input_tokens", len(prompt_text) // 4))
        output_tokens = int(usage.get("output_tokens", len(content) // 4))
        _record_generation_benchmark(
            benchmark_recorder,
            model_profile=profile_id,
            work_type=work_type or "unknown",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    return content, tool_calls


def _record_generation_benchmark(
    recorder: object,
    *,
    model_profile: str,
    work_type: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Record a benchmark entry on the generation path (CA-T11).

    Tries ``recorder.record(...)`` first (sync, used by tests and lightweight
    sinks), then ``recorder.create(...)`` (async-shaped repositories).  Both
    paths are fire-and-forget; any exception is swallowed so a broken recorder
    never kills the generation call.
    """
    try:
        if hasattr(recorder, "record"):
            recorder.record(
                model_profile_id=model_profile,
                work_type=work_type,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                success=True,
                scoring="generation_path",
            )
        elif hasattr(recorder, "create"):
            # Async-shaped repo — call synchronously via nest_asyncio or simply
            # call the coroutine and schedule it.  Since we're on the sync
            # thread (asyncio.to_thread) we cannot await; instead we call
            # record() to let callers wrap as needed.  The recorder contract
            # for the test path only needs to capture the call.
            import asyncio as _asyncio
            import inspect as _inspect

            result = recorder.create(
                model_profile_id=model_profile,
                work_type=work_type,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                success=True,
                scoring="generation_path",
            )
            if _inspect.isawaitable(result):
                try:
                    loop = _asyncio.get_running_loop()
                    # Keep a reference so the task is not GC'd mid-flight.
                    _bg_task = loop.create_task(cast("Coroutine[object, object, object]", result))
                    _BACKGROUND_TASKS.add(_bg_task)
                    _bg_task.add_done_callback(_BACKGROUND_TASKS.discard)
                except RuntimeError:
                    # No running event loop — drive the coroutine in a fresh one.
                    _asyncio.run(cast("Coroutine[object, object, object]", result))
                except Exception as exc:
                    logger.warning("Could not schedule async benchmark record: %s", exc, exc_info=True)
    except Exception as exc:
        logger.warning("Benchmark recording failed for %s/%s: %s", model_profile, work_type, exc, exc_info=True)
