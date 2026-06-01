"""Unit tests for ProjectManager — multi-project weight allocation."""

from __future__ import annotations

import pytest

from general_ludd.projects.manager import ProjectAllocationError, ProjectManager


class TestAddProject:
    def test_add_project_with_valid_weight(self):
        mgr = ProjectManager()
        p = mgr.add_project("alpha", 30.0, description="Alpha project")
        assert p.name == "alpha"
        assert p.weight == 30.0
        assert p.active is True
        assert p.project_id.startswith("proj-")
        assert p.description == "Alpha project"

    def test_add_project_updates_total_weight(self):
        mgr = ProjectManager()
        mgr.add_project("a", 25.0)
        mgr.add_project("b", 35.0)
        assert mgr.total_weight() == 60.0

    def test_add_project_fills_100_percent(self):
        mgr = ProjectManager()
        mgr.add_project("a", 40.0)
        mgr.add_project("b", 60.0)
        assert mgr.total_weight() == 100.0

    def test_add_project_exceeds_100_raises(self):
        mgr = ProjectManager()
        mgr.add_project("a", 60.0)
        with pytest.raises(ProjectAllocationError, match=r"only 40\.0% available"):
            mgr.add_project("b", 50.0)

    def test_add_project_zero_weight(self):
        mgr = ProjectManager()
        p = mgr.add_project("zero", 0.0)
        assert p.weight == 0.0
        assert mgr.total_weight() == 0.0

    def test_add_single_100_percent_project(self):
        mgr = ProjectManager()
        p = mgr.add_project("full", 100.0)
        assert p.weight == 100.0
        assert mgr.total_weight() == 100.0

    def test_add_second_project_after_100_raises(self):
        mgr = ProjectManager()
        mgr.add_project("full", 100.0)
        with pytest.raises(ProjectAllocationError, match=r"only 0\.0% available"):
            mgr.add_project("second", 1.0)

    def test_add_project_stores_config(self):
        mgr = ProjectManager()
        p = mgr.add_project("cfg", 50.0, priority="high", tier=2)
        assert p.config["priority"] == "high"
        assert p.config["tier"] == 2


class TestRemoveProject:
    def test_remove_project_marks_inactive(self):
        mgr = ProjectManager()
        p = mgr.add_project("alpha", 40.0)
        mgr.remove_project(p.project_id)
        assert p.active is False
        assert mgr.total_weight() == 0.0

    def test_remove_nonexistent_does_nothing(self):
        mgr = ProjectManager()
        mgr.remove_project("proj-nope0000")

    def test_removed_project_not_in_active_list(self):
        mgr = ProjectManager()
        p = mgr.add_project("alpha", 50.0)
        mgr.remove_project(p.project_id)
        active = mgr.list_projects(active_only=True)
        assert len(active) == 0

    def test_removed_project_in_all_list(self):
        mgr = ProjectManager()
        p = mgr.add_project("alpha", 50.0)
        mgr.remove_project(p.project_id)
        all_projects = mgr.list_projects(active_only=False)
        assert len(all_projects) == 1
        assert all_projects[0].active is False

    def test_remove_frees_weight_for_new_project(self):
        mgr = ProjectManager()
        p = mgr.add_project("alpha", 100.0)
        mgr.remove_project(p.project_id)
        new_p = mgr.add_project("beta", 100.0)
        assert new_p.weight == 100.0


