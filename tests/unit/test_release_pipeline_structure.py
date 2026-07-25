"""Structural tests that prevent the release pipeline failures from recurring.

These tests verify:
1. No circular dependencies in the CI workflow job graph
2. Build/release jobs do NOT depend on test-shard (which takes 60+ min)
3. The release job includes artifact verification steps
4. The workflow YAML is parseable (no !cancelled() tag issues)
"""
from __future__ import annotations

import re
import typing
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BUILD_YML = ROOT / ".github" / "workflows" / "build.yml"


def _workflow_source() -> str:
    assert BUILD_YML.exists(), f"build.yml not found at {BUILD_YML}"
    return BUILD_YML.read_text()


def _extract_jobs(src: str) -> dict[str, dict]:
    """Extract job names and their needs from the workflow YAML."""
    jobs: dict[str, dict] = {}
    lines = src.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^  (\w[\w-]*):\s*$", line)
        if m and not line.startswith("    "):
            job_name = m.group(1)
            needs: list[str] = []
            j = i + 1
            while j < len(lines) and (lines[j].startswith("    ") or lines[j].strip() == ""):
                needs_match = re.search(r"needs:\s*\[(.*?)\]", lines[j])
                if needs_match:
                    raw = needs_match.group(1)
                    parts = re.findall(r"[\w-]+", raw.split("#")[0])
                    needs = parts
                j += 1
            jobs[job_name] = {"needs": needs, "line": i + 1}
        i += 1
    return jobs


class TestNoCircularDependencies:
    """A job MUST NOT depend on itself. This caused 6+ failed CI runs."""

    def test_no_job_depends_on_itself(self):
        jobs = _extract_jobs(_workflow_source())
        violations = []
        for name, info in jobs.items():
            if name in info["needs"]:
                violations.append(
                    f"  {name} (line {info['line']}): depends on itself"
                )
        assert not violations, (
            "CIRCULAR DEPENDENCY detected — these jobs depend on themselves:\n"
            + "\n".join(violations)
        )


class TestBuildJobsDoNotDependOnTestShard:
    """Build/release jobs must NOT wait for test-shard.

    unit-1a takes 60+ minutes on CI. When it's in the needs chain,
    artifact creation is blocked for an hour or cancelled by timeout.
    Tests are continue-on-error (informational) and must not gate releases.
    """

    BUILD_RELEASE_JOBS: typing.ClassVar[list[str]] = [
        "linux", "macos", "windows", "termux",
        "container", "release",
    ]

    def test_build_jobs_dont_need_test_shard(self):
        src = _workflow_source()
        jobs = _extract_jobs(src)
        violations = []
        for job_name in self.BUILD_RELEASE_JOBS:
            if job_name not in jobs:
                continue
            needs = jobs[job_name]["needs"]
            if "test-shard" in needs:
                violations.append(
                    f"  {job_name}: needs test-shard — "
                    f"this blocks artifact creation for 60+ min. "
                    f"Remove test-shard from needs (tests are continue-on-error)."
                )
        assert not violations, (
            "Build/release jobs must NOT depend on test-shard:\n"
            + "\n".join(violations)
        )


class TestReleaseJobHasVerificationSteps:
    """The release job MUST verify artifacts before publishing."""

    def test_release_job_exists(self):
        src = _workflow_source()
        assert re.search(r"^  release:\s*$", src, re.MULTILINE), (
            "release job must exist in build.yml"
        )

    def test_release_verifies_completeness(self):
        src = _workflow_source()
        assert "verify-release-completeness" in src or "verify_release_completeness" in src, (
            "release job must call verify-release-completeness as a blocking step"
        )

    def test_release_verifies_staged_assets(self):
        src = _workflow_source()
        assert "Verify staged assets" in src, (
            "release job must verify staged assets before publishing"
        )


