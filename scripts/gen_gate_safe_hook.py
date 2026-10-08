#!/usr/bin/env python3
"""Generate the gate-safe agent_floor_stop.sh hook.

Usage: python3 scripts/gen_gate_safe_hook.py <output_path>

Writes the BLOCKING, gate-safe version of agent_floor_stop.sh.
Gate-safe rule: a running gate does NOT lower the read-only floor —
only heavy worktree-writers are capped during a gate.
"""
from __future__ import annotations

import os
import pathlib
import sys

CONTENT = r"""#!/usr/bin/env bash
# Stop hook (#79/#78): when explicitly configured, block turn-end below FLOOR.
# The default FLOOR is zero: never create read-only filler merely to occupy slots.
# A running gate does not rewrite an operator's explicit floor.
#
# ROBUST DESIGN: counts GROUND TRUTH -- the harness's own per-agent task .output
# files that were appended to within the activity window -- instead of a
# hand-maintained inc/dec counter that drifts and desyncs (it was reading 0 while
# agents ran / didn't run, so the hook could never reliably block). Ground truth
# is self-correcting: no dependency on PostToolUse/SubagentStop hooks firing or on
# $TMPDIR. FAIL-OPEN on any error (exit 0 = allow stop) so a hook bug can never
# wedge the session.
#
# DEAD-BAND CONTROLLER (rev 2026-06-17): this is the ONE hard gate -- it blocks
# turn-end only while live < FLOOR, and asks only for a SMALL refill back INTO the
# hold band [FLOOR, CEILING], not a full over-provision to the top. Inside the band
# the orchestrator holds and works in peace (the advisory hooks stay silent there).
# Transient/rate-limit dispatch errors are retryable (re-dispatch after backoff) --
# a one-line note, not a coercion. FAIL-OPEN on any error (exit 0 = allow stop).

HARD_CEILING=3
FLOOR="${CLAUDE_AGENT_FLOOR:-0}"
TARGET="${CLAUDE_AGENT_TARGET:-3}"
CEILING="${CLAUDE_AGENT_CEILING:-3}"
case "$FLOOR" in ''|*[!0-9]*) FLOOR="0" ;; esac
case "$TARGET" in ''|*[!0-9]*) TARGET="$HARD_CEILING" ;; esac
case "$CEILING" in ''|*[!0-9]*) CEILING="$HARD_CEILING" ;; esac
[ "$FLOOR" -gt "$HARD_CEILING" ] && FLOOR="$HARD_CEILING"
[ "$TARGET" -gt "$HARD_CEILING" ] && TARGET="$HARD_CEILING"
[ "$CEILING" -gt "$HARD_CEILING" ] && CEILING="$HARD_CEILING"
[ "$FLOOR" -gt "$CEILING" ] && FLOOR="$CEILING"
[ "$TARGET" -gt "$CEILING" ] && TARGET="$CEILING"
REFILL="$FLOOR"
WINDOW=90  # seconds; a live background agent streams tool/output well within this

# Never hard-wedge: if we're already inside a stop-hook continuation, allow stop.
input="$(cat 2>/dev/null || echo '{}')"
case "$input" in
  *'"stop_hook_active": true'*|*'"stop_hook_active":true'*) exit 0 ;;
esac

# Ground-truth live-agent count via the SHARED probe (scripts/agent_liveness.py):
# it counts only transcripts ACTIVELY appended during a short probe window, so a
# just-COMPLETED agent's final transcript write is NOT miscounted as live. The
# old inline heredoc used mtime<90s and reported 11 live when only 3 were running
# (a burst of completions all sat inside the 90s window) -- which HID the breach
# this hook exists to catch. Absolute path so the hook's CWD never matters;
# 2>/dev/null + the numeric-guard below keep it fail-open.
live="$(cd /Users/shawnwilson/gludd 2>/dev/null && python3 scripts/agent_liveness.py --count 2>/dev/null)"

case "$live" in
  ''|*[!0-9]*) exit 0 ;;   # couldn't determine -> fail open
esac

if [ "$live" -lt "$FLOOR" ]; then
  deficit=$((REFILL - live)); [ "$deficit" -lt 1 ] && deficit=1
  reason="AGENT-FLOOR (BLOCKING): ${live} live, below explicit floor ${FLOOR} "
  reason="${reason}(band ${FLOOR}-${CEILING}). A running gate does not rewrite the operator's floor. "
  reason="${reason}Dispatch about ${deficit} suitable disjoint task(s); never invent filler work. "
  reason="${reason}If no independent task exists, remove or lower the explicit floor."
  printf '{"decision":"block","reason":"%s"}\n' "$reason" >&2
  printf '{"decision":"block","reason":"%s"}\n' "$reason"
  exit 1
fi
exit 0
"""


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        print("Usage: python3 scripts/gen_gate_safe_hook.py <output_path>", file=sys.stderr)
        return 1
    out = pathlib.Path(argv[0])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(CONTENT)
    os.chmod(out, 0o755)
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
