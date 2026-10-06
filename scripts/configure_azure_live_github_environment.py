#!/usr/bin/env python3
"""Configure the protected GitHub Environment for Azure OIDC live proof."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence

from general_ludd.azure.accelerator_credentials import (
    load_azure_accelerator_credentials,
)

_REPOSITORY_PATTERN = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})/"
    r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})\Z"
)
_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_LOCATION_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{1,31}\Z")
_MAX_INVENTORY_BYTES = 1_048_576
_COMMAND_TIMEOUT_SECONDS = 30


class _GithubEnvironmentError(RuntimeError):
    """Content-free failure at the GitHub Environment boundary."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--github-environment", required=True)
    parser.add_argument("--auth-file", required=True)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--containerapp-environment", required=True)
    parser.add_argument("--location", required=True)
    parser.add_argument("--workload-profile", required=True)
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
        raise _GithubEnvironmentError("GitHub CLI invocation failed") from exc


def _inventory(repository: str, github_environment: str) -> dict[str, str]:
    completed = _run_gh(
        [
            "variable",
            "list",
            "--env",
            github_environment,
            "--repo",
            repository,
            "--json",
            "name,value",
        ]
    )
    if completed.returncode != 0:
        raise _GithubEnvironmentError("GitHub Environment inventory failed")
    encoded = completed.stdout.encode("utf-8")
    if len(encoded) > _MAX_INVENTORY_BYTES:
        raise _GithubEnvironmentError("GitHub Environment inventory is too large")
    try:
        raw = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise _GithubEnvironmentError("GitHub Environment inventory is invalid") from exc
    if not isinstance(raw, list):
        raise _GithubEnvironmentError("GitHub Environment inventory is invalid")
    inventory: dict[str, str] = {}
    for item in raw:
        if not isinstance(item, Mapping):
            raise _GithubEnvironmentError("GitHub Environment inventory is invalid")
        name = item.get("name")
        value = item.get("value")
        if not isinstance(name, str) or not isinstance(value, str) or name in inventory:
            raise _GithubEnvironmentError("GitHub Environment inventory is invalid")
        inventory[name] = value
    return inventory


def _set(
    repository: str,
    github_environment: str,
    name: str,
    value: str,
) -> None:
    completed = _run_gh(
        [
            "variable",
            "set",
            name,
            "--env",
            github_environment,
            "--repo",
            repository,
            "--body",
            value,
        ]
    )
    if completed.returncode != 0:
        raise _GithubEnvironmentError("GitHub Environment update failed")


def _delete(repository: str, github_environment: str, name: str) -> None:
    completed = _run_gh(
        [
            "variable",
            "delete",
            name,
            "--env",
            github_environment,
            "--repo",
            repository,
        ]
    )
    if completed.returncode != 0:
        raise _GithubEnvironmentError("GitHub Environment rollback failed")


def _restore(
    repository: str,
    github_environment: str,
    before: Mapping[str, str],
    changed: Sequence[str],
) -> bool:
    restored = True
    for name in reversed(changed):
        try:
            if name in before:
                _set(repository, github_environment, name, before[name])
            else:
                _delete(repository, github_environment, name)
        except _GithubEnvironmentError:
            restored = False
    return restored


def _validate(arguments: argparse.Namespace) -> None:
    if _REPOSITORY_PATTERN.fullmatch(arguments.repository) is None:
        raise ValueError
    for value in (
        arguments.github_environment,
        arguments.resource_group,
        arguments.containerapp_environment,
        arguments.workload_profile,
    ):
        if _IDENTIFIER_PATTERN.fullmatch(value) is None:
            raise ValueError
    if _LOCATION_PATTERN.fullmatch(arguments.location) is None:
        raise ValueError
    if not arguments.auth_file or arguments.validate_only not in {"0", "1"}:
        raise ValueError


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        _validate(arguments)
    except ValueError:
        print("AZURE_LIVE_ENV_CONFIG_ERROR class=invalid_input", file=sys.stderr)
        return 2

    if arguments.validate_only == "1":
        print(
            "AZURE_LIVE_ENV_CONFIG_PLAN "
            f"repository={arguments.repository} "
            f"environment={arguments.github_environment} variable_count=7"
        )
        return 0

    try:
        credentials = load_azure_accelerator_credentials(arguments.auth_file)
    except Exception:
        print("AZURE_LIVE_ENV_CONFIG_ERROR class=credential_load_failed", file=sys.stderr)
        return 1

    desired = {
        "AZURE_CLIENT_ID": credentials.client_id,
        "AZURE_TENANT_ID": credentials.tenant_id,
        "AZURE_SUBSCRIPTION_ID": credentials.subscription_id,
        "AZURE_RESOURCE_GROUP": arguments.resource_group,
        "AZURE_CONTAINERAPP_ENVIRONMENT": arguments.containerapp_environment,
        "AZURE_CONTAINERAPP_LOCATION": arguments.location,
        "AZURE_CONTAINERAPP_WORKLOAD_PROFILE": arguments.workload_profile,
    }
    try:
        before = _inventory(arguments.repository, arguments.github_environment)
    except _GithubEnvironmentError:
        print("AZURE_LIVE_ENV_CONFIG_ERROR class=inventory_failed", file=sys.stderr)
        return 1

    changed = [name for name, value in desired.items() if before.get(name) != value]
    if not changed:
        print(
            "AZURE_LIVE_ENV_CONFIG_UNCHANGED "
            f"repository={arguments.repository} "
            f"environment={arguments.github_environment} variable_count=7"
        )
        return 0

    applied: list[str] = []
    try:
        for name in changed:
            _set(
                arguments.repository,
                arguments.github_environment,
                name,
                desired[name],
            )
            applied.append(name)
        after = _inventory(arguments.repository, arguments.github_environment)
        if any(after.get(name) != value for name, value in desired.items()):
            raise _GithubEnvironmentError("GitHub Environment verification failed")
    except _GithubEnvironmentError:
        rollback = "restored" if _restore(
            arguments.repository,
            arguments.github_environment,
            before,
            applied,
        ) else "failed"
        print(
            f"AZURE_LIVE_ENV_CONFIG_ERROR class=mutation_failed rollback={rollback}",
            file=sys.stderr,
        )
        return 1

    print(
        "AZURE_LIVE_ENV_CONFIG_UPDATED "
        f"repository={arguments.repository} "
        f"environment={arguments.github_environment} variable_count=7 "
        f"changed_count={len(changed)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