class TestWorkflowYamlIsValid:
    """The workflow YAML must be parseable by GitHub Actions."""

    def test_no_bang_cancelled_outside_quotes(self):
        """!cancelled() outside quotes is parsed as YAML tag — causes 0s failure."""
        src = _workflow_source()
        for line in src.split("\n"):
            stripped = line.strip()
            if stripped.startswith("if:") and "!cancelled()" in stripped and not (
                stripped.startswith('if: "') or stripped.startswith("if: '")
            ):
                    pytest.fail(
                        f"!cancelled() in unquoted if: condition causes YAML parse failure: {stripped}"
                    )

    def test_timeout_is_generous(self):
        """test-shard timeout must be at least 60 minutes."""
        src = _workflow_source()
        m = re.search(r"timeout-minutes:\s*(\d+)", src)
        if m:
            all_timeouts = re.findall(r"timeout-minutes:\s*(\d+)", src)
            max_timeout = max(int(t) for t in all_timeouts)
            assert max_timeout >= 60, (
                f"Maximum timeout is {max_timeout} min — must be >= 60 "
                f"for slow CI runners"
            )


class TestNoJobExceedsMaxTimeout:
    """No job in build.yml may set timeout-minutes > 120.

    Excessive timeouts mask stuck/hung jobs: a job that legitimately needs
    >2h wall-clock is almost certainly doing something wrong (unbounded
    retry loop, waiting on a resource that will never arrive, OOM-thrashing).
    The 120-minute ceiling is the project policy; anything higher is a bug.

    Current maxima (as of CP.14):
      - test-shard: 120  (borderline OK — fattest shard runs ~18m on a slow
                          runner; 120m gives headroom without being excessive.
                          Durable follow-up: rebalance shards so the cap can
                          drop to 60.)
      - all other jobs: <= 30
    """

    MAX_TIMEOUT_MINUTES = 120

    def test_no_job_exceeds_max_timeout(self):
        import yaml

        src = _workflow_source()
        data = yaml.safe_load(src)
        jobs = data.get("jobs", {})
        assert jobs, "no jobs found in build.yml"

        violations: list[str] = []
        for job_name, job_spec in jobs.items():
            if not isinstance(job_spec, dict):
                continue
            timeout = job_spec.get("timeout-minutes")
            if timeout is None:
                # Missing timeout-minutes is a separate concern (GH default is
                # 360m); this test only enforces the upper bound on jobs that
                # DO set it.
                continue
            if timeout > self.MAX_TIMEOUT_MINUTES:
                violations.append(
                    f"  {job_name}: timeout-minutes={timeout} "
                    f"(max allowed={self.MAX_TIMEOUT_MINUTES})"
                )
        assert not violations, (
            "Jobs with excessive timeout-minutes (> "
            f"{self.MAX_TIMEOUT_MINUTES}m) — indicates a stuck/hung job:\n"
            + "\n".join(violations)
        )

    def test_all_jobs_have_explicit_timeout(self):
        """Every job should declare timeout-minutes (GH default is 360m)."""
        import yaml

        src = _workflow_source()
        data = yaml.safe_load(src)
        jobs = data.get("jobs", {})
        missing = [
            name for name, spec in jobs.items()
            if isinstance(spec, dict) and "timeout-minutes" not in spec
        ]
        assert not missing, (
            "Jobs missing explicit timeout-minutes (GH default 360m is "
            "excessive):\n  " + "\n  ".join(missing)
        )


class TestTestShardIsNonBlocking:
    """test-shard MUST have continue-on-error: true."""

    def test_continue_on_error_present(self):
        src = _workflow_source()
        # Find the test-shard job section
        idx = src.find("test-shard:")
        assert idx >= 0, "test-shard job must exist"
        section = src[idx:idx + 500]
        assert "continue-on-error: true" in section or 'continue-on-error: True' in section, (
            "test-shard must have continue-on-error: true so test failures "
            "don't block the release pipeline"
        )