class TestSetWeight:
    def test_set_weight_on_existing_project(self):
        mgr = ProjectManager()
        p = mgr.add_project("alpha", 30.0)
        mgr.set_weight(p.project_id, 50.0)
        assert p.weight == 50.0

    def test_set_weight_preserves_total_constraint(self):
        mgr = ProjectManager()
        a = mgr.add_project("a", 40.0)
        mgr.add_project("b", 60.0)
        mgr.set_weight(a.project_id, 20.0)
        assert mgr.total_weight() == 80.0

    def test_set_weight_exceeds_100_raises(self):
        mgr = ProjectManager()
        mgr.add_project("a", 60.0)
        b = mgr.add_project("b", 40.0)
        with pytest.raises(ProjectAllocationError, match=r"only 40\.0% available"):
            mgr.set_weight(b.project_id, 70.0)

    def test_set_weight_on_nonexistent_raises(self):
        mgr = ProjectManager()
        with pytest.raises(ProjectAllocationError, match="not found"):
            mgr.set_weight("proj-nope0000", 10.0)

    def test_set_weight_on_inactive_raises(self):
        mgr = ProjectManager()
        p = mgr.add_project("alpha", 50.0)
        mgr.remove_project(p.project_id)
        with pytest.raises(ProjectAllocationError, match="not active"):
            mgr.set_weight(p.project_id, 20.0)

    def test_set_weight_negative_raises(self):
        mgr = ProjectManager()
        p = mgr.add_project("alpha", 50.0)
        with pytest.raises(ProjectAllocationError, match="between 0 and 100"):
            mgr.set_weight(p.project_id, -5.0)

    def test_set_weight_above_100_raises(self):
        mgr = ProjectManager()
        p = mgr.add_project("alpha", 50.0)
        with pytest.raises(ProjectAllocationError, match="between 0 and 100"):
            mgr.set_weight(p.project_id, 150.0)


class TestRebalance:
    def test_rebalance_valid_weights(self):
        mgr = ProjectManager()
        a = mgr.add_project("a", 50.0)
        b = mgr.add_project("b", 50.0)
        mgr.rebalance({a.project_id: 70.0, b.project_id: 30.0})
        assert a.weight == 70.0
        assert b.weight == 30.0
        assert mgr.total_weight() == 100.0

    def test_rebalance_fails_not_100(self):
        mgr = ProjectManager()
        a = mgr.add_project("a", 50.0)
        b = mgr.add_project("b", 50.0)
        with pytest.raises(ProjectAllocationError, match="sum to 100%"):
            mgr.rebalance({a.project_id: 60.0, b.project_id: 50.0})

    def test_rebalance_unknown_project_raises(self):
        mgr = ProjectManager()
        a = mgr.add_project("a", 100.0)
        with pytest.raises(ProjectAllocationError, match="not found"):
            mgr.rebalance({a.project_id: 50.0, "proj-nope0000": 50.0})

    def test_rebalance_inactive_project_raises(self):
        mgr = ProjectManager()
        a = mgr.add_project("a", 50.0)
        b = mgr.add_project("b", 50.0)
        mgr.remove_project(b.project_id)
        with pytest.raises(ProjectAllocationError, match="not active"):
            mgr.rebalance({a.project_id: 100.0, b.project_id: 0.0})

    def test_rebalance_three_projects(self):
        mgr = ProjectManager()
        a = mgr.add_project("a", 33.0)
        b = mgr.add_project("b", 33.0)
        c = mgr.add_project("c", 34.0)
        mgr.rebalance({a.project_id: 50.0, b.project_id: 30.0, c.project_id: 20.0})
        assert mgr.total_weight() == 100.0


class TestListProjects:
    def test_list_active_only(self):
        mgr = ProjectManager()
        a = mgr.add_project("a", 40.0)
        mgr.add_project("b", 60.0)
        mgr.remove_project(a.project_id)
        active = mgr.list_projects(active_only=True)
        assert len(active) == 1
        assert active[0].name == "b"

    def test_list_all_includes_inactive(self):
        mgr = ProjectManager()
        a = mgr.add_project("a", 40.0)
        mgr.add_project("b", 60.0)
        mgr.remove_project(a.project_id)
        all_p = mgr.list_projects(active_only=False)
        assert len(all_p) == 2

    def test_list_empty_manager(self):
        mgr = ProjectManager()
        assert mgr.list_projects() == []


class TestTotalWeight:
    def test_total_weight_empty(self):
        mgr = ProjectManager()
        assert mgr.total_weight() == 0.0

    def test_total_weight_multiple_projects(self):
        mgr = ProjectManager()
        mgr.add_project("a", 25.0)
        mgr.add_project("b", 25.0)
        mgr.add_project("c", 50.0)
        assert mgr.total_weight() == 100.0

    def test_total_weight_ignores_inactive(self):
        mgr = ProjectManager()
        a = mgr.add_project("a", 50.0)
        mgr.add_project("b", 50.0)
        mgr.remove_project(a.project_id)
        assert mgr.total_weight() == 50.0


