"""Runtime verification that anti-stop TypeScript plugins are structurally sound.

Verifies hook registrations, pattern coverage, and disk presence — not behavioral
correctness (that requires an actual opencode session), but the structural properties
that must hold for the plugins to function at all.
"""

import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PLUGIN_DIR = PROJECT_ROOT / ".opencode" / "plugin"
OPENCODE_JSON = PROJECT_ROOT / "opencode.json"

EXPECTED_PLUGINS = [
    "enforce-make.ts",
    "enforce-deletion-gate.ts",
    "enforce-floor.ts",
    "enforce-delegate.ts",
    "enforce-stop.ts",
    "enforce-todos.ts",
    "enforce-false-done.ts",
    "enforce-session-start.ts",
    "enforce-deadline.ts",
]


def _read_plugin(name: str) -> str:
    path = PLUGIN_DIR / name
    assert path.exists(), f"Plugin file missing: {path}"
    return path.read_text()


# ---------------------------------------------------------------------------
# 1. enforce-stop.ts — hook registrations
# ---------------------------------------------------------------------------

class TestEnforceStopHookRegistrations:
    def test_has_text_complete_hook(self):
        content = _read_plugin("enforce-stop.ts")
        assert '"experimental.text.complete"' in content, (
            "enforce-stop.ts must register experimental.text.complete hook"
        )

    def test_has_tool_execute_before_hook(self):
        content = _read_plugin("enforce-stop.ts")
        assert '"tool.execute.before"' in content, (
            "enforce-stop.ts must register tool.execute.before hook"
        )

    def test_has_session_idle_hook(self):
        content = _read_plugin("enforce-stop.ts")
        assert '"session.idle"' in content, (
            "enforce-stop.ts must register session.idle hook"
        )

    def test_has_system_transform_hook(self):
        content = _read_plugin("enforce-stop.ts")
        assert '"experimental.chat.system.transform"' in content, (
            "enforce-stop.ts must register experimental.chat.system.transform hook"
        )


# ---------------------------------------------------------------------------
# 2. enforce-stop.ts text.complete is NOT a pass-through
# ---------------------------------------------------------------------------

class TestEnforceStopTextCompleteNotPassthrough:
    def test_text_complete_body_has_substantial_logic(self):
        content = _read_plugin("enforce-stop.ts")

        # Find the text.complete handler body between async and the closing
        match = re.search(
            r'"experimental\.text\.complete":\s*async.*?\{',
            content, re.DOTALL,
        )
        assert match is not None, "Could not find text.complete handler declaration"

        # Find the matching closing brace for the handler body
        # After the handler signature, the body starts — it should contain
        # substantial logic, not just a bare return
        body_start = match.end()
        # The handler should contain state checks (ratchetHasEntries, etc.)
        remaining = content[body_start:]
        assert "ratchetHasEntries" in remaining, (
            "text.complete handler must contain ratchetHasEntries check, not pass-through"
        )
        assert "tasksMdHasUnchecked" in remaining, (
            "text.complete handler must contain tasksMdHasUnchecked check"
        )
        assert "responseLooksTerminal" in remaining, (
            "text.complete handler must contain responseLooksTerminal check"
        )

        # The handler must REPLACE output.text, not just return
        assert "output.text" in remaining, (
            "text.complete handler must modify output.text (not pass through)"
        )


# ---------------------------------------------------------------------------
# 3. enforce-false-done.ts — CLAIM_PATTERNS coverage + text.complete usage
# ---------------------------------------------------------------------------

class TestEnforceFalseDonePatterns:
    def test_claim_patterns_include_committed(self):
        content = _read_plugin("enforce-false-done.ts")
        assert 'committed' in content, (
            "CLAIM_PATTERNS must include 'committed'"
        )

    def test_claim_patterns_include_pushed(self):
        content = _read_plugin("enforce-false-done.ts")
        assert 'pushed' in content, (
            "CLAIM_PATTERNS must include 'pushed'"
        )

    def test_claim_patterns_include_all_done(self):
        content = _read_plugin("enforce-false-done.ts")
        assert 'all done' in content, (
            "CLAIM_PATTERNS must include 'all done'"
        )

    def test_claim_patterns_include_everything_is(self):
        content = _read_plugin("enforce-false-done.ts")
        assert 'everything is' in content, (
            "CLAIM_PATTERNS must include 'everything is'"
        )

    def test_claim_patterns_include_no_issues(self):
        content = _read_plugin("enforce-false-done.ts")
        assert 'no issues' in content, (
            "CLAIM_PATTERNS must include 'no issues'"
        )

    def test_uses_text_complete_not_response_transform(self):
        content = _read_plugin("enforce-false-done.ts")
        assert '"experimental.text.complete"' in content, (
            "enforce-false-done.ts must use experimental.text.complete"
        )
        # Must NOT contain the older response.transform pattern
        assert '"experimental.chat.response.transform"' not in content, (
            "enforce-false-done.ts must NOT use experimental.chat.response.transform"
        )


