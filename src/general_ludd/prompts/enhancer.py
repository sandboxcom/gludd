"""Prompt enhancer: inject tool-call avoidance warnings from past bad calls.

Reads BadCallSituationStore and generates warnings that tell the model
which tools to avoid using, why, and suggests alternatives.
"""

from __future__ import annotations

import logging
from typing import Any

from general_ludd.execution.situation_store import BadCallSituationStore

logger = logging.getLogger(__name__)


class PromptEnhancer:
    """Enhances prompts with tool-call avoidance guidance.

    Reads recent BadCallSituations and injects warnings about tools that
    have been blocked, why, and what to use instead.
    """

    def __init__(
        self,
        store: BadCallSituationStore | None = None,
        max_situations: int = 20,
    ) -> None:
        self._store = store
        self._max_situations = max_situations

    def generate_avoidance_warning(self) -> str:
        """Generate a warning string about tools to avoid.

        Returns empty string if no store or no situations.
        """
        if self._store is None:
            return ""
        recent = self._store.list_recent(limit=self._max_situations)
        if not recent:
            return ""

        by_tool: dict[str, list[Any]] = {}
        for s in recent:
            by_tool.setdefault(s.tool_name, []).append(s)

        lines = ["", "### Tool Call Avoidance Guidance"]
        lines.append(
            "The following tools have been recently blocked. "
            "Avoid calling them for the same purpose:"
        )
        lines.append("")

        for tool_name, situations in by_tool.items():
            reasons: dict[str, int] = {}
            for s in situations:
                reasons[s.classification] = reasons.get(s.classification, 0) + 1

            reason_summary = ", ".join(
                f"{count}x {cls}" for cls, count in reasons.items()
            )
            latest = situations[0]
            lines.append(
                f"- **{tool_name}**: blocked {len(situations)} times ({reason_summary})"
            )
            lines.append(f"  Latest reason: {latest.reason[:200]}")
            if latest.task_excerpt:
                lines.append(f'  Context: "{latest.task_excerpt[:200]}"')

        lines.append("")
        lines.append(
            "**Guidance**: Do not call these tools with similar arguments. "
            "Use alternative approaches or different tools."
        )
        return "\n".join(lines)

    def enhance_prompt(self, system_prompt: str) -> str:
        """Inject avoidance guidance into a system prompt.

        Appends the warning to the end of the prompt.
        """
        warning = self.generate_avoidance_warning()
        if not warning:
            return system_prompt
        return system_prompt + "\n" + warning

    def enhance_messages(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Inject avoidance guidance into message list.

        Appends or modifies the system message.
        """
        warning = self.generate_avoidance_warning()
        if not warning:
            return messages
        for msg in messages:
            if msg.get("role") == "system":
                msg["content"] = msg["content"] + "\n" + warning
                return messages
        messages.insert(0, {"role": "system", "content": warning})
        return messages

    def get_recent_blocked_tools(self) -> set[str]:
        """Return set of tool names blocked recently."""
        if self._store is None:
            return set()
        recent = self._store.list_recent(limit=self._max_situations)
        return {s.tool_name for s in recent}

    def get_blocked_tool_counts(self) -> dict[str, int]:
        """Return {tool_name: count} of blocked calls."""
        if self._store is None:
            return {}
        counts: dict[str, int] = {}
        recent = self._store.list_recent(limit=self._max_situations)
        for s in recent:
            counts[s.tool_name] = counts.get(s.tool_name, 0) + 1
        return counts

    def format_tool_advice(self, tool_name: str) -> str:
        """Format specific advice for a tool based on past blocks."""
        if self._store is None:
            return ""
        situations = self._store.list_by_tool(tool_name, limit=10)
        if not situations:
            return ""
        lines = [f"Recent blocks for {tool_name}:"]
        for s in situations[:3]:
            lines.append(f"  - {s.classification}: {s.reason[:150]}")
        return "\n".join(lines)
