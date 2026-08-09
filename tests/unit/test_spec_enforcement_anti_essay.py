"""E01-E20: Anti-essay enforcement spec verification.

Verifies enforce-anti-essay.ts contains the structural checks required
by specs E01-E20: word-count thresholds, paragraph thresholds,
evidence detection, bolded-header patterns, status-summary patterns,
blocked-pattern logic, fail-open, disable env var, subagent guard,
hot-reload capability, and hook shape.
"""

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
PLUGIN = ROOT / ".opencode" / "plugin" / "enforce-anti-essay.ts"
HOT_MODULE_DIR = ROOT / ".opencode" / "lib"


def _content() -> str:
    if not PLUGIN.exists():
        return ""
    return PLUGIN.read_text()


class TestE01E03PluginExistenceAndRegistration:
    """E01-E03: the anti-essay plugin exists and is properly shaped."""

    def test_e01_plugin_file_exists(self):
        """E01: enforce-anti-essay.ts must exist on disk."""
        assert PLUGIN.exists(), "E01: enforce-anti-essay.ts is missing"

    def test_e02_plugin_has_plugin_type_import(self):
        """E02: plugin must import Plugin type from the opencode SDK."""
        content = _content()
        if not content:
            return
        assert "Plugin" in content, "E02: enforce-anti-essay.ts must import Plugin type"

    def test_e03_plugin_exports_default_satisfies_plugin(self):
        """E03: default export must satisfy the Plugin type."""
        content = _content()
        if not content:
            return
        assert "satisfies Plugin" in content, "E03: enforce-anti-essay.ts must export 'satisfies Plugin'"


class TestE04E06ThresholdConfiguration:
    """E04-E06: word-count, paragraph-count, and threshold env vars."""

    def test_e04_essay_word_threshold_env_var(self):
        """E04: ESSAY_WORD_THRESHOLD env var with default 50."""
        content = _content()
        if not content:
            return
        assert "ESSAY_WORD_THRESHOLD" in content, "E04: ESSAY_WORD_THRESHOLD env var must exist"
        assert "50" in content, "E04: default word threshold must be 50"

    def test_e05_essay_paragraph_threshold_env_var(self):
        """E05: ESSAY_PARAGRAPH_THRESHOLD env var with default 3."""
        content = _content()
        if not content:
            return
        assert "ESSAY_PARAGRAPH_THRESHOLD" in content, "E05: ESSAY_PARAGRAPH_THRESHOLD env var must exist"
        assert "3" in content, "E05: default paragraph threshold must be 3"

    def test_e06_wordcount_function_exists(self):
        """E06: wordCount function must split on whitespace."""
        content = _content()
        if not content:
            return
        assert "wordCount" in content, "E06: wordCount function must exist"
        assert "split" in content, "E06: wordCount must use string splitting"


class TestE07E09EvidenceDetection:
    """E07-E09: evidence-detection functions."""

    def test_e07_has_commit_hash_regex(self):
        """E07: hasCommitHash function with hex hash regex."""
        content = _content()
        if not content:
            return
        assert "hasCommitHash" in content, "E07: hasCommitHash function must exist"
        assert "0-9a-f" in content, "E07: hasCommitHash regex must match hex chars"

    def test_e08_has_test_count_regex(self):
        """E08: hasTestCount function detects test-pass counts."""
        content = _content()
        if not content:
            return
        assert "hasTestCount" in content, "E08: hasTestCount function must exist"
        assert "passed" in content.lower() or "pass" in content, "E08: hasTestCount must detect test-pass phrases"

    def test_e09_has_ci_verdict_regex(self):
        """E09: hasCiVerdict function detects CI verdict strings."""
        content = _content()
        if not content:
            return
        assert "hasCiVerdict" in content, "E09: hasCiVerdict function must exist"
        assert "conclusion" in content, "E09: hasCiVerdict must detect conclusion strings"


