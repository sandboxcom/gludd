"""Repo-config regression guards for CI failures hit this session.

Each test below pins a hard-won CI fix into place so it can never silently
regress. Every guard cites the concrete CI incident it prevents and fails with
a message that names that incident, so a future edit that removes the fix gets
an unmistakable explanation instead of a mysterious red build.

These are META-tests: they assert on the *repo configuration* (the pyinstaller
spec, the GitHub Actions workflow, tracked files, molecule scenario layout) —
not on runtime behavior. They use only stdlib + pathlib + pyyaml (pyyaml is a
hard project dependency, see pyproject.toml ``dependencies``). The build.yml
workflow is parsed as YAML so the assertions are structural (job env / needs /
if), not brittle substring matches.
"""

from __future__ import annotations

import configparser
import tomllib
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
DOCKERFILE = ROOT / "Dockerfile"
PYPROJECT = ROOT / "pyproject.toml"
SPEC = ROOT / "gludd.spec"
BUILD_YML = ROOT / ".github" / "workflows" / "build.yml"
GREENLET_COVERAGE_RC = ROOT / ".coveragerc-greenlet"
PARTIALS_GITKEEP = ROOT / "templates" / "prompts" / "partials" / ".gitkeep"
MOLECULE_PLAYBOOKS = ROOT / "molecule" / "playbooks"


def _load_build_workflow() -> dict[str, Any]:
    assert BUILD_YML.is_file(), (
        f"CI regression: {BUILD_YML} is missing — the entire Build and Release "
        "workflow (gate, molecule, per-OS builds, release) would not run."
    )
    data = yaml.safe_load(BUILD_YML.read_text(encoding="utf-8"))
    assert isinstance(data, dict) and "jobs" in data, (
        f"CI regression: {BUILD_YML} did not parse into a workflow with a 'jobs' "
        "mapping; the build/release pipeline definition is malformed."
    )
    return data


def test_spec_excludes_ansible_cli() -> None:
    """Guard: gludd.spec must keep 'ansible.cli' in its pyinstaller excludes.

    CI incident: the Windows pyinstaller build broke because pyinstaller imports
    ``ansible.cli`` at build time, and ``ansible.cli.initialize_locale()``
    hard-fails on Windows' default cp1252 locale
    ("Ansible requires UTF-8; Detected 1252"). gludd only uses ansible-core's
    executor API (runner/core_runner/templating), never the CLI, so the CLI is
    excluded from the bundle. Removing it from ``excludes`` re-breaks the build.
    """
    text = SPEC.read_text(encoding="utf-8")
    # The excludes are a Python literal list inside Analysis(...). Find the line
    # and assert the entry is present as a quoted string in the list.
    excludes_present = "'ansible.cli'" in text or '"ansible.cli"' in text
    assert "excludes=[" in text, (
        f"CI regression (Windows cp1252 build break): {SPEC} no longer has an "
        "excludes=[...] list in Analysis(); cannot verify ansible.cli exclusion."
    )
    assert excludes_present, (
        "CI regression (Windows pyinstaller cp1252 build break): "
        f"'ansible.cli' is no longer in the excludes list of {SPEC}. Without it, "
        "pyinstaller imports ansible.cli at build time and "
        "ansible.cli.initialize_locale() fails on Windows' cp1252 locale "
        "('Ansible requires UTF-8; Detected 1252'). Re-add 'ansible.cli' to "
        "excludes."
    )


def test_gate_job_sets_gludd_xdist_auto() -> None:
    """Guard: the gate job must set GLUDD_XDIST=auto in its env.

    CI incident: with a single xdist worker the test suite exceeded the 40-minute
    CI gate budget (timeout-minutes: 40) and the job was killed. Setting
    ``GLUDD_XDIST: auto`` fans the suite across all available cores so it stays
    under budget. Dropping this env var re-introduces the timeout.
    """
    wf = _load_build_workflow()
    gate = wf["jobs"].get("gate")
    assert gate is not None, (
        "CI regression: the 'gate' job vanished from build.yml — lint/type/test "
        "would no longer run on push/PR."
    )
    # Env can live on the job or on the step that runs `make ... test`.
    job_env = gate.get("env") or {}
    step_envs: list[dict[str, Any]] = [
        s.get("env") or {} for s in gate.get("steps", []) if isinstance(s, dict)
    ]
    all_envs = [job_env, *step_envs]
    found = any(str(e.get("GLUDD_XDIST")) == "auto" for e in all_envs)
    assert found, (
        "CI regression (gate exceeded the 40-min CI budget with a single xdist "
        "worker): the gate job no longer sets GLUDD_XDIST=auto. Restore "
        "'GLUDD_XDIST: auto' in the gate job/step env so the suite parallelizes "
        "across cores and stays under timeout-minutes: 40."
    )


