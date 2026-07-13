from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
PLUGINS_DIR = ROOT / ".opencode" / "plugins"
TEST_DIR = ROOT / "tests" / "unit"

PLUGIN_DIRS = [d for d in (PLUGIN_DIR, PLUGINS_DIR) if d.exists()]

HOOK_NAMES = frozenset([
    "tool.execute.before",
    "tool.execute.after",
    "text.complete",
    "session.idle",
    "event",
    "experimental.chat.system.transform",
    "experimental.text.complete",
])

PLUGIN_TO_TEST = {
    "enforce-clean-tree": ["test_clean_tree_plugin.py"],
    "enforce-commit-lock": ["test_commit_lock_plugin.py"],
    "enforce-deadline": ["test_enforcement_deadline_plugin.py"],
    "enforce-delegate": ["test_enforcement_delegate_plugin.py"],
    "enforce-deletion-gate": ["test_enforcement_deletion_gate_plugin.py"],
    "enforce-enhancement-ratio": ["test_enhancement_ratio_plugin.py"],
    "enforce-floor": ["test_enforcement_floor_plugin.py"],
    "enforce-make": ["test_enforce_make_plugin.py", "test_enforce_make_subagent.py"],
    "enforce-multitask": ["test_multitask_plugin.py"],
    "enforce-no-suppressions": ["test_no_suppression_comments_plugin.py"],
    "enforce-no-wait": ["test_no_wait_plugin.py"],
    "enforce-session-start": ["test_session_start_plugin.py", "test_enforcement_session_start_plugin.py"],
    "enforce-stop": ["test_enforce_stop_syntax.py", "test_enforce_false_done.py", "test_false_done_plugin.py", "test_stop_pattern_qa.py", "test_todo_guard_plugin.py"],
    "enforce-verified-claims": ["test_verified_claims_plugin.py"],
    "hot_reload": ["test_hot_reload_safe_merge.py", "test_hot_reload_code.py", "test_hot_reload_module.py", "test_hot_reload_toc.py"],
    "watchdog": ["test_watchdog_plugin.py"],
}


def _collect_plugin_files():
    files = []
    for d in PLUGIN_DIRS:
        for f in sorted(d.glob("*.ts")):
            files.append(f)
    return files


def _stem(f: Path) -> str:
    return f.stem


def _read(f: Path) -> str:
    return f.read_text()


class TestAllPluginsSyntax:
    def test_all_node_check(self):
        errors = []
        for f in _collect_plugin_files():
            result = subprocess.run(
                ["node", "--check", str(f)],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                errors.append(f"{f.name}: {result.stderr.strip()}")
        assert not errors, (
            f"{len(errors)} plugin file(s) have node --check errors:\n"
            + "\n".join(errors)
        )


class TestAllPluginsDefaultExport:
    def test_each_exports_default(self):
        missing = []
        for f in _collect_plugin_files():
            content = _read(f)
            if "export default" not in content:
                missing.append(f.name)
        assert not missing, (
            f"{len(missing)} plugin(s) missing export default:\n"
            + "\n".join(missing)
        )


class TestAllPluginsHookRegistration:
    def test_each_registers_at_least_one_hook(self):
        missing = []
        for f in _collect_plugin_files():
            content = _read(f)
            found = False
            for hook in HOOK_NAMES:
                if hook in content:
                    found = True
                    break
            if not found:
                missing.append(f.name)
        assert not missing, (
            f"{len(missing)} plugin(s) missing hook registration:\n"
            + "\n".join(missing)
        )


class TestAllPluginsTestCoverage:
    def test_all_plugins_have_test_files(self):
        untested = []
        plugin_files = _collect_plugin_files()
        for f in plugin_files:
            name = _stem(f)
            expected = PLUGIN_TO_TEST.get(name)
            if expected is None:
                untested.append(f"{name} (no mapping in PLUGIN_TO_TEST)")
                continue
            found = []
            not_found = []
            for test_file in expected:
                if (TEST_DIR / test_file).exists():
                    found.append(test_file)
                else:
                    not_found.append(test_file)
            if not found:
                untested.append(f"{name} → expected {expected}, found none")
        assert not untested, (
            f"{len(untested)} plugin(s) lack test coverage:\n"
            + "\n".join(untested)
        )

    def test_all_plugin_test_files_exist(self):
        missing = []
        for name, test_files in PLUGIN_TO_TEST.items():
            for tf in test_files:
                if not (TEST_DIR / tf).exists():
                    missing.append(f"{name} → {tf} (missing)")
        assert not missing, (
            f"{len(missing)} test file(s) referenced but not found:\n"
            + "\n".join(missing)
        )

    def test_test_files_import_or_reference_plugin(self):
        stale = []
        for name, test_files in PLUGIN_TO_TEST.items():
            plugin_filename = f"{name}.ts"
            for tf in test_files:
                test_path = TEST_DIR / tf
                if not test_path.exists():
                    continue
                content = test_path.read_text()
                if plugin_filename not in content and name not in content:
                    stale.append(f"{tf}: no reference to {plugin_filename} or '{name}'")
        assert not stale, (
            f"{len(stale)} test file(s) may be stale (no reference to plugin):\n"
            + "\n".join(stale)
        )
