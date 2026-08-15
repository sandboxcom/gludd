"""Deep agent configuration validation.
Validates opencode agent configs (user-level ~/.config/opencode/opencode.json
and project-level opencode.json) for structural correctness, tool permissions,
model configs, plugin references, and skill bindings.
"""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent
USER_CONFIG = Path.home() / ".config" / "opencode" / "opencode.json"
PROJECT_CONFIG = ROOT / "opencode.json"

pytestmark = pytest.mark.skipif(
    not USER_CONFIG.exists(),
    reason=(
        "user opencode config not present (CI runners have no user-level "
        f"opencode.json); deep user-config validation requires {USER_CONFIG}"
    ),
)
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
PLUGINS_DIR = ROOT / ".opencode" / "plugins"
SKILLS_DIR = ROOT / ".opencode" / "skills"

VALID_TOOLS = {"bash", "read", "write", "edit", "grep", "glob", "webfetch", "task", "skill", "todowrite"}
REQUIRED_AGENTS = {"build", "plan", "review", "explore", "general"}
ALLOWED_PATH_PREFIXES = frozenset(
    {
        "/Users/shawnwilson/gludd/**",
        "/tmp/**",
        "/private/tmp/**",
        "/private/var/folders/**",
        "/Users/shawnwilson/.config/opencode/**",
        "/Users/shawnwilson/.local/share/opencode/**",
        "/Users/shawnwilson/.cache/**",
    }
)


def _load_user_config():
    return json.loads(USER_CONFIG.read_text())


def _load_project_config():
    return json.loads(PROJECT_CONFIG.read_text())


# ── User-level agent config ──────────────────────────────────────────


class TestUserConfigExists:
    def test_user_config_file_exists(self):
        assert USER_CONFIG.exists(), f"Missing: {USER_CONFIG}"

    def test_user_config_is_valid_json(self):
        cfg = _load_user_config()
        assert isinstance(cfg, dict)
        assert "agent" in cfg

    def test_user_config_has_schema(self):
        cfg = _load_user_config()
        assert "$schema" in cfg
        assert cfg["$schema"] == "https://opencode.ai/config.json"


class TestToolsDeclaration:
    def test_top_level_tools_declared(self):
        cfg = _load_user_config()
        assert "tools" in cfg
        tools = cfg["tools"]
        for t in VALID_TOOLS:
            assert t in tools, f"tool '{t}' missing from top-level tools"
            assert isinstance(tools[t], bool), f"tool '{t}' must be boolean"

    def test_all_tools_are_known(self):
        cfg = _load_user_config()
        for t in cfg["tools"]:
            assert t in VALID_TOOLS, f"unknown tool '{t}' in top-level tools"


class TestAgentDefinitions:
    def test_required_agents_present(self):
        cfg = _load_user_config()
        agents = cfg["agent"]
        for name in REQUIRED_AGENTS:
            assert name in agents, f"agent '{name}' missing"

    def test_build_agent_has_model_and_tools(self):
        cfg = _load_user_config()
        build = cfg["agent"]["build"]
        assert "model" in build
        assert "tools" in build
        assert build["model"] == "opencode-go/deepseek-v4-pro"

    def test_plan_agent_has_model(self):
        cfg = _load_user_config()
        plan = cfg["agent"]["plan"]
        assert "model" in plan
        assert plan["model"] == "zai/glm-4.5"

    def test_review_agent_has_model(self):
        cfg = _load_user_config()
        review = cfg["agent"]["review"]
        assert "model" in review
        assert review["model"] == "opencode-go/qwen3.6-plus"

    def test_explore_agent_has_steps(self):
        cfg = _load_user_config()
        explore = cfg["agent"]["explore"]
        assert "steps" in explore
        assert isinstance(explore["steps"], int)
        assert explore["steps"] > 0

    def test_general_agent_has_steps(self):
        cfg = _load_user_config()
        general = cfg["agent"]["general"]
        assert "steps" in general
        assert isinstance(general["steps"], int)
        assert general["steps"] > 0


class TestAgentToolPermissions:
    def test_build_agent_tools_match_valid_set(self):
        cfg = _load_user_config()
        tools = cfg["agent"]["build"]["tools"]
        for t in tools:
            assert t in VALID_TOOLS, f"build agent has unknown tool '{t}'"
            assert isinstance(tools[t], bool)

    def test_explore_agent_tools_complete(self):
        cfg = _load_user_config()
        tools = cfg["agent"]["explore"]["tools"]
        for t in VALID_TOOLS:
            assert t in tools, f"explore agent missing tool '{t}'"

    def test_general_agent_tools_complete(self):
        cfg = _load_user_config()
        tools = cfg["agent"]["general"]["tools"]
        for t in VALID_TOOLS:
            assert t in tools, f"general agent missing tool '{t}'"


class TestModelConfigs:
    def test_default_agent_points_to_configured_agent(self):
        cfg = _load_user_config()
        assert "default_agent" in cfg
        assert cfg["default_agent"] in cfg["agent"], f"default_agent '{cfg['default_agent']}' not in agent definitions"

    def test_top_level_model_is_set(self):
        cfg = _load_user_config()
        assert "model" in cfg
        assert isinstance(cfg["model"], str)
        assert len(cfg["model"]) > 0

    def test_small_model_is_set(self):
        cfg = _load_user_config()
        assert "small_model" in cfg
        assert isinstance(cfg["small_model"], str)
        assert len(cfg["small_model"]) > 0

    def test_providers_declared(self):
        cfg = _load_user_config()
        assert "provider" in cfg
        providers = cfg["provider"]
        assert "opencode-go" in providers, "opencode-go provider missing"
        assert "zai" in providers, "zai provider missing"

    def test_agent_models_use_valid_providers(self):
        cfg = _load_user_config()
        known_providers = set(cfg.get("provider", {}).keys())
        for name, agent in cfg["agent"].items():
            if "model" not in agent:
                continue
            model = agent["model"]
            provider = model.split("/")[0] if "/" in model else model
            assert provider in known_providers or provider in {"opencode-go", "zai"}, (
                f"agent '{name}' model '{model}' uses unknown provider '{provider}'"
            )