def test_windows_job_is_release_blocking() -> None:
    """A green release must prove that the required Windows artifacts built."""
    wf = _load_build_workflow()
    windows = wf["jobs"].get("windows")
    assert windows is not None, (
        "CI regression: the 'windows' build job vanished from build.yml."
    )
    assert windows.get("continue-on-error", False) is False, (
        "CI regression: Windows is a required beta.3 artifact producer; "
        "continue-on-error can turn a missing zip/installer into a green pipeline"
    )


def test_windows_job_uses_canonical_pinned_bootstrap_actions() -> None:
    """Windows must use the same maintained action revisions as Linux."""
    wf = _load_build_workflow()
    jobs = wf["jobs"]

    def bootstrap_uses(job_name: str, action: str) -> list[str]:
        return [
            str(step.get("uses", ""))
            for step in jobs[job_name].get("steps", [])
            if str(step.get("uses", "")).startswith(f"{action}@")
        ]

    for action in ("actions/checkout", "astral-sh/setup-uv"):
        linux_uses = bootstrap_uses("linux", action)
        windows_uses = bootstrap_uses("windows", action)
        assert len(linux_uses) == len(windows_uses) == 1
        assert windows_uses == linux_uses, (
            f"Windows {action} must match the canonical Linux immutable pin; "
            f"got windows={windows_uses}, linux={linux_uses}."
        )


def test_windows_packaging_job_is_deterministic_and_fail_closed() -> None:
    """Pin the runner/toolchain and reject partial or untested artifacts."""
    windows = _load_build_workflow()["jobs"]["windows"]
    assert windows["runs-on"] == "windows-2022"

    steps = windows["steps"]
    setup_uv = next(
        step
        for step in steps
        if str(step.get("uses", "")).startswith("astral-sh/setup-uv@")
    )
    assert setup_uv.get("with", {}).get("python-version") == "3.12"

    names = [str(step.get("name", "")) for step in steps]
    smoke_index = names.index("Smoke test binary")
    package_index = names.index("Package zip")
    upload_index = next(
        i
        for i, step in enumerate(steps)
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    )
    assert smoke_index < package_index < upload_index
    assert steps[smoke_index].get("continue-on-error", False) is False

    upload = steps[upload_index]
    assert upload.get("if", "success()") == "success()"
    assert upload.get("with", {}).get("if-no-files-found") == "error"

    runs = "\n".join(str(step.get("run", "")) for step in steps)
    assert "choco install nsis --version=3.12.0" in runs
    assert "makensis.exe" in runs and "$makensis /WX" in runs
    assert "Get-FileHash" in runs
    assert "certutil" not in runs


def test_partials_gitkeep_is_tracked() -> None:
    """Guard: templates/prompts/partials/.gitkeep must exist.

    CI incident: git does not track empty directories. CI does not run
    ``make setup-dirs``, so on a fresh checkout the empty
    ``templates/prompts/partials/`` directory was absent and
    tests/e2e/test_obj01_skeleton.py::test_config_directories_exist failed with
    'Missing directory'. The tracked .gitkeep keeps the directory present.
    """
    assert PARTIALS_GITKEEP.is_file(), (
        "CI regression (fresh-checkout 'Missing directory' failure in "
        "tests/e2e/test_obj01_skeleton.py::test_config_directories_exist): "
        f"{PARTIALS_GITKEEP} is missing. git does not track empty dirs and CI "
        "does not run `make setup-dirs`, so without this tracked file the "
        "templates/prompts/partials/ directory is absent on a clean checkout. "
        "Re-add the .gitkeep."
    )


