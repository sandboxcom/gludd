"""Deep structural tests for .github/workflows/build.yml integrity."""

from __future__ import annotations

import json
import pathlib
import shlex
from copy import deepcopy
from typing import TYPE_CHECKING, Any

import pytest
import yaml

WORKFLOW_PATH = pathlib.Path(__file__).parent.parent.parent / ".github" / "workflows" / "build.yml"
Workflow = dict[object, Any]


def _load_workflow() -> Workflow:
    content = WORKFLOW_PATH.read_text()
    parsed: Workflow = yaml.load(content, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
    return parsed


def _extract_step_names(steps: list[Workflow]) -> set[str]:
    names: set[str] = set()
    for step in steps:
        if isinstance(step, dict) and "name" in step:
            names.add(step["name"])
    return names


def _collect_job_refs(needs_value: object) -> set[str]:
    if isinstance(needs_value, str):
        return {needs_value}
    if isinstance(needs_value, list):
        return {str(value) for value in needs_value}
    return set()


class TestAllNeedsResolveToRealJobs:
    def test_all_needs_references_real_jobs(self) -> None:
        wf = _load_workflow()
        jobs = wf.get("jobs", {})
        missing: list[tuple[str, str]] = []
        for job_name, job_def in jobs.items():
            needs = job_def.get("needs", [])
            for dep in _collect_job_refs(needs):
                if dep not in jobs:
                    missing.append((job_name, dep))
        assert not missing, "Jobs reference non-existent dependencies: " + "; ".join(
            f"{j} needs {d}" for j, d in missing
        )


class TestNoCircularDependencies:
    def test_no_circular_dependencies(self) -> None:
        wf = _load_workflow()
        jobs = wf.get("jobs", {})
        graph: dict[str, set[str]] = {}
        for name, defn in jobs.items():
            graph[name] = _collect_job_refs(defn.get("needs", []))

        visiting: set[str] = set()
        visited: set[str] = set()

        def dfs(node: str) -> bool:
            if node in visiting:
                return True
            if node in visited:
                return False
            visiting.add(node)
            for dep in graph.get(node, set()):
                if dfs(dep):
                    return True
            visiting.discard(node)
            visited.add(node)
            return False

        for job_name in graph:
            assert not dfs(job_name), f"Circular dependency detected starting from {job_name}"


class TestVersionIsRootJob:
    def test_version_has_no_needs(self) -> None:
        wf = _load_workflow()
        version = wf["jobs"].get("version", {})
        needs = version.get("needs")
        assert needs is None or needs == [], f"version job should have no needs, got {needs!r}"

    def test_version_produces_outputs(self) -> None:
        wf = _load_workflow()
        version = wf["jobs"]["version"]
        assert "outputs" in version
        assert "version" in version["outputs"]

    def test_version_has_timeout(self) -> None:
        wf = _load_workflow()
        version = wf["jobs"]["version"]
        assert "timeout-minutes" in version
        assert version["timeout-minutes"] > 0


def test_hosted_runtime_enables_native_trace_with_unchanged_command_and_limits() -> None:
    steps = _load_workflow()["jobs"]["gate"]["steps"]
    runtime = next(step for step in steps if step.get("name") == "Known runtime and handoff regression admission")
    words = shlex.split(runtime["run"])
    assert "RUN_WATCHED_PYTEST_TRACE=1" in words
    assert "CMD=make _pytest-runtime-compatibility-admission" in words
    assert "STALL_SECS=60" in words and "MAX_SECS=90" in words
    assert "OBSERVED_LABEL=ci-runtime-regression-admission" in words
    identity = "RUN_ID=runtime-admission-${{ github.run_id }}-${{ github.run_attempt }}-${{ matrix.python-version }}"
    assert identity in words


def test_hosted_runtime_archives_only_its_terminal_native_evidence() -> None:
    steps = _load_workflow()["jobs"]["gate"]["steps"]
    runtime_index = next(
        index for index, step in enumerate(steps)
        if step.get("name") == "Known runtime and handoff regression admission"
    )
    runtime = steps[runtime_index]
    assert runtime.get("id") == "runtime_regression_admission"
    archive = steps[runtime_index + 1]
    assert archive.get("id") == "runtime_diagnostic_archive"
    archive_words = shlex.split(archive["run"], comments=True)
    assert archive_words[:3] == ["make", "--no-print-directory", "runtime-startup-archive"]
    assert "PYTHON=$GITHUB_WORKSPACE/.venv/bin/python3" in archive_words
    assert "SYSTEM_PYTHON=$GITHUB_WORKSPACE/.venv/bin/python3" in archive_words
    assert archive["if"] == (
        "${{ always() && (steps.runtime_regression_admission.outcome == 'success' || "
        "steps.runtime_regression_admission.outcome == 'failure') }}"
    )
    environment = archive["env"]
    assert environment["RUNTIME_DIAGNOSTIC_VALIDATE_ONLY"] == "0"
    assert environment["OBSERVED_ROOT"] == ".gate-logs/observed"
    assert environment["OBSERVED_LABEL"] == "ci-runtime-regression-admission"
    assert environment["RUN_ID"] == (
        "runtime-admission-${{ github.run_id }}-${{ github.run_attempt }}-${{ matrix.python-version }}"
    )
    assert environment["RUNTIME_DIAGNOSTIC_SOURCE_SHA"] == "${{ github.sha }}"
    assert environment["RUNTIME_DIAGNOSTIC_ARCHIVE"] == (
        "/tmp/gludd-ci-runtime-admission-${{ github.run_id }}-${{ github.run_attempt }}-${{ matrix.python-version }}"
    )
    assert not archive.get("continue-on-error", False)


def test_hosted_runtime_upload_requires_the_successful_native_archive() -> None:
    steps = _load_workflow()["jobs"]["gate"]["steps"]
    archive = next((step for step in steps if step.get("id") == "runtime_diagnostic_archive"), None)
    assert archive is not None, "the native archive must own evidence before any upload"
    position = steps.index(archive)
    summary, upload = steps[position + 1:position + 3]
    condition = "${{ always() && steps.runtime_diagnostic_archive.outcome == 'success' }}"
    assert summary["if"] == upload["if"] == condition
    words = shlex.split(summary["run"], comments=True)
    assert words[:3] == ["make", "--no-print-directory", "test-xdist-trace-summary"]
    assert "PYTHON=$GITHUB_WORKSPACE/.venv/bin/python3" in words
    assert "SYSTEM_PYTHON=$GITHUB_WORKSPACE/.venv/bin/python3" in words
    path = archive["env"]["RUNTIME_DIAGNOSTIC_ARCHIVE"]
    assert summary["env"]["LOG"] == f"{path}/trace.raw"
    assert summary["env"]["RUN_ID"] == archive["env"]["RUN_ID"]
    assert summary["env"]["TRACE_SUMMARY_TIMINGS"] == summary["env"]["TRACE_SUMMARY_MEMORY"] == "1"
    assert upload["uses"] == "actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f"
    options = upload["with"]
    assert options["path"] == path
    assert options["if-no-files-found"] == "error"
    assert options["retention-days"] == 14
    assert options["include-hidden-files"] is False
    assert options["name"] == (
        "runtime-admission-${{ github.run_id }}-${{ github.run_attempt }}-"
        "${{ matrix.python-version }}-${{ github.sha }}"
    )
    assert steps[position + 3]["name"] == "Inject version"
    assert not summary.get("continue-on-error", False) and not upload.get("continue-on-error", False)


class TestGateJobStructure:
    def test_gate_needs_version(self) -> None:
        wf = _load_workflow()
        gate = wf["jobs"]["gate"]
        assert _collect_job_refs(gate.get("needs", [])) == {"version"}

    def test_gate_has_matrix(self) -> None:
        wf = _load_workflow()
        gate = wf["jobs"]["gate"]
        matrix = gate.get("strategy", {}).get("matrix", {})
        python_versions = matrix.get("python-version", [])
        assert len(python_versions) >= 2, f"gate matrix should have ≥2 Python versions, got {python_versions}"

    def test_gate_has_timeout(self) -> None:
        wf = _load_workflow()
        gate = wf["jobs"]["gate"]
        assert "timeout-minutes" in gate
        assert gate["timeout-minutes"] > 0

    def test_gate_matrix_fail_fast_is_false(self) -> None:
        wf = _load_workflow()
        gate = wf["jobs"]["gate"]
        strategy = gate.get("strategy", {})
        assert strategy.get("fail-fast") is False, "gate fail-fast should be False to get both Python version results"


class TestTestShardStructure:
    def test_test_shard_needs_version_and_gate(self) -> None:
        wf = _load_workflow()
        test_shard = wf["jobs"]["test-shard"]
        assert _collect_job_refs(test_shard.get("needs", [])) == {"version", "gate"}

    def test_test_shard_matrix_has_all_entries(self) -> None:
        wf = _load_workflow()
        shard = wf["jobs"]["test-shard"]
        names = set(shard.get("strategy", {}).get("matrix", {}).get("shard", []))
        expected = {
            "unit-1a1",
            "unit-1a2",
            "unit-1b",
            "unit-1d",
            "unit-2",
            "unit-3a",
            "unit-3b",
            "other",
        }
        assert names == expected, f"Expected shards {expected!r}, got {names!r}"

    def test_hosted_matrix_prioritizes_long_lanes_without_extra_runners(self) -> None:
        strategy = _load_workflow()["jobs"]["test-shard"]["strategy"]

        assert strategy["max-parallel"] == 3
        assert strategy["matrix"]["python-version"] == ["3.11"]
        assert strategy["matrix"]["shard"] == [
            "unit-2",
            "other",
            "unit-3a",
            "unit-3b",
            "unit-1b",
            "unit-1d",
            "unit-1a2",
            "unit-1a1",
        ]

    def test_oversized_unit_3_is_partitioned_without_overlap(self) -> None:
        if TYPE_CHECKING:
            from ci_named_shard_files import SHARDS
        else:
            from scripts.ci_named_shard_files import SHARDS

        assert SHARDS["unit-3a"][0] == ("tests/unit/test_[n-r]*.py",)
        assert SHARDS["unit-3b"][0] == (
            "tests/unit/test_[s-z]*.py",
            "tests/unit/secrets/",
        )

    def test_each_unit_shard_has_a_bounded_hosted_step_budget(self) -> None:
        wf = _load_workflow()
        job = wf["jobs"]["test-shard"]
        steps = wf["jobs"]["test-shard"]["steps"]
        test_step = next(step for step in steps if str(step.get("name", "")).startswith("Test (shard"))

        assert test_step["timeout-minutes"] >= 90
        assert test_step["timeout-minutes"] <= job["timeout-minutes"] - 15

    def test_workflow_does_not_duplicate_canonical_testpaths(self) -> None:
        wf = _load_workflow()
        shard = wf["jobs"]["test-shard"]
        include = shard.get("strategy", {}).get("matrix", {}).get("include", [])
        for entry in include:
            assert "testpaths" not in entry
            assert "exclude" not in entry

    def test_test_shard_has_timeout(self) -> None:
        wf = _load_workflow()
        test_shard = wf["jobs"]["test-shard"]
        assert "timeout-minutes" in test_shard

    def test_test_shard_fail_fast_is_false(self) -> None:
        wf = _load_workflow()
        test_shard = wf["jobs"]["test-shard"]
        strategy = test_shard.get("strategy", {})
        assert strategy.get("fail-fast") is False


class TestCoverageJobStructure:
    def test_coverage_needs_version_and_test_shard(self) -> None:
        wf = _load_workflow()
        coverage = wf["jobs"]["coverage"]
        assert _collect_job_refs(coverage.get("needs", [])) == {"version", "test-shard"}

    def test_coverage_has_timeout(self) -> None:
        wf = _load_workflow()
        coverage = wf["jobs"]["coverage"]
        assert "timeout-minutes" in coverage


class TestGameBuildingJobStructure:
    def test_game_building_installs_locked_media_extra_before_tests(self) -> None:
        wf = _load_workflow()
        steps = wf["jobs"]["game-building"]["steps"]
        commands = [str(step.get("run", "")) for step in steps if isinstance(step, dict)]
        command = next(command for command in commands if "make test-games" in command)

        profile_sync = "scripts/dependency_profiles.py sync --set ci-game-e2e"
        assert profile_sync in command
        assert command.index(profile_sync) < command.index(
            "make test-games"
        )


class TestPlatformBuildJobStructure:
    PLATFORM_JOBS: tuple[str, ...] = ("linux", "macos", "windows", "termux")

    def test_all_platform_jobs_exist(self) -> None:
        wf = _load_workflow()
        for job_name in self.PLATFORM_JOBS:
            assert job_name in wf["jobs"], f"Missing platform build job: {job_name}"

    def test_platform_jobs_need_version_and_gate(self) -> None:
        wf = _load_workflow()
        for job_name in self.PLATFORM_JOBS:
            job = wf["jobs"][job_name]
            assert _collect_job_refs(job.get("needs", [])) == {"version", "gate"}, (
                f"{job_name} needs {_collect_job_refs(job.get('needs', []))}"
            )

    def test_platform_jobs_have_timeout(self) -> None:
        wf = _load_workflow()
        for job_name in self.PLATFORM_JOBS:
            job = wf["jobs"][job_name]
            assert "timeout-minutes" in job, f"{job_name} missing timeout-minutes"

    def test_platform_jobs_have_runs_on(self) -> None:
        wf = _load_workflow()
        for job_name in self.PLATFORM_JOBS:
            job = wf["jobs"][job_name]
            assert "runs-on" in job, f"{job_name} missing runs-on"


class TestReleaseJobStructure:
    PLATFORM_JOBS: tuple[str, ...] = ("linux", "macos", "windows", "termux")
    RELEASE_PREREQUISITES: frozenset[str] = frozenset(
        {
            "version",
            "gate",
            "claim-before-provision-acceptance",
            "postgres-winner-compute-lifecycle-acceptance",
            "release_source_proof",
            "freellmapi-upstream-build",
            "test-shard",
            "coverage",
            "molecule",
            "container",
            "ansible-ee",
            "game-building",
            *PLATFORM_JOBS,
        }
    )

    def test_release_needs_gate_tests_and_all_artifact_producers(self) -> None:
        wf = _load_workflow()
        release = wf["jobs"]["release"]
        needs = _collect_job_refs(release.get("needs", []))
        assert needs == self.RELEASE_PREREQUISITES, (
            f"release needs {needs!r}, expected {self.RELEASE_PREREQUISITES!r}"
        )

    def test_release_only_runs_on_tags(self) -> None:
        wf = _load_workflow()
        release = wf["jobs"]["release"]
        condition = release.get("if", "")
        assert "refs/tags/v" in condition

    def test_release_has_timeout(self) -> None:
        wf = _load_workflow()
        release = wf["jobs"]["release"]
        assert "timeout-minutes" in release


class TestReleaseSourceEvidenceReuse:
    """A tag may reuse only a successful development run for its exact SHA."""

    HEAVY_VALIDATION_JOBS: tuple[str, ...] = (
        "freellmapi-upstream-build",
        "test-shard",
        "coverage",
        "molecule",
        "game-building",
    )
    TAG_ARTIFACT_JOBS: tuple[str, ...] = (
        "linux",
        "macos",
        "windows",
        "termux",
        "container",
        "ansible-ee",
    )

    def test_tag_source_proof_is_exact_sha_and_development_scoped(self) -> None:
        wf = _load_workflow()
        proof = wf["jobs"]["release_source_proof"]

        assert _collect_job_refs(proof.get("needs", [])) == {"version"}
        assert "startsWith(github.ref, 'refs/tags/v')" in str(proof.get("if", ""))
        checkout = next(
            step
            for step in proof["steps"]
            if str(step.get("uses", "")).startswith("actions/checkout@")
        )
        assert checkout["with"]["ref"] == "${{ github.sha }}"
        commands = "\n".join(str(step.get("run", "")) for step in proof["steps"])
        assert 'python scripts/require_ci_green.py "$GITHUB_SHA" development' in commands
        proof_step = next(step for step in proof["steps"] if "require_ci_green.py" in str(step.get("run", "")))
        assert proof_step["env"]["GH_TOKEN"] == "${{ github.token }}"

    def test_tag_skips_only_validation_already_proved_for_exact_sha(self) -> None:
        wf = _load_workflow()
        jobs = wf["jobs"]

        for name in ("freellmapi-upstream-build", "test-shard", "molecule"):
            assert "!startsWith(github.ref, 'refs/tags/v')" in str(jobs[name].get("if", ""))
        assert _collect_job_refs(jobs["coverage"].get("needs", [])) == {
            "version",
            "test-shard",
        }
        assert "!startsWith(github.ref, 'refs/tags/v')" in str(
            jobs["game-building"].get("if", "")
        )

        for name in self.TAG_ARTIFACT_JOBS:
            condition = str(jobs[name].get("if", ""))
            assert "startsWith(github.ref, 'refs/tags/v')" in condition
            checkout = next(
                step
                for step in jobs[name]["steps"]
                if str(step.get("uses", "")).startswith("actions/checkout@")
            )
            assert checkout["with"]["ref"] == "${{ github.sha }}"

    def test_release_fails_closed_on_proof_or_dependency_result_drift(self) -> None:
        wf = _load_workflow()
        release = wf["jobs"]["release"]
        condition = str(release.get("if", ""))

        assert "!cancelled()" in condition
        assert "needs.release_source_proof.result == 'success'" in condition
        assert "needs.gate.result == 'success'" in condition
        for name in self.HEAVY_VALIDATION_JOBS:
            expression = (
                f"needs.{name}.result" if "-" not in name else f"needs['{name}'].result"
            )
            assert f"{expression} == 'skipped'" in condition
        for name in self.TAG_ARTIFACT_JOBS:
            expression = (
                f"needs.{name}.result" if "-" not in name else f"needs['{name}'].result"
            )
            assert f"{expression} == 'success'" in condition

    def test_feature_doc_pins_practitioner_evidence_zdd_and_measured_bound(self) -> None:
        text = (
            WORKFLOW_PATH.parents[2]
            / "docs"
            / "features"
            / "beta4-dual-track-ci"
            / "exact-sha-promotion.md"
        ).read_text(encoding="utf-8")

        for required in (
            "Exact-SHA tag validation reuse",
            "GitHub Community discussion #27031",
            "2021-07-02",
            "GitHub Community discussion #44396",
            "2023-01-15",
            "75-minute reduction",
            "16 duplicate hosted jobs",
            "ZDD",
            "Rollback",
        ):
            assert required in text


class TestNoDuplicateJobNames:
    def test_no_duplicate_job_names(self) -> None:
        wf = _load_workflow()
        jobs = wf.get("jobs", {})
        assert len(jobs) == len(set(jobs)), "Duplicate job names found"


class TestEveryJobHasTimeout:
    def test_every_job_has_timeout_minutes(self) -> None:
        wf = _load_workflow()
        missing: list[str] = []
        for job_name, job_def in wf["jobs"].items():
            if "timeout-minutes" not in job_def:
                missing.append(job_name)
        assert not missing, f"Jobs missing timeout-minutes: {', '.join(sorted(missing))}"


class TestEveryJobHasRunsOn:
    def test_every_job_has_runs_on(self) -> None:
        wf = _load_workflow()
        missing: list[str] = []
        for job_name, job_def in wf["jobs"].items():
            if "runs-on" not in job_def:
                missing.append(job_name)
        assert not missing, f"Jobs missing runs-on: {', '.join(sorted(missing))}"


class TestAllJobsReachableFromVersion:
    def test_all_jobs_reachable_from_version_or_root(self) -> None:
        wf = _load_workflow()
        jobs = wf["jobs"]
        reachable: set[str] = set()

        roots = [name for name, defn in jobs.items() if not defn.get("needs")]
        reachable.update(roots)

        changed = True
        while changed:
            changed = False
            for name, defn in jobs.items():
                if name in reachable:
                    continue
                needs = _collect_job_refs(defn.get("needs", []))
                if needs and needs.issubset(reachable):
                    reachable.add(name)
                    changed = True

        unreachable = set(jobs) - reachable
        assert not unreachable, f"Jobs unreachable from any root: {sorted(unreachable)}"


class TestWorkflowLevelStructure:
    def test_concurrency_group_is_defined(self) -> None:
        wf = _load_workflow()
        assert "concurrency" in wf
        concurrency = wf["concurrency"]
        assert "group" in concurrency, "concurrency group missing"

    def test_permissions_defined(self) -> None:
        wf = _load_workflow()
        assert "permissions" in wf
        perms = wf["permissions"]
        assert "contents" in perms
        assert "packages" in perms

    def test_on_triggers_include_push_and_pr(self) -> None:
        wf = _load_workflow()
        on = wf.get("on", wf.get(True, {}))
        assert isinstance(on, dict), "workflow triggers must be a mapping"
        assert "push" in on
        assert "pull_request" in on

    def test_on_tag_pattern_is_v_star(self) -> None:
        wf = _load_workflow()
        on = wf.get("on", wf.get(True, {}))
        assert isinstance(on, dict), "workflow triggers must be a mapping"
        push = on.get("push", {})
        assert isinstance(push, dict), "workflow push trigger must be a mapping"
        tags = push.get("tags", [])
        assert isinstance(tags, list), "workflow push tags must be a list"
        assert "v*" in tags, f"Expected 'v*' in tag triggers, got {tags!r}"


class TestMoleculeJobStructure:
    def test_molecule_needs_version_and_gate(self) -> None:
        wf = _load_workflow()
        molecule = wf["jobs"]["molecule"]
        assert _collect_job_refs(molecule.get("needs", [])) == {"version", "gate"}

    def test_molecule_has_timeout(self) -> None:
        wf = _load_workflow()
        molecule = wf["jobs"]["molecule"]
        assert "timeout-minutes" in molecule

    def test_molecule_timeout_covers_two_bounded_attempts(self) -> None:
        wf = _load_workflow()
        timeout = wf["jobs"]["molecule"]["timeout-minutes"]

        assert 30 <= timeout <= 45

    def test_molecule_matrix_has_4_shards(self) -> None:
        wf = _load_workflow()
        molecule = wf["jobs"]["molecule"]
        shards = molecule.get("strategy", {}).get("matrix", {}).get("shard", [])
        assert shards == [1, 2, 3, 4], f"Expected 4 shards, got {shards!r}"


class TestPlatformJobArtifactNames:
    def test_linux_upload_artifact_name(self) -> None:
        wf = _load_workflow()
        uploads = [
            s
            for s in wf["jobs"]["linux"]["steps"]
            if isinstance(s, dict) and s.get("uses", "").startswith("actions/upload-artifact")
        ]
        names = {u.get("with", {}).get("name", "") for u in uploads}
        assert "gludd-linux-x86_64" in names, f"Expected gludd-linux-x86_64 artifact, got {names}"

    def test_macos_upload_artifact_name(self) -> None:
        wf = _load_workflow()
        uploads = [
            s
            for s in wf["jobs"]["macos"]["steps"]
            if isinstance(s, dict) and s.get("uses", "").startswith("actions/upload-artifact")
        ]
        names = {u.get("with", {}).get("name", "") for u in uploads}
        assert "gludd-macos-arm64" in names, f"Expected gludd-macos-arm64 artifact, got {names}"

    def test_windows_upload_artifact_name(self) -> None:
        wf = _load_workflow()
        uploads = [
            s
            for s in wf["jobs"]["windows"]["steps"]
            if isinstance(s, dict) and s.get("uses", "").startswith("actions/upload-artifact")
        ]
        names = {u.get("with", {}).get("name", "") for u in uploads}
        assert "gludd-windows-x86_64" in names, f"Expected gludd-windows-x86_64 artifact, got {names}"

    def test_termux_upload_artifact_name(self) -> None:
        wf = _load_workflow()
        uploads = [
            s
            for s in wf["jobs"]["termux"]["steps"]
            if isinstance(s, dict) and s.get("uses", "").startswith("actions/upload-artifact")
        ]
        names = {u.get("with", {}).get("name", "") for u in uploads}
        assert "gludd-linux-aarch64" in names, f"Expected gludd-linux-aarch64 artifact, got {names}"


def _assert_managed_gate_patch(workflow: Workflow) -> None:
    gate = workflow["jobs"]["gate"]
    assert gate["strategy"]["matrix"]["python-version"] == ["3.11", "3.12"]
    steps = gate["steps"]
    action = "astral-sh/setup-uv@fac544c07dec837d0ccb6301d7b5580bf5edae39"
    setups = [step for step in steps if str(step.get("uses", "")).startswith("astral-sh/setup-uv@")]
    assert len(setups) == 2
    by_condition = {step.get("if"): step for step in setups}
    assert set(by_condition) == {"matrix.python-version == '3.11'", "matrix.python-version == '3.12'"}
    assert by_condition["matrix.python-version == '3.11'"] == {
        "uses": action, "if": "matrix.python-version == '3.11'",
    }
    assert by_condition["matrix.python-version == '3.12'"] == {
        "uses": action, "if": "matrix.python-version == '3.12'",
        "with": {"version": "0.13.1", "python-version": "3.12.15", "activate-environment": False},
    }
    legacy = [step for step in steps if step.get("name") == "Install deps"]
    managed = [step for step in steps if step.get("name") == "Install managed Python 3.12 deps"]
    assert len(legacy) == len(managed) == 1
    assert legacy[0]["if"] == "matrix.python-version == '3.11'"
    assert managed[0]["if"] == "matrix.python-version == '3.12'"
    assert managed[0]["env"] == {"UV_PYTHON_PREFERENCE": "only-managed"}
    for step, request in ((legacy[0], "${{ matrix.python-version }}"), (managed[0], "3.12.15")):
        command = " ".join(str(step["run"]).replace("\\", "").split())
        assert f"python scripts/dependency_profiles.py sync --set ci --environment .venv --python {request}" in command
    assert workflow["env"]["UV_NO_SYNC"] == "1"
    for environment in (workflow.get("env", {}), gate.get("env", {}), managed[0]["env"]):
        assert environment.get("UV_PYTHON_DOWNLOADS") != "never"
        assert str(environment.get("UV_OFFLINE", "")).lower() not in {"1", "true"}


def test_gate312_uses_exact_current_managed_profile() -> None:
    _assert_managed_gate_patch(_load_workflow())


@pytest.mark.parametrize("mutation", (
    "uv-floating", "uv-old", "python-major-only", "python-system-old",
    "profile-major-only", "system-fallback", "extra-activation", "both-branches", "profile-bypass",
    "downloads-disabled", "offline-acquisition",
))
def test_managed_gate_patch_refuses_runtime_selection_drift(mutation: str) -> None:
    workflow = deepcopy(_load_workflow())
    _assert_managed_gate_patch(workflow)
    steps = workflow["jobs"]["gate"]["steps"]
    setup = next(
        step for step in steps
        if str(step.get("uses", "")).startswith("astral-sh/setup-uv@")
        and step.get("if") == "matrix.python-version == '3.12'"
    )
    install = next(step for step in steps if step.get("name") == "Install managed Python 3.12 deps")
    if mutation == "uv-floating":
        setup["with"]["version"] = "latest"
    elif mutation == "uv-old":
        setup["with"]["version"] = "0.11.17"
    elif mutation == "python-major-only":
        setup["with"]["python-version"] = "3.12"
    elif mutation == "python-system-old":
        setup["with"]["python-version"] = "3.12.3"
    elif mutation == "profile-major-only":
        install["run"] = str(install["run"]).replace("--python 3.12.15", "--python 3.12")
    elif mutation == "system-fallback":
        install["env"]["UV_PYTHON_PREFERENCE"] = "managed"
    elif mutation == "extra-activation":
        setup["with"]["activate-environment"] = True
    elif mutation == "both-branches":
        setup["if"] = "matrix.python-version == '3.11'"
    elif mutation == "downloads-disabled":
        workflow["env"]["UV_PYTHON_DOWNLOADS"] = "never"
    elif mutation == "offline-acquisition":
        workflow["env"]["UV_OFFLINE"] = "1"
    else:
        assert mutation == "profile-bypass"
        install["run"] = "uv sync --python 3.12.15"
    with pytest.raises(AssertionError):
        _assert_managed_gate_patch(workflow)


def test_gate312_guard_is_promoted_before_fanout(tmp_path: pathlib.Path) -> None:
    from tests.unit.test_runtime_admission_scheduling import _capture_owner

    _assert_managed_gate_patch(_load_workflow())
    root = WORKFLOW_PATH.parents[2]
    payload = json.loads((root / "config/gate_failure_promotions.json").read_text(encoding="utf-8"))
    node = "tests/unit/test_ci_workflow_integrity.py::test_gate312_guard_is_promoted_before_fanout"
    assert {
        "id": "hosted-managed-python312-patch",
        "owner_target": "_pytest-runtime-compatibility-admission",
        "node_kind": "pytest_node", "node": node, "full_gate_phase": "test",
    } in payload["families"]
    result, rows = _capture_owner(tmp_path, "")
    assert result.returncode == 0, result.stdout + result.stderr
    main_files = rows[0]["files"]
    assert isinstance(main_files, list)
    assert main_files.count("tests/unit/test_ci_workflow_integrity.py") == 1
    for row in rows[1:]:
        later_files = row["files"]
        assert isinstance(later_files, list)
        assert "tests/unit/test_ci_workflow_integrity.py" not in later_files
    assert payload["full_gate_target"] == "gate"
    assert payload["full_gate_required"] is True
    phase = next(
        item for item in payload["runtime_contract"]["phases"]
        if item["target"] == "_pytest-runtime-compatibility-admission"
    )
    assert (phase["max_seconds"], phase["quiet_seconds"]) == (90, 60)


def test_hosted_runtime_main_reports_native_durations_without_new_children(tmp_path: pathlib.Path) -> None:
    from tests.unit.test_runtime_admission_scheduling import _capture_owner

    result, rows = _capture_owner(tmp_path, "")
    assert result.returncode == 0, result.stdout + result.stderr
    assert [row["phase"] for row in rows] == ["full", "help", "reviewed"]
    assert len({row["pid"] for row in rows}) == 3
    assert [(row["nproc"], row["workers"]) for row in rows] == [("2", "2"), ("1", "1"), ("1", "1")]
    main_flags = shlex.split(str(rows[0]["flags"]))
    assert main_flags.count("--durations=20") == 1
    assert main_flags.count("--durations-min=0") == 1
    for row in rows[1:]:
        assert not any(
            flag.startswith(("--durations=", "--durations-min="))
            for flag in shlex.split(str(row["flags"]))
        )
