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

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
SPEC = ROOT / "gludd.spec"
BUILD_YML = ROOT / ".github" / "workflows" / "build.yml"
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
    """Guard: the gate job must set GLUDD_XDIST_WORKERS=auto in its env.

    CI incident: with a single xdist worker the test suite exceeded the 40-minute
    CI gate budget (timeout-minutes: 40) and the job was killed. Setting
    ``GLUDD_XDIST_WORKERS: auto`` fans the suite across all available cores so it stays
    under budget. Dropping this env var re-introduces the timeout.
    """
    wf = _load_build_workflow()
    gate = wf["jobs"].get("gate")
    assert gate is not None, (
        "CI regression: the 'gate' job vanished from build.yml — lint/type/test would no longer run on push/PR."
    )
    # Env can live on the job or on the step that runs `make ... test`.
    job_env = gate.get("env") or {}
    step_envs: list[dict[str, Any]] = [s.get("env") or {} for s in gate.get("steps", []) if isinstance(s, dict)]
    all_envs = [job_env, *step_envs]
    found = any(str(e.get("GLUDD_XDIST_WORKERS")) == "auto" for e in all_envs)
    assert found, (
        "CI regression (gate exceeded the 40-min CI budget with a single xdist "
        "worker): the gate job no longer sets GLUDD_XDIST_WORKERS=auto. Restore "
        "'GLUDD_XDIST_WORKERS: auto' in the gate job/step env so the suite parallelizes "
        "across cores and stays under timeout-minutes: 40."
    )


def test_gate_runs_opa_policy_validation() -> None:
    """Guard: CI gate must execute the Terraform/IAM OPA policy suite.

    The provider deployment policies are a security boundary. A previous gate
    only exercised Python smoke tests, allowing Rego regressions to merge
    unnoticed. Keep the invocation on the gate job and route it through the
    repository's ``make test-opa-policies`` target so local and CI behavior
    remain identical.
    """
    wf = _load_build_workflow()
    gate = wf["jobs"].get("gate")
    assert gate is not None
    runs = "\n".join(str(step.get("run", "")) for step in gate.get("steps", []) if isinstance(step, dict))
    assert "make test-opa-policies" in runs, (
        "CI regression: gate no longer runs make test-opa-policies; Terraform "
        "and IAM OPA policies must be validated before merge."
    )


def test_opa_make_target_has_container_fallback() -> None:
    """Guard: OPA validation must work on runners without a host OPA binary."""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    start = makefile.find("test-opa-policies:")
    assert start >= 0, "Makefile lost the test-opa-policies target"
    block = makefile[start : makefile.find("\n\n", start)]
    assert "docker run" in block and "OPA_IMAGE" in block, (
        "OPA policy target must fall back to the pinned Docker OPA image when the host binary is unavailable."
    )


def test_windows_job_is_release_blocking() -> None:
    """A green release must prove that the required Windows artifacts built."""
    wf = _load_build_workflow()
    windows = wf["jobs"].get("windows")
    assert windows is not None, "CI regression: the 'windows' build job vanished from build.yml."
    assert windows.get("continue-on-error", False) is False, (
        "CI regression: Windows is a required beta.3 artifact producer; "
        "continue-on-error can turn a missing zip/installer into a green pipeline"
    )


def test_windows_job_uses_canonical_pinned_bootstrap_actions() -> None:
    """Windows must use the same maintained action revisions as Linux.

    Run 30331174104 never reached project code because its Windows-only
    checkout revision could not be resolved. Keeping one old checkout pin and
    a floating setup-uv tag made that job uniquely fragile while every other
    platform used the repository's canonical, immutable Node 24 revisions.
    """
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
            f"got windows={windows_uses}, linux={linux_uses}. Divergent bootstrap "
            "actions can fail before packaging and leave the release incomplete."
        )


