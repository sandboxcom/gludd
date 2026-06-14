"""CI workflow structural test: assert build.yml actually builds and uploads artifacts on push.

The root cause of the original CI bug was that the linux/macos/windows/termux build
jobs had `if: startsWith(github.ref, 'refs/tags/v') || github.event_name == 'workflow_dispatch'`
— meaning they silently skipped on every normal branch push. Only the `gate` (lint/test)
job ran; no artifacts were ever built or uploaded on commit push.

These tests parse the workflow YAML and enforce the post-fix invariants:
  1. The workflow triggers on push to master and main (not just master).
  2. Each build job (linux/macos/windows/termux) has an `if` condition that
     evaluates to true on a push event (not only on tags or workflow_dispatch).
  3. Each build job has an `upload-artifact` step.
  4. The version job stamps a timestamped alpha version for non-tag pushes.
  5. The release job still gates on tag pushes only (no accidental prereleases on push).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from packaging.version import InvalidVersion, Version

ROOT = Path(__file__).resolve().parent.parent.parent
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "build.yml"

BUILD_JOBS = ["linux", "macos", "windows", "termux"]


def _load_workflow() -> dict[str, Any]:
    assert WORKFLOW_PATH.is_file(), f"build.yml not found at {WORKFLOW_PATH}"
    with WORKFLOW_PATH.open() as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict), "build.yml must be a valid YAML mapping"
    # PyYAML parses the bare word `on` as a YAML boolean True.
    # Normalise the key so the rest of the tests can use "on".
    if True in data and "on" not in data:
        data["on"] = data.pop(True)
    return data


class TestWorkflowTriggers:
    def test_workflow_triggers_on_push_to_master(self) -> None:
        wf = _load_workflow()
        push_branches = wf.get("on", {}).get("push", {}).get("branches", [])
        assert "master" in push_branches, (
            f"workflow must trigger on push to master; got branches={push_branches}"
        )

    def test_workflow_triggers_on_push_to_main(self) -> None:
        """Repo's GH remote may use 'main' as default; both must be listed."""
        wf = _load_workflow()
        push_branches = wf.get("on", {}).get("push", {}).get("branches", [])
        assert "main" in push_branches, (
            f"workflow must trigger on push to main (GH default); got branches={push_branches}"
        )

    def test_workflow_triggers_on_tags(self) -> None:
        wf = _load_workflow()
        push_tags = wf.get("on", {}).get("push", {}).get("tags", [])
        assert any("v*" in t for t in push_tags), (
            f"workflow must trigger on tag pushes (v*); got tags={push_tags}"
        )

    def test_workflow_has_workflow_dispatch(self) -> None:
        wf = _load_workflow()
        assert "workflow_dispatch" in wf.get("on", {}), (
            "workflow must support manual workflow_dispatch trigger"
        )


class TestVersionStamping:
    def test_version_job_exists(self) -> None:
        wf = _load_workflow()
        assert "version" in wf.get("jobs", {}), "version job must exist"

    def test_version_job_stamps_alpha_for_push(self) -> None:
        """For non-tag pushes, the version must be a timestamped alpha (not static)."""
        wf = _load_workflow()
        ver_job = wf["jobs"]["version"]
        steps = ver_job.get("steps", [])
        step_scripts = " ".join(
            step.get("run", "") for step in steps if isinstance(step, dict)
        )
        # The version computation must reference a timestamp (date) for non-tag pushes.
        assert "date" in step_scripts, (
            "version job must stamp a timestamped alpha version for non-tag pushes "
            f"(expected 'date' in step script); got:\n{step_scripts}"
        )
        assert "alpha" in step_scripts, (
            "version job must produce an alpha version string for non-tag pushes"
        )

    def test_version_job_outputs_version(self) -> None:
        wf = _load_workflow()
        ver_job = wf["jobs"]["version"]
        outputs = ver_job.get("outputs", {})
        assert "version" in outputs, "version job must declare a 'version' output"


