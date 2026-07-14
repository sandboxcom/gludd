"""Structural tests for scheduling/planner.py — PlanResult + OrchestrationPlanner."""

from __future__ import annotations

import pytest

from general_ludd.scheduling.planner import (
    OrchestrationPlanner,
    PlanResult,
)
from general_ludd.scheduling.scheduler import CycleError


class TestPlanResultDefaults:
    def test_default_batches_empty(self):
        pr = PlanResult()
        assert pr.batches == []
        assert pr.parallel_now == []
        assert pr.serialized == []
        assert pr.explanation == ""

    def test_custom_fields(self):
        pr = PlanResult(
            batches=[["a", "b"], ["c"]],
            parallel_now=["a", "b"],
            serialized=[("a", "c", "shared.py")],
            explanation="a and c conflict",
        )
        assert len(pr.batches) == 2
        assert pr.parallel_now == ["a", "b"]


class TestOrchestrationPlannerStructural:
    def test_init(self):
        planner = OrchestrationPlanner()
        assert planner._scheduler is not None

    def test_plan_work_missing_key_raises_keyerror(self):
        planner = OrchestrationPlanner()
        with pytest.raises(KeyError):
            planner.plan_work([{"invalid": "dict"}])

    def test_plan_work_unknown_dependency_raises_valueerror(self):
        planner = OrchestrationPlanner()
        with pytest.raises(ValueError):
            planner.plan_work([
                {"id": "a", "files": [], "depends_on": ["nonexistent"], "is_greenfield": True},
            ])

    def test_plan_work_self_dependency(self):
        planner = OrchestrationPlanner()
        with pytest.raises(CycleError):
            planner.plan_work([
                {"id": "a", "files": [], "depends_on": ["a"], "is_greenfield": True},
            ])

    def test_parallelizable_empty(self):
        planner = OrchestrationPlanner()
        assert planner.parallelizable([]) == []

    def test_parallelizable_single(self):
        planner = OrchestrationPlanner()
        ids = planner.parallelizable([
            {"id": "solo", "files": ["only.py"], "depends_on": [], "is_greenfield": False},
        ])
        assert ids == ["solo"]

    def test_explanation_for_no_conflicts(self):
        planner = OrchestrationPlanner()
        result = planner.plan_work([
            {"id": "a", "files": ["a.py"], "depends_on": [], "is_greenfield": False},
            {"id": "b", "files": ["b.py"], "depends_on": [], "is_greenfield": False},
        ])
        assert "No file-conflict serializations" in result["explanation"]

    def test_multi_file_conflict(self):
        planner = OrchestrationPlanner()
        result = planner.plan_work([
            {"id": "a", "files": ["shared.py", "another.py"], "depends_on": [], "is_greenfield": False},
            {"id": "b", "files": ["shared.py", "another.py"], "depends_on": [], "is_greenfield": False},
        ])
        assert "1 other shared file" in result["explanation"].lower() or "other shared file" in result["explanation"]

    def test_five_items_pick_parallel_now(self):
        planner = OrchestrationPlanner()
        items = []
        for i in range(10):
            items.append({
                "id": f"task-{i}",
                "files": [f"file-{i}.py"],
                "depends_on": [],
                "is_greenfield": False,
            })
        result = planner.plan_work(items)
        assert len(result["batches"]) == 1
        assert len(result["parallel_now"]) == 10
