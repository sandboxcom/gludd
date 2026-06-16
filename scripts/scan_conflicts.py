"""Scan git-tracked text files for unresolved git conflict markers.

Flags lines that begin a conflict hunk left behind by a botched merge/rebase:

    <<<<<<<   (start of ours)
    |||||||   (merge-base, diff3 style)
    =======   (separator)
    >>>>>>>   (end of theirs)

The bare ``=======`` separator is *also* a legitimate markdown horizontal rule
and an RST section underline, so flagging it unconditionally is a false-positive
magnet. We therefore only flag ``=======`` when the same file ALSO contains a
``<<<<<<<`` or ``>>>>>>>`` marker — i.e. there is a real conflict in progress.
``<<<<<<<``, ``|||||||`` and ``>>>>>>>`` are never valid prose, so they are
always flagged. See ``tests/unit/test_conflict_scanner.py`` for the matrix.

Stdlib only — no project imports. Importable for unit tests:
``scan_paths(paths) -> list[(path, line, marker)]`` plus a ``main()`` argparse
wrapper. With no path args ``main()`` scans every git-tracked file via
``git ls-files``.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# Markers that open a conflict hunk. A line "begins" the marker if it starts
# with the 7-char run (git emits exactly 7, optionally followed by a label).
ALWAYS_MARKERS: tuple[str, ...] = ("<<<<<<<", "|||||||", ">>>>>>>")
SEPARATOR_MARKER = "======="
ALL_MARKERS: tuple[str, ...] = (*ALWAYS_MARKERS, SEPARATOR_MARKER)

# Skip the scanner's own fixtures dir (tests deliberately embed markers there).
SKIP_DIR_PARTS: tuple[str, ...] = ("conflict_fixtures",)

Finding = tuple[str, int, str]


def _git_tracked_files() -> list[str]:
    """Return every git-tracked path via ``git ls-files`` (list-form, no shell)."""
    try:
        out = subprocess.run(
            ["git", "ls-files"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    return [line for line in out.stdout.splitlines() if line]


def _is_skipped(path: Path) -> bool:
    return any(part in SKIP_DIR_PARTS for part in path.parts)


def _read_text_lines(path: Path) -> list[str] | None:
    """Read a file as text, or return None if it is binary / unreadable."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    # A NUL byte is git's own heuristic for "binary"; skip those.
    if b"\x00" in data:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return text.splitlines()


def scan_file(path: Path) -> list[Finding]:
    """Scan a single file, returning (path, line_no, marker) for each hit.

    ``=======`` is only reported when the file also contains a ``<<<<<<<`` or
    ``>>>>>>>`` marker, so doc separators don't trip the scanner.
    """
    lines = _read_text_lines(path)
    if lines is None:
        return []

    raw_hits: list[Finding] = []
    has_real_conflict = False
    for line_no, line in enumerate(lines, start=1):
        for marker in ALL_MARKERS:
            if line.startswith(marker):
                raw_hits.append((str(path), line_no, marker))
                if marker in ALWAYS_MARKERS:
                    has_real_conflict = True
                break

    findings: list[Finding] = []
    for hit in raw_hits:
        marker = hit[2]
        if marker == SEPARATOR_MARKER and not has_real_conflict:
            continue
        findings.append(hit)
    return findings


def scan_paths(paths: list[str]) -> list[Finding]:
    """Scan the given paths for conflict markers.

    Directories are skipped silently (callers pass files); fixture dirs and
    binary/non-text files are skipped. Returns a flat list of findings sorted
    by (path, line).
    """
    findings: list[Finding] = []
    for raw in paths:
        path = Path(raw)
        if _is_skipped(path):
            continue
        if not path.is_file():
            continue
        findings.extend(scan_file(path))
    findings.sort(key=lambda f: (f[0], f[1]))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scan files for unresolved git conflict markers.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Files to scan. With none, scans all git-tracked files.",
    )
    args = parser.parse_args(argv)

    paths = args.paths or _git_tracked_files()
    findings = scan_paths(paths)

    for path, line_no, marker in findings:
        print(f"{path}:{line_no}: conflict marker {marker!r}")

    if findings:
        print(
            f"\nFound {len(findings)} conflict marker(s) in "
            f"{len({f[0] for f in findings})} file(s).",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
