"""Phase CM — Communication Discipline pattern verification.

Phase CM in TASKS.md enumerates 15 specs for agent communication discipline.
Several specs (CM.2, CM.3, CM.4, CM.5, CM.7, CM.8-11) cite patterns already
implemented in `.opencode/plugin/impl/enforce_stop_impl.ts` but lack a
consolidated verification test. This file pins the structural presence and
shape of each communication-detection pattern so a regression that removes
or empties one is caught at gate time.

Scope (one test class per CM spec covered):

  CM.2  STATUS_SUMMARY_RE + looksLikeStatusSummary()  — status-table block
  CM.3  STOP_PATTERN_PHRASES + PERMISSION_SEEKING_RE  — "shall I proceed?" block
  CM.4  QA_RESPONSE_PATTERNS                          — Q&A recap block
  CM.5  text.complete text-only-response block        — tool-call-required rule
  CM.7  COMPLETION_WORDS_RE / COMPLETION_VERBATIM /
        SHORT_COMPLETION_PHRASES                      — stop-signal words
  CM.8  EVIDENCE_PATTERNS + COMMIT_HASH_RE            — done-claim evidence
  (xl)  looksLikeStatusSummary interleaved detection  — bold headers + tables
  (xl)  COMPLETION_SMELL_RE                           — completion-adjacent lang

Each test does source-text verification against the impl file (and the lean
plugin wrapper). The patterns are not exported as named runtime symbols, so
we verify by regex against the source — the same approach used by
test_stop_pattern_qa.py and test_stop_pattern_phrases.py.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
PLUGIN = ROOT / ".opencode" / "plugin" / "enforce-stop.ts"
_IMPL = ROOT / ".opencode" / "plugin" / "impl" / "enforce_stop_impl.ts"


def _src() -> str:
    s = PLUGIN.read_text()
    if _IMPL.exists():
        s += "\n" + _IMPL.read_text()
    return s


def _impl_src() -> str:
    """Return ONLY the impl source — the canonical full-pattern definitions.

    The lean wrapper `enforce-stop.ts` carries stub placeholder patterns for
    import-shape verification; the real detection regexes live in the impl.
    Body-extraction tests must read the impl to avoid matching the stub.
    """
    assert _IMPL.exists(), f"impl file not found: {_IMPL}"
    return _IMPL.read_text()


def _extract_regex_body(name: str) -> str:
    # Search the IMPL source first (canonical patterns). Fall back to combined
    # src so a future single-file refactor doesn't silently break the pin.
    src = _impl_src()
    m = re.search(re.escape(name) + r"\s*=\s*/([^/\n]+)/", src)
    if not m:
        src = _src()
        m = re.search(re.escape(name) + r"\s*=\s*/([^/\n]+)/", src)
    assert m, f"{name} regex literal not found in enforce-stop source"
    return m.group(1).lower()


def _extract_function_body(name: str) -> str:
    """Extract a TypeScript function body, tolerating return-type annotations."""
    src = _impl_src()
    # `function foo(args): RetType {` — the `[^{]*` skips the return type.
    m = re.search(
        r"function\s+" + re.escape(name) + r"\s*\([^)]*\)[^{]*\{(?P<body>.+?)(?:^|\n)\}",
        src,
        re.S | re.M,
    )
    assert m, f"{name}() function body not found in impl source"
    return m.group("body")


class TestStatusSummaryDetection:
    """CM.2 — status-table / status-summary responses are blocked."""

    def test_status_summary_re_defined(self):
        assert "STATUS_SUMMARY_RE" in _src(), (
            "STATUS_SUMMARY_RE must be defined — status summaries with bold "
            "headers + tables were the BUGS.md #7/#10 failure mode."
        )

    def test_status_summary_re_includes_here_is_status(self):
        body = _src().lower()
        m = re.search(r"status_summary_re\s*=\s*new\s*regexp\(\s*\[([^\]]+)\]", body, re.S)
        assert m, "STATUS_SUMMARY_RE RegExp(...) body not found"
        joined = m.group(1)
        assert "status" in joined, (
            "STATUS_SUMMARY_RE must match 'status' summaries."
        )

    def test_looks_like_status_summary_function_exists(self):
        assert re.search(r"function\s+looksLikeStatusSummary\s*\(", _src()), (
            "looksLikeStatusSummary() function must exist — it performs the "
            "structural detection (bold headers + table rows + status bullets) "
            "that pure regex cannot."
        )

    def test_looks_like_status_summary_consults_regex(self):
        src = _src()
        assert re.search(r"STATUS_SUMMARY_RE\.test\s*\(", src), (
            "looksLikeStatusSummary() must call STATUS_SUMMARY_RE.test() — "
            "defining the regex without consulting it is dead code."
        )

    def test_status_summary_block_path_exists(self):
        src = _src()
        # Status summaries are routed through the QA-response block path
        # (looksLikeStatusSummary gates the QA block). Verify the block reason
        # is recorded so the event is machine-auditable.
        assert re.search(r"recordBlock\s*\(\s*['\"]qa-response-summary-stop", src), (
            "Status-summary detection must feed into a recorded block reason "
            "('qa-response-summary-stop') so the event is auditable."
        )


class TestPermissionSeekingDetection:
    """CM.3 — 'shall I proceed?' / 'want me to continue?' are blocked."""

    def test_stop_pattern_phrases_defined(self):
        assert "STOP_PATTERN_PHRASES" in _src(), (
            "STOP_PATTERN_PHRASES must be defined — the three classic "
            "permission-seeking deferral phrases."
        )

    def test_stop_pattern_phrases_includes_shall_i_continue(self):
        body = _extract_regex_body("STOP_PATTERN_PHRASES")
        assert "shall" in body and "continue" in body, (
            "STOP_PATTERN_PHRASES must match 'shall I continue?'."
        )

    def test_stop_pattern_phrases_includes_should_i_proceed(self):
        body = _extract_regex_body("STOP_PATTERN_PHRASES")
        assert "should" in body and "proceed" in body, (
            "STOP_PATTERN_PHRASES must match 'should I proceed?'."
        )

    def test_permission_seeking_re_defined(self):
        assert "PERMISSION_SEEKING_RE" in _src(), (
            "PERMISSION_SEEKING_RE must be defined — broader permission-seeking "
            "phrase detection beyond the three STOP_PATTERN_PHRASES."
        )

    def test_permission_seeking_re_includes_want_me_to(self):
        body = _extract_regex_body("PERMISSION_SEEKING_RE")
        assert "want me to" in body, (
            "PERMISSION_SEEKING_RE must match 'want me to ...' variants."
        )

    def test_permission_seeking_block_recorded(self):
        src = _src()
        assert re.search(r"recordBlock\s*\(\s*['\"]permission-seeking-stop", src), (
            "Permission-seeking block must call recordBlock('permission-seeking-stop') "
            "for machine-readable audit."
        )


class TestQaResponsePatterns:
    """CM.4 — bolded question headers / Q&A recaps are blocked."""

    def test_qa_response_patterns_defined(self):
        assert "QA_RESPONSE_PATTERNS" in _src(), (
            "QA_RESPONSE_PATTERNS must be defined — Q&A-style 'what was done' "
            "recaps were the BUGS.md #7/#10 root cause."
        )

    def test_qa_response_patterns_includes_completed_in_session(self):
        body = _extract_regex_body("QA_RESPONSE_PATTERNS")
        assert "completed in this session" in body, (
            "QA_RESPONSE_PATTERNS must match 'completed in this session'."
        )

    def test_qa_response_patterns_includes_what_changed(self):
        body = _extract_regex_body("QA_RESPONSE_PATTERNS")
        assert "changed" in body, (
            "QA_RESPONSE_PATTERNS must match '**What changed?**' bolded headers."
        )

    def test_qa_response_patterns_case_insensitive(self):
        src = _src()
        m = re.search(r"QA_RESPONSE_PATTERNS\s*=\s*/[^/\n]+/([a-z]+)", src)
        assert m, "QA_RESPONSE_PATTERNS regex flags not found"
        assert "i" in m.group(1), (
            "QA_RESPONSE_PATTERNS must be case-insensitive (the /i flag)."
        )

    def test_qa_block_directive_exists(self):
        src = _src()
        assert "QA RESPONSE SUMMARY BLOCKED" in src, (
            "The QA summary block directive must contain 'QA RESPONSE SUMMARY BLOCKED'."
        )


class TestToolCallRequiredEnforcement:
    """CM.5 — text-only responses while work is pending are blocked."""

    def test_text_complete_hook_registered(self):
        src = _src()
        assert re.search(r"['\"](?:experimental\.)?text\.complete['\"]\s*:", src), (
            "A text.complete hook (experimental or bare) must be registered."
        )

    def test_consecutive_text_only_block_exists(self):
        src = _src()
        assert re.search(r"recordBlock\s*\(\s*['\"]consecutive-text-only", src), (
            "The consecutive-text-only block must call "
            "recordBlock('consecutive-text-only')."
        )

    def test_consecutive_text_only_directive_exists(self):
        src = _src()
        assert "CONSECUTIVE TEXT-ONLY RESPONSES BLOCKED" in src, (
            "The CONSECUTIVE TEXT-ONLY RESPONSES BLOCKED directive must exist."
        )

    def test_post_results_text_only_block_exists(self):
        src = _src()
        assert re.search(r"recordBlock\s*\(\s*['\"]after-results-text-only", src), (
            "The post-results text-only block must call "
            "recordBlock('after-results-text-only')."
        )


class TestStopSignalWords:
    """CM.7 — stop-signal words / completion-verbatim phrases are detected.

    The TASKS.md spec names the conceptual 'STOP_SIGNAL_WORDS'; the actual
    implementation splits detection across three regexes of increasing
    strictness: COMPLETION_WORDS_RE, COMPLETION_VERBATIM, and
    SHORT_COMPLETION_PHRASES.
    """

    def test_completion_words_re_defined(self):
        assert "COMPLETION_WORDS_RE" in _src(), (
            "COMPLETION_WORDS_RE must be defined — the stop-signal word list."
        )

    def test_completion_words_re_includes_done(self):
        body = _extract_regex_body("COMPLETION_WORDS_RE")
        assert "done" in body, "COMPLETION_WORDS_RE must include 'done'."

    def test_completion_words_re_includes_committed(self):
        body = _extract_regex_body("COMPLETION_WORDS_RE")
        assert "committed" in body, "COMPLETION_WORDS_RE must include 'committed'."

    def test_completion_words_re_includes_shipped(self):
        body = _extract_regex_body("COMPLETION_WORDS_RE")
        assert "shipped" in body, "COMPLETION_WORDS_RE must include 'shipped'."

    def test_completion_verbatim_defined(self):
        assert "COMPLETION_VERBATIM" in _src(), (
            "COMPLETION_VERBATIM must be defined — the strictest completion "
            "phrases that have no non-completion reading."
        )

    def test_completion_verbatim_includes_all_done(self):
        body = _extract_regex_body("COMPLETION_VERBATIM")
        assert "all done" in body, (
            "COMPLETION_VERBATIM must include 'all done'."
        )

    def test_short_completion_phrases_defined(self):
        assert "SHORT_COMPLETION_PHRASES" in _src(), (
            "SHORT_COMPLETION_PHRASES must be defined."
        )

    def test_short_completion_phrases_non_empty(self):
        body = _extract_regex_body("SHORT_COMPLETION_PHRASES")
        assert len(body) > 10, (
            "SHORT_COMPLETION_PHRASES body is suspiciously short — expected "
            "multiple completion phrases."
        )

    def test_response_looks_terminal_consults_all_three(self):
        body = _extract_function_body("responseLooksTerminal")
        for name in ("COMPLETION_VERBATIM", "COMPLETION_WORDS_RE", "SHORT_COMPLETION_PHRASES"):
            assert name in body, (
                f"responseLooksTerminal() must consult {name} — otherwise the "
                "stop-signal word list is dead code."
            )


class TestEvidencePatternChecking:
    """CM.8-11 — done claims require machine-produced evidence in the same message."""

    def test_evidence_patterns_defined(self):
        src = _src()
        assert re.search(r"EVIDENCE_PATTERNS\s*=\s*\[", src), (
            "EVIDENCE_PATTERNS must be defined as an array — used to distinguish "
            "verified done-claims from unverified ones."
        )

    def test_evidence_patterns_non_empty(self):
        src = _src()
        m = re.search(r"EVIDENCE_PATTERNS\s*=\s*\[([^\]]+)\]", src, re.S)
        assert m, "EVIDENCE_PATTERNS array body not found"
        body = m.group(1)
        slash_count = body.count("/")
        assert slash_count >= 4, (
            f"EVIDENCE_PATTERNS must contain multiple regex literals "
            f"(found only {slash_count // 2} regexes)."
        )

    def test_commit_hash_re_defined(self):
        assert "COMMIT_HASH_RE" in _src(), (
            "COMMIT_HASH_RE must be defined — commit hashes are the canonical "
            "evidence token for 'committed' / 'pushed' claims."
        )

    def test_evidence_patterns_includes_gate_pass(self):
        src = _src()
        m = re.search(r"EVIDENCE_PATTERNS\s*=\s*\[([^\]]+)\]", src, re.S)
        assert m, "EVIDENCE_PATTERNS array body not found"
        body = m.group(1)
        assert "gate" in body.lower() and "passed" in body.lower(), (
            "EVIDENCE_PATTERNS must include a '=== GATE: PASSED ===' matcher."
        )

    def test_has_structured_evidence_function_exists(self):
        src = _src()
        assert re.search(r"function\s+hasStructuredEvidence\s*\(", src), (
            "hasStructuredEvidence() must exist — it consults EVIDENCE_PATTERNS "
            "to decide whether a done-claim carries proof."
        )

    def test_has_structured_evidence_consults_patterns(self):
        body = _extract_function_body("hasStructuredEvidence")
        assert "EVIDENCE_PATTERNS" in body, (
            "hasStructuredEvidence() must call EVIDENCE_PATTERNS.some(...) — "
            "defining the array without consulting it is dead code."
        )


class TestCompletionSmellDetection:
    """Completion-adjacent language detection — the broad net.

    COMPLETION_SMELL_RE is the loosest detector: any completion-adjacent
    substring triggers closer inspection. It exists separately from the
    strict COMPLETION_WORDS_RE because status summaries containing hashes
    still 'smell' like completion claims even when the strict word isn't
    present.
    """

    def test_completion_smell_re_defined(self):
        assert "COMPLETION_SMELL_RE" in _src(), (
            "COMPLETION_SMELL_RE must be defined — the broad completion-adjacent "
            "language detector."
        )

    def test_completion_smell_re_includes_done(self):
        body = _extract_regex_body("COMPLETION_SMELL_RE")
        assert "done" in body, "COMPLETION_SMELL_RE must include 'done'."

    def test_completion_smell_re_includes_continuing(self):
        body = _extract_regex_body("COMPLETION_SMELL_RE")
        assert "continuing" in body, (
            "COMPLETION_SMELL_RE must include 'continuing' — a phrase that "
            "appears in 'still working' status reports that are often stops."
        )

    def test_completion_smell_re_case_insensitive(self):
        src = _src()
        m = re.search(r"COMPLETION_SMELL_RE\s*=\s*/[^/\n]+/([a-z]+)", src)
        assert m, "COMPLETION_SMELL_RE regex flags not found"
        assert "i" in m.group(1), (
            "COMPLETION_SMELL_RE must be case-insensitive (the /i flag)."
        )

    def test_completion_smell_consulted_in_status_summary(self):
        body = _extract_function_body("looksLikeStatusSummary")
        assert "COMPLETION_SMELL_RE" in body, (
            "looksLikeStatusSummary() must consult COMPLETION_SMELL_RE — "
            "table-heavy status rows containing completion language are the "
            "canonical status-summary shape."
        )


class TestInterleavedSummaryDetection:
    """Structural detection of interleaved summaries.

    A response that embeds markdown section headers, bolded question-style
    sub-headers, and status tables/bullets is a status report in disguise —
    even if no single regex matches. looksLikeStatusSummary() performs this
    structural detection by counting headers/rows/bullets.
    """

    def test_looks_like_status_summary_counts_bold_headers(self):
        body = _extract_function_body("looksLikeStatusSummary")
        assert "boldHeaders" in body or "\\*\\*" in body, (
            "looksLikeStatusSummary() must count bold headers (**Header**) — "
            "the structural shape of a status-report recap."
        )

    def test_looks_like_status_summary_counts_table_rows(self):
        body = _extract_function_body("looksLikeStatusSummary")
        assert "tableRows" in body or "\\|" in body, (
            "looksLikeStatusSummary() must count markdown table rows (|...|) — "
            "the structural shape of a status-table recap."
        )

    def test_looks_like_status_summary_counts_status_bullets(self):
        body = _extract_function_body("looksLikeStatusSummary")
        assert "statusBullets" in body or "\\[ x" in body.lower(), (
            "looksLikeStatusSummary() must count status bullets "
            "(- [x] / - [ ] / ✅) — the structural shape of a task-list recap."
        )

    def test_looks_like_status_summary_wired_into_text_complete(self):
        src = _src()
        assert re.search(r"looksLikeStatusSummary\s*\(", src), (
            "looksLikeStatusSummary() must be called somewhere in the plugin — "
            "defining it without calling it is dead code."
        )
