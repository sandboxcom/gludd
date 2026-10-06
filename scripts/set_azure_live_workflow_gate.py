#!/usr/bin/env python3
"""Safely control the repository variable guarding the Azure live proof."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence

_VARIABLE_NAME = "AZURE_CONTAINERAPP_LIVE_ENABLED"
_REPOSITORY_PATTERN = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})/"
    r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})\Z"
)
_MAX_INVENTORY_BYTES = 1_048_576
_COMMAND_TIMEOUT_SECONDS = 30


class _GithubVariableError(RuntimeError):
    """Content-free failure at the GitHub variable boundary."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--enabled", required=True)
    parser.add_argument("--validate-only", required=True)
    return parser


def _run_gh(arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["GH_PROMPT_DISABLED"] = "1"
    environment["GH_NO_UPDATE_NOTIFIER"] = "1"
    try:
        return subprocess.run(
            ["gh", *arguments],
            capture_output=True,
            text=True,
            check=False,
            timeout=_COMMAND_TIMEOUT_SECONDS,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _GithubVariableError("GitHub CLI invocation failed") from exc


def _inventory(repository: str) -> dict[str, str]:
    completed = _run_gh(
        [
            "variable",
            "list",
            "--repo",
            repository,
            "--json",
            "name,value",
        ]
    )
    if completed.returncode != 0:
        raise _GithubVariableError("repository variable inventory failed")
    encoded = completed.stdout.encode("utf-8")
    if len(encoded) > _MAX_INVENTORY_BYTES:
        raise _GithubVariableError("repository variable inventory is too large")
    try:
        raw = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise _GithubVariableError("repository variable inventory is invalid") from exc
    if not isinstance(raw, list):
        raise _GithubVariableError("repository variable inventory is invalid")

    inventory: dict[str, str] = {}
    for item in raw:
        if not isinstance(item, Mapping):
            raise _GithubVariableError("repository variable inventory is invalid")
        name = item.get("name")
        value = item.get("value")
        if not isinstance(name, str) or not isinstance(value, str) or name in inventory:
            raise _GithubVariableError("repository variable inventory is invalid")
        inventory[name] = value
    return inventory


def _set(repository: str, value: str) -> None:
    completed = _run_gh(
        [
            "variable",
            "set",
            _VARIABLE_NAME,
            "--repo",
            repository,
            "--body",
            value,
        ]
    )
    if completed.returncode != 0:
        raise _GithubVariableError("repository variable update failed")


def _delete(repository: str) -> None:
    completed = _run_gh(
        ["variable", "delete", _VARIABLE_NAME, "--repo", repository]
    )
    if completed.returncode != 0:
        raise _GithubVariableError("repository variable rollback failed")


def _restore(repository: str, previous: str | None) -> None:
    if previous is None:
        _delete(repository)
    else:
        _set(repository, previous)


def _validated_flag(value: str, label: str) -> str:
    if value not in {"0", "1"}:
        raise ValueError(f"{label} must be 0 or 1")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if _REPOSITORY_PATTERN.fullmatch(arguments.repository) is None:
            raise ValueError("repository must be owner/name")
        enabled = _validated_flag(arguments.enabled, "enabled")
        validate_only = _validated_flag(arguments.validate_only, "validate-only")
    except ValueError:
        print("AZURE_LIVE_GATE_ERROR class=invalid_input", file=sys.stderr)
        return 2

    desired = "true" if enabled == "1" else "false"
    if validate_only == "1":
        print(
            "AZURE_LIVE_GATE_PLAN "
            f"repository={arguments.repository} variable={_VARIABLE_NAME} "
            f"enabled={desired}"
        )
        return 0

    try:
        before = _inventory(arguments.repository)
    except _GithubVariableError:
        print("AZURE_LIVE_GATE_ERROR class=inventory_failed", file=sys.stderr)
        return 1

    previous = before.get(_VARIABLE_NAME)
    if previous == desired:
        print(
            "AZURE_LIVE_GATE_UNCHANGED "
            f"repository={arguments.repository} variable={_VARIABLE_NAME} "
            f"enabled={desired}"
        )
        return 0

    mutation_succeeded = False
    try:
        _set(arguments.repository, desired)
        mutation_succeeded = True
        after = _inventory(arguments.repository)
        if after.get(_VARIABLE_NAME) != desired:
            raise _GithubVariableError("repository variable verification failed")
    except _GithubVariableError:
        rollback = "not_required"
        if mutation_succeeded:
            try:
                _restore(arguments.repository, previous)
                rollback = "restored"
            except _GithubVariableError:
                rollback = "failed"
        print(
            "AZURE_LIVE_GATE_ERROR "
            f"class=mutation_failed rollback={rollback}",
            file=sys.stderr,
        )
        return 1

    print(
        "AZURE_LIVE_GATE_UPDATED "
        f"repository={arguments.repository} variable={_VARIABLE_NAME} "
        f"enabled={desired} previously_present={str(previous is not None).lower()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
