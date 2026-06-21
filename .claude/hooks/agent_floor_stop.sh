#!/usr/bin/env bash
# Stop hook (#79/#78): BLOCK turn-end while fewer than FLOOR subagents are live.
# GATE-SAFE FLOOR RULE: a running gate does NOT lower the read-only floor.
# Only heavy worktree-writers are capped during a gate -- read-only agents
# (research/audit/review/explore) MUST still be dispatched to reach FLOOR.
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

FLOOR="${CLAUDE_AGENT_FLOOR:-6}"
TARGET="${CLAUDE_AGENT_TARGET:-10}"
CEILING="${CLAUDE_AGENT_CEILING:-12}"
# REFILL: refill just into the band (hysteresis), NOT up to TARGET.
# Clamp so that REFILL and the display band never invert when FLOOR is env-overridden
# above CEILING (e.g. CLAUDE_AGENT_FLOOR=999 for testing). The enforcement is correct
# either way; this is purely cosmetic -- avoids "hold band 999-12" in the reason string.
if [ "$FLOOR" -gt "$CEILING" ]; then
  DISPLAY_FLOOR="$CEILING"
else
  DISPLAY_FLOOR="$FLOOR"
fi
REFILL=$((DISPLAY_FLOOR + 2))
[ "$REFILL" -gt "$CEILING" ] && REFILL="$CEILING"
WINDOW=90  # seconds; a live background agent streams tool/output well within this

# ADVISORY MODE (2026-06-21, by user instruction): this hook previously emitted
# {"decision":"block"} to forcibly prevent turn-end while live<FLOOR. But the
# live-count probe (agent_liveness.py) only scans the Agent-tool task dir and
# CANNOT see Workflow subagents — so it reported "0 live" and trapped the
# orchestrator even while a Workflow was running a full parallel pool. That false
# alarm made it impossible to ever satisfy the floor during Workflow-based work.
# It is now ADVISORY: never blocks. Set GLUDD_FLOOR_ENFORCE=1 to restore blocking.
if [ "${GLUDD_FLOOR_ENFORCE:-0}" != "1" ]; then
  exit 0
fi

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
  # ENFORCING (rev 2026-06-18b): genuinely BLOCK turn-end via the Stop-hook JSON
  # contract -- {"decision":"block","reason":...} on stdout, exit 0. This is what
  # the user asked for: the floor DOES prevent stopping, it is not advisory.
  #
  # WHY exit 0 (not exit 1): in the Claude Code hook contract a non-zero exit from
  # a Stop hook is a HOOK ERROR (stderr shown to the user as a failure), which is
  # the disruptive "stop hook error" the user flagged. A *clean* block is the JSON
  # decision with exit 0. So we keep the enforcement (block) and drop the error.
  #
  # WHY python3 json.dumps: it guarantees well-formed, fully escaped JSON, so the
  # hook can never emit malformed stdout that the harness would report as an error
  # (a hand-built printf could break on quotes/newlines in the reason).
  #
  # ANTI-WEDGE: stop_hook_active (checked above) is the ONLY escape -- a second
  # consecutive stop is allowed so a genuine dead-end (rate-limited, no work left,
  # broken dispatch) can still end the session instead of looping forever. In the
  # normal cooperative case the block is real: it feeds the reason back, the main
  # loop dispatches agents, the floor is met, and the next stop succeeds.
  reason="AGENT-FLOOR ENFORCED: only ${live} subagent(s) live, below floor ${FLOOR} (hold band ${DISPLAY_FLOOR}-${CEILING}). Do NOT stop now. Dispatch disjoint Agent task(s) to refill to at least ${REFILL} -- read-only proposers/auditors/reviewers are allowed even while a gate runs -- then continue your work. If dispatch is blocked by a rate-limit/quota error, retry with backoff (that is the only acceptable reason to be below floor)."
  python3 -c 'import json,sys; print(json.dumps({"decision":"block","reason":sys.argv[1]}))' "$reason" 2>/dev/null && exit 0
  # python3 unavailable / failed -> fail OPEN (allow stop). Never wedge, never error.
  exit 0
fi
exit 0
