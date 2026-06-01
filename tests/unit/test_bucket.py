"""Unit tests for bucket allocation."""

from __future__ import annotations

from general_ludd.controllers.bucket import allocate_buckets
from general_ludd.controllers.pid import ControllerOutputs
from general_ludd.rules.engine import RuleAction
from general_ludd.schemas.queue import Queue


class TestBucketAllocation:
    def test_bucket_allocation_respects_hard_cap(self):
        pid_output = ControllerOutputs(
            desired_active_buckets_by_queue={"ansible": 20},
        )
        queues = [Queue(queue_name="ansible", resource_profile="local_heavy", hard_cap=10, soft_cap=5)]
        result = allocate_buckets(pid_output, [], queues)
        assert result["ansible"] == 10

    def test_bucket_allocation_respects_pid_output(self):
        pid_output = ControllerOutputs(
            desired_active_buckets_by_queue={"ansible": 3, "model": 5},
        )
        queues = [
            Queue(queue_name="ansible", hard_cap=10, soft_cap=5),
            Queue(queue_name="model", hard_cap=10, soft_cap=5),
        ]
        result = allocate_buckets(pid_output, [], queues)
        assert result["ansible"] == 3
        assert result["model"] == 5

    def test_bucket_allocation_applies_reduce_action(self):
        pid_output = ControllerOutputs(
            desired_active_buckets_by_queue={"ansible": 5},
        )
        queues = [Queue(queue_name="ansible", hard_cap=10, soft_cap=5)]
        actions = [
            RuleAction(
                rule_id="reduce_load",
                action_type="reduce_buckets",
                params={"queue": "ansible", "reduction": 2},
            ),
        ]
        result = allocate_buckets(pid_output, actions, queues)
        assert result["ansible"] == 3

    def test_bucket_allocation_falls_back_to_soft_cap(self):
        pid_output = ControllerOutputs(
            desired_active_buckets_by_queue={},
        )
        queues = [Queue(queue_name="core", hard_cap=10, soft_cap=5)]
        result = allocate_buckets(pid_output, [], queues)
        assert result["core"] == 5

    def test_bucket_allocation_reduce_floor_is_one(self):
        pid_output = ControllerOutputs(
            desired_active_buckets_by_queue={"ansible": 2},
        )
        queues = [Queue(queue_name="ansible", hard_cap=10, soft_cap=5)]
        actions = [
            RuleAction(
                rule_id="reduce_load",
                action_type="reduce_buckets",
                params={"queue": "ansible", "reduction": 10},
            ),
        ]
        result = allocate_buckets(pid_output, actions, queues)
        assert result["ansible"] == 1
