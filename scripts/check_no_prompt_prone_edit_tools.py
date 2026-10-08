#!/usr/bin/env python3
"""Block prompt-prone edit tooling in agent-facing policy files."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from pathlib import Path

DEFAULT_PATHS = (
    "AGENTS.md",
    "CONTRIBUTING.md",
    "docs/CLAUDE.md",
    "docs/AGENTIC_IMPLEMENTATION_SPEC.md",
    "docs/audit/AGENT_BEHAVIOR_FAILURE_AUDIT.md",
    ".opencode",
)

FORBIDDEN_TOKENS = {
    "apply_patch": "Use make write-text, append-text, replace-text, or copy-file.",
    "functions.apply_patch": "Use make edit targets instead of patch tooling.",
    "request_user_input": "Do not add explicit operator prompts to workflow policy.",
}

TEXT_SUFFIXES = {
    "",
    ".json",
    ".md",
    ".mjs",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".txt",
    ".yml",
    ".yaml",
}

SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
}

ALLOWLIST_FILES = {
    ".opencode/plugin/impl/enforce_make_impl.ts",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _iter_candidate_files(root: Path, requested: Iterable[str]) -> Iterable[Path]:
    for raw_path in requested:
        path = Path(raw_path)
        candidate = path if path.is_absolute() else root / path
        if not candidate.exists():
            yield candidate
            continue
        if candidate.is_file():
            yield candidate
            continue
        for child in candidate.rglob("*"):
            if any(part in SKIP_DIRS for part in child.parts):
                continue
            if child.is_file() and child.suffix in TEXT_SUFFIXES:
                yield child


def _scan_file(path: Path) -> list[str]:
    if not path.exists():
        return [f"{path}: missing configured scan path"]
    try:
        relative = str(path.resolve().relative_to(_repo_root()))
    except ValueError:
        # The path lives outside the repo (e.g. a temp file under pytest's
        # tmp_path). It can never be an allowlisted repo file, so scan it
        # without the repo-relative allowlist check.
        relative = str(path.resolve())
    if relative in ALLOWLIST_FILES:
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return []

    findings: list[str] = []
    for line_number, line in enumerate(lines, start=1):
        for token, guidance in FORBIDDEN_TOKENS.items():
            if token in line:
                findings.append(f"{path}:{line_number}: {token}: {guidance}")
    return findings


def scan(paths: Iterable[str] = DEFAULT_PATHS) -> list[str]:
    root = _repo_root()
    findings: list[str] = []
    for path in sorted(set(_iter_candidate_files(root, paths))):
        findings.extend(_scan_file(path))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reject prompt-prone edit tool references in agent-facing files.")
    parser.add_argument("paths", nargs="*", help="Files or directories to scan")
    args = parser.parse_args(argv)

    findings = scan(args.paths or DEFAULT_PATHS)
    if findings:
        print("Prompt-prone edit tooling references found:")
        for finding in findings:
            print(f"  - {finding}")
        return 1

    print("No prompt-prone edit tooling references found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
