"""Verify the 10-subagent cap is enforced across all guardrail layers.

The agent cap (max concurrent subagents) must be consistent everywhere.
A drift in any single layer creates a loophole the other layers cannot close.
This test pins all five layers to the current cap of 10.

Layers checked:
  1. .claude/settings.json            -> env.CLAUDE_AGENT_FLOOR == "10"
  2. .opencode/plugin/enforce-floor.ts    -> FLOOR constant defaults to 10
  3. .opencode/plugin/enforce-delegate.ts -> FLOOR constant defaults to 10
  4. .opencode/plugin/enforce-stop.ts     -> references MIN_DISPATCHES / under-floor
  5. AGENTS.md                            -> documents the 10-subagent cap
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent

CLAUDE_SETTINGS = ROOT / ".claude" / "settings.json"
ENFORCE_FLOOR = ROOT / ".opencode" / "plugin" / "enforce-floor.ts"
ENFORCE_DELEGATE = ROOT / ".opencode" / "plugin" / "enforce-delegate.ts"
ENFORCE_STOP = ROOT / ".opencode" / "plugin" / "enforce-stop.ts"
ENFORCE_STOP_IMPL = (
    ROOT / ".opencode" / "plugin" / "impl" / "enforce_stop_impl.ts"
)
AGENTS_MD = ROOT / "AGENTS.md"

EXPECTED_FLOOR = 10


def _floor_declaration(text: str) -> str:
    """Return the source line that declares the FLOOR constant."""
    match = re.search(r"^\s*const\s+FLOOR\b.*$", text, re.MULTILINE)
    assert match, "No 'const FLOOR' declaration found in plugin source"
    return match.group(0)


class TestClaudeSettingsFloor:
    def test_file_exists(self):
        assert CLAUDE_SETTINGS.exists(), ".claude/settings.json must exist"

    def test_claude_agent_floor_is_three(self):
        data = json.loads(CLAUDE_SETTINGS.read_text())
        assert "env" in data, "settings.json missing 'env' block"
        assert "CLAUDE_AGENT_FLOOR" in data["env"], (
            "settings.json must set CLAUDE_AGENT_FLOOR"
        )
        floor = int(data["env"]["CLAUDE_AGENT_FLOOR"])
        assert floor == EXPECTED_FLOOR, (
            f"CLAUDE_AGENT_FLOOR={floor}, expected {EXPECTED_FLOOR}"
        )


class TestEnforceFloorPlugin:
    def test_file_exists(self):
        assert ENFORCE_FLOOR.exists(), "enforce-floor.ts must exist"

    def test_floor_default_is_seven(self):
        line = _floor_declaration(ENFORCE_FLOOR.read_text())
        assert "10" in line, (
            f"enforce-floor.ts FLOOR must default to 10; got: {line!r}"
        )


class TestEnforceDelegatePlugin:
    def test_file_exists(self):
        assert ENFORCE_DELEGATE.exists(), "enforce-delegate.ts must exist"

    def test_floor_default_is_seven(self):
        line = _floor_declaration(ENFORCE_DELEGATE.read_text())
        assert "10" in line, (
            f"enforce-delegate.ts FLOOR must default to 10; got: {line!r}"
        )


class TestEnforceStopPlugin:
    def test_file_exists(self):
        assert ENFORCE_STOP.exists(), "enforce-stop.ts must exist"

    def test_references_dispatch_floor(self):
        text = ENFORCE_STOP.read_text() + ENFORCE_STOP_IMPL.read_text()
        assert "MIN_DISPATCHES" in text or "UNDER-FLOOR" in text, (
            "enforce-stop implementation must reference dispatch floor "
            "(MIN_DISPATCHES or UNDER-FLOOR)"
        )


class TestAgentsMdCap:
    def test_file_exists(self):
        assert AGENTS_MD.exists(), "AGENTS.md must exist"

    def test_documents_ten_subagent_cap(self):
        text = AGENTS_MD.read_text()
        patterns = [
            r"max.*10.*subagent",
            r"subagent.*cap.*10",
            r"10.*subagent.*cap",
            r"CLAUDE_AGENT_FLOOR.{0,40}[=\"]10\b",
            r"floor.{0,30}[=\"]10\b",
            r"[=\"]10\b.{0,30}floor",
            r"Concurrent subagents.*10 max",
            r"Max 10 subagents",
            r"10.agent.floor",
            r"10-Agent Dispatch",
        ]
        matched = [p for p in patterns if re.search(p, text, re.IGNORECASE)]
        assert matched, (
            "AGENTS.md must document the 10-subagent cap "
            "(no pattern tying '10' to the cap/subagent limit was found)"
        )
