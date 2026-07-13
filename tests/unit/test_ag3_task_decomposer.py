"""AG.3: Hierarchical task decomposition — CrewAI-style role-goal-backstory + manager-agent patterns."""

from __future__ import annotations

from general_ludd.agents.task_decomposer import (
    ManagerAgent,
    RoleGoalBackstory,
    SubTask,
    TaskDecomposer,
)


class TestRoleGoalBackstory:
    def test_construct_with_role_goal_backstory(self):
        r = RoleGoalBackstory(
            role="code_reviewer",
            goal="Find bugs and suggest fixes",
            backstory="Senior developer with 10 years of experience",
        )
        assert r.role == "code_reviewer"
        assert r.goal == "Find bugs and suggest fixes"
        assert "Senior developer" in r.backstory

    def test_default_tools_empty(self):
        r = RoleGoalBackstory(role="test_runner", goal="Run tests", backstory="QA engineer")
        assert r.tools == []

    def test_custom_tools(self):
        r = RoleGoalBackstory(
            role="security_auditor",
            goal="Audit for vulnerabilities",
            backstory="Security researcher",
            tools=["bandit", "safety", "trivy"],
        )
        assert r.tools == ["bandit", "safety", "trivy"]

    def test_equality_by_role(self):
        r1 = RoleGoalBackstory(role="planner", goal="Plan things", backstory="Planner")
        r2 = RoleGoalBackstory(role="planner", goal="Plan things", backstory="Planner")
        r3 = RoleGoalBackstory(role="coder", goal="Code things", backstory="Coder")
        assert r1 == r2
        assert r1 != r3

    def test_hashable_for_set_membership(self):
        r1 = RoleGoalBackstory(role="researcher", goal="Research", backstory="Librarian")
        r2 = RoleGoalBackstory(role="researcher", goal="Research", backstory="Librarian")
        s = {r1, r2}
        assert len(s) == 1

    def test_repr_includes_role(self):
        r = RoleGoalBackstory(role="architect", goal="Design system", backstory="Experienced architect")
        assert "architect" in repr(r)


class TestSubTask:
    def test_construct_basic(self):
        st = SubTask(id="1", description="Write unit tests")
        assert st.id == "1"
        assert st.description == "Write unit tests"
        assert st.dependencies == []
        assert st.assigned_role is None

    def test_with_dependencies(self):
        st = SubTask(
            id="2",
            description="Run tests",
            dependencies=["1"],
            assigned_role="test_runner",
        )
        assert st.dependencies == ["1"]
        assert st.assigned_role == "test_runner"

    def test_status_defaults_to_pending(self):
        st = SubTask(id="3", description="Deploy")
        assert st.status == "pending"

    def test_hashable_by_id(self):
        st1 = SubTask(id="a", description="Task A")
        st2 = SubTask(id="a", description="Task A")
        assert st1 == st2
        assert hash(st1) == hash(st2)

    def test_repr_includes_id_and_status(self):
        st = SubTask(id="x", description="Do X")
        r = repr(st)
        assert "x" in r
        assert "pending" in r