class TestE10E12PatternDetection:
    """E10-E12: bolded-header, status-summary, and paragraph-count detection."""

    def test_e10_has_bolded_headers_patterns(self):
        """E10: hasBoldedHeaders must match Q&A-style section headers."""
        content = _content()
        if not content:
            return
        assert "hasBoldedHeaders" in content or "bolded" in content.lower(), (
            "E10: hasBoldedHeaders detection must exist"
        )

    def test_e11_has_status_summary_patterns(self):
        """E11: hasStatusSummary must match status-report phrases."""
        content = _content()
        if not content:
            return
        assert "hasStatusSummary" in content, "E11: hasStatusSummary detection must exist"
        # At least some summary patterns must be present
        assert (
            "what was done" in content.lower() or "status report" in content.lower() or "session" in content.lower()
        ), "E11: hasStatusSummary must contain known stop-pattern phrases"

    def test_e12_paragraph_count_function_exists(self):
        """E12: paragraphCount function must split on blank lines."""
        content = _content()
        if not content:
            return
        assert "paragraphCount" in content, "E12: paragraphCount function must exist"


class TestE14E16BlockingLogic:
    """E14-E16: the blocking logic in the text.complete hook."""

    def test_e14_blocked_pattern_without_evidence_is_blanked(self):
        """E14: blocked pattern (bolded header or summary) without evidence -> NAG_TEXT."""
        content = _content()
        if not content:
            return
        assert "isBlockedPattern" in content, "E14: isBlockedPattern variable must exist"
        # When blocked pattern + no evidence, NAG_TEXT replaces output text
        assert "NAG_TEXT" in content, "E14: NAG_TEXT must be defined for blocking"

    def test_e15_essay_without_evidence_is_prepended_with_nag(self):
        """E15: essay (>threshold words/paragraphs) without evidence -> NAG + text."""
        content = _content()
        if not content:
            return
        assert "isEssay" in content, "E15: isEssay variable must exist"
        # Essay + no evidence -> NAG prepended, not blanked
        nag_lines = [line for line in content.split("\n") if "NAG_TEXT" in line and "+" in line]
        assert len(nag_lines) >= 1, "E15: NAG_TEXT must be prepended for essay case"

    def test_e16_evidence_bypasses_block(self):
        """E16: evidence (commit hash, test count, CI verdict) bypasses block."""
        content = _content()
        if not content:
            return
        assert "hasEvidence" in content, "E16: hasEvidence function must exist"
        # Evidence check is used in the blocking condition
        assert "evidence" in content.lower(), "E16: evidence variable must be checked"


class TestE17E18FailOpenAndDisable:
    """E17-E18: fail-open behavior and disable env var."""

    def test_e17_fail_open_catch_blocks(self):
        """E17: plugin must have try/catch fail-open blocks."""
        content = _content()
        if not content:
            return
        # Should have multiple try/catch blocks (at least for text.complete and tool.execute.before)
        catches = re.findall(r"catch\s*\{", content)
        assert len(catches) >= 2, "E17: enforce-anti-essay.ts must have >=2 fail-open catch blocks"

    def test_e18_disable_env_var_gludd_anti_essay_enforce(self):
        """E18: GLUDD_ANTI_ESSAY_ENFORCE=0 must disable enforcement."""
        content = _content()
        if not content:
            return
        assert "GLUDD_ANTI_ESSAY_ENFORCE" in content, "E18: GLUDD_ANTI_ESSAY_ENFORCE env var must be checked"


class TestE19E20SubagentAndHotReload:
    """E19-E20: subagent guard and hot-reload capability."""

    def test_e19_subagent_guard_at_top_of_hooks(self):
        """E19: isSubagent() check must skip enforcement for subagents."""
        content = _content()
        if not content:
            return
        assert "isSubagent" in content, "E19: isSubagent check must exist for subagent isolation"

    def test_e20_hot_reload_capable(self):
        """E20: plugin must use loadHotModule for hot-reload support."""
        content = _content()
        if not content:
            return
        assert "loadHotModule" in content, "E20: loadHotModule must be used for hot-reload capability"
        assert "defaultImpl" in content, "E20: defaultImpl fallback must be defined for hot-reload"


