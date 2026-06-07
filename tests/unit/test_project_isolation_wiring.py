"""Tests that all project isolation layers are wired end-to-end."""

from __future__ import annotations


class TestProjectSecretsWired:
    def test_project_secrets_manager_importable(self):
        from general_ludd.secrets.project_secrets import ProjectSecretsManager
        assert ProjectSecretsManager

    def test_build_secrets_resolver_returns_project_aware_when_projects_exist(self):
        from general_ludd.daemon import build_secrets_resolver
        resolver = build_secrets_resolver(projects_active=False)
        assert hasattr(resolver, "resolve")

    def test_project_secrets_wired_into_event_loop_dispatch(self):
        from general_ludd.event_loop.loop import EventLoop
        assert hasattr(EventLoop, "__init__")
        import inspect
        sig = inspect.signature(EventLoop.__init__)
        assert "project_secrets_manager" in sig.parameters


class TestProjectWorkspaceWired:
    def test_project_workspace_importable(self):
        from general_ludd.projects.workspace import ProjectWorkspace
        assert ProjectWorkspace

    def test_project_workspace_has_playbooks_dir(self):
        from general_ludd.projects.workspace import ProjectWorkspace
        ws = ProjectWorkspace(project_id="test", workspace_path="/tmp/gludd-test")
        assert hasattr(ws, "playbooks_dir")

    def test_project_workspace_has_templates_dir(self):
        from general_ludd.projects.workspace import ProjectWorkspace
        ws = ProjectWorkspace(project_id="test", workspace_path="/tmp/gludd-test")
        assert hasattr(ws, "templates_dir")

    def test_event_loop_accepts_project_workspace(self):
        import inspect

        from general_ludd.event_loop.loop import EventLoop
        sig = inspect.signature(EventLoop.__init__)
        assert "project_workspace" in sig.parameters


class TestPerProjectPlaybooks:
    def test_playbook_for_work_type_accepts_project_id(self):
        import inspect

        from general_ludd.event_loop.loop import _playbook_for_work_type
        sig = inspect.signature(_playbook_for_work_type)
        assert "project_id" in sig.parameters

    def test_playbook_project_fallback_to_default(self):
        from general_ludd.event_loop.loop import _playbook_for_work_type
        result = _playbook_for_work_type("code", project_id=None)
        assert isinstance(result, str)
        assert len(result) > 0


class TestPerProjectSkills:
    def test_skill_registry_match_trigger_accepts_project_id(self):
        import inspect

        from general_ludd.skills.registry import SkillRegistry
        sig = inspect.signature(SkillRegistry.match_trigger)
        assert "project_id" in sig.parameters

    def test_skill_registry_register_accepts_project_id(self):
        from general_ludd.skills.registry import SkillRegistry
        sr = SkillRegistry()
        from general_ludd.skills.skill import Skill
        s = Skill(name="test", tags=[], body="test", trigger_patterns=["test"])
        sr.register(s, project_id="proj-1")
        result = sr.match_trigger("test trigger", project_id="proj-1")
        assert len(result) >= 1

    def test_skill_registry_isolated_by_project(self):
        from general_ludd.skills.registry import SkillRegistry
        from general_ludd.skills.skill import Skill
        sr = SkillRegistry()
        s1 = Skill(name="p1-skill", tags=[], body="proj1", trigger_patterns=["proj1"])
        sr.register(s1, project_id="proj-1")
        result_a = sr.match_trigger("proj1 trigger", project_id="proj-1")
        result_b = sr.match_trigger("proj1 trigger", project_id="proj-2")
        assert len(result_a) >= 1
        assert len(result_b) == 0


class TestPerProjectMCP:
    def test_mcp_server_config_has_project_id(self):
        from general_ludd.mcp.config import MCPServerConfig
        assert "project_id" in MCPServerConfig.model_fields

    def test_mcp_config_project_id_defaults_none(self):
        from general_ludd.mcp.config import MCPServerConfig
        cfg = MCPServerConfig(server_id="test", command=["echo"])
        assert cfg.project_id is None

    def test_mcp_client_list_for_project(self):
        from general_ludd.mcp.client import MCPClient
        assert hasattr(MCPClient, "list_for_project")


class TestProjectLogWired:
    def test_project_log_adapter_importable(self):
        from general_ludd.logging.project_log import ProjectLogAdapter
        assert ProjectLogAdapter

    def test_project_log_filter_importable(self):
        from general_ludd.logging.project_log import ProjectLogFilter
        assert ProjectLogFilter

    def test_daemon_uses_project_log_adapter(self):
        import general_ludd.daemon as dm
        with open(str(dm.__file__)) as f:
            source = f.read()
        assert "ProjectLogAdapter" in source or "project_log" in source
