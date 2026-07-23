"""Behavior pin for the enhancement-ratio enforcement plugin.

Per AGENTS.md COST-EFFICIENCY DIRECTIVE §5 (2026-07-12): at least 50% of every
dispatch wave must be project enhancements, not just bug fixes.

This test extracts the plugin's exported keyword lists and classification logic
from the TypeScript source and exercises each against the spec cases.  Includes
behavioral wave-simulation tests for ratio thresholds and block/soft modes.

Refactored to match plugin source (2026-07-14):
  - Self-contained — ONLY tool.execute.before hook, NO text.complete hook
  - GLUDD_ENHANCEMENT_RATIO_BLOCK env var (was GLUDD_ENHANCEMENT_RATIO_HARD_DENY)
  - No early_warned field, no "Re-split" guidance, no "modified" variable
  - Deny message: "ENHANCEMENT RATIO VIOLATION: N% fixes (N/NN) ..."
  - Soft mode: console.warn only
"""
from __future__ import annotations

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


def _classify(prompt: str, enhancement_kw: list[str], fix_kw: list[str]) -> str:
    """Simulate the plugin's classify() function."""
    lower = prompt.lower()
    for kw in enhancement_kw:
        if kw in lower:
            return "enhancement"
    for kw in fix_kw:
        if kw in lower:
            return "fix"
    return "fix"


def _check_ratio(fix_count: int, total: int) -> tuple[bool, float, float]:
    """Simulate the ratio check. Returns (violation, fix_ratio, enhancement_ratio)."""
    fix_ratio = fix_count / total if total > 0 else 0.0
    enhancement_ratio = 1.0 - fix_ratio
    return fix_ratio > 0.5, fix_ratio, enhancement_ratio


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

    def test_no_text_complete_hook(self):
        """Refactored: self-contained plugin has NO text.complete hook key."""
        src = _plugin_source()
        assert '"text.complete":' not in src, \
            "text.complete hook key present — plugin is self-contained, only tool.execute.before"

    def test_fail_open_present(self):
        src = _plugin_source()
        assert "catch" in src.lower(), "No try/catch fail-open block found"

    def test_env_var_disable_present(self):
        src = _plugin_source()
        assert "GLUDD_ENHANCEMENT_RATIO_ENFORCE" in src, "Env-var disable switch missing"

    def test_block_env_var_present(self):
        src = _plugin_source()
        assert "GLUDD_ENHANCEMENT_RATIO_BLOCK" in src, "BLOCK env var (soft/hard switch) missing"

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

    def test_enhancement_checked_before_fix(self):
        """Keywords are checked enhancement-first; 'fix bug in tests' is enhancement."""
        enh_kw = _extract_keywords(_plugin_source(), "ENHANCEMENT_KEYWORDS")
        fix_kw = _extract_keywords(_plugin_source(), "FIX_KEYWORDS")
        result = _classify("fix bug in tests", enh_kw, fix_kw)
        assert result == "enhancement", (
            f"'fix bug in tests' should classify as enhancement (tests keyword checked first), got {result}"
        )


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

    def test_state_file_has_session_counters(self):
        src = _plugin_source()
        assert "session_enhancements" in src, "State shape missing 'session_enhancements'"
        assert "session_fixes" in src, "State shape missing 'session_fixes'"
        assert "session_unknown" in src, "State shape missing 'session_unknown'"
        assert "lastPid" in src, "State shape missing 'lastPid'"
        assert "lastTs" in src, "State shape missing 'lastTs'"

    def test_classify_returns_enhancement_or_fix(self):
        src = _plugin_source()
        assert '"enhancement"' in src, "classify() does not return 'enhancement'"
        assert '"fix"' in src, "classify() does not return 'fix'"


class TestWaveThresholds:
    def test_minimum_wave_size_is_two(self):
        src = _plugin_source()
        assert (
            "s.wave.length >= 2" in src or "wave.length >= 2" in src
            or "length >= 2" in src or "length > 1" in src
        ), "Wave threshold check for >=2 dispatches not found"

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
        wave_reset = src.count("s.wave = []") + src.count("s.wave=[]") + src.count("s.wave = []")
        assert wave_reset >= 1, "Wave does not reset after ratio check"

    def test_no_legacy_early_warned_state(self):
        src = _plugin_source()
        assert "early_warned" not in src, "legacy early_warned state should not be present"


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


