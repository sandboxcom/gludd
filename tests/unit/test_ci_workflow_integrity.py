"""Deep structural tests for .github/workflows/build.yml integrity."""

import pathlib

import yaml

WORKFLOW_PATH = pathlib.Path(__file__).parent.parent.parent / ".github" / "workflows" / "build.yml"


def _load_workflow():
    content = WORKFLOW_PATH.read_text()
    return yaml.safe_load(content)


def _extract_step_names(steps):
    names: set[str] = set()
    for step in steps:
        if isinstance(step, dict) and "name" in step:
            names.add(step["name"])
    return names


def _collect_job_refs(needs_value):
    if isinstance(needs_value, str):
        return {needs_value}
    if isinstance(needs_value, list):
        return set(needs_value)
    return set()


class TestAllNeedsResolveToRealJobs:
    def test_all_needs_references_real_jobs(self):
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
    def test_no_circular_dependencies(self):
        wf = _load_workflow()
        jobs = wf.get("jobs", {})
        graph: dict[str, set[str]] = {}
        for name, defn in jobs.items():
            graph[name] = _collect_job_refs(defn.get("needs", []))

        visiting: set[str] = set()
        visited: set[str] = set()

        def dfs(node):
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
    def test_version_has_no_needs(self):
        wf = _load_workflow()
        version = wf["jobs"].get("version", {})
        needs = version.get("needs")
        assert needs is None or needs == [], f"version job should have no needs, got {needs!r}"

    def test_version_produces_outputs(self):
        wf = _load_workflow()
        version = wf["jobs"]["version"]
        assert "outputs" in version
        assert "version" in version["outputs"]

    def test_version_has_timeout(self):
        wf = _load_workflow()
        version = wf["jobs"]["version"]
        assert "timeout-minutes" in version
        assert version["timeout-minutes"] > 0


class TestGateJobStructure:
    def test_gate_needs_version(self):
        wf = _load_workflow()
        gate = wf["jobs"]["gate"]
        assert _collect_job_refs(gate.get("needs", [])) == {"version"}

    def test_gate_has_matrix(self):
        wf = _load_workflow()
        gate = wf["jobs"]["gate"]
        matrix = gate.get("strategy", {}).get("matrix", {})
        python_versions = matrix.get("python-version", [])
        assert len(python_versions) >= 2, f"gate matrix should have ≥2 Python versions, got {python_versions}"

    def test_gate_has_timeout(self):
        wf = _load_workflow()
        gate = wf["jobs"]["gate"]
        assert "timeout-minutes" in gate
        assert gate["timeout-minutes"] > 0

    def test_gate_matrix_fail_fast_is_false(self):
        wf = _load_workflow()
        gate = wf["jobs"]["gate"]
        strategy = gate.get("strategy", {})
        assert strategy.get("fail-fast") is False, "gate fail-fast should be False to get both Python version results"


class TestTestShardStructure:
    def test_test_shard_needs_version_and_gate(self):
        wf = _load_workflow()
        test_shard = wf["jobs"]["test-shard"]
        assert _collect_job_refs(test_shard.get("needs", [])) == {"version", "gate"}

    def test_test_shard_matrix_has_all_entries(self):
        wf = _load_workflow()
        shard = wf["jobs"]["test-shard"]
        include = shard.get("strategy", {}).get("matrix", {}).get("include", [])
        names = {entry["shard"] for entry in include}
        expected = {"unit-1a1", "unit-1a2", "unit-1b", "unit-1d", "unit-2", "unit-3", "other"}
        assert names == expected, f"Expected shards {expected!r}, got {names!r}"

    def test_every_shard_has_testpaths(self):
        wf = _load_workflow()
        shard = wf["jobs"]["test-shard"]
        include = shard.get("strategy", {}).get("matrix", {}).get("include", [])
        for entry in include:
            assert "testpaths" in entry, f"Shard {entry.get('shard', '?')} missing testpaths"
            assert len(entry["testpaths"]) > 0, f"Shard {entry.get('shard', '?')} testpaths is empty"

    def test_test_shard_has_timeout(self):
        wf = _load_workflow()
        test_shard = wf["jobs"]["test-shard"]
        assert "timeout-minutes" in test_shard

    def test_test_shard_fail_fast_is_false(self):
        wf = _load_workflow()
        test_shard = wf["jobs"]["test-shard"]
        strategy = test_shard.get("strategy", {})
        assert strategy.get("fail-fast") is False


