"""Deep tests for bucket allocation — edge cases, interactions, invariants."""

from __future__ import annotations

from general_ludd.controllers.bucket import allocate_buckets
from general_ludd.controllers.pid import ControllerOutputs
from general_ludd.rules.engine import RuleAction
from general_ludd.schemas.queue import Queue


def _q(name: str, hard: int = 10, soft: int = 5) -> Queue:
    return Queue(queue_name=name, hard_cap=hard, soft_cap=min(soft, hard))


def _reduce(queue: str, reduction: int = 1, rule_id: str = "r1") -> RuleAction:
    return RuleAction(
        rule_id=rule_id,
        action_type="reduce_buckets",
        params={"queue": queue, "reduction": reduction},
    )


# ── PID output assignment ────────────────────────────────────────────────


def test_pid_output_dict_is_copied_not_mutated():
    pid = ControllerOutputs(desired_active_buckets_by_queue={"a": 7})
    result = allocate_buckets(pid, [], [_q("a")])
    assert result["a"] == 7
    assert pid.desired_active_buckets_by_queue == {"a": 7}  # unchanged


def test_each_queue_gets_its_own_pid_value():
    pid = ControllerOutputs(desired_active_buckets_by_queue={"a": 3, "b": 8})
    result = allocate_buckets(pid, [], [_q("a"), _q("b")])
    assert result == {"a": 3, "b": 8}


def test_queue_not_in_pid_falls_back_to_soft_cap():
    pid = ControllerOutputs(desired_active_buckets_by_queue={})
    result = allocate_buckets(pid, [], [_q("core", soft=7)])
    assert result["core"] == 7


def test_some_queues_in_pid_some_not():
    pid = ControllerOutputs(desired_active_buckets_by_queue={"a": 5})
    result = allocate_buckets(pid, [], [_q("a", soft=3), _q("b", soft=8)])
    assert result == {"a": 5, "b": 8}


# ── Hard cap enforcement ─────────────────────────────────────────────────


def test_pid_value_exceeding_hard_cap_is_clamped():
    pid = ControllerOutputs(desired_active_buckets_by_queue={"a": 50})
    result = allocate_buckets(pid, [], [_q("a", hard=5)])
    assert result["a"] == 5


def test_soft_cap_exceeding_hard_cap_is_clamped():
    pid = ControllerOutputs(desired_active_buckets_by_queue={})
    result = allocate_buckets(pid, [], [_q("a", hard=3, soft=10)])
    assert result["a"] == 3


def test_hard_cap_equals_pid_value_is_unchanged():
    pid = ControllerOutputs(desired_active_buckets_by_queue={"a": 5})
    result = allocate_buckets(pid, [], [_q("a", hard=5, soft=3)])
    assert result["a"] == 5


def test_multiple_queues_respect_individual_hard_caps():
    pid = ControllerOutputs(desired_active_buckets_by_queue={"a": 9, "b": 9})
    result = allocate_buckets(pid, [], [_q("a", hard=4), _q("b", hard=8)])
    assert result == {"a": 4, "b": 8}


def test_hard_cap_at_one():
    pid = ControllerOutputs(desired_active_buckets_by_queue={"a": 50})
    result = allocate_buckets(pid, [], [_q("a", hard=1)])
    assert result["a"] == 1


# ── Reduce actions ────────────────────────────────────────────────────────


def test_single_reduce_action():
    pid = ControllerOutputs(desired_active_buckets_by_queue={"a": 5})
    actions = [_reduce("a", 3)]
    result = allocate_buckets(pid, actions, [_q("a")])
    assert result["a"] == 2


def test_multiple_reduce_actions_on_same_queue():
    pid = ControllerOutputs(desired_active_buckets_by_queue={"a": 10})
    actions = [_reduce("a", 3, "r1"), _reduce("a", 2, "r2")]
    result = allocate_buckets(pid, actions, [_q("a", hard=10)])
    assert result["a"] == 5


def test_multiple_reduce_actions_on_different_queues():
    pid = ControllerOutputs(desired_active_buckets_by_queue={"a": 10, "b": 10})
    actions = [_reduce("a", 3), _reduce("b", 4)]
    result = allocate_buckets(pid, actions, [_q("a"), _q("b")])
    assert result == {"a": 7, "b": 6}


def test_reduce_action_on_nonexistent_queue_is_ignored():
    pid = ControllerOutputs(desired_active_buckets_by_queue={"a": 5})
    actions = [_reduce("nonexistent", 10)]
    result = allocate_buckets(pid, actions, [_q("a")])
    assert result == {"a": 5}


def test_reduce_action_default_reduction_is_one():
    pid = ControllerOutputs(desired_active_buckets_by_queue={"a": 5})
    action = RuleAction(
        rule_id="r1",
        action_type="reduce_buckets",
        params={"queue": "a"},
    )
    result = allocate_buckets(pid, [action], [_q("a")])
    assert result["a"] == 4


def test_reduce_action_empty_params():
    pid = ControllerOutputs(desired_active_buckets_by_queue={"a": 5})
    action = RuleAction(
        rule_id="r1",
        action_type="reduce_buckets",
        params={},
    )
    result = allocate_buckets(pid, [action], [_q("a")])
    assert result["a"] == 5  # target="" not in result, so ignored


