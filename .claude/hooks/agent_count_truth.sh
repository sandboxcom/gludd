#!/usr/bin/env bash
# agent_count_truth.sh — UserPromptSubmit + Stop hook
#
# ANTI-FABRICATION GUARDRAIL (incident 2026-06-19):
#   The orchestrator reported "15 live agents" when ground truth was 5.
#   It counted dispatched-count, not measured live-count — a fabrication.
#   This hook makes the real number VISIBLE every turn so the agent cannot
#   claim a different count without contradicting what is printed here.
#
# Emits a single unmissable line to the agent context:
#   [GROUND-TRUTH] live subagents = <n> (source: scripts/agent_liveness.py)
#   -- any other number you state is a fabrication
#
# FAIL-OPEN by design: any error -> exit 0, no output (never wedge the session).
# Registered under BOTH UserPromptSubmit AND Stop in .claude/settings.json so
# the real count is present at the start of every turn AND at turn-end.

REPO="/Users/shawnwilson/gludd"
LIVENESS="$REPO/scripts/agent_liveness.py"

# Resolve live count via the shared ground-truth probe (FLOOR_LIVE_OVERRIDE
# is the documented test seam -- set it to an integer to bypass filesystem probe).
live=""
if [ -f "$LIVENESS" ]; then
    live="$(cd "$REPO" 2>/dev/null && python3 "$LIVENESS" --count 2>/dev/null || echo "")"
fi

# Validate: must be a non-negative integer. On any error: fail open, no output.
case "$live" in
    ''|*[!0-9]*)
        exit 0
        ;;
esac

# Emit the unmissable ground-truth line.
echo "[GROUND-TRUTH] live subagents = ${live} (source: scripts/agent_liveness.py) -- any other number you state is a fabrication"

exit 0
