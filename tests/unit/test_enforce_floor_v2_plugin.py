"""Contract tests for the cumulative session floor enforcement plugin."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-floor-v2.ts"
OPENCODE_CONFIG = ROOT / "opencode.json"


def _source() -> str:
    return PLUGIN_PATH.read_text(encoding="utf-8")


def test_plugin_exists_and_is_registered() -> None:
    assert PLUGIN_PATH.is_file()
    registered = json.loads(OPENCODE_CONFIG.read_text(encoding="utf-8"))["plugin"]
    assert "./.opencode/plugin/enforce-floor-v2.ts" in registered


def test_plugin_defaults_to_ten_inflight_dispatches() -> None:
    source = _source()
    assert 'GLUDD_DISPATCH_FLOOR || "10"' in source
    assert "Math.max(0, s.dispatched - s.completed)" in source
    assert "Math.max(0, FLOOR - inFlight)" in source


def test_plugin_tracks_dispatch_and_completion_events() -> None:
    source = _source()
    assert 'callTracker(["add", "1"])' in source
    assert 'callTracker(["complete", "1"])' in source
    assert "GLUDD_DISPATCH_STATE_FILE" in source


def test_plugin_registers_tool_and_supported_text_hooks() -> None:
    source = _source()
    assert '"tool.execute.before"' in source
    assert '"experimental.text.complete"' in source
    assert '"text.complete"' not in source


def test_plugin_isolated_from_subagents_and_can_be_disengaged() -> None:
    source = _source()
    assert source.count("if (isSubagent())") >= 2
    assert "if (isDisengaged())" in source
    assert 'GLUDD_FLOOR_V2_ENFORCE !== "0"' in source


def test_plugin_only_blocks_while_tracked_work_is_pending() -> None:
    source = _source()
    assert "function hasPendingWork()" in source
    assert "TASKS.md" in source
    assert "ratchet.yml" in source
    assert "if (!hasPendingWork()) return" in source
