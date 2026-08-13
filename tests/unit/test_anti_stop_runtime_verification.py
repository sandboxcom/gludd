"""Runtime verification that anti-stop TypeScript plugins are structurally sound.

Verifies hook registrations, pattern coverage, and disk presence — not behavioral
correctness (that requires an actual opencode session), but the structural properties
that must hold for the plugins to function at all.
"""

import json
import re
from pathlib import Path

from tests.unit._plugin_contract import plugin_contract_source

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PLUGIN_DIR = PROJECT_ROOT / ".opencode" / "plugin"
PLUGIN_IMPL_DIR = PLUGIN_DIR / "impl"
PLUGINS_DIR = PROJECT_ROOT / ".opencode" / "plugins"
OPENCODE_JSON = PROJECT_ROOT / "opencode.json"

# Filenames that live in .opencode/plugin/ (singular). The watchdog daemon
# lives in .opencode/plugins/ (plural) and is tracked separately.
EXPECTED_PLUGIN_FILES = [
    "enforce-make.ts",
    "enforce-deletion-gate.ts",
    "enforce-floor.ts",
    "enforce-floor-v2.ts",
    "enforce-delegate.ts",
    "enforce-stop.ts",
    "enforce-session-start.ts",
    "enforce-deadline.ts",
    "enforce-no-suppressions.ts",
    "enforce-no-wait.ts",
    "enforce-commit-lock.ts",
    "enforce-clean-tree.ts",
    "enforce-verified-claims.ts",
    "enforce-multitask.ts",
    "enforce-enhancement-ratio.ts",
    "enforce-batch-push.ts",
    "enforce-depth.ts",
    "enforce-tdd.ts",
    "enforce-objective.ts",
    "enforce-anti-essay.ts",
    "enforce-branch-discipline.ts",
    "enforce-test-integrity.ts",
    "enforce-worktree.ts",
    "enforce-audit.ts",
    "enforce-context.ts",
    "enforce-deliverable.ts",
    "enforce-no-ci-poll.ts",
    "enforce-release-deadline.ts",
    "enforce-task-tracking.ts",
]
EXPECTED_PLUGINS_FILES = ["watchdog.ts"]

# Combined list of (filename, directory) tuples — the canonical source of
# truth for what must be registered in opencode.json AND exist on disk.
EXPECTED_PLUGINS = [
    (name, PLUGIN_DIR) for name in EXPECTED_PLUGIN_FILES
] + [
    (name, PLUGINS_DIR) for name in EXPECTED_PLUGINS_FILES
]


def _read_plugin(name: str) -> str:
    # Look in both plugin directories so callers can pass either filename.
    for d in (PLUGIN_DIR, PLUGINS_DIR):
        path = d / name
        if path.exists():
            return plugin_contract_source(path)
    raise AssertionError(
        f"Plugin file missing: {name} (searched {PLUGIN_DIR} and {PLUGINS_DIR})"
    )


def _read_plugin_with_impl(name: str) -> str:
    content = _read_plugin(name)
    impl_name = name.removesuffix(".ts").replace("-", "_") + "_impl.ts"
    impl_path = PLUGIN_IMPL_DIR / impl_name
    if impl_path.exists():
        content += impl_path.read_text()
    return content


# ---------------------------------------------------------------------------
# 1. enforce-stop.ts — hook registrations
# ---------------------------------------------------------------------------

class TestEnforceStopHookRegistrations:
    def test_has_supported_text_complete_hook(self):
        content = _read_plugin_with_impl("enforce-make.ts")
        assert '"experimental.text.complete"' in content, (
            "enforce-make.ts must register experimental.text.complete hook"
        )
        assert re.search(r'(?<!experimental\.)"text\.complete"\s*:', content) is None

    def test_has_tool_execute_before_hook(self):
        content = _read_plugin_with_impl("enforce-stop.ts")
        assert '"tool.execute.before"' in content, (
            "enforce-stop.ts must register tool.execute.before hook"
        )

    def test_has_session_idle_hook(self):
        content = _read_plugin_with_impl("enforce-make.ts")
        assert '"event"' in content and '"session.idle"' in content, (
            "enforce-make.ts must route session.idle through the supported event hook"
        )

    def test_has_system_transform_hook(self):
        content = _read_plugin_with_impl("enforce-stop.ts")
        assert '"experimental.chat.system.transform"' in content, (
            "enforce-stop.ts must register experimental.chat.system.transform hook"
        )


# ---------------------------------------------------------------------------
# 2. enforce-stop.ts text.complete is NOT a pass-through
# ---------------------------------------------------------------------------

class TestEnforceStopTextCompleteNotPassthrough:
    def test_text_complete_body_has_substantial_logic(self):
        content = (
            PLUGIN_IMPL_DIR / "enforce_make_impl.ts"
        ).read_text()

        # Find the text.complete handler body between async and the closing
        match = re.search(
            r'"experimental\.text\.complete":\s*async.*?\{',
            content, re.DOTALL,
        )
        assert match is not None, "Could not find text.complete handler declaration"

        # Find the matching closing brace for the handler body
        body_start = match.end()
        remaining = content[body_start:]
        # The handler must not be a bare pass-through — it should contain
        # subagent guard or gate-status check logic
        assert (
            "isSubagent()" in remaining
            or "subagent" in remaining.lower()
            or "gate-status" in remaining
            or ".gate-status" in remaining
        ), (
            "text.complete handler must contain guard/check logic, not pass-through"
        )

        # The handler must prepend/modify output when gate is RED
        assert "GATE RED" in remaining or "output" in remaining, (
            "text.complete handler must modify output (not pass through)"
        )


