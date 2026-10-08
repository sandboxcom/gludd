"""Bounded adapters from the canonical task ledger to backlog-audit tasks.

The task ledger remains owned by :mod:`scripts.validate_task_ledger`.  This
module intentionally calls its ``extract_tasks`` function instead of growing a
second checkbox parser.  The adapter adds only the stricter runtime boundary
needed by the executable backlog audit: repository confinement, input limits,
evidence-node extraction, and fail-closed handling of syntax the canonical
parser cannot represent.
"""

from __future__ import annotations

import re
import stat
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from scripts.validate_task_ledger import extract_tasks, task_is_effectively_complete

MAX_LEDGER_BYTES = 8 * 1024 * 1024
MAX_TASKS = 512
MAX_EVIDENCE_IDS = 64
MAX_TOUCHED_FILES = 32
MAX_REFERENCE_LENGTH = 1024

_TASK_MARKER = re.compile(r"^\s*[-+*]\s+\[(?P<state>[^\]])\]\s+")
_FIELD = re.compile(r"^\s*(?P<name>[a-z][a-z _-]*)\s*:\s*(?P<value>.*?)\s*$", re.I)
_CODE_SPAN = re.compile(r"`([^`\r\n]+)`")
_PLAIN_NODE_ID = re.compile(
    r"(?P<node>(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.py"
    r"(?:::[A-Za-z0-9_.$<>\[\]={},+:/-]+)+)"
)
_PATH_SUFFIXES = frozenset(
    {
        ".cfg",
        ".css",
        ".html",
        ".ini",
        ".js",
        ".json",
        ".md",
        ".mjs",
        ".py",
        ".pyi",
        ".sh",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".yaml",
        ".yml",
    }
)

FileReader = Callable[[str], str | None]
BacklogTask = dict[str, object]


class BacklogSourceError(ValueError):
    """Raised when task-source input cannot be interpreted safely."""


def _resolved_repo_root(repo_root: str | Path) -> Path:
    try:
        root = Path(repo_root).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise BacklogSourceError(f"repository root is unavailable: {repo_root}") from exc
    if not root.is_dir():
        raise BacklogSourceError(f"repository root is not a directory: {root}")
    return root


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _ledger_path(root: Path, tasks_path: str | Path | None) -> Path:
    requested = root / "TASKS.md" if tasks_path is None else Path(tasks_path)
    if not requested.is_absolute():
        requested = root / requested
    try:
        metadata = requested.lstat()
        resolved = requested.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise BacklogSourceError(f"task ledger is unavailable: {requested}") from exc
    if requested.is_symlink():
        raise BacklogSourceError(f"task ledger must not be a symlink: {requested}")
    if not _is_within(resolved, root):
        raise BacklogSourceError(f"task ledger is outside repository root: {requested}")
    if not stat.S_ISREG(metadata.st_mode):
        raise BacklogSourceError(f"task ledger is not a regular file: {requested}")
    if metadata.st_size > MAX_LEDGER_BYTES:
        raise BacklogSourceError(
            f"task ledger size exceeds {MAX_LEDGER_BYTES} byte limit"
        )
    return resolved


def _identity(path: Path) -> tuple[int, int, int, int]:
    metadata = path.stat()
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _reject_noncanonical_markers(text: str) -> None:
    """Refuse GFM markers that the canonical parser would silently omit."""
    for line_number, line in enumerate(text.splitlines(), start=1):
        marker = _TASK_MARKER.match(line)
        if marker is None:
            continue
        stripped = line.strip()
        if stripped.startswith(("- [x]", "- [ ]")):
            continue
        raise BacklogSourceError(
            "noncanonical task marker at line "
            f"{line_number}; TASKS.md requires '- [x]' or '- [ ]'"
        )


def _field_value(line: str, field_name: str) -> str:
    values: list[str] = []
    for segment in line.split("|")[1:]:
        match = _FIELD.match(segment)
        if match is None or match.group("name").strip().casefold() != field_name:
            continue
        values.append(match.group("value").strip())
    if len(values) > 1:
        raise BacklogSourceError(f"duplicate {field_name!r} fields in checked task")
    return values[0] if values else ""


def _normalize_node_id(candidate: str) -> str | None:
    node_id = candidate.strip().strip("'\"").rstrip(".)")
    if not node_id or len(node_id) > MAX_REFERENCE_LENGTH:
        return None
    if any(ord(char) < 32 for char in node_id):
        return None
    path_text, separator, selector = node_id.partition("::")
    if not separator or not selector or path_text.startswith("-"):
        return None
    pure_path = PurePosixPath(path_text)
    if pure_path.is_absolute() or ".." in pure_path.parts or pure_path.suffix != ".py":
        return None
    return node_id


