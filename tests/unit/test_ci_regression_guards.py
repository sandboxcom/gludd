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


def test_windows_job_is_non_blocking() -> None:
    """Guard: the windows build job must keep continue-on-error: true.

    CI incident: the Windows pyinstaller build is not yet reliable. Keeping the
    windows job non-blocking (``continue-on-error: true``) is the safety net that
    lets a tag still publish a Release with the linux/macos/termux artifacts
    instead of the whole release failing on Windows. Removing it makes a flaky
    Windows build block every release.
    """
    wf = _load_build_workflow()
    windows = wf["jobs"].get("windows")
    assert windows is not None, (
        "CI regression: the 'windows' build job vanished from build.yml."
    )
    assert windows.get("continue-on-error") is True, (
        "CI regression (unstable Windows pyinstaller build blocking releases): "
        "the windows job no longer has 'continue-on-error: true'. Restore it so "
        "an unstable Windows build stays NON-BLOCKING and a tag still ships the "
        "linux/macos/termux artifacts."
    )


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