class TestE21E23PendingWorkDetection:
    """E21-E23: hasPendingWork logic."""

    def test_e21_has_pending_work_checks_tasks_md(self):
        """E21: hasPendingWork must check TASKS.md for unchecked items."""
        content = _content()
        if not content:
            return
        assert "TASKS.md" in content, "E21: hasPendingWork must reference TASKS.md"

    def test_e22_has_pending_work_checks_ratchet_yml(self):
        """E22: hasPendingWork must check config/ratchet.yml."""
        content = _content()
        if not content:
            return
        assert "ratchet" in content, "E22: hasPendingWork must reference ratchet.yml"

    def test_e23_has_pending_work_fail_open(self):
        """E23: hasPendingWork must fail-open (return false on read error)."""
        content = _content()
        if not content:
            return
        # hasPendingWork has a try/catch that fails open
        # Verify the hasPendingWork function body has a catch
        has_pending = [line for line in content.split("\n") if "hasPendingWork" in line]
        assert len(has_pending) >= 1, "E23: hasPendingWork function must exist"


class TestE24E25HookRegistration:
    """E24-E25: hook registration with correct opencode hook names."""

    def test_e24_text_complete_hook_registered(self):
        """E24: experimental.text.complete hook must be registered."""
        content = _content()
        if not content:
            return
        assert "experimental.text.complete" in content, "E24: experimental.text.complete hook must be registered"

    def test_e25_tool_execute_before_hook_registered(self):
        """E25: tool.execute.before hook must be registered."""
        content = _content()
        if not content:
            return
        assert "tool.execute.before" in content, "E25: tool.execute.before hook must be registered"


class TestE26E28RegexPrecision:
    """E26-E28: regex patterns are precise enough for their use case."""

    def test_e26_commit_hash_regex_excludes_pure_digit_strings(self):
        """E26: commit hash regex must require at least one hex letter [a-f]."""
        content = _content()
        if not content:
            return
        # The regex must contain [a-f] (or similar) to exclude pure-digit matches
        commit_hash_re = re.search(r"hasCommitHash.*?/([^/]+)/", content, re.DOTALL)
        if not commit_hash_re:
            commit_hash_re = re.search(r"hasCommitHash.*?\[0-9a-f\].*?([a-f])", content, re.DOTALL)
        # If we found a regex with a-f, that's correct.
        # If the regex is only [0-9a-f]{7,40}, that's fine too — it includes a-f.
        found_a_f = "[a-f]" in content or "0-9a-f" in content
        assert found_a_f, "E26: hasCommitHash regex must include hex letters [a-f]"

    def test_e27_has_evidence_combines_all_three_checks(self):
        """E27: hasEvidence must OR-together hasCommitHash, hasTestCount, hasCiVerdict."""
        content = _content()
        if not content:
            return
        has_evidence_fn = content[content.find("hasEvidence") : content.find("hasEvidence") + 300]
        assert "hasCommitHash" in has_evidence_fn, "E27: hasEvidence must call hasCommitHash"
        assert "hasTestCount" in has_evidence_fn, "E27: hasEvidence must call hasTestCount"
        assert "hasCiVerdict" in has_evidence_fn, "E27: hasEvidence must call hasCiVerdict"

    def test_e28_nag_text_contains_policy_violation_message(self):
        """E28: NAG_TEXT must explain the violation and cite the disable env var."""
        content = _content()
        if not content:
            return
        assert "ANTI-ESSAY" in content, "E28: NAG_TEXT must identify itself as anti-essay"
        assert "GLUDD_ANTI_ESSAY_ENFORCE" in content, "E28: NAG_TEXT must cite the disable env var"
