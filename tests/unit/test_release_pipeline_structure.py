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


class JobInfo(typing.TypedDict):
    """The structural job fields extracted from the workflow source."""

    needs: list[str]
    line: int


def _workflow_source() -> str:
    assert BUILD_YML.exists(), f"build.yml not found at {BUILD_YML}"
    return BUILD_YML.read_text()


def _extract_jobs(src: str) -> dict[str, JobInfo]:
    """Extract job names and their needs from the workflow YAML."""
    jobs: dict[str, JobInfo] = {}
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

    def test_no_job_depends_on_itself(self) -> None:
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

    def test_build_jobs_do_not_serialize_on_test_shard(self) -> None:
        jobs = _extract_jobs(_workflow_source())
        violations = [
            name
            for name in self.PARALLEL_BUILD_JOBS
            if "test-shard" in jobs.get(name, {"needs": [], "line": 0})["needs"]
        ]
        assert not violations, f"Build jobs unexpectedly serialize on test-shard: {violations}"

    def test_release_waits_for_test_shard(self) -> None:
        jobs = _extract_jobs(_workflow_source())
        assert "test-shard" in jobs["release"]["needs"]


class TestReleaseJobHasVerificationSteps:
    """The release job MUST verify artifacts before publishing."""

    def test_release_job_exists(self) -> None:
        src = _workflow_source()
        assert re.search(r"^  release:\s*$", src, re.MULTILINE), (
            "release job must exist in build.yml"
        )

    def test_release_verifies_completeness(self) -> None:
        src = _workflow_source()
        assert "verify-release-completeness" in src or "verify_release_completeness" in src, (
            "release job must call verify-release-completeness as a blocking step"
        )

    def test_release_verifies_staged_assets(self) -> None:
        src = _workflow_source()
        assert "Verify staged assets" in src, (
            "release job must verify staged assets before publishing"
        )


