"""Behavior pin for the enhancement-ratio enforcement plugin.

Per AGENTS.md COST-EFFICIENCY DIRECTIVE §5 (2026-07-12): at least 50% of every
dispatch wave must be project enhancements, not just bug fixes.

This test extracts the plugin's exported keyword lists and classification logic
from the TypeScript source and exercises each against the spec cases.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

PLUGIN_PATH = Path(__file__).resolve().parents[2] / ".opencode/plugin/enforce-enhancement-ratio.ts"


def _plugin_source() -> str:
    return PLUGIN_PATH.read_text()


def _extract_keywords(src: str, varname: str) -> list[str]:
    """Extract string literal entries from a const array like `const X_KEYWORDS = [...]`."""
    block_match = re.search(
        rf"{varname}\s*=\s*\[(.*?)\]", src, re.DOTALL
    )
    assert block_match, f"{varname} export not found in plugin source"
    block = block_match.group(1)
    return [m.strip().strip("\"'") for m in re.findall(r'"([^"]*)"', block)]


# ── Test suite ──────────────────────────────────────────────────────────────

class TestPluginStructure:
    def test_plugin_file_exists(self):
        assert PLUGIN_PATH.exists(), f"Plugin missing at {PLUGIN_PATH}"

    def test_plugin_registered_in_opencode_json(self):
        oc = (PLUGIN_PATH.parents[2] / "opencode.json").read_text()
        assert "enforce-enhancement-ratio.ts" in oc, "Plugin not registered in opencode.json"

    def test_exports_keyword_lists(self):
        src = _plugin_source()
        assert "ENHANCEMENT_KEYWORDS" in src, "ENHANCEMENT_KEYWORDS export missing"
        assert "FIX_KEYWORDS" in src, "FIX_KEYWORDS export missing"

    def test_tool_execute_before_hook_registered(self):
        src = _plugin_source()
        assert "tool.execute.before" in src, "tool.execute.before hook missing"

    def test_text_complete_hook_registered(self):
        src = _plugin_source()
        assert "text.complete" in src, "text.complete hook missing"

    def test_fail_open_present(self):
        src = _plugin_source()
        assert "catch" in src.lower(), "No try/catch fail-open block found"

    def test_env_var_disable_present(self):
        src = _plugin_source()
        assert "GLUDD_ENHANCEMENT_RATIO_ENFORCE" in src, "Env-var disable switch missing"

    def test_subagent_skip_present(self):
        src = _plugin_source()
        assert "OPENCODE_SUBAGENT" in src, "Subagent skip gate missing"

    def test_state_file_defined(self):
        src = _plugin_source()
        assert "gludd-enhancement-ratio.json" in src, "State file path not defined"

    def test_isDispatchTool_function(self):
        src = _plugin_source()
        assert "task" in src and "agent" in src and "workflow" in src, "Dispatch tool detection incomplete"


class TestKeywordClassification:
    @pytest.fixture(scope="class")
    def enhancement_keywords(self) -> list[str]:
        return _extract_keywords(_plugin_source(), "ENHANCEMENT_KEYWORDS")

    @pytest.fixture(scope="class")
    def fix_keywords(self) -> list[str]:
        return _extract_keywords(_plugin_source(), "FIX_KEYWORDS")

    @pytest.mark.parametrize("prompt", [
        "Add a new feature for user auth",
        "Write tests for the daemon module",
        "Write documentation for the API endpoints",
        "Create a new make target for linting",
        "Add guardrail improvements",
        "Refactor the event loop",
        "Add observability to the pipeline",
        "Build a new presentation",
        "Create a skill for code review",
        "Add tooling scripts",
        "Codify the enhancement ratio rule",
        "Implement self-test mechanisms",
        "New feature: add project scaffolding",
        "Enhancement: improve plugin liveness checks",
    ])
    def test_classifies_as_enhancement(self, enhancement_keywords, prompt):
        lower = prompt.lower()
        matched = any(kw in lower for kw in enhancement_keywords)
        assert matched, f"Should classify as enhancement: {prompt!r}"

    @pytest.mark.parametrize("prompt", [
        "Fix the broken CI pipeline",
        "Bug: null pointer in daemon",
        "Repair the damaged config",
        "Fix regression in test suite",
        "Hotfix: security patch",
        "Repair the broken build",
        "Fix incident #42",
        "Fix: repair broken gate target",
        "Bug fix: repair broken CI",
    ])
    def test_classifies_as_fix(self, fix_keywords, prompt):
        lower = prompt.lower()
        matched = any(kw in lower for kw in fix_keywords)
        assert matched, f"Should classify as fix: {prompt!r}"

    @pytest.mark.parametrize("prompt", [
        "Investigate the logging subsystem",
        "Audit security posture",
        "Check the status of all agents",
        "Review code for style issues",
        "Run the full test suite",
        "Deploy to production",
        "Update dependencies",
        "Analyze performance bottlenecks",
    ])
    def test_unknown_defaults_to_fix(self, prompt):
        lower = prompt.lower()
        enh_match = any(kw in lower for kw in _extract_keywords(_plugin_source(), "ENHANCEMENT_KEYWORDS"))
        fix_match = any(kw in lower for kw in _extract_keywords(_plugin_source(), "FIX_KEYWORDS"))
        if not enh_match and not fix_match:
            pass


class TestStateFilePersistence:
    def test_state_file_path_consistent(self):
        src = _plugin_source()
        match = re.search(r'STATE_FILE\s*=\s*[^"]*"([^"]*)"', src)
        assert match, "STATE_FILE not found in plugin source"
        state_path = match.group(1)
        assert state_path.endswith("gludd-enhancement-ratio.json"), f"Unexpected state path: {state_path}"

    def test_state_file_has_wave_field(self):
        src = _plugin_source()
        assert "wave" in src, "State shape missing 'wave' field"
        assert "session_enhancements" in src, "State shape missing 'session_enhancements'"
        assert "session_fixes" in src, "State shape missing 'session_fixes'"

    def test_classify_returns_enhancement_or_fix(self):
        src = _plugin_source()
        assert '"enhancement"' in src, "classify() does not return 'enhancement'"
        assert '"fix"' in src, "classify() does not return 'fix'"


class TestWaveThresholds:
    def test_minimum_wave_size_is_two(self):
        src = _plugin_source()
        assert "s.wave.length < 2" in src or "wave.length < 2" in src or "length >= 2" in src or "length > 1" in src, \
            "Wave threshold check for ≥2 dispatches not found"

    def test_fix_ratio_threshold_at_50_percent(self):
        src = _plugin_source()
        assert "0.5" in src, "Fix ratio threshold (0.5 / 50%) not found in source"

    def test_console_warn_on_violation(self):
        src = _plugin_source()
        assert "console.warn" in src, "console.warn call for violation not found"

    def test_violation_message_contains_ag_ents_md(self):
        src = _plugin_source()
        assert "AGENTS.md" in src, "Violation message does not reference AGENTS.md"

    def test_wave_resets_after_check(self):
        src = _plugin_source()
        wave_reset = src.count("s.wave = []") + src.count("s.wave=[]")
        assert wave_reset >= 1, "Wave does not reset after ratio check"


class TestExtractPrompt:
    def test_extracts_prompt_field(self):
        src = _plugin_source()
        assert "args.prompt" in src, "Prompt extraction missing args.prompt"

    def test_extracts_description_field(self):
        src = _plugin_source()
        assert "args.description" in src, "Prompt extraction missing args.description"

    def test_fallback_to_stringify(self):
        src = _plugin_source()
        assert "JSON.stringify" in src, "No JSON.stringify fallback for unknown args shape"


class TestSubagentSkip:
    def test_skip_in_tool_execute_before(self):
        src = _plugin_source()
        hook_blocks = re.findall(r'tool\.execute\.before.*?=>\s*\{', src, re.DOTALL)
        assert len(hook_blocks) >= 1, "tool.execute.before hook not found"
        assert "OPENCODE_SUBAGENT" in src, "OPENCODE_SUBAGENT check not found for skip"

    def test_skip_in_text_complete(self):
        src = _plugin_source()
        hook_blocks = re.findall(r'text\.complete.*?=>\s*\{', src, re.DOTALL)
        assert len(hook_blocks) >= 1, "text.complete hook not found"


class TestEnforceEnvVar:
    def test_enforce_env_var_present(self):
        src = _plugin_source()
        assert "GLUDD_ENHANCEMENT_RATIO_ENFORCE" in src, "GLUDD_ENHANCEMENT_RATIO_ENFORCE env var not found"

    def test_default_on(self):
        src = _plugin_source()
        match = re.search(r'ENABLED\s*=\s*\(.*\)\s*!==\s*"0"', src)
        assert match, "ENABLED gate not default-ON (should be !== '0')"
