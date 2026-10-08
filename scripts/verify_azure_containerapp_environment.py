#!/usr/bin/env python3
"""Read and verify the GitHub Environment protecting paid Azure live proof."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from urllib.parse import quote

from general_ludd.azure_containerapp_environment_guard import (
    EXPECTED_ENVIRONMENT,
    EnvironmentProtectionError,
    EnvironmentProtectionReceipt,
    verify_environment_protection,
)

MAX_JSON_BYTES = 1_048_576
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
Runner = Callable[..., subprocess.CompletedProcess[str]]


def _canonical_environment() -> dict[str, object]:
    return {
        "name": EXPECTED_ENVIRONMENT,
        "can_admins_bypass": False,
        "protection_rules": [
            {
                "type": "required_reviewers",
                "prevent_self_review": True,
                "reviewers": [{"type": "Team", "reviewer": {"id": 1}}],
            },
            {"type": "branch_policy"},
        ],
        "deployment_branch_policy": {
            "protected_branches": False,
            "custom_branch_policies": True,
        },
    }


def _canonical_branch_policies() -> dict[str, object]:
    policies = [
        {"name": "development"},
        {"name": "master"},
        {"name": "v*"},
    ]
    return {"total_count": len(policies), "branch_policies": policies}


def _decode_mapping(raw: str, code: str) -> Mapping[str, object]:
    try:
        if len(raw.encode("utf-8")) > MAX_JSON_BYTES:
            raise EnvironmentProtectionError(f"{code}-too-large")
        decoded = json.loads(raw)
    except EnvironmentProtectionError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise EnvironmentProtectionError(f"{code}-invalid-json") from exc
    if not isinstance(decoded, Mapping):
        raise EnvironmentProtectionError(f"{code}-invalid-shape")
    return decoded


def _read_mapping(path: Path, code: str) -> Mapping[str, object]:
    try:
        if path.stat().st_size > MAX_JSON_BYTES:
            raise EnvironmentProtectionError(f"{code}-too-large")
        raw = path.read_text(encoding="utf-8")
    except EnvironmentProtectionError:
        raise
    except (OSError, UnicodeError) as exc:
        raise EnvironmentProtectionError(f"{code}-unreadable") from exc
    return _decode_mapping(raw, code)


def _github_get(
    endpoint: str,
    failure_code: str,
    runner: Runner,
) -> Mapping[str, object]:
    try:
        completed = runner(
            [
                "gh",
                "api",
                "--method",
                "GET",
                "-H",
                "Accept: application/vnd.github+json",
                "-H",
                "X-GitHub-Api-Version: 2022-11-28",
                endpoint,
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EnvironmentProtectionError(failure_code) from exc
    if completed.returncode != 0:
        raise EnvironmentProtectionError(failure_code)
    return _decode_mapping(completed.stdout, failure_code)


def _publish_receipt(
    receipt: EnvironmentProtectionReceipt,
    output: Path,
) -> None:
    """Durably replace one regular output with a private canonical receipt."""
    parent = output.parent
    try:
        if output.is_symlink() or parent.is_symlink():
            raise EnvironmentProtectionError("receipt-output-symlink")
        if output.exists() and not output.is_file():
            raise EnvironmentProtectionError("receipt-output-not-regular")
        if not parent.is_dir():
            raise EnvironmentProtectionError("receipt-output-parent-invalid")
    except EnvironmentProtectionError:
        raise
    except OSError as exc:
        raise EnvironmentProtectionError("receipt-output-publication-failed") from exc

    temporary: Path | None = None
    published = False
    try:
        descriptor, raw_temporary = tempfile.mkstemp(
            dir=parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
        )
        temporary = Path(raw_temporary)
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(receipt.canonical_json_bytes())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
        published = True
        metadata = output.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError("receipt output is not regular")
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            raise OSError("receipt output is not private")
        directory_descriptor = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except EnvironmentProtectionError:
        raise
    except (OSError, ValueError) as exc:
        if published:
            with suppress(OSError):
                output.unlink(missing_ok=True)
        raise EnvironmentProtectionError("receipt-output-publication-failed") from exc
    finally:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)


def run_guard(
    *,
    repository: str,
    environment: str,
    environment_json: Path | None,
    branch_policies_json: Path | None,
    validate_only: bool,
    runner: Runner = subprocess.run,
) -> EnvironmentProtectionReceipt:
    """Load one environment snapshot and return a non-sensitive receipt."""
    if REPOSITORY.fullmatch(repository) is None:
        raise EnvironmentProtectionError("repository-invalid")
    if environment != EXPECTED_ENVIRONMENT:
        raise EnvironmentProtectionError("environment-name-mismatch")
    if (environment_json is None) != (branch_policies_json is None):
        raise EnvironmentProtectionError("fixture-pair-required")

    if environment_json is not None and branch_policies_json is not None:
        environment_payload = _read_mapping(environment_json, "environment-fixture")
        branch_policies_payload = _read_mapping(
            branch_policies_json,
            "branch-policies-fixture",
        )
    elif validate_only:
        environment_payload = _canonical_environment()
        branch_policies_payload = _canonical_branch_policies()
    else:
        encoded_environment = quote(environment, safe="")
        base = f"repos/{repository}/environments/{encoded_environment}"
        environment_payload = _github_get(
            base,
            "github-environment-lookup-failed",
            runner,
        )
        branch_policies_payload = _github_get(
            f"{base}/deployment-branch-policies?per_page=100",
            "github-branch-policy-lookup-failed",
            runner,
        )
    return verify_environment_protection(
        environment_payload,
        branch_policies_payload,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--environment-json", type=Path)
    parser.add_argument("--branch-policies-json", type=Path)
    parser.add_argument("--receipt-output", type=Path)
    parser.add_argument("--validate-only", choices=("0", "1"), default="0")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the read-only guard and print only bounded evidence."""
    args = _parser().parse_args(argv)
    try:
        receipt = run_guard(
            repository=args.repository,
            environment=args.environment,
            environment_json=args.environment_json,
            branch_policies_json=args.branch_policies_json,
            validate_only=args.validate_only == "1",
        )
        if args.receipt_output is not None:
            _publish_receipt(receipt, args.receipt_output)
    except EnvironmentProtectionError as exc:
        print(
            "AZURE_CONTAINERAPP_ENVIRONMENT_GUARD_REJECTED "
            f"reason={exc.code} mutation=false"
        )
        return 2
    print(receipt.render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
