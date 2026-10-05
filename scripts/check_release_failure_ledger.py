#!/usr/bin/env python3
"""Validate immutable discovery-to-fix mappings for beta release failures."""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
from collections.abc import Callable
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LEDGER = ROOT / "docs" / "releases" / "beta-release-failures.json"
MAX_LEDGER_BYTES = 2_000_000
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
INTEGER_ID_RE = re.compile(r"^[1-9][0-9]*$")
BETA_TAG_RE = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+-beta\.[0-9]+$")
TARGET_RE = re.compile(r"^([A-Za-z0-9_.-]+):(?:\s|$)")

CommitExists = Callable[[str], bool]
IsAncestor = Callable[[str, str], bool]


def _required_string(
    record: dict[str, Any],
    field: str,
    label: str,
    errors: list[str],
    *,
    maximum: int = 512,
) -> str | None:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label}: {field} is required")
        return None
    if len(value) > maximum or "\n" in value or "\x00" in value:
        errors.append(f"{label}: {field} is not bounded single-line text")
        return None
    return value


def _make_targets(repository_root: Path) -> set[str]:
    makefile = repository_root / "Makefile"
    try:
        lines = makefile.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return set()
    return {
        match.group(1)
        for line in lines
        if (match := TARGET_RE.match(line)) is not None
    }


