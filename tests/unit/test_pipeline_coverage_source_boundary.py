"""Exercise coverage.py's actual source boundary against same-name wrappers."""

from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest
from coverage import Coverage
from coverage.exceptions import CoverageException

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


_CI_SIGNAL_PROFILE = ARTIFACT / "config/coverage_ci_signal_exact_sha.ini"
_CI_SIGNAL_PROVIDERS = ("ci_signal_exact_sha.py", "ci_remote_head_guard.py")


def _ci_signal_profile_decoys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, dict[str, Path]]:
    """Execute namesakes outside the physical source without substituting owners."""
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(sys, "path", [str(ROOT / "scripts"), *sys.path])
    monkeypatch.setenv("GLUDD_COVERAGE_SOURCE_ROOT", str(ROOT.resolve()))
    monkeypatch.setenv("COVERAGE_PROCESS_CONFIG", "")
    foreign = tmp_path / "foreign checkout"
    assert not foreign.resolve().is_relative_to(ROOT.resolve())
    (foreign / "scripts").mkdir(parents=True)
    markers: dict[str, Path] = {}
    for name in _CI_SIGNAL_PROVIDERS:
        marker = foreign / (name + ".executed")
        markers[name] = marker
        (foreign / "scripts" / name).write_text(
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('executed', encoding='utf-8')\n"
            "def fixture_only_policy(value):\n"
            "    if value:\n"
            "        return 'altered fixture branch'\n"
            "    return 'another fixture branch'\n",
            encoding="utf-8",
        )
    return foreign, markers


def test_ci_signal_profile_excludes_executed_foreign_namesakes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The profile must identify actual provider bytes, not matching basenames."""
    foreign, markers = _ci_signal_profile_decoys(tmp_path, monkeypatch)
    measurement = Coverage(
        config_file=str(_CI_SIGNAL_PROFILE), data_file=str(tmp_path / "ci-parent-boundary-data"),
    )
    measurement.start()
    try:
        for name in _CI_SIGNAL_PROVIDERS:
            owner = runpy.run_path(
                str(ROOT / "scripts" / name),
                run_name="gludd_ci_source_parent_" + name.removesuffix(".py"),
            )
            outside = runpy.run_path(
                str(foreign / "scripts" / name),
                run_name="gludd_ci_foreign_parent_" + name.removesuffix(".py"),
            )
            assert owner["__file__"] == str(ROOT / "scripts" / name)
            assert outside["__file__"] == str(foreign / "scripts" / name)
    finally:
        measurement.stop()
    assert all(marker.read_text(encoding="utf-8") == "executed" for marker in markers.values())
    measured = {Path(path).resolve() for path in measurement.get_data().measured_files()}
    canonical = {(ROOT / "scripts" / name).resolve() for name in _CI_SIGNAL_PROVIDERS}
    assert measured == canonical
    assert all((foreign / "scripts" / name).resolve() not in measured for name in _CI_SIGNAL_PROVIDERS)


def test_ci_signal_profile_keeps_physical_identity_across_native_child_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Native subprocess coverage must retain both providers and reject executed decoys."""
    foreign, markers = _ci_signal_profile_decoys(tmp_path, monkeypatch)
    measurement = Coverage(
        config_file=str(_CI_SIGNAL_PROFILE), data_file=str(tmp_path / "ci-child-boundary-data"),
    )
    program = (
        "import runpy, sys\n"
        "from pathlib import Path\n"
        f"root = Path({str(ROOT.resolve())!r})\n"
        f"foreign = Path({str(foreign)!r})\n"
        "sys.path[:0] = [str(root / 'scripts'), str(root)]\n"
        "owners = {}\n"
        f"for name in {_CI_SIGNAL_PROVIDERS!r}:\n"
        "    owners[name] = runpy.run_path(str(root / 'scripts' / name), "
        "run_name='gludd_ci_source_child_' + name.removesuffix('.py'))\n"
        "    runpy.run_path(str(foreign / 'scripts' / name), "
        "run_name='gludd_ci_foreign_child_' + name.removesuffix('.py'))\n"
        "assert owners['ci_remote_head_guard.py']['_full_sha']('a' * 40) is True\n"
        "assert owners['ci_remote_head_guard.py']['_full_sha']('invalid') is False\n"
        "assert owners['ci_signal_exact_sha.py']['_safe_component']('owned scope') == 'owned-scope'\n"
    )
    measurement.start()
    try:
        owners = {
            name: runpy.run_path(
                str(ROOT / "scripts" / name),
                run_name="gludd_ci_source_parent_" + name.removesuffix(".py"),
            )
            for name in _CI_SIGNAL_PROVIDERS
        }
        environment = dict(os.environ)
        completed = subprocess.run(
            [sys.executable, "-P", "-W", "error", "-c", program],
            cwd=foreign, env=environment, capture_output=True, text=True, timeout=15, check=False,
        )
    finally:
        measurement.stop()
        measurement.save()
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    assert all(marker.read_text(encoding="utf-8") == "executed" for marker in markers.values())
    measurement.combine(strict=True)
    data = measurement.get_data()
    canonical = {(ROOT / "scripts" / name).resolve() for name in _CI_SIGNAL_PROVIDERS}
    assert {Path(path).resolve() for path in data.measured_files()} == canonical
    for name, function in (
        ("ci_remote_head_guard.py", "_full_sha"), ("ci_signal_exact_sha.py", "_safe_component"),
    ):
        assert owners[name][function].__code__.co_firstlineno + 1 in (
            data.lines(str((ROOT / "scripts" / name).resolve())) or []
        )
    assert data.has_arcs()


def test_ci_signal_profile_requires_the_physical_source_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing native root must refuse, rather than widen to foreign copies."""
    monkeypatch.delenv("GLUDD_COVERAGE_SOURCE_ROOT", raising=False)
    with pytest.raises(CoverageException, match="GLUDD_COVERAGE_SOURCE_ROOT"):
        Coverage(config_file=str(_CI_SIGNAL_PROFILE), data_file=None)


def test_ci_signal_profile_retains_native_branch_and_subprocess_measurement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GLUDD_COVERAGE_SOURCE_ROOT", str(ROOT.resolve()))
    measurement = Coverage(config_file=str(_CI_SIGNAL_PROFILE), data_file=None)
    assert measurement.get_option("run:branch") is True
    assert measurement.get_option("run:parallel") is True
    assert measurement.get_option("run:patch") == ["subprocess"]
    assert measurement.get_option("run:omit") == []
    assert measurement.get_option("report:omit") is None
    assert measurement.get_option("run:disable_warnings") == []
