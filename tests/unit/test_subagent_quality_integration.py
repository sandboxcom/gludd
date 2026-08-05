"""Integration-quality tests for subagent fix-not-check rule enforcement.

AGENTS.md "Subagent Task Design — Fix, Don't Check" defines a table of
forbidden subagent task descriptions (dispatch prompt keywords). Any
match is a dispatch bug. This test verifies the rule is structurally
enforceable via enforce-deliverable.ts and its CHECK_ONLY_PATTERNS regex.

Covers:
  - Plugin file existence + structural properties
  - Regex literal extraction and keyword coverage
  - Every forbidden phrase from the AGENTS.md table matches the regex
  - Registration in opencode.json and plugin-hashes.json
  - Subagent guard, fail-open, env-var knob
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = ROOT / ".opencode" / "plugin"


def _read_plugin() -> str:
    path = PLUGIN_DIR / "enforce-deliverable.ts"
    assert path.exists(), f"Plugin not found: {path}"
    return path.read_text()


def _extract_regex() -> re.Pattern:
    src = _read_plugin()
    m = re.search(r"CHECK_ONLY_PATTERNS\s*=\s*/(.+?)/(\w*)", src, re.DOTALL)
    assert m is not None, "CHECK_ONLY_PATTERNS regex not found"
    flags = 0
    if "i" in m.group(2):
        flags |= re.IGNORECASE
    return re.compile(m.group(1), flags)


class TestPluginExistsAndRegistered:
    def test_file_exists(self):
        assert (PLUGIN_DIR / "enforce-deliverable.ts").exists()

    def test_registered_in_opencode_json(self):
        config = json.loads((ROOT / "opencode.json").read_text())
        plugins = config.get("plugin", [])
        assert any("enforce-deliverable.ts" in p for p in plugins), (
            "enforce-deliverable.ts not in opencode.json plugin list"
        )

    def test_registered_in_plugin_hashes(self):
        hashes = json.loads((ROOT / ".opencode" / "plugin-hashes.json").read_text())
        assert "enforce-deliverable.ts" in hashes, "enforce-deliverable.ts not in plugin-hashes.json"


class TestStructuralProperties:
    def test_subagent_guard_present(self):
        src = _read_plugin()
        guard = re.search(
            r"if\s*\(\s*isSubagent\(\)\s*\)\s*return",
            src,
        )
        assert guard is not None, "OPENCODE_SUBAGENT guard missing"

    def test_fail_open_comment_present(self):
        src = _read_plugin()
        assert "fail open" in src.lower(), "FAIL-OPEN comment missing"

    def test_env_var_knob_present(self):
        src = _read_plugin()
        assert "GLUDD_DELIVERABLE_ENFORCE" in src, "GLUDD_DELIVERABLE_ENFORCE env var check missing"

    def test_tool_execute_before_hook_exists(self):
        src = _read_plugin()
        assert '"tool.execute.before"' in src, "tool.execute.before hook missing"

    def test_is_dispatch_tool_defined(self):
        src = _read_plugin()
        assert "isDispatchTool" in src, "isDispatchTool function missing"

    def test_extract_prompt_defined(self):
        src = _read_plugin()
        assert "extractPrompt" in src, "extractPrompt function missing"

    def test_warning_not_blocking(self):
        src = _read_plugin()
        assert "console.warn" in src, "console.warn missing — plugin must warn, not block"
        assert "permissionDecision" not in src or '"deny"' not in src, "plugin MUST NOT block — warning-only"


class TestRegexKeywordCoverage:
    def test_action_verbs_present(self):
        src = _read_plugin()
        m = re.search(r"CHECK_ONLY_PATTERNS\s*=\s*/(.+?)/(\w*)", src, re.DOTALL)
        assert m is not None
        pattern_str = m.group(1).lower()
        for verb in [
            "check",
            "audit",
            "scan",
            "review",
            "survey",
            "report",
            "summarize",
            "run",
            "poll",
            "wait",
            "watch",
            "find",
            "read",
            "list",
        ]:
            assert verb in pattern_str, f"action verb '{verb}' missing from regex"

    def test_target_nouns_present(self):
        src = _read_plugin()
        m = re.search(r"CHECK_ONLY_PATTERNS\s*=\s*/(.+?)/(\w*)", src, re.DOTALL)
        assert m is not None
        pattern_str = m.group(1).lower()
        for noun in [
            "report",
            "summarize",
            "ci",
            "lint",
            "typecheck",
            "dead",
            "code",
            "dirty",
            "tree",
            "coverage",
            "secrets",
            "vulnerabilities",
            "status",
            "git",
            "type",
            "unused",
            "files",
            "completion",
            "imports",
        ]:
            assert noun in pattern_str, f"target noun '{noun}' missing from regex"

    def test_regex_is_case_insensitive(self):
        src = _read_plugin()
        assert re.search(r"CHECK_ONLY_PATTERNS\s*=\s*/.+/i", src) is not None, (
            "regex missing /i (case-insensitive) flag"
        )


class TestForbiddenPhrasesMatch:
    """Every forbidden phrase from AGENTS.md "Forbidden subagent task descriptions"
    table MUST match CHECK_ONLY_PATTERNS. If a phrase in the AGENTS.md table
    doesn't match, the rule is not mechanically enforceable."""

    regex = _extract_regex()

    def test_matches_check_ci_status(self):
        assert self.regex.search("check CI status")
        assert self.regex.search("check if CI is green")

    def test_matches_audit_lint(self):
        assert self.regex.search("audit lint")
        assert self.regex.search("run lint and report")

    def test_matches_check_type_errors(self):
        assert self.regex.search("check for type errors")
        assert self.regex.search("audit typecheck")

    def test_matches_check_dirty_tree(self):
        assert self.regex.search("check dirty tree")
        assert self.regex.search("check git status")

    def test_matches_scan_dead_code(self):
        assert self.regex.search("scan for dead code")
        assert self.regex.search("find unused imports")

    def test_matches_survey_test_coverage(self):
        assert self.regex.search("survey test coverage")
        assert self.regex.search("list uncovered files")

    def test_matches_review_and_report(self):
        assert self.regex.search("review and report")
        assert self.regex.search("read and summarize")

    def test_matches_check_for_secrets(self):
        assert self.regex.search("check for secrets")
        assert self.regex.search("audit for vulnerabilities")

    def test_matches_poll_until(self):
        assert self.regex.search("poll until CI green")

    def test_matches_wait_for(self):
        assert self.regex.search("wait for CI")

    def test_matches_watch_for(self):
        assert self.regex.search("watch for completion")

    def test_no_match_on_concrete_fix(self):
        assert self.regex.search("fix all lint errors") is None
        assert self.regex.search("write missing tests") is None
        assert self.regex.search("remove dead code found by vulture") is None
        assert self.regex.search("correct the type annotations") is None