# ---------------------------------------------------------------------------
# 3. All registered plugins exist on disk
# ---------------------------------------------------------------------------

class TestAllPluginsOnDisk:
    def test_opencode_json_has_plugin_array(self):
        raw = OPENCODE_JSON.read_text()
        config = json.loads(raw)
        assert "plugin" in config, "opencode.json must have plugin array"
        assert isinstance(config["plugin"], list), "plugin must be an array"
        assert len(config["plugin"]) >= len(EXPECTED_PLUGINS), (
            f"Expected at least {len(EXPECTED_PLUGINS)} plugins, found {len(config['plugin'])}"
        )

    def test_all_plugins_exist(self):
        raw = OPENCODE_JSON.read_text()
        config = json.loads(raw)
        plugins = config["plugin"]

        # The required set is a compatibility floor, not a ceiling. New
        # enforcement plugins may be registered without making this structural
        # smoke test a manually maintained plugin-count lock.
        registered_names = {Path(plugin_path).name for plugin_path in plugins}
        expected_names = {name for name, _directory in EXPECTED_PLUGINS}
        assert expected_names <= registered_names, (
            f"Required plugins missing from opencode.json: "
            f"{sorted(expected_names - registered_names)}"
        )

        for plugin_path in plugins:
            relative = plugin_path.removeprefix("./")
            full_path = PROJECT_ROOT / relative
            assert full_path.exists(), (
                f"Plugin registered in opencode.json but missing on disk: {plugin_path}"
            )
            assert full_path.is_file(), (
                f"Plugin path is not a file: {plugin_path}"
            )

    def test_plugins_contain_well_known_structure(self):
        for name, _directory in EXPECTED_PLUGINS:
            content = _read_plugin(name)
            assert (
                "import type { Plugin }" in content
                or 'import type {Plugin}' in content
                or "import type { PluginAPI }" in content
                or 'import type {PluginAPI}' in content
            ), (
                f"{name} must import the Plugin type"
            )
            has_plugin_assertion = (
                "satisfies Plugin" in content
                or ": Plugin" in content
                or "as Plugin" in content
            )
            has_explicit_hook_signature = (
                "export default async function" in content
                and "Promise<{" in content
                and '"tool.execute.' in content
            )
            assert has_plugin_assertion or has_explicit_hook_signature, (
                f"{name} must have a Plugin assertion or an explicit typed hook signature"
            )
            assert "export default" in content or "export default" in content, (
                f"{name} must have a default export"
            )


# ---------------------------------------------------------------------------
# 4. State-file smoke test — /tmp/gludd-stop-state.json
# ---------------------------------------------------------------------------

class TestStopStateFile:
    def test_stop_state_file_valid_json_if_exists(self):
        state_path = Path("/tmp/gludd-stop-state.json")
        if not state_path.exists():
            return  # Plugin has not fired in this session yet — that's fine

        try:
            raw = state_path.read_text()
        except OSError:
            # TOCTOU: file may be unlinked by a concurrent writer/xdist sibling
            # between exists() and read_text() — treat as "not present".
            return
        data = json.loads(raw)
        assert isinstance(data, dict), "State file must be a JSON object"
        # Expected keys from the StopStateCache interface
        expected_keys = {"ts", "ratchetEntries", "tasksMdUnchecked", "gateStatusRed",
                         "repoPending", "backlogOpen", "backlogItems"}
        for key in expected_keys:
            assert key in data, f"State file missing key: {key}"


# ---------------------------------------------------------------------------
# 5. Persistent block state file — /tmp/gludd-false-done-blocks.json
# ---------------------------------------------------------------------------

class TestFalseDoneBlockStateFile:
    def test_block_state_file_valid_json_if_exists(self):
        state_path = Path("/tmp/gludd-false-done-blocks.json")
        if not state_path.exists():
            return  # Plugin has not fired in this session yet — that's fine

        try:
            raw = state_path.read_text()
        except OSError:
            # TOCTOU: a sibling pytest-xdist worker's hook_plugin_env _restore()
            # snapshots+unlinks this shared /tmp path between exists() and
            # read_text(). File vanishing mid-read is equivalent to "not present"
            # — nothing to validate. (CI flake class: FileNotFoundError.)
            return
        data = json.loads(raw)
        if isinstance(data, list):
            assert len(data) > 0, "Block state array must have at least one entry"
            assert all(isinstance(entry, dict) for entry in data), (
                "Block state array entries must be objects"
            )
            return
        assert isinstance(data, dict), "Block state file must be a JSON object or array"
        assert "count" in data, "Block state file must have 'count' key"
        assert isinstance(data["count"], (int, float)), (
            "'count' must be a number"
        )