# ---------------------------------------------------------------------------
# 4. enforce-todos.ts — uses text.complete + has SUMMARY_KEYWORDS
# ---------------------------------------------------------------------------

class TestEnforceTodos:
    def test_uses_text_complete(self):
        content = _read_plugin("enforce-todos.ts")
        assert '"experimental.text.complete"' in content, (
            "enforce-todos.ts must use experimental.text.complete"
        )
        assert '"experimental.chat.response.transform"' not in content, (
            "enforce-todos.ts must NOT use experimental.chat.response.transform"
        )

    def test_summary_keywords_exist(self):
        content = _read_plugin("enforce-todos.ts")
        assert "SUMMARY_KEYWORDS" in content, (
            "enforce-todos.ts must define SUMMARY_KEYWORDS"
        )
        # Verify at least some of the known keywords are present
        for kw in ["summary", "completed", "done", "results"]:
            assert kw in content, (
                f"SUMMARY_KEYWORDS must contain '{kw}'"
            )


# ---------------------------------------------------------------------------
# 5. All 9 registered plugins exist on disk
# ---------------------------------------------------------------------------

class TestAllPluginsOnDisk:
    def test_opencode_json_has_plugin_array(self):
        raw = OPENCODE_JSON.read_text()
        config = json.loads(raw)
        assert "plugin" in config, "opencode.json must have plugin array"
        assert isinstance(config["plugin"], list), "plugin must be an array"
        assert len(config["plugin"]) == len(EXPECTED_PLUGINS), (
            f"Expected {len(EXPECTED_PLUGINS)} plugins, found {len(config['plugin'])}"
        )

    def test_all_nine_plugins_exist(self):
        raw = OPENCODE_JSON.read_text()
        config = json.loads(raw)
        plugins = config["plugin"]

        assert len(plugins) == 9, f"Expected 9 plugins, got {len(plugins)}"

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
        for name in EXPECTED_PLUGINS:
            content = _read_plugin(name)
            assert "import type { Plugin }" in content or 'import type {Plugin}' in content, (
                f"{name} must import the Plugin type"
            )
            assert "satisfies Plugin" in content or ": Plugin" in content or "as Plugin" in content, (
                f"{name} must have type assertion (satisfies Plugin, : Plugin, or as Plugin)"
            )
            assert "export default" in content or "export default" in content, (
                f"{name} must have a default export"
            )


# ---------------------------------------------------------------------------
# 6. State-file smoke test — /tmp/gludd-stop-state.json
# ---------------------------------------------------------------------------

class TestStopStateFile:
    def test_stop_state_file_valid_json_if_exists(self):
        state_path = Path("/tmp/gludd-stop-state.json")
        if not state_path.exists():
            return  # Plugin has not fired in this session yet — that's fine

        raw = state_path.read_text()
        data = json.loads(raw)
        assert isinstance(data, dict), "State file must be a JSON object"
        # Expected keys from the StopStateCache interface
        expected_keys = {"ts", "ratchetEntries", "tasksMdUnchecked", "gateStatusRed",
                         "repoPending", "backlogOpen", "backlogItems"}
        for key in expected_keys:
            assert key in data, f"State file missing key: {key}"


# ---------------------------------------------------------------------------
# 7. Persistent block state file — /tmp/gludd-false-done-blocks.json
# ---------------------------------------------------------------------------

class TestFalseDoneBlockStateFile:
    def test_block_state_file_valid_json_if_exists(self):
        state_path = Path("/tmp/gludd-false-done-blocks.json")
        if not state_path.exists():
            return  # Plugin has not fired in this session yet — that's fine

        raw = state_path.read_text()
        data = json.loads(raw)
        assert isinstance(data, dict), "Block state file must be a JSON object"
        assert "count" in data, "Block state file must have 'count' key"
        assert isinstance(data["count"], (int, float)), (
            "'count' must be a number"
        )
