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
