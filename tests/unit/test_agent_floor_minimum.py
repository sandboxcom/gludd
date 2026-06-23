"""Verify the 10-subagent minimum is enforced across all guardrail layers.

The agent floor (minimum concurrent subagents) must be consistent everywhere.
A drift in any single layer creates a loophole the other layers cannot close.
This test pins all five layers to a floor of 10.

Layers checked:
  1. .claude/settings.json            -> env.CLAUDE_AGENT_FLOOR == "10" (or higher)
  2. .opencode/plugin/enforce-floor.ts    -> FLOOR constant defaults to 10
  3. .opencode/plugin/enforce-delegate.ts -> FLOOR constant defaults to 10
  4. .opencode/plugin/enforce-stop.ts     -> FLOOR constant defaults to 10
  5. AGENTS.md                            -> documents the 10-subagent minimum
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent

CLAUDE_SETTINGS = ROOT / ".claude" / "settings.json"
ENFORCE_FLOOR = ROOT / ".opencode" / "plugin" / "enforce-floor.ts"
ENFORCE_DELEGATE = ROOT / ".opencode" / "plugin" / "enforce-delegate.ts"
ENFORCE_STOP = ROOT / ".opencode" / "plugin" / "enforce-stop.ts"
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

    def test_claude_agent_floor_is_ten_or_higher(self):
        data = json.loads(CLAUDE_SETTINGS.read_text())
        assert "env" in data, "settings.json missing 'env' block"
        assert "CLAUDE_AGENT_FLOOR" in data["env"], (
            "settings.json must set CLAUDE_AGENT_FLOOR"
        )
        floor = int(data["env"]["CLAUDE_AGENT_FLOOR"])
        assert floor >= EXPECTED_FLOOR, (
            f"CLAUDE_AGENT_FLOOR={floor}, expected >= {EXPECTED_FLOOR}"
        )


class TestEnforceFloorPlugin:
    def test_file_exists(self):
        assert ENFORCE_FLOOR.exists(), "enforce-floor.ts must exist"

    def test_floor_default_is_ten(self):
        line = _floor_declaration(ENFORCE_FLOOR.read_text())
        assert "10" in line, (
            f"enforce-floor.ts FLOOR must default to 10; got: {line!r}"
        )


class TestEnforceDelegatePlugin:
    def test_file_exists(self):
        assert ENFORCE_DELEGATE.exists(), "enforce-delegate.ts must exist"

    def test_floor_default_is_ten(self):
        line = _floor_declaration(ENFORCE_DELEGATE.read_text())
        assert "10" in line, (
            f"enforce-delegate.ts FLOOR must default to 10; got: {line!r}"
        )


class TestEnforceStopPlugin:
    def test_file_exists(self):
        assert ENFORCE_STOP.exists(), "enforce-stop.ts must exist"

    def test_floor_default_is_ten(self):
        line = _floor_declaration(ENFORCE_STOP.read_text())
        assert "10" in line, (
            f"enforce-stop.ts FLOOR must default to 10; got: {line!r}"
        )


class TestAgentsMdFloorMinimum:
    def test_file_exists(self):
        assert AGENTS_MD.exists(), "AGENTS.md must exist"

    def test_documents_ten_subagent_minimum(self):
        text = AGENTS_MD.read_text()
        # Accept any phrasing that ties "10" to the subagent floor / minimum.
        patterns = [
            r"10[-\s]?subagent",
            r"subagent.{0,40}\bminimum\b",
            r"\bminimum.{0,40}\b10\b",
            r"floor.{0,30}\b10\b",
            r"\b10\b.{0,30}floor",
            r"CLAUDE_AGENT_FLOOR.{0,40}\b10\b",
        ]
        matched = [p for p in patterns if re.search(p, text, re.IGNORECASE)]
        assert matched, (
            "AGENTS.md must document the 10-subagent minimum "
            "(no pattern tying '10' to the floor/subagent minimum was found)"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
