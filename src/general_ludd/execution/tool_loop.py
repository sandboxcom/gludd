"""MCP tool integration for model gateway calls.

Extends ExecutionEngine to handle tool-call loops where the model
requests tools via function calling, the engine executes them via MCP,
and the results are fed back to continue the conversation.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import TYPE_CHECKING, Any, cast

from general_ludd.compaction.aggressive import compact_dicts
from general_ludd.mcp.registry import MCPToolRegistry
from general_ludd.mcp.transport import MCPTransportError
from general_ludd.routers.dispatch import MAX_CALLS_PER_REQUEST
from general_ludd.schemas.job import JobSpec
from general_ludd.security.capability_lattice import check_dispatch, role_may_dispatch

if TYPE_CHECKING:
    from general_ludd.compaction.aggressive import CompactionLevel
    from general_ludd.models.gateway import ModelResponse

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 10
PER_TOOL_TIMEOUT_SECONDS = 30
CODE_MAX_ITERATIONS = 5
MAX_TOTAL_TOKENS_DEFAULT = 100_000
PER_ITERATION_TIMEOUT_DEFAULT = 300.0
MODEL_GATEWAY_WORKERS = 10

_MODEL_GATEWAY_EXECUTOR = ThreadPoolExecutor(
    max_workers=MODEL_GATEWAY_WORKERS,
    thread_name_prefix="gludd-model-gateway",
)

#: C15 defect 2 — per-response tool-call cap. A single model response may bundle
#: an unbounded number of tool calls; ``max_iterations`` only bounds ROUNDS, not
#: the fan-out WITHIN one response. Truncate to the first N calls and answer the
#: rejected ``tool_call_id`` s so none is orphaned. Pinned equal to the HTTP
#: dispatch router's ``MAX_CALLS_PER_REQUEST`` (D-16) by a drift-guard test.
MAX_TOOL_CALLS_PER_RESPONSE = MAX_CALLS_PER_REQUEST


async def _run_model_call(
    call: Callable[..., Any],
    /,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Run one blocking model request on the bounded, namespaced pool."""
    running_loop = asyncio.get_running_loop()
    context = contextvars.copy_context()
    bound_call = partial(context.run, call, *args, **kwargs)
    return await running_loop.run_in_executor(_MODEL_GATEWAY_EXECUTOR, bound_call)


def _validate_tool_args(args: dict[str, Any], schema: dict[str, Any]) -> str | None:
    """Validate ``args`` against a tool's JSON ``input_schema`` (C15 defect 3).

    Returns ``None`` when the args are valid (or when the schema is empty — an
    empty ``input_schema`` is a no-op for backward compatibility, since
    ``MCPTool.input_schema`` defaults to ``{}`` and is never ``None``). Returns a
    compact human-readable error string when validation fails, so the caller can
    feed it back to the model as a ``role:"tool"`` message instead of dispatching
    an unvalidated payload to ``call_tool``.
    """
    if not schema:
        return None
    try:
        from jsonschema import Draft202012Validator
    except ImportError:  # pragma: no cover - jsonschema is a hard dep
        return None
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(args), key=lambda e: list(e.path))
    if not errors:
        return None
    first = errors[0]
    loc = "/".join(str(p) for p in first.path) or "<root>"
    return f"invalid args at {loc}: {first.message}"


class ToolLoopExhausted(RuntimeError):
    """Raised when the model-tool loop hits its max-iteration budget.

    The model was still requesting tool calls when the loop ran out of
    iterations, so there is no trustworthy final assistant answer to return.
    Callers must treat this as a failed run rather than acting on the trailing
    (usually empty / raw-repr) content that the old code silently returned.
    """


