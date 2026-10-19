#!/usr/bin/env python3
"""Persist complete hosted-CI failures and block unowned retries.

The ledger is operational state, not a release claim.  A terminal GitHub run is
immutable per attempt: observing it again is idempotent, while conflicting data
for the same run ID and attempt fails closed. Every failed job becomes an
independently owned failure family so a rerun cannot hide sibling failures.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, TypedDict, cast

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LEDGER = ROOT / ".gludd" / "ci-failure-ledger.json"
DEFAULT_REPOSITORY = "sandboxcom/gludd"
LEDGER_VERSION = 1
MAX_LEDGER_BYTES = 2 * 1024 * 1024
LOCK_TIMEOUT_SECONDS = 2
FULL_SHA = re.compile(r"[0-9a-f]{40}\Z")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
MAKE_TARGET = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
MAKE_VARIABLE = re.compile(r"[A-Z][A-Z0-9_]*=.*\Z")
NON_FAILURE_CONCLUSIONS = {"success", "skipped", "neutral"}
FAILURE_STATUSES = {"open", "repaired", "resolved"}
RUN_FIELDS = (
    "attempt,databaseId,headSha,headBranch,status,conclusion,url,workflowName,jobs"
)
RUN_METADATA_FIELDS = RUN_FIELDS.removesuffix(",jobs")
RUNNER_ACQUISITION_MESSAGE = (
    "The job was not acquired by Runner of type hosted even after multiple attempts"
)
MAKE_RECURSION_ENV_VARS = (
    "MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES", "GNUMAKEFLAGS",
)
REPAIR_OWNER_ENV_VARS = (
    "CI_FAILURE_LEDGER", "CI_FAILURE_FAMILIES", "CI_FAILURE_VALIDATE_ONLY",
    "CI_REPAIR_ALL_OPEN", "CI_REPAIR_SHA", "CI_REPAIR_EVIDENCE_TARGET", "CI_REPAIR_EVIDENCE_VARS",
)

Runner = Callable[..., subprocess.CompletedProcess[str]]
AncestorCheck = Callable[[str, str], bool]


class LedgerError(RuntimeError):
    """Signal missing, contradictory, or unsafe CI ownership evidence."""


class RepairGuidance(TypedDict):
    """Parameterized first-run guidance without inferred family coverage."""

    family_ids: list[str]
    failed_sha: str | None
    candidate_sha: str | None
    argv: None
    argv_template: list[str]
    required_parameters: list[str]
    reason: str


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def new_ledger() -> dict[str, Any]:
    """Return an empty versioned ledger."""
    return {"version": LEDGER_VERSION, "runs": {}, "families": {}}


def _require_mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LedgerError(f"{label} must be an object")
    return cast(dict[str, Any], value)


def validate_ledger(ledger: dict[str, Any]) -> None:
    """Validate enough schema to make every guard fail closed."""
    if ledger.get("version") != LEDGER_VERSION:
        raise LedgerError("unsupported ledger version")
    runs = _require_mapping(ledger.get("runs"), "runs")
    families = _require_mapping(ledger.get("families"), "families")
    for run_key, run in runs.items():
        match = re.fullmatch(r"([1-9][0-9]*)(?::([1-9][0-9]*))?", str(run_key))
        if match is None:
            raise LedgerError(f"invalid run key: {run_key!r}")
        record = _require_mapping(run, f"run {run_key}")
        expected_run_id = int(match.group(1))
        expected_attempt = int(match.group(2) or "1")
        if _run_id(record.get("run_id")) != expected_run_id:
            raise LedgerError(f"run {run_key} identity mismatch")
        if _attempt(record) != expected_attempt:
            raise LedgerError(f"run {run_key} attempt mismatch")
        if FULL_SHA.fullmatch(str(record.get("sha") or "")) is None:
            raise LedgerError(f"run {run_key} has invalid SHA")
        digest = str(record.get("payload_fingerprint") or "")
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise LedgerError(f"run {run_key} has invalid payload fingerprint")
    for family_id, family in families.items():
        if re.fullmatch(r"[0-9a-f]{64}", str(family_id)) is None:
            raise LedgerError(f"invalid family key: {family_id!r}")
        record = _require_mapping(family, f"family {family_id}")
        if record.get("family_id") != family_id:
            raise LedgerError(f"family {family_id} identity mismatch")
        if record.get("status") not in FAILURE_STATUSES:
            raise LedgerError(f"family {family_id} has invalid status")
        occurrences = record.get("occurrences")
        if not isinstance(occurrences, list) or not occurrences:
            raise LedgerError(f"family {family_id} has no occurrences")
    _validate_terminal_details(ledger, runs)


def read_ledger(path: Path) -> dict[str, Any]:
    """Load a bounded ledger; absence means no observations yet."""
    if not path.exists():
        return new_ledger()
    try:
        if path.stat().st_size > MAX_LEDGER_BYTES:
            raise LedgerError("ledger exceeds the 2 MiB safety bound")
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LedgerError(f"ledger is invalid JSON: {exc}") from exc
    except OSError as exc:
        raise LedgerError(f"ledger cannot be read: {exc}") from exc
    ledger = _require_mapping(decoded, "ledger")
    validate_ledger(ledger)
    return ledger


def write_ledger(path: Path, ledger: dict[str, Any]) -> None:
    """Atomically write private, bounded operational state."""
    validate_ledger(ledger)
    encoded = (json.dumps(ledger, indent=2, sort_keys=True) + "\n").encode()
    if len(encoded) > MAX_LEDGER_BYTES:
        raise LedgerError("ledger exceeds the 2 MiB safety bound")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{path.name}.", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


@contextmanager
def _locked_ledger(path: Path) -> Iterator[dict[str, Any]]:
    """Reload the latest state under one bounded, private native writer lock."""
    from filelock import FileLock, Timeout

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    if lock_path.is_symlink():
        raise LedgerError("ledger transaction lock must not be a symbolic link")
    try:
        with FileLock(str(lock_path), timeout=LOCK_TIMEOUT_SECONDS, mode=0o600):
            yield read_ledger(path)
    except Timeout as exc:
        raise LedgerError("ledger transaction lock is already held; retry the command") from exc


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LedgerError(f"{label} must be a non-empty string")
    return value.strip()


def _run_id(value: object) -> int:
    if isinstance(value, bool):
        raise LedgerError("run ID must be a positive integer")
    try:
        run_id = int(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise LedgerError("run ID must be a positive integer") from exc
    if run_id < 1:
        raise LedgerError("run ID must be a positive integer")
    return run_id


def _attempt(payload: dict[str, Any]) -> int:
    raw = payload.get("attempt", 1)
    if isinstance(raw, bool):
        raise LedgerError("run attempt must be a positive integer")
    try:
        attempt = int(cast(Any, raw))
    except (TypeError, ValueError) as exc:
        raise LedgerError("run attempt must be a positive integer") from exc
    if attempt < 1:
        raise LedgerError("run attempt must be a positive integer")
    return attempt


def _snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    run_id = _run_id(payload.get("databaseId"))
    attempt = _attempt(payload)
    sha = _text(payload.get("headSha"), "head SHA").lower()
    if FULL_SHA.fullmatch(sha) is None:
        raise LedgerError("head SHA must be a full lowercase 40-character hash")
    status = _text(payload.get("status"), "run status").lower()
    if status != "completed":
        raise LedgerError(f"run {run_id} is not terminal (status={status})")
    conclusion = _text(payload.get("conclusion"), "run conclusion").lower()
    workflow = _text(payload.get("workflowName"), "workflow name")
    branch = _text(payload.get("headBranch"), "head branch")
    raw_jobs = payload.get("jobs")
    if not isinstance(raw_jobs, list) or not raw_jobs:
        raise LedgerError(f"run {run_id} jobs must be a non-empty array")
    jobs: list[dict[str, Any]] = []
    for index, raw_job in enumerate(raw_jobs):
        job = _require_mapping(raw_job, f"job {index}")
        name = _text(job.get("name"), f"job {index} name")
        job_status = _text(job.get("status"), f"job {name} status").lower()
        if job_status != "completed":
            raise LedgerError(f"terminal run {run_id} contains non-terminal job {name}")
        job_conclusion = _text(job.get("conclusion"), f"job {name} conclusion").lower()
        raw_steps = job.get("steps", [])
        if not isinstance(raw_steps, list):
            raise LedgerError(f"job {name} steps must be an array")
        failed_steps: list[str] = []
        for step_index, raw_step in enumerate(raw_steps):
            step = _require_mapping(raw_step, f"job {name} step {step_index}")
            step_conclusion = str(step.get("conclusion") or "").lower()
            if step_conclusion and step_conclusion not in NON_FAILURE_CONCLUSIONS:
                failed_steps.append(_text(step.get("name"), f"job {name} step name"))
        if job_conclusion not in NON_FAILURE_CONCLUSIONS and not failed_steps:
            failed_steps.append(f"<job:{job_conclusion}>")
        jobs.append(
            {
                "name": name,
                "status": job_status,
                "conclusion": job_conclusion,
                "failed_steps": failed_steps,
            }
        )
    snapshot = {
        "run_id": run_id,
        "sha": sha,
        "branch": branch,
        "workflow": workflow,
        "status": status,
        "conclusion": conclusion,
        "url": str(payload.get("url") or ""),
        "jobs": jobs,
    }
    # Attempt 1 omits the field to preserve fingerprints already written by
    # ledger version 1. Later attempts use a distinct run key and fingerprint.
    if attempt > 1:
        snapshot["attempt"] = attempt
    return snapshot


def _digest(value: object) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _receipt_snapshot(receipt: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in receipt.items()
        if key not in {"observed_at", "payload_fingerprint"}
    }


def _is_terminal_detail_enrichment(before: dict[str, Any], after: dict[str, Any]) -> bool:
    """Permit only missing failed-step names to fill for identical ordered jobs."""
    if {key: value for key, value in before.items() if key != "jobs"} != {
        key: value for key, value in after.items() if key != "jobs"
    }:
        return False
    old_jobs, new_jobs = before.get("jobs"), after.get("jobs")
    if not isinstance(old_jobs, list) or not isinstance(new_jobs, list) or len(old_jobs) != len(new_jobs):
        return False
    changed = False
    for old, new in zip(old_jobs, new_jobs, strict=True):
        if not isinstance(old, dict) or not isinstance(new, dict):
            return False
        if old == new:
            continue
        steps = new.get("failed_steps")
        if (
            old.get("conclusion") != "failure" or old.get("failed_steps") != ["<job:failure>"]
            or not isinstance(steps, list) or not steps
            or not all(
                isinstance(step, str) and bool(step) and step == step.strip() and not step.startswith("<job:")
                for step in steps
            )
            or {**old, "failed_steps": steps} != new
        ):
            return False
        changed = True
    return changed


def _validate_terminal_details(ledger: dict[str, Any], runs: dict[str, Any]) -> None:
    """Validate bounded additive detail projections against immutable receipts."""
    details = _require_mapping(ledger.get("terminal_details", {}), "terminal details")
    if len(details) > len(runs):
        raise LedgerError("terminal details exceed the immutable run inventory")
    fields = {"schema_version", "original_payload_fingerprint", "payload_fingerprint", "observed_at", "jobs"}
    for run_key, raw in details.items():
        detail = _require_mapping(raw, f"terminal detail {run_key}")
        if (
            run_key not in runs or set(detail) != fields
            or type(detail.get("schema_version")) is not int or detail["schema_version"] != 1
        ):
            raise LedgerError(f"terminal detail {run_key} has invalid schema or run identity")
        _text(detail["observed_at"], f"terminal detail {run_key} observation time")
        receipt = cast(dict[str, Any], runs[run_key])
        original = _receipt_snapshot(receipt)
        if (
            detail["original_payload_fingerprint"] != receipt["payload_fingerprint"]
            or _digest(original) != receipt["payload_fingerprint"]
        ):
            raise LedgerError(f"terminal detail {run_key} original receipt fingerprint mismatch")
        refined = {**original, "jobs": detail["jobs"]}
        if (
            not _is_terminal_detail_enrichment(original, refined)
            or _digest(refined) != detail["payload_fingerprint"]
        ):
            raise LedgerError(f"terminal detail {run_key} is not a verified failure-step enrichment")


def _family_id(snapshot: dict[str, Any], job: dict[str, Any]) -> str:
    return _digest(
        {
            "branch": snapshot["branch"],
            "workflow": snapshot["workflow"],
            "job": job["name"],
            "conclusion": job["conclusion"],
            "failed_steps": job["failed_steps"],
        }
    )


def _non_success_resolution_proven(
    family: dict[str, Any], runs: dict[str, Any]
) -> bool:
    """Require immutable, matching evidence before correcting a legacy resolution."""
    candidates = [
        run
        for run in runs.values()
        if run.get("run_id") == family.get("resolved_by_run")
        and run.get("sha") == family.get("resolved_by_sha")
        and run.get("branch") == family.get("branch")
        and run.get("workflow") == family.get("workflow")
        and (
            "resolved_by_attempt" not in family
            or _attempt(run) == family["resolved_by_attempt"]
        )
    ]
    # Historical receipts omitted the attempt. Their observation timestamp can
    # identify it; otherwise every matching attempt must prove non-success.
    dated_candidates = [
        run
        for run in candidates
        if family.get("resolved_at") is not None
        and run.get("observed_at") == family["resolved_at"]
    ]
    if dated_candidates:
        candidates = dated_candidates
    if not candidates:
        return False
    for run in candidates:
        snapshot = {
            key: value
            for key, value in run.items()
            if key not in {"observed_at", "payload_fingerprint"}
        }
        if run.get("status") != "completed" or _digest(snapshot) != run["payload_fingerprint"]:
            return False
        raw_jobs = run.get("jobs")
        if not isinstance(raw_jobs, list):
            return False
        jobs = [
            job
            for job in raw_jobs
            if isinstance(job, dict) and job.get("name") == family.get("job")
        ]
        if not jobs or any(job.get("conclusion") not in {"skipped", "neutral"} for job in jobs):
            return False
    return True


def _reconcile_non_success_resolutions(ledger: dict[str, Any]) -> bool:
    """Restore proven false projections without rewriting run or failure history."""
    changed = False
    runs = cast(dict[str, Any], ledger["runs"])
    for family in cast(dict[str, Any], ledger["families"]).values():
        if family["status"] != "resolved" or not _non_success_resolution_proven(family, runs):
            continue
        family["status"] = "repaired" if "repair" in family else "open"
        for key in ("resolved_by_run", "resolved_by_sha", "resolved_at", "resolved_by_attempt"):
            family.pop(key, None)
        changed = True
    return changed


def observe_payload(
    ledger: dict[str, Any], payload: dict[str, Any], *, observed_at: str
) -> str:
    """Record every terminal failure or resolve it with later hosted success."""
    validate_ledger(ledger)
    snapshot = _snapshot(payload)
    attempt = int(snapshot.get("attempt", 1))
    run_key = str(snapshot["run_id"]) if attempt == 1 else f"{snapshot['run_id']}:{attempt}"
    fingerprint = _digest(snapshot)
    runs = cast(dict[str, Any], ledger["runs"])
    existing = runs.get(run_key)
    if existing is not None:
        detail = ledger.get("terminal_details", {}).get(run_key)
        accepted_fingerprint = detail["payload_fingerprint"] if detail else existing["payload_fingerprint"]
        if accepted_fingerprint == fingerprint:
            return "reconciled" if _reconcile_non_success_resolutions(ledger) else "unchanged"
        original = _receipt_snapshot(existing)
        accepted = {**original, "jobs": detail["jobs"]} if detail else original
        if _is_terminal_detail_enrichment(accepted, snapshot):
            if _digest(original) != existing["payload_fingerprint"]:
                raise LedgerError(f"terminal detail {run_key} original receipt fingerprint mismatch")
            ledger.setdefault("terminal_details", {})[run_key] = {
                "schema_version": 1,
                "original_payload_fingerprint": existing["payload_fingerprint"],
                "payload_fingerprint": fingerprint,
                "observed_at": observed_at,
                "jobs": snapshot["jobs"],
            }
            validate_ledger(ledger)
            _reconcile_non_success_resolutions(ledger)
            return "enriched"
        raise LedgerError(f"immutable terminal run {run_key} changed after observation")
    runs[run_key] = {
        **snapshot,
        "observed_at": observed_at,
        "payload_fingerprint": fingerprint,
    }
    _reconcile_non_success_resolutions(ledger)

    families = cast(dict[str, Any], ledger["families"])
    for job in snapshot["jobs"]:
        if job["conclusion"] in NON_FAILURE_CONCLUSIONS:
            if job["conclusion"] != "success":
                continue
            for family in families.values():
                if (
                    family["branch"] == snapshot["branch"]
                    and family["workflow"] == snapshot["workflow"]
                    and family["job"] == job["name"]
                    and family["status"] != "resolved"
                ):
                    family["status"] = "resolved"
                    family["resolved_by_run"] = snapshot["run_id"]
                    family["resolved_by_sha"] = snapshot["sha"]
                    family["resolved_at"] = observed_at
                    if attempt > 1:
                        family["resolved_by_attempt"] = attempt
            continue
        family_id = _family_id(snapshot, job)
        occurrence = {
            "run_id": snapshot["run_id"],
            "sha": snapshot["sha"],
            "observed_at": observed_at,
        }
        if attempt > 1:
            occurrence["attempt"] = attempt
        family = families.get(family_id)
        if family is None:
            families[family_id] = {
                "family_id": family_id,
                "branch": snapshot["branch"],
                "workflow": snapshot["workflow"],
                "job": job["name"],
                "conclusion": job["conclusion"],
                "failed_steps": job["failed_steps"],
                "status": "open",
                "occurrences": [occurrence],
            }
            continue
        family["occurrences"].append(occurrence)
        family["status"] = "open"
        for stale_key in (
            "repair", "resolved_by_run", "resolved_by_sha", "resolved_at", "resolved_by_attempt"
        ):
            family.pop(stale_key, None)
    validate_ledger(ledger)
    return "recorded"


def _latest_failed_sha(family: dict[str, Any]) -> str:
    occurrence = _require_mapping(family["occurrences"][-1], "latest occurrence")
    return str(occurrence["sha"])


def guard_rerun(
    ledger: dict[str, Any],
    run_id: int,
    *,
    allow_unchanged: bool = False,
    reason: str = "",
) -> list[str]:
    """Return every reason an existing terminal run must not be rerun."""
    validate_ledger(ledger)
    run_candidates = [
        record
        for record in cast(dict[str, Any], ledger["runs"]).values()
        if record.get("run_id") == run_id
    ]
    run = max(run_candidates, key=_attempt) if run_candidates else None
    if run is None:
        return [f"run {run_id} has not been observed into the failure ledger"]
    if run["conclusion"] in NON_FAILURE_CONCLUSIONS:
        return [f"run {run_id} already succeeded; rerun would waste capacity"]
    if not allow_unchanged:
        return [
            f"run {run_id} is an unchanged failed run at {run['sha']}; "
            "land a repair commit instead"
        ]
    if not reason.strip():
        return ["unchanged rerun override requires a non-empty reason"]
    return []


def guard_runner_acquisition_rerun(
    payload: dict[str, Any], annotations_by_job: dict[int, list[str]]
) -> list[str]:
    """Permit one retry only when every failure is hosted-runner acquisition."""
    snapshot = _snapshot(payload)
    attempt = _attempt(payload)
    blockers: list[str] = []
    if snapshot["conclusion"] in NON_FAILURE_CONCLUSIONS:
        blockers.append("run already succeeded; infrastructure recovery is unnecessary")
    if attempt != 1:
        blockers.append(
            f"runner-acquisition recovery limit reached at attempt {attempt}; "
            "only attempt 1 may be retried automatically"
        )

    raw_jobs = cast(list[dict[str, Any]], payload["jobs"])
    failed_jobs = [
        job
        for job in raw_jobs
        if str(job.get("conclusion") or "").lower() not in NON_FAILURE_CONCLUSIONS
    ]
    if not failed_jobs:
        blockers.append("run has no failed or cancelled jobs to recover")
    for job in failed_jobs:
        name = str(job.get("name") or "<unnamed>")
        failed_steps = [
            str(step.get("name") or "<unnamed>")
            for step in job.get("steps", [])
            if isinstance(step, dict)
            and str(step.get("conclusion") or "").lower()
            not in NON_FAILURE_CONCLUSIONS | {""}
        ]
        if failed_steps:
            blockers.append(
                f"job {name} executed failing steps: {', '.join(failed_steps)}"
            )
            continue
        try:
            job_id = _run_id(job.get("databaseId"))
        except LedgerError:
            blockers.append(f"job {name} has no valid check-run identity")
            continue
        messages = annotations_by_job.get(job_id, [])
        if not any(RUNNER_ACQUISITION_MESSAGE in message for message in messages):
            blockers.append(f"job {name} has no runner-acquisition annotation")
    return blockers


def validate_repair_selection(
    ledger: dict[str, Any],
    family_ids: Sequence[str],
    *,
    repair_sha: str,
    evidence: str,
    is_ancestor: AncestorCheck,
) -> list[dict[str, Any]]:
    """Admit every selected family without modifying any receipt or ledger."""
    validate_ledger(ledger)
    if FULL_SHA.fullmatch(repair_sha) is None:
        raise LedgerError("repair SHA must be a full lowercase 40-character hash")
    if not evidence.strip():
        raise LedgerError("repair evidence must be non-empty")
    if not family_ids:
        raise LedgerError("at least one failure family must be selected")
    families = cast(dict[str, Any], ledger["families"])
    selected: list[dict[str, Any]] = []
    errors: list[str] = []
    for family_id in family_ids:
        family = families.get(family_id)
        if family is None:
            errors.append(f"unknown family {family_id}")
            continue
        if family["status"] == "resolved":
            errors.append(f"family {family_id} is already resolved by hosted CI")
            continue
        failed_sha = _latest_failed_sha(family)
        if failed_sha == repair_sha:
            errors.append(f"family {family_id} repair uses the same SHA as its failure")
            continue
        if not is_ancestor(failed_sha, repair_sha):
            errors.append(f"family {family_id} repair SHA does not descend from failure SHA")
            continue
        selected.append(family)
    if errors:
        raise LedgerError("; ".join(errors))
    return selected


def record_repair(
    ledger: dict[str, Any],
    family_ids: Sequence[str],
    *,
    repair_sha: str,
    evidence: str,
    recorded_at: str,
    is_ancestor: AncestorCheck,
) -> None:
    """Attach one verified local-evidence receipt to selected open families."""
    selected = validate_repair_selection(
        ledger, family_ids, repair_sha=repair_sha, evidence=evidence, is_ancestor=is_ancestor,
    )
    for family in selected:
        family["status"] = "repaired"
        family["repair"] = {
            "sha": repair_sha,
            "evidence": evidence,
            "recorded_at": recorded_at,
        }
    validate_ledger(ledger)


def build_repair_guidance(
    ledger: dict[str, Any],
    *,
    ledger_path: Path,
    branch: str,
    repair_sha: str,
    checkout_clean: bool,
) -> list[RepairGuidance]:
    """Require an explicit evidence owner for each independently selected family."""
    validate_ledger(ledger)
    families = cast(dict[str, Any], ledger["families"])
    scoped = {key: family for key, family in families.items() if family.get("branch") == branch}
    selected: list[tuple[str | None, dict[str, Any] | None]] = [
        (key, family) for key, family in sorted(scoped.items()) if family["status"] == "open"
    ]
    if not selected:
        if any(family["status"] == "repaired" for family in scoped.values()):
            return []
        selected = [(None, None)]
    guidance: list[RepairGuidance] = []
    for family_id, family in selected:
        failed_sha = _latest_failed_sha(family) if family is not None else None
        candidate = (
            repair_sha if checkout_clean is True and FULL_SHA.fullmatch(repair_sha)
            and repair_sha != failed_sha else None
        )
        required = ["CI_REPAIR_EVIDENCE_TARGET", "CI_REPAIR_EVIDENCE_VARS"]
        if family_id is None:
            required.insert(0, "CI_FAILURE_FAMILIES")
        if candidate is None:
            required.append("CI_REPAIR_SHA")
        guidance.append({
            "family_ids": [family_id] if family_id is not None else [],
            "failed_sha": failed_sha,
            "candidate_sha": candidate,
            "argv": None,
            "argv_template": [
                "make", "ci-failure-repair", f"CI_FAILURE_LEDGER={ledger_path}",
                f"CI_FAILURE_FAMILIES={family_id or '<explicit-family-id>'}",
                "CI_REPAIR_ALL_OPEN=0",
                f"CI_REPAIR_SHA={candidate or '<clean-descendant-repair-sha>'}",
                "CI_REPAIR_EVIDENCE_TARGET=<explicit-evidence-target>",
                "CI_REPAIR_EVIDENCE_VARS=<explicit-evidence-variables>",
                "CI_FAILURE_VALIDATE_ONLY=0",
            ],
            "required_parameters": required,
            "reason": (
                "Select evidence that covers only the explicit family selection. Run its first evidence execution "
                "through ci-failure-repair; the owner verifies the exact clean descendant checkout before execution. "
                "No family-to-target coverage mapping has been established."
            ),
        })
    return guidance


def guard_push(
    ledger: dict[str, Any],
    head_sha: str,
    *,
    is_ancestor: AncestorCheck,
    branch: str = "development",
) -> list[str]:
    """Return all unresolved or non-ancestral repair blockers for one push."""
    validate_ledger(ledger)
    if FULL_SHA.fullmatch(head_sha) is None:
        return ["push HEAD must be a full lowercase 40-character hash"]
    blockers: list[str] = []
    families = cast(dict[str, Any], ledger["families"])
    for family_id, family in sorted(families.items()):
        if family["branch"] != branch or family["status"] == "resolved":
            continue
        failed_sha = _latest_failed_sha(family)
        label = f"{family['workflow']}/{family['job']} family={family_id}"
        if head_sha == failed_sha:
            blockers.append(f"{label}: push is the same SHA as the recorded failure")
        elif family["status"] == "open":
            blockers.append(f"{label}: open failure has no verified repair receipt")
        else:
            repair = _require_mapping(family.get("repair"), f"{label} repair")
            repair_sha = str(repair.get("sha") or "")
            if not is_ancestor(repair_sha, head_sha):
                blockers.append(
                    f"{label}: repair {repair_sha} is not an ancestor of push HEAD"
                )
    return blockers


def status_lines(ledger: dict[str, Any]) -> list[str]:
    """Render every family and a count summary without hiding siblings."""
    validate_ledger(ledger)
    families = cast(dict[str, Any], ledger["families"])
    counts = {status: 0 for status in sorted(FAILURE_STATUSES)}
    lines: list[str] = []
    for family_id, family in sorted(families.items()):
        status = str(family["status"])
        counts[status] += 1
        steps = ", ".join(family["failed_steps"])
        lines.append(
            f"{status.upper()} {family['branch']} {family['workflow']}/{family['job']} "
            f"family={family_id} steps={steps} occurrences={len(family['occurrences'])}"
        )
    lines.append(
        "CI_FAILURE_LEDGER_SUMMARY "
        f"open={counts['open']} repaired={counts['repaired']} "
        f"resolved={counts['resolved']} runs={len(ledger['runs'])}"
    )
    return lines


def _paginated_json(
    endpoint: str, *, runner: Runner, failure_context: str, json_context: str, empty_json: str = "",
) -> object:
    """Decode one bounded native paginated CLI response without hiding partial failure."""
    result = runner(
        ["gh", "api", endpoint, "--paginate", "--slurp"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "gh api failed").strip()
        raise LedgerError(f"{failure_context} failed: {detail}")
    try:
        return cast(object, json.loads(result.stdout or empty_json))
    except json.JSONDecodeError as exc:
        raise LedgerError(f"{json_context} returned invalid JSON: {exc}") from exc


def _inventory_integer(value: object, label: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise LedgerError(f"{label} must be an integer >= {minimum}")
    return value


def _inventory_string(value: object, label: str, *, nullable: bool = False) -> str:
    if value is None and nullable:
        return ""
    if not isinstance(value, str) or (not nullable and not value.strip()):
        raise LedgerError(f"{label} must be a string")
    return value


def _inventory_time(value: object, label: str) -> str:
    """Match GitHub CLI's Go time.Time JSON, including null and nanoseconds."""
    if value is None:
        return "0001-01-01T00:00:00Z"
    if not isinstance(value, str) or re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})", value
    ) is None:
        raise LedgerError(f"{label} must be an RFC3339 timestamp or null")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise LedgerError(f"{label} must be an RFC3339 timestamp or null") from exc
    normalized = re.sub(
        r"\.(\d+)(?=Z|[+-])",
        lambda match: f".{match[1].rstrip('0')}" if match[1].rstrip("0") else "",
        value,
    )
    if not normalized.endswith("Z") and parsed.utcoffset() == timedelta(0):
        normalized = f"{normalized[:-6]}Z"
    return normalized


