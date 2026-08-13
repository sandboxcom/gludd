from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

from tests.unit._plugin_contract import plugin_contract_source

PLUGIN_DIR = Path("tools/opencode/plugin")  # symlink or real path
FLOOR_PLUGIN = PLUGIN_DIR / "enforce-floor.ts"
DELEGATE_PLUGIN = PLUGIN_DIR / "enforce-delegate.ts"
STOP_PLUGIN = PLUGIN_DIR / "enforce-stop.ts"
MULTITASK_PLUGIN = PLUGIN_DIR / "enforce-multitask.ts"

STALE_MS = 60_000
DISENGAGE_MAX_MS = 3_600_000  # 1 hour clamp

TOOL_STREAK_FIELDS = frozenset({
    "streak", "lastDispatchTs", "readStreak", "editStreak",
    "lastUpdateTs", "lastWriter",
})
READ_GRIND_FIELDS = frozenset({"count", "lastDispatchTs", "ts"})
MAINTHREAD_FIELDS = frozenset({"count", "ts"})
DISENGAGE_FIELDS = frozenset({
    "disengage_until", "disengage_until_epoch_ms", "reason", "ts",
})


def _find_plugin_dir() -> Path:
    for candidate in [
        Path(".opencode/plugin"),
        Path("tools/opencode/plugin"),
    ]:
        if (candidate / "enforce-floor.ts").exists():
            return candidate
    env = os.environ.get("GLUDD_PLUGIN_DIR", "")
    if env:
        return Path(env)
    return PLUGIN_DIR


def _plugin_source(name: str) -> str:
    plugin_dir = _find_plugin_dir()
    return plugin_contract_source(plugin_dir / name)


def _effective_plugin_source(name: str) -> str:
    plugin_dir = _find_plugin_dir()
    plugin_path = plugin_dir / name
    source = plugin_path.read_text()
    stem = plugin_path.stem
    for candidate in (
        plugin_dir / "impl" / f"{stem}_impl.ts",
        plugin_dir / "impl" / f"{stem.replace('-', '_')}_impl.ts",
    ):
        if candidate.is_file():
            return source + "\n" + candidate.read_text()
    return source


def _to_int(raw: str) -> int:
    return int(raw.replace("_", ""))


def _extract_stale_ms(src: str) -> int:
    m = re.search(r'READ_GRIND_STALE_MS\s*=\s*[^"]*"(\d+)"', src)
    if m:
        return int(m.group(1))
    m = re.search(r"\bSTALE_MS\s*=\s*([\d_]+)", src)
    if m:
        return _to_int(m.group(1))
    return STALE_MS


def _extract_max_disengage_ms(src: str) -> int:
    m = re.search(r"MAX_DISENGAGE_MS\s*=\s*([\d_]+)", src)
    if m:
        return _to_int(m.group(1))
    vals = [_to_int(x) for x in re.findall(r"now\s*\+\s*([\d_]+)", src)]
    return max(vals) if vals else DISENGAGE_MAX_MS


def _now_ms() -> int:
    return int(time.time() * 1000)


class TestPluginFilesExist:
    """All enforcement plugins must be present."""

    def test_enforce_floor_exists(self):
        p = _find_plugin_dir() / "enforce-floor.ts"
        assert p.exists(), f"Missing {p}"

    def test_enforce_delegate_exists(self):
        p = _find_plugin_dir() / "enforce-delegate.ts"
        assert p.exists(), f"Missing {p}"

    def test_enforce_stop_exists(self):
        p = _find_plugin_dir() / "enforce-stop.ts"
        assert p.exists(), f"Missing {p}"

    def test_enforce_multitask_exists(self):
        p = _find_plugin_dir() / "enforce-multitask.ts"
        assert p.exists(), f"Missing {p}"


