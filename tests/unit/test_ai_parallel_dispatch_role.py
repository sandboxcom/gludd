"""Structural tests for the general_ludd.agent.ai_parallel_dispatch role.

These do NOT run Ansible — they load the role's task/handler/defaults YAML and
assert the native-async promise/barrier wiring is present and intact:

  * the dispatch task launches gludd_model_call with `async:` + `poll: 0`
    (the promise) capped by max_in_flight batching;
  * the join uses async_status with an `until` retries/delay barrier (the await);
  * the required-subset gate (required_names / wait-set) is honored in the
    until + harvest `when`;
  * the handler / meta:flush_handlers variant exists with its own async_status
    re-join.

A regression that deletes async:, the async_status barrier, the until, or the
required-subset wiring will fail here long before molecule runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

# Repo-root-relative path to the role (this file lives at tests/unit/).
_ROLE = (
    Path(__file__).resolve().parents[2]
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "agent"
    / "roles"
    / "ai_parallel_dispatch"
)


def _load(rel: str) -> list[dict[str, object]]:
    """yaml.safe_load a role file into a list of task/handler dicts."""
    text = (_ROLE / rel).read_text()
    data = yaml.safe_load(text)
    assert isinstance(data, list), f"{rel} should be a YAML list of tasks"
    return data


def _module_keys(task: dict[str, object]) -> set[str]:
    """Top-level keys of a task that name a module (have a dotted FQCN or are
    bare module names), excluding directive keys."""
    directives = {
        "name", "loop", "loop_control", "register", "when", "until", "retries",
        "delay", "async", "poll", "no_log", "failed_when", "changed_when",
        "notify", "listen", "vars", "tags", "become", "block", "rescue",
        "always", "ignore_errors",
    }
    return {k for k in task if k not in directives}


def _mapping(value: object) -> dict[str, object]:
    """Narrow a parsed YAML mapping for strict structural assertions."""
    assert isinstance(value, dict)
    return value


def _list(value: object) -> list[object]:
    """Narrow a parsed YAML list for strict structural assertions."""
    assert isinstance(value, list)
    return value


def test_role_layout_present() -> None:
    """All deliverable files exist (mirrors implement_change layout)."""
    for rel in (
        "tasks/main.yml",
        "tasks/dispatch_batch.yml",
        "tasks/handler_barrier.yml",
        "handlers/main.yml",
        "defaults/main.yml",
        "meta/main.yml",
        "README.md",
    ):
        assert (_ROLE / rel).is_file(), f"missing role file: {rel}"


def test_main_batches_for_concurrency_cap() -> None:
    """main.yml slices dispatch_calls into max_in_flight batches and loops the
    per-batch include — the ONLY honest in-flight concurrency cap."""
    tasks = _load("tasks/main.yml")
    blob = yaml.dump(tasks)
    # batch(max_in_flight) is the slicing filter.
    assert "batch(max_in_flight" in blob, "missing batch(max_in_flight) slicing"
    # The per-batch fan-out is an include_tasks loop over the batches.
    includes = [
        t for t in tasks
        if t.get("ansible.builtin.include_tasks") == "dispatch_batch.yml"
    ]
    assert includes, "main.yml must include_tasks dispatch_batch.yml"
    assert any("_apd_batches" in str(t.get("loop", "")) for t in includes), (
        "the dispatch include must loop over the sliced _apd_batches"
    )


def test_main_validates_sum_over_batches_timeout() -> None:
    """The input assert must bound ceil(N/max_in_flight) sequential batches under
    the playbook timeout (the sum-over-batches graft) AND validate join_policy."""
    tasks = _load("tasks/main.yml")
    asserts = [t for t in tasks if "ansible.builtin.assert" in t]
    that_blob = yaml.dump(
        [_mapping(t["ansible.builtin.assert"]).get("that") for t in asserts]
    )
    assert "playbook_timeout" in that_blob, "no playbook_timeout ceiling in asserts"
    assert "max_in_flight" in that_blob, "sum-over-batches term missing max_in_flight"
    assert "join_policy in" in that_blob, "join_policy membership not validated"


def test_main_join_assert_honors_required_subset() -> None:
    """The final join assert is the single source of truth and checks the
    required-subset (required_returned vs required_names) for 'required'."""
    tasks = _load("tasks/main.yml")
    blob = yaml.dump(tasks)
    assert "_apd_required_returned" in blob, "required-subset gate absent"
    assert "_apd_required_names" in blob, "required name set absent"
    # fail-closed required set computed up front.
    assert "selectattr('required')" in blob, "required flag selection absent"


def test_dispatch_task_is_async_promise() -> None:
    """The dispatch task launches gludd_model_call with async: + poll: 0 (the
    promise) over the batch."""
    tasks = _load("tasks/dispatch_batch.yml")
    launches = [
        t for t in tasks
        if "general_ludd.agent.gludd_model_call" in _module_keys(t)
    ]
    assert launches, "no gludd_model_call task in dispatch_batch.yml"
    launch = launches[0]
    assert "async" in launch, "dispatch task missing async: (no promise)"
    assert launch.get("poll") == 0, "dispatch task must poll: 0 (fire-and-forget)"
    assert "_apd_batch" in str(launch.get("loop", "")), (
        "dispatch must loop over the capped _apd_batch"
    )
    # async value is driven by the tunable async_timeout.
    assert "async_timeout" in str(launch.get("async", "")), (
        "async: should be {{ async_timeout }}"
    )


def test_barrier_is_async_status_with_until_retries_delay() -> None:
    """The await: async_status looped with until/retries/delay, and the until
    counts only FINISHED jobs in the required wait-set."""
    tasks = _load("tasks/dispatch_batch.yml")
    barriers = [t for t in tasks if "ansible.builtin.async_status" in t]
    assert barriers, "no async_status barrier in dispatch_batch.yml"
    bar = barriers[0]
    assert "until" in bar, "barrier missing until: (not a real await)"
    assert "retries" in bar and "delay" in bar, "barrier missing retries/delay bound"
    until = str(bar["until"])
    assert "finished" in until, "until must gate on finished jobs"
    assert "_apd_wait_jids" in until, "until must count the required wait-set"
    # retries/delay are the tunables.
    assert "barrier_retries" in str(bar["retries"])
    assert "barrier_delay" in str(bar["delay"])


def test_harvest_drops_unfinished_and_failed() -> None:
    """Harvest folds in only finished, non-failed jobs (optional timeouts absent)."""
    tasks = _load("tasks/dispatch_batch.yml")
    harvest = next(
        t for t in tasks
        if "ansible.builtin.set_fact" in t and "_apd_results" in str(t.get("when", "")) + str(t)
        and t.get("when")
    )
    when = " ".join(str(c) for c in _list(harvest["when"]))
    assert "finished" in when, "harvest must require finished"
    assert "failed" in when, "harvest must exclude failed"


def test_handler_variant_flush_then_rejoin() -> None:
    """The handler variant notifies per call, flushes handlers as the barrier,
    then async_status-re-joins the ledger."""
    tasks = _load("tasks/handler_barrier.yml")
    metas = [t for t in tasks if t.get("ansible.builtin.meta") == "flush_handlers"]
    assert metas, "handler variant must use meta: flush_handlers as the barrier"
    notifies = [t for t in tasks if t.get("notify") == "apd dispatch"]
    assert notifies, "handler variant must notify the dispatch handler per call"
    rejoins = [t for t in tasks if "ansible.builtin.async_status" in t]
    assert rejoins, "handler variant must async_status-re-join launched promises"
    assert "until" in rejoins[0], "handler re-join missing until barrier"

    # The handlers themselves launch async (poll:0) gludd_model_call.
    handlers = _load("handlers/main.yml")
    h_launch = [
        h for h in handlers
        if "general_ludd.agent.gludd_model_call" in _module_keys(h)
    ]
    assert h_launch, "handler must launch gludd_model_call"
    assert h_launch[0].get("poll") == 0 and "async" in h_launch[0], (
        "handler launch must be async/poll:0"
    )
    assert h_launch[0].get("listen") == "apd dispatch", "handler must listen on the group"


def test_defaults_expose_promised_knobs() -> None:
    """defaults/main.yml exposes the documented tunables with safe defaults."""
    text = (_ROLE / "defaults" / "main.yml").read_text()
    defaults = yaml.safe_load(text)
    assert defaults["dispatch_calls"] == [], "dispatch_calls must default empty"
    assert defaults["max_in_flight"] == 4
    assert defaults["join_policy"] == "all"
    # async_timeout must be >= call_request_timeout (the deadline-ownership invariant).
    assert int(defaults["async_timeout"]) >= int(defaults["call_request_timeout"])
    assert defaults["enable_handler_variant"] is False
    # playbook_timeout reads GLUDD_PLAYBOOK_TIMEOUT.
    assert "GLUDD_PLAYBOOK_TIMEOUT" in str(defaults["playbook_timeout"])


@pytest.mark.parametrize(
    "rel",
    [
        "tasks/main.yml",
        "tasks/dispatch_batch.yml",
        "tasks/handler_barrier.yml",
        "handlers/main.yml",
        "defaults/main.yml",
        "meta/main.yml",
    ],
)
def test_role_yaml_parses(rel: str) -> None:
    """Every role YAML file is valid YAML (cheap syntax guard)."""
    yaml.safe_load((_ROLE / rel).read_text())
