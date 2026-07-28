"""CP.12: diagnostic uploads survive failures; release uploads do not.

Coverage and Molecule diagnostics use ``if: always()`` so a red job retains
evidence. Platform release assets upload only after every build and smoke step
succeeds, preventing partial binaries from entering the release fan-in.

The test parses .github/workflows/build.yml with yaml.safe_load and walks
every job's steps; any step using `actions/upload-artifact` MUST carry an
`if:` condition whose value contains `always()`.
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
BUILD_YML = ROOT / ".github" / "workflows" / "build.yml"


def _load_workflow() -> dict:
    """Parse build.yml into a dict via yaml.safe_load."""
    assert BUILD_YML.exists(), f"build.yml not found at {BUILD_YML}"
    with BUILD_YML.open() as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, dict), "build.yml top-level must be a mapping"
    return data


def _upload_artifact_steps(data: dict) -> list[tuple[str, dict]]:
    """Return (job_name, step) tuples for every upload-artifact step."""
    out: list[tuple[str, dict]] = []
    jobs = data.get("jobs") or {}
    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        steps = job.get("steps") or []
        for step in steps:
            if not isinstance(step, dict):
                continue
            uses = step.get("uses") or ""
            if "upload-artifact" in uses:
                out.append((job_name, step))
    return out


class TestUploadArtifactConditions:
    """Artifact conditions distinguish diagnostics from release assets."""

    def test_at_least_one_upload_artifact_step_exists(self):
        steps = _upload_artifact_steps(_load_workflow())
        assert steps, (
            "Expected at least one actions/upload-artifact step in build.yml — "
            "if there are none, this test is vacuously true and meaningless."
        )

    def test_diagnostic_uploads_have_if_always(self):
        data = _load_workflow()
        steps = _upload_artifact_steps(data)
        diagnostic_jobs = {"test-shard", "coverage", "molecule"}
        violations = [
            job_name
            for job_name, step in steps
            if job_name in diagnostic_jobs
            and "always()" not in str(step.get("if", ""))
        ]
        assert not violations, (
            "diagnostic uploads missing if: always(): " + ", ".join(violations)
        )

    def test_platform_release_assets_upload_only_on_success(self):
        data = _load_workflow()
        steps = _upload_artifact_steps(data)
        platform_jobs = {"linux", "macos", "windows", "termux"}
        violations = [
            f"{job_name}: {step.get('if')!r}"
            for job_name, step in steps
            if job_name in platform_jobs
            and str(step.get("if", "success()")) != "success()"
        ]
        assert not violations, (
            "platform release assets must not upload after failed smoke/build "
            "steps: " + ", ".join(violations)
        )