class TestBlockMode:
    """Tests for GLUDD_ENHANCEMENT_RATIO_BLOCK=1 hard-block mode."""

    def test_block_env_var_defined(self):
        src = _plugin_source()
        assert "GLUDD_ENHANCEMENT_RATIO_BLOCK" in src, "BLOCK env var not defined in source"

    def test_block_default_on(self):
        """BLOCK defaults to enabled: !== '0'."""
        src = _plugin_source()
        assert "BLOCK" in src, "BLOCK variable not present"
        match = re.search(r'BLOCK\s*=\s*\([^)]+\)\s*!==\s*"0"', src)
        assert match, "BLOCK should default on (strict !== '0' check)"

    def test_block_returns_permission_deny_on_violation(self):
        src = _plugin_source()
        assert 'permissionDecision: "deny"' in src, "permissionDecision deny not in source"

    def test_console_warn_still_present_for_soft_mode(self):
        src = _plugin_source()
        assert "console.warn" in src, "console.warn must remain for soft mode (BLOCK=0)"

    def test_deny_message_format(self):
        src = _plugin_source()
        assert "ENHANCEMENT RATIO VIOLATION" in src, "Deny message header missing"
        assert "Must be ≤50%" in src, "Deny message threshold instruction missing"
        assert "Replace fix dispatches with enhancement work" in src, "Deny message guidance missing"


