"""Structural tests for schemas/queue.py — Queue model and INITIAL_QUEUES."""

from __future__ import annotations

import pytest

from general_ludd.schemas.queue import INITIAL_QUEUES, Queue


class TestQueueModel:
    def test_defaults_populated(self):
        q = Queue(queue_name="test-queue")
        assert q.queue_name == "test-queue"
        assert q.queue_enabled is True
        assert q.priority_weight == 100
        assert q.resource_profile == "low_resource"
        assert q.hard_cap == 10
        assert q.soft_cap == 5
        assert q.pid_group is None
        assert q.allowed_playbooks == []
        assert q.allowed_model_profiles == []
        assert q.allowed_prompt_profiles == []
        assert q.required_molecule_coverage_profile is None
        assert q.max_error_rate == 0.5
        assert q.retry_policy == {}

    def test_queue_name_whitespace_stripped(self):
        q = Queue(queue_name="  my-queue  ")
        assert q.queue_name == "my-queue"

    def test_queue_name_slashes(self):
        q = Queue(queue_name="-a_b-")
        assert q.queue_name == "-a_b-"

    def test_queue_name_rejects_empty(self):
        with pytest.raises(ValueError, match="queue_name must not be empty"):
            Queue(queue_name="")

    def test_queue_name_rejects_spaces(self):
        with pytest.raises(ValueError, match="queue_name must match"):
            Queue(queue_name="bad name")

    def test_queue_name_rejects_special_chars(self):
        with pytest.raises(ValueError, match="queue_name must match"):
            Queue(queue_name="bad.name")

    def test_hard_cap_minimum(self):
        with pytest.raises(ValueError, match="hard_cap must be at least 1"):
            Queue(queue_name="test", hard_cap=0)

    def test_hard_cap_negative(self):
        with pytest.raises(ValueError, match="hard_cap must be at least 1"):
            Queue(queue_name="test", hard_cap=-5)

    def test_max_error_rate_low(self):
        with pytest.raises(ValueError, match="max_error_rate must be between"):
            Queue(queue_name="test", max_error_rate=-0.1)

    def test_max_error_rate_high(self):
        with pytest.raises(ValueError, match="max_error_rate must be between"):
            Queue(queue_name="test", max_error_rate=1.5)

    def test_max_error_rate_boundaries(self):
        q0 = Queue(queue_name="zero", max_error_rate=0.0)
        assert q0.max_error_rate == 0.0
        q1 = Queue(queue_name="one", max_error_rate=1.0)
        assert q1.max_error_rate == 1.0

    def test_soft_cap_exceeds_hard_cap(self):
        with pytest.raises(ValueError, match="soft_cap must not exceed hard_cap"):
            Queue(queue_name="test", hard_cap=5, soft_cap=10)

    def test_soft_cap_equals_hard_cap(self):
        q = Queue(queue_name="test", hard_cap=10, soft_cap=10)
        assert q.soft_cap == q.hard_cap

    def test_custom_fields(self):
        q = Queue(
            queue_name="custom",
            priority_weight=200,
            resource_profile="ai_heavy",
            hard_cap=20,
            soft_cap=15,
            pid_group="gpu-workers",
            allowed_playbooks=["play1.yml", "play2.yml"],
            allowed_model_profiles=["sonnet"],
            retry_policy={"max_retries": 3},
        )
        assert q.priority_weight == 200
        assert q.resource_profile == "ai_heavy"
        assert q.pid_group == "gpu-workers"
        assert q.allowed_playbooks == ["play1.yml", "play2.yml"]
        assert q.allowed_model_profiles == ["sonnet"]
        assert q.retry_policy == {"max_retries": 3}


class TestInitialQueues:
    def test_all_queues_have_unique_names(self):
        names = [q.queue_name for q in INITIAL_QUEUES]
        assert len(names) == len(set(names))

    def test_initial_count(self):
        assert len(INITIAL_QUEUES) == 13

    def test_manual_hold_disabled(self):
        manual = [q for q in INITIAL_QUEUES if q.queue_name == "manual_hold"]
        assert len(manual) == 1
        assert manual[0].queue_enabled is False

    def test_core_and_intake_low_resource(self):
        for name in ("intake", "core"):
            q = next(q for q in INITIAL_QUEUES if q.queue_name == name)
            assert q.resource_profile == "low_resource"

    def test_model_queue_ai_heavy(self):
        q = next(q for q in INITIAL_QUEUES if q.queue_name == "model")
        assert q.resource_profile == "ai_heavy"

    def test_every_queue_validates(self):
        for q in INITIAL_QUEUES:
            assert q.hard_cap >= q.soft_cap
            assert 0.0 <= q.max_error_rate <= 1.0
            assert q.priority_weight > 0
