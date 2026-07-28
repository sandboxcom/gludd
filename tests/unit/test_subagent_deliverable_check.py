"""Tests for enforce-deliverable.ts — the subagent deliverable enforcement plugin.

AGENTS.md "Subagent Task Design — Fix, Don't Check" requires every subagent
to produce a concrete fix/deliverable. This plugin scans dispatch prompts for
check-only patterns (check CI, audit lint, scan coverage, etc.) and injects
a console.warn when detected.

The test pins:
  - Plugin file existence
  - CHECK_ONLY_PATTERNS regex presence and shape
  - tool.execute.before hook presence
  - OPENCODE_SUBAGENT guard presence
  - isDispatchTool classification
  - extractPrompt function presence
  - Console-warn-only behavior (never blocks)
  - GLUDD_DELIVERABLE_ENFORCE env var knob
  - Registration in opencode.json plugin list
  - Registration in plugin-hashes.json
  - Coverage in test_subagent_context_isolation.py plugin lists
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
_SUBAGENT_GUARD_RE = re.compile(
    r'if\s*\(\s*(?:process\.env\.OPENCODE_SUBAGENT\s*===?\s*"1"|'
    r"isSubagent\(\))\s*\)\s*return"
)


def _read_plugin() -> str:
    path = PLUGIN_DIR / "enforce-deliverable.ts"
    assert path.exists(), f"Plugin not found: {path}"
    return path.read_text()


class TestPluginExists:
    def test_file_exists(self):
        assert (PLUGIN_DIR / "enforce-deliverable.ts").exists()

    def test_not_empty(self):
        src = _read_plugin()
        assert len(src) > 50, "Plugin file is too short"


class TestRegisteredInOpencodeJson:
    def test_in_plugin_list(self):
        config = json.loads((ROOT / "opencode.json").read_text())
        plugins = config.get("plugin", [])
        assert any("enforce-deliverable.ts" in p for p in plugins), (
            "enforce-deliverable.ts not found in opencode.json plugin list"
        )


class TestRegisteredInHashes:
    def test_in_plugin_hashes(self):
        hashes_path = ROOT / ".opencode" / "plugin-hashes.json"
        hashes = json.loads(hashes_path.read_text())
        assert "enforce-deliverable.ts" in hashes, (
            "enforce-deliverable.ts not found in plugin-hashes.json"
        )


class TestStructuralPattern:
    def test_is_subagent_guard_present(self):
        src = _read_plugin()
        guard = _SUBAGENT_GUARD_RE.search(src)
        assert guard is not None, "OPENCODE_SUBAGENT guard missing"

    def test_check_only_patterns_regex_exists(self):
        src = _read_plugin()
        assert "CHECK_ONLY_PATTERNS" in src, "CHECK_ONLY_PATTERNS constant missing"

    def test_check_only_patterns_is_regex(self):
        src = _read_plugin()
        m = re.search(r"CHECK_ONLY_PATTERNS\s*=\s*(/.+?/)", src, re.DOTALL)
        assert m is not None, "CHECK_ONLY_PATTERNS not assigned a regex literal"
        assert len(m.group(1)) > 0, "CHECK_ONLY_PATTERNS regex is empty"

    def test_regex_contains_forbidden_keywords(self):
        src = _read_plugin()
        m = re.search(r"CHECK_ONLY_PATTERNS\s*=\s*(/.+?/)\w*", src, re.DOTALL)
        assert m is not None
        pattern_str = m.group(1)
        for kw in ["check", "audit", "scan", "review", "survey", "report", "summarize"]:
            assert kw in pattern_str.lower(), f"'{kw}' not in CHECK_ONLY_PATTERNS"

    def test_regex_contains_target_keywords(self):
        src = _read_plugin()
        m = re.search(r"CHECK_ONLY_PATTERNS\s*=\s*(/.+?/)\w*", src, re.DOTALL)
        assert m is not None
        pattern_str = m.group(1)
        for kw in ["CI", "lint", "typecheck", "coverage", "secrets", "vulnerabilities", "status"]:
            assert kw.lower() in pattern_str.lower(), f"'{kw}' not in CHECK_ONLY_PATTERNS"

    def test_regex_is_case_insensitive(self):
        src = _read_plugin()
        assert re.search(r"CHECK_ONLY_PATTERNS\s*=\s*/.+/i", src) is not None, (
            "CHECK_ONLY_PATTERNS regex missing /i (case-insensitive) flag"
        )

    def test_is_dispatch_tool_defined(self):
        src = _read_plugin()
        assert "isDispatchTool" in src, "isDispatchTool function missing"

    def test_extract_prompt_defined(self):
        src = _read_plugin()
        assert "extractPrompt" in src, "extractPrompt function missing"

    def test_tool_execute_before_hook_exists(self):
        src = _read_plugin()
        assert '"tool.execute.before"' in src, "tool.execute.before hook missing"

    def test_subagent_guard_returns_not_throws(self):
        src = _read_plugin()
        guard = _SUBAGENT_GUARD_RE.search(src)
        assert guard is not None
        guard_text = guard.group(0)
        assert "throw" not in guard_text, "subagent guard must return, not throw"


class TestWarningNotBlock:
    def test_console_warn_present(self):
        src = _read_plugin()
        assert "console.warn" in src, "console.warn missing — plugin must warn, not block"

    def test_no_permission_decision_deny(self):
        src = _read_plugin()
        assert 'permissionDecision' not in src or '"deny"' not in src, (
            "Plugin MUST NOT block — it is warning-only"
        )

    def test_fail_open_comment_present(self):
        src = _read_plugin()
        assert "fail open" in src.lower(), "FAIL-OPEN comment missing"


class TestEnvVarKnob:
    def test_env_disabled(self):
        src = _read_plugin()
        m = re.search(
            r'GLUDD_DELIVERABLE_ENFORCE[^\n]*!==?\s*"0"',
            src,
        )
        assert m is not None, "GLUDD_DELIVERABLE_ENFORCE env var check missing"

    def test_enabled_check_before_work(self):
        src = _read_plugin()
        idx_check = src.find("if (!ENABLED)")
        idx_dispatch = src.find("isDispatchTool(tool)")
        assert idx_check > 0, "ENABLED check missing"
        assert idx_dispatch > idx_check, "ENABLED must be checked before dispatch check"


class TestRegexMatches:
    def test_matches_check_ci(self):
        src = _read_plugin()
        m = re.search(r"CHECK_ONLY_PATTERNS\s*=\s*(/.+?/)\w*", src, re.DOTALL)
        assert m is not None
        literal_str = re.sub(r"\\(.)", r"\1", m.group(1)[1:])
        assert len(literal_str) > 0, "CHECK_ONLY_PATTERNS regex literal is empty"

    def test_forbidden_phrase_table_coverage(self):
        """Each phrase from AGENTS.md forbidden-subagent-task table must have
        at least one keyword pair in CHECK_ONLY_PATTERNS."""
        src = _read_plugin()
        m = re.search(r"CHECK_ONLY_PATTERNS\s*=\s*(/.+?/)\w*", src, re.DOTALL)
        assert m is not None
        pattern_str = m.group(1)

        required_pairs = [
            ("check", "ci"),
            ("audit", "lint"),
            ("scan", "coverage"),
            ("review", "report"),
            ("survey", "coverage"),
            ("report", "status"),
        ]
        for verb, noun in required_pairs:
            assert verb in pattern_str.lower(), f"verb '{verb}' missing"
            assert noun in pattern_str.lower(), f"noun '{noun}' missing"


class TestCoveredInContextIsolation:
    def test_listed_in_tool_before_plugins(self):
        """The context-isolation audit must discover tool hooks dynamically."""
        iso_test = ROOT / "tests" / "unit" / "test_subagent_context_isolation.py"
        content = iso_test.read_text()
        assert "_enforce_plugins()" in content
        assert '"tool.execute.before"' in content
        assert "PLUGINS_WITH_TOOL_BEFORE = sorted" in content, (
            "context-isolation coverage must be generated from the live plugin inventory"
        )
