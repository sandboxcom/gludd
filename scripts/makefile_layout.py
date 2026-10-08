#!/usr/bin/env python3
"""Read the root Makefile and its explicit, ordered ``make/*.mk`` fragments."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

INCLUDE_PREFIX_RE = re.compile(r"^(?:-include|sinclude|include)(?:\s|$)")
EXACT_INCLUDE_RE = re.compile(r"^include (make/[A-Za-z0-9][A-Za-z0-9_.-]*\.mk)$")


class MakefileLayoutError(RuntimeError):
    """Raised when Makefile fragment discovery is ambiguous or unsafe."""


def _read_text(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise MakefileLayoutError(f"missing or unreadable Makefile source: {path}") from exc
    if text and not text.endswith("\n"):
        raise MakefileLayoutError(f"Makefile source lacks a final newline: {path}")
    return text


def _fragment_paths(entrypoint: Path, text: str) -> tuple[Path, ...]:
    relative_paths: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not INCLUDE_PREFIX_RE.match(line):
            continue
        match = EXACT_INCLUDE_RE.fullmatch(line)
        if match is None:
            raise MakefileLayoutError(
                f"{entrypoint}:{line_number}: include must be one explicit make/*.mk path"
            )
        raw = match.group(1)
        candidate = PurePosixPath(raw)
        if candidate.is_absolute() or ".." in candidate.parts or str(candidate) != raw:
            raise MakefileLayoutError(
                f"{entrypoint}:{line_number}: unsafe include path {raw!r}"
            )
        if raw in relative_paths:
            raise MakefileLayoutError(f"duplicate Makefile fragment include: {raw}")
        relative_paths.append(raw)

    fragments = tuple(entrypoint.parent / path for path in relative_paths)
    for fragment in fragments:
        fragment_text = _read_text(fragment)
        for line_number, line in enumerate(fragment_text.splitlines(), start=1):
            if INCLUDE_PREFIX_RE.match(line):
                raise MakefileLayoutError(
                    f"{fragment}:{line_number}: nested Makefile includes are forbidden"
                )
    return fragments


def makefile_sources(entrypoint: Path) -> tuple[Path, ...]:
    """Return the entrypoint followed by every explicit fragment in parse order."""

    resolved_entrypoint = entrypoint.resolve()
    text = _read_text(resolved_entrypoint)
    return (resolved_entrypoint, *_fragment_paths(resolved_entrypoint, text))


def compose_makefile(entrypoint: Path) -> str:
    """Return the logical Makefile text with explicit includes expanded in place."""

    resolved_entrypoint = entrypoint.resolve()
    text = _read_text(resolved_entrypoint)
    fragments = iter(_fragment_paths(resolved_entrypoint, text))
    output: list[str] = []
    for line in text.splitlines(keepends=True):
        if EXACT_INCLUDE_RE.fullmatch(line.removesuffix("\n")):
            output.append(_read_text(next(fragments)))
        else:
            output.append(line)
    return "".join(output)