def _inventory_job(
    raw: object, payload: dict[str, Any], repository: str
) -> dict[str, Any]:
    """Validate native job ownership and project the existing run-view schema."""
    job = _require_mapping(raw, "GitHub job")
    required = {
        "id", "run_id", "run_url", "head_sha", "url", "html_url", "name", "status",
        "conclusion", "started_at", "completed_at",
    }
    if not required.issubset(job):
        raise LedgerError("GitHub job is missing required fields")
    job_id = _inventory_integer(job["id"], "GitHub job ID")
    run_id = payload["databaseId"]
    if _inventory_integer(job["run_id"], "GitHub job run ID") != run_id:
        raise LedgerError("GitHub job belongs to a different run identity")
    if "run_attempt" in job and (
        _inventory_integer(job["run_attempt"], "GitHub job run attempt") != payload["attempt"]
    ):
        raise LedgerError("GitHub job belongs to a different run attempt")
    run_url = f"https://api.github.com/repos/{repository}/actions/runs/{run_id}"
    job_url = f"https://api.github.com/repos/{repository}/actions/jobs/{job_id}"
    if (_inventory_string(job["run_url"], "GitHub job run URL").lower() != run_url.lower()
            or _inventory_string(job["url"], "GitHub job API URL").lower() != job_url.lower()
            or job["head_sha"] != payload["headSha"]):
        raise LedgerError("GitHub job repository, run, or head SHA identity mismatch")
    raw_steps = job.get("steps")
    if raw_steps is None:
        raw_steps = []
    if not isinstance(raw_steps, list):
        raise LedgerError("GitHub job steps must be an array or null")
    steps: list[dict[str, Any]] = []
    for index, raw_step in enumerate(raw_steps):
        step = _require_mapping(raw_step, f"GitHub job step {index}")
        required_step = {"name", "status", "conclusion", "number", "started_at", "completed_at"}
        if not required_step.issubset(step):
            raise LedgerError("GitHub job step is missing required fields")
        steps.append({
            "name": _inventory_string(step["name"], "GitHub step name"),
            "status": _inventory_string(step["status"], "GitHub step status"),
            "conclusion": _inventory_string(step["conclusion"], "GitHub step conclusion", nullable=True),
            "number": _inventory_integer(step["number"], "GitHub step number"),
            "startedAt": _inventory_time(step["started_at"], "GitHub step start"),
            "completedAt": _inventory_time(step["completed_at"], "GitHub step completion"),
        })
    return {
        "databaseId": job_id,
        "name": _inventory_string(job["name"], "GitHub job name"),
        "status": _inventory_string(job["status"], "GitHub job status"),
        "conclusion": _inventory_string(job["conclusion"], "GitHub job conclusion", nullable=True),
        "startedAt": _inventory_time(job["started_at"], "GitHub job start"),
        "completedAt": _inventory_time(job["completed_at"], "GitHub job completion"),
        "url": _inventory_string(job["html_url"], "GitHub job URL"),
        "steps": steps,
    }


