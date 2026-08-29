"""Regression contract for the beta.3 fail-closed release pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "build.yml"

REQUIRED_RELEASE_JOBS = {
    "gate",
    "test-shard",
    "coverage",
    "molecule",
    "linux",
    "macos",
    "windows",
    "termux",
    "container",
    "ansible-ee",
    "game-building",
}
REQUIRED_FAIL_CLOSED_JOBS = REQUIRED_RELEASE_JOBS - {"gate", "coverage"}
PLATFORM_JOBS = ("linux", "macos", "windows", "termux")


def _workflow() -> dict[str, Any]:
    data = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert isinstance(data.get("jobs"), dict)
    return data


def _job(name: str) -> dict[str, Any]:
    job = _workflow()["jobs"].get(name)
    assert isinstance(job, dict), f"required workflow job {name!r} is missing"
    return job


def _run_blocks(job: dict[str, Any]) -> str:
    return "\n".join(
        str(step.get("run", ""))
        for step in job.get("steps", [])
        if isinstance(step, dict)
    )


def _upload_steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        step
        for step in job.get("steps", [])
        if isinstance(step, dict)
        and str(step.get("uses", "")).startswith("actions/upload-artifact@")
    ]


def test_tag_pipeline_has_no_false_green_escape_hatches() -> None:
    source = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "continue-on-error: true" not in source
    assert "if-no-files-found: warn" not in source
    assert "|| true" not in source
    assert "(informational)" not in source
    assert "reporting-only, non-gating" not in source


def test_cleanup_traps_preserve_primary_failures_and_fail_closed() -> None:
    source = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert source.count("primary_status=$?") >= 2
    assert source.count("cleanup_failed=1") >= 2
    assert source.count('return "$primary_status"') >= 2


def test_all_release_prerequisites_are_blocking() -> None:
    for name in REQUIRED_FAIL_CLOSED_JOBS:
        assert _job(name).get("continue-on-error", False) is False, name


def test_release_waits_for_every_test_and_artifact_producer() -> None:
    release = _job("release")
    needs = release.get("needs", [])
    if isinstance(needs, str):
        needs = [needs]
    assert set(needs) >= REQUIRED_RELEASE_JOBS
    assert str(release.get("if", "")) == "startsWith(github.ref, 'refs/tags/v')"


def test_test_shards_reject_empty_selection_and_missing_coverage() -> None:
    shard = _job("test-shard")
    runs = _run_blocks(shard)
    assert "scripts/run_ci_shards_serial.py" in runs
    runner = (ROOT / "scripts" / "run_ci_shards_serial.py").read_text(
        encoding="utf-8"
    )
    assert "SHARD-EMPTY" in runner
    assert "SHARD-COVERAGE-MISSING" in runner
    assert "failures[shard] = 2" in runner
    assert "RC -eq 5" not in runner
    coverage_config = (ROOT / ".coveragerc-greenlet").read_text(encoding="utf-8")
    assert "src/general_ludd" in coverage_config
    assert (
        "collections/ansible_collections/general_ludd/governance/plugins/module_utils"
        in coverage_config
    )
    assert "coverage_file.stat().st_size == 0" in runner
    uploads = _upload_steps(shard)
    assert uploads
    assert all(
        step.get("with", {}).get("if-no-files-found") == "error"
        for step in uploads
    )


def test_coverage_requires_all_shards_and_both_thresholds() -> None:
    coverage = _job("coverage")
    runs = _run_blocks(coverage)
    matrix = _job("test-shard")["strategy"]["matrix"]
    expected_files = len(matrix["python-version"]) * len(matrix["shard"])
    assert f"EXPECTED_SHARD_COVERAGE_FILES={expected_files}" in runs
    assert (
        'if [ "$ACTUAL_SHARD_COVERAGE_FILES" -ne '
        '"$EXPECTED_SHARD_COVERAGE_FILES" ]; then' in runs
    )
    assert "Expected exactly $EXPECTED_SHARD_COVERAGE_FILES shard coverage files" in runs
    assert "Expected at least 6 shard coverage files" not in runs
    assert "one shard's data lost after a green run" not in runs
    assert "coverage report --skip-covered --fail-under=85" in runs
    assert "coverage json -o coverage.json" in runs
    assert "audit_coverage.py" in runs
    assert "--threshold=85" in runs
    assert "--per-file-threshold=75" in runs
    assert "No shard coverage data found" not in runs
    uploads = _upload_steps(coverage)
    assert uploads
    assert all(
        step.get("with", {}).get("if-no-files-found") == "error"
        for step in uploads
    )


def test_molecule_and_platform_artifacts_fail_when_missing() -> None:
    for name in ("molecule", *PLATFORM_JOBS):
        uploads = _upload_steps(_job(name))
        assert uploads, f"{name} must retain diagnostic/build artifacts"
        assert all(
            step.get("with", {}).get("if-no-files-found") == "error"
            for step in uploads
        ), name


def test_every_platform_smokes_binary_before_upload() -> None:
    for name in PLATFORM_JOBS:
        steps = _job(name).get("steps", [])
        smoke_index = next(
            index
            for index, step in enumerate(steps)
            if "smoke test binary" in str(step.get("name", "")).lower()
        )
        upload_index = next(
            index
            for index, step in enumerate(steps)
            if str(step.get("uses", "")).startswith("actions/upload-artifact@")
        )
        assert smoke_index < upload_index, name
        assert steps[smoke_index].get("continue-on-error", False) is False


def test_release_upload_rejects_unmatched_and_zero_size_assets() -> None:
    release = _job("release")
    runs = _run_blocks(release)
    assert "find release-assets -type f -size 0" in runs
    assert "sha256sum -c" in runs
    release_steps = [
        step
        for step in release.get("steps", [])
        if str(step.get("uses", "")).startswith("softprops/action-gh-release@")
    ]
    assert len(release_steps) == 1
    assert release_steps[0].get("with", {}).get("fail_on_unmatched_files") is True