class TestTaskDecomposer:
    def test_decompose_simple_task_returns_subtasks(self):
        t = TaskDecomposer()
        result = t.decompose("Build a REST API", "lead_developer")
        assert len(result) > 0
        assert all(isinstance(st, SubTask) for st in result)

    def test_decompose_returns_at_least_three_subtasks_for_complex_task(self):
        t = TaskDecomposer()
        result = t.decompose(
            "Design and implement a distributed key-value store with replication and failover",
            "architect",
        )
        assert len(result) >= 3

    def test_decompose_subtasks_have_unique_ids(self):
        t = TaskDecomposer()
        result = t.decompose("Set up CI/CD pipeline", "devops_engineer")
        ids = [st.id for st in result]
        assert len(ids) == len(set(ids))

    def test_decompose_with_known_role_returns_role_specific_decomposition(self):
        t = TaskDecomposer()
        t.register_role(
            RoleGoalBackstory(
                role="code_reviewer",
                goal="Review code for correctness and style",
                backstory="Seasoned reviewer with expertise in multiple languages",
            )
        )
        result = t.decompose("Review the authentication module", "code_reviewer")
        assert len(result) > 0
        assert any("review" in st.description.lower() for st in result)

    def test_decompose_produces_dependency_chain(self):
        t = TaskDecomposer()
        result = t.decompose(
            "Build a full-stack webapp with database",
            "full_stack_developer",
        )
        has_deps = [st for st in result if st.dependencies]
        assert len(has_deps) > 0, "At least one sub-task should have dependencies"

    def test_decompose_dependency_ordering_is_valid(self):
        t = TaskDecomposer()
        result = t.decompose(
            "Create a machine learning pipeline from data ingestion to model serving",
            "ml_engineer",
        )
        id_set = {st.id for st in result}
        for st in result:
            for dep_id in st.dependencies:
                assert dep_id in id_set, f"Dependency {dep_id!r} not in sub-task ids"

    def test_register_and_list_roles(self):
        t = TaskDecomposer()
        r = RoleGoalBackstory(
            role="security_auditor",
            goal="Find security holes",
            backstory="Ethical hacker",
        )
        t.register_role(r)
        roles = t.list_roles()
        assert "security_auditor" in roles

    def test_decompose_empty_task_returns_empty_list(self):
        t = TaskDecomposer()
        result = t.decompose("", "developer")
        assert result == []

    def test_decompose_with_role_backstory_routes_subtasks_to_matching_roles(self):
        t = TaskDecomposer()
        t.register_role(
            RoleGoalBackstory(
                role="designer",
                goal="Create UI/UX designs",
                backstory="Designer with Figma expertise",
            )
        )
        t.register_role(
            RoleGoalBackstory(
                role="backend_dev",
                goal="Build server-side logic",
                backstory="Backend engineer in Python and Rust",
            )
        )
        result = t.decompose(
            "Build a user dashboard with analytics",
            "manager",
        )
        assigned = [st for st in result if st.assigned_role is not None]
        assert len(assigned) > 0

    def test_decompose_returns_status_pending(self):
        t = TaskDecomposer()
        result = t.decompose("Refactor the codebase", "senior_dev")
        for st in result:
            assert st.status == "pending"