def test_every_molecule_scenario_is_structurally_complete() -> None:
    """Guard: every molecule/playbooks/<scenario> has the required files.

    CI incident (run 27596845359): a molecule scenario directory was committed
    without its full file set, so ``make molecule-test-all`` ran an
    unverified/structurally-incomplete scenario and failed. A complete scenario
    must have molecule.yml + default/converge.yml + default/verify.yml; the
    absence of any of these is the smell that an un-runnable scenario slipped in.

    This iterates the committed scenarios at runtime, so a future incomplete
    scenario fails here BEFORE it can break `make molecule-test-all` in CI.
    """
    assert MOLECULE_PLAYBOOKS.is_dir(), (
        f"CI regression: {MOLECULE_PLAYBOOKS} is missing — no molecule scenarios "
        "to verify; `make molecule-test-all` would have nothing to run."
    )
    scenario_dirs = sorted(p for p in MOLECULE_PLAYBOOKS.iterdir() if p.is_dir())
    assert scenario_dirs, (
        f"CI regression: no scenario directories under {MOLECULE_PLAYBOOKS}."
    )

    incomplete: dict[str, list[str]] = {}
    required = ("molecule.yml", "default/converge.yml", "default/verify.yml")
    for scenario in scenario_dirs:
        missing = [rel for rel in required if not (scenario / rel).is_file()]
        if missing:
            incomplete[scenario.name] = missing

    assert not incomplete, (
        "CI regression (molecule-test-all failure, run 27596845359 — an "
        "unverified/structurally-incomplete scenario was committed): the "
        "following molecule/playbooks scenarios are missing required files "
        f"{required}: {incomplete}. Every committed scenario must ship "
        "molecule.yml + default/converge.yml + default/verify.yml so "
        "`make molecule-test-all` can run it."
    )


def test_release_job_is_tag_gated_and_needs_all_builds() -> None:
    """Guard: the release job must be tag-gated and depend on all 4 build jobs.

    CI incident: the release job must publish a GitHub Release ONLY on a version
    tag, and only AFTER all platform builds have produced their artifacts.
    Required:
      - ``if: startsWith(github.ref, 'refs/tags/v')`` — never release off a
        branch push / PR / non-version tag.
      - ``needs`` includes all four platform build jobs (linux, macos, windows,
        termux) so the release waits for (and can collect) every artifact.
    Loosening either lets a release fire prematurely or with missing artifacts.
    """
    wf = _load_build_workflow()
    release = wf["jobs"].get("release")
    assert release is not None, (
        "CI regression: the 'release' job vanished from build.yml — tags would "
        "no longer publish a GitHub Release."
    )

    if_cond = str(release.get("if", ""))
    assert "startsWith(github.ref, 'refs/tags/v')" in if_cond, (
        "CI regression (release could fire off a non-tag ref): the release job's "
        "if-condition no longer requires "
        "startsWith(github.ref, 'refs/tags/v'). Restore the tag gate so releases "
        f"only publish on version tags. Found if: {if_cond!r}"
    )

    needs = release.get("needs", [])
    if isinstance(needs, str):
        needs = [needs]
    needs_set = set(needs)
    required_builds = {"linux", "macos", "windows", "termux"}
    missing = required_builds - needs_set
    assert not missing, (
        "CI regression (release ran before all platform artifacts existed): the "
        f"release job's 'needs' is missing {sorted(missing)}. It must depend on "
        "all four platform build jobs (linux, macos, windows, termux) so the "
        f"release waits for every artifact. Found needs: {sorted(needs_set)}"
    )


def test_test_shard_collects_coverage_on_every_shard() -> None:
    """Guard: every test-shard matrix leg must collect coverage data.

    CI incident: coverage was only collected on the ``unit-1`` / Python 3.11
    shard (all other 7 shards used ``--no-cov``), so the published coverage
    report represented ~¼ of the suite and systematically understated real
    coverage.  Each shard must run pytest with ``--cov=general_ludd`` (no
    shard may pass ``--no-cov``) so the downstream ``coverage`` job has data
    from every shard to merge.
    """
    wf = _load_build_workflow()
    shard = wf["jobs"].get("test-shard")
    assert shard is not None, (
        "CI regression: the 'test-shard' job vanished from build.yml."
    )
    steps = shard.get("steps", [])
    test_steps = [
        s for s in steps
        if "Test" in str(s.get("name", "")) and "cov" in str(s.get("run", "")).lower()
    ]
    assert test_steps, (
        "CI regression: no test-shard step runs pytest with --cov. Every shard "
        "must emit .coverage data so the 'coverage' aggregation job can merge it."
    )
    for step in test_steps:
        run = str(step.get("run", ""))
        assert "--cov=general_ludd" in run, (
            "CI regression: a test-shard step dropped --cov=general_ludd. "
            f"Step {step.get('name')!r} run block: {run!r}"
        )
        assert "--no-cov" not in run, (
            "CI regression: a test-shard step uses --no-cov, which silos its "
            "coverage and defeats the aggregation job. Step "
            f"{step.get('name')!r} must use --cov instead."
        )