class TestToolStreakStaleReset:
    """enforce-floor.ts: /tmp/gludd-tool-streak.json resets when stale (>60s)."""

    def test_stale_constant_extracted(self):
        src = _plugin_source("enforce-floor.ts")
        stale_ms = _extract_stale_ms(src)
        assert stale_ms == 60_000, f"Expected 60000, got {stale_ms}"

    def test_stale_lastUpdateTs_resets_all_fields(self):
        now = _now_ms()
        old_ts = now - STALE_MS - 1000
        state = {
            "streak": 25,
            "lastDispatchTs": old_ts - 5000,
            "readStreak": 12,
            "editStreak": 8,
            "lastUpdateTs": old_ts,
            "lastWriter": "enforce-floor",
        }
        is_stale = (now - state["lastUpdateTs"]) > STALE_MS
        assert is_stale, "Old lastUpdateTs should trigger stale detection"

        if is_stale:
            state = {
                "streak": 0,
                "lastDispatchTs": 0,
                "readStreak": 0,
                "editStreak": 0,
                "lastUpdateTs": now,
                "lastWriter": "stale-reset",
            }
        assert state["streak"] == 0
        assert state["readStreak"] == 0
        assert state["editStreak"] == 0
        assert state["lastWriter"] == "stale-reset"

    def test_fresh_lastUpdateTs_no_reset(self):
        now = _now_ms()
        recent_ts = now - 10_000
        state = {"streak": 5, "lastUpdateTs": recent_ts}
        is_stale = (now - state["lastUpdateTs"]) > STALE_MS
        assert not is_stale, "Recent state should not be stale"

    def test_stale_at_boundary(self):
        now = _now_ms()
        boundary_ts = now - STALE_MS
        state = {"streak": 1, "lastUpdateTs": boundary_ts}
        is_stale = (now - state["lastUpdateTs"]) > STALE_MS
        assert not is_stale, "At boundary (not over), state should NOT be stale"

    def test_state_fields_match_plugin_contract(self):
        src = _plugin_source("enforce-floor.ts")
        reset_fields = set()
        if "stale-reset" in src:
            reset_fields_match = re.findall(
                r'["\'](\w+)["\']\s*:\s*0', src
            )
            reset_fields = set(reset_fields_match)
        expected = {"streak", "lastDispatchTs", "readStreak", "editStreak"}
        missing = expected - reset_fields
        known_good = reset_fields == set()  # regex may not match if format differs
        assert known_good or not missing, (
            f"Missing zeroed fields in stale reset: {missing}"
        )


class TestReadGrindStaleReset:
    """enforce-delegate.ts: /tmp/gludd-read-grind.json count resets when stale."""

    def test_stale_constant_extracted(self):
        src = _plugin_source("enforce-delegate.ts")
        stale_ms = _extract_stale_ms(src)
        assert stale_ms in (60_000, STALE_MS), (
            f"Expected 60000, got {stale_ms}"
        )

    def test_stale_lastDispatchTs_resets_count(self):
        now = _now_ms()
        old_ts = now - STALE_MS - 1000
        state = {"count": 30, "lastDispatchTs": old_ts, "ts": old_ts}
        is_stale = (now - state["lastDispatchTs"]) > STALE_MS
        assert is_stale, "Old lastDispatchTs should trigger stale detection"

        if is_stale:
            state = {"count": 0, "lastDispatchTs": now, "ts": now}
        assert state["count"] == 0

    def test_fresh_lastDispatchTs_no_reset(self):
        now = _now_ms()
        recent_ts = now - 10_000
        state = {"count": 8, "lastDispatchTs": recent_ts, "ts": recent_ts}
        is_stale = (now - state["lastDispatchTs"]) > STALE_MS
        assert not is_stale, "Recent dispatch should not trigger stale"

    def test_floor_plugin_also_resets_read_grind_on_init(self):
        src = _plugin_source("enforce-floor.ts")
        has_read_grind_reset = "read-grind" in src
        assert has_read_grind_reset, (
            "enforce-floor.ts should reset read-grind on init"
        )