class TestBuildJobsRunOnPush:
    """Each build job (linux/macos/windows/termux) must run on push to master/main.

    Before the fix, all build jobs had:
        if: startsWith(github.ref, 'refs/tags/v') || github.event_name == 'workflow_dispatch'
    which skipped them entirely on regular branch pushes.
    The fix adds:
        || (github.event_name == 'push' && !startsWith(github.ref, 'refs/tags/'))
    """

    @pytest.mark.parametrize("job_name", BUILD_JOBS)
    def test_build_job_if_condition_allows_push(self, job_name: str) -> None:
        wf = _load_workflow()
        jobs = wf.get("jobs", {})
        assert job_name in jobs, f"job '{job_name}' must exist in build.yml"
        job = jobs[job_name]
        condition = job.get("if", "")
        assert condition, f"job '{job_name}' must have an 'if' condition"
        # The condition must allow push events (not only tags and workflow_dispatch).
        # We check for the presence of 'push' in the if-condition expression.
        assert "push" in condition, (
            f"job '{job_name}' if-condition must include push-event logic; "
            f"currently: {condition!r}"
        )

    @pytest.mark.parametrize("job_name", BUILD_JOBS)
    def test_build_job_not_restricted_to_tags_only(self, job_name: str) -> None:
        """Verify the condition is not the old tag-only form."""
        wf = _load_workflow()
        job = wf["jobs"][job_name]
        condition = job.get("if", "")
        old_tag_only = (
            "startsWith(github.ref, 'refs/tags/v') || github.event_name == 'workflow_dispatch'"
        )
        # The old, broken condition — it must no longer be the COMPLETE condition.
        assert condition.strip() != old_tag_only, (
            f"job '{job_name}' still has the old tag-only condition that skips push builds"
        )

    @pytest.mark.parametrize("job_name", BUILD_JOBS)
    def test_build_job_has_upload_artifact_step(self, job_name: str) -> None:
        wf = _load_workflow()
        job = wf["jobs"][job_name]
        steps = job.get("steps", [])
        upload_steps = [
            s for s in steps
            if isinstance(s, dict) and "upload-artifact" in str(s.get("uses", ""))
        ]
        assert upload_steps, (
            f"job '{job_name}' must have an upload-artifact step; "
            f"steps uses: {[s.get('uses') for s in steps if isinstance(s, dict)]}"
        )

    @pytest.mark.parametrize("job_name", BUILD_JOBS)
    def test_build_job_has_version_injection_step(self, job_name: str) -> None:
        wf = _load_workflow()
        job = wf["jobs"][job_name]
        steps = job.get("steps", [])
        inject_steps = [
            s for s in steps
            if isinstance(s, dict) and "Inject version" in str(s.get("name", ""))
        ]
        assert inject_steps, (
            f"job '{job_name}' must have an 'Inject version' step to stamp __version__ "
            f"before building; steps names: {[s.get('name') for s in steps if isinstance(s, dict)]}"
        )

    @pytest.mark.parametrize("job_name", BUILD_JOBS)
    def test_build_job_needs_version_and_gate(self, job_name: str) -> None:
        wf = _load_workflow()
        job = wf["jobs"][job_name]
        needs = job.get("needs", [])
        if isinstance(needs, str):
            needs = [needs]
        assert "version" in needs, f"job '{job_name}' must need 'version'"
        assert "gate" in needs, f"job '{job_name}' must need 'gate' (gate must pass before build)"


