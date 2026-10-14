"""Exercise coverage.py's actual source boundary against same-name wrappers."""

from __future__ import annotations

import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest
from coverage import Coverage

ARTIFACT = Path(__file__).resolve().parents[2]
ROOT = ARTIFACT if (ARTIFACT / "Makefile").is_file() else Path.cwd()
PROFILE = ARTIFACT / "config/coverage_pipeline_status.ini"


@pytest.mark.parametrize("name", ["pipeline_status.py", "require_ci_green.py"])
def test_pipeline_profile_measures_only_the_canonical_named_source(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tracing real files must include the owner and reject its external namesake."""
    monkeypatch.chdir(ROOT)
    canonical = ROOT / "scripts" / name
    external = tmp_path / "external/scripts" / name
    external.parent.mkdir(parents=True)
    external.write_text("fixture_wrapper_executed = True\n", encoding="utf-8")
    monkeypatch.setenv("COVERAGE_PROCESS_CONFIG", "")
    measurement = Coverage(config_file=str(PROFILE), data_file=str(tmp_path / "source-boundary-data"))
    measurement.start()
    try:
        owner = runpy.run_path(str(canonical), run_name="pipeline_coverage_owner_fixture")
        wrapper = runpy.run_path(str(external), run_name="pipeline_coverage_wrapper_fixture")
    finally:
        measurement.stop()

    measured = {Path(path).resolve() for path in measurement.get_data().measured_files()}
    assert owner["__file__"] == str(canonical)
    assert wrapper["fixture_wrapper_executed"] is True
    assert canonical.resolve() in measured
    assert external.resolve() not in measured
    assert all(path.is_relative_to((ROOT / "scripts").resolve()) for path in measured)
    report = tmp_path / "source-boundary-report.json"
    measurement.json_report(outfile=str(report))
    assert set(json.loads(report.read_text(encoding="utf-8"))["files"]) == {
        "scripts/pipeline_status.py", "scripts/require_ci_green.py",
    }


@pytest.mark.parametrize("name", ["pipeline_status.py", "require_ci_green.py"])
def test_pipeline_profile_preserves_source_identity_in_a_changed_directory_child(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The native subprocess patch must not select the child's same-name wrapper."""
    monkeypatch.chdir(ROOT)
    monkeypatch.setenv("COVERAGE_PROCESS_CONFIG", "")
    canonical = ROOT / "scripts" / name
    external = tmp_path / "external/scripts" / name
    external.parent.mkdir(parents=True)
    external.write_text("fixture_wrapper_executed = True\n", encoding="utf-8")
    measurement = Coverage(config_file=str(PROFILE), data_file=str(tmp_path / "child-boundary-data"))
    measurement.start()
    try:
        owner = runpy.run_path(str(canonical), run_name="pipeline_coverage_parent_fixture")
        completed = subprocess.run(
            [sys.executable, "-W", "error", "-c", (
                "import runpy, sys; "
                f"sys.path.insert(0, {str(ROOT)!r}); "
                f"owner = runpy.run_path({str(canonical)!r}, run_name='pipeline_coverage_child_fixture'); "
                "assert owner['_run_id']({'databaseId': 17}) == 17; "
                f"assert runpy.run_path({str(external)!r})['fixture_wrapper_executed'] is True"
            )],
            cwd=external.parent.parent, capture_output=True, text=True, timeout=15, check=False,
        )
    finally:
        measurement.stop()
        measurement.save()
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    measurement.combine(strict=True)
    measured = {Path(path).resolve() for path in measurement.get_data().measured_files()}
    assert canonical.resolve() in measured
    assert external.resolve() not in measured
    assert owner["_run_id"].__code__.co_firstlineno + 1 in (
        measurement.get_data().lines(str(canonical.resolve())) or []
    )


def test_pipeline_profile_preserves_branch_and_subprocess_measurement() -> None:
    """Fixing the boundary must retain the actual native child instrumentation."""
    measurement = Coverage(config_file=str(PROFILE), data_file=None)
    assert measurement.get_option("run:branch") is True
    assert measurement.get_option("run:parallel") is True
    assert measurement.get_option("run:patch") == ["subprocess"]
    assert measurement.get_option("run:source_dirs") == ["scripts"]
    assert measurement.get_option("run:include") == []
    assert measurement.get_option("report:include") == [
        "scripts/pipeline_status.py", "scripts/require_ci_green.py",
    ]
    assert measurement.get_option("run:omit") == []
    assert measurement.get_option("report:omit") is None
    assert measurement.get_option("run:disable_warnings") == []