def test_windows_packaging_job_is_deterministic_and_fail_closed() -> None:
    """Pin the runner/toolchain and reject partial or untested artifacts."""
    windows = _load_build_workflow()["jobs"]["windows"]
    assert windows["runs-on"] == "windows-2022", (
        "Windows packaging must pin windows-2022 instead of following the "
        "windows-latest image migration to an unqualified toolchain"
    )

    steps = windows["steps"]
    setup_uv = next(step for step in steps if str(step.get("uses", "")).startswith("astral-sh/setup-uv@"))
    assert setup_uv.get("with", {}).get("python-version") == "3.12"

    names = [str(step.get("name", "")) for step in steps]
    smoke_index = names.index("Smoke test binary")
    package_index = names.index("Package zip")
    upload_index = next(
        i for i, step in enumerate(steps) if str(step.get("uses", "")).startswith("actions/upload-artifact@")
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
    must have molecule.yml plus converge.yml/verify.yml (under ``default/`` or
    at the scenario root). The sole exception is Molecule 26's canonical
    discovery anchor: ``default/molecule.yml`` must stay a non-shared no-op
    (``shared_state: false`` and an empty test sequence), so it cannot execute
    lifecycle work.

    This iterates the committed scenarios at runtime, so a future incomplete
    scenario fails here BEFORE it can break `make molecule-test-all` in CI.
    """
    assert MOLECULE_PLAYBOOKS.is_dir(), (
        f"CI regression: {MOLECULE_PLAYBOOKS} is missing — no molecule scenarios "
        "to verify; `make molecule-test-all` would have nothing to run."
    )
    scenario_dirs = sorted(p for p in MOLECULE_PLAYBOOKS.iterdir() if p.is_dir())
    assert scenario_dirs, f"CI regression: no scenario directories under {MOLECULE_PLAYBOOKS}."

    incomplete: dict[str, list[str]] = {}
    required_layouts = (
        ("molecule.yml", "default/converge.yml", "default/verify.yml"),
        ("molecule.yml", "converge.yml", "verify.yml"),
    )
    for scenario in scenario_dirs:
        if not (scenario / "molecule.yml").is_file():
            continue
        if scenario.name == "default":
            anchor = scenario / "molecule.yml"
            if anchor.is_file():
                anchor_data = yaml.safe_load(anchor.read_text(encoding="utf-8"))
                # The playbooks default anchor mirrors the canonical
                # molecule/default/ discovery anchor: a non-shared no-op with
                # an empty test sequence so it cannot execute lifecycle work.
                # Inert provisioner metadata is permitted (the canonical
                # anchor ships the same block); what must stay is
                # shared_state:false + no lifecycle steps.
                if (
                    isinstance(anchor_data, dict)
                    and anchor_data.get("shared_state") is False
                    and anchor_data.get("scenario", {}).get("test_sequence") == []
                ):
                    continue
        if not any(all((scenario / rel).is_file() for rel in layout) for layout in required_layouts):
            incomplete[scenario.name] = [" or ".join(layout) for layout in required_layouts]

    assert not incomplete, (
        "CI regression (molecule-test-all failure, run 27596845359 — an "
        "unverified/structurally-incomplete scenario was committed): the "
        "following molecule/playbooks scenarios are missing a complete layout "
        f"{required_layouts}: {incomplete}. Every committed scenario must ship "
        "molecule.yml plus converge.yml/verify.yml (under default/ or at root) so "
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
        "CI regression: the 'release' job vanished from build.yml — tags would no longer publish a GitHub Release."
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
    coverage. Each shard must run pytest with ``--cov`` and a filesystem-bound
    source configuration (no shard may pass ``--no-cov``) so pre-imported
    modules and the downstream aggregate are both measured.
    """
    wf = _load_build_workflow()
    shard = wf["jobs"].get("test-shard")
    assert shard is not None, "CI regression: the 'test-shard' job vanished from build.yml."
    runs = "\n".join(
        str(step.get("run", ""))
        for step in shard.get("steps", [])
        if isinstance(step, dict)
    )
    assert "scripts/run_ci_shards_serial.py" in runs, (
        "CI regression: hosted shards no longer delegate to the canonical "
        "local/hosted runner"
    )
    runner = (ROOT / "scripts" / "run_ci_shards_serial.py").read_text(
        encoding="utf-8"
    )
    assert '"--cov"' in runner
    assert "--cov=general_ludd" not in runner
    coverage_config = (ROOT / ".coveragerc-greenlet").read_text(encoding="utf-8")
    assert "src/general_ludd" in coverage_config
    assert '"--cov-report="' in runner
    assert '"--cov-fail-under=0"' in runner
    assert "--no-cov" not in runner
    assert "_save_shard_coverage" in runner and "_aggregate_coverage" in runner


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
    step_blobs = ["\n".join(f"{k}: {v}" for k, v in s.items()) for s in cov.get("steps", [])]
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


def test_gate_pins_ci_evidence_to_event_sha() -> None:
    """The gate must prove it tested the exact commit that triggered GHA.

    A branch can advance while a queued run is starting.  An implicit checkout
    ref then makes local evidence ambiguous; release readiness must never rely
    on a stale-success run for a different commit.
    """
    wf = _load_build_workflow()
    gate = wf["jobs"]["gate"]
    checkouts = [step for step in gate["steps"] if str(step.get("uses", "")).startswith("actions/checkout@")]
    assert checkouts, "CI regression: gate has no actions/checkout step"
    assert checkouts[0].get("with", {}).get("ref") == "${{ github.sha }}", (
        "CI regression: gate checkout must pin ref to github.sha; implicit refs "
        "can test a moving branch instead of the triggering commit."
    )
    runs = "\n".join(str(step.get("run", "")) for step in gate["steps"])
    assert "git rev-parse HEAD" in runs and "GITHUB_SHA" in runs, (
        "CI regression: gate must assert checked-out HEAD equals GITHUB_SHA before producing release evidence."
    )


def test_all_build_checkouts_pin_event_sha() -> None:
    """Every Build and Release job must use the immutable triggering SHA."""
    wf = _load_build_workflow()
    unpinned: list[str] = []
    for name, job in wf["jobs"].items():
        for step in job.get("steps", []):
            if (
                str(step.get("uses", "")).startswith("actions/checkout@")
                and step.get("with", {}).get("ref") != "${{ github.sha }}"
            ):
                unpinned.append(str(name))
    assert not unpinned, (
        "CI regression: checkout steps without ref: ${{ github.sha }} in jobs "
        f"{sorted(set(unpinned))}; stale branch contents could be packaged."
    )
