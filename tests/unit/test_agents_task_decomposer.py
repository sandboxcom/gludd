"""Unit tests for agents/task_decomposer.py — TaskDecomposer, ManagerAgent, data models."""

from __future__ import annotations

from general_ludd.agents.task_decomposer import (
    ManagerAgent,
    RoleGoalBackstory,
    SubTask,
    TaskDecomposer,
)


class TestRoleGoalBackstory:
    def test_creation(self):
        rgb = RoleGoalBackstory(
            role="backend_dev",
            goal="Build robust APIs",
            backstory="Experienced backend developer",
            tools=["pytest", "fastapi"],
        )
        assert rgb.role == "backend_dev"
        assert rgb.goal == "Build robust APIs"
        assert rgb.tools == ["pytest", "fastapi"]

    def test_hash_by_role(self):
        a = RoleGoalBackstory(role="x", goal="a", backstory="b")
        b = RoleGoalBackstory(role="x", goal="c", backstory="d")
        assert hash(a) == hash(b)

    def test_equality_by_role(self):
        a = RoleGoalBackstory(role="x", goal="a", backstory="b")
        b = RoleGoalBackstory(role="x", goal="c", backstory="d")
        assert a == b

    def test_not_equal_different_role(self):
        a = RoleGoalBackstory(role="x", goal="a", backstory="b")
        b = RoleGoalBackstory(role="y", goal="a", backstory="b")
        assert a != b

    def test_not_equal_non_rgb(self):
        a = RoleGoalBackstory(role="x", goal="a", backstory="b")
        assert a != "not a role"
        assert (a == "not a role") is False

    def test_repr(self):
        rgb = RoleGoalBackstory(role="backend_dev", goal="g", backstory="b")
        assert "backend_dev" in repr(rgb)


class TestSubTask:
    def test_creation(self):
        st = SubTask(id="1", description="Write tests", status="pending")
        assert st.id == "1"
        assert st.description == "Write tests"
        assert st.status == "pending"
        assert st.dependencies == []
        assert st.assigned_role is None

    def test_with_dependencies(self):
        st = SubTask(
            id="2",
            description="Implement feature",
            dependencies=["1"],
            assigned_role="backend_dev",
        )
        assert st.dependencies == ["1"]
        assert st.assigned_role == "backend_dev"

    def test_hash_by_id(self):
        a = SubTask(id="1", description="a")
        b = SubTask(id="1", description="b")
        assert hash(a) == hash(b)

    def test_equality_by_id(self):
        a = SubTask(id="1", description="a")
        b = SubTask(id="1", description="b")
        assert a == b

    def test_not_equal_different_id(self):
        a = SubTask(id="1", description="a")
        b = SubTask(id="2", description="a")
        assert a != b

    def test_repr(self):
        st = SubTask(id="1", description="d", status="in_progress")
        assert "1" in repr(st)
        assert "in_progress" in repr(st)


class TestTaskDecomposer:
    def test_decompose_empty_description(self):
        td = TaskDecomposer()
        assert td.decompose("", "any_role") == []
        assert td.decompose("   ", "any_role") == []

    def test_decompose_whitespace_only(self):
        td = TaskDecomposer()
        assert td.decompose("   ", "any_role") == []

    def test_decompose_with_keyword_match(self):
        td = TaskDecomposer()
        result = td.decompose("Build an API for user authentication", "manager")
        assert len(result) > 0
        descriptions = [s.description for s in result]
        assert any("API" in d for d in descriptions)

    def test_decompose_default_fallback(self):
        td = TaskDecomposer()
        result = td.decompose("Do something completely novel", "agent")
        assert len(result) == 5
        assert result[0].description == "Analyze requirements and constraints"

    def test_decompose_task_ids_sequential(self):
        td = TaskDecomposer()
        result = td.decompose("Do something completely novel", "agent")
        assert result[0].id == "1"
        assert result[1].id == "2"

    def test_decompose_dependencies_chained(self):
        td = TaskDecomposer()
        result = td.decompose("Do something completely novel", "agent")
        assert result[0].dependencies == []
        assert result[1].dependencies == ["1"]

    def test_register_role(self):
        td = TaskDecomposer()
        rgb = RoleGoalBackstory(role="backend_dev", goal="Build APIs", backstory="Dev")
        td.register_role(rgb)
        assert "backend_dev" in td.list_roles()

    def test_list_roles_sorted(self):
        td = TaskDecomposer()
        td.register_role(RoleGoalBackstory(role="z_role", goal="g", backstory="b"))
        td.register_role(RoleGoalBackstory(role="a_role", goal="g", backstory="b"))
        assert td.list_roles() == ["a_role", "z_role"]

    def test_match_role_with_registered_role(self):
        td = TaskDecomposer()
        td.register_role(RoleGoalBackstory(role="backend_dev", goal="Build APIs", backstory="Dev"))
        assert td._match_role("Implement API endpoint") == "backend_dev"

    def test_match_role_no_match_returns_first_registered(self):
        td = TaskDecomposer()
        td.register_role(RoleGoalBackstory(role="first_role", goal="g", backstory="b"))
        assert td._match_role("Do something novel") == "first_role"

    def test_match_role_no_registered_returns_none(self):
        td = TaskDecomposer()
        assert td._match_role("anything") is None

    def test_decompose_assigns_roles_when_registered(self):
        td = TaskDecomposer()
        td.register_role(RoleGoalBackstory(role="backend_dev", goal="Build APIs", backstory="Dev"))
        result = td.decompose("Build an API for the backend", "manager")
        assert any(s.assigned_role == "backend_dev" for s in result)


