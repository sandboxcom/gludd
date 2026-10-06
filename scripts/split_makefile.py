#!/usr/bin/env python3
"""Split the historical monolithic Makefile into ordered semantic fragments."""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from scripts.makefile_layout import MakefileLayoutError, makefile_sources

MAX_LINES_EXCLUSIVE = 2500


@dataclass(frozen=True, slots=True)
class PartSpec:
    """Output name and the exact first line that begins a semantic part."""

    name: str
    start_marker: str | None


PARTS: tuple[PartSpec, ...] = (
    PartSpec("00-foundation.mk", None),
    PartSpec("10-observability-and-tests.mk", "# --- Notification system ---"),
    PartSpec(
        "20-recovery-and-git.mk",
        "# --- Crash recovery: cleanup after OpenCode/JSC crash ---",
    ),
    PartSpec("30-ci-and-release.mk", "# Item 17: Poll CI until green with periodic heartbeat"),
    PartSpec("40-cross-version-and-worktrees.mk", "# --- Cross-version CI reproduction (W16) ---"),
    PartSpec("50-development-and-guardrails.mk", "# --- Development-branch workflow targets ---"),
    PartSpec(
        "60-quality-packaging-and-sandbox.mk",
        "# --- Proactive bug scanner: find issues before the user does ---",
    ),
    PartSpec("70-orchestration-and-model-runtime.mk", "# --- Orchestration planner (#32) ---"),
    PartSpec(
        "80-agent-enforcement.mk",
        "# --- Agent watchdog daemon (10s poll, resets streak counter) ---",
    ),
    PartSpec(
        "90-infrastructure-and-services.mk",
        "# --- Terraform: shared provider plugin cache (one download per provider) -----",
    ),
    PartSpec("95-collections-and-pipelines.mk", "# --- Collection Tests ---"),
    PartSpec(
        "99-azure-and-local-models.mk",
        "# --- Azure Event Guard (Azure Activity Log smoke-test guard) ---",
    ),
)


class SplitError(RuntimeError):
    """Raised when the source cannot be partitioned without semantic ambiguity."""


def _partition(source: str) -> tuple[tuple[PartSpec, str], ...]:
    lines = source.splitlines(keepends=True)
    starts = [0]
    for spec in PARTS[1:]:
        marker = f"{spec.start_marker}\n"
        matches = [index for index, line in enumerate(lines) if line == marker]
        if len(matches) != 1:
            raise SplitError(
                f"split marker must occur exactly once: {spec.start_marker!r}; "
                f"found={len(matches)}"
            )
        index = matches[0]
        if index == 0 or lines[index - 1].strip():
            raise SplitError(f"split marker is not preceded by a blank line: {spec.start_marker}")
        starts.append(index)
    if starts != sorted(starts) or len(starts) != len(set(starts)):
        raise SplitError("split markers are not in canonical order")

    result: list[tuple[PartSpec, str]] = []
    boundaries = (*starts, len(lines))
    for position, spec in enumerate(PARTS):
        text = "".join(lines[boundaries[position] : boundaries[position + 1]])
        count = len(text.splitlines())
        if not text or count >= MAX_LINES_EXCLUSIVE:
            raise SplitError(
                f"{spec.name}: invalid fragment line count {count}; "
                f"must be 1..{MAX_LINES_EXCLUSIVE - 1}"
            )
        result.append((spec, text))
    if "".join(text for _, text in result) != source:
        raise SplitError("fragment concatenation does not reproduce the source byte-for-byte")
    return tuple(result)


def _entrypoint_text() -> str:
    includes = "\n".join(f"include make/{spec.name}" for spec in PARTS)
    return (
        "# Canonical GNU Make entrypoint.\n"
        "# Targets and defaults live in explicit semantic fragments; order is API.\n"
        "# Do not replace these declarations with a wildcard include.\n\n"
        f"{includes}\n"
    )


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    finally:
        temporary = Path(temporary_name)
        if temporary.exists():
            temporary.unlink()


def _validate_split(entrypoint: Path) -> tuple[Path, ...]:
    try:
        sources = makefile_sources(entrypoint)
    except MakefileLayoutError as exc:
        raise SplitError(str(exc)) from exc
    expected = tuple(entrypoint.parent / "make" / spec.name for spec in PARTS)
    if sources[1:] != expected:
        raise SplitError(
            "entrypoint include order drifted: "
            f"actual={[str(path) for path in sources[1:]]}"
        )
    for path in sources:
        count = len(path.read_text(encoding="utf-8").splitlines())
        if count >= MAX_LINES_EXCLUSIVE:
            raise SplitError(f"{path}: {count} lines exceeds split layout ceiling")
    return sources


def _make_help_output(root: Path, *, dry_run: bool) -> bytes:
    command = ["make", "--no-print-directory"]
    if dry_run:
        command.append("-n")
    command.append("help")
    result = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        check=False,
        shell=False,
        timeout=30,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace")[-500:]
        raise SplitError(
            f"{'dry-run ' if dry_run else ''}help failed with "
            f"exit {result.returncode}: {detail}"
        )
    return result.stdout


def split_makefile(entrypoint: Path, *, apply: bool) -> tuple[Path, ...]:
    """Validate an existing split or plan/apply the one-time semantic partition."""

    entrypoint = entrypoint.resolve()
    source = entrypoint.read_text(encoding="utf-8")
    if source.startswith("# Canonical GNU Make entrypoint."):
        return _validate_split(entrypoint)

    partitioned = _partition(source)
    make_directory = entrypoint.parent / "make"
    expected_names = {spec.name for spec in PARTS}
    if make_directory.exists():
        unexpected = sorted(
            path.name for path in make_directory.glob("*.mk") if path.name not in expected_names
        )
        if unexpected:
            raise SplitError(f"unexpected pre-existing make fragments: {unexpected}")
    if not apply:
        return (entrypoint, *(make_directory / spec.name for spec, _ in partitioned))

    help_before = _make_help_output(entrypoint.parent, dry_run=False)
    dry_help_before = _make_help_output(entrypoint.parent, dry_run=True)
    for spec, text in partitioned:
        _atomic_write(make_directory / spec.name, text)
    _atomic_write(entrypoint, _entrypoint_text())
    try:
        sources = _validate_split(entrypoint)
        help_after = _make_help_output(entrypoint.parent, dry_run=False)
        dry_help_after = _make_help_output(entrypoint.parent, dry_run=True)
        if help_before != help_after or dry_help_before != dry_help_after:
            raise SplitError("make help or make -n help output changed after split")
    except (OSError, UnicodeError, SplitError):
        _atomic_write(entrypoint, source)
        raise
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    print(
        f"makefile-split: APPLIED source_sha256={digest} "
        f"fragments={len(sources) - 1}"
    )
    return sources


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--entrypoint", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        sources = split_makefile(args.entrypoint, apply=args.apply)
    except (OSError, UnicodeError, SplitError) as exc:
        print(f"makefile-split: ERROR: {exc}")
        return 2
    mode = "validated" if args.entrypoint.read_text(encoding="utf-8").startswith(
        "# Canonical GNU Make entrypoint."
    ) else "planned"
    print(f"makefile-split: {mode.upper()} fragments={len(sources) - 1}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