def test_reduce_action_non_reduce_buckets_type_is_ignored():
    pid = ControllerOutputs(desired_active_buckets_by_queue={"a": 5})
    action = RuleAction(
        rule_id="r1",
        action_type="pause_queue",
        params={"queue": "a"},
    )
    result = allocate_buckets(pid, [action], [_q("a")])
    assert result["a"] == 5


def test_reduce_below_zero_floors_at_one():
    pid = ControllerOutputs(desired_active_buckets_by_queue={"a": 2})
    actions = [_reduce("a", 10)]
    result = allocate_buckets(pid, actions, [_q("a")])
    assert result["a"] == 1


def test_reduce_to_exactly_one():
    pid = ControllerOutputs(desired_active_buckets_by_queue={"a": 3})
    actions = [_reduce("a", 2)]
    result = allocate_buckets(pid, actions, [_q("a")])
    assert result["a"] == 1


# ── Reduce + hard cap interaction ────────────────────────────────────────


def test_reduce_then_hard_cap_both_apply():
    pid = ControllerOutputs(desired_active_buckets_by_queue={"a": 8})
    actions = [_reduce("a", 2)]
    result = allocate_buckets(pid, actions, [_q("a", hard=4)])
    assert result["a"] == 4  # 8-2=6 → clamped to 4


def test_hard_cap_seen_before_reduce_still_applies():
    pid = ControllerOutputs(desired_active_buckets_by_queue={"a": 10})
    result = allocate_buckets(pid, [], [_q("a", hard=3)])
    assert result["a"] == 3


# ── Empty / minimal inputs ───────────────────────────────────────────────


def test_empty_queues_returns_empty_dict():
    pid = ControllerOutputs(desired_active_buckets_by_queue={"a": 5})
    result = allocate_buckets(pid, [_reduce("a")], [])
    assert result == {}


def test_empty_rule_actions_noop():
    pid = ControllerOutputs(desired_active_buckets_by_queue={"a": 5})
    result = allocate_buckets(pid, [], [_q("a")])
    assert result == {"a": 5}


def test_empty_pid_output_all_queues_fallback_to_soft_cap():
    pid = ControllerOutputs()
    result = allocate_buckets(pid, [], [_q("a", soft=3), _q("b", soft=7)])
    assert result == {"a": 3, "b": 7}


def test_all_empty():
    pid = ControllerOutputs()
    result = allocate_buckets(pid, [], [])
    assert result == {}


# ── Invariants ───────────────────────────────────────────────────────────


def test_result_never_contains_extra_keys():
    pid = ControllerOutputs(desired_active_buckets_by_queue={"a": 5, "z": 99})
    result = allocate_buckets(pid, [], [_q("a")])
    assert set(result.keys()) == {"a"}


def test_pid_zero_result_is_zero():
    pid = ControllerOutputs(desired_active_buckets_by_queue={"a": 0})
    result = allocate_buckets(pid, [], [_q("a", hard=10, soft=5)])
    assert result["a"] == 0


def test_pid_negative_result_is_negative():
    pid = ControllerOutputs(desired_active_buckets_by_queue={"a": -5})
    result = allocate_buckets(pid, [], [_q("a", hard=10, soft=5)])
    assert result["a"] == -5


def test_no_value_exceeds_hard_cap():
    pid = ControllerOutputs(desired_active_buckets_by_queue={"a": 999, "b": 999})
    queues = [_q("a", hard=5, soft=3), _q("b", hard=8, soft=4)]
    actions = [_reduce("a", 10), _reduce("b", 10)]
    result = allocate_buckets(pid, actions, queues)
    assert result["a"] <= 5
    assert result["b"] <= 8


def test_reduction_never_drops_below_one():
    pid = ControllerOutputs(desired_active_buckets_by_queue={"a": 1})
    actions = [_reduce("a", 100)]
    result = allocate_buckets(pid, actions, [_q("a")])
    assert result["a"] == 1


def test_deterministic_output():
    pid = ControllerOutputs(desired_active_buckets_by_queue={"a": 5, "b": 3})
    queues = [_q("a"), _q("b")]
    actions = [_reduce("a", 2)]
    r1 = allocate_buckets(pid, actions, queues)
    r2 = allocate_buckets(pid, actions, queues)
    assert r1 == r2


# ── Stress: many queues, many reduces ────────────────────────────────────


def test_many_queues_many_reduces():
    pid = ControllerOutputs(desired_active_buckets_by_queue={f"q{i}": 10 for i in range(20)})
    queues = [_q(f"q{i}", hard=10) for i in range(20)]
    actions = [_reduce(f"q{i}", i) for i in range(20)]
    result = allocate_buckets(pid, actions, queues)
    assert len(result) == 20
    for i in range(20):
        assert result[f"q{i}"] == max(1, 10 - i)


def test_duplicate_queue_name_last_pid_wins_before_hard_cap():
    queues = [_q("a", soft=5, hard=99), _q("a", soft=77, hard=99)]
    pid = ControllerOutputs(desired_active_buckets_by_queue={})
    result = allocate_buckets(pid, [], queues)
    assert result["a"] == 77  # second queue's soft_cap overwrites first


def test_reduce_on_queue_not_in_any_queue_list_is_noop():
    pid = ControllerOutputs(desired_active_buckets_by_queue={})
    actions = [_reduce("ghost", 10)]
    result = allocate_buckets(pid, actions, [_q("a")])
    assert result == {"a": _q("a").soft_cap}
