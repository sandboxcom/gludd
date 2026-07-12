"""Tests for CI RED/PENDING block on text-only completion responses.

The enforce-stop.ts plugin (text.complete hook) must block text-only
responses that look like completions when CI is RED or PENDING, even
if local work is clean (gate green, TASKS.md empty).

TDD: this file was written FIRST to assert the CI-RED block behavior
before the plugin was patched.
"""

import json
import os
import re
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
PLUGIN = ROOT / ".opencode" / "plugin" / "enforce-stop.ts"

# ── Python mirror of the plugin's CI check logic ──────────────────────────

def _read_ci_status_from_cache(cache_path):
    """Mirror ciIsPendingOrRed() from enforce-stop.ts."""
    if not cache_path or not os.path.exists(cache_path):
        return "UNKNOWN", 0
    try:
        data = json.loads(Path(cache_path).read_text())
        status = data.get("last_ci_status", "UNKNOWN")
        ts = data.get("last_ci_check", 0)
        return status, ts
    except Exception:
        return "UNKNOWN", 0


def _fresh_ts():
    """Return a recent timestamp (seconds) that won't trigger stale check."""
    import time as _time
    return int(_time.time())


def ci_is_red(status, last_check, stale_sec=600):
    """CI is RED when status is neither SUCCESS nor PENDING and cache is not stale."""
    if status in ("SUCCESS", "PENDING"):
        return False
    now = _fresh_ts()
    return not (now - last_check > stale_sec)


def ci_is_pending(status, last_check, stale_sec=600):
    """CI is PENDING when status is PENDING and cache is not stale."""
    if status != "PENDING":
        return False
    now = _fresh_ts()
    return not now - last_check > stale_sec


def would_block_ci_red(text, ci_status, ci_last_check):
    """Returns True if the CI-RED block would fire for this response.

    Block fires when:
      1. CI is RED (not SUCCESS, not PENDING, not stale)
      2. OR CI is PENDING (not stale)
      3. AND the response is NOT a subagent final report
    """
    is_subagent = bool(
        re.search(r"(?:## Report|## Result|RAW OUTPUT|## CMD:|Files changed|"
                  r"Files edited|Test results|Output:|Exit code)", text)
    )
    if is_subagent:
        return False
    if ci_is_red(ci_status, ci_last_check):
        return True
    return bool(ci_is_pending(ci_status, ci_last_check))


# ── STRUCTURAL TESTS — pin the CI RED block exists in plugin source ────────

class TestCiBlockExistsInPlugin:
    """The enforce-stop.ts plugin must contain the CI RED/PENDING block code."""

    def _src(self):
        return PLUGIN.read_text()

    def test_ci_red_block_message_exists(self):
        src = self._src()
        assert "CI RED" in src, (
            "Plugin must contain a CI RED block message in enforce-stop.ts "
            "text.complete hook."
        )
        assert "COMPLETION CLAIM BLOCKED" in src, (
            "CI RED block must contain 'COMPLETION CLAIM BLOCKED' message."
        )

    def test_ci_pending_block_message_exists(self):
        src = self._src()
        assert "CI PENDING" in src, (
            "Plugin must contain a CI PENDING block message in enforce-stop.ts "
            "text.complete hook."
        )

    def test_ci_red_reads_watchdog_cache(self):
        src = self._src()
        assert "gludd-watchdog-ci.json" in src, (
            "CI RED block must read CI status from /tmp/gludd-watchdog-ci.json "
            "— the watchdog's persistent CI cache."
        )
        assert "last_ci_status" in src, (
            "CI RED block must check last_ci_status field from watchdog cache."
        )

    def test_ci_block_logs_false_done(self):
        src = self._src()
        assert "logFalseDoneBlock" in src, (
            "CI RED block must log via logFalseDoneBlock for audit trail."
        )
        assert "ci-red-text-only" in src or "ci-red-false-done" in src, (
            "CI RED block log must include a specific reason identifier."
        )

    def test_ci_block_records_block(self):
        src = self._src()
        assert "recordBlock" in src, (
            "CI RED block must record via recordBlock for anti-wedge tracking."
        )