class TestAgentsMdTablePresent:
    """Verify the AGENTS.md forbidden-phrases table still exists. If it is
    removed, this test is the structural pin that catches the regression."""

    def test_forbidden_phrase_table_exists(self):
        agents_md = (ROOT / "AGENTS.md").read_text()
        assert "check CI status" in agents_md, "'check CI status' missing from AGENTS.md forbidden phrase table"
        assert "audit lint" in agents_md, "'audit lint' missing from AGENTS.md forbidden phrase table"
        assert "scan for dead code" in agents_md, "'scan for dead code' missing from AGENTS.md forbidden phrase table"
        assert "survey test coverage" in agents_md, (
            "'survey test coverage' missing from AGENTS.md forbidden phrase table"
        )
        assert "review and report" in agents_md, "'review and report' missing from AGENTS.md forbidden phrase table"
        assert "check for secrets" in agents_md, "'check for secrets' missing from AGENTS.md forbidden phrase table"
        assert "poll until" in agents_md, "'poll until' missing from AGENTS.md forbidden phrase table"
        assert "wait for" in agents_md, "'wait for' missing from AGENTS.md forbidden phrase table"
        assert "watch for" in agents_md, "'watch for' missing from AGENTS.md forbidden phrase table"

    def test_subagent_task_design_section_exists(self):
        agents_md = (ROOT / "AGENTS.md").read_text()
        assert "Subagent Task Design — Fix, Don't Check" in agents_md, (
            "CRITICAL section 'Subagent Task Design — Fix, Don't Check' missing from AGENTS.md"
        )