class TestCoverageJobStructure:
    def test_coverage_needs_version_and_test_shard(self):
        wf = _load_workflow()
        coverage = wf["jobs"]["coverage"]
        assert _collect_job_refs(coverage.get("needs", [])) == {"version", "test-shard"}

    def test_coverage_has_timeout(self):
        wf = _load_workflow()
        coverage = wf["jobs"]["coverage"]
        assert "timeout-minutes" in coverage


class TestGameBuildingJobStructure:
    def test_game_building_installs_locked_media_extra_before_tests(self) -> None:
        wf = _load_workflow()
        steps = wf["jobs"]["game-building"]["steps"]
        commands = [str(step.get("run", "")) for step in steps if isinstance(step, dict)]
        command = next(command for command in commands if "make test-games" in command)

        assert "uv sync --frozen --extra game-e2e" in command
        assert command.index("uv sync --frozen --extra game-e2e") < command.index(
            "make test-games"
        )


class TestPlatformBuildJobStructure:
    PLATFORM_JOBS: tuple[str, ...] = ("linux", "macos", "windows", "termux")

    def test_all_platform_jobs_exist(self):
        wf = _load_workflow()
        for job_name in self.PLATFORM_JOBS:
            assert job_name in wf["jobs"], f"Missing platform build job: {job_name}"

    def test_platform_jobs_need_version_and_gate(self):
        wf = _load_workflow()
        for job_name in self.PLATFORM_JOBS:
            job = wf["jobs"][job_name]
            assert _collect_job_refs(job.get("needs", [])) == {"version", "gate"}, (
                f"{job_name} needs {_collect_job_refs(job.get('needs', []))}"
            )

    def test_platform_jobs_have_timeout(self):
        wf = _load_workflow()
        for job_name in self.PLATFORM_JOBS:
            job = wf["jobs"][job_name]
            assert "timeout-minutes" in job, f"{job_name} missing timeout-minutes"

    def test_platform_jobs_have_runs_on(self):
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
            "test-shard",
            "coverage",
            "molecule",
            "container",
            "ansible-ee",
            "game-building",
            *PLATFORM_JOBS,
        }
    )

    def test_release_needs_gate_tests_and_all_artifact_producers(self):
        wf = _load_workflow()
        release = wf["jobs"]["release"]
        needs = _collect_job_refs(release.get("needs", []))
        assert needs == self.RELEASE_PREREQUISITES, (
            f"release needs {needs!r}, expected {self.RELEASE_PREREQUISITES!r}"
        )

    def test_release_only_runs_on_tags(self):
        wf = _load_workflow()
        release = wf["jobs"]["release"]
        condition = release.get("if", "")
        assert "refs/tags/v" in condition

    def test_release_has_timeout(self):
        wf = _load_workflow()
        release = wf["jobs"]["release"]
        assert "timeout-minutes" in release


class TestNoDuplicateJobNames:
    def test_no_duplicate_job_names(self):
        wf = _load_workflow()
        jobs = wf.get("jobs", {})
        assert len(jobs) == len(set(jobs)), "Duplicate job names found"


class TestEveryJobHasTimeout:
    def test_every_job_has_timeout_minutes(self):
        wf = _load_workflow()
        missing: list[str] = []
        for job_name, job_def in wf["jobs"].items():
            if "timeout-minutes" not in job_def:
                missing.append(job_name)
        assert not missing, f"Jobs missing timeout-minutes: {', '.join(sorted(missing))}"


