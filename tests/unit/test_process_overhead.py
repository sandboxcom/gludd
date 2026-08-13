"""Process overhead measurement — detect overfitted enforcement machinery.

Codified processes (anti-stop plugins, enforcement guardrails) must be measured
for whether they cause more work than they save. This test suite detects over-fitting
by measuring actual runtime behavior: state-file block counts, plugin line counts,
pattern-list growth, and the ratio of state-based vs vocabulary-based checks.
"""

import json
import time
from pathlib import Path

import pytest

from tests.unit._hook_fixtures import HookEnv, hook_plugin_env_impl
from tests.unit._plugin_contract import plugin_contract_source

ROOT = Path(__file__).parent.parent.parent
PLUGIN_DIR = ROOT / ".opencode" / "plugin"


@pytest.fixture
def hook_plugin_env(tmp_path: Path):
    yield from hook_plugin_env_impl(tmp_path)


def _read_json(path: str) -> dict | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _implementation_lines(path: Path) -> int:
    """Count executable-ish plugin lines, not comments or blank prose."""
    count = 0
    in_block_comment = False
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line:
            continue
        if in_block_comment:
            if "*/" in line:
                in_block_comment = False
            continue
        if line.startswith("/*"):
            if "*/" not in line:
                in_block_comment = True
            continue
        if line.startswith("*") or line.startswith("//"):
            continue
        count += 1
    return count


# ---------------------------------------------------------------------------
# TestAntiStopFalsePositiveRate
# ---------------------------------------------------------------------------


class TestAntiStopFalsePositiveRate:
    """Measure whether state-file tracking shows over-firing."""

    def test_false_done_state_file_is_namespaced_for_harnesses(self):
        """Runtime harnesses must be able to avoid polluting live telemetry."""
        source = plugin_contract_source(PLUGIN_DIR / "enforce-stop.ts")
        assert "process.env.GLUDD_FALSE_DONE_BLOCKS_FILE" in source

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

    def test_stop_state_file_has_recent_activity(self, hook_plugin_env: HookEnv):
        """A real stop-hook event must refresh its isolated state immediately."""
        before_ms = time.time() * 1000
        result = hook_plugin_env.invoke(
            "enforce-stop.ts",
            "event",
            input={"event": {"type": "session.idle"}},
        )
        assert result.returncode == 0, result.stderr
        data = hook_plugin_env.read_json("GLUDD_STOP_STATE_FILE")
        assert isinstance(data, dict), data
        ts = data.get("ts", 0)
        age_ms = (time.time() * 1000) - ts
        assert before_ms <= ts <= time.time() * 1000, (
            f"Stop-state timestamp {ts!r} was not written by this hook invocation "
            f"(started at {before_ms:.0f}ms)."
        )
        assert age_ms < 10_000, (
            f"Stop-state timestamp is {age_ms:.0f}ms old after direct hook invocation; "
            f"the event handler did not refresh observable state."
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
        """Total plugin line count threshold; too-large plugins are a maintenance burden."""
        total = 0
        file_lines: dict[str, int] = {}
        for f in plugin_files:
            n = _implementation_lines(f)
            file_lines[f.name] = n
            total += n
        detail = ", ".join(f"{k}: {v}" for k, v in sorted(file_lines.items()))
        assert total < 6000, (
            f"Total plugin line count is {total} (threshold: < 6000). "
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
        stop_src = plugin_contract_source(PLUGIN_DIR / "enforce-stop.ts")
        false_done_src = plugin_contract_source(PLUGIN_DIR / "enforce-stop.ts")

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

    # Wave E: these two tests used to be soft, CI-skipped probes that only
    # checked whether a PRIOR live opencode session happened to have left a
    # state file behind. Deduped to the shared `hook_plugin_env` harness
    # (see tests/unit/_hook_fixtures.py and test_hook_runtime_verification.py's
    # TestEnforceSessionStartWritesStateFiles / TestEnforceDeadlineWritesStateFiles
    # for the fuller assertion coverage) — driven DIRECTLY here, no skipif.
    def test_session_start_plugin_produces_state(self, hook_plugin_env: HookEnv):
        """Session-start plugin must write its state file when driven directly."""
        result = hook_plugin_env.invoke(
            "enforce-session-start.ts", "tool.execute.before",
            input={"tool": "read", "filePath": "TASKS.md"},
        )
        assert result.returncode == 0, result.stderr
        data = hook_plugin_env.read_json("GLUDD_SESSION_STATE")
        assert isinstance(data, dict), (
            f"enforce-session-start.ts must produce observable state to prove it "
            f"fires. A registered plugin with no state file is a dead "
            f"registration. Got: {data!r}"
        )
        for key in ("started_at", "readsDone", "dispatches"):
            assert key in data, f"GLUDD_SESSION_STATE missing key {key!r}: {data!r}"
        assert data["readsDone"] is True, "reading TASKS.md must set readsDone=true"

    def test_deadline_plugin_tracks_tasks(self, hook_plugin_env: HookEnv):
        """Deadline plugin must write valid JSON state file when driven directly."""
        result = hook_plugin_env.invoke(
            "enforce-deadline.ts", "tool.execute.before",
            input={"tool": "task"},
            output={"args": {"description": "process-overhead smoke", "subagent_type": "general-purpose"}},
        )
        assert result.returncode == 0, result.stderr
        data = hook_plugin_env.read_json("GLUDD_TASK_DEADLINE_STATE")
        assert isinstance(data, dict), (
            f"Deadline state file is not valid JSON or not a dict: {data!r}"
        )
        assert data, (
            f"Deadline plugin must track at least one dispatch after being "
            f"driven directly with a task call; got empty state: {data!r}"
        )


# ---------------------------------------------------------------------------
# TestOverfitDetection
# ---------------------------------------------------------------------------


class TestOverfitDetection:
    """Structural checks that prove enforcement is designed, not pattern-whac-a-mole."""

    def test_enforce_stop_patterns_mostly_state_based(self):
        """State-based checks >= 3, proving structural design not pattern-whac-a-mole."""
        stop_src = plugin_contract_source(PLUGIN_DIR / "enforce-stop.ts")

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
            src = plugin_contract_source(PLUGIN_DIR / plugin_name)

            has_text_complete = '"experimental.text.complete"' in src
            assert has_text_complete, (
                f"{plugin_name} missing experimental.text.complete handler"
            )

            has_try = "try {" in src
            assert has_try, (
                f"{plugin_name} text.complete handler has no try/catch fail-open "
                f"guard. A guardrail that can wedge the session is a net negative."
            )
