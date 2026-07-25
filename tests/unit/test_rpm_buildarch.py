"""tests/unit/test_rpm_buildarch.py — PK.15.

Verifies the RPM spec's ``BuildArch`` directive matches the architecture of the
CI runner that produces the ``.rpm`` artifact.  A mismatch would either fail
the build (rpmbuild refuses to emit a mismatched arch) or ship a binary
mislabelled for the wrong platform.

Checks:
1. ``dist/rpm/gludd.spec`` exists.
2. A ``BuildArch:`` directive is present in the spec.
3. The ``BuildArch`` value is ``x86_64``.
4. The CI ``linux`` job in ``build.yml`` runs on an x86_64 runner.
5. The spec arch and the CI arch agree.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = ROOT / "dist" / "rpm" / "gludd.spec"
BUILD_YML = ROOT / ".github" / "workflows" / "build.yml"

# GitHub-hosted runners whose labels resolve to x86_64.
# ``ubuntu-latest`` currently maps to ubuntu-24.04 (x86_64); the ARM runner is
# explicitly ``ubuntu-24.04-arm`` / ``ubuntu-*-arm*`` and is excluded here.
X86_64_RUNNER_LABELS: frozenset[str] = frozenset(
    {
        "ubuntu-latest",
        "ubuntu-24.04",
        "ubuntu-22.04",
        "ubuntu-20.04",
        "ubuntu-18.04",
    }
)

_BUILDARCH_RE = re.compile(r"^BuildArch:\s*(\S+)\s*$", re.MULTILINE)


def _spec_text() -> str:
    if not SPEC_PATH.exists():
        pytest.fail(f"RPM spec not found at {SPEC_PATH}")
    return SPEC_PATH.read_text()


def _buildarch_value(spec_text: str) -> str | None:
    m = _BUILDARCH_RE.search(spec_text)
    return m.group(1) if m else None


def _linux_job_runs_on(workflow_src: str) -> str | None:
    """Return the ``runs-on:`` label of the ``linux:`` job, or None."""
    pat = re.compile(
        r"^  linux:\s*\n"  # job key at column 2
        r"(?:.*\n)*?"  # any job-body lines (non-greedy)
        r"^    runs-on:\s*(\S+)\s*$",  # first runs-on under linux:
        re.MULTILINE,
    )
    m = pat.search(workflow_src)
    return m.group(1) if m else None


def _runner_arch(runs_on: str) -> str:
    """Map a GitHub ``runs-on`` label to a CPU architecture string."""
    label = runs_on.strip().strip("\"'")
    if "arm" in label or "aarch64" in label:
        return "aarch64"
    if label in X86_64_RUNNER_LABELS:
        return "x86_64"
    if "x64" in label or "x86_64" in label:
        return "x86_64"
    return "unknown"


class TestSpecExists:
    def test_spec_file_exists(self) -> None:
        assert SPEC_PATH.is_file(), f"Expected RPM spec at {SPEC_PATH}"

    def test_spec_file_nonempty(self) -> None:
        assert len(_spec_text()) > 0


class TestBuildArchDirective:
    def test_buildarch_present(self) -> None:
        assert _BUILDARCH_RE.search(_spec_text()) is not None, (
            "gludd.spec must declare a BuildArch directive"
        )

    def test_buildarch_value_is_x86_64(self) -> None:
        val = _buildarch_value(_spec_text())
        assert val == "x86_64", f"BuildArch expected x86_64, got {val!r}"

    def test_buildarch_only_one_directive(self) -> None:
        matches = _BUILDARCH_RE.findall(_spec_text())
        assert len(matches) == 1, (
            f"Expected exactly one BuildArch line, found {len(matches)}: {matches}"
        )


class TestCILinuxRunnerArch:
    def test_build_yml_exists(self) -> None:
        assert BUILD_YML.is_file(), f"build.yml not found at {BUILD_YML}"

    def test_linux_job_present(self) -> None:
        assert re.search(r"^  linux:\s*$", _build_yml_src(), re.MULTILINE) is not None

    def test_linux_job_has_runs_on(self) -> None:
        assert _linux_job_runs_on(_build_yml_src()) is not None, (
            "linux: job missing a runs-on: directive"
        )

    def test_linux_job_runs_on_x86_64(self) -> None:
        label = _linux_job_runs_on(_build_yml_src())
        assert label is not None
        arch = _runner_arch(label)
        assert arch == "x86_64", (
            f"linux job runs-on {label!r} → {arch}; expected x86_64"
        )


class TestSpecAndCIArchMatch:
    def test_spec_arch_matches_ci_arch(self) -> None:
        spec_arch = _buildarch_value(_spec_text())
        ci_label = _linux_job_runs_on(_build_yml_src())
        assert spec_arch is not None, "spec has no BuildArch"
        assert ci_label is not None, "linux job has no runs-on"
        ci_arch = _runner_arch(ci_label)
        assert spec_arch == ci_arch, (
            f"BuildArch={spec_arch!r} but CI linux job runs on {ci_label!r} ({ci_arch})"
        )


def _build_yml_src() -> str:
    if not BUILD_YML.exists():
        pytest.fail(f"build.yml not found at {BUILD_YML}")
    return BUILD_YML.read_text()