def _evidence_ids(line: str) -> list[str]:
    evidence = _field_value(line, "evidence")
    if not evidence:
        return []
    candidates: list[str] = []
    masked = evidence
    for match in _CODE_SPAN.finditer(evidence):
        span = match.group(1).strip()
        if ".py::" in span:
            candidates.append(span)
        masked = masked.replace(match.group(0), " ")
    candidates.extend(match.group("node") for match in _PLAIN_NODE_ID.finditer(masked))

    result: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        node_id = _normalize_node_id(candidate)
        if node_id is None or node_id in seen:
            continue
        seen.add(node_id)
        result.append(node_id)
    if len(result) > MAX_EVIDENCE_IDS:
        raise BacklogSourceError(
            f"checked task exceeds {MAX_EVIDENCE_IDS} evidence ID limit"
        )
    return result


def _looks_like_file_reference(value: str) -> bool:
    if not value or "::" in value or "://" in value or any(char.isspace() for char in value):
        return False
    path = PurePosixPath(value)
    return "/" in value or path.suffix.casefold() in _PATH_SUFFIXES


def _normalize_touched_file(candidate: str) -> str:
    value = candidate.strip()
    if not value or len(value) > MAX_REFERENCE_LENGTH:
        raise BacklogSourceError("touched file reference is empty or too long")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value.startswith("-"):
        raise BacklogSourceError(f"touched file is outside repository root: {value}")
    return path.as_posix()


def _touched_files(line: str) -> list[str]:
    description = line.split("|", 1)[0]
    result: list[str] = []
    seen: set[str] = set()
    for match in _CODE_SPAN.finditer(description):
        candidate = match.group(1).strip()
        if not _looks_like_file_reference(candidate):
            continue
        normalized = _normalize_touched_file(candidate)
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    if len(result) > MAX_TOUCHED_FILES:
        raise BacklogSourceError(
            f"checked task exceeds {MAX_TOUCHED_FILES} touched file limit"
        )
    return result


def load_task_ledger(
    repo_root: str | Path,
    *,
    tasks_path: str | Path | None = None,
) -> list[BacklogTask]:
    """Load checked completion claims through the canonical ledger parser.

    Checked records with no executable pytest node ID remain in the result with
    an empty ``evidence_test_ids`` list.  ``BacklogAuditor`` therefore assigns
    ``FALSE_CLAIM`` rather than allowing prose or a commit hash to masquerade as
    executable evidence.
    """
    root = _resolved_repo_root(repo_root)
    ledger = _ledger_path(root, tasks_path)
    before = _identity(ledger)
    try:
        text = ledger.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError) as exc:
        raise BacklogSourceError(f"task ledger cannot be read as UTF-8: {ledger}") from exc
    _reject_noncanonical_markers(text)
    try:
        checked, unchecked = extract_tasks(ledger)
    except (OSError, UnicodeError, ValueError) as exc:
        raise BacklogSourceError(f"canonical task parser failed: {exc}") from exc
    if _identity(ledger) != before:
        raise BacklogSourceError("task ledger changed while it was being parsed")
    if len(checked) + len(unchecked) > MAX_TASKS:
        raise BacklogSourceError(f"task ledger exceeds {MAX_TASKS} task limit")

    tasks: list[BacklogTask] = []
    seen_ids: set[str] = set()
    for record in checked:
        ids = record["ids"]
        if len(ids) != 1:
            raise BacklogSourceError(
                "checked task must have exactly one canonical primary task ID"
            )
        task_id = ids[0]
        if task_id in seen_ids:
            raise BacklogSourceError(f"duplicate checked task ID: {task_id}")
        seen_ids.add(task_id)
        if not task_is_effectively_complete(record, checked=True):
            raise BacklogSourceError(
                f"checked task {task_id} has a non-complete status"
            )
        tasks.append(
            {
                "id": task_id,
                "status": record["status"] or "complete",
                "evidence_test_ids": _evidence_ids(record["line"]),
                "touched_files": _touched_files(record["line"]),
            }
        )
    return tasks


def confined_file_reader(repo_root: str | Path) -> FileReader:
    """Return a bounded reader that cannot follow a path outside ``repo_root``."""
    root = _resolved_repo_root(repo_root)

    def read(path: str) -> str | None:
        requested = Path(path)
        if not requested.is_absolute():
            requested = root / requested
        try:
            metadata = requested.lstat()
            resolved = requested.resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        if requested.is_symlink() or not _is_within(resolved, root):
            return None
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_LEDGER_BYTES:
            return None
        before = _identity(resolved)
        try:
            payload = resolved.read_bytes()
        except OSError:
            return None
        if len(payload) > MAX_LEDGER_BYTES or _identity(resolved) != before:
            return None
        return payload.decode("utf-8", errors="replace")

    return read


__all__ = (
    "MAX_EVIDENCE_IDS",
    "MAX_LEDGER_BYTES",
    "MAX_TASKS",
    "MAX_TOUCHED_FILES",
    "BacklogSourceError",
    "BacklogTask",
    "confined_file_reader",
    "load_task_ledger",
)
