#!/usr/bin/env bash
# Stop hook — ENFORCES the invariant "keep working the subagent/multitasking
# architecture backlog until every item is actually fixed."
#
# WHY THIS EXISTS: the orchestrator repeatedly treated "fix my multitasking" as a
# finite set of deliverables, shipped one or two, and drifted back to object-level
# work — so the meta-work only happened when the user prompted, then stopped. A
# passive memory note did not prevent the relapse. This is the CODE enforcement:
# while the tracked backlog (scripts/multitasking_backlog.json) has any item that
# is not effectively-done (status==done AND non-empty evidence), turn-end is
# blocked with a reminder to keep an agent assigned to it. An item cannot be
# rubber-stamped done without evidence (the checker enforces that).
#
# ANTI-WEDGE: honours stop_hook_active (a second consecutive stop is allowed, so a
# session can always end after acknowledging). FAIL-OPEN on any error (missing
# script / malformed file / non-repo cwd) so a broken check can never wedge work.

REPO_DIR="/Users/shawnwilson/gludd"
CHECK="$REPO_DIR/scripts/multitasking_backlog_check.py"

input="$(cat 2>/dev/null || echo '{}')"
case "$input" in
  *'"stop_hook_active": true'*|*'"stop_hook_active":true'*) exit 0 ;;
esac

# Script not present yet (e.g. backlog branch not merged into this tree) -> fail open.
[ -f "$CHECK" ] || exit 0

# --assert-done: exit 0 = all effectively-done; 1 = work remains; 2 = file error.
( cd "$REPO_DIR" 2>/dev/null && python3 "$CHECK" --assert-done >/dev/null 2>&1 )
rc=$?

if [ "$rc" -eq 1 ]; then
  # ENFORCING (rev 2026-06-18b): genuinely BLOCK turn-end while the tracked backlog
  # has unverified items, via the Stop-hook JSON contract ({"decision":"block"} on
  # stdout, exit 0). This is what the user asked for -- the gate is not advisory.
  #
  # WHY exit 0 (not exit 1): a non-zero Stop-hook exit is reported as a HOOK ERROR
  # (the disruptive "stop hook error" the user flagged). A clean block is the JSON
  # decision + exit 0 -- enforcement without the error. json.dumps guarantees the
  # open-item list (arbitrary text) is escaped so the JSON can never be malformed.
  #
  # ANTI-WEDGE: stop_hook_active (checked above) allows a second consecutive stop,
  # so even if scripts/multitasking_backlog.json goes stale (items genuinely done
  # but the file reverted by a git checkout), the session can still end -- it just
  # nags once. Keep the json honest so this stays signal, not noise.
  open_list="$(cd "$REPO_DIR" 2>/dev/null && python3 "$CHECK" --list-open 2>/dev/null | tr '\n' '; ')"
  reason="MULTITASKING BACKLOG not done -- do NOT stop. Keep an agent assigned to the open/unverified items until each is status==done WITH evidence: ${open_list}"
  python3 -c 'import json,sys; print(json.dumps({"decision":"block","reason":sys.argv[1]}))' "$reason" 2>/dev/null && exit 0
  # python3 unavailable / failed -> fail OPEN (allow stop). Never wedge, never error.
  exit 0
fi

# rc 0 (all done) or rc 2 (file error -> fail open) -> allow.
exit 0