def _pytest_nodes(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return set()
    nodes: set[str] = set()

    def collect(body: list[ast.stmt], prefix: tuple[str, ...] = ()) -> None:
        for statement in body:
            if isinstance(statement, ast.ClassDef):
                collect(statement.body, (*prefix, statement.name))
            elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                nodes.add("::".join((*prefix, statement.name)))

    collect(tree.body)
    return nodes


def _validate_regression_node(
    value: str,
    *,
    repository_root: Path,
    label: str,
    errors: list[str],
) -> str | None:
    file_name, separator, raw_node = value.partition("::")
    relative = PurePosixPath(file_name)
    if (
        not separator
        or not raw_node
        or relative.is_absolute()
        or ".." in relative.parts
        or relative.suffix != ".py"
    ):
        errors.append(f"{label}: regression_node must be a repository pytest node")
        return None
    node = raw_node.split("[", maxsplit=1)[0]
    if node not in _pytest_nodes(repository_root / relative):
        errors.append(f"{label}: regression node does not exist: {value}")
        return None
    return file_name


def _validate_preflight(
    value: object,
    *,
    regression_file: str | None,
    regression_node: str | None,
    make_targets: set[str],
    label: str,
    errors: list[str],
) -> None:
    if not isinstance(value, dict):
        errors.append(f"{label}: earliest_preflight is required")
        return
    target = _required_string(value, "target", label, errors, maximum=80)
    argv = value.get("argv")
    if not isinstance(argv, list) or not argv or not all(
        isinstance(item, str) and item and "\n" not in item and "\x00" not in item
        for item in argv
    ):
        errors.append(f"{label}: earliest_preflight argv must be a bounded string list")
        return
    if target is None:
        return
    if argv[:2] != ["make", target]:
        errors.append(f"{label}: earliest_preflight argv does not invoke its target")
    if target not in make_targets:
        errors.append(f"{label}: earliest_preflight target is not defined: {target}")
    rendered = " ".join(argv)
    if "-W error" not in rendered:
        errors.append(f"{label}: earliest_preflight must treat warnings as errors")
    if regression_file is not None and f"TESTFILES={regression_file}" not in argv:
        errors.append(f"{label}: earliest_preflight does not select the regression file")
    leaf = regression_node.rsplit("::", maxsplit=1)[-1] if regression_node else None
    if leaf is not None and leaf not in rendered:
        errors.append(f"{label}: earliest_preflight does not name the regression node")


def validate_ledger(
    payload: object,
    *,
    repository_root: Path,
    commit_exists: CommitExists,
    is_ancestor: IsAncestor,
) -> list[str]:
    """Return all ledger violations; an empty list is the only passing result."""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["ledger root must be an object"]
    if payload.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    release_series = payload.get("release_series")
    if not isinstance(release_series, str) or re.fullmatch(
        r"v[0-9]+\.[0-9]+\.[0-9]+-beta", release_series
    ) is None:
        errors.append("release_series must identify a beta series")
    try:
        reviewed_at = date.fromisoformat(str(payload.get("reviewed_at", "")))
        if reviewed_at > date.today():
            errors.append("reviewed_at cannot be in the future")
    except ValueError:
        errors.append("reviewed_at must be an ISO date")

    discoveries = payload.get("discoveries")
    incidents = payload.get("incidents")
    if not isinstance(discoveries, list) or not discoveries:
        errors.append("discoveries must be a non-empty list")
        discoveries = []
    if not isinstance(incidents, list) or not incidents:
        errors.append("incidents must be a non-empty list")
        incidents = []

    discovery_by_id: dict[str, dict[str, Any]] = {}
    run_jobs: set[tuple[str, str]] = set()
    for index, raw in enumerate(discoveries):
        label = f"discoveries[{index}]"
        if not isinstance(raw, dict):
            errors.append(f"{label}: entry must be an object")
            continue
        discovery_id = _required_string(raw, "id", label, errors, maximum=100)
        run_id = _required_string(raw, "run_id", label, errors, maximum=20)
        job_id = _required_string(raw, "job_id", label, errors, maximum=20)
        release_tag = _required_string(raw, "release_tag", label, errors, maximum=40)
        head_sha = _required_string(raw, "head_sha", label, errors, maximum=40)
        evidence_command = _required_string(
            raw, "evidence_command", label, errors, maximum=100
        )
        run_url = _required_string(raw, "run_url", label, errors, maximum=200)
        job_url = _required_string(raw, "job_url", label, errors, maximum=240)
        for field in ("run_conclusion", "job_conclusion", "trigger_event"):
            _required_string(raw, field, label, errors, maximum=40)
        if release_tag is not None and BETA_TAG_RE.fullmatch(release_tag) is None:
            errors.append(f"{label}: release_tag is not a beta tag")
        if run_id is not None and INTEGER_ID_RE.fullmatch(run_id) is None:
            errors.append(f"{label}: run_id must be a positive decimal ID")
        if job_id is not None and INTEGER_ID_RE.fullmatch(job_id) is None:
            errors.append(f"{label}: job_id must be a positive decimal ID")
        if head_sha is not None and (
            SHA_RE.fullmatch(head_sha) is None or not commit_exists(head_sha)
        ):
            errors.append(f"{label}: head_sha is not a local commit")
        if raw.get("job_conclusion") != "failure":
            errors.append(f"{label}: job_conclusion must be failure")
        if run_id is not None and job_id is not None:
            run_job = (run_id, job_id)
            if run_job in run_jobs:
                errors.append(f"{label}: duplicate run/job mapping")
            run_jobs.add(run_job)
            expected_id = f"gha-{run_id}-{job_id}"
            if discovery_id is not None and discovery_id != expected_id:
                errors.append(f"{label}: id must be {expected_id}")
            expected_run_url = (
                f"https://github.com/sandboxcom/gludd/actions/runs/{run_id}"
            )
            expected_job_url = f"{expected_run_url}/job/{job_id}"
            if run_url is not None and run_url != expected_run_url:
                errors.append(f"{label}: run_url does not match run_id")
            if job_url is not None and job_url != expected_job_url:
                errors.append(f"{label}: job_url does not match run/job IDs")
        if run_id is not None and evidence_command is not None:
            expected_evidence_command = f"make ci-view RUN={run_id}"
            if evidence_command != expected_evidence_command:
                errors.append(
                    f"{label}: evidence_command must be {expected_evidence_command}"
                )
        if discovery_id is not None:
            if discovery_id in discovery_by_id:
                errors.append(f"{label}: duplicate discovery id")
            else:
                discovery_by_id[discovery_id] = raw

    make_targets = _make_targets(repository_root)
    incident_counts: dict[str, int] = {}
    for index, raw in enumerate(incidents):
        label = f"incidents[{index}]"
        if not isinstance(raw, dict):
            errors.append(f"{label}: entry must be an object")
            continue
        discovery_id = _required_string(raw, "discovery_id", label, errors, maximum=100)
        signature = _required_string(raw, "failure_signature", label, errors)
        fix_commit = _required_string(raw, "fix_commit", label, errors, maximum=40)
        regression_node = _required_string(raw, "regression_node", label, errors)
        if signature is not None and len(signature) < 12:
            errors.append(f"{label}: failure_signature is too short")
        discovery = discovery_by_id.get(discovery_id or "")
        if discovery_id is not None:
            incident_counts[discovery_id] = incident_counts.get(discovery_id, 0) + 1
            if incident_counts[discovery_id] > 1:
                errors.append(f"{label}: duplicate incident mapping")
            if discovery is None:
                errors.append(f"{label}: incident references unknown discovery")
        if fix_commit is not None:
            if SHA_RE.fullmatch(fix_commit) is None or not commit_exists(fix_commit):
                errors.append(f"{label}: fix_commit is not a local commit")
            elif discovery is not None:
                head_sha = discovery.get("head_sha")
                if not isinstance(head_sha, str) or not is_ancestor(head_sha, fix_commit):
                    errors.append(f"{label}: fix_commit does not descend from head_sha")
        regression_file = None
        if regression_node is not None:
            regression_file = _validate_regression_node(
                regression_node,
                repository_root=repository_root,
                label=label,
                errors=errors,
            )
        _validate_preflight(
            raw.get("earliest_preflight"),
            regression_file=regression_file,
            regression_node=regression_node,
            make_targets=make_targets,
            label=label,
            errors=errors,
        )

    for discovery_id in sorted(discovery_by_id):
        if incident_counts.get(discovery_id, 0) == 0:
            errors.append(f"unmapped failed discovery: {discovery_id}")
    return sorted(set(errors))


def _git_commit_exists(repository_root: Path, sha: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository_root), "cat-file", "-e", f"{sha}^{{commit}}"],
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _git_is_ancestor(repository_root: Path, parent: str, child: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository_root), "merge-base", "--is-ancestor", parent, child],
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _load_ledger(path: Path) -> tuple[object | None, str | None]:
    try:
        if path.stat().st_size > MAX_LEDGER_BYTES:
            return None, f"ledger exceeds {MAX_LEDGER_BYTES} bytes"
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"ledger is not readable JSON: {exc}"


def main(argv: list[str] | None = None) -> int:
    """Validate the configured ledger and emit one bounded terminal summary."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    repository_root = args.repository_root.resolve()
    payload, load_error = _load_ledger(args.ledger.resolve())
    errors = [load_error] if load_error else validate_ledger(
        payload,
        repository_root=repository_root,
        commit_exists=lambda sha: _git_commit_exists(repository_root, sha),
        is_ancestor=lambda parent, child: _git_is_ancestor(
            repository_root, parent, child
        ),
    )
    for error in errors:
        print(f"FAIL {error}")
    if errors:
        print(f"RELEASE_FAILURE_LEDGER_FAIL errors={len(errors)}")
        return 1
    assert isinstance(payload, dict)
    print(
        "RELEASE_FAILURE_LEDGER_PASS "
        f"discoveries={len(payload['discoveries'])} incidents={len(payload['incidents'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