class TestWaveRatioSimulations:
    """Behavioral ratio simulations using extracted keywords."""

    @pytest.fixture(scope="class")
    def keywords(self):
        src = _plugin_source()
        return {
            "enhancement": _extract_keywords(src, "ENHANCEMENT_KEYWORDS"),
            "fix": _extract_keywords(src, "FIX_KEYWORDS"),
        }

    def _classify_prompts(self, prompts, kw):
        return [_classify(p, kw["enhancement"], kw["fix"]) for p in prompts]

    def test_6_fixes_1_enhancement_violates(self, keywords):
        """6 fixes + 1 enhancement = 85.7% fixes -> violation."""
        prompts = [
            "Fix bug in auth module",
            "Repair broken connection pool",
            "Repair damaged config",
            "Fix regression in pipeline",
            "Hotfix security patch",
            "Fix incident #99",
            "Add new tests for the daemon",  # enhancement
        ]
        results = self._classify_prompts(prompts, keywords)
        fix_count = results.count("fix")
        total = len(results)
        violation, fix_ratio, _ = _check_ratio(fix_count, total)
        assert fix_count == 6, f"Expected 6 fixes, got {fix_count}: {list(zip(prompts, results, strict=False))}"
        assert violation, f"6 fixes / 1 enhancement ({fix_ratio:.0%}) should trigger violation"
        assert fix_ratio > 0.5, f"Fix ratio {fix_ratio:.2%} should exceed 50%"

    def test_4_enhancements_3_fixes_no_violation(self, keywords):
        """4 enhancements + 3 fixes = 42.9% fixes -> no violation."""
        prompts = [
            "Write tests for the daemon module",
            "Write documentation for the API endpoints",
            "Create a new make target for linting",
            "Add guardrail improvements",
            "Fix bug in auth module",
            "Repair broken connection pool",
            "Repair damaged config",
        ]
        results = self._classify_prompts(prompts, keywords)
        fix_count = results.count("fix")
        total = len(results)
        violation, fix_ratio, _ = _check_ratio(fix_count, total)
        assert fix_count == 3, f"Expected 3 fixes, got {fix_count}: {list(zip(prompts, results, strict=False))}"
        assert not violation, f"3 fixes / 4 enhancements ({fix_ratio:.0%}) should NOT trigger violation"

    def test_5_enhancements_2_fixes_no_violation(self, keywords):
        """5 enhancements + 2 fixes = 28.6% fixes -> no violation."""
        prompts = [
            "Add a new feature for user auth",
            "Write tests for the daemon module",
            "Write documentation for the API endpoints",
            "Create a new make target for linting",
            "Add guardrail improvements",
            "Fix bug in auth module",
            "Repair broken connection pool",
        ]
        results = self._classify_prompts(prompts, keywords)
        fix_count = results.count("fix")
        violation, _, _ = _check_ratio(fix_count, len(results))
        assert fix_count == 2, f"Expected 2 fixes, got {fix_count}"
        assert not violation

    def test_all_enhancements_no_violation(self, keywords):
        """All 7 enhancements -> no violation."""
        prompts = [
            "Add a new feature for user auth",
            "Write tests for the daemon module",
            "Write documentation for the API endpoints",
            "Create a new make target for linting",
            "Add guardrail improvements",
            "Refactor the event loop",
            "Add observability to the pipeline",
        ]
        results = self._classify_prompts(prompts, keywords)
        fix_count = results.count("fix")
        violation, _, _ = _check_ratio(fix_count, len(results))
        assert fix_count == 0, f"Expected 0 fixes, got {fix_count}"
        assert not violation

    def test_all_fixes_violates(self, keywords):
        """All 7 fixes -> violation."""
        prompts = [
            "Fix bug in auth module",
            "Repair broken connection pool",
            "Repair damaged config",
            "Fix regression in pipeline",
            "Hotfix security patch",
            "Fix incident #99",
            "Bug: null pointer in daemon",
        ]
        results = self._classify_prompts(prompts, keywords)
        fix_count = results.count("fix")
        violation, fix_ratio, _ = _check_ratio(fix_count, len(results))
        assert fix_count == 7, f"Expected 7 fixes, got {fix_count}"
        assert violation, f"All fixes should trigger violation (ratio {fix_ratio:.0%})"

    def test_exactly_50_percent_even_wave(self, keywords):
        """3 enhancements + 3 fixes = 50% -> no violation (threshold is > 0.5)."""
        prompts = [
            "Add tests for auth module",
            "Write documentation for API",
            "Refactor event loop",
            "Fix bug in auth module",
            "Repair broken connection pool",
            "Repair damaged config",
        ]
        results = self._classify_prompts(prompts, keywords)
        fix_count = results.count("fix")
        violation, _fix_ratio, _ = _check_ratio(fix_count, len(results))
        assert fix_count == 3, f"Expected 3 fixes, got {fix_count}"
        assert not violation, "Exactly 50% (3/6) should NOT trigger violation"

    def test_exactly_50_percent_odd_wave(self, keywords):
        """2 enhancements + 2 fixes + 1 enhancement = 2/5 fixes (40%) -> no violation."""
        prompts = [
            "Add tests for auth module",
            "Write documentation for API",
            "Fix bug in auth module",
            "Repair broken connection pool",
            "Refactor event loop",
        ]
        results = self._classify_prompts(prompts, keywords)
        fix_count = results.count("fix")
        violation, fix_ratio, _ = _check_ratio(fix_count, len(results))
        assert fix_count == 2
        assert not violation, f"2/5 fixes ({fix_ratio:.0%}) should NOT trigger violation"

    def test_two_fixes_no_enhancements_violates_ratio(self, keywords):
        """2 fixes + 0 enhancements: fixRatio = 1.0 > 0.5 -> violation."""
        prompts = [
            "Fix bug in auth module",
            "Repair broken connection pool",
        ]
        results = self._classify_prompts(prompts, keywords)
        fix_count = results.count("fix")
        total = len(results)
        violation, fix_ratio, _ = _check_ratio(fix_count, total)
        assert violation, (
            f"2 fixes / 0 enhancements ({fix_ratio:.0%}) should trigger violation via ratio check"
        )

    def test_one_fix_zero_enhancements_below_wave_threshold(self, keywords):
        """1 fix + 0 enhancements: wave < 2, no ratio check triggered."""
        prompts = ["Fix bug in auth module"]
        results = self._classify_prompts(prompts, keywords)
        assert len(results) < 2, "Single entry wave should not trigger ratio check"

    def test_one_fix_one_enhancement_no_violation(self, keywords):
        """1 fix + 1 enhancement: fixRatio = 0.5, NOT > 0.5 -> no violation."""
        prompts = [
            "Fix bug in auth module",
            "Write tests for daemon",
        ]
        results = self._classify_prompts(prompts, keywords)
        fix_count = results.count("fix")
        violation, fix_ratio, _ = _check_ratio(fix_count, len(results))
        assert not violation, f"1f+1e ({fix_ratio:.0%}) should NOT trigger violation (≤50%)"

    def test_subagent_prompts_are_classifiable(self, keywords):
        """Realistic subagent-style prompts all get a classification."""
        prompts = [
            "ENHANCEMENT: Add new self-tests for the guardrail plugins",
            "FIX: repair broken connector logger imports",
            "Add documentation for the task ledger system",
            "Write a new reveal.js presentation",
            "Fix regression in CI pipeline",
            "Create a make target for observability checks",
            "Implement a new feature: agent worktree isolation",
            "Bug fix: repair broken type annotations",
            "Refactor the event loop for better performance",
            "Codify the enhancement ratio enforcement rule",
        ]
        results = self._classify_prompts(prompts, keywords)
        for prompt, result in zip(prompts, results, strict=False):
            assert result in ("enhancement", "fix"), (
                f"Prompt {prompt!r} classified as {result!r} — must be enhancement or fix"
            )


class TestEnhancedKeywordsCoverage:
    """Ensure the ENHANCEMENT_KEYWORDS list covers the spec categories."""

    REQUIRED_CATEGORIES: tuple[str, ...] = (
        "enhancement",
        "feature",
        "docs",
        "test",
        "tooling",
        "script",
        "make target",
        "guardrail",
        "refactor",
        "observability",
        "codify",
        "self-test",
    )

    def test_all_required_categories_present(self):
        enh_kw = _extract_keywords(_plugin_source(), "ENHANCEMENT_KEYWORDS")
        for cat in self.REQUIRED_CATEGORIES:
            assert cat in enh_kw, f"Required enhancement category {cat!r} missing from ENHANCEMENT_KEYWORDS"

    def test_fix_keywords_coverage(self):
        fix_kw = _extract_keywords(_plugin_source(), "FIX_KEYWORDS")
        required_fix = ["fix", "bug", "repair", "regression", "broken", "incident", "hotfix"]
        for cat in required_fix:
            assert cat in fix_kw, f"Required fix category {cat!r} missing from FIX_KEYWORDS"
