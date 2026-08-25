#!/usr/bin/env python3
"""Require complete local and GitHub-hosted CI evidence for one exact SHA."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from scripts.ci_named_shard_files import SHARDS
    from scripts.resource_arbiter import resource_root
else:
    from ci_named_shard_files import SHARDS
    from resource_arbiter import resource_root

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "sandboxcom/gludd"
WORKFLOW_NAME = "Build and Release"


def _read_attestation(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    """Read one JSON attestation without converting malformed evidence to green."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"{path}: unreadable attestation: {exc}"]
    if not isinstance(payload, dict):
        return None, [f"{path}: attestation root is not an object"]
    return payload, []


def _validate_attestation(
    path: Path,
    payload: dict[str, Any],
    *,
    sha: str,
    lane: str,
) -> tuple[set[str], list[str]]:
    """Return covered shards and every fail-closed contract violation."""
    errors: list[str] = []
    if payload.get("schema_version") != 2:
        errors.append(f"{path}: unsupported attestation schema")
    actual_lane = payload.get("lane")
    if actual_lane != lane:
        errors.append(f"{path}: has lane {actual_lane!r}; expected {lane!r}")
    if payload.get("status") != "pass" or payload.get("returncode") != 0:
        errors.append(f"{path}: is not a terminal pass")
    if payload.get("runner") != "scripts/run_ci_shards_serial.py":
        errors.append(f"{path}: was not produced by the canonical shard runner")
    if not payload.get("started_at") or not payload.get("completed_at"):
        errors.append(f"{path}: is missing terminal timestamps")

    identity = payload.get("identity")
    if not isinstance(identity, dict):
        errors.append(f"{path}: identity is missing or malformed")
    else:
        if identity.get("head_sha") != sha or identity.get("expected_sha") != sha:
            errors.append(f"{path}: does not match candidate SHA {sha}")
        if identity.get("clean") is not True:
            errors.append(f"{path}: was produced from a dirty checkout")
        if identity.get("exact_sha") is not True or identity.get("queries_ok") is not True:
            errors.append(f"{path}: repository identity was not exact and verified")

    raw_shards = payload.get("shards")
    if not isinstance(raw_shards, list) or not raw_shards or not all(
        isinstance(item, str) and item for item in raw_shards
    ):
        errors.append(f"{path}: shard coverage is missing or malformed")
        return set(), errors
    shards = set(raw_shards)
    if len(shards) != len(raw_shards):
        errors.append(f"{path}: shard coverage contains duplicates")
    unknown = shards - set(SHARDS)
    if unknown:
        errors.append(f"{path}: unknown shards: {sorted(unknown)}")
    return shards, errors


def verify_dual_track_evidence(
    local_attestation: Path,
    hosted_attestations: Sequence[Path],
    sha: str,
) -> list[str]:
    """Return all reasons the exact candidate lacks complete dual-track proof."""
    errors: list[str] = []
    required = set(SHARDS)
    local_payload, local_errors = _read_attestation(local_attestation)
    errors.extend(local_errors)
    if local_payload is not None:
        local_shards, validation_errors = _validate_attestation(
            local_attestation,
            local_payload,
            sha=sha,
            lane="local",
        )
        errors.extend(validation_errors)
        if local_shards != required:
            errors.append(
                "local attestation does not cover the canonical shard set: "
                f"missing={sorted(required - local_shards)} "
                f"extra={sorted(local_shards - required)}"
            )

    hosted_counts: Counter[str] = Counter()
    for path in sorted(hosted_attestations):
        payload, read_errors = _read_attestation(path)
        errors.extend(read_errors)
        if payload is None:
            continue
        shards, validation_errors = _validate_attestation(
            path,
            payload,
            sha=sha,
            lane="hosted",
        )
        errors.extend(validation_errors)
        if len(shards) != 1:
            errors.append(f"{path}: hosted attestation must cover exactly one shard")
        hosted_counts.update(shards)
    missing = required - set(hosted_counts)
    if missing:
        errors.append(f"missing hosted shard attestations: {sorted(missing)}")
    duplicates = sorted(shard for shard, count in hosted_counts.items() if count != 1)
    if duplicates:
        errors.append(f"hosted shard attestations are ambiguous: {duplicates}")
    return errors


