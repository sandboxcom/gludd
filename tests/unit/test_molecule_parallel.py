"""Prove the CI ``molecule`` job runs scenarios in PARALLEL shards, not sequentially.

Parses ``.github/workflows/build.yml`` and asserts:

  - The ``molecule`` job exists.
  - It declares a ``strategy.matrix`` with a ``shard`` key holding >= 2 entries
    (parallel execution requires multiple matrix legs).
  - ``fail-fast`` is ``false`` so every shard reports its own result instead of
    cancelling the remaining legs on the first failure.
  - Each shard runs a SUBSET of scenarios — not the full suite in one leg. The
    run step invokes ``make molecule-test-shard SHARD=<n>/<total>``, which the
    Makefile implements as a contiguous slice of the sorted scenario list (see
    the ``molecule-test-shard`` target). A single leg running
    ``molecule-test-all`` would defeat parallelism.
  - ``max-parallel`` is either unset (GitHub Actions default = all legs run
    concurrently) or set to a value >= the shard count (no artificial throttle
    below the matrix size).

These properties are structural invariants — if the YAML drifts and collapses
the matrix back to a single sequential leg, the ~30 min wall time returns and
shard-level failure surfacing is lost. This test is the canary.
"""

from __future__ import annotations

import re
from typing import Any, cast

import yaml

_WORKFLOW_PATH = ".github/workflows/build.yml"


def _load_workflow() -> dict[str, Any]:
    with open(_WORKFLOW_PATH) as fh:
        raw = cast(dict[Any, Any], yaml.safe_load(fh))
    # YAML 1.1 (PyYAML) parses the top-level 'on' key as boolean True.
    # Unpack it so downstream callers get the same dict regardless of parser.
    if True in raw and "push" in raw[True]:
        raw["on"] = raw.pop(True)
    return cast(dict[str, Any], raw)


def _molecule_job() -> dict[str, Any]:
    wf = _load_workflow()
    assert "jobs" in wf, "build.yml has no 'jobs' mapping"
    assert "molecule" in wf["jobs"], (
        "CI regression: the 'molecule' job vanished from build.yml — molecule "
        "scenarios would no longer run in CI."
    )
    return cast(dict[str, Any], wf["jobs"]["molecule"])


# ---------------------------------------------------------------------------
# Job existence + structure
# ---------------------------------------------------------------------------


class TestMoleculeJobExists:
    def test_molecule_job_present(self) -> None:
        wf = _load_workflow()
        assert "molecule" in wf["jobs"], (
            "molecule job must exist in build.yml to run molecule scenarios in CI"
        )

    def test_molecule_job_is_dict(self) -> None:
        job = _molecule_job()
        assert isinstance(job, dict), "molecule job must be a mapping"

    def test_molecule_job_has_steps(self) -> None:
        job = _molecule_job()
        steps = job.get("steps")
        assert isinstance(steps, list) and steps, (
            "molecule job must have at least one step"
        )

    def test_molecule_depends_on_gate(self) -> None:
        job = _molecule_job()
        needs = job.get("needs", [])
        needles = needs if isinstance(needs, list) else [needs]
        assert "gate" in needles, (
            f"molecule job must depend on 'gate' so it only runs after the gate "
            f"passes; needs={needs!r}"
        )


# ---------------------------------------------------------------------------
# Matrix strategy with >= 2 shards
# ---------------------------------------------------------------------------


