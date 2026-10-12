"""Regression coverage for Build and Release gate failures."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
import yaml
from scripts import ansible_runtime_artifacts as artifacts

ROOT = Path(__file__).resolve().parents[2]


def test_rpm_changelog_weekday_matches_its_actual_date() -> None:
    """Package metadata must not produce the hosted bogus-date warning."""
    spec = (ROOT / "dist/rpm/gludd.spec").read_text(encoding="utf-8")
    entry = next(line for line in spec.splitlines() if line.startswith("* "))
    label = " ".join(entry.split()[1:5])
    assert datetime.strptime(label, "%a %b %d %Y").strftime("%a") == label.split()[0]


@pytest.mark.parametrize("target", ["deb-package", "rpm-package"])
def test_linux_packages_keep_exact_workflow_artifact_version(target: str) -> None:
    """PEP 440 source normalization may not rename platform release assets."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/build.yml").read_text())
    steps = workflow["jobs"]["linux"]["steps"]
    package_step = next(step for step in steps if f"make {target} " in step.get("run", ""))
    assert f'make {target} VERSION="$VERSION"' in package_step["run"]
    assert package_step.get("continue-on-error", False) is False


def test_gate_runs_lint_before_azure_coverage_without_hiding_coverage() -> None:
    workflow = (ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")
    lint_offset = workflow.index("      - name: Lint\n")
    coverage_offset = workflow.index(
        "      - name: Azure Container App coverage (hermetic)\n"
    )
    coverage_end = workflow.find("      - name:", coverage_offset + 1)
    coverage_step = workflow[coverage_offset:coverage_end]

    assert lint_offset < coverage_offset
    assert "!cancelled() && matrix.python-version == '3.11'" in coverage_step


def test_runtime_artifacts_read_the_split_controller_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller_profile = tmp_path / "pyproject.toml"
    controller_profile.write_text(
        """\
[project]
name = "test-controller-profile"
version = "0"
dependencies = [
    "ansible-core>=2.19,<2.20",
    "ansible-runner>=2.4",
    "azure-identity==1.26.0",
    "azure-monitor-query==2.0.0",
    "jsonpointer==3.2.0",
    "openapi-core==0.23.1",
    "signxml==5.1.0",
]
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(artifacts, "CONTROLLER_PROFILE", controller_profile)

    assert artifacts.validate_files() == []
