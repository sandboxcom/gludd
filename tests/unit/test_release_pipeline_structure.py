"""Structural tests that prevent release pipeline failures from recurring.

These tests verify:
1. No circular dependencies in the CI workflow job graph
2. Build jobs run in parallel while release waits for every prerequisite
3. The release job includes artifact verification steps
4. The workflow YAML is parseable
5. Test, Molecule, and platform artifact jobs fail closed
6. The release job downloads all build artifacts via pattern: gludd-*
7. The molecule and coverage jobs exist and are correctly wired
8. The test-shard matrix covers the full tests/unit/ letter range (a-z)
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


class TestReleaseFanInAndParallelBuilds:
    """Build jobs run in parallel while release waits for required tests."""

    PARALLEL_BUILD_JOBS: typing.ClassVar[list[str]] = [
        "linux",
        "macos",
        "windows",
        "termux",
        "container",
    ]

    def test_build_jobs_do_not_serialize_on_test_shard(self):
        jobs = _extract_jobs(_workflow_source())
        violations = [
            name
            for name in self.PARALLEL_BUILD_JOBS
            if "test-shard" in jobs.get(name, {}).get("needs", [])
        ]
        assert not violations, f"Build jobs unexpectedly serialize on test-shard: {violations}"

    def test_release_waits_for_test_shard(self):
        jobs = _extract_jobs(_workflow_source())
        assert "test-shard" in jobs["release"]["needs"]


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


class TestReleasePrerequisitesAreBlocking:
    """Required test and artifact producers never soften failures."""

    REQUIRED_JOBS: typing.ClassVar[list[str]] = [
        "test-shard",
        "molecule",
        "linux",
        "macos",
        "windows",
        "termux",
        "container",
    ]

    def test_required_jobs_do_not_continue_on_error(self):
        import yaml

        jobs = yaml.safe_load(_workflow_source()).get("jobs", {})
        violations = [
            name
            for name in self.REQUIRED_JOBS
            if not isinstance(jobs.get(name), dict)
            or jobs[name].get("continue-on-error", False) is not False
        ]
        assert not violations, (
            "Required jobs must fail closed; invalid jobs: " + ", ".join(violations)
        )


class TestReleaseDownloadsAllArtifacts:
    """The release job MUST download all build artifacts via pattern: gludd-*.

    Without merge-multiple: true and pattern: gludd-*, the release job would
    only see artifacts from one build job, silently dropping the others.
    The gludd-* prefix matches all named artifacts: gludd-linux-x86_64,
    gludd-macos-arm64, gludd-windows-x86_64, gludd-linux-aarch64.
    """

    def _release_section(self) -> str:
        src = _workflow_source()
        idx = src.find("  release:")
        assert idx >= 0, "release job must exist in build.yml"
        return src[idx:]

    def test_release_downloads_gludd_pattern(self):
        section = self._release_section()
        assert "pattern: gludd-*" in section, (
            "release job must download artifacts matching pattern: gludd-* "
            "to collect all platform build outputs into one staging directory"
        )

    def test_release_merges_multiple(self):
        section = self._release_section()
        assert "merge-multiple: true" in section, (
            "release job must set merge-multiple: true on download-artifact "
            "so all gludd-* artifacts land in a single directory rather than "
            "each in its own subdirectory"
        )


class TestMoleculeJobExists:
    """The molecule job MUST exist for ansible role/module testing.

    Molecule tests validate ansible content (roles, modules, playbooks)
    against a real-ish environment. Without this job, ansible regressions
    go undetected until production deployment.
    """

    def test_molecule_job_present(self):
        src = _workflow_source()
        assert re.search(r"^  molecule:\s*$", src, re.MULTILINE), (
            "molecule job must exist in build.yml for ansible role/module testing"
        )

    def test_molecule_is_blocking(self):
        import yaml

        jobs = yaml.safe_load(_workflow_source()).get("jobs", {})
        mol = jobs.get("molecule")
        assert isinstance(mol, dict), "molecule job must exist in build.yml"
        assert mol.get("continue-on-error", False) is False


class TestCoverageJobExists:
    """The coverage aggregation job MUST exist to merge per-shard coverage data.

    Each test-shard leg produces a .coverage SQLite data file. Without the
    coverage job to combine them, the published report understates real
    coverage by ~75% (only one shard's data would be visible).
    """

    def test_coverage_job_present(self):
        src = _workflow_source()
        assert re.search(r"^  coverage:\s*$", src, re.MULTILINE), (
            "coverage job must exist in build.yml to aggregate per-shard "
            "coverage data into a single canonical coverage.xml"
        )

    def test_coverage_needs_test_shard(self):
        import yaml

        src = _workflow_source()
        data = yaml.safe_load(src)
        jobs = data.get("jobs", {})
        cov = jobs.get("coverage")
        assert isinstance(cov, dict), "coverage job must exist in build.yml"
        needs = cov.get("needs", [])
        if isinstance(needs, str):
            needs = [needs]
        assert "test-shard" in needs, (
            "coverage job must depend on test-shard so it runs after all "
            "shards have produced their .coverage data files"
        )


class TestShardMatrixCoverage:
    """The test-shard matrix MUST cover the full tests/unit/ directory.

    Every test_* file under tests/unit/ starting with a letter a-z must be
    collected by exactly one shard. The current layout covers all 26 letters:
      unit-1a1: test_a[a-m]*    unit-1a2: test_a[n-z]* + test_a[0-9]*
      unit-1b:  test_[ce]*      unit-1d:  test_[bd]*
      unit-2:   test_[f-m]*     unit-3a:  test_[n-r]*
      unit-3b:  test_[s-z]* + secrets/
      other:    integration/, e2e/, connector*, _e2e.py rerouted files

    A gap in the letter coverage means tests silently stop running — the
    shard glob matches nothing, pytest exits 5 (no tests collected), and the
    shard is treated as a no-op pass.
    """

    EXPECTED_SHARDS: typing.ClassVar[list[str]] = [
        "unit-1a1", "unit-1a2", "unit-1b", "unit-1d",
        "unit-2", "unit-3a", "unit-3b", "other",
    ]

    def _matrix(self) -> dict:
        import yaml

        src = _workflow_source()
        data = yaml.safe_load(src)
        jobs = data.get("jobs", {})
        shard_job = jobs.get("test-shard")
        assert isinstance(shard_job, dict), "test-shard job must exist"
        matrix = shard_job.get("strategy", {}).get("matrix", {})
        assert isinstance(matrix, dict), "test-shard must have a strategy.matrix"
        return matrix

    def test_all_expected_shards_present(self):
        matrix = self._matrix()
        shard_list = matrix.get("shard", [])
        missing = [s for s in self.EXPECTED_SHARDS if s not in shard_list]
        assert not missing, (
            "test-shard matrix is missing expected shard(s): "
            + ", ".join(missing)
            + " — every letter range must have a dedicated shard"
        )

    def test_letter_ranges_cover_a_to_z(self):
        """Every letter a-z must appear in at least one shard's testpaths glob."""
        matrix = self._matrix()
        includes = matrix.get("include", [])
        covered: set[str] = set()
        for inc in includes:
            tp = inc.get("testpaths", "")
            # Range form: test_[f-m]* → expands f through m
            for m in re.finditer(r"test_\[([a-z])-([a-z])\]", tp):
                lo, hi = m.group(1), m.group(2)
                for code in range(ord(lo), ord(hi) + 1):
                    covered.add(chr(code))
            # Set form: test_[bd]* → adds b and d
            for m in re.finditer(r"test_\[([a-z]+)\]", tp):
                for ch in m.group(1):
                    covered.add(ch)
            # Prefix form: test_a[a-m]* → first letter is 'a'
            for m in re.finditer(r"test_([a-z])\[", tp):
                covered.add(m.group(1))
        all_letters = set(chr(c) for c in range(ord("a"), ord("z") + 1))
        missing = sorted(all_letters - covered)
        assert not missing, (
            "test-shard matrix does not cover letter(s): "
            + ", ".join(missing)
            + " — tests starting with these letters will never be collected"
        )
