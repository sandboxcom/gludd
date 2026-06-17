"""File-based issue source: GitHub-style markdown checkbox TODO lists.

System-of-record adapter for plain-text task lists. Each ``- [ ]`` / ``- [x]``
checkbox line is one issue. Writes are performed back into the same file (the
file IS the system of record).

Self-contained: depends only on the Python standard library. No base class,
no sibling-module imports.

ID resolution (in priority order):
1. trailing ``<!--id:ABC-->`` HTML comment
2. trailing ``(#42)`` reference
3. otherwise a stable hash derived from the file-relative path + checkbox text

Path safety: every file path is realpath-confined to the configured ``root``.
``../`` escapes, absolute paths outside the root, and symlinks that resolve
outside the root are all rejected with ``ValueError`` at construction time.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any

# An open or done checkbox item. Captures: indent, mark char, the trailing text.
_CHECKBOX_RE = re.compile(r"^(?P<indent>\s*)-\s+\[(?P<mark>[ xX])\]\s+(?P<text>.*?)\s*$")
_HTML_ID_RE = re.compile(r"<!--\s*id:\s*(?P<id>[^\s>]+?)\s*-->")
_PAREN_HASH_ID_RE = re.compile(r"\(#(?P<id>[A-Za-z0-9._-]+)\)")

_DONE_STATUSES = {"done", "closed", "complete", "completed", "resolved", "x"}


def _confine(root: str, path: str) -> str:
    """Resolve ``path`` (relative to ``root`` if not absolute) and confine it.

    Returns the realpath of the target. Raises ``ValueError`` if the resolved
    target escapes ``root`` (via ``..``, an absolute path outside root, or a
    symlink whose target lies outside root).
    """
    root_real = os.path.realpath(root)
    candidate = path if os.path.isabs(path) else os.path.join(root_real, path)
    target_real = os.path.realpath(candidate)
    # Confinement check: target must equal root or live beneath it.
    if target_real != root_real and not target_real.startswith(root_real + os.sep):
        raise ValueError(f"path {path!r} escapes the configured root {root!r}")
    return target_real


class MarkdownTodoSource:
    """Issue source backed by a markdown checkbox TODO file."""

    SYSTEM = "markdown"

    def __init__(self, config: dict[str, Any]) -> None:
        root = config.get("root")
        path = config.get("path")
        if not root:
            raise ValueError("config['root'] (allowed base directory) is required")
        if not path:
            raise ValueError("config['path'] is required")
        self._root = os.path.realpath(str(root))
        self._path = _confine(self._root, str(path))
        self.name = f"markdown:{os.path.relpath(self._path, self._root)}"

    # -- internals --------------------------------------------------------

    def _rel(self) -> str:
        return os.path.relpath(self._path, self._root)

    def _derive_id(self, text: str) -> str:
        seed = f"{self._rel()}::{text}".encode()
        return "md-" + hashlib.sha1(seed).hexdigest()[:12]

    def _extract_id(self, text: str) -> tuple[str, str]:
        """Return ``(external_id, clean_title)`` for a checkbox's text."""
        m = _HTML_ID_RE.search(text)
        if m:
            ext = m.group("id")
            title = _HTML_ID_RE.sub("", text).strip()
            return ext, title
        m = _PAREN_HASH_ID_RE.search(text)
        if m:
            ext = m.group("id")
            title = _PAREN_HASH_ID_RE.sub("", text).strip()
            return ext, title
        return self._derive_id(text), text.strip()

    def _read_lines(self) -> list[str]:
        with open(self._path, encoding="utf-8") as fh:
            return fh.read().splitlines()

    def _write_lines(self, lines: list[str]) -> None:
        with open(self._path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")

    def _parse(self, lines: list[str]) -> list[dict[str, Any]]:
        """Parse checkbox lines into (line_index, external_id, title, status)."""
        out: list[dict[str, Any]] = []
        for idx, line in enumerate(lines):
            m = _CHECKBOX_RE.match(line)
            if not m:
                continue
            mark = m.group("mark")
            text = m.group("text")
            ext, title = self._extract_id(text)
            status = "done" if mark.lower() == "x" else "open"
            out.append(
                {
                    "line": idx,
                    "indent": m.group("indent"),
                    "external_id": ext,
                    "title": title,
                    "status": status,
                }
            )
        return out

    def _file_mtime(self) -> float | None:
        try:
            return os.path.getmtime(self._path)
        except OSError:
            return None

    # -- contract ---------------------------------------------------------

    def health(self) -> dict[str, Any]:
        try:
            with open(self._path, encoding="utf-8"):
                pass
        except OSError as exc:
            return {"ok": False, "detail": f"unreadable: {exc}"}
        return {"ok": True, "detail": f"readable: {self._rel()}"}

    def fetch_issues(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        lines = self._read_lines()
        mtime = self._file_mtime()
        issues: list[dict[str, Any]] = []
        for item in self._parse(lines):
            issues.append(
                {
                    "external_id": item["external_id"],
                    "source": self.SYSTEM,
                    "title": item["title"],
                    "description": item["title"],
                    "status": item["status"],
                    "assignee": None,
                    "labels": [],
                    "priority": None,
                    "url": f"{self._path}#L{item['line'] + 1}",
                    "updated_ts": mtime,
                    "raw": {
                        "line": item["line"] + 1,
                        "indent": item["indent"],
                        "checkbox": lines[item["line"]],
                    },
                }
            )
        return issues

    def update_status(self, external_id: str, status: str, comment: str | None = None) -> dict[str, Any]:
        lines = self._read_lines()
        items = self._parse(lines)
        target = next((it for it in items if it["external_id"] == external_id), None)
        if target is None:
            raise KeyError(f"external_id {external_id!r} not found in {self._rel()}")

        is_done = status.strip().lower() in _DONE_STATUSES
        new_mark = "x" if is_done else " "
        line_idx = target["line"]
        original = lines[line_idx]
        m = _CHECKBOX_RE.match(original)
        assert m is not None  # parsed as a checkbox above
        text = m.group("text")
        if comment:
            marker = f" <!--gludd:{comment}-->"
            if marker.strip() not in text:
                text = f"{text}{marker}"
        lines[line_idx] = f"{m.group('indent')}- [{new_mark}] {text}"
        self._write_lines(lines)

        return {
            "external_id": external_id,
            "status": "done" if is_done else "open",
            "source": self.SYSTEM,
            "url": f"{self._path}#L{line_idx + 1}",
            "comment": comment,
        }

    def add_comment(self, external_id: str, comment: str) -> dict[str, Any]:
        lines = self._read_lines()
        items = self._parse(lines)
        target = next((it for it in items if it["external_id"] == external_id), None)
        if target is None:
            raise KeyError(f"external_id {external_id!r} not found in {self._rel()}")

        line_idx = target["line"]
        note_indent = target["indent"] + "  "
        note = f"{note_indent}- {comment}"
        lines.insert(line_idx + 1, note)
        self._write_lines(lines)

        return {
            "external_id": external_id,
            "source": self.SYSTEM,
            "comment": comment,
            "url": f"{self._path}#L{line_idx + 2}",
        }