def test_coverage_aggregation_job_exists() -> None:
    """Guard: a dedicated coverage-aggregation job must exist and merge shards.

    CI incident: with 8 parallel test-shards and no aggregation step, coverage
    was never combined across shards — the only published report came from a
    single shard and missed ~75% of the suite.  This test pins the fix:

      - A ``coverage`` job exists in build.yml.
      - It ``needs: [test-shard]`` so it runs after every shard finishes.
      - It downloads the per-shard coverage artifacts (``download-artifact``
        with a ``coverage-*`` pattern).
      - It runs ``coverage combine`` to merge the shard data files into one.
      - It uploads a merged ``coverage.xml`` artifact.
    """
    wf = _load_build_workflow()
    jobs = wf["jobs"]
    assert "coverage" in jobs, (
        "CI regression: the 'coverage' aggregation job vanished from build.yml. "
        "Without it, per-shard .coverage files are never combined and the "
        "published report only reflects one shard (~25% of the suite)."
    )
    cov = jobs["coverage"]

    needs = cov.get("needs", [])
    if isinstance(needs, str):
        needs = [needs]
    assert "test-shard" in needs, (
        "CI regression: the 'coverage' job does not depend on 'test-shard', so "
        "it could run before any shard produced coverage data. "
        f"Found needs: {needs!r}"
    )

    # Serialize each step's run/uses/with so we can assert on the download
    # pattern (which lives in the `with:` block, not run/uses).
    step_blobs = [
        "\n".join(f"{k}: {v}" for k, v in s.items())
        for s in cov.get("steps", [])
    ]
    step_runs = "\n".join(str(s.get("run", "")) for s in cov.get("steps", []))
    combined = step_runs + "\n" + "\n".join(step_blobs)
    assert "download-artifact" in combined, (
        "CI regression: the 'coverage' job has no download-artifact step — it "
        "cannot fetch the per-shard .coverage data files to merge them."
    )
    assert "coverage-" in combined, (
        "CI regression: the 'coverage' job's download-artifact step does not "
        "target the per-shard 'coverage-*' artifacts, so shard data would not "
        "be fetched for merging."
    )
    assert "coverage combine" in step_runs, (
        "CI regression: the 'coverage' job does not invoke 'coverage combine', "
        "so per-shard data files are never merged into a single report."
    )
    assert "coverage xml" in step_runs, (
        "CI regression: the 'coverage' job does not invoke 'coverage xml', so "
        "no canonical Cobertura report is produced from the merged data."
    )
    assert "upload-artifact" in combined, (
        "CI regression: the 'coverage' job does not upload the merged "
        "coverage.xml artifact, so the combined report is not retained."
    )


def test_docker_builder_copies_wheel_force_includes_before_project_sync() -> None:
    """Guard: Hatch force-includes must exist before Docker installs the project.

    Hosted beta.3 incident: ``uv sync --frozen --no-dev`` failed because Hatch
    tried to force-include ``/app/infra/terraform`` before Docker copied it.
    """
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    project_sync = dockerfile.rindex("uv sync --frozen --no-dev")
    builder_prefix = dockerfile[:project_sync]
    copied_sources = {
        source
        for line in builder_prefix.splitlines()
        if line.startswith("COPY ")
        for source in line.removeprefix("COPY ").split()[:-1]
    }
    pyproject = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    force_includes = set(
        pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    )

    assert force_includes <= copied_sources, (
        "Docker packaging regression: every Hatch wheel force-include must be "
        "copied before the final `uv sync --frozen --no-dev`; missing sources: "
        f"{sorted(force_includes - copied_sources)}"
    )