class TestGetAllocation:
    def test_get_allocation_dict(self):
        mgr = ProjectManager()
        a = mgr.add_project("a", 30.0)
        b = mgr.add_project("b", 70.0)
        alloc = mgr.get_allocation()
        assert len(alloc) == 2
        assert alloc[a.project_id] == 30.0
        assert alloc[b.project_id] == 70.0

    def test_get_allocation_excludes_inactive(self):
        mgr = ProjectManager()
        a = mgr.add_project("a", 50.0)
        mgr.add_project("b", 50.0)
        mgr.remove_project(a.project_id)
        alloc = mgr.get_allocation()
        assert len(alloc) == 1


class TestSelectProject:
    def test_select_project_returns_none_when_empty(self):
        mgr = ProjectManager()
        assert mgr.select_project() is None

    def test_select_project_returns_single_project(self):
        mgr = ProjectManager()
        p = mgr.add_project("only", 100.0)
        selected = mgr.select_project()
        assert selected.project_id == p.project_id

    def test_select_project_weighted_distribution(self):
        mgr = ProjectManager()
        a = mgr.add_project("heavy", 99.0)
        b = mgr.add_project("light", 1.0)
        counts = {a.project_id: 0, b.project_id: 0}
        for _ in range(10000):
            selected = mgr.select_project()
            counts[selected.project_id] += 1
        assert counts[a.project_id] > counts[b.project_id]
        assert counts[b.project_id] > 0


class TestGetSummary:
    def test_get_summary_empty(self):
        mgr = ProjectManager()
        s = mgr.get_summary()
        assert s["total_projects"] == 0
        assert s["active_projects"] == 0
        assert s["total_weight"] == 0.0
        assert s["unallocated"] == 100.0

    def test_get_summary_with_projects(self):
        mgr = ProjectManager()
        a = mgr.add_project("a", 60.0, description="first")
        b = mgr.add_project("b", 40.0, description="second")
        mgr.remove_project(a.project_id)
        s = mgr.get_summary()
        assert s["total_projects"] == 2
        assert s["active_projects"] == 1
        assert s["total_weight"] == 40.0
        assert s["unallocated"] == 60.0
        assert len(s["projects"]) == 2
        by_id = {p["project_id"]: p for p in s["projects"]}
        assert by_id[a.project_id]["active"] is False
        assert by_id[b.project_id]["active"] is True
        assert by_id[b.project_id]["description"] == "second"


class TestGetProject:
    def test_get_project_found(self):
        mgr = ProjectManager()
        p = mgr.add_project("alpha", 50.0)
        found = mgr.get_project(p.project_id)
        assert found is p

    def test_get_project_not_found(self):
        mgr = ProjectManager()
        assert mgr.get_project("proj-nope0000") is None


class TestWeightInvariant:
    def test_weights_never_exceed_100_after_add(self):
        mgr = ProjectManager()
        mgr.add_project("a", 50.0)
        mgr.add_project("b", 50.0)
        assert mgr.total_weight() <= 100.0

    def test_weights_never_exceed_100_after_set_weight(self):
        mgr = ProjectManager()
        a = mgr.add_project("a", 30.0)
        mgr.add_project("b", 70.0)
        mgr.set_weight(a.project_id, 10.0)
        assert mgr.total_weight() <= 100.0

    def test_weights_never_exceed_100_after_remove(self):
        mgr = ProjectManager()
        a = mgr.add_project("a", 60.0)
        mgr.add_project("b", 40.0)
        mgr.remove_project(a.project_id)
        assert mgr.total_weight() <= 100.0

    def test_weights_never_exceed_100_after_rebalance(self):
        mgr = ProjectManager()
        a = mgr.add_project("a", 40.0)
        b = mgr.add_project("b", 60.0)
        mgr.rebalance({a.project_id: 90.0, b.project_id: 10.0})
        assert mgr.total_weight() == 100.0
