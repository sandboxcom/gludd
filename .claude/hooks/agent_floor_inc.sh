#!/usr/bin/env bash
# PostToolUse(Agent): a subagent was just launched -> increment the live count.
# Counter is a flock-guarded tally so concurrent launches/returns don't race.
f="${TMPDIR:-/tmp}/claude-agent-floor.count"
{
  flock 9
  n=$(cat "$f" 2>/dev/null || echo 0)
  echo $((n + 1)) > "$f"
} 9>"$f.lock"
exit 0
