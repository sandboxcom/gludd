"""Bucket allocation combining PID output and rule actions."""

from __future__ import annotations

from agentic_harness.controllers.pid import ControllerOutputs
from agentic_harness.rules.engine import RuleAction
from agentic_harness.schemas.queue import Queue


def allocate_buckets(
    pid_output: ControllerOutputs,
    rule_actions: list[RuleAction],
    queues: list[Queue],
) -> dict[str, int]:
    result: dict[str, int] = {}

    for queue in queues:
        result[queue.queue_name] = pid_output.desired_active_buckets_by_queue.get(
            queue.queue_name, queue.soft_cap,
        )

    for action in rule_actions:
        if action.action_type == "reduce_buckets":
            target = action.params.get("queue", "")
            reduction = action.params.get("reduction", 1)
            if target in result:
                result[target] = max(1, result[target] - reduction)

    for queue in queues:
        if result.get(queue.queue_name, 0) > queue.hard_cap:
            result[queue.queue_name] = queue.hard_cap

    return result
