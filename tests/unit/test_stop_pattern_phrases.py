"""P5 audit fix: stop-pattern phrase detection in enforce-stop.ts.

The multitasking audit flagged that three classic permission-seeking
deferral phrases were missing from enforce-stop.ts stop detection:

  - "Shall I continue?"
  - "Should I proceed?"
  - "Want me to ...?"

AGENTS.md "Anti-Stop Patterns" lists each as a hard policy violation:
  - Asking "Should I continue?" when there are clearly pending tasks
  - Listing findings/gaps/audit results and asking "Want me to start building?"
  - Listing remaining tasks and asking "Want me to proceed?" or "What priority?"

These are the "ask permission, then stop" anti-pattern. They differ from
the completion-claim patterns (✅ / Done.) already in responseLooksTerminal:
the agent is not claiming work is finished, it is asking the user to green-
light the next step before doing it. Both are premature stops.

This test pins the new STOP_PATTERN_PHRASES regex in enforce-stop.ts. The
plugin MUST:
  1. Define a STOP_PATTERN_PHRASES regex with all three phrases.
  2. Wire it into the text.complete hook so a response containing one of
     these phrases AND no machine evidence is blocked.

Note: enforce-stop.ts is intentionally a LEAN plugin — NO_WAIT_PATTERNS
and CONSTRAINT_AS_STOP_PATTERNS were removed (see test_plugin_behavior.py
TestEnforceStopNoWaitPatterns / TestEnforceStopConstraintPatterns).
STOP_PATTERN_PHRASES is the narrow replacement: exactly the three audit-
flagged phrases, not a sprawling vocabulary list.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
PLUGIN = ROOT / ".opencode" / "plugin" / "enforce-stop.ts"


def _src() -> str:
    return PLUGIN.read_text()


class TestStopPatternPhrasesDefined:
    """The plugin must define STOP_PATTERN_PHRASES and include all three."""

    def test_constant_exists(self):
        assert "STOP_PATTERN_PHRASES" in _src(), (
            "enforce-stop.ts must define STOP_PATTERN_PHRASES — the audit "
            "flagged 'Shall I continue?', 'Should I proceed?', 'Want me to "
            "...?' as missing from stop detection."
        )

    def _extract_regex_body(self) -> str:
        src = _src()
        m = re.search(r"STOP_PATTERN_PHRASES\s*=\s*/([^/\n]+)/", src)
        assert m, "STOP_PATTERN_PHRASES regex literal not found"
        # The regex body contains literal tokens ("shall", "continue", etc.)
        # separated by `\s+` whitespace syntax. Token substring checks work
        # directly against the raw body — no normalization needed.
        return m.group(1).lower()

    def test_includes_shall_i_continue(self):
        body = self._extract_regex_body()
        assert "shall" in body and "continue" in body, (
            "STOP_PATTERN_PHRASES must match 'Shall I continue?' — explicitly "
            "named in AGENTS.md Anti-Stop Patterns. Expected both 'shall' and "
            f"'continue' tokens in the regex body, got: {body!r}"
        )

    def test_includes_should_i_proceed(self):
        body = self._extract_regex_body()
        assert "should" in body and "proceed" in body, (
            "STOP_PATTERN_PHRASES must match 'Should I proceed?' — explicitly "
            "named in AGENTS.md Anti-Stop Patterns. Expected both 'should' and "
            f"'proceed' tokens in the regex body, got: {body!r}"
        )

    def test_includes_want_me_to(self):
        body = self._extract_regex_body()
        assert "want" in body and "me" in body and "to" in body, (
            "STOP_PATTERN_PHRASES must match 'Want me to ...?' — explicitly "
            "named in AGENTS.md Anti-Stop Patterns ('Want me to proceed?', "
            "'Want me to start building?')."
        )

    def test_regex_is_case_insensitive(self):
        src = _src()
        m = re.search(r"STOP_PATTERN_PHRASES\s*=\s*/[^/\n]+/([a-z]+)", src)
        assert m, "STOP_PATTERN_PHRASES regex flags not found"
        flags = m.group(1)
        assert "i" in flags, (
            "STOP_PATTERN_PHRASES must be case-insensitive (the `/i` flag) "
            "so 'Want me to', 'want me to', and 'WANT ME TO' all match."
        )


class TestStopPatternPhrasesWired:
    """The constant must be consulted in the text.complete hook."""

    def test_referenced_in_text_complete(self):
        src = _src()
        # The regex must be TESTED (.test()) somewhere in the plugin body
        assert re.search(r"STOP_PATTERN_PHRASES\.test\s*\(", src), (
            "STOP_PATTERN_PHRASES must be consulted via .test() somewhere in "
            "the plugin — defining the constant without consulting it is "
            "dead code (Guardrail Integrity Policy)."
        )

    def test_constant_not_in_dead_array(self):
        """Guard against regressing to a giant vocabulary list.

        The plugin was deliberately made lean (NO_WAIT_PATTERNS and
        CONSTRAINT_AS_STOP_PATTERNS removed). STOP_PATTERN_PHRASES must
        remain a SINGLE regex literal — not an array of strings — so it
        cannot silently grow back into the unbounded vocabulary pattern.
        """
        src = _src()
        # Reject the array shape `STOP_PATTERN_PHRASES = [`
        assert not re.search(r"STOP_PATTERN_PHRASES\s*=\s*\[", src), (
            "STOP_PATTERN_PHRASES must be a regex literal (not an array). "
            "An array would regress toward the unbounded vocabulary lists "
            "the lean-plugin refactor removed."
        )


class TestMarkdownTableBypassRemoved:
    """P5 structural pin: the markdown-table bypass must be GONE from the TS.

    The Python helper in test_false_done_plugin.py (_would_block_narrowed)
    was updated to drop `has_table` from the work-artifact union. This
    companion structural test greps the TS plugin directly so a future
    edit cannot silently re-introduce the bypass.

    The bug (P5 audit): `MARKDOWN_TABLE_RE = /\\|.*\\|.*\\|/` was OR'd into
    `hasWorkArtifact`, letting the agent write a summary table and stop —
    the exact "summary table as stopping point" pattern AGENTS.md forbids.
    A table alone is NOT evidence of work; only machine evidence (commit
    hash / gate output / pass count) cancels the false-done block, and
    that path is `hasStructuredEvidence`.
    """

    def test_haswork_artifact_excludes_markdown_table(self):
        """`hasMarkdownTable` MUST NOT be OR'd into `hasWorkArtifact`."""
        src = _src()
        # Locate the hasWorkArtifact assignment line
        m = re.search(r"hasWorkArtifact\s*=\s*([^\n]+)", src)
        assert m, "hasWorkArtifact assignment not found in plugin source"
        line = m.group(1)
        assert "hasMarkdownTable" not in line, (
            "P5 REGRESSION: hasWorkArtifact includes hasMarkdownTable. A "
            "markdown table alone is NOT evidence of work — the agent can "
            "write a summary table and stop. Remove hasMarkdownTable from "
            "the union; rely on hasStructuredEvidence (commit hash / pass "
            "count / gate output) for the legitimate table+evidence case."
        )

    def test_late_haswork_artifact_excludes_markdown_table(self):
        """The late (hasLocalWork) bypass must also exclude the table.

        The hasLocalWork block has a parallel `lateHasWorkArtifact` union
        with the same bypass list. The P5 fix must close BOTH paths — a
        markdown table must not bypass the hasLocalWork block either.
        """
        src = _src()
        m = re.search(r"lateHasWorkArtifact\s*=\s*([^\n]+)", src)
        assert m, "lateHasWorkArtifact assignment not found in plugin source"
        line = m.group(1)
        assert "lateHasMarkdownTable" not in line, (
            "P5 REGRESSION: lateHasWorkArtifact includes lateHasMarkdownTable. "
            "The hasLocalWork bypass must not let a summary table through."
        )
