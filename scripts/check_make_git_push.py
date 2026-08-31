#!/usr/bin/env python3
"""Reject executable Make recipes that push without the repository SSH identity."""

from __future__ import annotations

import argparse
import shlex
from collections.abc import Iterable
from pathlib import Path


def _logical_recipes(text: str) -> Iterable[tuple[int, str]]:
    """Yield logical recipe commands with their first physical line number."""
    start = 0
    parts: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.startswith("\t"):
            if parts:
                yield start, " ".join(parts)
                parts = []
            continue
        command = line[1:].lstrip("@-+").strip()
        if not parts:
            start = line_number
        continued = command.endswith("\\")
        parts.append(command[:-1].rstrip() if continued else command)
        if not continued:
            yield start, " ".join(parts)
            parts = []
    if parts:
        yield start, " ".join(parts)


def _segments(command: str) -> list[list[str]]:
    """Split one shell recipe into command segments without expanding strings."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
    lexer.commenters = "#"
    lexer.whitespace_split = True
    tokens = list(lexer)
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token and all(char in ";&|" for char in token):
            if segments[-1]:
                segments.append([])
            continue
        segments[-1].append(token)
    return [segment for segment in segments if segment]


def find_unprefixed_pushes(text: str) -> list[str]:
    """Return executable git-push recipes missing a same-segment SSH prefix."""
    findings: list[str] = []
    for line_number, command in _logical_recipes(text):
        try:
            segments = _segments(command)
        except ValueError:
            if "git push" in command:
                findings.append(f"{line_number}: {command}")
            continue
        for segment in segments:
            for index in range(len(segment) - 1):
                if segment[index : index + 2] != ["git", "push"]:
                    continue
                prefix = segment[:index]
                if not any(token.startswith("GIT_SSH_COMMAND=") for token in prefix):
                    findings.append(f"{line_number}: {command}")
                    break
            else:
                continue
            break
    return findings


def _parser() -> argparse.ArgumentParser:
    """Build the bounded command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("makefile", type=Path)
    return parser


def main() -> int:
    """Validate one Makefile and emit stable release-gate diagnostics."""
    args = _parser().parse_args()
    try:
        text = args.makefile.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        print(f"CHECK_MAKE_GIT_PUSH_FAIL path={args.makefile} error={type(exc).__name__}")
        return 2
    findings = find_unprefixed_pushes(text)
    if findings:
        for finding in findings:
            print(f"CHECK_MAKE_GIT_PUSH_VIOLATION {finding}")
        print(f"CHECK_MAKE_GIT_PUSH_FAIL findings={len(findings)}")
        return 1
    print("CHECK_MAKE_GIT_PUSH_PASS findings=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
