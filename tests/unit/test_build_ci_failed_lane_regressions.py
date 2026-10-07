"""Regression coverage for Build and Release gate failures."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts import ansible_runtime_artifacts as artifacts

ROOT = Path(__file__).resolve().parents[2]


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
]
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(artifacts, "CONTROLLER_PROFILE", controller_profile)

    assert artifacts.validate_files() == []