class TestWorkflowYamlIsValid:
    """The workflow YAML must be parseable by GitHub Actions."""

    def test_no_bang_cancelled_outside_quotes(self) -> None:
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

    def test_timeout_is_generous(self) -> None:
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

    def test_no_job_exceeds_max_timeout(self) -> None:
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

    def test_all_jobs_have_explicit_timeout(self) -> None:
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

    def test_required_jobs_do_not_continue_on_error(self) -> None:
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

    def test_release_downloads_gludd_pattern(self) -> None:
        section = self._release_section()
        assert "pattern: gludd-*" in section, (
            "release job must download artifacts matching pattern: gludd-* "
            "to collect all platform build outputs into one staging directory"
        )

    def test_release_merges_multiple(self) -> None:
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

    def test_molecule_job_present(self) -> None:
        src = _workflow_source()
        assert re.search(r"^  molecule:\s*$", src, re.MULTILINE), (
            "molecule job must exist in build.yml for ansible role/module testing"
        )

    def test_molecule_is_blocking(self) -> None:
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

    def test_coverage_job_present(self) -> None:
        src = _workflow_source()
        assert re.search(r"^  coverage:\s*$", src, re.MULTILINE), (
            "coverage job must exist in build.yml to aggregate per-shard "
            "coverage data into a single canonical coverage.xml"
        )

    def test_coverage_needs_test_shard(self) -> None:
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
    """The canonical shard registry MUST cover the full tests/unit/ directory.

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

    def _matrix(self) -> dict[str, object]:
        import yaml

        src = _workflow_source()
        data = yaml.safe_load(src)
        jobs = data.get("jobs", {})
        shard_job = jobs.get("test-shard")
        assert isinstance(shard_job, dict), "test-shard job must exist"
        matrix = shard_job.get("strategy", {}).get("matrix", {})
        assert isinstance(matrix, dict), "test-shard must have a strategy.matrix"
        return matrix

    def test_all_expected_shards_present(self) -> None:
        matrix = self._matrix()
        shard_list = matrix.get("shard", [])
        assert isinstance(shard_list, list), "matrix.shard must be a list"
        missing = [s for s in self.EXPECTED_SHARDS if s not in shard_list]
        assert not missing, (
            "test-shard matrix is missing expected shard(s): "
            + ", ".join(missing)
            + " — every letter range must have a dedicated shard"
        )

    def test_letter_ranges_cover_a_to_z(self) -> None:
        """Every top-level lettered unit test belongs to the canonical plan."""
        if typing.TYPE_CHECKING:
            from ci_named_shard_files import ISOLATED_TESTS, SHARDS, expand_shard
        else:
            from scripts.ci_named_shard_files import ISOLATED_TESTS, SHARDS, expand_shard

        selected = {
            token
            for shard in SHARDS
            for token in expand_shard(shard)
            if token.startswith("tests/unit/test_")
        }
        selected.update(ISOLATED_TESTS)
        unit_root = ROOT / "tests" / "unit"
        expected = {
            path.relative_to(ROOT).as_posix()
            for path in unit_root.glob("test_[a-z]*.py")
        }
        missing = sorted(expected - selected)
        assert not missing, (
            "canonical shard registry does not cover unit test(s): "
            + ", ".join(missing)
        )


class TestReleaseCandidateHostedValidation:
    """Candidate pushes must use native CI without entering publication jobs."""

    @staticmethod
    def _mapping(value: object) -> dict[object, object]:
        assert isinstance(value, dict), "expected a workflow mapping"
        result: dict[object, object] = {}
        for key, member in value.items():
            result[key] = member
        return result

    @staticmethod
    def _strings(value: object) -> list[str]:
        assert isinstance(value, list), "expected a workflow string list"
        result: list[str] = []
        for member in value:
            assert isinstance(member, str), "expected a workflow string"
            result.append(member)
        return result

    def _workflow(self, filename: str) -> dict[object, object]:
        import yaml

        payload = (ROOT / ".github" / "workflows" / filename).read_text(
            encoding="utf-8"
        )
        document: object = yaml.load(
            payload, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader)
        )
        return self._mapping(document)

    def _events(self, workflow: dict[object, object]) -> dict[object, object]:
        # PyYAML's YAML 1.1 loader may decode the Actions key "on" as True.
        return self._mapping(workflow.get("on", workflow.get(True)))

    def _steps(self, job: dict[object, object]) -> list[dict[object, object]]:
        value: object = job["steps"]
        assert isinstance(value, list), "expected a workflow step list"
        return [self._mapping(step) for step in value]

    @pytest.mark.parametrize(
        (
            "hosted_workflow_filename",
            "hosted_push_branches",
            "hosted_pr_branches",
        ),
        [
            (
                "build.yml",
                ["development", "master", "main"],
                ["master", "main", "development"],
            ),
            (
                "molecule.yml",
                ["master", "main", "development"],
                ["master", "main", "development"],
            ),
            ("pages.yml", ["master", "development"], ["master", "development"]),
        ],
        ids=("build", "molecule", "pages"),
    )
    def test_candidate_push_filters_preserve_existing_routes(
        self,
        hosted_workflow_filename: str,
        hosted_push_branches: list[str],
        hosted_pr_branches: list[str],
    ) -> None:
        events = self._events(self._workflow(hosted_workflow_filename))
        push = self._mapping(events["push"])
        pull_request = self._mapping(events["pull_request"])
        assert self._strings(push["branches"]) == [
            *hosted_push_branches,
            "release-candidate/**",
        ]
        assert "branches-ignore" not in push
        assert self._strings(pull_request["branches"]) == hosted_pr_branches
        assert "workflow_dispatch" in events

    @pytest.mark.parametrize(
        ("pr_target_branch", "expected_premerge"),
        [
            ("master", True),
            ("main", True),
            ("development", True),
            ("feature/example", False),
            ("release-candidate/v012-example", False),
            ("development-copy", False),
            ("develop", False),
        ],
    )
    def test_build_pr_filters_match_literal_target_branches(
        self, pr_target_branch: str, expected_premerge: bool
    ) -> None:
        """PR base-branch validation remains separate from push release proof."""
        events = self._events(self._workflow("build.yml"))
        pull_request = self._mapping(events["pull_request"])
        branches = self._strings(pull_request["branches"])

        assert not {"branches-ignore", "paths", "paths-ignore"}.intersection(pull_request)
        assert all(re.fullmatch(r"[A-Za-z0-9_/-]+", branch) for branch in branches)
        assert (pr_target_branch in branches) is expected_premerge

    def _molecule_group(
        self, event_name: str, git_ref: str, commit_sha: str
    ) -> str:
        """Render the pinned source grammar, without modeling GitHub scheduling."""
        workflow = self._workflow("molecule.yml")
        concurrency = self._mapping(workflow["concurrency"])
        group = concurrency["group"]
        assert isinstance(group, str)
        selector = "${{ github.event_name == 'pull_request' && github.ref || github.sha }}"
        rendered = group.replace(
            selector, git_ref if event_name == "pull_request" else commit_sha
        )
        for name, value in {
            "github.workflow": "Molecule Tests",
            "github.event_name": event_name,
            "github.ref": git_ref,
            "github.sha": commit_sha,
        }.items():
            rendered = rendered.replace("${{ " + name + " }}", value)
        assert "${{" not in rendered, "unrecognized concurrency expression"
        return rendered

    def test_molecule_concurrency_preserves_sha_ref_and_event_inputs(self) -> None:
        concurrency = self._mapping(self._workflow("molecule.yml")["concurrency"])

        assert concurrency == {
            "group": (
                "${{ github.workflow }}-"
                "${{ github.event_name == 'pull_request' && github.ref || github.sha }}-"
                "${{ github.ref }}-${{ github.event_name }}"
            ),
            "cancel-in-progress": "${{ github.event_name == 'pull_request' }}",
        }

    @pytest.mark.parametrize(
        "concurrency_pair",
        [
            (
                "push", "refs/heads/development", "a" * 40,
                "push", "refs/heads/development", "b" * 40, False,
            ),
            (
                "push", "refs/heads/release-candidate/v012", "a" * 40,
                "push", "refs/heads/release-candidate/v012", "b" * 40, False,
            ),
            (
                "workflow_dispatch", "refs/heads/development", "a" * 40,
                "workflow_dispatch", "refs/heads/development", "b" * 40, False,
            ),
            (
                "pull_request", "refs/pull/42/merge", "a" * 40,
                "pull_request", "refs/pull/42/merge", "b" * 40, True,
            ),
            (
                "pull_request", "refs/pull/42/merge", "a" * 40,
                "pull_request", "refs/pull/43/merge", "a" * 40, False,
            ),
            (
                "push", "refs/heads/development", "a" * 40,
                "workflow_dispatch", "refs/heads/development", "a" * 40, False,
            ),
            (
                "push", "refs/heads/development", "a" * 40,
                "push", "refs/heads/master", "a" * 40, False,
            ),
            (
                "push", "refs/heads/development", "a" * 40,
                "push", "refs/heads/development", "a" * 40, True,
            ),
        ],
        ids=(
            "development-push-shas",
            "candidate-push-shas",
            "dispatch-shas",
            "same-pr-coalesces",
            "different-prs",
            "push-and-dispatch",
            "same-sha-different-refs",
            "same-source-repeat",
        ),
    )
    def test_molecule_concurrency_pair_uses_actual_workflow_group(
        self, concurrency_pair: tuple[str, str, str, str, str, str, bool]
    ) -> None:
        first_event, first_ref, first_sha, second_event, second_ref, second_sha, shared = (
            concurrency_pair
        )

        first = self._molecule_group(first_event, first_ref, first_sha)
        second = self._molecule_group(second_event, second_ref, second_sha)

        assert (first == second) is shared

    def test_molecule_concurrency_retains_native_job_limits_and_permissions(self) -> None:
        workflow = self._workflow("molecule.yml")
        jobs = self._mapping(workflow["jobs"])
        molecule = self._mapping(jobs["molecule"])
        strategy = self._mapping(molecule["strategy"])

        assert workflow["permissions"] == {"contents": "read"}
        assert molecule["timeout-minutes"] == 45
        assert strategy == {
            "fail-fast": False,
            "max-parallel": 3,
            "matrix": {"shard": [1, 2, 3, 4, 5, 6]},
        }
        assert self._mapping(molecule["env"])["SHARD_COUNT"] == 6

    def test_pages_candidate_keeps_presentation_path_filter(self) -> None:
        events = self._events(self._workflow("pages.yml"))
        push = self._mapping(events["push"])
        pull_request = self._mapping(events["pull_request"])
        paths = self._strings(push["paths"])
        assert paths == self._strings(pull_request["paths"])
        assert "make/**" in paths
        assert ".github/workflows/pages.yml" in paths
        assert "docs/presentation/**" in paths
        assert "*" not in paths and "**" not in paths

    def test_candidate_keeps_native_publication_guards(self) -> None:
        jobs = self._mapping(self._workflow("build.yml")["jobs"])
        release = self._mapping(jobs["release"])
        condition: object = release["if"]
        assert isinstance(condition, str), "expected a release condition"
        assert condition.startswith("startsWith(github.ref, 'refs/tags/v') &&")
        publish = next(
            step
            for step in self._steps(self._mapping(jobs["container"]))
            if step.get("name") == "Publish verified container image"
        )
        assert publish["if"] == (
            "github.event_name != 'pull_request' && "
            "(startsWith(github.ref, 'refs/tags/v') || "
            "github.ref == 'refs/heads/master')"
        )
        ee_push = next(
            step
            for step in self._steps(self._mapping(jobs["ansible-ee"]))
            if step.get("name") == "Push tagged execution environment"
        )
        assert ee_push["if"] == "startsWith(github.ref, 'refs/tags/v')"
        pages_jobs = self._mapping(self._workflow("pages.yml")["jobs"])
        pages = self._mapping(pages_jobs["deploy"])
        assert pages["if"] == (
            "github.event_name == 'push' && github.ref == 'refs/heads/master'"
        )
        assert self._strings(pages["needs"]) == ["validate", "javascript-coverage"]

    def test_candidate_retains_exact_checkout_and_python_scope(self) -> None:
        jobs = self._mapping(self._workflow("build.yml")["jobs"])
        gate = self._mapping(jobs["gate"])
        gate_strategy = self._mapping(gate["strategy"])
        gate_matrix = self._mapping(gate_strategy["matrix"])
        assert self._strings(gate_matrix["python-version"]) == ["3.11", "3.12"]
        shard = self._mapping(jobs["test-shard"])
        shard_strategy = self._mapping(shard["strategy"])
        shard_matrix = self._mapping(shard_strategy["matrix"])
        assert self._strings(shard_matrix["python-version"]) == ["3.11"]
        for name in ("gate", "test-shard"):
            checkouts: list[dict[object, object]] = []
            for step in self._steps(self._mapping(jobs[name])):
                uses: object = step.get("uses")
                if isinstance(uses, str) and uses.startswith("actions/checkout@"):
                    checkouts.append(step)
            assert len(checkouts) == 1
            checkout_with = self._mapping(checkouts[0]["with"])
            assert checkout_with["ref"] == "${{ github.sha }}"
