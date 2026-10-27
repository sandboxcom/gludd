"""Preserve verdict proof contracts while stale structural assertions follow their owner."""

from __future__ import annotations

import importlib
import inspect
import json
import re
import shlex
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from scripts.makefile_layout import compose_makefile

from tests.unit.test_ci_verdict_all_required import ROOT, SHA, _run, _sandbox, _stanza

STALE_ASSERTIONS = (
    ("test_ci_interaction_discipline", "TestCiVerdictHeadSha", "test_ci_verdict_prints_headsha"),
    ("test_ci_interaction_discipline", "TestCiVerdictHeadSha", "test_ci_verdict_prints_run_id"),
    ("test_ci_interaction_discipline", "TestCiVerdictOutputFormat", "test_green_format"),
    ("test_ci_interaction_discipline", "TestCiVerdictOutputFormat", "test_red_format"),
    ("test_ci_interaction_discipline", "TestCiVerdictOutputFormat", "test_pending_format"),
    ("test_ci_interaction_discipline", "TestCiVerdictOutputFormat", "test_includes_sha_and_run_in_output"),
    ("test_push_discipline_checks", "TestCIVerdictMustMatchHeadSHA", "test_ci_verdict_reports_head_sha"),
)


@pytest.mark.parametrize("target", ("ci-verdict", "ci-verdict-safe"))
@pytest.mark.parametrize("selection", ("legacy", "explicit", "current"))
def test_native_verdict_captures_explicit_legacy_or_current_branch_once(
    tmp_path: Path, target: str, selection: str,
) -> None:
    branch = "master" if selection == "legacy" else "development"
    runs = [_run("Build and Release", 1), _run("Molecule Tests", 2)]
    for run in runs:
        run["headBranch"] = branch
    variables = {
        "legacy": ("CI_BRANCH=", "BRANCH=master"),
        "explicit": ("CI_BRANCH=development", "BRANCH=master"),
        "current": ("CI_BRANCH=", "BRANCH="),
    }[selection]
    result = _sandbox(tmp_path, runs, target, make_variables=variables)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"REMOTE CI branch={branch} sha={SHA}" in result.stdout
    assert f"CI GREEN: sha={SHA} run=1,2" in result.stdout
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert len(calls) == 1
    assert calls[0][calls[0].index("--commit") + 1] == SHA


@pytest.mark.parametrize(("module_name", "class_name", "method_name"), STALE_ASSERTIONS)
def test_original_verdict_contracts_follow_the_actual_shared_owner(
    tmp_path: Path, module_name: str, class_name: str, method_name: str,
) -> None:
    module = importlib.import_module("tests.unit." + module_name)
    instance = getattr(module, class_name)()
    method = cast(Callable[..., None], getattr(instance, method_name))
    parameters = inspect.signature(method).parameters
    method(tmp_path=tmp_path) if "tmp_path" in parameters else method()


@pytest.mark.parametrize(
    "case", ("green", "red-build", "red-molecule", "pending", "no-run", "wrong-sha", "wrong-branch", "wrong-event"),
)
def test_native_shared_verdict_owner_retains_identity_states_and_run_ids(
    tmp_path: Path, case: str,
) -> None:
    runs = [_run("Build and Release", 1), _run("Molecule Tests", 2)]
    if case.startswith("red-"):
        runs[0 if case == "red-build" else 1]["conclusion"] = "failure"
    elif case == "pending":
        runs[1].update(status="in_progress", conclusion="")
    elif case == "no-run":
        runs.clear()
    elif case.startswith("wrong-"):
        key, value = {
            "wrong-sha": ("headSha", "b" * 40),
            "wrong-branch": ("headBranch", "foreign"),
            "wrong-event": ("event", "pull_request"),
        }[case]
        for run in runs:
            run[key] = value

    result = _sandbox(tmp_path, runs)
    output = result.stdout + result.stderr
    marker = "CI GREEN" if case == "green" else "CI RED" if case.startswith("red-") else "CI PENDING"
    run_ids = "none" if case == "no-run" or case.startswith("wrong-") else "1,2"
    assert f"{marker}: sha={SHA} run={run_ids}" in output
    assert f"REMOTE CI branch=development sha={SHA}" in output
    assert (result.returncode == 0) is (case == "green"), output
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert len(calls) == 1 and "--jq" not in calls[0]
    assert calls[0][calls[0].index("--commit") + 1] == SHA
    assert not (tmp_path / "history.json").exists()
    assert not (tmp_path / "restart-count").exists()

    if case in {"no-run", "wrong-sha", "wrong-branch", "wrong-event"}:
        assert "INCOMPLETE no workflow runs found for exact SHA" in output
        assert "INCOMPLETE missing required workflow: Build and Release" in output
        assert "INCOMPLETE missing required workflow: Molecule Tests" in output
        assert "SUMMARY 0 failed, 0 pending, 0 passed, 2 missing" in output
        assert "Build and Release run 1" not in output and "Molecule Tests run 2" not in output
        assert "Error 2" in result.stderr
    else:
        assert "Build and Release run 1 status=completed conclusion=" in output
        assert "Molecule Tests run 2 status=" in output
        if case == "green":
            assert "PASS Build and Release run 1 status=completed conclusion=success" in output
            assert "PASS Molecule Tests run 2 status=completed conclusion=success" in output
            assert "SUMMARY 0 failed, 0 pending, 2 passed, 0 missing" in output
        elif case == "pending":
            assert "PENDING Molecule Tests run 2 status=in_progress conclusion=none" in output
            assert "SUMMARY 0 failed, 1 pending, 1 passed, 0 missing" in output
            assert "Error 2" in result.stderr
        else:
            workflow, run_id = ("Build and Release", 1) if case == "red-build" else ("Molecule Tests", 2)
            assert f"FAIL {workflow} run {run_id} status=completed conclusion=failure" in output
            assert "SUMMARY 1 failed, 0 pending, 1 passed, 0 missing" in output
            assert "Error 1" in result.stderr


