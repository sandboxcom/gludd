"""CP.12: every upload-artifact step MUST have `if: always()`.

Ensures partial failures (a build step that exits non-zero before the upload)
still upload whatever was produced, so CI artifacts are never silently lost.
Without `if: always()`, a failed preceding step skips the upload entirely,
making it impossible to diagnose the failure from the partial artifact.

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


class TestUploadArtifactAlwaysPresent:
    """Every actions/upload-artifact step MUST have `if: always()` (CP.12)."""

    def test_at_least_one_upload_artifact_step_exists(self):
        steps = _upload_artifact_steps(_load_workflow())
        assert steps, (
            "Expected at least one actions/upload-artifact step in build.yml — "
            "if there are none, this test is vacuously true and meaningless."
        )

    def test_every_upload_artifact_has_if_always(self):
        data = _load_workflow()
        steps = _upload_artifact_steps(data)
        violations = []
        for job_name, step in steps:
            cond = step.get("if")
            if cond is None:
                violations.append(
                    f"  job '{job_name}': upload-artifact step missing 'if:' "
                    f"condition entirely (needs `if: always()`)"
                )
                continue
            if "always()" not in str(cond):
                violations.append(
                    f"  job '{job_name}': upload-artifact step has "
                    f"'if: {cond}' — must contain 'always()' so partial "
                    f"failures still upload"
                )
        assert not violations, (
            "upload-artifact steps missing `if: always()` (CP.12) — "
            "partial failures will NOT upload artifacts for diagnosis:\n"
            + "\n".join(violations)
        )