# ── BEHAVIORAL TESTS — Python mirror of plugin logic ──────────────────────

class TestCiRedBlocksTextOnly:
    """When CI is RED, text-only responses should be blocked."""

    def test_ci_red_blocks_done_claim(self):
        """'All done.' with CI=failure should be blocked."""
        assert would_block_ci_red("All done.", "FAILURE", _fresh_ts()), (
            "CI RED MUST block a bare 'All done.' — pipeline is broken."
        )

    def test_ci_red_blocks_completion_with_evidence(self):
        """Even text with a commit hash should be blocked when CI is RED."""
        assert would_block_ci_red("Done. Commit abc1234.", "FAILURE", _fresh_ts()), (
            "CI RED MUST block even when a commit hash is present — "
            "a commit on a broken pipeline is not evidence of completion."
        )

    def test_ci_red_blocks_status_report(self):
        """A status report with checkboxes when CI is RED should be blocked."""
        assert would_block_ci_red("- [x] fix bug A\n- [x] fix bug B", "FAILURE", _fresh_ts()), (
            "CI RED MUST block status text — pipeline is broken."
        )


class TestCiGreenAllowsText:
    """When CI is GREEN, text-only responses should NOT be blocked by the CI check."""

    def test_ci_green_allows_done_claim(self):
        """'All done.' with CI=SUCCESS should NOT be blocked by CI check."""
        assert not would_block_ci_red("All done.", "SUCCESS", _fresh_ts()), (
            "CI GREEN must NOT block text — the CI check should pass through."
        )

    def test_ci_green_allows_status_report(self):
        """Status report with CI=SUCCESS should NOT be blocked by CI check."""
        assert not would_block_ci_red("- [x] feature A", "SUCCESS", _fresh_ts()), (
            "CI GREEN must NOT block text — the CI check should pass through."
        )


class TestCiPendingBlocksText:
    """When CI is PENDING, text-only completion responses should be blocked."""

    def test_ci_pending_blocks_done_claim(self):
        """'All done.' with CI=PENDING should be blocked."""
        assert would_block_ci_red("All done.", "PENDING", _fresh_ts()), (
            "CI PENDING MUST block a completion claim — verdict is unknown."
        )

    def test_ci_pending_blocks_completion_text(self):
        """Any completion-looking text with CI=PENDING should be blocked."""
        assert would_block_ci_red("- [x] all tasks", "PENDING", _fresh_ts()), (
            "CI PENDING MUST block completion text — the pipeline hasn't finished."
        )


class TestCiStaleAllowsText:
    """When the CI cache is stale (>10 min), fail open — don't block."""

    def test_stale_ci_red_allows_text(self):
        """CI RED but cache is 20 min old — fail open, allow text."""
        import time
        stale_check = int(time.time()) - (20 * 60)  # 20 minutes ago
        assert not would_block_ci_red("All done.", "FAILURE", stale_check), (
            "Stale CI RED (>10 min) MUST fail open — don't block indefinitely."
        )

    def test_stale_ci_pending_allows_text(self):
        """CI PENDING but cache is 15 min old — fail open, allow text."""
        import time
        stale_check = int(time.time()) - (15 * 60)
        assert not would_block_ci_red("All done.", "PENDING", stale_check), (
            "Stale CI PENDING (>10 min) MUST fail open."
        )


class TestCiUnknownAllowsText:
    """When CI status is UNKNOWN (no cache file), fail open."""

    def test_unknown_ci_allows_text(self):
        """No cache file means CI status unknown — fail open."""
        assert not would_block_ci_red("All done.", "UNKNOWN", 0), (
            "Unknown CI MUST fail open — don't block when status is unavailable."
        )


