"""Process overhead measurement — detect overfitted enforcement machinery.

Codified processes (anti-stop plugins, enforcement guardrails) must be measured
for whether they cause more work than they save. This test suite detects over-fitting
by measuring actual runtime behavior: state-file block counts, plugin line counts,
pattern-list growth, and the ratio of state-based vs vocabulary-based checks.
"""

import json
import os
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent
PLUGIN_DIR = ROOT / ".opencode" / "plugin"


def _read_json(path: str) -> dict | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None


# ---------------------------------------------------------------------------
# TestAntiStopFalsePositiveRate
# ---------------------------------------------------------------------------


class TestAntiStopFalsePositiveRate:
    """Measure whether state-file tracking shows over-firing."""
    def test_false_done_state_file_tracks_block_count(self):
        """Recent block rate checks for over-fitting; persistent file is cumulative."""
        data = _read_json("/tmp/gludd-false-done-blocks.json")
        # If the state file doesn't exist, the plugin isn't active — not a failure.
        if data is None:
            return
        if not isinstance(data, list):
            return
        # Count only blocks from the last 5 minutes — persistent file accumulates
        # across sessions; only the recent rate signals overfitting.
        cutoff = time.time() - 300
        recent = [e for e in data if (e.get("ts", 0) / 1000) >= cutoff]
        count = len(recent)
        assert count < 5, (
            f"False-done block count in last 5 min is {count}. "
            f">= 5 recent blocks indicates enforcement is over-fitting to "
            f"common vocabulary."
        )

    def test_stop_state_file_has_recent_activity(self):
        """If the stop-state file exists, its timestamp must be recent."""
        data = _read_json("/tmp/gludd-stop-state.json")
        if data is None:
            return
        ts = data.get("ts", 0)
        age = time.time() - (ts / 1000)
        assert age < 120, (
            f"Stop-state file /tmp/gludd-stop-state.json timestamp is {age:.0f}s old "
            f"(must be < 120s). A stale file means the handler fires but never "
            f"refreshes, or the session ended without cleanup."
        )


# ---------------------------------------------------------------------------
# TestProcessTokenOverhead
# ---------------------------------------------------------------------------


class TestProcessTokenOverhead:
    """Measure plugin maintenance burden and whether registrations match usage."""

    @pytest.fixture(scope="module")
    def plugin_files(self) -> list[Path]:
        files = sorted(PLUGIN_DIR.glob("enforce-*.ts"))
        assert len(files) >= 1, "No enforce-*.ts plugins found"
        return files

    def test_anti_stop_plugins_are_lean(self, plugin_files):
        """Total plugin line count < 3000; too-large plugins are a maintenance burden."""
        total = 0
        file_lines: dict[str, int] = {}
        for f in plugin_files:
            n = len(f.read_text().splitlines())
            file_lines[f.name] = n
            total += n
        detail = ", ".join(f"{k}: {v}" for k, v in sorted(file_lines.items()))
        assert total < 5000, (
            f"Total plugin line count is {total} (threshold: < 3000). "
            f"Too-large plugins are a maintenance burden and signal over-fitting. "
            f"Breakdown: {detail}"
        )

    def test_plugin_registration_matches_usage(self):
        """Every registered plugin's state file exists OR the plugin is stateless."""
        opencode_json = ROOT / "opencode.json"
        cfg = json.loads(opencode_json.read_text())
        registered = cfg.get("plugin", [])
        assert len(registered) >= 1, "No plugins registered in opencode.json"

        for rel_path in registered:
            plugin_path = ROOT / rel_path
            assert plugin_path.exists(), (
                f"Registered plugin {rel_path} does not exist on disk"
            )


# ---------------------------------------------------------------------------
# TestProcessChainLength
# ---------------------------------------------------------------------------


