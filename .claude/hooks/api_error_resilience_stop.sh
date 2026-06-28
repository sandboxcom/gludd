#!/usr/bin/env bash
# Stop hook: API-ERROR / TRANSIENT-FAILURE RESILIENCE.
#
# PURPOSE (requested 2026-06-25): "ensure that work is still running after any
# API error." When a subagent dies on a transient/API error — Anthropic 429 rate
# limit, 529 overload, 503, a "session limit" reset, or the 600s stall watchdog —
# the work that agent was doing is DROPPED. Without this, the orchestrator can see
# the failure notification, shrug, and stop — abandoning the work. This hook makes
# a transient failure a RETRY signal, not a stop signal: it detects recent
# transient-failure agents and BLOCKS turn-end with a directive to re-dispatch the
# dropped work, using EXPONENTIAL BACKOFF so it never hammers a provider that is
# actively overloaded, and ANTI-WEDGES so a persistent outage can still end the
# session instead of looping forever.
#
# Companion to agent_floor_stop.sh: the floor hook enforces the *count*; this hook
# enforces *recovery of failed work*. Codifies the repo's transient-error-retry-
# with-backoff policy (see docs/MULTITASKING_POLICY.md + the gateway RetryPolicy)
# at the session-orchestration layer.
#
# FAIL-OPEN on ANY error (exit 0 = allow stop) so a hook bug can never wedge a
# session. Disabled unless GLUDD_API_RESILIENCE=1 (default ON via settings.json).

set +e

# Toggle: default ON; set GLUDD_API_RESILIENCE=0 to disable the block (still logs).
if [ "${GLUDD_API_RESILIENCE:-1}" != "1" ]; then exit 0; fi

# Anti-wedge: a second consecutive stop-hook pass always allows the stop.
input="$(cat 2>/dev/null || echo '{}')"
case "$input" in
  *'"stop_hook_active": true'*|*'"stop_hook_active":true'*) exit 0 ;;
esac

_now="$(date +%s 2>/dev/null || echo 0)"
[ "$_now" = 0 ] && exit 0   # no clock -> fail open