class ToolCallLoop:
    def __init__(
        self,
        model_gateway: Any,
        mcp_client: Any = None,
        max_iterations: int = MAX_TOOL_ITERATIONS,
        per_tool_timeout: float = PER_TOOL_TIMEOUT_SECONDS,
        mcp_registry: MCPToolRegistry | None = None,
        role: str | None = None,
        compaction_level: CompactionLevel | None = None,
        summarize_fn: Callable[[str, str], str] | None = None,
        tool_auditor: Any = None,
        situation_store: Any = None,
        budget_guard: Any = None,
        adversarial_detector: Any = None,
        max_total_tokens: int | None = None,
        per_iteration_timeout: float | None = None,
        work_type_max_iterations: dict[str, int] | None = None,
    ) -> None:
        self._gateway = model_gateway
        self._mcp_client = mcp_client
        self._max_iterations = max_iterations
        self._per_tool_timeout = per_tool_timeout
        # SLICE 2 (task #56): opt-in pre-call SLM context-compaction. When a
        # ``compaction_level`` is supplied, the ITERATIVE tool-loop history is
        # compacted BEFORE each model call so long tool conversations send fewer
        # tokens. ``summarize_fn`` is the small-model summarizer (from
        # ``compaction.slm.make_slm_summarize_fn``); None uses the deterministic
        # extractive fallback. BOTH default to None → the loop is byte-for-byte
        # identical to the pre-SLICE-2 behaviour (fully backward compatible), and
        # ``compact_dicts`` is itself fail-soft so compaction can never crash the
        # loop. The trailing (open) tool round is ALWAYS preserved verbatim so an
        # open ``tool_call_id`` is never orphaned — see ``run_with_tools``.
        self._compaction_level = compaction_level
        self._summarize_fn = summarize_fn
        self._auditor = tool_auditor
        self._situation_store = situation_store
        # Per-role capability gate (issue #58 lattice): when a role is supplied,
        # MCP tool use is gated through ``check_dispatch(role, "mcp")`` before any
        # tool is invoked. A role without the "mcp" dispatch capability is
        # refused fail-closed (CapabilityError) and no tool call is made. When
        # ``role`` is None the gate is skipped entirely, preserving the
        # pre-existing behaviour for callers that don't supply one.
        self._role = role
        # Finding 3 (capability gate): the registry of tools the MCP layer
        # actually advertises. Model-chosen tool names are resolved against it
        # to (a) reject any name the model invented / smuggled and (b) pin the
        # call to the tool's real server_id instead of a wildcard None.
        self._mcp_registry = mcp_registry
        if mcp_registry is None and mcp_client is not None:
            # Fall back to the facade's own registry when one wasn't passed
            # explicitly, so the gate is on by default whenever it can be.
            self._mcp_registry = getattr(mcp_client, "_registry", None)
        self._budget_guard = budget_guard
        self._adversarial_detector = adversarial_detector
        self._max_total_tokens = max_total_tokens or MAX_TOTAL_TOKENS_DEFAULT
        self._per_iteration_timeout = per_iteration_timeout or PER_ITERATION_TIMEOUT_DEFAULT
        self._work_type_max_iterations = work_type_max_iterations or {}

    def _resolve_server_id(self, tc_name: str) -> str:
        """Map a model-chosen tool name to its registered server_id.

        Capability gate (Finding 3): a tool name that is NOT in the registry is
        rejected outright with MCPTransportError — the model cannot reach an
        unadvertised tool, and a real server_id is always supplied (never None).
        """
        registry = self._mcp_registry
        if registry is None:
            raise MCPTransportError(f"MCP tool registry unavailable; refusing ungated tool call {tc_name!r}")
        tool = registry.get_tool(tc_name)
        if tool is None:
            # Distinguish ambiguous (same name on multiple servers) from unknown.
            if tc_name in registry.tool_names():
                raise MCPTransportError(
                    f"Tool {tc_name!r} is ambiguous across multiple servers; supply an explicit server_id"
                )
            raise MCPTransportError(f"Tool {tc_name!r} is not a registered MCP tool (capability gate); refusing call")
        if not tool.server_id:
            raise MCPTransportError(f"Tool {tc_name!r} is not a registered MCP tool (capability gate); refusing call")
        return tool.server_id

    def is_available(self) -> bool:
        return self._mcp_client is not None

    async def run_with_tools(
        self,
        job: JobSpec,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        if self._mcp_client is None:
            return cast(str, await self._call_model(job, system_prompt, user_prompt))

        if self._role is not None:
            check_dispatch(self._role, "mcp")

        work_type = getattr(job, "work_type", "code") or "code"
        effective_max_iterations = self._work_type_max_iterations.get(work_type, self._max_iterations)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        open_round_start = len(messages)

        tools = await self._mcp_client.list_tools()
        tool_schemas = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in tools
        ]
        # C15 defect 3: name -> input_schema map for per-call arg validation.
        schema_by_name: dict[str, dict[str, Any]] = {t.name: (t.input_schema or {}) for t in tools}

        cumulative_tokens = 0
        for iteration in range(effective_max_iterations):
            if self._budget_guard is not None:
                from general_ludd.budget_guard_check import budget_pre_check, compute_projected_cost_usd

                projected = compute_projected_cost_usd(self._gateway, self._budget_guard)
                denial = budget_pre_check(self._budget_guard, projected_cost=projected)
                if denial is not None:
                    logger.warning(
                        "ToolCallLoop budget denied for job %s at iteration %d: %s",
                        job.job_id,
                        iteration + 1,
                        denial,
                    )
                    raise ToolLoopExhausted(
                        f"Tool call loop budget exhausted at iteration "
                        f"{iteration + 1}/{effective_max_iterations} for job "
                        f"{job.job_id}: {denial}"
                    )

            if self._compaction_level is not None:
                messages = self._compact_history(
                    messages,
                    goal=user_prompt,
                    open_round_start=open_round_start,
                )

            try:
                response = await asyncio.wait_for(
                    self._call_with_tools(job, messages, tool_schemas),
                    timeout=self._per_iteration_timeout,
                )
            except TimeoutError as err:
                logger.warning(
                    "ToolCallLoop iteration %d timed out after %.0fs for job %s",
                    iteration + 1,
                    self._per_iteration_timeout,
                    job.job_id,
                )
                raise ToolLoopExhausted(
                    f"Tool call loop iteration {iteration + 1} timed out "
                    f"({self._per_iteration_timeout}s) for job {job.job_id}"
                ) from err

            content = getattr(response, "content", "") or str(response)
            tool_calls = getattr(response, "tool_calls", None)

            usage = getattr(response, "usage_metadata", {}) or {}
            input_tokens = int(usage.get("input_tokens", 0))
            output_tokens = int(usage.get("output_tokens", 0))
            cumulative_tokens += input_tokens + output_tokens
            if cumulative_tokens > self._max_total_tokens:
                logger.warning(
                    "ToolCallLoop token limit exceeded for job %s: %d > %d",
                    job.job_id,
                    cumulative_tokens,
                    self._max_total_tokens,
                )
                raise ToolLoopExhausted(
                    f"Tool call loop total tokens {cumulative_tokens} exceeded "
                    f"limit {self._max_total_tokens} for job {job.job_id}"
                )

            if self._adversarial_detector is not None and content:
                scan_result = self._adversarial_detector.scan_text(content, file_path=f"tool_loop:{job.job_id}")
                if scan_result.blocked:
                    logger.warning(
                        "ToolCallLoop adversarial scan blocked output for job %s: %s",
                        job.job_id,
                        scan_result.summary,
                    )
                    raise ToolLoopExhausted(
                        f"Tool call loop output blocked by adversarial scan "
                        f"at iteration {iteration + 1} for job {job.job_id}: "
                        f"{scan_result.summary}"
                    )

            if tool_calls:
                # C15 defect 2 (per-response cap): bound the fan-out WITHIN one
                # response. Truncate to the first MAX_TOOL_CALLS_PER_RESPONSE and
                # answer every REJECTED tool_call_id with a "cap exceeded" tool
                # message so no id is orphaned. open_round_start is captured
                # AFTER this so the cap-rejection messages (and every other tool
                # message this round) land in the CURRENT open round, never in the
                # compactable prefix — preserving the tool_call_id pairing
                # invariant compaction depends on.
                accepted = tool_calls[:MAX_TOOL_CALLS_PER_RESPONSE]
                rejected = tool_calls[MAX_TOOL_CALLS_PER_RESPONSE:]
                open_round_start = len(messages)
                for tc in rejected:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.get("id", ""),
                            "content": (
                                f"Tool call rejected: this response exceeded the "
                                f"per-response tool-call cap of "
                                f"{MAX_TOOL_CALLS_PER_RESPONSE}. Do not retry; issue "
                                f"fewer tool calls per turn."
                            ),
                        }
                    )
                if rejected:
                    logger.warning(
                        "ToolCallLoop capped tool calls for job %s: %d requested, %d executed, %d rejected",
                        job.job_id,
                        len(tool_calls),
                        len(accepted),
                        len(rejected),
                    )
                for tc in accepted:
                    tc_name = tc.get("function", {}).get("name", "")
                    tc_args = tc.get("function", {}).get("arguments", "{}")
                    if isinstance(tc_args, str):
                        import json as _json

                        try:
                            tc_args = _json.loads(tc_args)
                        except _json.JSONDecodeError:
                            logger.warning(
                                "Malformed tool-call arguments for %r (job %s); using empty args. Raw: %.200r",
                                tc_name,
                                job.job_id,
                                tc_args,
                            )
                            tc_args = {}
                    if not isinstance(tc_args, dict):
                        tc_args = {}
                    # C15 defect 1 (Phase-2 lattice): the entry check_dispatch
                    # only gates the loop ONCE. Re-check the lattice for EVERY
                    # tool call so a role that lacks the "mcp" capability can
                    # never reach call_tool from inside the round loop. role=None
                    # preserves the pre-existing ungated behaviour.
                    if self._role is not None and not role_may_dispatch(self._role, "mcp"):
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.get("id", ""),
                                "content": (
                                    f"Tool call denied: role {self._role!r} lacks the "
                                    f"capability to dispatch MCP tool calls "
                                    f"(capability_denied). Do not retry."
                                ),
                            }
                        )
                        logger.warning(
                            "ToolCallLoop per-call capability denied for role %r on tool %r (job %s)",
                            self._role,
                            tc_name,
                            job.job_id,
                        )
                        continue
                    # C15 defect 3 (arg schema validation): reject args that do
                    # not conform to the tool's input_schema BEFORE the auditor
                    # gate and BEFORE call_tool. An empty schema is a no-op.
                    schema_err = _validate_tool_args(tc_args, schema_by_name.get(tc_name, {}))
                    if schema_err is not None:
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.get("id", ""),
                                "content": (
                                    f"Tool call rejected: arguments are not valid for "
                                    f"{tc_name!r}: {schema_err}. Fix the arguments and "
                                    f"retry."
                                ),
                            }
                        )
                        logger.info(
                            "ToolCallLoop rejected invalid args for %r (job %s): %s",
                            tc_name,
                            job.job_id,
                            schema_err,
                        )
                        continue
                    if self._auditor is not None:
                        situation = self._auditor.audit(
                            tc_name,
                            tc_args,
                            task_context=user_prompt[:500],
                            work_type=getattr(job, "work_type", ""),
                            capture_situation=True,
                        )
                        if situation is not None:
                            if self._situation_store is not None:
                                with contextlib.suppress(Exception):
                                    self._situation_store.save(situation)
                            messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": tc.get("id", ""),
                                    "content": (
                                        f"Tool call blocked by auditor: {situation.classification}. "
                                        f"{situation.reason} "
                                        f"Do not retry this call. Use a different approach."
                                    ),
                                }
                            )
                            logger.info(
                                "Blocked tool call %r for job %s: %s",
                                tc_name,
                                job.job_id,
                                situation.classification,
                            )
                            continue
                    try:
                        server_id = self._resolve_server_id(tc_name)
                        result = await asyncio.wait_for(
                            self._mcp_client.call_tool(
                                server_id,
                                tc_name,
                                tc_args,
                            ),
                            timeout=self._per_tool_timeout,
                        )
                        if self._auditor is not None:
                            self._auditor.record_success(tc_name, tc_args, result)
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.get("id", ""),
                                "content": str(result),
                            }
                        )
                    except TimeoutError:
                        if self._auditor is not None:
                            self._auditor.record_error(
                                tc_name,
                                tc_args,
                                f"timeout after {self._per_tool_timeout}s",
                            )
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.get("id", ""),
                                "content": (f"Tool error: {tc_name!r} timed out after {self._per_tool_timeout}s"),
                            }
                        )
                        logger.warning(
                            "Tool %r timed out after %ss for job %s",
                            tc_name,
                            self._per_tool_timeout,
                            job.job_id,
                        )
                    except Exception as exc:
                        if self._auditor is not None:
                            self._auditor.record_error(tc_name, tc_args, str(exc))
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.get("id", ""),
                                "content": f"Tool error: {exc}",
                            }
                        )
                continue
            return content

        logger.warning(
            "Tool call loop reached max iterations (%d) for job %s",
            effective_max_iterations,
            job.job_id,
        )
        raise ToolLoopExhausted(
            f"Tool call loop reached max iterations ({effective_max_iterations}) "
            f"for job {job.job_id} while the model was still requesting tools; "
            f"no final assistant answer was produced"
        )

    def reset_auditor(self) -> None:
        """Reset the auditor state for a fresh job."""
        if self._auditor is not None:
            self._auditor.reset()

    def _compact_history(
        self,
        messages: list[dict[str, Any]],
        *,
        goal: str,
        open_round_start: int,
    ) -> list[dict[str, Any]]:
        """Compact only the OLDER prefix of the tool-loop history, fail-soft.

        Tool-call/tool-result PAIRING preservation (the load-bearing invariant):
        everything from ``open_round_start`` onward is the CURRENT open tool round
        — the ``role: "tool"`` results answering the ``tool_call_id`` s the model
        just requested. That trailing run is preserved VERBATIM (the original dict
        objects, ``tool_call_id`` intact) and is NEVER handed to ``compact_dicts``
        (which strips messages to ``{role, content}`` and would drop the id,
        orphaning the open call). Only the older prefix is summarized.

        ``compact_dicts`` itself is fail-soft (returns its input on any error) and
        does its own threshold gating, so short histories pass through untouched
        and compaction can never raise into the loop.
        """
        level = self._compaction_level
        if level is None:  # pragma: no cover - guarded by caller
            return messages
        prefix = messages[:open_round_start]
        trailing = messages[open_round_start:]
        if not prefix:
            return messages
        compacted_prefix = compact_dicts(
            prefix,
            goal=goal,
            level=level,
            summarize_fn=self._summarize_fn,
        )
        return [*compacted_prefix, *trailing]

    async def _call_model(
        self,
        job: JobSpec,
        system_prompt: str,
        user_prompt: str,
    ) -> ModelResponse:
        profile_id = job.model_profile or "default"
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if user_prompt:
            messages.append({"role": "user", "content": user_prompt})
        return cast(
            "ModelResponse",
            await _run_model_call(
                self._gateway.call_model,
                profile_id,
                messages=messages,
                work_type=job.work_type,
                # S-1 (task #25): scope secret resolution to this job's project so
                # the tool-loop model call resolves credentials through the project's
                # ProjectSecretsManager (isolation); None → shared base behavior.
                project_id=job.project_id,
            ),
        )

    async def _call_with_tools(
        self,
        job: JobSpec,
        messages: list[dict[str, Any]],
        tool_schemas: list[dict[str, Any]],
    ) -> ModelResponse:
        profile_id = job.model_profile or "default"
        return cast(
            "ModelResponse",
            await _run_model_call(
                self._gateway.call_model,
                profile_id,
                messages=messages,
                tools=tool_schemas,
                work_type=job.work_type,
                # S-1 (task #25): scope secret resolution to this job's project (as
                # above); None → shared base behavior.
                project_id=job.project_id,
            ),
        )
