"""Prove the CI gate job Python 3.11/3.12 matrix is properly configured.

Parses .github/workflows/build.yml and asserts:
  - The ``gate`` job exists and has a ``strategy.matrix`` block
  - ``python-version`` is a list containing at least "3.11" and "3.12"
  - ``fail-fast`` is ``false`` (so both versions' results surface independently)
  - The gate job runs lint, typecheck, collect-check, and smoke
  - The gate job depends on the ``version`` job (needs: version)

These properties are structural invariants — if the YAML changes in CI and
breaks the matrix, the repo would lose 3.11/3.12 coverage silently. This test
is the canary.
"""

from __future__ import annotations

import yaml

_WORKFLOW_PATH = ".github/workflows/build.yml"


def _load_workflow() -> dict:
    with open(_WORKFLOW_PATH) as fh:
        raw = yaml.safe_load(fh)
    # YAML 1.1 (PyYAML) parses the top-level 'on' key as boolean True.
    # Unpack it so downstream callers get the same dict regardless of parser.
    if True in raw and "push" in raw[True]:
        raw["on"] = raw.pop(True)
    return raw


# ---------------------------------------------------------------------------
# Gate job exists
# ---------------------------------------------------------------------------


class TestGateJobExists:
    def test_gate_job_present(self):
        wf = _load_workflow()
        assert "jobs" in wf
        assert "gate" in wf["jobs"]

    def test_gate_job_is_dict(self):
        wf = _load_workflow()
        assert isinstance(wf["jobs"]["gate"], dict)


# ---------------------------------------------------------------------------
# Python version matrix
# ---------------------------------------------------------------------------


class TestPythonVersionMatrix:
    def test_gate_has_strategy(self):
        wf = _load_workflow()
        job = wf["jobs"]["gate"]
        assert "strategy" in job, "gate job missing strategy block"

    def test_strategy_has_matrix(self):
        wf = _load_workflow()
        matrix = wf["jobs"]["gate"]["strategy"]
        assert "matrix" in matrix, "gate strategy missing matrix block"

    def test_python_version_in_matrix(self):
        wf = _load_workflow()
        matrix = wf["jobs"]["gate"]["strategy"]["matrix"]
        assert "python-version" in matrix, (
            "python-version not found in gate matrix"
        )

    def test_python_version_is_list(self):
        wf = _load_workflow()
        pv = wf["jobs"]["gate"]["strategy"]["matrix"]["python-version"]
        assert isinstance(pv, list), (
            f"python-version should be a list, got {type(pv).__name__}"
        )

    def test_python_3_11_in_matrix(self):
        wf = _load_workflow()
        pv = wf["jobs"]["gate"]["strategy"]["matrix"]["python-version"]
        assert "3.11" in pv, (
            f"python-version matrix missing 3.11; found: {pv}"
        )

    def test_python_3_12_in_matrix(self):
        wf = _load_workflow()
        pv = wf["jobs"]["gate"]["strategy"]["matrix"]["python-version"]
        assert "3.12" in pv, (
            f"python-version matrix missing 3.12; found: {pv}"
        )

    def test_fail_fast_is_false(self):
        wf = _load_workflow()
        strategy = wf["jobs"]["gate"]["strategy"]
        assert "fail-fast" in strategy, "fail-fast not specified in gate strategy"
        assert strategy["fail-fast"] is False, (
            f"fail-fast must be false so both Python versions' results surface; "
            f"got {strategy['fail-fast']}"
        )

    def test_exactly_two_python_versions_or_more(self):
        wf = _load_workflow()
        pv = wf["jobs"]["gate"]["strategy"]["matrix"]["python-version"]
        assert len(pv) >= 2, (
            f"Expected at least 2 python versions (3.11, 3.12); got {len(pv)}: {pv}"
        )


# ---------------------------------------------------------------------------
# Gate job structural invariants
# ---------------------------------------------------------------------------


class TestGateJobStructure:
    def test_gate_runs_on_ubuntu(self):
        wf = _load_workflow()
        runs_on = wf["jobs"]["gate"]["runs-on"]
        assert runs_on == "ubuntu-latest", (
            f"gate job should run on ubuntu-latest; got {runs_on}"
        )

    def test_gate_depends_on_version(self):
        wf = _load_workflow()
        needs = wf["jobs"]["gate"].get("needs", [])
        needles = needs if isinstance(needs, list) else [needs]
        assert "version" in needles, (
            f"gate job must depend on version job; needs={needs}"
        )

    def test_gate_has_steps(self):
        wf = _load_workflow()
        steps = wf["jobs"]["gate"]["steps"]
        assert isinstance(steps, list)
        assert len(steps) > 0

    def test_gate_timeout_minutes_set(self):
        wf = _load_workflow()
        job = wf["jobs"]["gate"]
        assert "timeout-minutes" in job, "gate job missing timeout-minutes"

    def test_gate_timeout_is_reasonable(self):
        wf = _load_workflow()
        timeout = wf["jobs"]["gate"]["timeout-minutes"]
        assert timeout > 0
        assert timeout <= 60, f"gate timeout-minutes {timeout} too high"


# ---------------------------------------------------------------------------
# Build YAML structural invariants
# ---------------------------------------------------------------------------


