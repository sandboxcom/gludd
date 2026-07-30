"""PK.11 — verify each build job generates a .sha256 checksum for its binary.

Parses ``.github/workflows/build.yml`` and asserts that the ``linux``,
``macos``, and ``windows`` build jobs each:

1. Run a checksum tool (``sha256sum`` / ``shasum -a 256`` /
   ``Get-FileHash -Algorithm SHA256``) that emits a ``.sha256`` sidecar file.
2. Reference the built binary/archive by its ``gludd-*`` name in that
   checksum step (so we checksum the real artifact, not an anonymous file).
3. Upload the ``.sha256`` file as a workflow artifact or stage it for
   release (so the checksum is actually publishable, not stranded on the
   runner).

If a job is missing any of these, the test fails and names the gap.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_YML = REPO_ROOT / ".github" / "workflows" / "build.yml"

# Build jobs in scope for PK.11. ``termux`` is a linux-aarch64 variant that
# follows the same pattern; it is included as an extra-data parametrize so the
# test surface stays honest, but the task spec names linux/macos/windows.
BUILD_JOBS: tuple[str, ...] = ("linux", "macos", "windows", "termux")

# Regex patterns matching the platform-specific checksum tools.
# Each must produce a ``.sha256`` output file (asserted separately).
CHECKSUM_TOOL_PATTERNS: tuple[str, ...] = (
    r"sha256sum\b",
    r"shasum\s+-a\s+256",
    r"Get-FileHash\b.*-Algorithm\s+SHA256\b",
)

# Pattern matching the built binary/archive name prefix. Every checksummed
# artifact must be named ``gludd-...`` so the checksum is bound to a real
# build output (not a stray file).
GLUDD_ARTIFACT_PATTERN = r"gludd-"


# ---------------------------------------------------------------------------
# YAML loading + helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    """Load build.yml once per module; fail loud if missing or malformed."""
    assert BUILD_YML.is_file(), f"build.yml not found at {BUILD_YML}"
    with BUILD_YML.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, dict), "build.yml top-level must be a mapping"
    assert "jobs" in data, "build.yml has no 'jobs' mapping"
    return data


def _job(workflow: dict[str, Any], name: str) -> dict[str, Any]:
    """Return a single job dict, asserting it exists."""
    job = workflow.get("jobs", {}).get(name)
    assert job is not None, f"build.yml job '{name}' not found"
    return job


def _steps(workflow: dict[str, Any], name: str) -> list[dict[str, Any]]:
    """Return the ``steps`` list for a job, asserting non-empty."""
    steps = _job(workflow, name).get("steps") or []
    assert steps, f"build.yml job '{name}' has no steps"
    return steps


def _step_runs(workflow: dict[str, Any], name: str) -> list[str]:
    """Return only the ``run:`` blocks (the shell/pwsh scripts) for a job."""
    runs: list[str] = []
    for step in _steps(workflow, name):
        run = step.get("run")
        if isinstance(run, str):
            runs.append(run)
    return runs


def _combined_runs(workflow: dict[str, Any], name: str) -> str:
    """All ``run:`` blocks joined into one searchable blob."""
    return "\n".join(_step_runs(workflow, name))


# ---------------------------------------------------------------------------
# Test 1: each build job has a checksum-tool step
# ---------------------------------------------------------------------------


class TestChecksumToolPresent:
    """Each build job runs a supported SHA-256 checksum tool."""

    @pytest.mark.parametrize("job_name", BUILD_JOBS, ids=lambda n: f"job:{n}")
    def test_has_checksum_tool(self, workflow: dict[str, Any], job_name: str) -> None:
        combined = _combined_runs(workflow, job_name)
        assert combined, f"build.yml job '{job_name}' has no run: blocks"

        matched = [p for p in CHECKSUM_TOOL_PATTERNS if re.search(p, combined)]
        assert matched, (
            f"build.yml job '{job_name}' has no supported checksum "
            f"step producing a SHA-256 checksum. Expected one of: "
            f"{CHECKSUM_TOOL_PATTERNS}"
        )


# ---------------------------------------------------------------------------
# Test 2: the checksum step references the gludd-* binary name
# ---------------------------------------------------------------------------


class TestChecksumReferencesBinaryName:
    """The checksum command must hash a ``gludd-*`` named artifact."""

    @pytest.mark.parametrize("job_name", BUILD_JOBS, ids=lambda n: f"job:{n}")
    def test_checksum_line_names_gludd(
        self, workflow: dict[str, Any], job_name: str
    ) -> None:
        runs = _step_runs(workflow, job_name)
        # Find the specific run block(s) containing a checksum tool.
        checksum_blocks = [
            r
            for r in runs
            if any(re.search(p, r) for p in CHECKSUM_TOOL_PATTERNS)
        ]
        assert checksum_blocks, (
            f"GAP: build.yml job '{job_name}' has no checksum-tool step at all "
            f"(see TestChecksumToolPresent)."
        )

        # At least one checksum block must reference the gludd-* artifact name.
        names_gludd = any(
            re.search(GLUDD_ARTIFACT_PATTERN, block) for block in checksum_blocks
        )
        assert names_gludd, (
            f"GAP: build.yml job '{job_name}' checksum step does not reference "
            f"a 'gludd-*' artifact name. The checksum may be hashing a stray "
            f"file instead of the built binary. Blocks: {checksum_blocks}"
        )


# ---------------------------------------------------------------------------
# Test 3: a .sha256 output file is produced
# ---------------------------------------------------------------------------


class TestSha256FileEmitted:
    """Each build job writes a ``.sha256`` sidecar file."""

    @pytest.mark.parametrize("job_name", BUILD_JOBS, ids=lambda n: f"job:{n}")
    def test_emits_sha256_file(self, workflow: dict[str, Any], job_name: str) -> None:
        combined = _combined_runs(workflow, job_name)
        assert re.search(r"\.sha256\b", combined), (
            f"GAP: build.yml job '{job_name}' never writes a .sha256 output "
            f"file. The checksum tool may be printing to stdout without "
            f"redirecting to a sidecar."
        )


# ---------------------------------------------------------------------------
# Test 4: the .sha256 file is uploaded as an artifact or staged for release
# ---------------------------------------------------------------------------


def _artifact_paths(workflow: dict[str, Any], job_name: str) -> list[str]:
    """Collect every ``path:`` entry from upload-artifact steps in a job.

    The ``path`` value may be a scalar string or a multi-line block string;
    both forms are flattened into a list of stripped lines.
    """
    paths: list[str] = []
    for step in _steps(workflow, job_name):
        uses = step.get("uses") or ""
        if "upload-artifact" not in uses:
            continue
        with_block = step.get("with") or {}
        raw_path = with_block.get("path")
        if isinstance(raw_path, str):
            paths.extend(line.strip() for line in raw_path.splitlines() if line.strip())
        elif isinstance(raw_path, list):
            paths.extend(str(p).strip() for p in raw_path if p)
    return paths


class TestChecksumUploadedOrStaged:
    """The ``.sha256`` file must be uploaded as a workflow artifact.

    The release job downloads ``gludd-*`` artifacts and stages them for the
    GitHub Release, so an upload-artifact entry listing ``*.sha256`` is the
    mechanistic proof the checksum reaches the release pipeline.
    """

    @pytest.mark.parametrize("job_name", BUILD_JOBS, ids=lambda n: f"job:{n}")
    def test_sha256_in_artifact_upload(
        self, workflow: dict[str, Any], job_name: str
    ) -> None:
        paths = _artifact_paths(workflow, job_name)
        assert paths, (
            f"GAP: build.yml job '{job_name}' has no upload-artifact step — "
            f"the .sha256 file is stranded on the runner and never reaches "
            f"the release pipeline."
        )

        joined = "\n".join(paths)
        assert re.search(r"\.sha256\b", joined), (
            f"GAP: build.yml job '{job_name}' upload-artifact path list does "
            f"not include any .sha256 file. Uploaded paths:\n{joined}"
        )


# ---------------------------------------------------------------------------
# Test 5: summary — all three jobs covered (no silent gaps)
# ---------------------------------------------------------------------------


class TestAllBuildJobsCovered:
    """Smoke test: linux, macos, windows all exist and all checksum.

    Prevents a future refactor from silently dropping one platform's
    checksum step while the parametrized tests above stay green on the
    remaining two.
    """

    def test_all_three_jobs_present(self, workflow: dict[str, Any]) -> None:
        missing = [j for j in BUILD_JOBS if j not in workflow.get("jobs", {})]
        assert not missing, (
            f"GAP: build.yml is missing build job(s): {missing}. "
            f"PK.11 requires linux, macos, and windows."
        )

    def test_all_three_jobs_checksum(self, workflow: dict[str, Any]) -> None:
        gaps: list[str] = []
        for job_name in BUILD_JOBS:
            combined = _combined_runs(workflow, job_name)
            has_tool = any(
                re.search(p, combined) for p in CHECKSUM_TOOL_PATTERNS
            )
            has_file = bool(re.search(r"\.sha256\b", combined))
            if not (has_tool and has_file):
                gaps.append(
                    f"{job_name} (tool={has_tool}, .sha256 file={has_file})"
                )
        assert not gaps, (
            "GAP: the following build jobs lack checksum generation: "
            + ", ".join(gaps)
        )
