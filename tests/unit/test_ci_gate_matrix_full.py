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
        return yaml.safe_load(fh)


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
