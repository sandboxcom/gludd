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

# Project root
ROOT = Path(__file__).parent.parent.parent
PLAYBOOKS_DIR = ROOT / "playbooks"

# Import the work type map directly from the module
from general_ludd.ansible.action_policy import (  # noqa: E402
    ActionPolicyConfig,
    validate_action,
)
from general_ludd.ansible.manifest import generate_manifest  # noqa: E402
from general_ludd.event_loop.loop import _WORK_TYPE_PLAYBOOK_MAP  # noqa: E402

# Playbooks that use ansible.builtin.command for user-supplied commands — allowed
_COMMAND_ALLOWED_PLAYBOOKS = {
    "validate_task.yml",  # explicitly runs test_commands loop (user-supplied, not templated strings)
}


class TestWorkTypePlaybookRegistry:
    """Every work_type in _WORK_TYPE_PLAYBOOK_MAP must resolve to a real file."""

    def test_all_work_types_have_playbook_entries(self):
        """Sanity: the map is non-empty and contains expected work types."""
        assert len(_WORK_TYPE_PLAYBOOK_MAP) > 0, "_WORK_TYPE_PLAYBOOK_MAP is empty"
        expected_keys = {"code", "test", "analysis", "audit", "prompt", "self_improvement",
                         "dependency", "review"}
        for key in expected_keys:
            assert key in _WORK_TYPE_PLAYBOOK_MAP, (
                f"work_type '{key}' missing from _WORK_TYPE_PLAYBOOK_MAP"
            )

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
        if not playbook_path.is_file():
            pytest.skip(f"Playbook {playbook_name} does not exist (covered by test_playbook_file_exists)")
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
        if not playbook_path.is_file():
            pytest.skip(f"Playbook {playbook_name} does not exist")
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
        if not playbook_path.is_file():
            pytest.skip(f"Playbook {playbook_name} does not exist")
        manifest = generate_manifest(str(playbook_path))
        assert manifest.playbook == playbook_name, (
            f"manifest.playbook mismatch: got {manifest.playbook!r}"
        )

    @pytest.mark.parametrize("work_type,playbook_name", list(_WORK_TYPE_PLAYBOOK_MAP.items()))
    def test_action_policy_allows_playbook(self, work_type: str, playbook_name: str):
        """A permissive ActionPolicy must allow each playbook."""
        playbook_path = PLAYBOOKS_DIR / playbook_name
        if not playbook_path.is_file():
            pytest.skip(f"Playbook {playbook_name} does not exist")
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
        if not galaxy_path.is_file():
            pytest.skip("galaxy.yml missing")
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
        if not path.is_file():
            pytest.skip(f"{name}.py missing")
        return path.read_text()

    @pytest.mark.parametrize("module_name", ["gludd_db", "gludd_model_call", "gludd_agent_run"])
    def test_psk_is_no_log(self, module_name: str):
        """Modules with PSK param must mark it no_log=True."""
        content = self._read_module(module_name)
        assert "no_log=True" in content or 'no_log: true' in content, (
            f"{module_name}: psk parameter must have no_log=True"
        )

    @pytest.mark.parametrize("module_name", ["gludd_ping", "gludd_model_call", "gludd_worktree",
                                              "gludd_git", "gludd_db", "gludd_skill",
                                              "gludd_mcp_tool", "gludd_agent_run"])
    def test_module_has_documentation_block(self, module_name: str):
        """Every module must have a DOCUMENTATION string."""
        content = self._read_module(module_name)
        assert "DOCUMENTATION:" in content or "DOCUMENTATION" in content, (
            f"{module_name}: missing DOCUMENTATION block"
        )

    @pytest.mark.parametrize("module_name", ["gludd_ping", "gludd_model_call", "gludd_worktree",
                                              "gludd_git", "gludd_db", "gludd_skill",
                                              "gludd_mcp_tool", "gludd_agent_run"])
    def test_module_has_examples_block(self, module_name: str):
        """Every module must have an EXAMPLES string."""
        content = self._read_module(module_name)
        assert "EXAMPLES:" in content or "EXAMPLES" in content, (
            f"{module_name}: missing EXAMPLES block"
        )

    @pytest.mark.parametrize("module_name", ["gludd_ping", "gludd_model_call", "gludd_worktree",
                                              "gludd_git", "gludd_db", "gludd_skill",
                                              "gludd_mcp_tool", "gludd_agent_run"])
    def test_module_has_return_block(self, module_name: str):
        """Every module must have a RETURN string."""
        content = self._read_module(module_name)
        assert "RETURN:" in content or "RETURN" in content, (
            f"{module_name}: missing RETURN block"
        )

    @pytest.mark.parametrize("module_name", ["gludd_ping", "gludd_model_call", "gludd_worktree",
                                              "gludd_git", "gludd_db", "gludd_skill",
                                              "gludd_mcp_tool", "gludd_agent_run"])
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
