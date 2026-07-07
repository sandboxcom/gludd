"""Tool call auditor: detect useless, repeated, or irrelevant MCP tool calls.

When the model emits tool calls, this auditor intercepts them BEFORE
execution and classifies each call as allowed / redundant / error_loop /
irrelevant. Blocked calls produce a BadCallSituation that can be saved
for future prompt enhancement.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field


@dataclass
class CallVerdict:
    """The result of auditing a tool call."""
    allowed: bool
    classification: str = ""  # "redundant", "error_loop", "irrelevant", ""
    reason: str = ""


@dataclass
class BadCallSituation:
    """Minimal snapshot of a blocked tool call for prompt enhancement.

    Captures just enough context to understand WHY the call was blocked,
    without serializing the full agent environment.
    """
    tool_name: str
    tool_args: dict[str, object]
    classification: str
    reason: str
    task_excerpt: str = ""
    recent_calls: list[dict[str, object]] = field(default_factory=list)
    timestamp: float = 0.0
    work_type: str = ""


class RedundancyDetector:
    """Detects consecutive repeated calls to the same tool with the same args."""

    def __init__(self, max_repeats: int = 2) -> None:
        self._max_repeats = max_repeats
        self._last_call: tuple[str, str] | None = None  # (tool_name, args_hash)
        self._consecutive_count: int = 0

    def check(self, tool_name: str, args: dict[str, object]) -> CallVerdict:
        args_hash = hashlib.sha256(
            json.dumps(args, sort_keys=True, default=str).encode()
        ).hexdigest()
        call_key = (tool_name, args_hash)
        if self._last_call == call_key:
            self._consecutive_count += 1
            if self._consecutive_count >= self._max_repeats:
                return CallVerdict(
                    allowed=False,
                    classification="redundant",
                    reason=(
                        f"Repeated tool call: {tool_name!r} called "
                        f"{self._consecutive_count + 1} times consecutively "
                        f"with the same arguments (max {self._max_repeats})"
                    ),
                )
        else:
            self._last_call = call_key
            self._consecutive_count = 0
        return CallVerdict(allowed=True)

    def reset(self) -> None:
        self._last_call = None
        self._consecutive_count = 0


class ErrorLoopDetector:
    """Detects when the model keeps retrying a tool that already errored."""

    def __init__(self, max_error_retries: int = 2) -> None:
        self._max_error_retries = max_error_retries
        self._error_counts: dict[str, int] = {}  # args_hash -> count

    def _hash_args(self, args: dict[str, object]) -> str:
        return hashlib.sha256(
            json.dumps(args, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]

    def check(self, tool_name: str, args: dict[str, object]) -> CallVerdict:
        key = f"{tool_name}:{self._hash_args(args)}"
        count = self._error_counts.get(key, 0)
        if count >= self._max_error_retries:
            return CallVerdict(
                allowed=False,
                classification="error_loop",
                reason=(
                    f"Tool {tool_name!r} with these args has errored {count} times "
                    f"(max {self._max_error_retries}); refusing retry"
                ),
            )
        return CallVerdict(allowed=True)

    def record_error(self, tool_name: str, args: dict[str, object], error: str) -> None:
        key = f"{tool_name}:{self._hash_args(args)}"
        self._error_counts[key] = self._error_counts.get(key, 0) + 1

    def record_success(self, tool_name: str, args: dict[str, object]) -> None:
        key = f"{tool_name}:{self._hash_args(args)}"
        self._error_counts.pop(key, None)

    def reset(self) -> None:
        self._error_counts.clear()


class IrrelevanceDetector:
    """Detects tool calls that don't match the task context.

    Uses keyword matching: each tool name maps to a set of keywords that
    should appear in the task context for the call to be relevant.
    """

    def __init__(
        self,
        relevant_keywords: dict[str, list[str]] | None = None,
    ) -> None:
        if relevant_keywords is None:
            relevant_keywords = {
                "read_file": ["file", "read", "source", "code", "content", "text", "document"],
                "search_code": ["search", "find", "grep", "look", "query", "code", "source"],
                "write_file": ["write", "create", "save", "output", "generate", "produce"],
                "execute_command": ["run", "execute", "command", "test", "build", "deploy"],
                "list_directory": ["list", "directory", "folder", "files", "structure", "ls"],
                "git_commit": ["commit", "git", "push", "version", "change"],
                "deploy": ["deploy", "ship", "release", "publish", "prod"],
                "query_database": ["query", "database", "db", "sql", "select", "data"],
                "fetch_url": ["fetch", "url", "download", "http", "api", "web", "get"],
                "send_message": ["send", "message", "notify", "alert", "email", "slack"],
            }
        self._keywords = relevant_keywords

    def check(
        self, tool_name: str, args: dict[str, object], task_context: str,
    ) -> CallVerdict:
        if not task_context:
            return CallVerdict(allowed=True)
        keywords = self._keywords.get(tool_name)
        if keywords is None:
            return CallVerdict(allowed=True)  # no keywords = no restriction
        context_lower = task_context.lower()
        for kw in keywords:
            if kw in context_lower:
                return CallVerdict(allowed=True)
        return CallVerdict(
            allowed=False,
            classification="irrelevant",
            reason=(
                f"Tool {tool_name!r} does not match task context; "
                f"expected keywords: {keywords}"
            ),
        )

    def reset(self) -> None:
        pass  # stateless


class ToolCallAuditor:
    """Intercepts MCP tool calls before execution, blocking useless ones.

    Combines three detectors:
    - RedundancyDetector: blocks repeated identical calls
    - ErrorLoopDetector: blocks retries after repeated errors
    - IrrelevanceDetector: blocks calls that don't match task context

    Maintains a bounded call history for situation capture.
    """

    def __init__(
        self,
        redundancy_detector: RedundancyDetector | None = None,
        error_loop_detector: ErrorLoopDetector | None = None,
        irrelevance_detector: IrrelevanceDetector | None = None,
        max_history: int = 20,
    ) -> None:
        self._redundancy = redundancy_detector or RedundancyDetector()
        self._error_loop = error_loop_detector or ErrorLoopDetector()
        self._irrelevance = irrelevance_detector or IrrelevanceDetector()
        self._max_history = max_history
        self.call_history: list[dict[str, object]] = []

    def audit(
        self,
        tool_name: str,
        tool_args: dict[str, object],
        task_context: str = "",
        work_type: str = "",
        capture_situation: bool = False,
    ) -> CallVerdict | BadCallSituation | None:
        """Audit a tool call before execution.

        Returns a CallVerdict when capture_situation is False (default).
        Returns a BadCallSituation if the call is blocked and capture_situation
        is True. Returns None if the call is allowed and capture_situation is True.
        """
        self._record_call(tool_name, tool_args)

        # Check redundancy first (cheapest check)
        verdict = self._redundancy.check(tool_name, tool_args)
        if not verdict.allowed:
            return self._maybe_capture(verdict, tool_name, tool_args, task_context, work_type, capture_situation)

        # Check error loop
        verdict = self._error_loop.check(tool_name, tool_args)
        if not verdict.allowed:
            return self._maybe_capture(verdict, tool_name, tool_args, task_context, work_type, capture_situation)

        # Check irrelevance
        verdict = self._irrelevance.check(tool_name, tool_args, task_context)
        if not verdict.allowed:
            return self._maybe_capture(verdict, tool_name, tool_args, task_context, work_type, capture_situation)

        if capture_situation:
            return None
        return CallVerdict(allowed=True)

    def record_error(self, tool_name: str, args: dict[str, object], error: str) -> None:
        self._error_loop.record_error(tool_name, args, error)

    def record_success(self, tool_name: str, args: dict[str, object], result: object = None) -> None:
        self._error_loop.record_success(tool_name, args)
        self._record_call(tool_name, args, success=True, result=str(result)[:200] if result else None)

    def reset(self) -> None:
        self._redundancy.reset()
        self._error_loop.reset()
        self._irrelevance.reset()
        self.call_history.clear()

    def _record_call(
        self, tool_name: str, args: dict[str, object],
        success: bool | None = None, result: str | None = None,
    ) -> None:
        short_args = {k: str(v)[:100] for k, v in args.items()}
        if success is not None or result is not None:
            for i in range(len(self.call_history) - 1, -1, -1):
                entry = self.call_history[i]
                if entry["tool_name"] == tool_name and entry.get("args") == short_args:
                    if success is not None:
                        entry["success"] = success
                    if result is not None:
                        entry["result"] = result
                    return
        new_entry: dict[str, object] = {
            "tool_name": tool_name,
            "args": short_args,
            "timestamp": time.time(),
        }
        if success is not None:
            new_entry["success"] = success
        if result is not None:
            new_entry["result"] = result
        self.call_history.append(new_entry)
        while len(self.call_history) > self._max_history:
            self.call_history.pop(0)

    def _maybe_capture(
        self, verdict: CallVerdict, tool_name: str, args: dict[str, object],
        task_context: str, work_type: str, capture: bool,
    ) -> CallVerdict | BadCallSituation:
        if not capture:
            return verdict
        return BadCallSituation(
            tool_name=tool_name,
            tool_args=args,
            classification=verdict.classification,
            reason=verdict.reason,
            task_excerpt=(task_context[:500] if task_context else ""),
            recent_calls=list(self.call_history[-5:]),
            timestamp=time.time(),
            work_type=work_type,
        )