# --- Locate this session's per-agent task .output transcripts -----------------
# The harness writes one <task-id>.output per subagent under the session's tasks/
# dir. We scan the TAILS of recently-written ones for terminal failure signatures.
# Glob is broad + fail-open so the exact session path never matters.
_taskglob=(/private/tmp/claude-*/*gludd*/*/tasks/*.output)
[ -e "${_taskglob[0]}" ] || _taskglob=("$HOME"/.claude/projects/*gludd*/*/tasks/*.output)
[ -e "${_taskglob[0]}" ] || exit 0   # no task files visible -> nothing to do

# Terminal transient-failure signatures (case-insensitive). Tight set: these mark
# an agent that DIED on a transient cause, not a model deciding something. We read
# only the last 4KB of each file (the agent's final state) to avoid matching code
# or discussion of these terms mid-transcript, and to stay fast.
_sig='stalled: no progress|stream watchdog|hit your session limit|session limit ·|rate_limit|rate limit|overloaded|overloaded_error|"type":"overloaded"|529 |503 Service|service unavailable|APIStatusError|APIConnectionError|terminal API error'

_failed=0
_recent_secs="${GLUDD_API_RESILIENCE_WINDOW:-900}"   # 15 min
_fpinput=""   # accumulates "<file>:<mtime>\n" lines for the failure-set fingerprint
for f in "${_taskglob[@]}"; do
  [ -f "$f" ] || continue
  # only files touched within the recent window
  _mt="$(stat -f %m "$f" 2>/dev/null || stat -c %Y "$f" 2>/dev/null || echo 0)"
  case "$_mt" in ''|*[!0-9]*) continue ;; esac
  [ "$((_now - _mt))" -gt "$_recent_secs" ] && continue
  # tail-only scan (last ~4KB) for a terminal failure signature
  if tail -c 4096 "$f" 2>/dev/null | grep -qiE "$_sig"; then
    _failed=$((_failed + 1))
    _fpinput="${_fpinput}${f}:${_mt}
"
  fi
done

[ "$_failed" -eq 0 ] && exit 0   # no recent transient failures -> allow stop

# --- Fingerprint of the current failure set ----------------------------------
# Identify WHICH failures these are (sorted <file>:<mtime> list), so we only fire
# on a NEW transient failure — not re-counting the SAME already-handled dead
# agents every Stop and escalating the backoff against a static, recovered set.
# Use stdlib tools (sort + cksum, or md5); fail-open to an empty fingerprint if
# unavailable so a missing tool can never wedge — an empty fingerprint just makes
# every run look "changed" (the old always-fire behavior), never blocks the user.
_fingerprint=""
if [ -n "$_fpinput" ]; then
  _fingerprint="$(printf '%s' "$_fpinput" | sort 2>/dev/null | cksum 2>/dev/null | tr -d ' \t' 2>/dev/null)"
  case "$_fingerprint" in ''|*[!0-9]*) _fingerprint="" ;; esac
fi

# --- Exponential backoff state ------------------------------------------------
# Track consecutive resilience triggers so repeated overload backs off and is
# eventually allowed to rest (anti-wedge), instead of tight-looping a re-dispatch
# against a provider that is down.
_state="${GLUDD_API_RESILIENCE_STATE:-/tmp/gludd-api-resilience-state}"
_count=0; _last=0; _stored_fp=""
if [ -r "$_state" ]; then
  # Format: "<count> <epoch> [<fingerprint>]". Backward-tolerant: an old
  # "<count> <epoch>" line (no fingerprint) reads _stored_fp="" -> treated as no
  # stored fingerprint -> first-time path (fires), never crashes on the old format.
  read -r _count _last _stored_fp < "$_state" 2>/dev/null
  case "$_count" in ''|*[!0-9]*) _count=0 ;; esac
  case "$_last" in ''|*[!0-9]*) _last=0 ;; esac
  case "$_stored_fp" in *[!0-9]*) _stored_fp="" ;; esac
fi

# --- STALE failure set: same fingerprint as last trigger -> reset + allow ------
# The current failures are the SAME dead agents we already fired on (no NEW
# transient failure since). Don't keep blocking + escalating the backoff on an
# already-handled, recovered set: RESET the streak to 0 and ALLOW the stop.
# (Only when we have a real fingerprint on both sides — empty means "unknown",
# which must fall through to the fire path, never silently reset.)
if [ -n "$_fingerprint" ] && [ "$_fingerprint" = "$_stored_fp" ]; then
  printf '%s %s %s\n' "0" "$_now" "$_fingerprint" > "$_state" 2>/dev/null || true
  msg="API-RESILIENCE: ${_failed} recent transient-failure agent(s), but the failure set is UNCHANGED since the last trigger (already handled/recovered — no NEW failure). Resetting backoff and allowing stop; not re-blocking on stale failures."
  python3 -c 'import json,sys; print(json.dumps({"hookSpecificOutput":{"hookEventName":"Stop","additionalContext":sys.argv[1]}}))' "$msg" 2>/dev/null
  exit 0
fi

# Reset the streak if it has been quiet for a while (errors are no longer a burst).
if [ "$((_now - _last))" -gt $((_recent_secs * 2)) ]; then _count=0; fi
_count=$((_count + 1))
# Store the NEW fingerprint alongside count+epoch so the next run can detect
# whether the failure set changed again.
printf '%s %s %s\n' "$_count" "$_now" "$_fingerprint" > "$_state" 2>/dev/null || true

# Backoff seconds: 30,60,120,240,480 capped at 600. The model is asked to wait
# this long before re-dispatching when a burst is ongoing.
_backoff=$((30 * (1 << (_count - 1))))
[ "$_backoff" -gt 600 ] && _backoff=600

# ANTI-WEDGE: after many consecutive triggers the provider is plausibly down for
# an extended period. Stop blocking so the session can rest; the operator/loop can
# resume later. (The floor hook's own rate-limit clause already permits resting.)
_maxtriggers="${GLUDD_API_RESILIENCE_MAX:-6}"
if [ "$_count" -gt "$_maxtriggers" ]; then
  msg="API-RESILIENCE: ${_failed} agent(s) failed on transient/API errors, and this is trigger #${_count} (persistent overload). Allowing stop to rest — re-dispatch the dropped work when the provider recovers (it is a retry signal, not abandonment). Backoff would be ${_backoff}s."
  python3 -c 'import json,sys; print(json.dumps({"hookSpecificOutput":{"hookEventName":"Stop","additionalContext":sys.argv[1]}}))' "$msg" 2>/dev/null
  exit 0
fi

# BLOCK turn-end: force re-dispatch of the dropped work with backoff. Clean JSON
# decision + exit 0 (a non-zero exit would surface as a disruptive hook error).
reason="API-ERROR RESILIENCE: ${_failed} subagent(s) died on a transient/API error (rate-limit 429 / overload 529 / 503 / session-limit / stall watchdog) within the last $((_recent_secs / 60))m. A transient failure is a RETRY signal, NOT a stop — the work those agents were doing is dropped and must be RE-DISPATCHED so work keeps running after the API error. Re-dispatch that work now (read-only re-runs are fine). If the provider is still actively overloaded, wait ~${_backoff}s (exponential backoff, trigger #${_count}) before re-dispatching so you don't hammer it. Do NOT abandon the dropped work."
python3 -c 'import json,sys; print(json.dumps({"decision":"block","reason":sys.argv[1]}))' "$reason" 2>/dev/null && exit 0

# python3 unavailable -> fail OPEN. Never wedge, never error.
exit 0