class TestMoleculeMatrixShards:
    def test_has_strategy(self) -> None:
        job = _molecule_job()
        assert "strategy" in job, "molecule job missing strategy block"

    def test_strategy_has_matrix(self) -> None:
        job = _molecule_job()
        strategy = job["strategy"]
        assert "matrix" in strategy, "molecule strategy missing matrix block"

    def test_matrix_has_shard_key(self) -> None:
        job = _molecule_job()
        matrix = job["strategy"]["matrix"]
        assert "shard" in matrix, (
            "molecule matrix must define a 'shard' dimension for parallelism"
        )

    def test_shard_is_list(self) -> None:
        job = _molecule_job()
        shard = job["strategy"]["matrix"]["shard"]
        assert isinstance(shard, list), (
            f"matrix.shard must be a list of shard identifiers; got "
            f"{type(shard).__name__}"
        )

    def test_at_least_two_shards(self) -> None:
        """Parallel execution requires >= 2 matrix legs. A single shard is
        functionally identical to running the whole suite sequentially — the
        wall-time savings that motivated sharding would be zero."""
        job = _molecule_job()
        shard = job["strategy"]["matrix"]["shard"]
        assert len(shard) >= 2, (
            f"matrix.shard must have >= 2 entries for parallel execution; "
            f"got {len(shard)}: {shard}"
        )

    def test_shard_values_are_distinct(self) -> None:
        """Duplicate shard ids would cause two legs to run the same slice and
        leave other slices unrun — a silent coverage hole."""
        job = _molecule_job()
        shard = job["strategy"]["matrix"]["shard"]
        assert len(set(shard)) == len(shard), (
            f"matrix.shard entries must be distinct; got duplicates: {shard}"
        )


# ---------------------------------------------------------------------------
# fail-fast disabled so every shard reports independently
# ---------------------------------------------------------------------------


class TestMoleculeFailFast:
    def test_fail_fast_is_false(self) -> None:
        job = _molecule_job()
        strategy = job["strategy"]
        assert "fail-fast" in strategy, (
            "molecule strategy must set fail-fast explicitly"
        )
        assert strategy["fail-fast"] is False, (
            f"fail-fast must be false so all shard results surface; got "
            f"{strategy['fail-fast']}"
        )


# ---------------------------------------------------------------------------
# Each shard runs a SUBSET of scenarios (not the whole suite in one leg)
# ---------------------------------------------------------------------------


class TestMoleculeShardSubset:
    """The whole point of the matrix is that each leg runs a DIFFERENT slice
    of the scenario list. The run step must invoke the sharded runner
    (``molecule-test-shard SHARD=<n>/<total>``) — NOT ``molecule-test-all``,
    which would run every scenario in every leg (Nx duplicate work) and
    defeat parallelism."""

    def test_run_step_invokes_sharded_runner(self) -> None:
        job = _molecule_job()
        run_cmds: list[str] = []
        for step in job["steps"]:
            run = step.get("run")
            if isinstance(run, str):
                run_cmds.append(run)
        joined = "\n".join(run_cmds)
        assert "molecule-test-shard" in joined, (
            "molecule job must invoke `make molecule-test-shard` (the sharded "
            "runner) so each matrix leg runs only its slice of scenarios; "
            "found run commands:\n" + joined
        )

    def test_run_step_passes_shard_expression(self) -> None:
        """The SHARD argument must be parameterized by the matrix value, not
        hardcoded — otherwise every leg runs the same slice."""
        job = _molecule_job()
        run_cmds: list[str] = []
        for step in job["steps"]:
            run = step.get("run")
            if isinstance(run, str) and "molecule-test-shard" in run:
                run_cmds.append(run)
        assert run_cmds, "no step invokes molecule-test-shard"
        assert any("matrix.shard" in cmd for cmd in run_cmds), (
            "molecule-test-shard must be parameterized by ${{ matrix.shard }} "
            "so each leg runs a different slice; found: " + "|".join(run_cmds)
        )

    def test_run_step_does_not_run_all_scenarios(self) -> None:
        """A shard leg must NOT run ``molecule-test-all`` (the full-suite
        runner) — that would duplicate work across every leg and defeat the
        purpose of sharding."""
        job = _molecule_job()
        for step in job["steps"]:
            run = step.get("run")
            if isinstance(run, str):
                assert "molecule-test-all" not in run, (
                    "molecule shard step must not invoke molecule-test-all "
                    "(full-suite runner) — use molecule-test-shard instead. "
                    f"offending step run: {run!r}"
                )

    def test_shard_is_single_pass_and_does_not_retry_completed_scenarios(self) -> None:
        """A failed scenario must not rerun every earlier passing scenario."""
        job = _molecule_job()
        run_steps = [
            step
            for step in job["steps"]
            if isinstance(step.get("run"), str)
            and "molecule-test-shard" in step["run"]
        ]
        assert len(run_steps) == 1
        command = run_steps[0]["run"]
        assert "retry_molecule" not in command
        assert "Molecule attempt" not in command
        assert "sleep $delay" not in command
        assert run_steps[0]["timeout-minutes"] >= 40

    def test_shard_uses_fractional_form(self) -> None:
        """The SHARD arg must be in ``<n>/<total>`` form so the Makefile can
        slice the scenario list. A bare shard id (e.g. ``SHARD=1``) would not
        tell the runner how many total shards exist."""
        job = _molecule_job()
        run_cmds: list[str] = []
        for step in job["steps"]:
            run = step.get("run")
            if isinstance(run, str) and "molecule-test-shard" in run:
                run_cmds.append(run)
        assert run_cmds, "no step invokes molecule-test-shard"
        # Look for SHARD=<expr>/<total> on the same line as molecule-test-shard.
        # The actual form in build.yml is: SHARD=${{ matrix.shard }}/4 — note the
        # GitHub Actions expression contains spaces, so \S* won't span it. Match
        # any non-newline chars between SHARD= and the /<total> denominator.
        pattern = re.compile(r"SHARD=[^\n]*/[^\n]+")
        assert any(pattern.search(cmd) for cmd in run_cmds), (
            "molecule-test-shard must be invoked with SHARD=<n>/<total> "
            "fractional form so the runner knows the total shard count; "
            "found: " + "|".join(run_cmds)
        )


