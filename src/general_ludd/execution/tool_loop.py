"""MCP tool integration for model gateway calls.

Extends ExecutionEngine to handle tool-call loops where the model
requests tools via function calling, the engine executes them via MCP,
and the results are fed back to continue the conversation.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from general_ludd.compaction.aggressive import compact_dicts
from general_ludd.mcp.registry import MCPToolRegistry
from general_ludd.mcp.transport import MCPTransportError
from general_ludd.schemas.job import JobSpec
from general_ludd.security.capability_lattice import check_dispatch

if TYPE_CHECKING:
    from general_ludd.compaction.aggressive import CompactionLevel

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 10
PER_TOOL_TIMEOUT_SECONDS = 30


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

    def _resolve_server_id(self, tc_name: str) -> str:
        """Map a model-chosen tool name to its registered server_id.

        Capability gate (Finding 3): a tool name that is NOT in the registry is
        rejected outright with MCPTransportError — the model cannot reach an
        unadvertised tool, and a real server_id is always supplied (never None).
        """
        registry = self._mcp_registry
        if registry is None:
            raise MCPTransportError(
                "MCP tool registry unavailable; refusing ungated tool call "
                f"{tc_name!r}"
            )
        tool = registry.get_tool(tc_name)
        if tool is None:
            # Distinguish ambiguous (same name on multiple servers) from unknown.
            if tc_name in registry.tool_names():
                raise MCPTransportError(
                    f"Tool {tc_name!r} is ambiguous across multiple servers; "
                    f"supply an explicit server_id"
                )
            raise MCPTransportError(
                f"Tool {tc_name!r} is not a registered MCP tool (capability "
                f"gate); refusing call"
            )
        if not tool.server_id:
            raise MCPTransportError(
                f"Tool {tc_name!r} is not a registered MCP tool (capability "
                f"gate); refusing call"
            )
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

        # Per-role capability gate (fail-closed): if a role was supplied, it must
        # hold the "mcp" dispatch capability to drive MCP tool use at all. A role
        # that lacks it is refused HERE — before list_tools / any call_tool — so
        # the model can never reach a tool through an unauthorised role. A None
        # role skips the gate (backward compatible with callers that pass none).
        if self._role is not None:
            check_dispatch(self._role, "mcp")

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        # SLICE 2 pairing guard: index in ``messages`` where the CURRENT open
        # tool round begins. Everything from here on (the tool results answering
        # the tool_call_ids the model just asked for) is preserved verbatim by
        # ``_compact_history`` so an open ``tool_call_id`` can never be summarized
        # away. It is re-marked each iteration just before new tool results are
        # appended.
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

        for _ in range(self._max_iterations):
            # SLICE 2 (task #56): pre-call SLM context-compaction. When enabled,
            # shrink the ITERATIVE history BEFORE the model call so long tool
            # conversations send fewer tokens. Default OFF (compaction_level is
            # None) → this is a no-op and ``messages`` is unchanged. The helper is
            # fail-soft (returns the input on any error) so compaction can NEVER
            # crash the loop, and it preserves the open tool round verbatim.
            if self._compaction_level is not None:
                messages = self._compact_history(
                    messages, goal=user_prompt, open_round_start=open_round_start,
                )
            response = await self._call_with_tools(job, messages, tool_schemas)
            content = getattr(response, "content", "") or str(response)
            tool_calls = getattr(response, "tool_calls", None)

            if tool_calls:
                # This iteration's tool results are the new OPEN round: mark the
                # boundary so they are preserved verbatim on the next compaction.
                open_round_start = len(messages)
                for tc in tool_calls:
                    tc_name = tc.get("function", {}).get("name", "")
                    tc_args = tc.get("function", {}).get("arguments", "{}")
                    if isinstance(tc_args, str):
                        import json as _json
                        try:
                            tc_args = _json.loads(tc_args)
                        except _json.JSONDecodeError:
                            # Don't silently swallow malformed model output: an
                            # operator needs to see that the model emitted invalid
                            # tool-call JSON (the tool then runs with empty args).
                            logger.warning(
                                "Malformed tool-call arguments for %r (job %s); "
                                "using empty args. Raw: %.200r",
                                tc_name, job.job_id, tc_args,
                            )
                            tc_args = {}
                    # Audit the tool call before execution
                    if self._auditor is not None:
                        situation = self._auditor.audit(
                            tc_name, tc_args,
                            task_context=user_prompt[:500],
                            work_type=getattr(job, "work_type", ""),
                            capture_situation=True,
                        )
                        if situation is not None:
                            if self._situation_store is not None:
                                with contextlib.suppress(Exception):
                                    self._situation_store.save(situation)
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc.get("id", ""),
                                "content": (
                                    f"Tool call blocked by auditor: {situation.classification}. "
                                    f"{situation.reason} "
                                    f"Do not retry this call. Use a different approach."
                                ),
                            })
                            logger.info(
                                "Blocked tool call %r for job %s: %s",
                                tc_name, job.job_id, situation.classification,
                            )
                            continue
                    try:
                        server_id = self._resolve_server_id(tc_name)
                        # Per-tool timeout: a hung/slow MCP tool must NOT stall the
                        # whole tool loop (and the daemon that awaits it). Bound the
                        # call; on timeout we fall through to the tool-error branch
                        # below, which appends an error message the model can react
                        # to, instead of hanging forever.
                        result = await asyncio.wait_for(
                            self._mcp_client.call_tool(
                                server_id, tc_name, tc_args,
                            ),
                            timeout=self._per_tool_timeout,
                        )
                        if self._auditor is not None:
                            self._auditor.record_success(tc_name, tc_args, result)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id", ""),
                            "content": str(result),
                        })
                    except TimeoutError:
                        if self._auditor is not None:
                            self._auditor.record_error(
                                tc_name, tc_args,
                                f"timeout after {self._per_tool_timeout}s",
                            )
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id", ""),
                            "content": (
                                f"Tool error: {tc_name!r} timed out after "
                                f"{self._per_tool_timeout}s"
                            ),
                        })
                        logger.warning(
                            "Tool %r timed out after %ss for job %s",
                            tc_name, self._per_tool_timeout, job.job_id,
                        )
                    except Exception as exc:
                        if self._auditor is not None:
                            self._auditor.record_error(tc_name, tc_args, str(exc))
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id", ""),
                            "content": f"Tool error: {exc}",
                        })
                continue
            return content

        logger.warning(
            "Tool call loop reached max iterations (%d) for job %s",
            self._max_iterations, job.job_id,
        )
        raise ToolLoopExhausted(
            f"Tool call loop reached max iterations ({self._max_iterations}) "
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
        self, job: JobSpec, system_prompt: str, user_prompt: str,
    ) -> Any:
        profile_id = job.model_profile or "default"
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if user_prompt:
            messages.append({"role": "user", "content": user_prompt})
        return await asyncio.to_thread(
            self._gateway.call_model, profile_id, messages=messages,
            work_type=job.work_type,
            # S-1 (task #25): scope secret resolution to this job's project so
            # the tool-loop model call resolves credentials through the project's
            # ProjectSecretsManager (isolation); None → shared base behavior.
            project_id=job.project_id,
        )

    async def _call_with_tools(
        self, job: JobSpec, messages: list[dict[str, Any]],
        tool_schemas: list[dict[str, Any]],
    ) -> Any:
        profile_id = job.model_profile or "default"
        return await asyncio.to_thread(
            self._gateway.call_model,
            profile_id,
            messages=messages,
            tools=tool_schemas,
            work_type=job.work_type,
            # S-1 (task #25): scope secret resolution to this job's project (as
            # above); None → shared base behavior.
            project_id=job.project_id,
        )