class TestSubagentReportBypassesCiBlock:
    """Subagent final reports should bypass the CI RED block."""

    def test_subagent_report_not_blocked_by_ci_red(self):
        text = "## Report\n\nFiles changed: src/foo.py\nTest results: 3 passed"
        assert not would_block_ci_red(text, "FAILURE", _fresh_ts()), (
            "Subagent report MUST bypass CI RED block — it's a work deliverable, "
            "not a completion claim."
        )

    def test_subagent_raw_output_not_blocked_by_ci_pending(self):
        text = "## CMD: make lint\nRAW OUTPUT:\n0 errors"
        assert not would_block_ci_red(text, "PENDING", _fresh_ts()), (
            "Subagent RAW OUTPUT MUST bypass CI PENDING block."
        )


# ── TEMP FILE SIMULATION — write CI cache and verify behavior ─────────────

class TestCiCacheFileRoundTrip:
    """Simulate writing the watchdog CI cache file and reading it back."""

    def test_write_red_cache_and_read_back(self):
        """Write CI=failure to cache, read back as RED."""
        import time
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            data = {"last_ci_status": "FAILURE", "last_ci_check": int(time.time())}
            json.dump(data, f)
            cache_path = f.name
        try:
            status, ts = _read_ci_status_from_cache(cache_path)
            assert status == "FAILURE"
            assert ci_is_red(status, ts), "FAILURE status with fresh timestamp should be RED."
        finally:
            os.unlink(cache_path)

    def test_write_green_cache_and_read_back(self):
        """Write CI=SUCCESS to cache, read back as GREEN."""
        import time
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            data = {"last_ci_status": "SUCCESS", "last_ci_check": int(time.time())}
            json.dump(data, f)
            cache_path = f.name
        try:
            status, ts = _read_ci_status_from_cache(cache_path)
            assert status == "SUCCESS"
            assert not ci_is_red(status, ts), "SUCCESS status should NOT be RED."
            assert not ci_is_pending(status, ts), "SUCCESS status should NOT be PENDING."
        finally:
            os.unlink(cache_path)

    def test_write_pending_cache_and_read_back(self):
        """Write CI=PENDING to cache, read back as PENDING."""
        import time
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            data = {"last_ci_status": "PENDING", "last_ci_check": int(time.time())}
            json.dump(data, f)
            cache_path = f.name
        try:
            status, ts = _read_ci_status_from_cache(cache_path)
            assert status == "PENDING"
            assert not ci_is_red(status, ts), "PENDING should NOT be classified as RED."
            assert ci_is_pending(status, ts), "PENDING should be classified as PENDING."
        finally:
            os.unlink(cache_path)

    def test_missing_cache_fails_open(self):
        """No cache file should fail open (treat as unknown, not red)."""
        nonexistent = "/tmp/gludd-nonexistent-ci-cache-test.json"
        status, ts = _read_ci_status_from_cache(nonexistent)
        assert status == "UNKNOWN"
        assert not ci_is_red(status, ts), "Missing cache should fail open."
        assert not ci_is_pending(status, ts), "Missing cache should fail open."


# ── Plugin registration integrity ─────────────────────────────────────────

class TestCiBlockPluginIntegrity:
    """The CI RED block must not regress."""

    def test_ci_block_present_in_text_complete_hook(self):
        src = PLUGIN.read_text()
        # The CI RED block must be inside the text.complete handler
        txt_complete_start = src.index('"experimental.text.complete"')
        txt_complete_section = src[txt_complete_start:]
        assert "CI RED" in txt_complete_section, (
            "CI RED block must be inside the text.complete hook section."
        )

    def test_ci_block_has_disengage_escape_hatch(self):
        """The disengage-enforcement signal should still work when CI is RED."""
        src = PLUGIN.read_text()
        assert "disengage" in src.lower(), (
            "Plugin must still reference disengage enforcement escape hatch."
        )