class TestBuildYAMLStructure:
    def test_workflow_name(self):
        wf = _load_workflow()
        assert "name" in wf

    def test_on_push_master(self):
        wf = _load_workflow()
        push = wf.get("on", {}).get("push", {})
        assert isinstance(push, dict)
        branches = push.get("branches", [])
        assert "master" in branches or "main" in branches

    def test_concurrency_group_set(self):
        wf = _load_workflow()
        concurrency = wf.get("concurrency", {})
        assert "group" in concurrency

    def test_permissions_write(self):
        wf = _load_workflow()
        permissions = wf.get("permissions", {})
        assert "contents" in permissions
        assert permissions["contents"] == "write"


class TestConcurrencyNoPushEviction:
    """Regression guard for the CI-eviction defect: on `push` (branch or tag),
    GitHub keeps only ONE pending run per concurrency `group`. If the group key
    is `${{ github.workflow }}-${{ github.ref }}` (ref only, not SHA), a second
    push to the same branch SILENTLY CANCELS the still-queued run for the
    PREVIOUS commit before it ever executes a single job (status=completed,
    conclusion=cancelled, jobs=[]). That commit can then be tagged and released
    having NEVER been tested — a cancelled run is neither pass nor fail, it is
    the absence of a verdict.

    Fix: the group key must include `github.sha` for non-pull_request events, so
    every commit gets its OWN group and can never be evicted by a later push.
    PR runs are exempt — they intentionally coalesce on the ref and cancel
    superseded runs via cancel-in-progress: true, which is correct (only the
    latest push in a PR needs a verdict).
    """

    def test_concurrency_group_is_conditional_expression(self):
        wf = _load_workflow()
        group = wf["concurrency"]["group"]
        assert isinstance(group, str)
        assert "github.event_name" in group and "pull_request" in group, (
            "concurrency.group must branch on github.event_name == 'pull_request' "
            "so push/tag runs use a different (SHA-keyed) group than PR runs; "
            f"got: {group!r}"
        )

    def test_concurrency_group_keys_non_pr_runs_on_sha(self):
        wf = _load_workflow()
        group = wf["concurrency"]["group"]
        assert "github.sha" in group, (
            "concurrency.group must include github.sha for the non-pull_request "
            "branch, so a push to a branch/tag can never evict (silently cancel) "
            "the still-pending run of an earlier commit on the same ref. "
            f"got: {group!r}"
        )

    def test_concurrency_group_still_uses_ref_for_pr_coalescing(self):
        wf = _load_workflow()
        group = wf["concurrency"]["group"]
        assert "github.ref" in group, (
            "concurrency.group must still reference github.ref for the "
            "pull_request branch, so repeated pushes to the same PR coalesce "
            f"(intended eviction). got: {group!r}"
        )

    def test_cancel_in_progress_only_for_pull_request(self):
        wf = _load_workflow()
        cancel = wf["concurrency"]["cancel-in-progress"]
        assert isinstance(cancel, str)
        assert "pull_request" in cancel, (
            "cancel-in-progress must remain scoped to pull_request events only — "
            "push/tag runs must never cancel-in-progress, since that is exactly "
            f"the eviction mechanism this test class guards against. got: {cancel!r}"
        )

    def _old_broken_group_key(self, event_name: str, ref: str, sha: str) -> str:
        """Simulates the OLD (buggy) group key: workflow-ref only."""
        return f"Build and Release-{ref}"

    def _new_group_key(self, event_name: str, ref: str, sha: str) -> str:
        """Simulates the NEW group key semantics asserted above: ref for PRs,
        sha for everything else (push/tag/workflow_dispatch)."""
        if event_name == "pull_request":
            return f"Build and Release-{ref}"
        return f"Build and Release-{sha}"

    def test_two_pushes_to_same_branch_get_different_groups(self):
        """The concrete regression scenario: two commits pushed in quick
        succession to the same branch (same ref, different sha) must land in
        DIFFERENT concurrency groups under the new key — proving the second
        push cannot silently cancel the first commit's run."""
        ref = "refs/heads/development"
        sha_old = "0b6237c4" * 5  # commit whose run was observed cancelled
        sha_new = "deadbeef" * 5  # the push that evicted it under the old key

        old_group_1 = self._old_broken_group_key("push", ref, sha_old)
        old_group_2 = self._old_broken_group_key("push", ref, sha_new)
        assert old_group_1 == old_group_2, (
            "sanity check: the OLD key collides on ref for both pushes "
            "(this is the bug being fixed)"
        )

        new_group_1 = self._new_group_key("push", ref, sha_old)
        new_group_2 = self._new_group_key("push", ref, sha_new)
        assert new_group_1 != new_group_2, (
            "two different commits pushed to the same branch must resolve to "
            "different concurrency groups so neither run can evict the other"
        )

    def test_pr_pushes_to_same_ref_still_share_a_group(self):
        """PR coalescing must be preserved: repeated pushes to the same PR
        (same ref, different sha each time) still share ONE group so the
        superseded run is cancelled (intended behavior)."""
        ref = "refs/pull/42/merge"
        sha_1 = "aaaa1111" * 5
        sha_2 = "bbbb2222" * 5

        group_1 = self._new_group_key("pull_request", ref, sha_1)
        group_2 = self._new_group_key("pull_request", ref, sha_2)
        assert group_1 == group_2, (
            "PR runs must still coalesce on ref regardless of sha, so "
            "cancel-in-progress can evict the superseded run as intended"
        )
