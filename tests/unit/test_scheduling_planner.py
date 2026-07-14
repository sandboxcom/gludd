"""Structural tests for scheduling/planner.py — deterministic concurrency planner."""

from general_ludd.scheduling.planner import OrchestrationPlanner, PlanResult


class TestPlanResult:
    def test_default_construction(self):
        pr = PlanResult()
        assert pr.batches == []
        assert pr.parallel_now == []
        assert pr.serialized == []
        assert pr.explanation == ""

    def test_custom_construction(self):
        pr = PlanResult(
            batches=[["a", "b"]],
            parallel_now=["a", "b"],
            serialized=[("a", "c", "src/foo.py")],
            explanation="test",
        )
        assert pr.parallel_now == ["a", "b"]
        assert len(pr.serialized) == 1


class TestOrchestrationPlannerConstruction:
    def test_construct(self):
        planner = OrchestrationPlanner()
        assert planner is not None
        assert hasattr(planner, "plan_work")
        assert hasattr(planner, "parallelizable")


class TestOrchestrationPlannerPlanWork:
    def test_empty_items(self):
        planner = OrchestrationPlanner()
        result = planner.plan_work([])
        assert result["batches"] == []
        assert result["parallel_now"] == []
        assert result["serialized"] == []
        assert isinstance(result["explanation"], str)

    def test_single_item(self):
        planner = OrchestrationPlanner()
        result = planner.plan_work([
            {"id": "a", "files": [], "depends_on": [], "is_greenfield": False},
        ])
        assert result["batches"] == [["a"]]
        assert result["parallel_now"] == ["a"]

    def test_independent_items_parallel(self):
        planner = OrchestrationPlanner()
        result = planner.plan_work([
            {"id": "a", "files": ["src/foo.py"], "depends_on": [], "is_greenfield": False},
            {"id": "b", "files": ["src/bar.py"], "depends_on": [], "is_greenfield": False},
        ])
        assert "a" in result["parallel_now"]
        assert "b" in result["parallel_now"]

    def test_conflicting_files_serialize(self):
        planner = OrchestrationPlanner()
        result = planner.plan_work([
            {"id": "a", "files": ["src/foo.py"], "depends_on": [], "is_greenfield": False},
            {"id": "b", "files": ["src/foo.py"], "depends_on": [], "is_greenfield": False},
        ])
        assert len(result["batches"]) >= 2
        # They must be in different batches
        batch_0 = set(result["batches"][0])
        assert len(batch_0) == 1

    def test_depends_on_ordering(self):
        planner = OrchestrationPlanner()
        result = planner.plan_work([
            {"id": "a", "files": ["src/foo.py"], "depends_on": [], "is_greenfield": False},
            {"id": "b", "files": ["src/bar.py"], "depends_on": ["a"], "is_greenfield": False},
        ])
        # a should be in an earlier batch than b
        assert "a" in result["batches"][0]
        assert "b" not in result["batches"][0]

    def test_greenfield_no_files_never_serializes(self):
        planner = OrchestrationPlanner()
        result = planner.plan_work([
            {"id": "g", "files": [], "depends_on": [], "is_greenfield": True},
            {"id": "h", "files": [], "depends_on": [], "is_greenfield": True},
            {"id": "i", "files": [], "depends_on": [], "is_greenfield": True},
        ])
        # Greenfield items with no files should all be parallel
        assert len(result["batches"]) == 1
        assert len(result["batches"][0]) == 3

    def test_result_has_expected_keys(self):
        planner = OrchestrationPlanner()
        result = planner.plan_work([
            {"id": "a", "files": [], "depends_on": [], "is_greenfield": False},
        ])
        for key in ("batches", "parallel_now", "serialized", "explanation"):
            assert key in result, f"missing key {key}"


class TestOrchestrationPlannerParallelizable:
    def test_returns_ids(self):
        planner = OrchestrationPlanner()
        ids = planner.parallelizable([
            {"id": "a", "files": [], "depends_on": [], "is_greenfield": False},
            {"id": "b", "files": [], "depends_on": [], "is_greenfield": False},
        ])
        assert "a" in ids
        assert "b" in ids

    def test_serialized_items_not_parallelizable(self):
        planner = OrchestrationPlanner()
        ids = planner.parallelizable([
            {"id": "a", "files": ["src/foo.py"], "depends_on": [], "is_greenfield": False},
            {"id": "b", "files": ["src/foo.py"], "depends_on": [], "is_greenfield": False},
        ])
        assert len(ids) == 1
