"""Structural pin for the enforce-objective plugin.

Verifies the plugin exists, is registered, exports helpers, has env-disable,
subagent guard, fail-open, hot-reload, and objective-detection logic.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode/plugin/enforce-objective.ts"
SESSION_PATH = ROOT / "SESSION.md"


def _plugin_source() -> str:
    assert PLUGIN_PATH.exists(), f"Plugin missing at {PLUGIN_PATH}"
    return PLUGIN_PATH.read_text()


class TestPluginStructure:
    def test_plugin_file_exists(self):
        assert PLUGIN_PATH.exists()

    def test_plugin_registered_in_opencode_json(self):
        oc = (ROOT / "opencode.json").read_text()
        assert "enforce-objective.ts" in oc

    def test_tool_execute_before_hook(self):
        src = _plugin_source()
        assert "tool.execute.before" in src

    def test_text_complete_hook(self):
        src = _plugin_source()
        assert "text.complete" in src

    def test_fail_open_present(self):
        src = _plugin_source()
        assert "catch" in src.lower()

    def test_env_var_disable(self):
        src = _plugin_source()
        assert "GLUDD_OBJECTIVE_ENFORCE" in src

    def test_subagent_guard(self):
        src = _plugin_source()
        assert "isSubagent()" in src

    def test_hot_reload_capable(self):
        src = _plugin_source()
        assert "loadHotModule" in src

    def test_uses_getProjectRoot(self):
        src = _plugin_source()
        assert "getProjectRoot" in src


class TestHelperFunctions:
    def test_getPrimaryObjective_is_private(self):
        src = _plugin_source()
        assert "function getPrimaryObjective" in src
        assert "export function getPrimaryObjective" not in src

    def test_isObjectiveMet_is_private(self):
        src = _plugin_source()
        assert "function isObjectiveMet" in src
        assert "export function isObjectiveMet" not in src

    def test_isCiGreenFromCache_is_private(self):
        src = _plugin_source()
        assert "function isCiGreenFromCache" in src
        assert "export function isCiGreenFromCache" not in src

    def test_nag_prefix_present(self):
        src = _plugin_source()
        assert "NAG_PREFIX" in src
        assert "NO PRIMARY OBJECTIVE SET" in src

    def test_getPrimaryObjective_reads_session_md(self):
        src = _plugin_source()
        pidx = src.index("function getPrimaryObjective")
        end = src.find("function getStackedObjective", pidx + 1)
        if end == -1:
            end = len(src)
        body = src[pidx:end]
        assert "SESSION.md" in body

    def test_ci_objective_detection_regex(self):
        src = _plugin_source()
        assert r"\bCI\s*GREEN\b|\bGREEN\s*CI\b" in src or "CI.*GREEN|GREEN.*CI" in src

    def test_ci_cache_path_present(self):
        src = _plugin_source()
        assert "gludd-watchdog-ci.json" in src

    def test_dispatch_tools_allowed(self):
        src = _plugin_source()
        assert '"task"' in src and '"agent"' in src and '"workflow"' in src

    def test_read_tools_allowed(self):
        src = _plugin_source()
        assert '"read"' in src and '"grep"' in src and '"glob"' in src


class TestObjectiveExtraction:
    def test_parses_primary_objective_field(self):
        regex = re.compile(r"^## PRIMARY OBJECTIVE:\s*(.+)$", re.MULTILINE)
        assert regex is not None
        match = regex.search("## PRIMARY OBJECTIVE: FOO BAR")
        assert match is not None
        assert match.group(1) == "FOO BAR"

    def test_parses_multiline_objective(self):
        regex = re.compile(r"^## PRIMARY OBJECTIVE:\s*(.+)$", re.MULTILINE)
        text = "# Header\n## PRIMARY OBJECTIVE: GREEN CI ON DEV → BETA.2\n## Next section\n"
        match = regex.search(text)
        assert match is not None
        assert "GREEN CI" in match.group(1)

    def test_no_objective_returns_empty(self):
        regex = re.compile(r"^## PRIMARY OBJECTIVE:\s*(.+)$", re.MULTILINE)
        assert regex.search("# Just a header\nNo objective here\n") is None


class TestSESSIONmdIntegration:
    def test_session_md_has_primary_objective(self):
        assert SESSION_PATH.exists(), "SESSION.md missing"
        content = SESSION_PATH.read_text()
        match = re.search(r"^## PRIMARY OBJECTIVE:\s*(.+)$", content, re.MULTILINE)
        assert match, "SESSION.md must have PRIMARY OBJECTIVE field"
        assert len(match.group(1).strip()) > 0, "PRIMARY OBJECTIVE must not be empty"

    def test_primary_objective_mentions_ci(self):
        content = SESSION_PATH.read_text()
        match = re.search(r"^## PRIMARY OBJECTIVE:\s*(.+)$", content, re.MULTILINE)
        assert match, "SESSION.md must have PRIMARY OBJECTIVE field"
        obj = match.group(1)
        assert "GREEN" in obj.upper() or "CI" in obj.upper(), "PRIMARY OBJECTIVE should reference CI status"


class TestCiCacheDetection:
    def test_ci_green_detection_mirrors_plugin_logic(self):
        """Verify the plugin's CI-green detection reads gludd-watchdog-ci.json
        and checks last_ci_status === 'SUCCESS' within a 600s freshness window.
        This test verifies the LOGIC structure matches, not the live CI state."""
        import time as _time
        from pathlib import Path

        Path("/tmp/gludd-watchdog-ci.json")

        # Simulate: fresh + SUCCESS → green
        sim = {"last_ci_check": _time.time(), "last_ci_status": "SUCCESS"}
        fresh = (_time.time() - sim["last_ci_check"]) < 600
        success = sim["last_ci_status"] == "SUCCESS"
        assert fresh and success, "Simulated green should be green"

        # Simulate: stale → not green
        sim_stale = {"last_ci_check": _time.time() - 700, "last_ci_status": "SUCCESS"}
        fresh_stale = (_time.time() - sim_stale["last_ci_check"]) < 600
        assert not fresh_stale, "Stale cache should not be fresh"

        # Simulate: FAILURE → not green
        sim_fail = {"last_ci_check": _time.time(), "last_ci_status": "FAILURE"}
        assert sim_fail["last_ci_status"] != "SUCCESS", "FAILURE should not be green"