def _git_head() -> str:
    """Resolve the candidate commit without accepting an abbreviated ref."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    sha = result.stdout.strip()
    if result.returncode != 0 or len(sha) != 40:
        raise RuntimeError("unable to resolve an exact 40-character candidate SHA")
    return sha


def _successful_run_id(sha: str) -> int:
    """Return the newest successful hosted workflow for the exact SHA."""
    result = subprocess.run(
        [
            "gh",
            "run",
            "list",
            "--commit",
            sha,
            "-R",
            REPOSITORY,
            "--json",
            "conclusion,databaseId,status,headSha,workflowName",
            "--limit",
            "20",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "gh run list failed").strip()
        raise RuntimeError(detail)
    payload = json.loads(result.stdout or "[]")
    if not isinstance(payload, list):
        raise RuntimeError("gh run list returned a non-list payload")
    candidates = [
        item
        for item in payload
        if isinstance(item, dict)
        and item.get("headSha") == sha
        and item.get("workflowName") == WORKFLOW_NAME
        and item.get("status") == "completed"
        and item.get("conclusion") == "success"
        and isinstance(item.get("databaseId"), int)
    ]
    if not candidates:
        raise RuntimeError(f"no successful hosted workflow found for exact SHA {sha}")
    return max(int(item["databaseId"]) for item in candidates)


def _download_hosted_attestations(run_id: int, destination: Path) -> list[Path]:
    """Download only shard evidence from one hosted run into an owned directory."""
    print(f"DUAL-TRACK-HOSTED-DOWNLOAD run={run_id}", flush=True)
    result = subprocess.run(
        [
            "gh",
            "run",
            "download",
            str(run_id),
            "-R",
            REPOSITORY,
            "--pattern",
            "coverage-*",
            "--dir",
            str(destination),
        ],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "gh run download failed").strip()
        raise RuntimeError(detail)
    paths = sorted(destination.rglob("ci-shard-attestation.json"))
    if not paths:
        raise RuntimeError(f"run {run_id} has no hosted shard attestations")
    return paths


def main() -> int:
    """Fetch hosted evidence when needed and verify both execution lanes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", help="exact candidate SHA; defaults to HEAD")
    parser.add_argument("--local-attestation", type=Path)
    parser.add_argument("--hosted-evidence-dir", type=Path)
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    try:
        sha = args.sha or _git_head()
        if len(sha) != 40:
            raise ValueError("candidate SHA must contain exactly 40 characters")
        if args.validate_only:
            print(f"DUAL-TRACK-CI-VALIDATED sha={sha}")
            return 0
        local = args.local_attestation or (
            resource_root(ROOT) / "ci-shards" / "attestation.json"
        )
        if args.hosted_evidence_dir is not None:
            hosted = sorted(
                args.hosted_evidence_dir.rglob("ci-shard-attestation.json")
            )
            errors = verify_dual_track_evidence(local, hosted, sha)
        else:
            parent = resource_root(ROOT) / "dual-track-downloads"
            parent.mkdir(parents=True, exist_ok=True)
            run_id = args.run_id or _successful_run_id(sha)
            with tempfile.TemporaryDirectory(prefix="hosted-", dir=parent) as temporary:
                hosted = _download_hosted_attestations(run_id, Path(temporary))
                errors = verify_dual_track_evidence(local, hosted, sha)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"DUAL-TRACK-CI-FAIL error={exc}")
        return 2
    if errors:
        for error in errors:
            print(f"DUAL-TRACK-CI-FAIL {error}")
        return 1
    print(f"DUAL-TRACK-CI-PASS sha={sha} hosted-shards={len(SHARDS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
