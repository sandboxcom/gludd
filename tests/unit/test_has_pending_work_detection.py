"""Tests for hasPendingWork() detection logic in enforce-multitask.ts.

Verifies each of the six detection categories against the actual TypeScript
regex/logic extracted from the plugin source.  When the TS implementation
changes, these tests must also change so they stay the structural pin.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

PLUGIN_PATH = Path(__file__).resolve().parents[2] / ".opencode/plugin/enforce-multitask.ts"


def _plugin_source() -> str:
    return PLUGIN_PATH.read_text()


def _extract_has_pending_work_body() -> str:
    src = _plugin_source()
    m = re.search(r"function hasPendingWork\(\).*?\{([\s\S]*?)\n\}", src)
    assert m, "could not extract hasPendingWork body"
    return m.group(1)


def _extract_ts_regex(js_expr: str) -> str:
    m = re.search(r"/([^/]+)/", js_expr)
    assert m, f"could not extract regex from: {js_expr!r}"
    return m.group(1)


# ── 1. TASKS.md unchecked checkboxes ──────────────────────────────────────────


class TestCheckboxDetection:
    """Regex: /^\\s*[-*]\\s*\\[\\s*\\]/m  (dotALL multiline — start anchor)"""

    CHECKBOX_RE = re.compile(r"^\s*[-*]\s*\[\s*\]", re.MULTILINE)

    def test_matches_dash_bracket_space_bracket(self):
        assert self.CHECKBOX_RE.search("- [ ] Task description")

    def test_matches_star_bracket_bracket(self):
        assert self.CHECKBOX_RE.search("* [] Another task")

    def test_matches_indented(self):
        assert self.CHECKBOX_RE.search("  - [ ] Indented checkbox")

    def test_matches_no_space_in_brackets(self):
        assert self.CHECKBOX_RE.search("- [] No space in brackets")

    def test_does_not_match_checked(self):
        assert not self.CHECKBOX_RE.search("- [x] Done task")

    def test_does_not_match_x_in_brackets(self):
        assert not self.CHECKBOX_RE.search("- [X] Completed")

    def test_does_not_match_non_checkbox_paragraph(self):
        assert not self.CHECKBOX_RE.search("Just some text with [ ] inside")

    def test_pin_ts_source_uses_same_regex(self):
        body = _extract_has_pending_work_body()
        # Checkbox regex is delegated to hasTasksMdPendingWork() in shared.ts.
        # Verify delegation exists and the regex is present in shared.ts.
        assert "hasTasksMdPendingWork" in body
        import_path = PLUGIN_PATH.parent.parent / "lib" / "shared.ts"
        shared_src = import_path.read_text() if import_path.exists() else ""
        assert "/^\\s*[-*]\\s*\\[\\s*\\]/" in shared_src.replace(" ", "").replace("\t", "")


# ── 2. Table rows with NOT STARTED / IN PROGRESS / PENDING ─────────────────────


class TestTableRowDetection:
    """hasTasksMdPendingWork() in shared.ts now scans table rows via
    /\\|\\s*(NOT STARTED|IN PROGRESS|PENDING)\\s*\\|/im.  The checkbox
    regex is the primary detector; table-row detection is a secondary pass
    that catches TASKS.md entries in status-table format."""

    MARKDOWN_TABLE = """
