"""CP.16 — verify the ``coverage`` job in build.yml depends on ``test-shard``.

The coverage aggregation job produces a single canonical ``coverage.xml`` by
merging per-shard ``.coverage.*`` data files uploaded by every ``test-shard``
matrix leg. If the ``needs:`` list ever drops ``test-shard``, the coverage job
could run before (or instead of) the test shards that produce its input data —
silently publishing an empty or partial report. This test pins the dependency
structurally by parsing the YAML workflow, so a regression fails fast with a
precise message instead of an opaque CI red.

Uses only stdlib + pyyaml (pyyaml is a hard project dependency).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
BUILD_YML = ROOT / ".github" / "workflows" / "build.yml"


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    """Load build.yml once per module; fail loud if it's missing or malformed."""
    assert BUILD_YML.is_file(), f"build.yml not found at {BUILD_YML}"
    data = yaml.safe_load(BUILD_YML.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "build.yml top-level must be a mapping"
    assert "jobs" in data, "build.yml has no 'jobs' mapping"
    return data


@pytest.fixture(scope="module")
def coverage_job(workflow: dict[str, Any]) -> dict[str, Any]:
    """Return the ``coverage`` job dict, asserting it exists."""
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict), "'jobs' must be a mapping"
    assert "coverage" in jobs, (
        "CP.16: the 'coverage' job does not exist in build.yml — without it, "
        "per-shard .coverage files are never combined into a single report."
    )
    cov = jobs["coverage"]
    assert isinstance(cov, dict), "'coverage' job must be a mapping"
    return cov


def test_coverage_job_exists(workflow: dict[str, Any]) -> None:
    """The coverage aggregation job must be present."""
    assert "coverage" in workflow["jobs"], (
        "CP.16: no 'coverage' job in build.yml — coverage is never aggregated."
    )


def test_coverage_job_needs_test_shard(coverage_job: dict[str, Any]) -> None:
    """The coverage job MUST declare a dependency on ``test-shard``.

    Without it, the coverage job could start before any shard produced a
    .coverage data file, so ``coverage combine`` would have nothing to merge
    and the published report would understate the suite by ~75%.
    """
    needs = coverage_job.get("needs", [])
    if isinstance(needs, str):
        needs = [needs]
    assert isinstance(needs, list), (
        f"CP.16: 'coverage'.needs must be a list, got {type(needs).__name__}"
    )
    assert "test-shard" in needs, (
        "CP.16: 'coverage'.needs does not include 'test-shard'. The coverage "
        "job requires the .coverage data files produced by the test shards. "
        f"Found needs: {needs!r}"
    )


def test_coverage_job_downloads_artifacts(coverage_job: dict[str, Any]) -> None:
    """The coverage job MUST have a ``download-artifact`` step.

    Each test-shard uploads its ``.coverage.<shard>-<py>`` file as an artifact
    named ``coverage-*``; the coverage job downloads them before combining.
    A missing download step means the merge step has no input data.
    """
    steps = coverage_job.get("steps", [])
    assert isinstance(steps, list) and steps, "'coverage'.steps must be non-empty"

    has_download = any(
        isinstance(s, dict) and "download-artifact" in str(s.get("uses", ""))
        for s in steps
    )
    assert has_download, (
        "CP.16: the 'coverage' job has no actions/download-artifact step — "
        "it cannot fetch the per-shard .coverage data files to merge them."
    )

    # The download step must target the coverage-* artifacts produced by the
    # test-shard job, otherwise it would download unrelated build artifacts.
    download_steps = [
        s for s in steps
        if isinstance(s, dict) and "download-artifact" in str(s.get("uses", ""))
    ]
    combined = "\n".join(
        str(s.get("with", "")) + "\n" + str(s.get("run", ""))
        for s in download_steps
    )
    assert "coverage-" in combined or "coverage" in combined, (
        "CP.16: the download-artifact step in 'coverage' does not reference a "
        "'coverage-*' (or 'coverage') pattern — it may be downloading the wrong "
        "artifacts instead of the per-shard coverage data files."
    )


def test_coverage_job_runs_merge_or_report(coverage_job: dict[str, Any]) -> None:
    """The coverage job MUST run a coverage merge/report step.

    Specifically: ``coverage combine`` to merge the shard data files, then
    ``coverage xml`` / ``coverage report`` to emit the canonical report. A
    job that downloads the data but never combines it produces no report.
    """
    steps = coverage_job.get("steps", [])
    assert isinstance(steps, list) and steps, "'coverage'.steps must be non-empty"

    runs = "\n".join(str(s.get("run", "")) for s in steps if isinstance(s, dict))
    assert "coverage combine" in runs, (
        "CP.16: the 'coverage' job has no 'coverage combine' step — per-shard "
        ".coverage files are downloaded but never merged into one data file."
    )
    # A report-rendering command (xml or report) must follow the combine.
    assert "coverage xml" in runs or "coverage report" in runs, (
        "CP.16: the 'coverage' job runs 'coverage combine' but never emits a "
        "report — no 'coverage xml' or 'coverage report' step found. The merged "
        "data file is never rendered into the canonical coverage.xml / summary."
    )