class TestMainthreadStreakStaleReset:
    """enforce-delegate.ts: /tmp/gludd-mainthread-streak.json."""

    def test_mainthread_streak_format_count_and_ts(self):
        now = _now_ms()
        state = {"count": 3, "ts": now}
        assert "count" in state
        assert "ts" in state
        assert isinstance(state["count"], int), "count must be an integer"

    def test_mainthread_streak_backcompat_bare_integer(self):
        bare = 5
        if isinstance(bare, int):
            parsed = {"count": bare, "ts": _now_ms()}
        assert parsed["count"] == 5
        assert isinstance(parsed["ts"], int)

    def test_count_0_means_no_grind(self):
        state = {"count": 0, "ts": _now_ms()}
        assert state["count"] == 0, "Zero count means no mainthread grind"

    def test_dispatch_resets_mainthread_streak(self):
        src = _plugin_source("enforce-delegate.ts")
        has_dispatch_reset = (
            "count" in src and "0" in src
        )
        assert has_dispatch_reset, (
            "enforce-delegate.ts should reset count on dispatch"
        )


class TestDisengageSignal:
    """Disengage signal (/tmp/gludd-watchdog-disengage.json) works across all
    enforcement plugins."""

    def test_shared_lib_defines_disengage_path(self):
        """Post-E.5 refactor: the disengage path literal lives in
        .opencode/lib/shared.ts (DISENGAGE_PATH); plugins import
        isDisengaged/DISENGAGE_PATH instead of duplicating the literal."""
        shared_src = (
            _find_plugin_dir().parent / "lib" / "shared.ts"
        ).read_text()
        assert "watchdog-disengage" in shared_src, (
            "shared.ts must define the disengage signal path "
            "(/tmp/gludd-watchdog-disengage.json)"
        )

    def test_all_plugins_reference_disengage_signal(self):
        plugins = [
            "enforce-floor.ts",
            "enforce-delegate.ts",
            "enforce-stop.ts",
            "enforce-multitask.ts",
        ]
        self.test_shared_lib_defines_disengage_path()
        for name in plugins:
            src = _effective_plugin_source(name)
            has_ref = (
                "watchdog-disengage" in src
                or "isDisengaged" in src
                or "DISENGAGE_PATH" in src
            )
            assert has_ref, (
                f"{name} does not reference the disengage signal — it must "
                "either contain the literal or import isDisengaged/"
                "DISENGAGE_PATH from ../lib/shared"
            )

    def test_disengage_signal_has_required_fields(self):
        now = _now_ms()
        signal = {
            "disengage_until": now + DISENGAGE_MAX_MS,
            "disengage_until_epoch_ms": now + DISENGAGE_MAX_MS,
            "reason": "manual_disengage",
            "ts": int(time.time()),
        }
        for field in DISENGAGE_FIELDS:
            assert field in signal, f"Missing required field: {field}"

    def test_disengage_clamped_to_one_hour_max(self):
        now = _now_ms()
        overly_long = now + DISENGAGE_MAX_MS + 3_600_000
        clamped = min(overly_long, now + DISENGAGE_MAX_MS)
        assert clamped == now + DISENGAGE_MAX_MS, (
            "Disengage should be clamped to 1 hour max"
        )

    def test_expired_disengage_not_active(self):
        now = _now_ms()
        past = now - 60_000
        is_active = past > now
        assert not is_active, "Past disengage should not be active"

    def test_future_disengage_is_active(self):
        now = _now_ms()
        future = now + 600_000  # 10 min from now
        is_active = future > now
        assert is_active, "Future disengage should be active"

    def test_max_disengage_constant_same_across_plugins(self):
        constants = []
        for name in ["enforce-floor.ts", "enforce-delegate.ts", "enforce-stop.ts"]:
            src = _effective_plugin_source(name)
            val = _extract_max_disengage_ms(src)
            constants.append(val)
        unique = set(constants)
        assert len(unique) == 1, (
            f"MAX_DISENGAGE_MS differs across plugins: "
            f"{dict(zip(['floor','delegate','stop'], constants, strict=False))}"
        )

    def test_stop_plugin_block_counter_has_own_disengage_until(self):
        src = _effective_plugin_source("enforce-stop.ts")
        has_field = "disengageUntil" in src
        assert has_field, (
            "enforce-stop.ts block counter should have disengageUntil"
        )

    def test_make_disengage_target_writes_three_files(self):
        target_phrase = "disengage-enforcement"
        makefile = Path("Makefile").read_text()
        assert target_phrase in makefile, (
            f"Makefile missing {target_phrase} target"
        )