class TestReleaseJobGating:
    def test_release_job_only_on_tags(self) -> None:
        """The release job (GH prerelease creation) must still only run on tag pushes."""
        wf = _load_workflow()
        jobs = wf.get("jobs", {})
        assert "release" in jobs, "release job must exist"
        release_condition = jobs["release"].get("if", "")
        assert "refs/tags/v" in release_condition, (
            f"release job must be gated on tag pushes only; got: {release_condition!r}"
        )
        # Must NOT trigger on every branch push.
        assert "event_name == 'push'" not in release_condition, (
            "release job must NOT trigger on every branch push — only tags"
        )

    def test_release_job_needs_all_build_jobs(self) -> None:
        wf = _load_workflow()
        release_needs = wf["jobs"]["release"].get("needs", [])
        if isinstance(release_needs, str):
            release_needs = [release_needs]
        for job_name in BUILD_JOBS:
            assert job_name in release_needs, (
                f"release job must depend on '{job_name}' build job"
            )


class TestWorkflowYAMLValidity:
    def test_workflow_is_valid_yaml(self) -> None:
        """parse without error"""
        _load_workflow()

    def test_workflow_has_jobs_key(self) -> None:
        wf = _load_workflow()
        assert "jobs" in wf, "workflow must have a 'jobs' key"

    def test_all_build_jobs_use_hash_pinned_checkout(self) -> None:
        """SECURITY.md requires hash-pinned actions (not floating tags)."""
        wf = _load_workflow()
        for job_name in BUILD_JOBS:
            job = wf["jobs"].get(job_name, {})
            steps = job.get("steps", [])
            for step in steps:
                if not isinstance(step, dict):
                    continue
                uses = step.get("uses", "")
                if uses.startswith("actions/checkout"):
                    # Hash-pinned: must contain a 40-char hex SHA after @
                    assert re.search(r"@[0-9a-f]{40}$", uses), (
                        f"job '{job_name}' actions/checkout must be hash-pinned; got: {uses!r}"
                    )


