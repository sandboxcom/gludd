"""Structural contract for the immutable GitHub Actions run summary target."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]


def test_ci_run_summary_target_is_fail_closed_and_id_bound() -> None:
    source = compose_makefile(ROOT / "Makefile")
    marker = "ci-run-summary:"
    assert marker in source
    block = source.split(marker, maxsplit=1)[1].split("\n\n", maxsplit=1)[0]

    assert '$(RUN)' in block
    assert "scripts/ci_run_summary.py" in block
    assert "--run" in block
    assert "CI_RUN_SUMMARY_REPO" in block
    assert "CI_RUN_SUMMARY_VALIDATE_ONLY" in block
    assert "|| true" not in block
    assert "2>/dev/null" not in block


def test_ci_run_summary_has_a_safe_behavioral_contract() -> None:
    payload = json.loads(
        (ROOT / "config" / "make_target_contract.json").read_text(encoding="utf-8")
    )
    contract = next(
        item for item in payload["targets"] if item["name"] == "ci-run-summary"
    )

    assert contract["make_variables"] == [
        "RUN",
        "CI_RUN_SUMMARY_REPO",
        "CI_RUN_SUMMARY_VALIDATE_ONLY",
        "CI_RUN_SUMMARY_SHARD_TIMINGS",
    ]
    assert contract["behavior"] == (
        "make ci-run-summary RUN=123 CI_RUN_SUMMARY_REPO=sandboxcom/gludd "
        "CI_RUN_SUMMARY_VALIDATE_ONLY=1 CI_RUN_SUMMARY_SHARD_TIMINGS=1"
    )


def test_ci_run_summary_target_wires_optional_shard_timings() -> None:
    source = compose_makefile(ROOT / "Makefile")
    block = source.split("ci-run-summary:", maxsplit=1)[1].split("\n\n", maxsplit=1)[0]

    assert "CI_RUN_SUMMARY_SHARD_TIMINGS ?= 0" in source
    assert "CI_RUN_SUMMARY_SHARD_TIMINGS must be 0 or 1" in block
    assert "--shard-timings" in block


def test_release_policy_requires_producer_owned_current_observations() -> None:
    policy = (ROOT / "AGENTS.md").read_text(encoding="utf-8").split(
        "## Make Target Selection Contract", maxsplit=1,
    )[0]

    assert "Do not manually update generated current handoff or observation JSON" in policy
    assert "make ci-await" in policy and "make pipeline-status" in policy
    assert "CI_RUN_SUMMARY_SHARD_TIMINGS=1" in policy
    assert "diagnostic-only" in policy


@pytest.mark.parametrize("value", ["2", "yes", ""])
def test_ci_run_summary_rejects_invalid_timing_mode_before_lookup(value: str) -> None:
    result = subprocess.run(
        [
            "make",
            "ci-run-summary",
            "RUN=123",
            "CI_RUN_SUMMARY_REPO=sandboxcom/gludd",
            "CI_RUN_SUMMARY_VALIDATE_ONLY=1",
            f"CI_RUN_SUMMARY_SHARD_TIMINGS={value}",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )

    assert result.returncode != 0
    assert "CI_RUN_SUMMARY_SHARD_TIMINGS must be 0 or 1" in result.stdout
    assert "CI-RUN-SUMMARY VALIDATED" not in result.stdout



def _native_success_payload() -> dict[str, object]:
    return {
        "databaseId": 123,
        "headSha": "a" * 40,
        "status": "completed",
        "conclusion": "success",
        "workflowName": "Molecule Tests",
        "url": "https://github.com/sandboxcom/gludd/actions/runs/123",
        "jobs": [
            {"name": f"molecule-{index}", "status": "completed", "conclusion": "success"}
            for index in range(1, 7)
        ],
    }


def _native_summary(
    tmp_path: Path,
    *,
    payload: dict[str, object] | None = None,
    raw_payload: str | None = None,
    native_exit: int = 0,
    validate_only: str = "0",
    shard_timings: str = "0",
) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    fixture = tmp_path / "native summary"
    binaries = fixture / "bin"
    binaries.mkdir(parents=True)
    response = fixture / "response.json"
    response.write_text(
        raw_payload if raw_payload is not None else json.dumps(
            _native_success_payload() if payload is None else payload
        ),
        encoding="utf-8",
    )
    capture = fixture / "gh-argv.jsonl"
    gh = binaries / "gh"
    gh.write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, sys\n"
        f"capture = pathlib.Path({str(capture)!r})\n"
        "with capture.open('a', encoding='utf-8') as output:\n"
        "    output.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        f"if {native_exit}:\n"
        "    print('fixture gh lookup failed', file=sys.stderr)\n"
        f"    raise SystemExit({native_exit})\n"
        f"print(pathlib.Path({str(response)!r}).read_text(encoding='utf-8'), end='')\n",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    environment = os.environ.copy()
    for key in ("MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKELEVEL"):
        environment.pop(key, None)
    environment.update(
        PATH=str(binaries) + os.pathsep + os.environ["PATH"],
        GH_TOKEN="ci-summary-private-carrier",
        GH_CONFIG_DIR=str(fixture / "private-gh"),
        UV_PROJECT_ENVIRONMENT=sys.prefix,
        VIRTUAL_ENV=sys.prefix,
        UV_NO_SYNC="1",
        UV_PYTHON=f"{sys.version_info.major}.{sys.version_info.minor}",
        UV_PYTHON_DOWNLOADS="never",
        PYTHON=sys.executable,
        SYSTEM_PYTHON=sys.executable,
    )
    result = subprocess.run(
        [
            "make", "--no-print-directory", "ci-run-summary",
            "RUN=123", "CI_RUN_SUMMARY_REPO=sandboxcom/gludd",
            f"CI_RUN_SUMMARY_VALIDATE_ONLY={validate_only}",
            f"CI_RUN_SUMMARY_SHARD_TIMINGS={shard_timings}",
            f"PYTHON={sys.executable}", f"SYSTEM_PYTHON={sys.executable}",
            f"UV_PROJECT_ENVIRONMENT={sys.prefix}", f"VIRTUAL_ENV={sys.prefix}",
            "UV_NO_SYNC=1", f"UV_PYTHON={sys.version_info.major}.{sys.version_info.minor}",
            "UV_PYTHON_DOWNLOADS=never",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    calls = (
        [cast(list[str], json.loads(line)) for line in capture.read_text(encoding="utf-8").splitlines()]
        if capture.exists() else []
    )
    return result, calls


def _native_guidance(result: subprocess.CompletedProcess[str]) -> str:
    lines = [
        line for line in result.stderr.splitlines()
        if line.startswith("AGENT_NEXT target=ci-run-summary ")
    ]
    assert len(lines) == 1, result.stdout + result.stderr
    assert "AGENT_NEXT target=ci-run-summary " not in result.stdout
    return lines[0]


def test_native_passed_summary_reports_only_diagnostic_identity(tmp_path: Path) -> None:
    result, calls = _native_summary(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == [[
        "run", "view", "123", "--repo", "sandboxcom/gludd", "--json",
        "databaseId,headSha,status,conclusion,url,workflowName,jobs",
    ]]
    assert "RUN 123 WORKFLOW Molecule Tests SHA " + "a" * 40 in result.stdout
    assert result.stdout.count("PASS molecule-") == 6
    assert "0 failed, 0 running, 6 passed, 0 other  (6 total)" in result.stdout
    prompt = _native_guidance(result)
    for field in (
        "outcome=passed", "native_status=0", "authority=diagnostic-only",
        "gate_evidence=false", "release_ready=false", "run_id=123",
        "head_sha=" + "a" * 40, "identity=validated-run-id-sha",
        "scope=reported-run-and-listed-jobs", "next_owner=pipeline-status",
    ):
        assert field in prompt
    assert "ci-summary-private-carrier" not in result.stdout + result.stderr


def test_native_plan_reports_no_lookup_or_hosted_proof(tmp_path: Path) -> None:
    result, calls = _native_summary(tmp_path, validate_only="1", shard_timings="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == []
    assert result.stdout == "CI-RUN-SUMMARY VALIDATED run=123 repo=sandboxcom/gludd\n"
    assert "FETCH_START" not in result.stderr
    assert "SHARD_TIMINGS " not in result.stdout
    prompt = _native_guidance(result)
    for field in (
        "outcome=planned", "native_status=0", "run_id=123", "head_sha=unknown",
        "identity=requested-only", "scope=offline-inputs", "next_owner=ci-run-summary",
        "gate_evidence=false", "release_ready=false",
        "offline-plan-performs-no-lookup",
    ):
        assert field in prompt
    assert "outcome=passed" not in prompt


@pytest.mark.parametrize("empty", [True, False], ids=["queued-without-jobs", "running-job"])
def test_native_pending_summary_keeps_nonzero_and_requires_caller_context(
    tmp_path: Path, empty: bool,
) -> None:
    payload = _native_success_payload()
    payload["status"], payload["conclusion"] = ("queued" if empty else "in_progress"), None
    if empty:
        payload["jobs"] = []
    else:
        jobs = cast(list[dict[str, object]], payload["jobs"])
        jobs[0]["status"], jobs[0]["conclusion"] = "in_progress", None
    result, calls = _native_summary(tmp_path, payload=payload)
    assert result.returncode == 2 and len(calls) == 1
    assert ("No jobs found in CI run." if empty else "1 running") in result.stdout
    prompt = _native_guidance(result)
    for field in (
        "outcome=pending", "native_status=1", "next_owner=ci-await",
        "select-actual-branch-workflow-and-kind", "gate_evidence=false",
        "release_ready=false", "no_unchanged_rerun=true",
    ):
        assert field in prompt
    assert "outcome=passed" not in prompt


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "skipped"])
def test_native_nongreen_summary_inspects_exact_run_before_repair(
    tmp_path: Path, conclusion: str,
) -> None:
    payload = _native_success_payload()
    jobs = cast(list[dict[str, object]], payload["jobs"])
    jobs[0]["conclusion"] = conclusion
    payload["conclusion"] = conclusion
    if conclusion == "failure":
        payload["status"], payload["conclusion"] = "in_progress", None
        jobs[1]["status"], jobs[1]["conclusion"] = "in_progress", None
    result, calls = _native_summary(tmp_path, payload=payload)
    assert result.returncode == 2 and len(calls) == 1
    assert ("FAIL molecule-1" if conclusion == "failure" else "?    molecule-1") in result.stdout
    prompt = _native_guidance(result)
    for field in (
        "outcome=failed", "native_status=1", "run_id=123", "head_sha=" + "a" * 40,
        "next_owner=ci-view", "failed-or-censored-job", "no_unchanged_rerun=true",
    ):
        assert field in prompt
    assert "outcome=passed" not in prompt and "next_owner=ci-await" not in prompt


@pytest.mark.parametrize(
    ("failure", "native_status", "message"),
    [
        ("tool", 4, "fixture gh lookup failed"),
        ("json", 1, "invalid JSON"),
        ("run-id", 1, "requested run 123 but received 124"),
        ("sha", 1, "invalid immutable head SHA"),
    ],
)
def test_native_lookup_error_preserves_failure_and_withholds_response_identity(
    tmp_path: Path, failure: str, native_status: int, message: str,
) -> None:
    payload = _native_success_payload()
    if failure == "run-id":
        payload["databaseId"] = 124
    if failure == "sha":
        payload["headSha"] = "untrusted-short-sha"
    result, calls = _native_summary(
        tmp_path, payload=payload,
        raw_payload="not-json" if failure == "json" else None,
        native_exit=4 if failure == "tool" else 0,
    )
    assert result.returncode == 2 and len(calls) == 1
    assert message in result.stderr
    assert "RUN 123" not in result.stdout and "PASS " not in result.stdout
    prompt = _native_guidance(result)
    for field in (
        "outcome=lookup-error", f"native_status={native_status}", "run_id=123",
        "head_sha=unknown", "identity=requested-only", "scope=run-lookup",
        "next_owner=ci-view", "repair-reported-lookup-or-identity-input",
        "no_unchanged_rerun=true",
    ):
        assert field in prompt
    assert "a" * 40 not in prompt and "untrusted-short-sha" not in prompt
    assert "outcome=passed" not in prompt


def test_native_passed_summary_retains_all_eight_unknown_shard_sources(tmp_path: Path) -> None:
    result, calls = _native_summary(tmp_path, shard_timings="1")
    assert result.returncode == 0 and len(calls) == 1
    lines = [
        line.removeprefix("SHARD_TIMINGS ") for line in result.stdout.splitlines()
        if line.startswith("SHARD_TIMINGS ")
    ]
    assert len(lines) == 1
    report = cast(dict[str, object], json.loads(lines[0]))
    rows = cast(list[dict[str, object]], report["shards"])
    assert [row["shard"] for row in rows] == [
        "unit-1a1", "unit-1a2", "unit-1b", "unit-1d", "unit-2", "unit-3a", "unit-3b", "other",
    ]
    assert all(row["source_job_id"] is None and row["test_step_seconds"] is None for row in rows)
    assert report["gate_evidence"] is False and report["release_ready"] is False
    prompt = _native_guidance(result)
    assert "outcome=passed" in prompt and "native_status=0" in prompt
    assert "next_owner=pipeline-status" in prompt and "authority=diagnostic-only" in prompt


def test_native_guidance_omits_untrusted_workflow_and_job_labels(tmp_path: Path) -> None:
    payload = _native_success_payload()
    payload["workflowName"] = "private-workflow-label"
    cast(list[dict[str, object]], payload["jobs"])[0]["name"] = "private-job-label"
    result, calls = _native_summary(tmp_path, payload=payload)
    assert result.returncode == 0 and len(calls) == 1
    assert "private-workflow-label" in result.stdout and "private-job-label" in result.stdout
    prompt = _native_guidance(result)
    assert "private-workflow-label" not in prompt and "private-job-label" not in prompt
    assert "ci-summary-private-carrier" not in result.stdout + result.stderr