class TestEveryJobHasRunsOn:
    def test_every_job_has_runs_on(self):
        wf = _load_workflow()
        missing: list[str] = []
        for job_name, job_def in wf["jobs"].items():
            if "runs-on" not in job_def:
                missing.append(job_name)
        assert not missing, f"Jobs missing runs-on: {', '.join(sorted(missing))}"


class TestAllJobsReachableFromVersion:
    def test_all_jobs_reachable_from_version_or_root(self):
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
    def test_concurrency_group_is_defined(self):
        wf = _load_workflow()
        assert "concurrency" in wf
        concurrency = wf["concurrency"]
        assert "group" in concurrency, "concurrency group missing"

    def test_permissions_defined(self):
        wf = _load_workflow()
        assert "permissions" in wf
        perms = wf["permissions"]
        assert "contents" in perms
        assert "packages" in perms

    def test_on_triggers_include_push_and_pr(self):
        wf = _load_workflow()
        on = wf.get("on", wf.get(True, {}))
        assert "push" in on
        assert "pull_request" in on

    def test_on_tag_pattern_is_v_star(self):
        wf = _load_workflow()
        on = wf.get("on", wf.get(True, {}))
        push = on.get("push", {})
        tags = push.get("tags", [])
        assert "v*" in tags, f"Expected 'v*' in tag triggers, got {tags!r}"


class TestMoleculeJobStructure:
    def test_molecule_needs_version_and_gate(self):
        wf = _load_workflow()
        molecule = wf["jobs"]["molecule"]
        assert _collect_job_refs(molecule.get("needs", [])) == {"version", "gate"}

    def test_molecule_has_timeout(self):
        wf = _load_workflow()
        molecule = wf["jobs"]["molecule"]
        assert "timeout-minutes" in molecule

    def test_molecule_matrix_has_4_shards(self):
        wf = _load_workflow()
        molecule = wf["jobs"]["molecule"]
        shards = molecule.get("strategy", {}).get("matrix", {}).get("shard", [])
        assert shards == [1, 2, 3, 4], f"Expected 4 shards, got {shards!r}"


class TestPlatformJobArtifactNames:
    def test_linux_upload_artifact_name(self):
        wf = _load_workflow()
        uploads = [
            s
            for s in wf["jobs"]["linux"]["steps"]
            if isinstance(s, dict) and s.get("uses", "").startswith("actions/upload-artifact")
        ]
        names = {u.get("with", {}).get("name", "") for u in uploads}
        assert "gludd-linux-x86_64" in names, f"Expected gludd-linux-x86_64 artifact, got {names}"

    def test_macos_upload_artifact_name(self):
        wf = _load_workflow()
        uploads = [
            s
            for s in wf["jobs"]["macos"]["steps"]
            if isinstance(s, dict) and s.get("uses", "").startswith("actions/upload-artifact")
        ]
        names = {u.get("with", {}).get("name", "") for u in uploads}
        assert "gludd-macos-arm64" in names, f"Expected gludd-macos-arm64 artifact, got {names}"

    def test_windows_upload_artifact_name(self):
        wf = _load_workflow()
        uploads = [
            s
            for s in wf["jobs"]["windows"]["steps"]
            if isinstance(s, dict) and s.get("uses", "").startswith("actions/upload-artifact")
        ]
        names = {u.get("with", {}).get("name", "") for u in uploads}
        assert "gludd-windows-x86_64" in names, f"Expected gludd-windows-x86_64 artifact, got {names}"

    def test_termux_upload_artifact_name(self):
        wf = _load_workflow()
        uploads = [
            s
            for s in wf["jobs"]["termux"]["steps"]
            if isinstance(s, dict) and s.get("uses", "").startswith("actions/upload-artifact")
        ]
        names = {u.get("with", {}).get("name", "") for u in uploads}
        assert "gludd-linux-aarch64" in names, f"Expected gludd-linux-aarch64 artifact, got {names}"