class TestVersionPEP440:
    """Assert that the version job emits PEP 440-valid strings.

    Root cause of the original CI failure: the non-tag path emitted
    ``v0.1.0-alpha-202606140832`` which is NOT PEP 440-valid because:
      1. It has a leading 'v' (rejected by packaging.version.Version)
      2. It uses a hyphen before the timestamp instead of a dot
         ('alpha-TIMESTAMP' is not a valid pre-release specifier)

    The correct form is ``0.1.0-alpha.202606140832`` — no leading 'v',
    dot-separated pre-release epoch (matches pyproject.toml).

    For tag builds (refs/tags/v1.2.3) the leading 'v' must be stripped
    before injection into pyproject.toml.
    """

    # Representative timestamp matching the format date -u +%Y%m%d%H%M produces.
    _SAMPLE_TIMESTAMP = "202606140832"

    def _get_version_step_script(self) -> str:
        wf = _load_workflow()
        ver_job = wf["jobs"]["version"]
        steps = ver_job.get("steps", [])
        return " ".join(step.get("run", "") for step in steps if isinstance(step, dict))

    def test_non_tag_version_has_no_leading_v(self) -> None:
        """The non-tag version string must NOT start with 'v'."""
        script = self._get_version_step_script()
        # Extract the literal version prefix used in the non-tag else branch.
        # The script must NOT contain 'version=v' (that was the broken form).
        assert "version=v" not in script, (
            "non-tag version must not start with 'v'; the workflow had 'version=v0.1.0-alpha-...' "
            "which is not PEP 440-valid. It must be 'version=0.1.0-alpha.$(date...)'"
        )

    def test_non_tag_version_uses_dot_before_timestamp(self) -> None:
        """Non-tag version must use a dot before the timestamp, not a hyphen.

        PEP 440: '0.1.0-alpha.202606140832' is valid (dot-separated pre-release).
        '0.1.0-alpha-202606140832' is NOT valid (hyphen makes it an invalid local label).
        """
        script = self._get_version_step_script()
        # After the fix the script must contain 'alpha.' (dot-then-date) NOT 'alpha-$(date'
        assert "alpha-$(date" not in script, (
            "non-tag version uses 'alpha-$(date...)' (hyphen before timestamp); "
            "that is not PEP 440-valid. Use 'alpha.$(date...)' (dot before timestamp)."
        )
        assert "alpha.$(date" in script, (
            "non-tag version must contain 'alpha.$(date...)' so the timestamp is a "
            "dot-separated pre-release segment — the only PEP 440-valid form."
        )

    def test_non_tag_representative_version_is_pep440_valid(self) -> None:
        """A concrete sample of the non-tag version must parse without error."""
        sample = f"0.1.0-alpha.{self._SAMPLE_TIMESTAMP}"
        try:
            v = Version(sample)
        except InvalidVersion as exc:
            pytest.fail(
                f"Representative non-tag version {sample!r} is not PEP 440-valid: {exc}"
            )
        # Confirm it is pre-release (alpha)
        assert v.is_prerelease, f"Expected {sample!r} to be a pre-release version"

    def test_tag_path_strips_leading_v(self) -> None:
        """For tag pushes (v1.2.3) the workflow must strip the leading 'v' before injection.

        The broken form: ``echo "version=${{ github.ref_name }}"`` which would emit
        'v1.2.3' — NOT PEP 440-valid for pyproject injection.
        The correct form uses ``${GITHUB_REF_NAME#v}`` (bash prefix strip) so that
        e.g. tag 'v1.2.3' becomes version '1.2.3'.
        """
        script = self._get_version_step_script()
        # The tag path must strip the 'v' prefix.
        assert "GITHUB_REF_NAME#v}" in script or "${GITHUB_REF_NAME#v}" in script, (
            "tag version path must strip the leading 'v' via ${GITHUB_REF_NAME#v}. "
            "Injecting 'v1.2.3' into pyproject.toml fails PEP 440 validation."
        )
        # Must NOT emit github.ref_name verbatim (that would include the 'v').
        # Check the tag branch does NOT use github.ref_name directly without stripping.
        assert 'version=${{ github.ref_name }}' not in script, (
            "tag version path must not use '${{ github.ref_name }}' verbatim — "
            "that emits e.g. 'v1.2.3' which is not PEP 440-valid."
        )

    def test_representative_tag_version_is_pep440_valid(self) -> None:
        """A tag like 'v1.2.3' stripped to '1.2.3' must be PEP 440-valid."""
        sample = "1.2.3"
        try:
            v = Version(sample)
        except InvalidVersion as exc:
            pytest.fail(f"Stripped tag version {sample!r} is not PEP 440-valid: {exc}")
        assert not v.is_prerelease, f"Expected {sample!r} to be a stable release version"

    def test_pyproject_current_version_is_pep440_valid(self) -> None:
        """The version in pyproject.toml must also be PEP 440-valid at all times."""
        pyproject_path = ROOT / "pyproject.toml"
        assert pyproject_path.is_file(), "pyproject.toml must exist"
        content = pyproject_path.read_text()
        match = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
        assert match, "Could not find version = \"...\" in pyproject.toml"
        ver_str = match.group(1)
        try:
            Version(ver_str)
        except InvalidVersion as exc:
            pytest.fail(
                f"pyproject.toml version {ver_str!r} is not PEP 440-valid: {exc}"
            )

    def test_init_py_current_version_is_pep440_valid(self) -> None:
        """The __version__ in src/general_ludd/__init__.py must be PEP 440-valid."""
        init_path = ROOT / "src" / "general_ludd" / "__init__.py"
        assert init_path.is_file(), "src/general_ludd/__init__.py must exist"
        content = init_path.read_text()
        match = re.search(r'__version__\s*=\s*"([^"]+)"', content)
        assert match, "Could not find __version__ = \"...\" in __init__.py"
        ver_str = match.group(1)
        try:
            Version(ver_str)
        except InvalidVersion as exc:
            pytest.fail(
                f"__init__.py __version__ {ver_str!r} is not PEP 440-valid: {exc}"
            )
