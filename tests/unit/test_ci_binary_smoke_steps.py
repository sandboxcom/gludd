"""Structural test: build jobs in .github/workflows/build.yml MUST run a
post-build smoke test against the freshly-built binary BEFORE any
upload-artifact step.

Catches the regression class where a PyInstaller build succeeds but the
binary crashes at runtime (e.g. the "Missing base YAML definition file"
ansible error that shipped in v0.1.0-beta.1). A broken binary that crashes
on ``version`` / ``--help`` must never reach the release artifact stage.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_YML = REPO_ROOT / ".github" / "workflows" / "build.yml"


@pytest.fixture(scope="module")
def build_workflow() -> dict:
    """Load .github/workflows/build.yml with yaml.safe_load."""
    assert BUILD_YML.is_file(), f"build.yml missing at {BUILD_YML}"
    with BUILD_YML.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, dict), "build.yml top-level must be a mapping"
    assert "jobs" in data, "build.yml has no 'jobs' mapping"
    return data


def _job(workflow: dict, name: str) -> dict:
    job = workflow.get("jobs", {}).get(name)
    assert isinstance(job, dict), (
        f"build.yml job '{name}' missing — required for post-build smoke test"
    )
    return job


def _steps(workflow: dict, name: str) -> list[dict]:
    return _job(workflow, name).get("steps", []) or []


def _step_index(workflow: dict, job_name: str, predicate) -> int:
    """Return the index of the first step matching predicate(name, run)."""
    for idx, step in enumerate(_steps(workflow, job_name)):
        step_name = step.get("name") or ""
        step_run = step.get("run") or ""
        if predicate(step_name, step_run):
            return idx
    return -1


def _step_uses_uses(step: dict) -> str:
    """Return the action referenced by an actions/... step, else empty."""
    uses = step.get("uses") or ""
    return uses.split("@", 1)[0] if uses else ""


# ---------------------------------------------------------------------------
# Smoke test step exists per platform
# ---------------------------------------------------------------------------


class TestSmokeTestStepPerPlatform:
    """Each platform build job (linux/macos/windows) must run a smoke test."""

    @pytest.mark.parametrize(
        "job_name", ["linux", "macos", "windows"], ids=lambda n: f"job:{n}"
    )
    def test_smoke_test_step_exists(self, build_workflow: dict, job_name: str) -> None:
        steps = _steps(build_workflow, job_name)
        assert steps, f"build.yml job '{job_name}' has no steps"

        smoke_steps = [
            s for s in steps
            if "smoke" in (s.get("name") or "").lower()
            and "binary" in (s.get("name") or "").lower()
        ]
        assert smoke_steps, (
            f"build.yml job '{job_name}' has no 'Smoke test binary' step — "
            f"a freshly-built binary must be exercised before publishing"
        )

    @pytest.mark.parametrize(
        "job_name", ["linux", "macos", "windows"], ids=lambda n: f"job:{n}"
    )
    def test_smoke_step_runs_binary(
        self, build_workflow: dict, job_name: str
    ) -> None:
        combined = "\n".join(
            (s.get("run") or "") for s in _steps(build_workflow, job_name)
        )
        assert re.search(r"(?:gludd(?:\.exe)?\s+version|gludd(?:\.exe)?\s+--version)", combined), (
            f"build.yml job '{job_name}' smoke test must run the version command"
        )
        assert "--help" in combined, (
            f"build.yml job '{job_name}' smoke test must run `--help`"
        )


# ---------------------------------------------------------------------------
# Ordering: smoke test AFTER Build executable, BEFORE upload-artifact
# ---------------------------------------------------------------------------


class TestSmokeTestOrdering:
    """Smoke test must run after build, before artifact upload."""

    @pytest.mark.parametrize(
        "job_name", ["linux", "macos", "windows"], ids=lambda n: f"job:{n}"
    )
    def test_smoke_after_build_before_upload(
        self, build_workflow: dict, job_name: str
    ) -> None:
        build_idx = _step_index(
            build_workflow,
            job_name,
            lambda n, r: "build executable" in n.lower(),
        )
        assert build_idx >= 0, (
            f"build.yml job '{job_name}' has no 'Build executable' step"
        )

        smoke_idx = _step_index(
            build_workflow,
            job_name,
            lambda n, r: "smoke" in n.lower() and "binary" in n.lower(),
        )
        assert smoke_idx > build_idx, (
            f"build.yml job '{job_name}': 'Smoke test binary' must run AFTER "
            f"'Build executable' (build={build_idx}, smoke={smoke_idx})"
        )

        upload_indices = [
            i for i, s in enumerate(_steps(build_workflow, job_name))
            if _step_uses_uses(s) == "actions/upload-artifact"
        ]
        assert upload_indices, (
            f"build.yml job '{job_name}' has no upload-artifact step"
        )
        first_upload = upload_indices[0]
        assert smoke_idx < first_upload, (
            f"build.yml job '{job_name}': 'Smoke test binary' (idx {smoke_idx}) "
            f"must run BEFORE upload-artifact (idx {first_upload}) so a broken "
            f"binary never reaches the published artifact"
        )


# ---------------------------------------------------------------------------
# Smoke test must check for known crash signatures
# ---------------------------------------------------------------------------


class TestSmokeTestCrashDetection:
    """Smoke test step must guard against known crash signatures."""

    CRASH_SIGNATURES: tuple[str, ...] = (
        "traceback",
        "Missing base YAML definition file",
    )

    @pytest.mark.parametrize(
        "job_name", ["linux", "macos", "windows"], ids=lambda n: f"job:{n}"
    )
    def test_smoke_step_detects_crash_signatures(
        self, build_workflow: dict, job_name: str
    ) -> None:
        smoke_steps = [
            s for s in _steps(build_workflow, job_name)
            if "smoke" in (s.get("name") or "").lower()
            and "binary" in (s.get("name") or "").lower()
        ]
        assert smoke_steps, (
            f"build.yml job '{job_name}' has no 'Smoke test binary' step"
        )

        combined_run = "\n".join((s.get("run") or "") for s in smoke_steps)
        for signature in self.CRASH_SIGNATURES:
            assert signature.lower() in combined_run.lower(), (
                f"build.yml job '{job_name}' smoke test does not check for "
                f"crash signature '{signature}'. A binary that crashes with "
                f"this error must fail the step, not be published silently."
            )


# ---------------------------------------------------------------------------
# Linux daemon smoke test
# ---------------------------------------------------------------------------


class TestLinuxDaemonSmokeTest:
    """The linux build job must additionally smoke-test daemon startup."""

    def test_daemon_smoke_step_exists(self, build_workflow: dict) -> None:
        steps = _steps(build_workflow, "linux")
        daemon_steps = [
            s for s in steps
            if "daemon" in (s.get("name") or "").lower()
            and "smoke" in (s.get("name") or "").lower()
        ]
        assert daemon_steps, (
            "build.yml linux job has no 'Smoke test daemon start' step — "
            "the daemon must be verified to boot on the platform that can run it"
        )

    def test_daemon_smoke_healthcheck(self, build_workflow: dict) -> None:
        combined = "\n".join(
            (s.get("run") or "")
            for s in _steps(build_workflow, "linux")
            if "daemon" in (s.get("name") or "").lower()
            and "smoke" in (s.get("name") or "").lower()
        )
        assert combined.strip(), (
            "build.yml linux daemon smoke test step has no run block"
        )
        assert "daemon start" in combined or "daemon" in combined.lower(), (
            "linux daemon smoke test must start the daemon"
        )
        assert re.search(r"/health\b", combined) or "curl" in combined, (
            "linux daemon smoke test must hit a /health endpoint to verify boot"
        )

    def test_daemon_smoke_uses_current_blocking_cli_contract(
        self, build_workflow: dict
    ) -> None:
        """The daemon command itself is the server; there is no ``start`` verb."""
        combined = "\n".join(
            (s.get("run") or "")
            for s in _steps(build_workflow, "linux")
            if "daemon" in (s.get("name") or "").lower()
            and "smoke" in (s.get("name") or "").lower()
        )

        assert "./dist/gludd daemon start" not in combined
        assert re.search(r"\./dist/gludd daemon\s+--host\s+127\.0\.0\.1", combined)
        assert "/healthz" in combined

    def test_daemon_smoke_fails_immediately_when_server_exits(
        self, build_workflow: dict
    ) -> None:
        """A dead binary is surfaced with its exit status, not a blind timeout."""
        combined = "\n".join(
            (s.get("run") or "")
            for s in _steps(build_workflow, "linux")
            if "daemon" in (s.get("name") or "").lower()
            and "smoke" in (s.get("name") or "").lower()
        )

        assert 'kill -0 "$DAEMON_PID"' in combined
        assert 'wait "$DAEMON_PID"' in combined
        assert "daemon exited before becoming healthy" in combined.lower()

    def test_daemon_smoke_before_upload(self, build_workflow: dict) -> None:
        daemon_idx = _step_index(
            build_workflow,
            "linux",
            lambda n, r: "daemon" in n.lower() and "smoke" in n.lower(),
        )
        assert daemon_idx >= 0, (
            "build.yml linux job has no 'Smoke test daemon start' step"
        )
        upload_indices = [
            i for i, s in enumerate(_steps(build_workflow, "linux"))
            if _step_uses_uses(s) == "actions/upload-artifact"
        ]
        assert upload_indices, "build.yml linux job has no upload-artifact step"
        assert daemon_idx < upload_indices[0], (
            f"linux 'Smoke test daemon start' (idx {daemon_idx}) must run "
            f"BEFORE upload-artifact (idx {upload_indices[0]})"
        )