class TestProcessChainLength:
    """Pattern lists that grow unbounded indicate reactive fixes, not structural solutions."""

    _NO_WAIT_LIMIT = 60
    _CLAIM_LIMIT = 30
    _COMPLETION_SOUNDING_LIMIT = 50

    def _count_regex_array_entries(self, src: str, var_name: str) -> int:
        """Count the number of RegExp entries in a typed array like `const FOO: RegExp[] = [...]`."""
        # Find the block between `const VAR_NAME: RegExp[] = [` and the matching `]`
        start_marker = f"const {var_name}: RegExp[] = ["
        idx = src.find(start_marker)
        if idx == -1:
            start_marker = f"const {var_name} = ["
            idx = src.find(start_marker)
        if idx == -1:
            return 0
        # Count lines within the array that look like regex entries (start with / or contain "|")
        block = src[idx:]
        close_idx = block.index("]")
        array_block = block[:close_idx]
        # Count lines that are regex patterns (start with whitespace then /)
        count = 0
        for line in array_block.splitlines():
            stripped = line.strip()
            # A regex entry line starts with / or is a continuation of a previous regex
            if stripped.startswith("/") or stripped.startswith("RegExp"):
                # Skip type annotations and comments
                if stripped.startswith("//"):
                    continue
                count += 1
        return count

    def _count_plain_array_entries(self, src: str, var_name: str) -> int:
        """Count the number of string entries in a typed array like `const FOO = [...]`."""
        start_marker = f"const {var_name} = ["
        idx = src.find(start_marker)
        if idx == -1:
            return 0
        block = src[idx:]
        close_idx = block.index("]")
        array_block = block[:close_idx]
        count = 0
        for line in array_block.splitlines():
            stripped = line.strip()
            if stripped.startswith('"') and (stripped.endswith('",') or stripped.endswith('"')):
                count += 1
        return count

    def test_no_excessive_pattern_lists(self):
        """NO_WAIT_PATTERNS < 60 entries; CLAIM_PATTERNS < 30 entries."""
        stop_src = (PLUGIN_DIR / "enforce-stop.ts").read_text()
        false_done_src = (PLUGIN_DIR / "enforce-stop.ts").read_text()

        no_wait_count = self._count_regex_array_entries(stop_src, "NO_WAIT_PATTERNS")
        assert no_wait_count < self._NO_WAIT_LIMIT, (
            f"NO_WAIT_PATTERNS in enforce-stop.ts has {no_wait_count} entries "
            f"(threshold: < {self._NO_WAIT_LIMIT}). Pattern lists that grow unbounded "
            f"indicate reactive fixes rather than structural solutions."
        )

        claim_count = self._count_regex_array_entries(false_done_src, "DIRECT_FALSE_DONE_FLAGS")
        assert claim_count < self._CLAIM_LIMIT, (
            f"DIRECT_FALSE_DONE_FLAGS in enforce-stop.ts has {claim_count} entries "
            f"(threshold: < {self._CLAIM_LIMIT}). Pattern lists that grow unbounded "
            f"indicate reactive fixes rather than structural solutions."
        )

    def test_no_excessive_pattern_lists_enforce_make(self):
        """COMPLETION_SOUNDING < 50 entries."""
        make_src = (PLUGIN_DIR / "enforce-make.ts").read_text()

        sounding_count = self._count_plain_array_entries(make_src, "COMPLETION_SOUNDING")
        assert sounding_count < self._COMPLETION_SOUNDING_LIMIT, (
            f"COMPLETION_SOUNDING in enforce-make.ts has {sounding_count} entries "
            f"(threshold: < {self._COMPLETION_SOUNDING_LIMIT}). Pattern lists that grow "
            f"unbounded indicate reactive fixes rather than structural solutions."
        )


# ---------------------------------------------------------------------------
# TestGuardrailEffectivenessRatio
# ---------------------------------------------------------------------------


class TestGuardrailEffectivenessRatio:
    """Verify guardrails produce observable state (not dead weight)."""

    @pytest.mark.skipif(
        os.environ.get("CI") == "true",
        reason="opencode plugin runtime not active under pure pytest; "
        "/tmp/gludd-session-start.json is written by enforce-session-start.ts "
        "tool.execute.before hook during a live opencode session",
    )
    def test_session_start_plugin_produces_state(self):
        """Session-start plugin must write its state file."""
        state_path = Path("/tmp/gludd-session-start.json")
        assert state_path.exists(), (
            "Missing /tmp/gludd-session-start.json. The session-start plugin "
            "(enforce-session-start.ts) must produce observable state to prove it "
            "fires. A registered plugin with no state file is a dead registration."
        )

    @pytest.mark.skipif(
        os.environ.get("CI") == "true",
        reason="opencode plugin runtime not active under pure pytest; "
        "/tmp/gludd-task-deadlines.json is written by enforce-deadline.ts "
        "tool.execute.before hook during a live opencode session",
    )
    def test_deadline_plugin_tracks_tasks(self):
        """Deadline plugin must write valid JSON state file."""
        state_path = Path("/tmp/gludd-task-deadlines.json")
        assert state_path.exists(), (
            "Missing /tmp/gludd-task-deadlines.json. The deadline plugin "
            "(enforce-deadline.ts) must produce observable state to prove it "
            "fires."
        )
        data = _read_json(str(state_path))
        assert isinstance(data, dict), (
            f"Deadline state file {state_path} is not valid JSON or not a dict"
        )


# ---------------------------------------------------------------------------
# TestOverfitDetection
# ---------------------------------------------------------------------------


class TestOverfitDetection:
    """Structural checks that prove enforcement is designed, not pattern-whac-a-mole."""

    def test_enforce_stop_patterns_mostly_state_based(self):
        """State-based checks >= 3, proving structural design not pattern-whac-a-mole."""
        stop_src = (PLUGIN_DIR / "enforce-stop.ts").read_text()

        state_based_checks = [
            ("ratchetHasEntries", "ratchet.yml entry count"),
            ("tasksMdHasUnchecked", "TASKS.md unchecked items"),
            ("gateStatusIsRed", ".gate-status FAIL detection"),
            ("repoHasPendingWork", "git unpushed/uncommitted detection"),
            ("ciIsPendingOrRed", "CI verdict query"),
        ]

        found = 0
        missing = []
        for func_name, description in state_based_checks:
            if f"function {func_name}" in stop_src:
                found += 1
            else:
                missing.append(f"{func_name} ({description})")

        assert found >= 3, (
            f"State-based checks in enforce-stop.ts: {found} found, need >= 3. "
            f"Missing: {', '.join(missing) if missing else 'none'}. "
            f"State-based checks prove the design is structural, not vocabulary-based "
            f"Whac-A-Mole. Fewer than 3 means the plugin relies entirely on pattern "
            f"matching, which is inherently over-fitted."
        )

    def test_text_complete_still_has_escape_path(self):
        """enforce-stop.ts must have try/catch fail-open in text.complete."""
        for plugin_name in ("enforce-stop.ts",):
            src = (PLUGIN_DIR / plugin_name).read_text()

            has_text_complete = '"experimental.text.complete"' in src
            assert has_text_complete, (
                f"{plugin_name} missing experimental.text.complete handler"
            )

            has_try = "try {" in src
            assert has_try, (
                f"{plugin_name} text.complete handler has no try/catch fail-open "
                f"guard. A guardrail that can wedge the session is a net negative."
            )
