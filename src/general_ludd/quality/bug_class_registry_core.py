"""Core contracts, source scanning, and contextual bug-class detectors."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

SEED_MARKER = "bug-class-seed" ":exclude"
SWEEP_ROOT = Path("src") / "general_ludd"
Occurrence = tuple[str, int, str]


@dataclass(frozen=True)
class BugClass:
    """Detection rule paired with the regression test guarding that rule."""

    id: str
    description: str
    detector: re.Pattern[str] | Callable[[str, str], bool]
    guard_test_id: str
    remediation: str


_REGISTRY_FILENAMES = frozenset(
    {"bug_class_registry.py", "bug_class_registry_core.py"}
)


def _iter_source_files(repo_root: Path) -> list[Path]:
    """Return deterministic Python candidates beneath the source root."""
    base = repo_root / SWEEP_ROOT
    if not base.is_dir():
        return []
    return sorted(
        path
        for path in base.rglob("*.py")
        if path.is_file() and path.name not in _REGISTRY_FILENAMES
    )


def _read_text(path: Path) -> str | None:
    """Read UTF-8 source, returning ``None`` for binary or unreadable files."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _scan_file(path: Path, text: str, bug_class: BugClass) -> list[Occurrence]:
    """Find every occurrence of one bug class in a source file."""
    detector = bug_class.detector
    occurrences: list[Occurrence] = []
    if isinstance(detector, re.Pattern):
        for lineno, line in enumerate(text.splitlines(), start=1):
            if SEED_MARKER not in line and detector.search(line):
                occurrences.append((str(path), lineno, line.strip()))
        return occurrences

    cleaned_lines = [line for line in text.splitlines() if SEED_MARKER not in line]
    cleaned = "\n".join(cleaned_lines)
    if detector(str(path), cleaned):
        anchor = cleaned_lines[0].strip() if cleaned_lines else ""
        occurrences.append((str(path), 0, anchor))
    return occurrences


def sweep(
    repo_root: str | Path,
    classes: list[BugClass],
) -> dict[str, list[Occurrence]]:
    """Walk the project source tree and return every match for every class."""
    root = Path(repo_root)
    result: dict[str, list[Occurrence]] = {bug_class.id: [] for bug_class in classes}
    for path in _iter_source_files(root):
        text = _read_text(path)
        if text is None:
            continue
        for bug_class in classes:
            result[bug_class.id].extend(_scan_file(path, text, bug_class))
    return result


def verify_guards(
    classes: list[BugClass],
    known_test_ids: set[str],
) -> list[BugClass]:
    """Return bug classes that do not name an active regression guard."""
    return [
        bug_class
        for bug_class in classes
        if not bug_class.guard_test_id or bug_class.guard_test_id not in known_test_ids
    ]


_SUBPROCESS_TOKENS = (
    "subprocess.run",
    "subprocess.Popen",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "create_subprocess_exec",
)


def detect_unvalidated_subprocess_argv(path: str, text: str) -> bool:
    """Detect interpolation or concatenation directly inside subprocess argv."""
    del path
    for line in text.splitlines():
        if not any(token in line for token in _SUBPROCESS_TOKENS):
            continue
        has_fstring = 'f"' in line or "f'" in line
        has_concat = '" +' in line or "' +" in line or "+ '" in line or '+ "' in line
        if has_fstring or has_concat:
            return True
    return False


_SSRF_SAFE_GUARDS = (
    "is_url_blocked",
    "host_is_blocked",
    "is_safe_endpoint",
    "is_safe_fetch_url",
    "resolved_host_is_blocked",
)
_SSRF_URL_TOKENS = ("base_url", "clone_url", "clone(", "git clone")
_SSRF_SINK_TOKENS = (
    "subprocess",
    "create_subprocess",
    "httpx",
    "requests",
    "client(",
)


def detect_ssrf_unvalidated_url(path: str, text: str) -> bool:
    """Detect URL-bearing sinks in files without canonical SSRF guards."""
    del path
    if any(guard in text for guard in _SSRF_SAFE_GUARDS):
        return False
    has_url = any(token in text for token in _SSRF_URL_TOKENS)
    has_sink = any(token in text for token in _SSRF_SINK_TOKENS)
    return has_url and has_sink


__all__ = (
    "SEED_MARKER",
    "SWEEP_ROOT",
    "BugClass",
    "Occurrence",
    "detect_ssrf_unvalidated_url",
    "detect_unvalidated_subprocess_argv",
    "sweep",
    "verify_guards",
)
