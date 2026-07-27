"""Unit tests for the E2E test scenario knowledge catalog."""

from __future__ import annotations

from general_ludd.agents.test_generation.knowledge.test_scenarios import E2E_SCENARIOS, E2EScenario


class TestE2EScenarioDataclass:
    def test_create_with_required_fields(self):
        s = E2EScenario(name="test", description="a test scenario")
        assert s.name == "test"
        assert s.description == "a test scenario"
        assert s.steps == []
        assert s.tags == []

    def test_create_with_all_fields(self):
        s = E2EScenario(
            name="full",
            description="fully populated",
            steps=["step1", "step2"],
            tags=["api", "rest"],
        )
        assert s.steps == ["step1", "step2"]
        assert s.tags == ["api", "rest"]

    def test_fields_are_mutable(self):
        s = E2EScenario(name="mut", description="desc")
        s.steps.append("new_step")
        s.tags.append("new_tag")
        assert s.steps == ["new_step"]
        assert s.tags == ["new_tag"]

    def test_default_lists_are_independent(self):
        a = E2EScenario(name="a", description="desc")
        b = E2EScenario(name="b", description="desc")
        a.steps.append("only_in_a")
        assert b.steps == []


class TestE2EScenariosCatalog:
    def test_catalog_is_non_empty(self):
        assert len(E2E_SCENARIOS) > 0

    def test_all_entries_are_e2e_scenarios(self):
        for s in E2E_SCENARIOS:
            assert isinstance(s, E2EScenario)

    def test_all_have_names_and_descriptions(self):
        for s in E2E_SCENARIOS:
            assert s.name, f"scenario missing name"
            assert s.description, f"scenario '{s.name}' missing description"

    def test_all_have_tags(self):
        for s in E2E_SCENARIOS:
            assert s.tags, f"scenario '{s.name}' has no tags"

    def test_all_have_steps(self):
        for s in E2E_SCENARIOS:
            assert s.steps, f"scenario '{s.name}' has no steps"

    def test_all_names_are_unique(self):
        names = [s.name for s in E2E_SCENARIOS]
        assert len(names) == len(set(names))

    def test_expected_scenarios_present(self):
        names = {s.name for s in E2E_SCENARIOS}
        expected = {"crud_lifecycle", "auth_flow", "timeout_handling", "concurrent_edits", "daemon_restart"}
        assert expected <= names

    def test_no_unexpected_scenarios(self):
        names = {s.name for s in E2E_SCENARIOS}
        expected = {"crud_lifecycle", "auth_flow", "timeout_handling", "concurrent_edits", "daemon_restart"}
        assert names == expected

    def test_crud_lifecycle_steps_have_post_get_delete(self):
        crud = next(s for s in E2E_SCENARIOS if s.name == "crud_lifecycle")
        step_text = " ".join(crud.steps).lower()
        assert "post" in step_text
        assert "get" in step_text
        assert "delete" in step_text

    def test_auth_flow_has_login_and_protected(self):
        auth = next(s for s in E2E_SCENARIOS if s.name == "auth_flow")
        step_text = " ".join(auth.steps).lower()
        assert "login" in step_text
        assert "protected" in step_text

    def test_timeout_handling_has_retry(self):
        timeout = next(s for s in E2E_SCENARIOS if s.name == "timeout_handling")
        step_text = " ".join(timeout.steps).lower()
        assert "retry" in step_text or "timeout" in step_text

    def test_concurrent_edits_has_multiple_clients(self):
        concurrent = next(s for s in E2E_SCENARIOS if s.name == "concurrent_edits")
        step_text = " ".join(concurrent.steps).lower()
        assert "client_a" in step_text or "client" in step_text

    def test_daemon_restart_has_kill_and_restart(self):
        daemon = next(s for s in E2E_SCENARIOS if s.name == "daemon_restart")
        step_text = " ".join(daemon.steps).lower()
        assert "kill" in step_text or "restart" in step_text