class TestManagerAgent:
    def test_construct_with_role(self):
        role = RoleGoalBackstory(
            role="project_manager",
            goal="Deliver project on time",
            backstory="Experienced PM with agile background",
        )
        m = ManagerAgent(manager_role=role)
        assert m.manager_role == role
        assert m.team == []

    def test_add_team_member(self):
        pm = RoleGoalBackstory(role="pm", goal="Manage project", backstory="PM")
        dev = RoleGoalBackstory(role="dev", goal="Write code", backstory="Developer")
        m = ManagerAgent(manager_role=pm)
        m.add_team_member(dev)
        assert len(m.team) == 1
        assert m.team[0] == dev

    def test_assign_task_to_role_matched_agent(self):
        pm = RoleGoalBackstory(role="pm", goal="Manage", backstory="PM")
        dev = RoleGoalBackstory(role="backend_dev", goal="Build APIs", backstory="Backend dev")
        qa = RoleGoalBackstory(role="qa_engineer", goal="Test features", backstory="QA engineer")
        m = ManagerAgent(manager_role=pm)
        m.add_team_member(dev)
        m.add_team_member(qa)

        tasks = [
            SubTask(id="1", description="Build REST endpoints"),
            SubTask(id="2", description="Write integration tests"),
        ]
        assignments = m.assign_tasks(tasks)
        assert len(assignments) == 2

    def test_assign_tasks_uses_role_backstory_for_matching(self):
        pm = RoleGoalBackstory(role="pm", goal="Manage", backstory="PM")
        dev = RoleGoalBackstory(role="backend_dev", goal="Build backend services", backstory="Backend developer")
        ds = RoleGoalBackstory(role="data_scientist", goal="Analyze data", backstory="Data scientist")
        m = ManagerAgent(manager_role=pm)
        m.add_team_member(dev)
        m.add_team_member(ds)

        tasks = [
            SubTask(id="1", description="Implement database schema for user profiles"),
            SubTask(id="2", description="Analyze user engagement metrics"),
        ]
        assignments = m.assign_tasks(tasks)
        assert len(assignments) == 2

    def test_manager_can_decompose_and_assign(self):
        pm = RoleGoalBackstory(
            role="engineering_manager",
            goal="Deliver high-quality software",
            backstory="Engineering manager leading a full-stack team",
        )
        dev = RoleGoalBackstory(role="developer", goal="Implement features", backstory="Full-stack developer")
        m = ManagerAgent(manager_role=pm)
        m.add_team_member(dev)

        decomposer = TaskDecomposer()
        subtasks = decomposer.decompose(
            "Add a user authentication system with OAuth2 support",
            "engineering_manager",
        )
        assert len(subtasks) > 0

        assignments = m.assign_tasks(subtasks)
        assert len(assignments) == len(subtasks)

    def test_no_team_members_returns_empty_assignments(self):
        pm = RoleGoalBackstory(role="solo_pm", goal="Manage solo", backstory="Solo PM")
        m = ManagerAgent(manager_role=pm)
        assignments = m.assign_tasks([SubTask(id="1", description="Do everything")])
        assert len(assignments) == 1
        unresolved = [a for a in assignments.values() if a is None]
        assert len(unresolved) == 1

    def test_assign_tasks_respects_dependency_order(self):
        pm = RoleGoalBackstory(role="pm", goal="Manage", backstory="PM")
        dev = RoleGoalBackstory(role="dev", goal="Code", backstory="Developer")
        m = ManagerAgent(manager_role=pm)
        m.add_team_member(dev)

        tasks = [
            SubTask(id="3", description="Deploy to production", dependencies=["1", "2"]),
            SubTask(id="1", description="Write code"),
            SubTask(id="2", description="Review code", dependencies=["1"]),
        ]
        assignments = m.assign_tasks(tasks)
        assert len(assignments) == 3


class TestTaskDecomposerIntegration:
    """End-to-end: register roles, decompose a complex task, assign via manager."""

    def test_full_pipeline_design_to_assignment(self):
        decomposer = TaskDecomposer()
        manager_role = RoleGoalBackstory(
            role="architect",
            goal="Design robust systems",
            backstory="Chief architect with 15 years of experience",
        )
        backend = RoleGoalBackstory(
            role="backend_dev",
            goal="Implement server-side logic and APIs",
            backstory="Senior backend engineer",
        )
        frontend = RoleGoalBackstory(
            role="frontend_dev",
            goal="Build user-facing interfaces",
            backstory="Senior frontend engineer",
        )
        devops = RoleGoalBackstory(
            role="devops_engineer",
            goal="Automate deployment and infrastructure",
            backstory="SRE with cloud expertise",
        )

        for role in [backend, frontend, devops]:
            decomposer.register_role(role)

        manager = ManagerAgent(manager_role=manager_role)
        for role in [backend, frontend, devops]:
            manager.add_team_member(role)

        subtasks = decomposer.decompose(
            "Build a real-time collaborative document editor with version history",
            "architect",
        )

        assert len(subtasks) >= 4, "Complex task should decompose into 4+ sub-tasks"

        assignments = manager.assign_tasks(subtasks)
        assert len(assignments) == len(subtasks)

        member_roles = {m.role for m in manager.team}
        for st in subtasks:
            if st.assigned_role is not None:
                assert st.assigned_role in member_roles, (
                    f"SubTask {st.id!r} assigned to unknown role {st.assigned_role!r}"
                )
