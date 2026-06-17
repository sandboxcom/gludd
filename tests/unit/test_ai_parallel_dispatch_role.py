"""Structural tests for the ai_parallel_dispatch Ansible role.

These assert the SHAPE of the role's task YAML — the promise/await mechanics, the
concurrency cap, the corrected barrier structure (outer retry loop, NOT an
aggregate `until` on a looped task), the timeout-budget asserts, and the
handler-variant wave capping.

They are deliberately structural: real `async_status`/sweep runtime is exercised
by `make molecule-test SCENARIO=role_ai_parallel_dispatch`, not here. But they DO
guard against the specific defects from adversarial review re-appearing:
  * an `until` on the LOOPED async_status that references the aggregate `.results`
    (the false-join bug),
  * the timeout assert under-counting (omitting async_timeout / per-item retries),
  * the handler variant fanning out unbounded (no max_in_flight wave cap),
  * barrier_delay int-truncation divergence between the two paths,
  * the missing premature-abandonment (window >= async_timeout) guard.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROLE = (
    Path(__file__).resolve().parents[2]
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "agent"
    / "roles"
    / "ai_parallel_dispatch"
)
TASKS = ROLE / "tasks"


def _load(path: Path) -> list[dict]:
    with path.open() as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, list), f"{path} should be a task list"
    return data


def _raw(path: Path) -> str:
    return path.read_text()


def _task_by_async_status(tasks: list[dict]) -> list[dict]:
    return [t for t in tasks if "ansible.builtin.async_status" in t]


# --------------------------------------------------------------------------- #
# File layout / canonical names
# --------------------------------------------------------------------------- #
class TestLayout:
    def test_expected_task_files_exist(self):
        for name in (
            "main.yml",
            "dispatch_batch.yml",
            "barrier_sweep.yml",
            "handler_barrier.yml",
            "handler_wave.yml",
        ):
            assert (TASKS / name).is_file(), f"missing tasks/{name}"

    def test_old_handler_variant_name_removed(self):
        assert not (
            TASKS / "flush_handlers_variant.yml"
        ).exists(), "old flush_handlers_variant.yml should be renamed to handler_barrier.yml"

    def test_main_includes_handler_barrier_not_old_name(self):
        raw = _raw(TASKS / "main.yml")
        assert "handler_barrier.yml" in raw
        assert "flush_handlers_variant.yml" not in raw

    def test_readme_exists(self):
        assert (ROLE / "README.md").is_file()


# --------------------------------------------------------------------------- #
# Promise: async + poll:0 fan-out, concurrency-capped batching
# --------------------------------------------------------------------------- #
class TestPromiseFanout:
    def test_dispatch_launches_async_poll0(self):
        tasks = _load(TASKS / "dispatch_batch.yml")
        launch = next(t for t in tasks if "general_ludd.agent.gludd_model_call" in t)
        assert "async" in launch, "the dispatch task must be launched with async:"
        assert launch.get("poll") == 0, "poll: 0 makes it return the promise immediately"
        assert launch.get("loop") == "{{ _apd_batch }}", "fan out over the batch slice"

    def test_main_batches_for_concurrency_cap(self):
        raw = _raw(TASKS / "main.yml")
        assert "batch(max_in_flight" in raw, "fan-out must be sliced by max_in_flight"
        # sequential include_tasks loop over the batches = the cap
        assert "include_tasks: dispatch_batch.yml" in raw


# --------------------------------------------------------------------------- #
# Barrier correctness: outer retry loop, NOT aggregate-until-on-looped-task
# --------------------------------------------------------------------------- #
class TestBarrierStructure:
    def test_dispatch_batch_has_no_until_on_async_status(self):
        """The false-join bug: until/retries on a looped async_status with an
        aggregate `.results` reference. The corrected design drives retries via an
        OUTER include_tasks loop, so dispatch_batch.yml must NOT carry until on a
        looped async_status."""
        tasks = _load(TASKS / "dispatch_batch.yml")
        for t in _task_by_async_status(tasks):
            assert "until" not in t, (
                "async_status in dispatch_batch.yml must not use until (per-item "
                "retry + stale aggregate). The outer retry loop is the retry driver."
            )

    def test_barrier_is_outer_retry_loop_over_sweep(self):
        tasks = _load(TASKS / "dispatch_batch.yml")
        sweep_inc = next(
            t
            for t in tasks
            if t.get("ansible.builtin.include_tasks") == "barrier_sweep.yml"
        )
        # outer loop over a retry-index range, skipped once the barrier is done
        assert "range(0" in str(sweep_inc.get("loop", "")), "loop over retry-index range"
        assert "_apd_barrier_done" in str(sweep_inc.get("when", "")), (
            "later sweeps must be skipped once the barrier predicate is satisfied "
            "(true early-return for any/required)"
        )

    def test_sweep_async_status_is_single_pass_no_until(self):
        tasks = _load(TASKS / "barrier_sweep.yml")
        poll = next(iter(_task_by_async_status(tasks)))
        assert "until" not in poll, "each sweep is ONE non-retrying pass"
        assert "retries" not in poll, "retries live on the OUTER loop, not the sweep"
        assert poll.get("loop") == "{{ _apd_all_jids }}", "sweep polls every jid once"

    def test_sweep_recomputes_aggregate_barrier_predicate(self):
        """The cross-job (aggregate) wait-set check that a per-item until could
        never express now lives in a set_fact recomputed each sweep."""
        raw = _raw(TASKS / "barrier_sweep.yml")
        assert "_apd_barrier_done" in raw
        assert "_apd_wait_jids" in raw
        # any -> >=1 finished; all/required -> whole wait-set finished
        assert "join_policy == 'any'" in raw

    def test_sweep_harvest_drops_failed_and_unfinished(self):
        tasks = _load(TASKS / "barrier_sweep.yml")
        harvest = next(
            t
            for t in tasks
            if "ansible.builtin.set_fact" in t
            and "_apd_results" in str(t.get("ansible.builtin.set_fact"))
        )
        when = " ".join(str(c) for c in harvest.get("when", []))
        assert "finished" in when, "only finished jobs are harvested"
        assert "failed" in when, "finished-but-failed jobs are dropped"


# --------------------------------------------------------------------------- #
# Timeout budget asserts
# --------------------------------------------------------------------------- #
class TestTimeoutBudget:
    def test_validate_assert_present(self):
        tasks = _load(TASKS / "main.yml")
        assert any(
            t.get("name") == "Validate dispatch inputs" for t in tasks
        ), "input validation assert must exist"

    def test_no_premature_abandonment_guard(self):
        """window (barrier_retries*barrier_delay) must be >= async_timeout."""
        raw = _raw(TASKS / "main.yml")
        # the >= async_timeout constraint on the polling window
        assert ">= (async_timeout | int)" in raw

    def test_sum_over_batches_uses_max_window_async(self):
        """The per-batch wall-clock is max(window, async_timeout), not just the
        window — a launched job runs up to async_timeout regardless of polling."""
        raw = _raw(TASKS / "main.yml")
        assert "| max)" in raw, "sum-over-batches must take max(window, async_timeout)"
        assert "async_timeout | int]" in raw

    def test_join_policy_membership_checked(self):
        raw = _raw(TASKS / "main.yml")
        assert "join_policy in ['all', 'required', 'any']" in raw


# --------------------------------------------------------------------------- #
# Required-subset join + artifact
# --------------------------------------------------------------------------- #
class TestJoinAndArtifact:
    def test_required_set_fail_closed(self):
        raw = _raw(TASKS / "main.yml")
        # 'required' with no flagged call falls back to ALL names (fail-closed)
        assert "_apd_required_names" in raw
        assert "map(attribute='name') | list)" in raw

    def test_join_assert_covers_three_policies(self):
        raw = _raw(TASKS / "main.yml")
        for pol in ("join_policy == 'all'", "join_policy == 'required'", "join_policy == 'any'"):
            assert pol in raw

    def test_artifact_emits_dropped(self):
        raw = _raw(TASKS / "main.yml")
        assert "_apd_dropped" in raw, "artifact must record dropped/unfinished calls"
        assert "'dropped':" in raw


# --------------------------------------------------------------------------- #
# Handler variant: wave capping + barrier_delay float consistency
# --------------------------------------------------------------------------- #
class TestHandlerVariant:
    def test_handler_barrier_caps_with_waves(self):
        raw = _raw(TASKS / "handler_barrier.yml")
        assert "batch(max_in_flight" in raw, "handler path must slice into waves"
        assert "handler_wave.yml" in raw, "per-wave flush, not one global flush"

    def test_handler_wave_flushes_per_wave(self):
        tasks = _load(TASKS / "handler_wave.yml")
        metas = [t for t in tasks if t.get("ansible.builtin.meta") == "flush_handlers"]
        assert len(metas) == 1, "exactly one flush per wave (the per-wave barrier)"
        notify = next(t for t in tasks if t.get("notify") == "apd dispatch")
        assert notify.get("loop") == "{{ _apd_h_wave }}", "notify only this wave"

    def test_handler_rejoin_uses_per_item_scalar_until(self):
        """The CORRECT idiom: until on a looped async_status referencing the
        per-item scalar .finished (not an aggregate)."""
        tasks = _load(TASKS / "handler_barrier.yml")
        rejoin = next(
            t
            for t in _task_by_async_status(tasks)
            if "until" in t
        )
        assert "_apd_h_poll.finished" in rejoin["until"]
        assert "results" not in rejoin["until"], "must check per-item, not aggregate"

    def test_barrier_delay_float_in_both_paths(self):
        """barrier_delay must be | float in BOTH the sweep wait and the handler
        re-join — no int-truncation of sub-second delays in one path only."""
        handler_raw = _raw(TASKS / "handler_barrier.yml")
        sweep_raw = _raw(TASKS / "barrier_sweep.yml")
        assert "barrier_delay | float" in handler_raw
        assert "barrier_delay | int" not in handler_raw, "handler path must not truncate"
        assert "barrier_delay | float" in sweep_raw


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