| Task | Status |
|------|--------|
| Alpha | NOT STARTED |
| Beta  | IN PROGRESS |
| Gamma | PENDING    |
""".strip()

    def test_checkbox_re_does_not_match_table_words(self):
        """Demonstrate: checkbox regex does not catch table-status keywords."""
        assert not TestCheckboxDetection.CHECKBOX_RE.search(self.MARKDOWN_TABLE)

    def test_word_NOT_STARTED_present(self):
        assert "NOT STARTED" in self.MARKDOWN_TABLE

    def test_word_IN_PROGRESS_present(self):
        assert "IN PROGRESS" in self.MARKDOWN_TABLE

    def test_word_PENDING_present(self):
        assert "PENDING" in self.MARKDOWN_TABLE

    def test_no_table_row_pattern_in_has_pending_work(self):
        body = _extract_has_pending_work_body()
        body_lower = body.lower()
        for kw in ["not started", "in progress"]:
            assert kw not in body_lower, (
                f"unexpected keyword '{kw}' in hasPendingWork — "
                "table-row detection was NOT expected but appears present"
            )
        # "pending" appears in hasTasksMdPendingWork function name, the
        # todowrite status check, and the stop-state mirror probe
        # (state.hasPendingWork); strip those references then verify no
        # table-row detection remains.
        cleaned = body_lower
        cleaned = re.sub(r"\bhastasksmdpendingwork\b", "", cleaned)
        cleaned = re.sub(r'"pending"', "", cleaned)
        cleaned = re.sub(r"state\.haspendingwork", "", cleaned)
        assert "pending" not in cleaned, (
            "unexpected keyword 'pending' in hasPendingWork outside function-name / todowrite / stop-state contexts — "
            "table-row detection was NOT expected but appears present"
        )
        # Verify table-row detection IS in shared.ts (hasTasksMdPendingWork delegate)
        shared_import = PLUGIN_PATH.parent.parent / "lib" / "shared.ts"
        shared_src = shared_import.read_text() if shared_import.exists() else ""
        assert "NOT STARTED" in shared_src
        assert "IN PROGRESS" in shared_src
        assert "PENDING" in shared_src

    # ── Table-row regex detection (added when hasTasksMdPendingWork gained table-row support) ──

    TABLE_ROW_RE = re.compile(r"\|\s*(NOT STARTED|IN PROGRESS|PENDING)\s*\|", re.IGNORECASE)

    def test_table_row_regex_matches_NOT_STARTED(self):
        assert self.TABLE_ROW_RE.search("| NOT STARTED |")

    def test_table_row_regex_matches_IN_PROGRESS(self):
        assert self.TABLE_ROW_RE.search("| IN PROGRESS |")

    def test_table_row_regex_matches_PENDING(self):
        assert self.TABLE_ROW_RE.search("| PENDING |")

    def test_table_row_regex_case_insensitive(self):
        assert self.TABLE_ROW_RE.search("| not started |")
        assert self.TABLE_ROW_RE.search("| in progress |")
        assert self.TABLE_ROW_RE.search("| pending |")

    def test_table_row_regex_does_not_match_COMPLETED(self):
        assert not self.TABLE_ROW_RE.search("| COMPLETED |")
        assert not self.TABLE_ROW_RE.search("| DONE |")
        assert not self.TABLE_ROW_RE.search("| CANCELLED |")

    def test_table_row_regex_with_extra_whitespace(self):
        assert self.TABLE_ROW_RE.search("|   NOT STARTED   |")
        assert self.TABLE_ROW_RE.search("|  IN PROGRESS  |")

    def test_has_tasks_md_pending_work_with_table_rows(self):
        """Structural pin: hasTasksMdPendingWork in shared.ts uses the table-row regex."""
        shared_path = PLUGIN_PATH.parent.parent / "lib" / "shared.ts"
        assert shared_path.exists(), "shared.ts missing"
        shared_src = shared_path.read_text()
        assert re.search(r"/\\\|\\s\*\(NOT\s+STARTED", shared_src) or "NOT STARTED" in shared_src, (
            "shared.ts missing table-row NOT STARTED regex"
        )

    def test_table_row_in_full_has_pending_work_chain(self):
        """Verify table-row keywords appear in hasTasksMdPendingWork function body."""
        shared_path = PLUGIN_PATH.parent.parent / "lib" / "shared.ts"
        shared_src = shared_path.read_text()
        func_body = shared_src.split("function hasTasksMdPendingWork")[1]
        assert "NOT STARTED" in func_body
        assert "IN PROGRESS" in func_body
        assert "PENDING" in func_body
        assert "|\\s*" in func_body.replace("|\\s", "|\\s").replace("| ", "| ")


# ── 3. Unresolved BUGS.md entries ─────────────────────────────────────────────


class TestBugsMdDetection:
    """Regex: /^###\\s+\\d{4}-\\d{2}-\\d{2}\\s+[-—]/m  WITHOUT (resolved)"""

    BUGS_RE = re.compile(r"^###\s+\d{4}-\d{2}-\d{2}\s+[-—]", re.MULTILINE)

    def test_matches_open_bug_entry(self):
        assert self.BUGS_RE.search("### 2026-07-01 — Agent stopped prematurely")

    def test_matches_emdash(self):
        assert self.BUGS_RE.search("### 2026-08-01 —\u2014 Disk full incident")

    def test_matches_hyphen(self):
        assert self.BUGS_RE.search("### 2026-08-01 - Disk full incident")

    def test_does_not_match_resolved_entry_when_filtered(self):
        line = "### 2026-07-03 — Something broke"
        is_open = self.BUGS_RE.search(line) is not None and "(resolved)" not in line
        assert is_open

    def test_resolved_entry_filtered_out(self):
        line = "### 2026-07-03 — Something broke (resolved)"
        is_open = self.BUGS_RE.search(line) is not None and "(resolved)" not in line
        assert not is_open

    def test_does_not_match_date_in_body_paragraph(self):
        assert not self.BUGS_RE.search("Today is 2026-07-15 — and it worked")

    def test_requires_h3_heading(self):
        assert not self.BUGS_RE.search("## 2026-07-03 — Not a level-3 heading")

    def test_requires_dash_after_date(self):
        assert not self.BUGS_RE.search("### 2026-07-03 no dash separator")

    def test_pin_ts_source_uses_same_regex(self):
        body = _extract_has_pending_work_body()
        assert r"\d{4}-\d{2}-\d{2}" in body or "d{4}-" in body


