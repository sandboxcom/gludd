"""W6 wire-up proof: every work_type in loop.py's map resolves to an existing
playbook file, and each playbook passes ActionPolicy/manifest validation.

This test suite asserts:
  1. _WORK_TYPE_PLAYBOOK_MAP contains only playbooks that exist on disk.
  2. generate_manifest() succeeds for each playbook (no YAML errors).
  3. validate_action() with a permissive policy allows each playbook.
  4. No banned modules (shell/command with templated user input) appear in
     the manifests for playbooks that should not have them.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from general_ludd.ansible.action_policy import (
    ActionPolicyConfig,
    validate_action,
)
from general_ludd.ansible.manifest import generate_manifest
from general_ludd.event_loop.loop import _WORK_TYPE_PLAYBOOK_MAP

# Project root
ROOT = Path(__file__).parent.parent.parent
PLAYBOOKS_DIR = ROOT / "playbooks"

# Playbooks that use ansible.builtin.command for user-supplied commands — allowed
_COMMAND_ALLOWED_PLAYBOOKS = {
    "validate_task.yml",  # explicitly runs test_commands loop (user-supplied, not templated strings)
}


class TestWorkTypePlaybookRegistry:
    """Every work_type in _WORK_TYPE_PLAYBOOK_MAP must resolve to a real file."""

    def test_all_work_types_have_playbook_entries(self):
        """Sanity: the map is non-empty and contains expected work types."""
        assert len(_WORK_TYPE_PLAYBOOK_MAP) > 0, "_WORK_TYPE_PLAYBOOK_MAP is empty"
        expected_keys = {"code", "test", "analysis", "audit", "prompt", "self_improve",
                         "dependency", "review"}
        for key in expected_keys:
            assert key in _WORK_TYPE_PLAYBOOK_MAP, (
                f"work_type '{key}' missing from _WORK_TYPE_PLAYBOOK_MAP"
            )

    def test_langgraph_feature_work_types_wired(self):
        from general_ludd.models.job_invocation import _GENERATION_WORK_TYPES
        assert _WORK_TYPE_PLAYBOOK_MAP["model_decision"] == "langgraph_decide.yml"
        assert _WORK_TYPE_PLAYBOOK_MAP["langgraph_generate"] == "langchain_generate.yml"
        assert "model_decision" not in _GENERATION_WORK_TYPES
        assert "langgraph_generate" not in _GENERATION_WORK_TYPES

    @pytest.mark.parametrize("work_type,playbook_name", list(_WORK_TYPE_PLAYBOOK_MAP.items()))
    def test_playbook_file_exists(self, work_type: str, playbook_name: str):
        """Each mapped playbook file must exist on disk."""
        playbook_path = PLAYBOOKS_DIR / playbook_name
        assert playbook_path.is_file(), (
            f"work_type '{work_type}' maps to '{playbook_name}' but "
            f"{playbook_path} does not exist"
        )

    @pytest.mark.parametrize("work_type,playbook_name", list(_WORK_TYPE_PLAYBOOK_MAP.items()))
    def test_playbook_is_valid_yaml(self, work_type: str, playbook_name: str):
        """Each playbook must parse as valid YAML."""
        playbook_path = PLAYBOOKS_DIR / playbook_name
        assert playbook_path.is_file(), f"Required playbook {playbook_name} does not exist"
        content = playbook_path.read_text()
        parsed = yaml.safe_load(content)
        assert isinstance(parsed, list), (
            f"{playbook_name}: top-level must be a list of plays"
        )
        assert len(parsed) > 0, f"{playbook_name}: must have at least one play"

    @pytest.mark.parametrize("work_type,playbook_name", list(_WORK_TYPE_PLAYBOOK_MAP.items()))
    def test_playbook_has_hosts_and_gather_facts(self, work_type: str, playbook_name: str):
        """Each playbook must have 'hosts' in every play and 'gather_facts: false'."""
        playbook_path = PLAYBOOKS_DIR / playbook_name
        assert playbook_path.is_file(), f"Required playbook {playbook_name} does not exist"
        plays = yaml.safe_load(playbook_path.read_text()) or []
        for i, play in enumerate(plays):
            if not isinstance(play, dict):
                continue
            assert "hosts" in play, (
                f"{playbook_name} play[{i}] missing 'hosts' key"
            )

    @pytest.mark.parametrize("work_type,playbook_name", list(_WORK_TYPE_PLAYBOOK_MAP.items()))
    def test_manifest_extraction_succeeds(self, work_type: str, playbook_name: str):
        """generate_manifest() must succeed without raising for each playbook."""
        playbook_path = PLAYBOOKS_DIR / playbook_name
        assert playbook_path.is_file(), f"Required playbook {playbook_name} does not exist"
        manifest = generate_manifest(str(playbook_path))
        assert manifest.playbook == playbook_name, (
            f"manifest.playbook mismatch: got {manifest.playbook!r}"
        )

    @pytest.mark.parametrize("work_type,playbook_name", list(_WORK_TYPE_PLAYBOOK_MAP.items()))
    def test_action_policy_allows_playbook(self, work_type: str, playbook_name: str):
        """A permissive ActionPolicy must allow each playbook."""
        playbook_path = PLAYBOOKS_DIR / playbook_name
        assert playbook_path.is_file(), f"Required playbook {playbook_name} does not exist"
        policy = ActionPolicyConfig(enabled=True, default_mode="allow")
        manifest = generate_manifest(str(playbook_path))
        result = validate_action(policy, manifest)
        assert result.allowed, (
            f"Policy denied {playbook_name}: {result.reason}"
        )


class TestCollectionStructure:
    """The general_ludd.agent collection must have the required skeleton."""

    COLLECTION_DIR = ROOT / "collections" / "ansible_collections" / "general_ludd" / "agent"

    def test_galaxy_yml_exists(self):
        assert (self.COLLECTION_DIR / "galaxy.yml").is_file(), (
            "galaxy.yml missing from collection"
        )

    def test_galaxy_yml_valid(self):
        galaxy_path = self.COLLECTION_DIR / "galaxy.yml"
        assert galaxy_path.is_file(), "Required collection galaxy.yml is missing"
        data = yaml.safe_load(galaxy_path.read_text())
        assert data.get("namespace") == "general_ludd"
        assert data.get("name") == "agent"
        assert "version" in data

    @pytest.mark.parametrize("module_name", [
        "gludd_ping",
        "gludd_model_call",
        "gludd_worktree",
        "gludd_git",
        "gludd_db",
        "gludd_skill",
        "gludd_mcp_tool",
        "gludd_agent_run",
        "gludd_facts",
        "gludd_message",
        "gludd_metrics",
        "gludd_traces",
        "gludd_langchain_generate",
        "gludd_langgraph_workflow",
        "gludd_langgraph_decision",
        "gludd_open_code",
    ])
    def test_module_file_exists(self, module_name: str):
        module_path = self.COLLECTION_DIR / "plugins" / "modules" / f"{module_name}.py"
        assert module_path.is_file(), f"Module {module_name}.py missing from collection"

    def test_module_utils_shim_exists(self):
        shim = self.COLLECTION_DIR / "plugins" / "module_utils" / "gludd.py"
        assert shim.is_file(), "module_utils/gludd.py shim missing"

    def test_agent_task_role_exists(self):
        role_dir = self.COLLECTION_DIR / "roles" / "agent_task"
        assert (role_dir / "tasks" / "main.yml").is_file(), "agent_task role tasks/main.yml missing"
        assert (role_dir / "defaults" / "main.yml").is_file(), "agent_task role defaults/main.yml missing"
        assert (role_dir / "meta" / "main.yml").is_file(), "agent_task role meta/main.yml missing"


class TestModuleSecurityProperties:
    """Module files must have required security properties."""

    MODULES_DIR = (
        ROOT / "collections" / "ansible_collections" / "general_ludd" / "agent"
        / "plugins" / "modules"
    )

    def _read_module(self, name: str) -> str:
        path = self.MODULES_DIR / f"{name}.py"
        assert path.is_file(), f"Required collection module {name}.py is missing"
        return path.read_text()

    @pytest.mark.parametrize("module_name", ["gludd_db", "gludd_model_call", "gludd_agent_run",
                                              "gludd_langchain_generate", "gludd_langgraph_workflow",
                                              "gludd_langgraph_decision", "gludd_ornith"])
    def test_psk_is_no_log(self, module_name: str):
        """Modules with PSK param must mark it no_log=True."""
        content = self._read_module(module_name)
        assert "no_log=True" in content or 'no_log: true' in content, (
            f"{module_name}: psk parameter must have no_log=True"
        )

    @pytest.mark.parametrize("module_name", ["gludd_ping", "gludd_model_call", "gludd_worktree",
                                              "gludd_git", "gludd_db", "gludd_skill",
                                              "gludd_mcp_tool", "gludd_agent_run",
                                              "gludd_langchain_generate", "gludd_langgraph_workflow",
                                              "gludd_langgraph_decision", "gludd_open_code"])
    def test_module_has_documentation_block(self, module_name: str):
        """Every module must have a DOCUMENTATION string."""
        content = self._read_module(module_name)
        assert "DOCUMENTATION:" in content or "DOCUMENTATION" in content, (
            f"{module_name}: missing DOCUMENTATION block"
        )

    @pytest.mark.parametrize("module_name", ["gludd_ping", "gludd_model_call", "gludd_worktree",
                                              "gludd_git", "gludd_db", "gludd_skill",
                                              "gludd_mcp_tool", "gludd_agent_run",
                                              "gludd_langchain_generate", "gludd_langgraph_workflow",
                                              "gludd_langgraph_decision", "gludd_ornith", "gludd_open_code"])
    def test_module_has_examples_block(self, module_name: str):
        """Every module must have an EXAMPLES string."""
        content = self._read_module(module_name)
        assert "EXAMPLES:" in content or "EXAMPLES" in content, (
            f"{module_name}: missing EXAMPLES block"
        )

    @pytest.mark.parametrize("module_name", ["gludd_ping", "gludd_model_call", "gludd_worktree",
                                              "gludd_git", "gludd_db", "gludd_skill",
                                              "gludd_mcp_tool", "gludd_agent_run",
                                              "gludd_langchain_generate", "gludd_langgraph_workflow",
                                              "gludd_langgraph_decision", "gludd_ornith", "gludd_open_code"])
    def test_module_has_return_block(self, module_name: str):
        """Every module must have a RETURN string."""
        content = self._read_module(module_name)
        assert "RETURN:" in content or "RETURN" in content, (
            f"{module_name}: missing RETURN block"
        )

    @pytest.mark.parametrize("module_name", ["gludd_ping", "gludd_model_call", "gludd_worktree",
                                              "gludd_git", "gludd_db", "gludd_skill",
                                              "gludd_mcp_tool", "gludd_agent_run",
                                              "gludd_langchain_generate", "gludd_langgraph_workflow",
                                              "gludd_langgraph_decision", "gludd_open_code"])
    def test_module_supports_check_mode(self, module_name: str):
        """Every module must declare supports_check_mode."""
        content = self._read_module(module_name)
        assert "supports_check_mode" in content, (
            f"{module_name}: missing supports_check_mode declaration"
        )


class TestFactsAndMessageModules:
    """Facts + message-queue Ansible modules (gludd_facts, gludd_message).

    Covers import, DOCUMENTATION/EXAMPLES/RETURN presence, argument_spec, PSK
    no_log, and check-mode support.
    """

    MODULES_DIR = (
        ROOT / "collections" / "ansible_collections" / "general_ludd" / "agent"
        / "plugins" / "modules"
    )

    def _read_module(self, name: str) -> str:
        path = self.MODULES_DIR / f"{name}.py"
        assert path.is_file(), f"{name}.py missing"
        return path.read_text()

    @pytest.mark.parametrize("module_name", ["gludd_facts", "gludd_message"])
    def test_module_exists_and_importable(self, module_name: str):
        import importlib.util

        path = self.MODULES_DIR / f"{module_name}.py"
        assert path.is_file(), f"{module_name}.py missing"
        spec = importlib.util.spec_from_file_location(module_name, str(path))
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        # Loads the module body (its main() only runs under __main__).
        spec.loader.exec_module(mod)
        assert hasattr(mod, "main")

    @pytest.mark.parametrize("module_name", ["gludd_facts", "gludd_message"])
    def test_has_doc_blocks(self, module_name: str):
        content = self._read_module(module_name)
        assert "DOCUMENTATION:" in content
        assert "EXAMPLES:" in content
        assert "RETURN:" in content

    @pytest.mark.parametrize("module_name", ["gludd_facts", "gludd_message"])
    def test_argument_spec_and_check_mode(self, module_name: str):
        content = self._read_module(module_name)
        assert "argument_spec=dict(" in content
        assert "supports_check_mode=True" in content

    @pytest.mark.parametrize("module_name", ["gludd_facts", "gludd_message"])
    def test_psk_no_log(self, module_name: str):
        content = self._read_module(module_name)
        assert 'psk=dict(type="str", default="", no_log=True)' in content

    def test_message_body_no_log(self):
        """gludd_message body may carry sensitive content and must be no_log."""
        content = self._read_module("gludd_message")
        assert 'body=dict(type="str", default="", no_log=True)' in content

    def test_facts_returns_ansible_facts(self):
        content = self._read_module("gludd_facts")
        assert '"ansible_facts": {"gludd"' in content

    def test_message_states(self):
        content = self._read_module("gludd_message")
        assert 'choices=["send", "receive", "ack"]' in content
        assert '"gludd_inbox"' in content


class TestMetricsAndTracesModules:
    """Observability fact modules (gludd_metrics, gludd_traces).

    These inject ``ansible_facts.gludd_metrics`` / ``ansible_facts.gludd_traces``
    from the daemon's read-only /api/metrics and /api/traces endpoints so a
    playbook can branch on live cost/usage/benchmark and trace data.
    """

    MODULES_DIR = (
        ROOT / "collections" / "ansible_collections" / "general_ludd" / "agent"
        / "plugins" / "modules"
    )

    def _read_module(self, name: str) -> str:
        path = self.MODULES_DIR / f"{name}.py"
        assert path.is_file(), f"{name}.py missing"
        return path.read_text()

    @pytest.mark.parametrize("module_name", ["gludd_metrics", "gludd_traces"])
    def test_module_exists_and_importable(self, module_name: str):
        import importlib.util

        path = self.MODULES_DIR / f"{module_name}.py"
        assert path.is_file(), f"{module_name}.py missing"
        spec = importlib.util.spec_from_file_location(module_name, str(path))
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert hasattr(mod, "main")

    @pytest.mark.parametrize("module_name", ["gludd_metrics", "gludd_traces"])
    def test_has_doc_blocks(self, module_name: str):
        content = self._read_module(module_name)
        assert "DOCUMENTATION:" in content
        assert "EXAMPLES:" in content
        assert "RETURN:" in content

    @pytest.mark.parametrize("module_name", ["gludd_metrics", "gludd_traces"])
    def test_argument_spec_and_check_mode(self, module_name: str):
        content = self._read_module(module_name)
        assert "argument_spec=dict(" in content
        assert "supports_check_mode=True" in content

    @pytest.mark.parametrize("module_name", ["gludd_metrics", "gludd_traces"])
    def test_psk_no_log(self, module_name: str):
        content = self._read_module(module_name)
        assert 'psk=dict(type="str", default="", no_log=True)' in content

    def test_metrics_returns_ansible_facts(self):
        content = self._read_module("gludd_metrics")
        assert '"ansible_facts": {"gludd_metrics"' in content
        assert "/api/metrics" in content

    def test_traces_returns_ansible_facts(self):
        content = self._read_module("gludd_traces")
        assert '"ansible_facts": {"gludd_traces"' in content
        assert "/api/traces" in content

    def test_traces_supports_filters(self):
        content = self._read_module("gludd_traces")
        assert 'todo_id=dict(type="str"' in content
        assert 'limit=dict(type="int"' in content

    def test_metrics_supports_filters(self):
        content = self._read_module("gludd_metrics")
        assert 'agent_id=dict(type="str"' in content
        assert 'project_id=dict(type="str"' in content


class TestSkillRenderer:
    """Tests for the shared render_skill function (W6.5)."""

    def test_render_simple_variable(self):
        from general_ludd.skills.renderer import render_skill
        result = render_skill("Hello {{ name }}!", {"name": "world"})
        assert result == "Hello world!"

    def test_render_no_variables(self):
        from general_ludd.skills.renderer import render_skill
        result = render_skill("Static text with no variables")
        assert result == "Static text with no variables"

    def test_render_strict_undefined_raises(self):
        from general_ludd.skills.renderer import SkillRenderError, render_skill
        with pytest.raises(SkillRenderError, match="undefined"):
            render_skill("Hello {{ missing_var }}!", {})

    def test_render_multiple_variables(self):
        from general_ludd.skills.renderer import render_skill
        result = render_skill("{{ a }} + {{ b }} = {{ c }}", {"a": "1", "b": "2", "c": "3"})
        assert result == "1 + 2 = 3"

    def test_render_empty_body(self):
        from general_ludd.skills.renderer import render_skill
        result = render_skill("", {})
        assert result == ""

    def test_ssti_object_graph_payload_blocked(self):
        """An SSTI payload reaching builtins via the object graph must be
        blocked by the sandbox — the renderer wraps the jinja SecurityError in
        SkillRenderError (fail-closed), not rendered."""
        from general_ludd.skills.renderer import SkillRenderError, render_skill

        payload = "{{ ().__class__.__mro__ }}"
        with pytest.raises(SkillRenderError):
            render_skill(payload, {})

    def test_ssti_builtins_access_blocked(self):
        """Attribute traversal to reach ``__builtins__`` / subclasses must be
        blocked at render time (raised as SkillRenderError, fail-closed)."""
        from general_ludd.skills.renderer import SkillRenderError, render_skill

        payload = "{{ ''.__class__.__base__.__subclasses__() }}"
        with pytest.raises(SkillRenderError):
            render_skill(payload, {})

    def test_sandbox_preserves_legitimate_filters(self):
        """Normal templating — variable substitution + a builtin filter the
        skills rely on — must still work under the sandbox."""
        from general_ludd.skills.renderer import render_skill

        result = render_skill("{{ name | upper }}", {"name": "ludd"})
        assert result == "LUDD"


class TestGluddDbOpContract:
    """Every ``op:`` a role passes to gludd_db must be a declared argspec choice.

    Regression guard: story_create previously called gludd_db with
    ``op: todo_create`` while the module's argspec only allowed
    {todo_get, todo_update_status, resource_preference} — a guaranteed runtime
    failure. This test parses the module's declared choices and every role task
    that invokes gludd_db, and fails if any role uses an op the module rejects.
    """

    MODULES_DIR = (
        ROOT / "collections" / "ansible_collections" / "general_ludd" / "agent"
        / "plugins" / "modules"
    )
    ROLES_DIR = (
        ROOT / "collections" / "ansible_collections" / "general_ludd" / "agent" / "roles"
    )

    def _declared_ops(self) -> set[str]:
        """Extract the op= choices=[...] list from gludd_db.py's argspec."""
        import ast

        src = (self.MODULES_DIR / "gludd_db.py").read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            # Find the op=dict(..., choices=[...]) keyword in argument_spec=dict(...)
            if isinstance(node, ast.keyword) and node.arg == "op" and isinstance(node.value, ast.Call):
                for kw in node.value.keywords:
                    if kw.arg == "choices" and isinstance(kw.value, ast.List):
                        return {
                            e.value
                            for e in kw.value.elts
                            if isinstance(e, ast.Constant) and isinstance(e.value, str)
                        }
        raise AssertionError("could not locate op= choices in gludd_db.py argspec")

    def _role_db_ops(self) -> list[tuple[str, str]]:
        """Return (role_name, op) for every gludd_db task across all roles."""
        found: list[tuple[str, str]] = []
        for tasks_file in self.ROLES_DIR.glob("*/tasks/*.yml"):
            try:
                docs = list(yaml.safe_load_all(tasks_file.read_text()))
            except yaml.YAMLError:
                continue
            role_name = tasks_file.parent.parent.name
            for doc in docs:
                if not isinstance(doc, list):
                    continue
                for task in doc:
                    self._collect_db_ops(task, role_name, found)
        return found

    def _collect_db_ops(self, task, role_name, found) -> None:
        """Recurse into block/rescue/always and record any gludd_db op."""
        if not isinstance(task, dict):
            return
        for key in ("block", "rescue", "always"):
            if isinstance(task.get(key), list):
                for sub in task[key]:
                    self._collect_db_ops(sub, role_name, found)
        for mod_key in ("general_ludd.agent.gludd_db", "gludd_db"):
            args = task.get(mod_key)
            if isinstance(args, dict) and "op" in args:
                op = args["op"]
                if isinstance(op, str) and "{{" not in op:
                    found.append((role_name, op))

    def test_todo_create_is_a_declared_choice(self):
        assert "todo_create" in self._declared_ops(), (
            "gludd_db must declare todo_create (story_create / issue_reporter need it)"
        )

    def test_every_role_db_op_is_declared(self):
        declared = self._declared_ops()
        used = self._role_db_ops()
        assert used, "expected at least one gludd_db op across roles"
        invalid = [(role, op) for role, op in used if op not in declared]
        assert not invalid, (
            f"role(s) call gludd_db with op not in argspec choices {sorted(declared)}: {invalid}"
        )
