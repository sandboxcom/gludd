"""Verify dispatch-floor alignment across guardrail layers.

The code and active harness must share one three-subagent hard cap. A drift in
either direction creates a loophole the other layers cannot close.

Layers checked:
  1. .claude/settings.json            -> env.CLAUDE_AGENT_FLOOR == "0" (opt-in floor)
  2. .opencode/plugin/enforce-floor.ts    -> imports and clamps the canonical cap
  3. .opencode/plugin/enforce-delegate.ts -> imports and clamps the canonical cap
  4. .opencode/plugin/enforce-stop.ts     -> references MIN_DISPATCHES / under-floor
  5. AGENTS.md                            -> documents the cap and active harness
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent

CLAUDE_SETTINGS = ROOT / ".claude" / "settings.json"
ENFORCE_FLOOR = ROOT / ".opencode" / "plugin" / "enforce-floor.ts"
ENFORCE_DELEGATE = ROOT / ".opencode" / "plugin" / "enforce-delegate.ts"
ENFORCE_STOP = ROOT / ".opencode" / "plugin" / "enforce-stop.ts"
ENFORCE_STOP_IMPL = ROOT / ".opencode" / "plugin" / "impl" / "enforce_stop_impl.ts"
AGENTS_MD = ROOT / "AGENTS.md"

EXPECTED_HARNESS_FLOOR = 0
EXPECTED_CODE_DEFAULT_FLOOR = 0


def _floor_declaration(text: str) -> str:
    """Return the source line that declares the FLOOR constant."""
    match = re.search(r"^\s*const\s+FLOOR\b.*$", text, re.MULTILINE)
    assert match, "No 'const FLOOR' declaration found in plugin source"
    return match.group(0)


class TestClaudeSettingsFloor:
    def test_file_exists(self):
        assert CLAUDE_SETTINGS.exists(), ".claude/settings.json must exist"

    def test_claude_agent_floor_is_opt_in(self):
        data = json.loads(CLAUDE_SETTINGS.read_text())
        assert "env" in data, "settings.json missing 'env' block"
        assert "CLAUDE_AGENT_FLOOR" in data["env"], "settings.json must set CLAUDE_AGENT_FLOOR"
        floor = int(data["env"]["CLAUDE_AGENT_FLOOR"])
        assert floor == EXPECTED_HARNESS_FLOOR, (
            f"CLAUDE_AGENT_FLOOR={floor}, expected active-harness value {EXPECTED_HARNESS_FLOOR}"
        )


class TestEnforceFloorPlugin:
    def test_file_exists(self):
        assert ENFORCE_FLOOR.exists(), "enforce-floor.ts must exist"

    def test_floor_uses_canonical_cap(self):
        text = ENFORCE_FLOOR.read_text()
        line = _floor_declaration(text)
        assert "Math.min(" in line
        assert "clampDispatchCount" in text
        assert "../lib/multitask_config.ts" in text
        assert "String(MIN_DISPATCHES)" in text


class TestEnforceDelegatePlugin:
    def test_file_exists(self):
        assert ENFORCE_DELEGATE.exists(), "enforce-delegate.ts must exist"

    def test_floor_uses_canonical_cap(self):
        text = ENFORCE_DELEGATE.read_text()
        line = _floor_declaration(text)
        assert "clampDispatchCount" in line
        assert "../lib/multitask_config.ts" in text
        assert "String(HARD_MAX_DISPATCHES)" in text


class TestEnforceStopPlugin:
    def test_file_exists(self):
        assert ENFORCE_STOP.exists(), "enforce-stop.ts must exist"

    def test_references_dispatch_floor(self):
        text = "\n".join((ENFORCE_STOP.read_text(), ENFORCE_STOP_IMPL.read_text()))
        assert "MIN_DISPATCHES" in text or "UNDER-FLOOR" in text, (
            "enforce-stop.ts must reference dispatch floor (MIN_DISPATCHES or UNDER-FLOOR)"
        )


class TestAgentsMdCap:
    def test_file_exists(self):
        assert AGENTS_MD.exists(), "AGENTS.md must exist"

    def test_documents_three_subagent_cap(self):
        text = AGENTS_MD.read_text()
        patterns = [
            r"max.*3.*subagent",
            r"subagent.*ceiling.*three",
            r"HARD_MAX_DISPATCHES=3",
            r"Concurrent subagents.*3 max",
        ]
        matched = [p for p in patterns if re.search(p, text, re.IGNORECASE)]
        assert matched, (
            "AGENTS.md must document the canonical three-subagent cap"
        )
