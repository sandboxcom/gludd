"""Behavior pin: text.complete never fires on tool output (2026-07-12).

Research found: opencode's text.complete hook ONLY fires on text-end LLM
stream events — never on tool output from Read/Grep/Glob/Bash. The
_isInput.role field does not exist in the payload. The prior isToolOutput
guard was dead code. These tests verify:

1. isToolOutput guard is REMOVED from both plugins
2. RESEARCH FINDING comment is PRESENT in both plugins (prevents re-addition)
3. Enforcement logic (zero-streak, DELEGATE-FIRST, FALSE-DONE) still exists
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MULTITASK_PATH = ROOT / ".opencode/plugin/enforce-multitask.ts"
STOP_PATH = ROOT / ".opencode/plugin/enforce-stop.ts"
STOP_IMPL_PATH = ROOT / ".opencode/plugin/impl/enforce_stop_impl.ts"


def _src(path: Path) -> str:
    s = path.read_text()
    if path == STOP_PATH and STOP_IMPL_PATH.exists():
        s += "\n" + STOP_IMPL_PATH.read_text()
    return s


def _from_marker(src: str) -> str:
    idx = src.find('"experimental.text.complete"')
    assert idx >= 0, "text.complete handler not found"
    return src[idx:]


# ── enforce-multitask.ts ──────────────────────────────────────────────────

class TestMultitaskNoToolOutputGuard:
    def test_isToolOutput_variable_removed(self):
        src = _src(MULTITASK_PATH)
        assert "const isToolOutput" not in src, (
            "const isToolOutput variable declaration must be removed"
        )
        assert "if (isToolOutput)" not in src, (
            "if(isToolOutput) dead code block must be removed"
        )

    def test_research_finding_comment_present(self):
        src = _src(MULTITASK_PATH)
        assert "RESEARCH FINDING" in src, (
            "RESEARCH FINDING comment must document that text.complete never"
            " receives tool output"
        )

    def test_zero_streak_enforcement_still_active(self):
        handler = _from_marker(_src(MULTITASK_PATH))
        assert "zeroStreak++" in handler, "zero-streak tracking must survive"
        assert "MUST DISPATCH" in handler, "MUST DISPATCH block must survive"

    def test_hasResultMarker_runs_unconditionally(self):
        """hasResultMarker tracking runs on ALL text (no guard needed — all
        text in text.complete is agent-generated)."""
        handler = _from_marker(_src(MULTITASK_PATH))
        assert "hasResultMarker" in handler, "result marker tracking must exist"


class TestMultitaskEnforcementOrder:
    def test_enforcement_after_research_comment(self):
        handler = _from_marker(_src(MULTITASK_PATH))
        comment_idx = handler.find("RESEARCH FINDING")
        assert comment_idx >= 0
        after = handler[comment_idx:]
        assert "_state.prevMessageDispatches" in after, (
            "Enforcement logic must follow the research comment"
        )


# ── enforce-stop.ts ───────────────────────────────────────────────────────

class TestStopNoToolOutputGuard:
    def test_isToolOutput_variable_removed(self):
        src = _src(STOP_PATH)
        assert "const isToolOutput" not in src, (
            "const isToolOutput variable declaration must be removed"
        )
        assert "if (isToolOutput)" not in src, (
            "if(isToolOutput) dead code block must be removed"
        )

    def test_research_finding_comment_present(self):
        src = _src(STOP_PATH)
        assert "RESEARCH FINDING" in src, (
            "RESEARCH FINDING comment must document that text.complete never"
            " receives tool output"
        )

    def test_enforcement_still_active(self):
        handler = _from_marker(_src(STOP_PATH))
        assert "DELEGATE-FIRST" in handler, "DELEGATE-FIRST nag must survive"
        assert "FALSE-DONE" in handler, "FALSE-DONE detection must survive"
        assert "hasLocalWork" in handler or "HARD STOP" in handler, (
            "hasLocalWork block must survive"
        )

    def test_isDisengaged_runs_first(self):
        handler = _from_marker(_src(STOP_PATH))
        dis = handler.find("isDisengaged()")
        rf = handler.find("RESEARCH FINDING")
        assert dis >= 0 and rf >= 0
        assert dis < rf, f"isDisengaged() ({dis}) must precede research finding ({rf})"
