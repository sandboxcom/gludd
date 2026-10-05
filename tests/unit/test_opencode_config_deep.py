"""Deep structural validation of opencode.json configuration.

Checks permission rules completeness, plugin registrations validity,
skill path existence, agent definitions, and subagent config correctness.
"""

import json
from pathlib import Path
from typing import Any, ClassVar, cast

ROOT = Path(__file__).parent.parent.parent
OPENCODE_JSON = ROOT / "opencode.json"
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
PLUGINS_DIR = ROOT / ".opencode" / "plugins"
SKILLS_DIR = ROOT / ".opencode" / "skills"

REQUIRED_PERMISSION_TOOLS = {"read", "edit", "glob", "grep", "bash", "external_directory"}
ALLOWED_EXTERNAL_PATH_PREFIXES = frozenset(
    {
        "/tmp/**",
        "/private/tmp/**",
        "/private/var/folders/**",
        "/Users/shawnwilson/.config/opencode/**",
        "/Users/shawnwilson/.local/share/opencode/**",
        "/Users/shawnwilson/.cache/**",
    }
)
SKILL_DIRS = {
    "ai-ml-expert",
    "azure-expert",
    "background-test-runner",
    "chemistry-expert",
    "culinary-expert",
    "electronics-expert",
    "enforce-bootstrap",
    "git-release-captain",
    "go-expert",
    "guardrail-pattern",
    "java-expert",
    "materials-engineer",
    "opencode-customize",
    "python-expert",
    "revealjs-presentation",
    "skills",
    "test-quality",
    "travel-agent",
    "type-safety",
}
SKILL_DIRS_REQUIRE_SKILL_MD = SKILL_DIRS - {
    "skills",  # meta-skill directory (skill fragments, no SKILL.md)
    "opencode-customize",  # built-in skill with different structure
}


def _load() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(OPENCODE_JSON.read_text()))


class TestPermissionRules:
    def test_all_permission_tools_present(self) -> None:
        """Every required permission tool must have a rule block."""
        cfg = _load()
        perm = cfg["permission"]
        for tool in REQUIRED_PERMISSION_TOOLS:
            assert tool in perm, f"Missing permission block for tool: {tool}"

    def test_permission_catchalls_match_the_supported_schema(self) -> None:
        """Only external and command boundaries deny their catch-all.

        The workspace is implicit in OpenCode's file-tool schema.  Reads are
        allowed there except for secret files, while writes route through the
        single ``edit`` capability.  External paths remain fail-closed in the
        shared ``external_directory`` boundary.
        """
        cfg = _load()
        permission = cfg["permission"]

        assert permission["read"] == {
            "*": "allow",
            "*.env": "deny",
            "*.env.*": "deny",
            "*.env.example": "allow",
        }
        for tool in ("edit", "glob", "grep"):
            assert permission[tool] == "allow"
        assert "write" not in permission, "OpenCode routes writes through edit"
        for tool in ("bash", "external_directory"):
            rules = permission[tool]
            first_key = next(iter(rules))
            assert first_key == "*", f"{tool}: first rule key must be '*', got '{first_key}'"
            assert rules[first_key] == "deny", f"{tool}: first rule must be 'deny', got '{rules[first_key]}'"

    def test_file_tools_do_not_duplicate_external_path_rules(self) -> None:
        """File tools must share the one external-directory allowlist."""
        cfg = _load()
        permission = cfg["permission"]
        for tool in ("read", "edit", "glob", "grep"):
            value = permission[tool]
            if isinstance(value, dict):
                assert not any(rule.startswith("/") for rule in value), (
                    f"{tool}: external paths belong only in external_directory"
                )
            else:
                assert value == "allow"
        allowed = {
            key
            for key, action in permission["external_directory"].items()
            if action == "allow"
        }
        assert allowed == ALLOWED_EXTERNAL_PATH_PREFIXES

    def test_bash_only_allows_make(self) -> None:
        """bash permission must only allow 'make *' patterns."""
        cfg = _load()
        bash_rules = cfg["permission"]["bash"]
        assert "*" in bash_rules and bash_rules["*"] == "deny", "bash must deny '*' first"
        allowed = {k for k, v in bash_rules.items() if v == "allow"}
        assert allowed == {"make *"}, f"bash must only allow 'make *', got: {sorted(allowed)}"

    def test_external_directory_allows_system_paths(self) -> None:
        """external_directory must allow /tmp, /private/..., .config, .local, .cache."""
        cfg = _load()
        rules = cfg["permission"]["external_directory"]
        assert rules.get("*") == "deny", "external_directory must deny '*' first"
        allowed = {k for k, v in rules.items() if v == "allow"}
        assert "/tmp/**" in allowed, "external_directory must allow /tmp/**"
        assert "/private/tmp/**" in allowed, "external_directory must allow /private/tmp/**"
        assert "/Users/shawnwilson/.config/opencode/**" in allowed
        assert "/Users/shawnwilson/.local/share/opencode/**" in allowed
        assert "/Users/shawnwilson/.cache/**" in allowed

    def test_doom_loop_is_denied(self) -> None:
        """Doom loop execution must be explicitly denied."""
        cfg = _load()
        assert cfg["permission"].get("doom_loop") == "deny", "permission.doom_loop must be 'deny'"

    def test_no_unknown_permission_tools(self) -> None:
        """No unknown/surprise permission tool blocks should exist."""
        cfg = _load()
        allowed = REQUIRED_PERMISSION_TOOLS | {"doom_loop"}
        for key in cfg["permission"]:
            assert key in allowed, f"Unknown permission key: '{key}'. Allowed: {sorted(allowed)}"


