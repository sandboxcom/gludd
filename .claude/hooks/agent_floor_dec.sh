#!/usr/bin/env bash
# SubagentStop hook (#79/#78): a subagent just returned -> a DRAIN happened. This
# is the moment the live count can dip BELOW the floor, mid-turn, long before the
# Stop hook (which only fires at turn-end) would ever notice. So this hook is the
# mid-turn enforcer.
#
# ROOT-CAUSE FIX (2026-06-16): the old version computed "launch N more" from the
# hand-maintained inc/dec counter in $TMPDIR/claude-agent-floor.count. That counter
# DRIFTS and DESYNCS — `make floor-status` caught it reading 0 while 6 agents were
# live. A wrong mid-turn number is worse than none: it told the orchestrator
# "floor held" during an actual breach. We now compute the deficit from GROUND
# TRUTH — the SAME shared probe (scripts/agent_liveness.py) the Stop hook uses —
# so the two enforcers always agree. The counter is still decremented below for
# pure observability (floor-status prints it) but no DECISION depends on it.
#
# FAIL-OPEN: any error -> emit nothing / exit 0, never wedge a turn.

FLOOR="${CLAUDE_AGENT_FLOOR:-6}"
TARGET="${CLAUDE_AGENT_TARGET:-10}"   # refill to TARGET, not FLOOR, so the next
                                      # burst of completions can't immediately re-breach.

# --- observability only: keep the inc/dec tally roughly in sync (not load-bearing) ---
f="${TMPDIR:-/tmp}/claude-agent-floor.count"
{
  flock 9
  n=$(cat "$f" 2>/dev/null || echo 1)
  n=$((n - 1)); [ "$n" -lt 0 ] && n=0
  echo "$n" > "$f"
} 9>"$f.lock" 2>/dev/null

# --- GROUND TRUTH: the shared liveness probe (same source the Stop hook trusts) ---
# A short probe keeps SubagentStop latency low; the just-stopped agent's transcript
# is frozen so it can't inflate the count via "grew", and the recent-write tail at
# worst over-counts it by 1 (safe direction: we'd refill one fewer, the Stop hook
# backstops). Absolute path so CWD never matters; numeric-guard => fail-open.
live="$(cd /Users/shawnwilson/gludd 2>/dev/null && FLOOR_PROBE_SECS="${FLOOR_PROBE_SECS:-0.8}" FLOOR_TAIL_SECS="${FLOOR_TAIL_SECS:-12}" python3 scripts/agent_liveness.py --count 2>/dev/null)"
case "$live" in
  ''|*[!0-9]*) exit 0 ;;   # couldn't determine -> fail open (silent)
esac

if [ "$live" -lt "$FLOOR" ]; then
  deficit=$((TARGET - live)); [ "$deficit" -lt 1 ] && deficit=1
  msg="AGENT-FLOOR BREACH (mid-turn, ground-truth): only ${live} subagent(s) streaming (floor ${FLOOR}, target ${TARGET}). A subagent just returned and dropped you below the floor. Dispatch ${deficit} Agent call(s) NOW on DISJOINT work to reach TARGET ${TARGET} BEFORE continuing other work -- do not wait for the Stop hook. Re-dispatch any agent that died on a transient/529 error."
  # decision:block on SubagentStop is not honored, so surface as a high-signal
  # systemMessage (rendered into the orchestrator's context immediately).
  # WHY python3 json.dumps: guarantees the message (which may contain em-dashes,
  # parens, etc.) is properly escaped in the JSON output. printf '{"systemMessage":
  # "%s"}' "$msg" is a latent bug -- if $msg ever gains a " or \ character, the JSON
  # becomes malformed and the harness surfaces a hook error even on exit 0.
  python3 -c 'import json,sys; print(json.dumps({"systemMessage":sys.argv[1]}))' "$msg" 2>/dev/null || true
  exit 0
fi

# Healthy: a quiet observability line (no directive, nothing to do). Use python3
# json.dumps for the same reason -- safety against future message changes.
healthy_msg="[agent-floor] subagent returned -> ${live} live / floor ${FLOOR} / target ${TARGET} -- floor held."
python3 -c 'import json,sys; print(json.dumps({"systemMessage":sys.argv[1]}))' "$healthy_msg" 2>/dev/null || true
exit 0