class TestManagerAgent:
    def test_init(self):
        manager_role = RoleGoalBackstory(role="manager", goal="Coordinate", backstory="Lead")
        ma = ManagerAgent(manager_role)
        assert ma.manager_role.role == "manager"
        assert ma.team == []

    def test_add_team_member(self):
        ma = ManagerAgent(RoleGoalBackstory(role="manager", goal="g", backstory="b"))
        member = RoleGoalBackstory(role="backend_dev", goal="Build APIs", backstory="Dev")
        ma.add_team_member(member)
        assert len(ma.team) == 1
        assert ma.team[0].role == "backend_dev"

    def test_assign_tasks_with_assigned_role(self):
        ma = ManagerAgent(RoleGoalBackstory(role="manager", goal="g", backstory="b"))
        dev = RoleGoalBackstory(role="backend_dev", goal="Build APIs", backstory="Dev")
        qa = RoleGoalBackstory(role="qa_engineer", goal="Test everything", backstory="QA")
        ma.add_team_member(dev)
        ma.add_team_member(qa)

        tasks = [
            SubTask(id="1", description="Write API", assigned_role="backend_dev"),
            SubTask(id="2", description="Write tests", assigned_role="qa_engineer"),
        ]
        assignments = ma.assign_tasks(tasks)
        assert assignments["1"].role == "backend_dev"
        assert assignments["2"].role == "qa_engineer"

    def test_assign_tasks_unassigned_matches_by_keyword(self):
        ma = ManagerAgent(RoleGoalBackstory(role="manager", goal="g", backstory="b"))
        dev = RoleGoalBackstory(role="backend_dev", goal="Build robust APIs", backstory="Dev")
        ma.add_team_member(dev)

        tasks = [SubTask(id="1", description="Implement the API endpoint")]
        assignments = ma.assign_tasks(tasks)
        assert assignments["1"].role == "backend_dev"

    def test_assign_tasks_unassigned_no_match_returns_first(self):
        ma = ManagerAgent(RoleGoalBackstory(role="manager", goal="g", backstory="b"))
        dev = RoleGoalBackstory(role="backend_dev", goal="Build APIs", backstory="Dev")
        ma.add_team_member(dev)

        tasks = [SubTask(id="1", description="Do something unrelated")]
        assignments = ma.assign_tasks(tasks)
        assert assignments["1"].role == "backend_dev"

    def test_assign_tasks_empty_team_returns_none(self):
        ma = ManagerAgent(RoleGoalBackstory(role="manager", goal="g", backstory="b"))
        tasks = [SubTask(id="1", description="Task")]
        assignments = ma.assign_tasks(tasks)
        assert assignments["1"] is None

    def test_best_match_empty_team(self):
        ma = ManagerAgent(RoleGoalBackstory(role="manager", goal="g", backstory="b"))
        assert ma._best_match("anything") is None

    def test_best_match_keyword_in_goal(self):
        ma = ManagerAgent(RoleGoalBackstory(role="manager", goal="g", backstory="b"))
        dev = RoleGoalBackstory(role="backend_dev", goal="Build robust APIs", backstory="Dev")
        ma.add_team_member(dev)
        result = ma._best_match("We need to build a robust API")
        assert result is not None
        assert result.role == "backend_dev"