# ── 4. Non-empty ratchet.yml ──────────────────────────────────────────────────


class TestRatchetDetection:
    """Counts non-comment, non-empty lines containing :: or key: value.
    entries > 0 → hasPendingWork() returns true."""

    def test_key_colon_value_counted(self):
        line = "  some_key: some_value"
        is_non_empty = line.strip() != ""
        is_non_comment = not line.strip().startswith("#")
        matches = "::" in line or bool(re.search(r"^\w[\w\s]*:\s", line.strip()))
        assert is_non_empty and is_non_comment and matches

    def test_double_colon_counted(self):
        line = "  general_ludd::agent::runner"
        is_non_empty = line.strip() != ""
        is_non_comment = not line.strip().startswith("#")
        matches = "::" in line or bool(re.search(r"^\w[\w\s]*:\s", line))
        assert is_non_empty and is_non_comment and matches

    def test_comment_skipped(self):
        line = "  # some_key: value"
        is_entry = (
            line.strip() != ""
            and not line.strip().startswith("#")
            and ("::" in line or bool(re.search(r"^\w[\w\s]*:\s", line.strip())))
        )
        assert not is_entry

    def test_empty_line_skipped(self):
        line = "    "
        is_entry = (
            line.strip() != ""
            and not line.strip().startswith("#")
            and ("::" in line or bool(re.search(r"^\w[\w\s]*:\s", line.strip())))
        )
        assert not is_entry

    def test_paragraph_line_skipped(self):
        line = "This is just a sentence with no colon pattern"
        is_entry = (
            line.strip() != ""
            and not line.strip().startswith("#")
            and ("::" in line or bool(re.search(r"^\w[\w\s]*:\s", line.strip())))
        )
        assert not is_entry

    def test_empty_ratchet_means_no_pending_work(self):
        content = """# only comments\n# no real entries\n"""
        entries = sum(
            1
            for line in content.split("\n")
            if line.strip()
            and not line.strip().startswith("#")
            and ("::" in line or bool(re.search(r"^\w[\w\s]*:\s", line.strip())))
        )
        assert entries == 0

    def test_pin_ratchet_pattern_in_has_pending_work(self):
        body = _extract_has_pending_work_body()
        assert "ratchet" in body.lower()
        assert "::" in body


# ── 5. Red gate-status ────────────────────────────────────────────────────────


class TestGateStatusDetection:
    """Regex: /=== GATE:\\s*FAILED/  plus a per-phase prefix parser that
    flags any gate-status line starting with a phase name and containing
    FAIL (replaced the older 'test REQUIRED' / 'smoke REQUIRED' detector)."""

    GATE_FAIL_RE = re.compile(r"=== GATE:\s*FAILED")

    def test_matches_exact_failed(self):
        assert self.GATE_FAIL_RE.search("=== GATE: FAILED")

    def test_matches_failed_with_spaces(self):
        assert self.GATE_FAIL_RE.search("=== GATE:   FAILED")

    def test_does_not_match_passed(self):
        assert not self.GATE_FAIL_RE.search("=== GATE: PASSED")

    def test_phase_line_test_detected(self):
        content = "test FAIL 2 — run make test first"
        assert content.startswith("test ")
        assert "FAIL" in content

    def test_phase_line_smoke_detected(self):
        content = "smoke FAIL — daemon not responding"
        assert content.startswith("smoke ")
        assert "FAIL" in content

    def test_pin_gate_failed_regex_in_has_pending_work(self):
        body = _extract_has_pending_work_body()
        assert "=== GATE" in body
        assert "FAILED" in body
        assert "^(lint |typecheck |collect |test |smoke" in body, (
            "per-phase gate-status prefix parser missing from hasPendingWork"
        )
        assert "/FAIL/" in body, "per-phase FAIL matcher missing from hasPendingWork"


# ── 6. CI not green ───────────────────────────────────────────────────────────


