"""Runtime tests for enforcement plugin configuration and correctness.

Tests that verify:
1. Plugin files have valid syntax (node --check)
2. All registered plugins exist on disk
3. opencode.json permission ordering is correct
4. Enforcement plugins have subagent guards
5. Specific bug fixes (clean-tree ESM import, stop plugin async pattern)

These tests do NOT rely on Node's --experimental-strip-types (which has known
bugs in v26 with try/catch inside arrow functions with inline types).
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


class TestPluginFilesSyntax:
    """Every .ts plugin file must pass node --check."""

    def test_all_plugins_syntax_ok(self):
        plugin_dir = ROOT / ".opencode" / "plugin"
        plugins_dir = ROOT / ".opencode" / "plugins"
        errors = []
        for d in [plugin_dir, plugins_dir]:
            if not d.is_dir():
                continue
            for f in sorted(d.glob("*.ts")):
                result = subprocess.run(
                    ["node", "--check", str(f)],
                    capture_output=True,
                    text=True,
                    cwd=str(ROOT),
                )
                if result.returncode != 0:
                    errors.append(f"{f.name}: node --check failed\n{result.stderr}")
        assert not errors, f"Syntax errors in {len(errors)} files:\n" + "\n".join(errors)


class TestPluginRegistration:
    """Every plugin in opencode.json must exist on disk."""

    def test_all_registered_plugins_exist(self):
        config = json.loads((ROOT / "opencode.json").read_text())
        registered = config.get("plugin", [])
        for path in registered:
            if path.startswith("./"):
                path = path[2:]
            full = ROOT / path
            assert full.is_file(), f"Registered plugin {path} not found on disk at {full}"

    def test_all_disk_files_registered_or_utility(self):
        config = json.loads((ROOT / "opencode.json").read_text())
        registered = set()
        for p in config.get("plugin", []):
            if p.startswith("./"):
                p = p[2:]
            registered.add(p)
        # Shared helpers live in .opencode/lib/ (E.5 refactor); they are not
        # plugins and are never registered in opencode.json.
        utilities = {
            ".opencode/lib/hot_reload.ts",
            ".opencode/lib/shared.ts",
        }
        plugin_dir = ROOT / ".opencode" / "plugin"
        if plugin_dir.is_dir():
            for f in sorted(plugin_dir.glob("*.ts")):
                rel = str(f.relative_to(ROOT))
                assert rel in registered or rel in utilities, f"{rel} on disk but not registered (or utility)"

    def test_enforce_stop_registered(self):
        config = json.loads((ROOT / "opencode.json").read_text())
        plugins = config.get("plugin", [])
        stop_paths = [p for p in plugins if "enforce-stop" in p]
        assert stop_paths, "enforce-stop.ts NOT registered in opencode.json"


class TestPermissionOrdering:
    """opencode.json bash permission must deny first, then allow make."""

    def test_bash_deny_before_allow(self):
        config = json.loads((ROOT / "opencode.json").read_text())
        bash_rules = config.get("permission", {}).get("bash", {})
        keys = list(bash_rules.keys())
        if "*" in keys and "make *" in keys:
            deny_idx = keys.index("*")
            allow_idx = keys.index("make *")
            assert deny_idx < allow_idx, (
                f"Permission ordering wrong: '*: deny' at position {deny_idx}, "
                f"'make *: allow' at position {allow_idx}. "
                "Last-match-wins means make would be denied. Swap them."
            )


class TestSubagentGuards:
    """All enforcement plugins with hooks must have subagent guards."""

    def test_all_enforcement_plugins_guard_import_or_inline(self):
        plugin_dir = ROOT / ".opencode" / "plugin"
        enforcement_plugins = [f for f in plugin_dir.glob("enforce-*.ts")]
        # shared.ts moved from .opencode/plugin/ to .opencode/lib/ (E.5 refactor),
        # so plugins import it as "../lib/shared.ts". Accept any path ending in
        # shared.ts so the guard check does not silently stop matching on a move.
        guard_imported = re.compile(r'import\s+\{[^}]*isSubagent[^}]*\}\s+from\s+"[^"]*shared\.ts"')
        guard_called = re.compile(r"\bisSubagent\(\)")
        guard_inline = re.compile(r'process\.env\.OPENCODE_SUBAGENT\s*===?\s*"1"')
        _is_subagent_defined = re.compile(r"function\s+_isSubagent\s*\(\s*\)")

        for plugin in enforcement_plugins:
            src = plugin.read_text()
            # Must either import isSubagent from shared.ts or define _isSubagent inline
            has_import = bool(guard_imported.search(src))
            has_inline = bool(_is_subagent_defined.search(src) and guard_inline.search(src))
            assert has_import or has_inline, (
                f"{plugin.name}: no isSubagent guard — must either import from shared.ts or define _isSubagent() inline"
            )
            # Must actually CALL the guard
            has_call = bool(guard_called.search(src)) or bool(guard_inline.search(src))
            assert has_call, f"{plugin.name}: imports/defines guard but never calls it"


class TestCleanTreeFix:
    """enforce-clean-tree.ts must avoid static process-module imports."""

    def test_clean_tree_uses_wrapped_process_import(self):
        src = (ROOT / ".opencode" / "plugin" / "enforce-clean-tree.ts").read_text()
        helper = (ROOT / ".opencode" / "lib" / "plugin_test_exports.ts").read_text()
        assert "getGitStatus" in src, (
            "enforce-clean-tree.ts must delegate git status reads to the shared helper"
        )
        assert 'import { createRequire } from "node:module"' in helper
        assert '"node:child_" + "process"' in helper, "shared helper must avoid a static child-process import"
        assert 'require("node:child_process")' not in src, "enforce-clean-tree.ts still uses direct CJS require()"
        assert 'require("node:child_process")' not in helper, "shared helper still uses direct CJS require()"


class TestEnforceStopFix:
    """enforce-stop.ts must not use patterns that break opencode startup."""

    def test_enforce_stop_no_bare_satisfies(self):
        src = (ROOT / ".opencode" / "plugin" / "enforce-stop.ts").read_text()
        # Node v26 strip-types supports the TypeScript `satisfies` operator.
        assert "satisfies Plugin" in src, "enforce-stop.ts: export should retain `satisfies Plugin` typing"

    def test_enforce_stop_ends_correctly(self):
        src = (ROOT / ".opencode" / "plugin" / "enforce-stop.ts").read_text()
        trimmed_end = src.rstrip()
        assert trimmed_end.endswith("}) satisfies Plugin"), (
            f"enforce-stop.ts must end with '}}) satisfies Plugin' — got: ...{trimmed_end[-45:]}"
        )

    def test_enforce_stop_uses_simple_export(self):
        src = (ROOT / ".opencode" / "plugin" / "enforce-stop.ts").read_text()
        # Must use async () => not async ({ }) =>
        assert "async () =>" in src, "enforce-stop.ts: export default must use async () =>, not async ({ }) =>"
        assert "async ({ }) =>" not in src, (
            "enforce-stop.ts: still has async ({ }) => — Node type stripper breaks on this"
        )


class TestDeletionGateFix:
    """enforce-deletion-gate.ts import fix."""

    def test_deletion_gate_uses_correct_import(self):
        src = (ROOT / ".opencode" / "plugin" / "enforce-deletion-gate.ts").read_text()
        assert "@opencode-ai/plugin" in src, (
            "enforce-deletion-gate.ts must import from @opencode-ai/plugin, not @opencode/core"
        )
        assert "@opencode/core" not in src, (
            "enforce-deletion-gate.ts still imports from @opencode/core — use @opencode-ai/plugin"
        )


class TestPluginCompilation:
    """scripts/compile_plugins_for_test.mjs exists and can compile plugins."""

    def test_compiler_script_exists(self):
        script = ROOT / "scripts" / "compile_plugins_for_test.mjs"
        assert script.is_file(), "compile_plugins_for_test.mjs not found"

    def test_compiler_produces_output(self):
        script = ROOT / "scripts" / "compile_plugins_for_test.mjs"
        result = subprocess.run(
            ["node", str(script)],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        assert result.returncode == 0, f"Compilation failed: {result.stderr[:500]}"
        out_dir = Path("/tmp/gludd-plugin-js")
        assert out_dir.is_dir(), "Output directory not created"
        mjs_files = list(out_dir.glob("*.mjs"))
        assert len(mjs_files) >= 10, f"Expected >=10 compiled files, got {len(mjs_files)}"