def fetch_run(run_id: int, repository: str, *, runner: Runner = subprocess.run) -> dict[str, Any]:
    """Fetch latest run metadata and its complete native, attempt-pinned inventory."""
    if REPOSITORY.fullmatch(repository) is None:
        raise LedgerError("repository must be owner/name")
    _inventory_integer(run_id, "run ID")
    result = runner(
        [
            "gh",
            "run",
            "view",
            str(run_id),
            "--repo",
            repository,
            "--json",
            RUN_METADATA_FIELDS,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "gh run view failed").strip()
        raise LedgerError(f"GitHub run lookup failed: {detail}")
    try:
        decoded = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise LedgerError(f"GitHub run lookup returned invalid JSON: {exc}") from exc
    payload = _require_mapping(decoded, "GitHub run")
    if set(payload) != set(RUN_METADATA_FIELDS.split(",")):
        raise LedgerError("GitHub run metadata does not match the requested fields")
    if _inventory_integer(payload["databaseId"], "GitHub run ID") != run_id:
        raise LedgerError("GitHub returned a different run identity")
    attempt = _inventory_integer(payload["attempt"], "GitHub run attempt")
    for field in ("headSha", "headBranch", "status", "url", "workflowName"):
        _inventory_string(payload[field], f"GitHub run {field}")
    _inventory_string(payload["conclusion"], "GitHub run conclusion", nullable=True)
    run_url = f"https://github.com/{repository}/actions/runs/{run_id}"
    if payload["url"].lower() != run_url.lower() or FULL_SHA.fullmatch(payload["headSha"]) is None:
        raise LedgerError("GitHub run repository or head SHA identity mismatch")
    pages = _paginated_json(
        f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=50",
        runner=runner, failure_context="GitHub job inventory lookup", json_context="GitHub job inventory",
    )
    if not isinstance(pages, list) or not pages:
        raise LedgerError("GitHub job inventory must contain an array of pages")
    jobs: list[dict[str, Any]] = []
    seen: set[int] = set()
    total_count: int | None = None
    for index, raw_page in enumerate(pages):
        page = _require_mapping(raw_page, f"GitHub job inventory page {index}")
        count = _inventory_integer(page.get("total_count"), "GitHub job total_count", minimum=0)
        if total_count is not None and count != total_count:
            raise LedgerError("GitHub job inventory total_count changed during pagination")
        total_count = count
        raw_jobs = page.get("jobs")
        if not isinstance(raw_jobs, list):
            raise LedgerError("GitHub job inventory page jobs must be an array")
        for raw_job in raw_jobs:
            job = _inventory_job(raw_job, payload, repository)
            if job["databaseId"] in seen:
                raise LedgerError("GitHub job inventory contains a duplicate job identity")
            seen.add(job["databaseId"])
            jobs.append(job)
    if len(jobs) != total_count:
        raise LedgerError("GitHub job inventory is incomplete or contradicts total_count")
    payload["jobs"] = jobs
    return payload


def fetch_job_annotations(
    job_id: int, repository: str, *, runner: Runner = subprocess.run
) -> list[str]:
    """Fetch every annotation message for one check run without truncation."""
    if job_id < 1:
        raise LedgerError("check-run ID must be a positive integer")
    if REPOSITORY.fullmatch(repository) is None:
        raise LedgerError("repository must be owner/name")
    decoded = _paginated_json(
        f"repos/{repository}/check-runs/{job_id}/annotations", runner=runner,
        failure_context="check-run annotation lookup", json_context="check-run annotations", empty_json="[]",
    )
    if not isinstance(decoded, list):
        raise LedgerError("check-run annotations must be an array")
    raw_annotations: list[object] = []
    for page in decoded:
        if isinstance(page, list):
            raw_annotations.extend(page)
        else:
            raw_annotations.append(page)
    messages: list[str] = []
    for index, raw in enumerate(raw_annotations):
        annotation = _require_mapping(raw, f"check-run annotation {index}")
        message = annotation.get("message")
        if isinstance(message, str) and message.strip():
            messages.append(message.strip())
    return messages


def fetch_runner_acquisition_annotations(
    payload: dict[str, Any],
    repository: str,
    *,
    fetcher: Callable[[int, str], list[str]] = fetch_job_annotations,
) -> dict[int, list[str]]:
    """Fetch annotations for every non-successful job in a terminal attempt."""
    raw_jobs = payload.get("jobs")
    if not isinstance(raw_jobs, list):
        raise LedgerError("GitHub run jobs must be an array")
    annotations: dict[int, list[str]] = {}
    for index, raw_job in enumerate(raw_jobs):
        job = _require_mapping(raw_job, f"job {index}")
        if str(job.get("conclusion") or "").lower() in NON_FAILURE_CONCLUSIONS:
            continue
        job_id = _run_id(job.get("databaseId"))
        annotations[job_id] = fetcher(job_id, repository)
    return annotations


def fetch_run_index(
    sha: str, repository: str, *, runner: Runner = subprocess.run
) -> list[dict[str, Any]]:
    """Fetch every workflow summary for one exact commit SHA."""
    if FULL_SHA.fullmatch(sha) is None:
        raise LedgerError("index SHA must be a full lowercase 40-character hash")
    if REPOSITORY.fullmatch(repository) is None:
        raise LedgerError("repository must be owner/name")
    fields = (
        "databaseId,headSha,headBranch,status,conclusion,url,workflowName,event,createdAt"
    )
    result = runner(
        [
            "gh",
            "run",
            "list",
            "--commit",
            sha,
            "--limit",
            "100",
            "--repo",
            repository,
            "--json",
            fields,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "gh run list failed").strip()
        raise LedgerError(f"GitHub run index lookup failed: {detail}")
    try:
        decoded = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise LedgerError(f"GitHub run index returned invalid JSON: {exc}") from exc
    if not isinstance(decoded, list):
        raise LedgerError("GitHub run index must be an array")
    if len(decoded) >= 100:
        raise LedgerError("GitHub run index reached its limit; evidence may be incomplete")
    if any(not isinstance(item, dict) for item in decoded):
        raise LedgerError("GitHub run index entries must be objects")
    return cast(list[dict[str, Any]], decoded)


def _exact_sha_terminal_runs(
    index: Sequence[dict[str, Any]],
    *,
    sha: str,
    branch: str,
) -> list[int]:
    """Select the newest terminal push attempt from every exact-SHA workflow."""
    newest: dict[str, dict[str, Any]] = {}
    for run in index:
        workflow = str(run.get("workflowName") or "").strip()
        if (
            str(run.get("headSha") or "") != sha
            or str(run.get("headBranch") or "") != branch
            or str(run.get("event") or "") != "push"
            or not workflow
        ):
            continue
        current = newest.get(workflow)
        key = (str(run.get("createdAt") or ""), _run_id(run.get("databaseId")))
        if current is None:
            newest[workflow] = run
            continue
        current_key = (
            str(current.get("createdAt") or ""),
            _run_id(current.get("databaseId")),
        )
        if key > current_key:
            newest[workflow] = run

    return [
        _run_id(newest[workflow].get("databaseId"))
        for workflow in sorted(newest)
        if str(newest[workflow].get("status") or "").lower() == "completed"
    ]


def observe_exact_sha(
    ledger: dict[str, Any],
    index: Sequence[dict[str, Any]],
    *,
    sha: str,
    branch: str,
    repository: str,
    observed_at: str,
    fetcher: Callable[[int, str], dict[str, Any]] = fetch_run,
) -> tuple[list[tuple[int, str]], list[str]]:
    """Observe newest terminal runs for all workflows without short-circuiting."""
    validate_ledger(ledger)
    results: list[tuple[int, str]] = []
    errors: list[str] = []
    for run_id in _exact_sha_terminal_runs(index, sha=sha, branch=branch):
        try:
            payload = fetcher(run_id, repository)
            if str(payload.get("headSha") or "").lower() != sha:
                raise LedgerError("detail SHA differs from indexed exact SHA")
            result = observe_payload(ledger, payload, observed_at=observed_at)
            results.append((run_id, result))
        except (LedgerError, OSError) as exc:
            errors.append(f"run {run_id}: {exc}")
    return results, errors


def remote_head(
    branch: str, remote: str, *, runner: Runner = subprocess.run
) -> str:
    """Resolve a full branch head for automatic exact-SHA observation."""
    if not branch.strip() or not remote.strip():
        raise LedgerError("branch and remote must be non-empty")
    result = runner(
        ["git", "ls-remote", remote, f"refs/heads/{branch}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "git ls-remote failed").strip()
        raise LedgerError(f"remote head lookup failed: {detail}")
    first = next((line for line in result.stdout.splitlines() if line.strip()), "")
    sha = first.split(maxsplit=1)[0].lower() if first else ""
    if FULL_SHA.fullmatch(sha) is None:
        raise LedgerError(f"no exact remote head for {remote}/{branch}")
    return sha


def _git_is_ancestor(old_sha: str, new_sha: str, *, runner: Runner = subprocess.run) -> bool:
    result = runner(
        ["git", "merge-base", "--is-ancestor", old_sha, new_sha],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    return result.returncode == 0


def _git_head(*, runner: Runner = subprocess.run) -> str:
    result = runner(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    sha = result.stdout.strip().lower()
    if result.returncode != 0 or FULL_SHA.fullmatch(sha) is None:
        raise LedgerError("could not resolve an exact local HEAD")
    return sha


def _validation_payload(run_id: int) -> dict[str, Any]:
    return {
        "databaseId": run_id,
        "headSha": "0" * 40,
        "headBranch": "development",
        "status": "completed",
        "conclusion": "success",
        "workflowName": "Build and Release",
        "url": "",
        "jobs": [
            {
                "name": "contract",
                "status": "completed",
                "conclusion": "success",
                "steps": [],
            }
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    observe = commands.add_parser("observe")
    observe.add_argument("--run", type=int, required=True)
    observe.add_argument("--repo", default=DEFAULT_REPOSITORY)
    observe.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    observe.add_argument("--validate-only", action="store_true")

    observe_sha = commands.add_parser("observe-sha")
    observe_sha.add_argument("--sha", default="")
    observe_sha.add_argument("--branch", default="development")
    observe_sha.add_argument("--remote", default="sandboxcom")
    observe_sha.add_argument("--repo", default=DEFAULT_REPOSITORY)
    observe_sha.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    observe_sha.add_argument("--validate-only", action="store_true")

    status = commands.add_parser("status")
    status.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    status.add_argument("--validate-only", action="store_true")

    rerun = commands.add_parser("guard-rerun")
    rerun.add_argument("--run", type=int, required=True)
    rerun.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    rerun.add_argument("--allow-unchanged", action="store_true")
    rerun.add_argument("--reason", default="")
    rerun.add_argument("--validate-only", action="store_true")

    infra_rerun = commands.add_parser("guard-runner-acquisition-rerun")
    infra_rerun.add_argument("--run", type=int, required=True)
    infra_rerun.add_argument("--repo", default=DEFAULT_REPOSITORY)
    infra_rerun.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    infra_rerun.add_argument("--validate-only", action="store_true")

    repair = commands.add_parser("repair")
    repair.add_argument("--family", action="append", default=[])
    repair.add_argument("--all-open", action="store_true")
    repair.add_argument("--sha", required=True)
    repair.add_argument("--evidence-target", required=True)
    repair.add_argument("--evidence-var", action="append", default=[])
    repair.add_argument("--evidence-vars-from-env", action="store_true")
    repair.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    repair.add_argument("--validate-only", action="store_true")

    push = commands.add_parser("guard-push")
    push.add_argument("--head", default="")
    push.add_argument("--branch", default="development")
    push.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    push.add_argument("--validate-only", action="store_true")
    return parser


def _print_blockers(blockers: Sequence[str], operation: str) -> int:
    if not blockers:
        print(f"CI_FAILURE_{operation}_GUARD_PASS")
        return 0
    print(f"CI_FAILURE_{operation}_GUARD_BLOCKED count={len(blockers)}", file=sys.stderr)
    for blocker in blockers:
        print(f"  - {blocker}", file=sys.stderr)
    return 1


def _command_observe(args: argparse.Namespace) -> int:
    payload = _validation_payload(args.run) if args.validate_only else fetch_run(args.run, args.repo)
    if args.validate_only:
        ledger = new_ledger()
        result = observe_payload(ledger, payload, observed_at=_now())
    else:
        with _locked_ledger(args.ledger) as ledger:
            result = observe_payload(ledger, payload, observed_at=_now())
            write_ledger(args.ledger, ledger)
    print(f"CI_FAILURE_LEDGER_OBSERVE run={args.run} result={result}")
    print("\n".join(status_lines(ledger)))
    return 0


def _command_status(args: argparse.Namespace) -> int:
    if args.validate_only:
        print("CI_FAILURE_LEDGER_STATUS_VALIDATE_ONLY_PASS")
        return 0
    existed = args.ledger.exists()
    ledger = read_ledger(args.ledger)
    if not existed:
        print("CI_FAILURE_LEDGER_INACTIVE no terminal runs have been observed")
    print("\n".join(status_lines(ledger)))
    return 1 if any(f["status"] == "open" for f in ledger["families"].values()) else 0


def _command_observe_sha(args: argparse.Namespace) -> int:
    if args.validate_only:
        print("CI_FAILURE_LEDGER_OBSERVE_SHA_VALIDATE_ONLY_PASS")
        return 0
    sha = args.sha.lower() if args.sha else remote_head(args.branch, args.remote)
    index = fetch_run_index(sha, args.repo)
    fetched: dict[int, dict[str, Any] | str] = {}
    for run_id in _exact_sha_terminal_runs(index, sha=sha, branch=args.branch):
        try:
            fetched[run_id] = fetch_run(run_id, args.repo)
        except (LedgerError, OSError) as exc:
            fetched[run_id] = str(exc)

    def prepared_run(run_id: int, _repository: str) -> dict[str, Any]:
        payload = fetched[run_id]
        if isinstance(payload, str):
            raise LedgerError(payload)
        return payload

    with _locked_ledger(args.ledger) as ledger:
        results, errors = observe_exact_sha(
            ledger,
            index,
            sha=sha,
            branch=args.branch,
            repository=args.repo,
            observed_at=_now(),
            fetcher=prepared_run,
        )
        if results:
            write_ledger(args.ledger, ledger)
    for run_id, result in results:
        print(f"CI_FAILURE_LEDGER_OBSERVE_SHA run={run_id} result={result}")
    print("\n".join(status_lines(ledger)))
    if errors:
        print(f"CI_FAILURE_LEDGER_OBSERVE_SHA_ERRORS count={len(errors)}", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 2
    return 0


def _command_rerun(args: argparse.Namespace) -> int:
    if args.validate_only:
        print("CI_FAILURE_RERUN_GUARD_VALIDATE_ONLY_PASS")
        return 0
    ledger = read_ledger(args.ledger)
    return _print_blockers(
        guard_rerun(
            ledger,
            args.run,
            allow_unchanged=args.allow_unchanged,
            reason=args.reason,
        ),
        "RERUN",
    )


def _command_runner_acquisition_rerun(args: argparse.Namespace) -> int:
    if args.validate_only:
        print("CI_FAILURE_RUNNER_ACQUISITION_RERUN_GUARD_VALIDATE_ONLY_PASS")
        return 0
    payload = fetch_run(args.run, args.repo)
    attempt = _attempt(payload)
    run_key = str(args.run) if attempt == 1 else f"{args.run}:{attempt}"
    ledger = read_ledger(args.ledger)
    if run_key not in cast(dict[str, Any], ledger["runs"]):
        raise LedgerError(f"run {run_key} must be observed before recovery")
    annotations = fetch_runner_acquisition_annotations(payload, args.repo)
    return _print_blockers(
        guard_runner_acquisition_rerun(payload, annotations),
        "RUNNER_ACQUISITION_RERUN",
    )


def _require_repair_checkout(sha: str) -> None:
    """Bind evidence to the native owner's current clean, exact checkout."""
    try:
        result = subprocess.run(
            ["make", "--no-print-directory", "worktree-state"], cwd=ROOT,
            check=False, capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LedgerError("could not verify the native repair checkout") from exc
    try:
        state = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise LedgerError("repair checkout state is not valid native JSON") from exc
    if (result.returncode != 0 or not isinstance(state, dict)
            or state.get("head") != sha or state.get("is_clean") is not True
            or type(state.get("dirty_count")) is not int or state["dirty_count"] != 0
            or state.get("path") != str(ROOT)):
        raise LedgerError("repair evidence requires the claimed SHA in its clean native checkout")


def _repair_evidence_environment(inherited: dict[str, str]) -> dict[str, str]:
    """Detach evidence from Make recursion and ambient repair-owner inputs."""
    child = inherited.copy()
    for name in (*MAKE_RECURSION_ENV_VARS, *REPAIR_OWNER_ENV_VARS):
        child.pop(name, None)
    return child


def _command_repair(args: argparse.Namespace) -> int:
    if FULL_SHA.fullmatch(args.sha) is None:
        raise LedgerError("repair SHA must be a full lowercase 40-character hash")
    if MAKE_TARGET.fullmatch(args.evidence_target) is None:
        raise LedgerError("evidence target must be one plain make target")
    evidence_vars = list(args.evidence_var)
    if args.evidence_vars_from_env:
        try:
            evidence_vars.extend(shlex.split(os.environ.get("CI_REPAIR_EVIDENCE_VARS", "")))
        except ValueError as exc:
            raise LedgerError("CI_REPAIR_EVIDENCE_VARS must use valid shell quoting") from exc
    if any(MAKE_VARIABLE.fullmatch(value) is None for value in evidence_vars):
        raise LedgerError("each evidence variable must use UPPER_CASE=value")
    if args.validate_only:
        print("CI_FAILURE_REPAIR_VALIDATE_ONLY_PASS")
        return 0
    _require_repair_checkout(args.sha)
    ledger = read_ledger(args.ledger)
    families = cast(dict[str, Any], ledger["families"])
    selected = list(args.family)
    if args.all_open:
        selected.extend(
            family_id
            for family_id, family in families.items()
            if family["status"] == "open"
        )
    selected = sorted(set(selected))
    command = ["make", args.evidence_target, *evidence_vars]
    validate_repair_selection(
        ledger, selected, repair_sha=args.sha, evidence=shlex.join(command), is_ancestor=_git_is_ancestor,
    )
    selected_fingerprints = {
        family_id: _digest(families.get(family_id)) for family_id in selected
    }
    print(f"CI_FAILURE_REPAIR_EVIDENCE command={shlex.join(command)}", flush=True)
    result = subprocess.run(
        command, cwd=ROOT, check=False, env=_repair_evidence_environment(dict(os.environ)),
    )
    if result.returncode != 0:
        raise LedgerError(f"repair evidence failed with exit {result.returncode}")
    _require_repair_checkout(args.sha)
    record_repair(
        ledger,
        selected,
        repair_sha=args.sha,
        evidence=shlex.join(command),
        recorded_at=_now(),
        is_ancestor=_git_is_ancestor,
    )
    with _locked_ledger(args.ledger) as current:
        current_families = cast(dict[str, Any], current["families"])
        for family_id in selected:
            if _digest(current_families.get(family_id)) != selected_fingerprints[family_id]:
                raise LedgerError(
                    f"family {family_id} changed while repair evidence ran; rerun its evidence"
                )
        current_families.update({family_id: families[family_id] for family_id in selected})
        write_ledger(args.ledger, current)
    print(f"CI_FAILURE_REPAIR_RECORDED families={len(selected)} sha={args.sha}")
    return 0


def _command_push(args: argparse.Namespace) -> int:
    if args.validate_only:
        print("CI_FAILURE_PUSH_GUARD_VALIDATE_ONLY_PASS")
        return 0
    if not args.ledger.exists():
        print("CI_FAILURE_PUSH_GUARD_INACTIVE no terminal runs have been observed")
        return 0
    ledger = read_ledger(args.ledger)
    head = args.head.lower() if args.head else _git_head()
    return _print_blockers(
        guard_push(ledger, head, branch=args.branch, is_ancestor=_git_is_ancestor),
        "PUSH",
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run a validated ledger command and report failures without hiding evidence."""
    args = _parser().parse_args(argv)
    try:
        handlers = {
            "observe": _command_observe,
            "observe-sha": _command_observe_sha,
            "status": _command_status,
            "guard-rerun": _command_rerun,
            "guard-runner-acquisition-rerun": _command_runner_acquisition_rerun,
            "repair": _command_repair,
            "guard-push": _command_push,
        }
        return handlers[args.command](args)
    except (LedgerError, OSError) as exc:
        print(f"ci-failure-ledger: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