# ---------------------------------------------------------------------------
# Parallel-execution design (max-parallel)
# ---------------------------------------------------------------------------


class TestMoleculeMaxParallel:
    """``max-parallel`` controls how many matrix legs run concurrently. If
    unset, GitHub Actions defaults to running ALL legs in parallel (the
    desired behavior for sharding). If set, it must be >= the shard count —
    a value below the shard count would serialize legs and defeat the
    wall-time savings."""

    def test_max_parallel_absent_or_ge_shard_count(self) -> None:
        job = _molecule_job()
        strategy = job["strategy"]
        shard_count = len(strategy["matrix"]["shard"])
        max_parallel = strategy.get("max-parallel")
        if max_parallel is None:
            # GitHub Actions default: all legs run concurrently. This is the
            # desired behavior for sharding — no assertion needed.
            return
        assert isinstance(max_parallel, int), (
            f"max-parallel must be an int if set; got {type(max_parallel).__name__}"
        )
        assert max_parallel >= shard_count, (
            f"max-parallel ({max_parallel}) must be >= shard count "
            f"({shard_count}) so legs are not serialized below the matrix size"
        )


# ---------------------------------------------------------------------------
# Observability: each shard uploads its own log artifact
# ---------------------------------------------------------------------------


class TestMoleculeShardArtifacts:
    """Each shard leg uploads its own per-shard artifact so a failure can be
    diagnosed without reproducing locally. The artifact name must be
    parameterized by ``matrix.shard`` so legs don't collide."""

    def test_upload_artifact_step_exists(self) -> None:
        job = _molecule_job()
        upload_steps = [
            s for s in job["steps"]
            if "uses" in s and "upload-artifact" in s.get("uses", "")
        ]
        assert upload_steps, (
            "molecule job must have at least one actions/upload-artifact step "
            "to surface per-scenario logs in CI"
        )

    def test_artifact_name_parameterized_by_shard(self) -> None:
        job = _molecule_job()
        upload_steps = [
            s for s in job["steps"]
            if "uses" in s and "upload-artifact" in s.get("uses", "")
        ]
        assert upload_steps, "no upload-artifact step found"
        names = [
            s.get("with", {}).get("name", "") for s in upload_steps
        ]
        assert any("matrix.shard" in n for n in names), (
            "upload-artifact name must include ${{ matrix.shard }} so each "
            f"leg's artifact is distinct; found names: {names}"
        )
