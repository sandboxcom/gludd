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

    def test_release_job_tag_name_matches_git_tag(self) -> None:
        """The release job's tag_name MUST use github.ref_name (the real git tag,
        e.g. ``v0.1.0-alpha.2``), NOT env.VERSION (which strips the leading ``v``
        to produce the PEP 440 version ``0.1.0-alpha.2``).

        Root cause of the alpha.2/alpha.3 missing-artifact bug: ``tag_name`` was
        set to ``${{ env.VERSION }}`` = ``0.1.0-alpha.2``, which does not match
        the pushed git tag ``v0.1.0-alpha.2``. ``softprops/action-gh-release``
        therefore created a release for a non-existent tag, so assets were never
        attached to the real tag — ``make verify-release-artifact`` reported zero
        assets.
        """
        wf = _load_workflow()
        release_steps = wf["jobs"]["release"].get("steps", [])
        gh_release_steps = [
            s for s in release_steps
            if isinstance(s, dict) and "softprops/action-gh-release" in s.get("uses", "")
        ]
        assert gh_release_steps, "release job must use softprops/action-gh-release"
        with_field = gh_release_steps[0].get("with", {})
        tag_name = with_field.get("tag_name", "")
        assert "${{ github.ref_name" in tag_name, (
            f"release job tag_name must use github.ref_name (the real git tag with 'v' prefix); "
            f"got: {tag_name!r}. Using env.VERSION (without 'v') creates a release for a "
            f"non-existent tag, so assets never attach to the real pushed tag."
        )

    def test_release_job_includes_sbom_and_licenses(self) -> None:
        """The release job must stage sbom.json, LICENSE, and THIRD_PARTY_LICENSES.md
        into the release assets, not just the platform tarballs."""
        wf = _load_workflow()
        release_steps = wf["jobs"]["release"].get("steps", [])
        all_run_text = " ".join(
            s.get("run", "") for s in release_steps if isinstance(s, dict)
        )
        assert "sbom" in all_run_text.lower(), (
            "release job must generate/stage the SBOM"
        )
        assert "LICENSE" in all_run_text, (
            "release job must stage LICENSE into release assets"
        )
        assert "THIRD_PARTY_LICENSES" in all_run_text, (
            "release job must stage THIRD_PARTY_LICENSES.md into release assets"
        )

    def test_release_job_stages_all_platform_tarballs(self) -> None:
        """The release job must download and stage ALL platform tarballs.

        The build jobs upload artifacts named:
          - gludd-linux-x86_64   (linux-amd64 tarball)
          - gludd-macos-arm64    (macos-arm64 tarball)
          - gludd-windows-x86_64 (windows-amd64 zip)
          - gludd-linux-aarch64  (linux-aarch64 / termux tarball)

        The release job's download-artifact step must use a pattern broad
        enough to capture all of them (e.g. ``gludd-*``), not a single name.
        """
        wf = _load_workflow()
        release_steps = wf["jobs"]["release"].get("steps", [])
        download_steps = [
            s for s in release_steps
            if isinstance(s, dict) and "download-artifact" in s.get("uses", "")
        ]
        assert download_steps, (
            "release job must have a download-artifact step to pull platform tarballs"
        )
        # The pattern field must be broad enough to match all gludd-* artifacts.
        patterns = [str(s.get("with", {}).get("pattern", "")) for s in download_steps]
        assert any("gludd-*" in p or "*" in p for p in patterns), (
            "release download-artifact pattern must match all gludd-* artifacts; "
            f"got patterns={patterns}"
        )

    def test_release_job_cleans_stale_release(self) -> None:
        """The release job must delete any pre-existing release for the tag
        before creating a new one.

        Root cause of the alpha.2 "release already_exists" failure: a prior run
        created the release record but failed mid-asset-upload. The next run's
        ``softprops/action-gh-release`` then errored on the duplicate. The fix
        is a ``gh release delete ... --yes`` step before the create step.
        """
        wf = _load_workflow()
        release_steps = wf["jobs"]["release"].get("steps", [])
        all_run_text = " ".join(
            s.get("run", "") for s in release_steps if isinstance(s, dict)
        )
        assert "gh release delete" in all_run_text, (
            "release job must have a 'gh release delete' cleanup step to handle "
            "the 'release already_exists' error from action-gh-release"
        )
        # The cleanup must target the tag being released (github.ref_name).
        assert "github.ref_name" in all_run_text, (
            "release cleanup step must target ${{ github.ref_name }}"
        )

    def test_release_job_verifies_assets_after_publish(self) -> None:
        """The release job must verify the published release has assets (>= 4).

        This catches the alpha.2/alpha.3 failure mode where a tag was pushed and
        a release record existed, but zero assets were attached (CI skipped the
        release job or it errored mid-upload). A post-release verification step
        makes that failure loud instead of silent.
        """
        wf = _load_workflow()
        release_steps = wf["jobs"]["release"].get("steps", [])
        # Find the step(s) AFTER the softprops/action-gh-release step.
        gh_release_idx = None
        for i, s in enumerate(release_steps):
            if isinstance(s, dict) and "softprops/action-gh-release" in s.get("uses", ""):
                gh_release_idx = i
                break
        assert gh_release_idx is not None, "release job must use softprops/action-gh-release"
        post_release_steps = release_steps[gh_release_idx + 1:]
        assert post_release_steps, (
            "release job must have at least one step after action-gh-release "
            "(the post-release asset verification step)"
        )
        post_run_text = " ".join(
            s.get("run", "") for s in post_release_steps if isinstance(s, dict)
        )
        assert "gh release view" in post_run_text, (
            "post-release step must use 'gh release view' to inspect the published release"
        )
        assert "assets" in post_run_text, (
            "post-release step must inspect the release assets list"
        )
        # Must enforce a minimum asset count (>= 4: linux, macos, windows, termux
        # at minimum — usually plus SBOM, LICENSE, THIRD_PARTY_LICENSES).
        assert re.search(r"ASSET_COUNT|-lt\s+\d+|\.assets\s*\|\s*length", post_run_text), (
            "post-release step must assert a minimum asset count (>= 4)"
        )


    def test_release_job_generates_sha256sums_aggregate(self) -> None:
        """The release job must generate a SHA256SUMS aggregate file covering all
        staged assets and include it in the published release files.

        This gives consumers a single checksum manifest to verify every asset
        (tarballs, SBOM, licenses) in one ``sha256sum -c SHA256SUMS`` invocation,
        rather than verifying each per-file .sha256 sidecar individually.
        """
        wf = _load_workflow()
        release_steps = wf["jobs"]["release"].get("steps", [])
        all_run_text = " ".join(
            s.get("run", "") for s in release_steps if isinstance(s, dict)
        )
        assert "sha256sum *" in all_run_text or "sha256sum *" in all_run_text, (
            "release job must run 'sha256sum *' over the staged release assets to "
            "produce a SHA256SUMS aggregate manifest"
        )
        assert "SHA256SUMS" in all_run_text, (
            "release job must write the aggregate checksums to a file named SHA256SUMS"
        )
        # The SHA256SUMS file must land in release-assets/ so action-gh-release's
        # `files: release-assets/*` glob picks it up automatically.
        release_files_glob = ""
        for s in release_steps:
            if not isinstance(s, dict):
                continue
            if "softprops/action-gh-release" in s.get("uses", ""):
                with_field = s.get("with", {})
                release_files_glob = str(with_field.get("files", ""))
        assert "release-assets/*" in release_files_glob, (
            "release job action-gh-release 'files' must glob release-assets/* so the "
            "generated SHA256SUMS file is included as a release asset; "
            f"got files={release_files_glob!r}"
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


class TestGateMatrixStructure:
    """Validate the CI gate and related job matrix structures.

    Structural invariants that keep CI fast and informative:
    - The gate runs on two Python versions in parallel with fail-fast disabled
      so both versions' results are always reported.
    - test-shard splits the suite across 4 path-based groups x 2 Python versions
      so failures surface at job granularity in minutes.
    - molecule is also fail-fast:false so all 4 shards report results.
    """

    def test_gate_matrix_python_versions(self) -> None:
        wf = _load_workflow()
        gate_job = wf["jobs"]["gate"]
        strategy = gate_job.get("strategy", {})
        matrix = strategy.get("matrix", {})
        python_versions = matrix.get("python-version", [])
        assert python_versions == ["3.11", "3.12"], (
            f"gate job matrix.python-version must be ['3.11', '3.12']; "
            f"got {python_versions!r}"
        )

    def test_gate_matrix_fail_fast_is_false(self) -> None:
        wf = _load_workflow()
        gate_job = wf["jobs"]["gate"]
        strategy = gate_job.get("strategy", {})
        fail_fast = strategy.get("fail-fast", True)
        assert fail_fast is False, (
            "gate job strategy.fail-fast must be false so both Python versions "
            "always report results on every run"
        )

    def test_test_shard_matrix_dimensions(self) -> None:
        wf = _load_workflow()
        ts_job = wf["jobs"]["test-shard"]
        strategy = ts_job.get("strategy", {})
        matrix = strategy.get("matrix", {})
        assert "python-version" in matrix, (
            "test-shard matrix must include python-version dimension"
        )
        assert "shard" in matrix, (
            "test-shard matrix must include shard dimension"
        )
        assert matrix.get("python-version") == ["3.11", "3.12"], (
            f"test-shard python-version must be ['3.11', '3.12']; "
            f"got {matrix.get('python-version')!r}"
        )
        assert matrix.get("shard") == ["unit-1", "unit-2", "unit-3", "other"], (
            f"test-shard shard dimension must be ['unit-1', 'unit-2', 'unit-3', 'other']; "
            f"got {matrix.get('shard')!r}"
        )
        assert strategy.get("fail-fast") is False, (
            "test-shard strategy.fail-fast must be false so all shards report results"
        )

    def test_molecule_fail_fast_is_false(self) -> None:
        wf = _load_workflow()
        mol_job = wf["jobs"]["molecule"]
        strategy = mol_job.get("strategy", {})
        fail_fast = strategy.get("fail-fast", True)
        assert fail_fast is False, (
            "molecule job strategy.fail-fast must be false so all shards "
            "report results every run"
        )