class TestPluginRegistrations:
    def test_plugin_array_exists(self) -> None:
        cfg = _load()
        assert "plugin" in cfg, "plugin array must exist"
        assert isinstance(cfg["plugin"], list), "plugin must be an array"
        assert len(cfg["plugin"]) > 0, "plugin array must not be empty"

    def test_every_registered_plugin_file_exists(self) -> None:
        """Every plugin path in the array must resolve to an existing file."""
        cfg = _load()
        missing = []
        for rel in cfg["plugin"]:
            abs_path = ROOT / rel.removeprefix("./")
            if not abs_path.is_file():
                missing.append(rel)
        assert not missing, "Registered plugin files not found on disk:\n  " + "\n  ".join(missing)

    def test_no_duplicate_plugin_registrations(self) -> None:
        """No plugin should be registered more than once."""
        cfg = _load()
        seen = {}
        dupes = []
        for rel in cfg["plugin"]:
            if rel in seen:
                dupes.append(rel)
            seen[rel] = True
        assert not dupes, f"Duplicate plugin registrations found: {dupes}"

    def test_all_plugins_are_typescript(self) -> None:
        """Every registered plugin path must end in .ts."""
        cfg = _load()
        non_ts = [p for p in cfg["plugin"] if not p.endswith(".ts")]
        assert not non_ts, f"Non-TypeScript plugins: {non_ts}"

    def test_plugins_are_under_opencode_dir(self) -> None:
        """Plugin paths must be under .opencode/plugin/ or .opencode/plugins/."""
        cfg = _load()
        bad = []
        for p in cfg["plugin"]:
            clean = p.removeprefix("./")
            if not (clean.startswith(".opencode/plugin/") or clean.startswith(".opencode/plugins/")):
                bad.append(p)
        assert not bad, f"Plugins outside .opencode/: {bad}"

    def test_plugin_count_matches_disk(self) -> None:
        """The number of registered .ts plugins should match the files on disk."""
        cfg = _load()
        registered = set(cfg["plugin"])
        on_disk = set()
        for d in (PLUGIN_DIR, PLUGINS_DIR):
            if d.is_dir():
                for f in d.iterdir():
                    if f.suffix == ".ts" and f.is_file():
                        on_disk.add(f"./{f.relative_to(ROOT)}")
        orphan_on_disk = on_disk - registered
        orphan_registered = registered - on_disk
        assert not orphan_on_disk, ".ts files on disk not registered in opencode.json:\n  " + "\n  ".join(
            sorted(orphan_on_disk)
        )
        assert not orphan_registered, "Registered plugin paths not found on disk:\n  " + "\n  ".join(
            sorted(orphan_registered)
        )

    def test_watchdog_plugin_registered(self) -> None:
        """watchdog.ts must be registered (it originates from plugins/, not plugin/)."""
        cfg = _load()
        watchdog_paths = [p for p in cfg["plugin"] if "watchdog" in p]
        assert len(watchdog_paths) > 0, "watchdog plugin not registered"