def test_shard_coverage_upload_includes_hidden_file_and_fails_closed() -> None:
    """Guard: upload-artifact must opt into the hidden ``.coverage.*`` payload."""
    workflow = _load_build_workflow()
    steps = workflow["jobs"]["test-shard"]["steps"]
    uploads = [
        step
        for step in steps
        if str(step.get("name", "")).startswith("Upload coverage data")
    ]

    assert len(uploads) == 1, (
        "CI regression: expected exactly one test-shard coverage upload step, "
        f"found {len(uploads)}."
    )
    upload_with = uploads[0].get("with", {})
    assert upload_with.get("include-hidden-files") is True, (
        "CI regression: actions/upload-artifact excludes the shard's hidden "
        "`.coverage.*` file unless `include-hidden-files: true` is explicit."
    )
    assert upload_with.get("if-no-files-found") == "error", (
        "CI regression: shard coverage upload must fail closed when the hidden "
        "coverage file is missing."
    )


def test_test_shard_coverage_is_private_and_combines_parallel_fragments() -> None:
    """Guard against checkout-level and xdist-worker coverage data loss.

    A hosted shard may execute tests which themselves invoke pytest/coverage.
    Keeping the outer shard's data at ``./.coverage`` lets those nested commands
    erase or replace it.  pytest-cov may also leave worker fragments alongside a
    canonical file, so checking only whether the canonical file exists can skip
    required combination and silently drop one worker's executed lines.
    """
    workflow = _load_build_workflow()
    steps = workflow["jobs"]["test-shard"]["steps"]
    test_steps = [
        step
        for step in steps
        if str(step.get("name", "")).startswith("Test (shard ")
    ]

    assert len(test_steps) == 1
    step = test_steps[0]
    coverage_file = str(step.get("env", {}).get("COVERAGE_FILE", ""))
    assert "runner.temp" in coverage_file, (
        "CI coverage data must live under runner.temp, outside the shared "
        "checkout where nested pytest/coverage commands can replace it."
    )
    assert "matrix.shard" in coverage_file
    assert "matrix.python-version" in coverage_file

    run = str(step.get("run", ""))
    assert 'if compgen -G "${COVERAGE_FILE}.*"' in run, (
        "CI must detect and combine xdist .coverage worker fragments even when "
        "pytest-cov also left a canonical COVERAGE_FILE."
    )
    assert "coverage combine --keep" in run
    assert 'test -s "$COVERAGE_FILE"' in run
    assert 'cp "$COVERAGE_FILE"' in run
    assert "if [ ! -f .coverage ]" not in run


def test_greenlet_coverage_is_scoped_to_unit3() -> None:
    """Trace SQLAlchemy greenlets without slowing every hosted shard.

    Hosted beta.3 run 30517080961 reported ``routers/self_improve.py`` below
    the per-file floor even though its endpoint tests passed.  Every missing
    block began immediately after an awaited repository call: coverage stopped
    tracing when SQLAlchemy switched through greenlet and never recorded the
    resumed coroutine.  Global greenlet/thread tracing fixed that gap, but run
    30520711085 then timed out otherwise-green unit-1b and unit-2 tests on both
    Python versions.  Only unit-3 owns the self-improve tests, so it alone must
    pay the extra tracing cost.
    """
    pyproject = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    default_run = pyproject["tool"]["coverage"]["run"]
    assert "concurrency" not in default_run, (
        "CI timeout regression (beta.3 run 30520711085): greenlet/thread "
        "coverage tracing must not be enabled globally because its overhead "
        "trips unit-1b and unit-2 wall-clock guards."
    )

    assert GREENLET_COVERAGE_RC.is_file()
    greenlet_config = configparser.ConfigParser()
    greenlet_config.read(GREENLET_COVERAGE_RC, encoding="utf-8")
    concurrency = {
        value.strip()
        for value in greenlet_config.get("run", "concurrency").splitlines()
        if value.strip()
    }
    assert concurrency == {"greenlet", "thread"}, (
        "CI coverage regression (beta.3 run 30517080961): coverage must enable "
        "both greenlet and thread concurrency or lines resumed after async "
        "SQLAlchemy awaits silently disappear from per-file coverage."
    )

    workflow = _load_build_workflow()
    test_steps = [
        step
        for step in workflow["jobs"]["test-shard"]["steps"]
        if str(step.get("name", "")).startswith("Test (shard ")
    ]
    assert len(test_steps) == 1
    test_step = test_steps[0]
    coverage_config = str(test_step.get("env", {}).get("COVERAGE_CONFIG", ""))
    assert coverage_config == (
        "${{ matrix.shard == 'unit-3' "
        "&& '.coveragerc-greenlet' || 'pyproject.toml' }}"
    )
    assert '--cov-config="$COVERAGE_CONFIG"' in str(test_step.get("run", ""))


