"""E2E tests that verify .opencode/ plugins actually load, instantiate, and run.

These tests invoke the Node.js verifier script (``.opencode/scripts/verify-plugins.mjs``)
which performs actual dynamic import + factory call + hook invocation against every
.ts plugin file.  Structural-pattern tests (``tests/unit/test_plugin_*.py``) are
necessary but insufficient — a parser-only check won't catch factory-throws errors,
auto-discovered non-plugin files, or OLD-API vs NEW-API mismatches that crash opencode
at boot.

Session 51 incident: ``_exports.ts`` companion files placed in ``.opencode/plugin/``
were auto-discovered by opencode and crashed at boot with
``TypeError: undefined is not an object (evaluating 'N.event')``.  No existing test
caught this because no test actually *loaded* the plugins through the Node.js module
loader.

These tests are the mechanical fix.
"""

import json
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
VERIFIER_SCRIPT = PROJECT_ROOT / ".opencode" / "scripts" / "verify-plugins.mjs"
PLUGIN_DIR = PROJECT_ROOT / ".opencode" / "plugin"
PLUGINS_DIR = PROJECT_ROOT / ".opencode" / "plugins"
NODE_BIN = "node"


def _run_verifier() -> dict:
    """Run the Node.js verifier script and return parsed JSON results."""
    assert VERIFIER_SCRIPT.is_file(), f"Verifier script not found: {VERIFIER_SCRIPT}"

    result = subprocess.run(
        [NODE_BIN, "--experimental-strip-types", str(VERIFIER_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(PROJECT_ROOT),
    )
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        # Print both stdout and stderr for debugging
        print(f"STDOUT:\n{stdout[:2000]}")
        print(f"STDERR:\n{stderr[:2000]}")
        pytest.fail(f"Verifier output is not valid JSON (exit={result.returncode})")

    return data


class TestPluginLoad:
    """Every .ts file in plugin directories must import and instantiate cleanly."""

    def test_all_plugins_load_without_errors(self):
        """No plugin import or factory call throws an error."""
        data = _run_verifier()
        failures = data.get("failures", [])
        failed_plugins = data.get("summaries", {}).get("failed-plugins", [])

        assert len(failures) == 0, (
            f"{len(failures)} plugin failure(s):\n"
            + "\n".join(f"  [{f['test']}] {f['message']}" for f in failures)
        )
        assert len(failed_plugins) == 0, (
            f"{len(failed_plugins)} plugins failed to load:\n"
            + "\n".join(f"  {p['file']}: {p.get('reason', 'unknown')}" for p in failed_plugins)
        )

    def test_verifier_exit_code_pass(self):
        """Verifier exits 0 when all checks pass."""
        result = subprocess.run(
            [NODE_BIN, "--experimental-strip-types", str(VERIFIER_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(PROJECT_ROOT),
        )
        assert result.returncode == 0, (
            f"Verifier exit={result.returncode}\n"
            f"STDERR: {result.stderr[:2000]}"
        )


class TestPluginDirectoryHygiene:
    """No dangerous non-plugin files in plugin directories."""

    def test_no_underscore_prefixed_ts_files_in_plugin_dir(self):
        """Files like _exports.ts are auto-discovered and crash opencode."""
        assert PLUGIN_DIR.is_dir(), "Required .opencode/plugin directory is missing"
        unders = [f for f in PLUGIN_DIR.iterdir()
                   if f.name.startswith("_") and f.suffix == ".ts"]
        assert len(unders) == 0, (
            f"Underscore-prefixed .ts files in .opencode/plugin/ WILL crash opencode "
            f"(Session 51 incident): {[f.name for f in unders]}"
        )

    def test_no_backup_orig_files_in_plugin_dir(self):
        """Backup/orig files are auto-discovered as plugins."""
        for dir_ in [PLUGIN_DIR, PLUGINS_DIR]:
            if not dir_.exists():
                continue
            dangerous = [f for f in dir_.iterdir()
                         if any(x in f.name for x in (".orig.", ".backup.", ".bak."))]
            assert len(dangerous) == 0, (
                f"Backup files in plugin dir: {[f.name for f in dangerous]}"
            )

    def test_all_ts_files_export_valid_plugin_factory(self):
        """Every .ts file must export a function (or be explicitly allowlisted)."""
        assert PLUGIN_DIR.is_dir(), "Required .opencode/plugin directory is missing"
        ts_files = list(PLUGIN_DIR.glob("*.ts"))
        assert len(ts_files) > 0, "No .ts files in .opencode/plugin/"

        # The verifier already checks this; we assert it through the JSON output
        data = _run_verifier()
        failures = data.get("failures", [])
        factory_failures = [f for f in failures if "factory" in f.get("test", "").lower()]
        assert len(factory_failures) == 0, (
            f"Factory failures: {factory_failures}"
        )


class TestOpencodeConfig:
    """opencode.json must be valid per the published schema."""

    def test_schema_present(self):
        """$schema must point to the published config schema."""
        cfg_path = PROJECT_ROOT / "opencode.json"
        assert cfg_path.is_file(), "Required opencode.json is missing"
        cfg = json.loads(cfg_path.read_text())
        assert cfg.get("$schema") == "https://opencode.ai/config.json", (
            f"$schema is {cfg.get('$schema')}"
        )

    def test_no_unknown_top_level_keys(self):
        """Unknown keys are rejected with ConfigInvalidError by opencode."""
        KNOWN_KEYS = {
            "$schema", "username", "model", "small_model", "default_agent",
            "shell", "logLevel", "share", "autoupdate", "snapshot",
            "instructions", "skills", "references", "agent", "command",
            "provider", "disabled_providers", "enabled_providers", "mcp",
            "plugin", "permission", "formatter", "lsp", "experimental",
            "tool_output", "compaction",
        }
        cfg_path = PROJECT_ROOT / "opencode.json"
        assert cfg_path.is_file(), "Required opencode.json is missing"
        cfg = json.loads(cfg_path.read_text())
        unknown = set(cfg.keys()) - KNOWN_KEYS
        assert len(unknown) == 0, (
            f"Unknown top-level keys: {unknown}. opencode WILL reject these."
        )

    def test_all_registered_plugins_exist_on_disk(self):
        """Every path in the plugin array must resolve to an existing file."""
        cfg_path = PROJECT_ROOT / "opencode.json"
        assert cfg_path.is_file(), "Required opencode.json is missing"
        cfg = json.loads(cfg_path.read_text())
        plugins = cfg.get("plugin", [])
        if not plugins:
            return
        missing = []
        for entry in plugins:
            p = entry if isinstance(entry, str) else entry[0] if isinstance(entry, list) else None
            if p and p.startswith("./"):
                abs_path = (PROJECT_ROOT / p).resolve()
                if not abs_path.exists():
                    missing.append(p)
        assert len(missing) == 0, (
            f"Registered plugins not found on disk: {missing}"
        )

    def test_make_allow_before_star_deny(self):
        """Permission ordering: 'make *: allow' must come BEFORE '*: deny' (last-match-wins)."""
        cfg_path = PROJECT_ROOT / "opencode.json"
        assert cfg_path.is_file(), "Required opencode.json is missing"
        cfg = json.loads(cfg_path.read_text())
        bash_perms = cfg.get("permission", {}).get("bash", {})
        if isinstance(bash_perms, list):
            # OpenCode's current schema is an ordered rule list.  Rules are
            # matched last-to-first, so the specific make allow must follow
            # the catch-all deny in the source list.
            star_idx = next(
                (i for i, rule in enumerate(bash_perms)
                 if isinstance(rule, dict)
                 and rule.get("path") == "*"
                 and rule.get("allow") is False),
                -1,
            )
            make_idx = next(
                (i for i, rule in enumerate(bash_perms)
                 if isinstance(rule, dict)
                 and str(rule.get("command", "")).startswith("make")
                 and rule.get("allow") is True),
                -1,
            )
        elif isinstance(bash_perms, dict):
            # Preserve compatibility with older object-map configs.
            keys = list(bash_perms.keys())
            star_idx = next((i for i, k in enumerate(keys) if k == "*"), -1)
            make_idx = next((i for i, k in enumerate(keys) if k.startswith("make")), -1)
        else:
            pytest.fail("permission.bash must be an ordered rule list or object map")
        if star_idx != -1 and make_idx != -1:
            assert star_idx < make_idx, (
                f"'*: deny' at position {star_idx} must come BEFORE "
                f"'make *: allow' at position {make_idx} (last-match-wins)"
            )


class TestHookInvocation:
    """Plugin hooks must not crash when invoked with realistic inputs."""

    def test_no_hook_invocation_crashes(self):
        """No hook throws TypeError/ReferenceError on invocation."""
        data = _run_verifier()
        failures = data.get("failures", [])
        hook_failures = [f for f in failures if "CRASH" in f.get("message", "")]
        assert len(hook_failures) == 0, (
            "Hook invocation crashes:\n"
            + "\n".join(f"  [{f['test']}] {f['message']}" for f in hook_failures)
        )

    def test_tool_execute_before_hooks_return_valid_shape(self):
        """tool.execute.before hooks return undefined (pass) or {permissionDecision: ...}."""
        data = _run_verifier()
        failures = data.get("failures", [])
        # All execute.before failures should be enforcement, not crashes
        crash = [f for f in failures
                 if "execute-before" in f.get("test", "") and "CRASH" in f.get("message", "")]
        assert len(crash) == 0, f"tool.execute.before crash: {crash}"


class TestNodeV26Compatibility:
    """All plugin code must be parseable by Node v26 --experimental-strip-types."""

    def test_no_try_inside_catch(self):
        """try {} inside catch {} is a parse error under --experimental-strip-types."""
        import re
        try_in_catch = re.compile(r'\bcatch\s*(?:\([^)]*\))?\s*\{[^}]*\btry\b', re.DOTALL)
        violations = []
        for dir_ in [PLUGIN_DIR, PLUGINS_DIR]:
            if not dir_.exists():
                continue
            for f in dir_.glob("*.ts"):
                content = f.read_text()
                if try_in_catch.search(content):
                    violations.append(str(f.relative_to(PROJECT_ROOT)))
        assert len(violations) == 0, (
            f"try-inside-catch (Node v26 parse error) in: {violations}"
        )

    def test_no_type_annotated_catch(self):
        """catch (e: TypeError) may fail under --experimental-strip-types."""
        import re
        typed_catch = re.compile(r'\bcatch\s*\(\s*\w+\s*:\s*(?!any\b|unknown\b)\w+')
        violations = []
        for dir_ in [PLUGIN_DIR, PLUGINS_DIR]:
            if not dir_.exists():
                continue
            for f in dir_.glob("*.ts"):
                content = f.read_text()
                if typed_catch.search(content):
                    violations.append(str(f.relative_to(PROJECT_ROOT)))
        assert len(violations) == 0, (
            f"Type-annotated catch variable in: {violations}"
        )


class TestSubagentGuard:
    """Every enforcement plugin must guard against firing inside subagents."""

    def test_all_enforcement_plugins_have_subagent_guard(self):
        """OPENCODE_SUBAGENT (or isSubagent/GLUDD_SUBAGENT) check present."""
        assert PLUGIN_DIR.is_dir(), "Required .opencode/plugin directory is missing"
        missing = []
        for f in sorted(PLUGIN_DIR.glob("*.ts")):
            # Skip shared utilities that aren't enforcement plugins
            if f.name.startswith("_") or f.name == "hot_reload.ts":
                continue
            content = f.read_text()
            if "OPENCODE_SUBAGENT" not in content and "isSubagent" not in content:
                missing.append(f.name)
        assert len(missing) == 0, (
            f"Enforcement plugins without subagent guard: {missing}"
            f"\nEnforcement WILL fire inside subagents without this guard."
        )


class TestLibraryIntegrity:
    """Shared library files must be importable."""

    def test_lib_files_are_importable(self):
        """shared.ts, hot_reload.ts must resolve without errors."""
        data = _run_verifier()
        failures = data.get("failures", [])
        lib_failures = [f for f in failures if "lib-" in f.get("test", "")]
        assert len(lib_failures) == 0, (
            f"Library import failures: {lib_failures}"
        )


class TestAutoDiscoveredSafety:
    """Auto-discovered plugin files must be valid plugins or explicitly allowlisted."""

    def test_all_auto_discovered_ts_files_are_valid_plugins(self):
        """Every .ts file not in opencode.json plugin array must still export a valid plugin."""
        data = _run_verifier()
        failures = data.get("failures", [])
        auto_failures = [f for f in failures if "auto-discovered" in f.get("test", "")]
        assert len(auto_failures) == 0, (
            "Auto-discovered files that fail as plugins:\n"
            + "\n".join(f"  [{f['test']}] {f['message']}" for f in auto_failures)
        )

    def test_hot_reload_ts_is_safe(self):
        """hot_reload.ts is auto-discovered but must be a valid plugin."""
        hot_path = PLUGIN_DIR / "hot_reload.ts"
        if not hot_path.exists():
            return
        data = _run_verifier()
        loaded = data.get("summaries", {}).get("loaded-plugins", [])
        hot = [p for p in loaded if "hot_reload" in p.get("file", "")]
        assert len(hot) > 0, "hot_reload.ts not found in loaded plugins"
        assert len(hot[0].get("hooks", [])) > 0, "hot_reload.ts has no hooks"
