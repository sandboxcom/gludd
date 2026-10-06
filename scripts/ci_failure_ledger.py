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
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LEDGER = ROOT / ".gludd" / "ci-failure-ledger.json"
DEFAULT_REPOSITORY = "sandboxcom/gludd"
LEDGER_VERSION = 1
MAX_LEDGER_BYTES = 2 * 1024 * 1024
FULL_SHA = re.compile(r"[0-9a-f]{40}\Z")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
MAKE_TARGET = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
MAKE_VARIABLE = re.compile(r"[A-Z][A-Z0-9_]*=.*\Z")
SUCCESS_CONCLUSIONS = {"success", "skipped", "neutral"}
FAILURE_STATUSES = {"open", "repaired", "resolved"}
OBSERVABLE_RUN_EVENTS = frozenset(("push", "workflow_dispatch"))
RUN_FIELDS = (
    "attempt,databaseId,headSha,headBranch,status,conclusion,url,workflowName,jobs"
)
RUNNER_ACQUISITION_MESSAGE = (
    "The job was not acquired by Runner of type hosted even after multiple attempts"
)

Runner = Callable[..., subprocess.CompletedProcess[str]]
AncestorCheck = Callable[[str, str], bool]


class LedgerError(RuntimeError):
    """Signal missing, contradictory, or unsafe CI ownership evidence."""


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
            if step_conclusion and step_conclusion not in SUCCESS_CONCLUSIONS:
                failed_steps.append(_text(step.get("name"), f"job {name} step name"))
        if job_conclusion not in SUCCESS_CONCLUSIONS and not failed_steps:
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
        if existing.get("payload_fingerprint") == fingerprint:
            return "unchanged"
        raise LedgerError(f"immutable terminal run {run_key} changed after observation")
    runs[run_key] = {
        **snapshot,
        "observed_at": observed_at,
        "payload_fingerprint": fingerprint,
    }

    families = cast(dict[str, Any], ledger["families"])
    for job in snapshot["jobs"]:
        if job["conclusion"] in SUCCESS_CONCLUSIONS:
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
        for stale_key in ("repair", "resolved_by_run", "resolved_by_sha", "resolved_at"):
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
    if run["conclusion"] in SUCCESS_CONCLUSIONS:
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
    if snapshot["conclusion"] in SUCCESS_CONCLUSIONS:
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
        if str(job.get("conclusion") or "").lower() not in SUCCESS_CONCLUSIONS
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
            not in SUCCESS_CONCLUSIONS | {""}
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
    for family in selected:
        family["status"] = "repaired"
        family["repair"] = {
            "sha": repair_sha,
            "evidence": evidence,
            "recorded_at": recorded_at,
        }
    validate_ledger(ledger)


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


def fetch_run(run_id: int, repository: str, *, runner: Runner = subprocess.run) -> dict[str, Any]:
    """Fetch the latest immutable attempt with every job and step from GitHub."""
    if REPOSITORY.fullmatch(repository) is None:
        raise LedgerError("repository must be owner/name")
    result = runner(
        [
            "gh",
            "run",
            "view",
            str(run_id),
            "--repo",
            repository,
            "--json",
            RUN_FIELDS,
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
    if _run_id(payload.get("databaseId")) != run_id:
        raise LedgerError("GitHub returned a different run identity")
    return payload


def fetch_job_annotations(
    job_id: int, repository: str, *, runner: Runner = subprocess.run
) -> list[str]:
    """Fetch every annotation message for one check run without truncation."""
    if job_id < 1:
        raise LedgerError("check-run ID must be a positive integer")
    if REPOSITORY.fullmatch(repository) is None:
        raise LedgerError("repository must be owner/name")
    result = runner(
        [
            "gh",
            "api",
            f"repos/{repository}/check-runs/{job_id}/annotations",
            "--paginate",
            "--slurp",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "gh api failed").strip()
        raise LedgerError(f"check-run annotation lookup failed: {detail}")
    try:
        decoded = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise LedgerError(f"check-run annotations returned invalid JSON: {exc}") from exc
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
        if str(job.get("conclusion") or "").lower() in SUCCESS_CONCLUSIONS:
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
    newest: dict[str, dict[str, Any]] = {}
    for run in index:
        workflow = str(run.get("workflowName") or "").strip()
        if (
            str(run.get("headSha") or "") != sha
            or str(run.get("headBranch") or "") != branch
            or str(run.get("event") or "") not in OBSERVABLE_RUN_EVENTS
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

    results: list[tuple[int, str]] = []
    errors: list[str] = []
    for workflow in sorted(newest):
        summary = newest[workflow]
        if str(summary.get("status") or "").lower() != "completed":
            continue
        run_id = _run_id(summary.get("databaseId"))
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
    ledger = new_ledger() if args.validate_only else read_ledger(args.ledger)
    payload = _validation_payload(args.run) if args.validate_only else fetch_run(args.run, args.repo)
    result = observe_payload(ledger, payload, observed_at=_now())
    if not args.validate_only:
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
    ledger = read_ledger(args.ledger)
    index = fetch_run_index(sha, args.repo)
    results, errors = observe_exact_sha(
        ledger,
        index,
        sha=sha,
        branch=args.branch,
        repository=args.repo,
        observed_at=_now(),
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


def _command_repair(args: argparse.Namespace) -> int:
    if MAKE_TARGET.fullmatch(args.evidence_target) is None:
        raise LedgerError("evidence target must be one plain make target")
    if any(MAKE_VARIABLE.fullmatch(value) is None for value in args.evidence_var):
        raise LedgerError("each evidence variable must use UPPER_CASE=value")
    if args.validate_only:
        print("CI_FAILURE_REPAIR_VALIDATE_ONLY_PASS")
        return 0
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
    command = ["make", args.evidence_target, *args.evidence_var]
    print(f"CI_FAILURE_REPAIR_EVIDENCE command={shlex.join(command)}", flush=True)
    result = subprocess.run(command, cwd=ROOT, check=False)
    if result.returncode != 0:
        raise LedgerError(f"repair evidence failed with exit {result.returncode}")
    record_repair(
        ledger,
        selected,
        repair_sha=args.sha,
        evidence=shlex.join(command),
        recorded_at=_now(),
        is_ancestor=_git_is_ancestor,
    )
    write_ledger(args.ledger, ledger)
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
    """Run the selected failure-ledger command and fail closed on input errors."""
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