@pytest.mark.parametrize("consumer", ("ci-wait", "ci-poll-master"))
@pytest.mark.parametrize("case", ("green", "red", "pending", "no-run"))
def test_native_verdict_output_preserves_the_actual_existing_consumer_filters(
    tmp_path: Path, consumer: str, case: str,
) -> None:
    runs = [_run("Build and Release", 1), _run("Molecule Tests", 2)]
    if case == "red":
        runs[0]["conclusion"] = "failure"
    elif case == "pending":
        runs[1].update(status="in_progress", conclusion="")
    elif case == "no-run":
        runs.clear()
    result = _sandbox(tmp_path, runs)
    recipe = _stanza(compose_makefile(ROOT / "Makefile"), consumer)
    pattern = re.search(r"""\|\s*grep\s+(-q(?:E)?)\s+('[^']*'|"[^"]*")""", recipe)
    assert pattern is not None, "follow the actual existing consumer's literal grep filter"
    selected = subprocess.run(
        ["grep", *shlex.split(pattern.group(1) + " " + pattern.group(2))],
        input=result.stdout, text=True, capture_output=True, check=False, timeout=5,
    )
    assert selected.returncode in {0, 1}, selected.stderr
    expected = case == "green" or (consumer == "ci-poll-master" and case == "red")
    assert (selected.returncode == 0) is expected, result.stdout + result.stderr


@pytest.mark.parametrize("target", ("ci-verdict", "ci-verdict-safe"))
def test_validate_only_never_emits_compatibility_proof_markers(tmp_path: Path, target: str) -> None:
    result = _sandbox(tmp_path, [], target, validate_only=1)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PIPELINE_STATUS_VALIDATE_ONLY_PASS" in result.stdout
    assert not re.search(r"^CI (GREEN|RED|PENDING):", result.stdout, re.MULTILINE)
    assert not (tmp_path / "calls.jsonl").exists()


@pytest.mark.parametrize("incomplete", ("failure-pending", "failure-missing", "foreign-owned"))
def test_safe_verdict_compatibility_line_never_authorizes_incomplete_or_foreign_history(
    tmp_path: Path, incomplete: str,
) -> None:
    history = tmp_path / "history.json"
    restart = tmp_path / "restart-count"
    owner = "b" * 40 if incomplete == "foreign-owned" else SHA
    history.write_text(json.dumps({"last_push_sha": owner, "retained": "owned"}))
    restart.write_text("2")
    before = history.read_bytes()
    runs = [_run("Build and Release", 1), _run("Molecule Tests", 2)]
    if incomplete != "foreign-owned":
        runs[0]["conclusion"] = "failure"
        if incomplete == "failure-pending":
            runs[1].update(status="in_progress", conclusion="")
        else:
            runs.pop()
    result = _sandbox(tmp_path, runs, "ci-verdict-safe")
    assert result.returncode != 0, result.stdout + result.stderr
    assert f"CI PENDING: sha={SHA} run=" in result.stdout
    assert not re.search(r"^CI (GREEN|RED):", result.stdout, re.MULTILINE)
    assert history.read_bytes() == before and restart.read_text() == "2"
    assert "Error 2" in result.stderr


@pytest.mark.parametrize("target", ("ci-verdict", "ci-verdict-safe"))
def test_native_remote_verdict_does_not_load_snapshot_only_providers(
    tmp_path: Path, target: str,
) -> None:
    runs = [_run("Build and Release", 1), _run("Molecule Tests", 2)]
    result = _sandbox(
        tmp_path, runs, target,
        make_variables=(f"PYTHON={sys.executable} -X importtime",),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"CI GREEN: sha={SHA} run=1,2" in result.stdout
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert len(calls) == 1 and "--jq" not in calls[0]
    assert calls[0][calls[0].index("--commit") + 1] == SHA
    loaded = {
        match.group(1).strip()
        for match in re.finditer(r"(?m)^import time:\s*\d+\s*\|\s*\d+\s*\|\s*(.+)$", result.stderr)
    }
    assert {"runpy", "argparse"} <= loaded, "the same native child must exercise import instrumentation"
    snapshot_only = {
        "scripts.active_work_status", "scripts.automatic_disk_cleanup", "scripts.check_disk_usage",
        "active_work_status", "automatic_disk_cleanup", "check_disk_usage",
    }
    assert not (loaded & snapshot_only), sorted(loaded & snapshot_only)