class TestSkills:
    def test_registered_skills_have_skill_md(self) -> None:
        """Every skill directory (except known exceptions) must contain a SKILL.md file."""
        if not SKILLS_DIR.is_dir():
            return
        missing = []
        for skill_dir in SKILL_DIRS_REQUIRE_SKILL_MD:
            skill_path = SKILLS_DIR / skill_dir
            if not skill_path.is_dir():
                missing.append(f"{skill_dir}/ (directory missing)")
                continue
            skill_md = skill_path / "SKILL.md"
            if not skill_md.is_file():
                missing.append(f"{skill_dir}/SKILL.md (file missing)")
        assert not missing, "Skill directories missing SKILL.md:\n  " + "\n  ".join(missing)

    def test_skill_dirs_exist_on_disk(self) -> None:
        """Every listed skill directory must exist on disk."""
        if not SKILLS_DIR.is_dir():
            return
        missing = []
        for s in SKILL_DIRS:
            if not (SKILLS_DIR / s).is_dir():
                missing.append(s)
        assert not missing, f"Skill directories not found: {missing}"

    def test_no_orphan_skill_dirs(self) -> None:
        """No unexpected directories in .opencode/skills/."""
        if not SKILLS_DIR.is_dir():
            return
        on_disk = {d.name for d in SKILLS_DIR.iterdir() if d.is_dir()}
        orphans = on_disk - SKILL_DIRS
        assert not orphans, f"Skill directories on disk but not in test registry: {sorted(orphans)}"


class TestTopLevelConfig:
    def test_compaction_config_valid(self) -> None:
        """Compaction settings must be present and well-formed."""
        cfg = _load()
        comp = cfg.get("compaction", {})
        assert isinstance(comp.get("prune"), bool), "compaction.prune must be bool"
        assert isinstance(comp.get("reserved"), (int, float)), "compaction.reserved must be a number"
        assert comp["reserved"] > 0, "compaction.reserved must be positive"

    def test_formatter_enabled(self) -> None:
        """formatter must be enabled (boolean true)."""
        cfg = _load()
        assert cfg.get("formatter") is True, "formatter must be true"

    def test_lsp_enabled(self) -> None:
        """LSP must be enabled (boolean true)."""
        cfg = _load()
        assert cfg.get("lsp") is True, "lsp must be true"

    def test_snapshot_disabled(self) -> None:
        """snapshot should be false (opt-in only)."""
        cfg = _load()
        assert cfg.get("snapshot") is False, "snapshot must be false"

    def test_schema_url_valid(self) -> None:
        """$schema must point to the official opencode config schema."""
        cfg = _load()
        assert cfg.get("$schema") == "https://opencode.ai/config.json", (
            f"$schema must be https://opencode.ai/config.json, got {cfg.get('$schema')}"
        )

    def test_no_unknown_top_level_keys(self) -> None:
        """No top-level keys outside the known schema (extended from test_opencode_json_schema.py)."""
        known = {
            "$schema",
            "permission",
            "plugin",
            "compaction",
            "formatter",
            "lsp",
            "snapshot",
            # Other schema-allowed keys not currently used:
            "shell",
            "logLevel",
            "server",
            "command",
            "skills",
            "references",
            "reference",
            "watcher",
            "share",
            "autoshare",
            "autoupdate",
            "disabled_providers",
            "enabled_providers",
            "model",
            "small_model",
            "default_agent",
            "username",
            "mode",
            "agent",
            "provider",
            "mcp",
            "instructions",
            "layout",
            "tools",
            "attachment",
            "enterprise",
            "tool_output",
            "experimental",
        }
        cfg = _load()
        unknown = set(cfg.keys()) - known
        assert not unknown, f"Unknown top-level keys: {sorted(unknown)}"


class TestEnforcementPlugins:
    """Verify critical enforcement plugins are registered."""

    CRITICAL_PLUGINS: ClassVar = [
        "enforce-stop.ts",
        "enforce-make.ts",
        "enforce-floor.ts",
        "enforce-delegate.ts",
        "enforce-multitask.ts",
        "enforce-session-start.ts",
        "enforce-clean-tree.ts",
        "enforce-tdd.ts",
        "enforce-no-suppressions.ts",
        "enforce-verified-claims.ts",
        "enforce-deletion-gate.ts",
        "enforce-batch-push.ts",
        "enforce-no-wait.ts",
        "enforce-enhancement-ratio.ts",
        "enforce-branch-discipline.ts",
        "enforce-worktree.ts",
        "enforce-deadline.ts",
        "enforce-commit-lock.ts",
        "enforce-depth.ts",
        "enforce-context.ts",
        "enforce-objective.ts",
        "enforce-test-integrity.ts",
        "enforce-task-tracking.ts",
        "enforce-anti-essay.ts",
        "enforce-audit.ts",
        "enforce-directives.ts",
        "enforce-deliverable.ts",
        "enforce-no-ci-poll.ts",
        "enforce-release-deadline.ts",
        "enforce-additive-task.ts",
        "enforce-floor-v2.ts",
        "watchdog.ts",
    ]

    def test_all_critical_plugins_registered(self) -> None:
        cfg = _load()
        registered = {Path(p).name for p in cfg["plugin"]}
        missing = [p for p in self.CRITICAL_PLUGINS if p not in registered]
        assert not missing, "Critical enforcement plugins not registered:\n  " + "\n  ".join(missing)