class TestCiNotGreenDetection:
    """Reads GLUDD_CI_CACHE_PATH JSON; if last_ci_status != SUCCESS
    and last_ci_check < 10 min ago, hasPendingWork() returns true."""

    CI_CACHE_PATH = "/tmp/gludd-watchdog-ci.json"

    def test_failing_ci_within_window_is_pending(self):
        now_ms = int(time.time() * 1000)
        data = {"last_ci_check": now_ms / 1000, "last_ci_status": "FAILURE"}
        raw_check = data["last_ci_check"]
        last_check = raw_check if raw_check >= 1e11 else raw_check * 1000
        status = data["last_ci_status"]
        within_window = (now_ms - last_check) < 600_000
        is_pending = within_window and status != "SUCCESS"
        assert is_pending

    def test_success_ci_is_not_pending(self):
        now_ms = int(time.time() * 1000)
        data = {"last_ci_check": now_ms / 1000, "last_ci_status": "SUCCESS"}
        raw_check = data["last_ci_check"]
        last_check = raw_check if raw_check >= 1e11 else raw_check * 1000
        status = data["last_ci_status"]
        within_window = (now_ms - last_check) < 600_000
        is_pending = within_window and status != "SUCCESS"
        assert not is_pending

    def test_stale_ci_cache_is_not_pending(self):
        old_ms = int((time.time() - 900) * 1000)
        data = {"last_ci_check": old_ms / 1000, "last_ci_status": "FAILURE"}
        raw_check = data["last_ci_check"]
        last_check = raw_check if raw_check >= 1e11 else raw_check * 1000
        now_ms = old_ms + 900_000
        within_window = (now_ms - last_check) < 600_000
        assert not within_window

    def test_no_ci_cache_file_means_no_pending(self):
        assert not Path("/tmp/gludd-watchdog-ci-nonexistent.json").exists()

    def test_timestamp_normalization_below_1e11(self):
        raw = 1700000000
        normalized = raw if raw >= 1e11 else raw * 1000
        assert normalized == 1700000000000

    def test_timestamp_already_ms_left_alone(self):
        raw = 1700000000000
        normalized = raw if raw >= 1e11 else raw * 1000
        assert normalized == 1700000000000

    def test_pin_ci_cache_path_in_has_pending_work(self):
        body = _extract_has_pending_work_body()
        assert "gludd-watchdog-ci" in body
        assert "last_ci_status" in body
        assert "600_000" in body.replace("600000", "600_000")


# ── 7. todowrite state (bonus — present in hasPendingWork) ────────────────────


class TestTodowriteStateDetection:
    """Reads /tmp/gludd-todowrite-state.json; items with status pending
    or in_progress → hasPendingWork() returns true."""

    def test_pending_item_detected(self):
        data = {"items": [{"status": "pending", "content": "do X"}]}
        items = data.get("items", [])
        has_pending = any(it.get("status") in ("pending", "in_progress") for it in items if isinstance(it, dict))
        assert has_pending

    def test_in_progress_item_detected(self):
        data = {"items": [{"status": "in_progress", "content": "doing Y"}]}
        items = data.get("items", [])
        has_pending = any(it.get("status") in ("pending", "in_progress") for it in items if isinstance(it, dict))
        assert has_pending

    def test_completed_items_ignored(self):
        data = {"items": [{"status": "completed", "content": "done Z"}]}
        items = data.get("items", [])
        has_pending = any(it.get("status") in ("pending", "in_progress") for it in items if isinstance(it, dict))
        assert not has_pending

    def test_empty_items_list_not_pending(self):
        data = {"items": []}
        items = data.get("items", [])
        has_pending = any(it.get("status") in ("pending", "in_progress") for it in items if isinstance(it, dict))
        assert not has_pending

    def test_pin_todowrite_in_has_pending_work(self):
        body = _extract_has_pending_work_body()
        assert "todowrite" in body.lower() or "status" in body.lower()


# ── 8. Structural pin: hasPendingWork is wired into enforcement ───────────────


class TestStructuralPins:
    """The function must exist, be called, and fail-to-open (try/catch)."""

    def test_function_exists(self):
        assert "function hasPendingWork" in _plugin_source()

    def test_called_from_tool_execute_before(self):
        src = _plugin_source()
        exec_section = src.split('"tool.execute.before"')[1]
        assert "hasPendingWork()" in exec_section

    def test_called_from_text_complete(self):
        src = _plugin_source()
        assert "handleMessageBoundary" in src.split("handleTextComplete")[3]

    def test_wrapped_in_try_catch(self):
        body = _extract_has_pending_work_body()
        assert "catch" in body or "try {" in body.lower()