class TestProviderConfig:
    def test_zai_provider_has_base_url(self):
        cfg = _load_user_config()
        zai = cfg["provider"]["zai"]
        assert "options" in zai
        assert "baseURL" in zai["options"]
        assert zai["options"]["baseURL"].startswith("https://")


# ── Project-level config ─────────────────────────────────────────────


class TestProjectConfigExists:
    def test_project_config_file_exists(self):
        assert PROJECT_CONFIG.exists(), f"Missing: {PROJECT_CONFIG}"

    def test_project_config_is_valid_json(self):
        cfg = _load_project_config()
        assert isinstance(cfg, dict)


class TestProjectPermissionRules:
    def test_bash_allows_only_make(self):
        cfg = _load_project_config()
        bash_perm = cfg["permission"]["bash"]
        assert bash_perm["*"] == "deny"
        assert "make *" in bash_perm

    def test_global_file_tools_use_supported_permission_schema(self):
        cfg = _load_project_config()
        permission = cfg["permission"]

        assert permission["edit"] == "allow"
        assert permission["glob"] == "allow"
        assert permission["grep"] == "allow"
        assert "write" not in permission, "OpenCode's global edit permission covers file writes"

    def test_read_permission_preserves_fail_closed_env_rules(self):
        cfg = _load_project_config()
        read_permission = cfg["permission"]["read"]

        assert list(read_permission) == ["*", "*.env", "*.env.*", "*.env.example"]
        assert read_permission == {
            "*": "allow",
            "*.env": "deny",
            "*.env.*": "deny",
            "*.env.example": "allow",
        }

    def test_external_directory_has_no_gludd(self):
        cfg = _load_project_config()
        ed = cfg["permission"]["external_directory"]
        assert "/Users/shawnwilson/gludd/**" not in ed, "external_directory must not include gludd/**"

    def test_doom_loop_is_denied(self):
        cfg = _load_project_config()
        assert cfg["permission"]["doom_loop"] == "deny"


class TestPluginRegistration:
    def test_plugin_paths_exist(self):
        cfg = _load_project_config()
        for rel_path in cfg.get("plugin", []):
            abs_path = (ROOT / rel_path).resolve()
            assert abs_path.exists(), f"plugin not found: {abs_path}"

    def test_plugins_are_typescript(self):
        cfg = _load_project_config()
        for rel_path in cfg.get("plugin", []):
            assert rel_path.endswith(".ts"), f"non-TS plugin: {rel_path}"

    def test_plugin_count_exceeds_25(self):
        cfg = _load_project_config()
        assert len(cfg.get("plugin", [])) > 25, f"expected >25 plugins, got {len(cfg.get('plugin', []))}"


class TestCompactionSettings:
    def test_compaction_prune_enabled(self):
        cfg = _load_project_config()
        assert cfg.get("compaction", {}).get("prune") is True

    def test_compaction_reserved_reasonable(self):
        cfg = _load_project_config()
        reserved = cfg.get("compaction", {}).get("reserved", 0)
        assert reserved > 0, "compaction.reserved must be positive"


# ── Skill bindings ───────────────────────────────────────────────────


class TestSkillBindings:
    def test_skill_dirs_exist_on_disk(self):
        decl_dirs = {d.name for d in SKILLS_DIR.iterdir() if d.is_dir()}
        # Known active skill directories (not deprecated/empty)
        active = {d for d in decl_dirs if not d.startswith(".")}
        assert len(active) >= 15, f"expected >=15 skill dirs, got {len(active)}"

    def test_each_skill_dir_has_skill_md(self):
        for d in sorted(SKILLS_DIR.iterdir()):
            if not d.is_dir():
                continue
            if d.name in ("skills", "opencode-customize"):  # meta or built-in
                continue
            skill_md = d / "SKILL.md"
            assert skill_md.exists(), f"missing SKILL.md in {d.name}"

    def test_skill_dirs_have_content(self):
        for d in sorted(SKILLS_DIR.iterdir()):
            if not d.is_dir():
                continue
            if d.name in ("skills", "opencode-customize"):
                continue
            skill_md = d / "SKILL.md"
            if not skill_md.exists():
                continue
            content = skill_md.read_text()
            assert len(content) > 50, f"SKILL.md in {d.name} is too short ({len(content)} chars)"


# ── Cross-config consistency ─────────────────────────────────────────


class TestCrossConfigConsistency:
    def test_agent_tools_subset_of_top_level_tools(self):
        cfg = _load_user_config()
        top_tools = set(cfg.get("tools", {}).keys())
        for name, agent in cfg["agent"].items():
            agent_tools = set(agent.get("tools", {}).keys())
            extra = agent_tools - top_tools
            assert not extra, f"agent '{name}' has tools not in top-level: {extra}"

    def test_instructions_file_exists(self):
        cfg = _load_user_config()
        for instr in cfg.get("instructions", []):
            p = Path.home() / ".config" / "opencode" / instr
            assert p.exists(), f"instruction file missing: {p}"

    def test_no_unknown_top_level_keys_in_project(self):
        cfg = _load_project_config()
        known = {"$schema", "permission", "compaction", "formatter", "lsp", "snapshot", "plugin"}
        unknown = set(cfg.keys()) - known
        assert not unknown, f"unknown top-level keys in opencode.json: {unknown}"