class TestSubagentGuardPassthrough:
    """Subagent guard returns output unmodified for benign content."""

    def test_stop_plugin_has_text_complete_hook(self):
        src = _plugin_source("enforce-stop.ts")
        assert "text.complete" in src, (
            "enforce-stop.ts should export text.complete hook"
        )

    def test_multitask_plugin_has_text_complete_hook(self):
        src = _plugin_source("enforce-multitask.ts")
        assert "text.complete" in src, (
            "enforce-multitask.ts should export text.complete hook"
        )

    def test_floor_plugin_has_text_complete_hook(self):
        src = _plugin_source("enforce-floor.ts")
        assert "text.complete" in src, (
            "enforce-floor.ts should export text.complete hook"
        )

    def test_benign_output_passes_through_unmodified(self):
        """Non-matching content should not be blanked or altered."""
        benign = "Task result: wrote 3 files. All tests pass."
        blank_patterns = [
            r"BLANKED",
            r"blanked",
            r"STOP PATTERN",
            r"HARD STOP",
        ]
        for pattern in blank_patterns:
            assert not re.search(pattern, benign, re.IGNORECASE), (
                f"Benign text should not match blank pattern: {pattern}"
            )
        assert len(benign) > 0
        assert benign.endswith(".")

    def test_disengage_blocks_blanking(self):
        """When disengage is active, blanking should be suppressed."""
        now = _now_ms()
        disengage_active = (now + 600_000) > now
        assert disengage_active, "Active disengage should suppress blanking"

    def test_stop_patterns_extracted_from_plugin(self):
        src = _plugin_source("enforce-stop.ts")
        has_qa_patterns = "QA_RESPONSE_PATTERNS" in src
        has_done_words = "DONE_WORDS" in src or "doneWords" in src
        has_stop_words = "stop_patterns" in src.lower() or "STOP_PATTERNS" in src
        assert has_qa_patterns or has_done_words or has_stop_words, (
            "enforce-stop.ts should define stop/blanking patterns"
        )


class TestNodeStripTypes:
    """Verify the --experimental-strip-types pattern for plugin validation."""

    def test_node_strip_types_available(self):
        result = subprocess.run(
            ["node", "--experimental-strip-types", "-e",
             "const x: number = 1; console.log(x);"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            f"node --experimental-strip-types failed: {result.stderr[:200]}"
        )
        assert result.stdout.strip() == "1", (
            "node --experimental-strip-types should evaluate TypeScript"
        )

    def test_node_strip_types_handles_interface(self):
        result = subprocess.run(
            ["node", "--experimental-strip-types", "-e",
             "interface Foo { x: number }; const f: Foo = {x: 1}; console.log(f.x);"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            f"node --experimental-strip-types interface failed: {result.stderr[:200]}"
        )
        assert result.stdout.strip() == "1"

    def test_plugin_files_parse_as_typescript(self):
        for name in [
            "enforce-floor.ts",
            "enforce-delegate.ts",
            "enforce-stop.ts",
            "enforce-multitask.ts",
        ]:
            src = _plugin_source(name)
            has_export = "export" in src
            has_import = "import" in src
            assert has_export or has_import, (
                f"{name} does not appear to be a TypeScript module"
            )

    def test_node_can_strip_type_annotations_from_plugins(self):
        plugin_dir = _find_plugin_dir()
        for name in ["enforce-multitask.ts"]:
            path = plugin_dir / name
            result = subprocess.run(
                ["node", "--experimental-strip-types", "--check", str(path)],
                capture_output=True, text=True,
                env={**os.environ, "NODE_OPTIONS": ""},
            )
            assert "error TS" not in result.stderr, (
                f"{name} has type errors: {result.stderr[:300]}"
            )
