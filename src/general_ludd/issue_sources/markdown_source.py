"""Markdown checklist issue source (checkbox parse + rewrite).

Treats GitHub-flavoured task-list checkboxes in a Markdown file as work items:

    - [ ] Implement the widget          # open  -> backlog
    - [x] Wire up the daemon            # done  -> complete

Each unchecked/checked list item becomes one
:class:`~general_ludd.issue_sources.base.IssueRecord`. The ``external_id`` is a
stable hash of the item's text + its 1-based ordinal, so re-parsing the same file
yields the same id (dedup-friendly) even though Markdown has no native ids.

This is a *file*-based source: it has no ``base_url`` and no network transport.
``write_back`` rewrites the file in place — ``DONE`` ticks the box (``[ ]`` ->
``[x]``); ``CLAIM`` is a no-op success (a Markdown checklist has no in-progress
state). The rewrite is idempotent: ticking an already-ticked box is a no-op.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from general_ludd.issue_sources.base import (
    IssueRecord,
    IssueSource,
    SourceTransport,
    Transition,
    new_issue_record,
)

# Matches a task-list item, capturing indent, the checkbox mark, and the text.
#   group 'indent'  leading whitespace
#   group 'mark'    the char inside the brackets ('' | ' ' | 'x' | 'X')
#   group 'text'    the item label
_TASK_RE = re.compile(r"^(?P<indent>\s*)[-*+]\s+\[(?P<mark>[ xX]?)\]\s+(?P<text>.*\S)\s*$")


def _stable_id(text: str, ordinal: int) -> str:
    digest = hashlib.sha1(f"{ordinal}:{text}".encode()).hexdigest()[:12]
    return f"md-{ordinal}-{digest}"


class MarkdownSource(IssueSource):
    """Issue source backed by task-list checkboxes in a Markdown file."""

    SOURCE = "markdown"

    def __init__(self, config: dict[str, Any], transport: SourceTransport | None = None) -> None:
        # File-based: no base_url, no SSRF check, no transport needed.
        super().__init__(config, transport, require_base_url=False)
        path = config.get("path")
        if not path:
            raise ValueError("config['path'] is required for the markdown source")
        self.path: str = str(path)

    # -- read -------------------------------------------------------------- #

    def _read_lines(self) -> list[str]:
        with open(self.path, encoding="utf-8") as fh:
            return fh.read().splitlines()

    @staticmethod
    def _parse_lines(lines: list[str]) -> list[tuple[int, str, bool, str]]:
        """Return (line_index, external_id, checked, text) for each task item."""
        out: list[tuple[int, str, bool, str]] = []
        ordinal = 0
        for idx, line in enumerate(lines):
            m = _TASK_RE.match(line)
            if not m:
                continue
            ordinal += 1
            text = m.group("text").strip()
            checked = m.group("mark").lower() == "x"
            out.append((idx, _stable_id(text, ordinal), checked, text))
        return out

    def fetch(self, spec: dict[str, Any] | None = None) -> list[IssueRecord]:
        spec = spec or {}
        include_done = bool(spec.get("include_done", True))
        records: list[IssueRecord] = []
        for _idx, ext_id, checked, text in self._parse_lines(self._read_lines()):
            if checked and not include_done:
                continue
            records.append(
                new_issue_record(
                    external_id=ext_id,
                    title=text,
                    body="",
                    status="done" if checked else "open",
                    labels=[],
                    url=f"file://{self.path}",
                    raw={"path": self.path, "checked": checked, "text": text},
                )
            )
        return records

    # -- write-back -------------------------------------------------------- #

    def write_back(self, external_id: str, transition: Transition) -> bool:
        if transition is Transition.CLAIM:
            # A Markdown checklist has no "in progress" state — claiming is a
            # local-only event, nothing to rewrite. Idempotent success.
            return True
        lines = self._read_lines()
        parsed = self._parse_lines(lines)
        target_idx: int | None = None
        for idx, ext_id, checked, _text in parsed:
            if ext_id == external_id:
                if checked:
                    return True  # already ticked -> idempotent no-op success
                target_idx = idx
                break
        if target_idx is None:
            return False
        m = _TASK_RE.match(lines[target_idx])
        assert m is not None  # we only stored matching lines
        # Rewrite just the checkbox mark to 'x', preserving indent + text.
        lines[target_idx] = re.sub(r"\[[ xX]?\]", "[x]", lines[target_idx], count=1)
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        return True