def test_test_shards_cap_xdist_for_nested_process_headroom() -> None:
    """Guard: hosted shards must leave RAM for nested Node/process tests."""
    workflow = _load_build_workflow()
    job = workflow["jobs"]["test-shard"]
    test_steps = [
        step
        for step in job["steps"]
        if str(step.get("name", "")).startswith("Test (shard ")
    ]

    assert len(test_steps) == 1
    test_env = test_steps[0].get("env", {})
    assert str(test_env.get("GLUDD_XDIST")) in {
        "2",
        "${{ matrix.shard == 'unit-1a1' && '1' || '2' }}",
    }, (
        "CI resource regression: the hosted adaptive runner must be explicitly "
        "capped at no more than two xdist workers so nested Node and subprocess "
        "tests retain headroom on the 7 GiB runner."
    )
    all_envs = [job.get("env", {}), *[step.get("env", {}) for step in job["steps"]]]
    assert all("GLUDD_TEST_WORKER_MEM_MB" not in env for env in all_envs), (
        "CI resource regression: the obsolete RLIMIT_AS override must stay "
        "absent; V8 needs a large contiguous virtual address range."
    )


def test_node_heavy_unit_1a1_shard_runs_serially() -> None:
    """Guard: plugin syntax subprocess checks get a fresh serial test process.

    Hosted beta.3 run 30494011946 exhausted V8 code-range reservations when
    Python 3.12's unit-1a1 shard ran two xdist workers. Run 30495510250 then
    proved serializing all 1,705 shard tests retained too much memory in one
    process on both Python versions. The Node-heavy file must run alone.
    """
    workflow = _load_build_workflow()
    isolated_steps = [
        step
        for step in workflow["jobs"]["test-shard"]["steps"]
        if step.get("name") == "Run isolated Node plugin syntax in fresh process"
    ]

    assert len(isolated_steps) == 1
    command = str(isolated_steps[0].get("run", ""))
    assert "uv run pytest ${{ matrix.isolated_testpaths }}" in command
    assert "adaptive_test.py" not in command and " -n " not in command, (
        "CI resource regression: the Node plugin syntax suite must run serially "
        "in its own short-lived pytest process."
    )


def test_node_plugin_syntax_suite_runs_in_fresh_pytest_process() -> None:
    """Guard the both-Python V8 CodeRange failure from run 30495510250.

    Serializing the whole unit-1a1 shard retained all 1,705 tests in one Python
    process and still exhausted the runner before ``node --check``.  The
    Node-heavy file must instead run in a fresh, coverage-free pytest process
    and be excluded from the long-lived coverage process.
    """
    workflow = _load_build_workflow()
    job = workflow["jobs"]["test-shard"]
    unit_1a1 = next(
        entry
        for entry in job["strategy"]["matrix"]["include"]
        if entry.get("shard") == "unit-1a1"
    )
    assert "*/test_all_plugins_runtime.py" in str(unit_1a1.get("exclude", "")).split()
    assert str(unit_1a1.get("isolated_testpaths", "")).split() == [
        "tests/unit/test_all_plugins_runtime.py"
    ]

    steps = job["steps"]
    isolated_index = next(
        index
        for index, step in enumerate(steps)
        if step.get("name") == "Run isolated Node plugin syntax in fresh process"
    )
    shard_index = next(
        index
        for index, step in enumerate(steps)
        if str(step.get("name", "")).startswith("Test (shard ")
    )
    isolated = steps[isolated_index]

    assert isolated_index < shard_index
    assert isolated.get("if") == "matrix.shard == 'unit-1a1'"
    isolated_run = str(isolated.get("run", ""))
    assert "${{ matrix.isolated_testpaths }}" in isolated_run
    assert "--cov" not in isolated_run
