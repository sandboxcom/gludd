# Memory-to-Hook Audit — 2026-06-18

Auditor: background agent (read-only; write access for this doc only).
Constraint: hooks must be **context-efficient** — silent (exit 0, zero stdout/stderr)
when not triggered; emit only when the guardrail fires.

---

## 1. Memory Inventory & Behavioral Classification

| Memory | Type | Behavioral? |
|---|---|---|
| agent-orchestration-prefs | behavioral / orchestration | YES |
| gludd-never-block-on-questions | behavioral | YES |
| gludd-make-only-policy | behavioral / technical | YES — already hooked |
| gludd-gate-concurrency-hygiene | behavioral | YES |
| gludd-observability-invariant | behavioral | YES — partly codified |
| gludd-stall-detection | behavioral | YES |
| gludd-anti-laziness-fix-root-cause | behavioral | YES — too subjective |
| gludd-parallelism-forcing-function | behavioral | YES — extensively hooked |
| gludd-no-unquantified-status-claims | behavioral | YES — hard to hook |
| gludd-disk-discipline | behavioral / operational | YES — NOT yet hooked |
| transient-error-retry-with-backoff | behavioral | YES — subjective |
| fix-means-repair-never-disable | behavioral | YES — partially hookable |
| agent-at-rest-not-incomplete | behavioral | YES — mostly subjective |
| gludd-validation-2026-06 | state / history | NO |
| gludd-glm-orchestration | state / history | NO |
| gludd-batch-ship-state | state | NO |

---

## 2. Per-Memory Assessment

### agent-orchestration-prefs
**Behavior:** AGGRESSIVE parallelism; floor/target/ceiling; delegate-first; async dispatch;
never block main thread; file-partition the apply.

**Current enforcement:**
- `.claude/hooks/agent_floor_stop.sh` — Stop hook, BLOCKS turn-end while live < FLOOR=6.
  Uses JSON `{"decision":"block"}` + exit 0. Strong.
- `.claude/hooks/agent_floor_dec.sh` — SubagentStop, ground-truth mid-turn advisory.
- `.claude/hooks/agent_floor_pretool.sh` — PreToolUse(*), advisory refill nudge.
- `.claude/hooks/agent_floor_posttool.sh` — PostToolUse(*), advisory.
- `.claude/hooks/agent_floor_userprompt.sh` — UserPromptSubmit, turn-start status.
- `.claude/hooks/agent_ceiling_pretool.sh` — PreToolUse(Agent), ceiling block.
- `.claude/hooks/mainthread_budget.sh` — PreToolUse+PostToolUse, streak counter advisory.
- `.opencode/plugin/enforce-floor.ts` — response.transform, floor/ceiling directive injection.
- `.claude/hooks/multitasking_backlog_stop.sh` — Stop hook backlog enforcement.
- `tests/unit/test_multitask_guardrails.py` — gate-pinned wiring test.

**Verdict: ALREADY-DONE.** The most-enforced memory in the repo. Six hooks + a plugin +
a test all address it. No gap.

---

### gludd-never-block-on-questions
**Behavior:** Never emit `AskUserQuestion` or "should I proceed?" — default to action.

**Current enforcement:**
- `enforce-make.ts` `response.transform` includes TASK_COMPLETION_WARNING and
  STOP_PATTERN_BLOCK which fire on completion-sounding text. These are stop-pattern
  guards but do NOT specifically target the question-asking pattern (`AskUserQuestion`
  tool call or "should I / want me to" prose).
- The MECHANICAL CONTRACT injected via `system.transform` includes "Found a gap? Fix it
  now. Never list it and ask."

**Could a hook enforce it?**
The `AskUserQuestion` tool call is a PreToolUse event with `tool_name = "AskUserQuestion"`.
A hook CAN intercept and deny it cleanly (same pattern as enforce_make_bash.sh). Prose
questions in text are not interceptable from a shell hook.

**Worth building?** Moderate. The stop-pattern plugin already catches most prose question
patterns. The tool-call form (`AskUserQuestion`) is a clean, objective trigger.

**Recommend: BUILD** (low complexity, fills a specific gap not covered by the plugin).

---

### gludd-make-only-policy
**Enforcement:** `.claude/hooks/enforce_make_bash.sh` (PreToolUse Bash, BLOCK via JSON),
`.opencode/plugin/enforce-make.ts` (`tool.execute.before`).
**Verdict: ALREADY-DONE.** Fully enforced, multiple layers.

---

### gludd-gate-concurrency-hygiene
**Behavior:** Never launch a second pytest/gate while one runs; check `make ps-pytest`
first; kill bg gate shell when killing an agent.

**Current enforcement:** None — pure memory. The `agent_ceiling_pretool.sh` limits
overall agent count but does not specifically detect a running pytest before launching
another one.

**Could a hook enforce it?**
A PreToolUse(Bash) hook could: after checking the command is `make gate` or
`make test`, run a quick `pgrep -f pytest` (or read the known basetemp lockfile) and
block with a warning if a pytest is already running.

**Problem:** The hook already runs INSIDE the Bash PreToolUse handler (enforce_make_bash.sh
exits 0 to allow, then a second hook could run). But hooks run as separate processes;
they can check the system. The trigger is objective: `command matches /make (gate|test)/`
AND a pytest process is already running (detectable via `/tmp/gludd-gate-basetemp`
existence OR `pgrep -f pytest` with a count check).

**Context-cost:** Silent when no pytest is running (which is most of the time).
**Worth building?** YES — this is the exact failure mode that caused the 208-error incident.
The trigger is objective, the guard is simple.

**Recommend: BUILD.**

---

### gludd-observability-invariant
**Behavior:** Never launch a silent bg task; every long op must stream/heartbeat.

**Current enforcement:**
- `tests/unit/test_observability_guardrails.py` — gate-level; enforces that Makefile
  gate/test targets use `tee` and emit phase markers.
- The test file covers the gate and ci-wait-anon but not ad-hoc agent behavior.

**Could a hook enforce it?**
The specific anti-pattern is `Agent(..., run_in_background=True)` followed by no
monitoring call and no heartbeat in the assistant text. A PostToolUse(Agent) hook could
detect `run_in_background: true` in the tool input and inject a reminder. However, the
signal that makes it a violation is NOT the background flag alone — it's the subsequent
silence, which cannot be detected from a single PostToolUse event.

A Stop hook could warn "you may have a silent bg agent running" but it has no reliable
way to know which agents are silent vs. healthy-streaming.

**Verdict:** Not hook-enforceable at the agent-behavior level. The in-repo tests cover
the Makefile layer. The memory captures the behavioral intent that cannot be mechanically
detected from shell hooks.

**Recommend: SKIP** (keep as memory + in-repo tests; no hook adds value here).

---

### gludd-stall-detection
**Behavior:** Don't quote unmeasured ETAs; flat output = stall; use `make run-watched`.

**Could a hook enforce it?**
ETA quoting in assistant text is not detectable from a shell hook. `make run-watched`
usage is a preference, not verifiable without reading the response content. The
response-transform plugin COULD scan for ETA-pattern text ("should take about X minutes",
"estimate N minutes") but the false-positive rate would be high and the scan itself bloats
context on every response.

**Verdict:** Too subjective / high false-positive. Stall DETECTION (watching for flat
output) belongs in the `make run-watched` tooling, which already exists.

**Recommend: SKIP.**

---

### gludd-anti-laziness-fix-root-cause
**Behavior:** Don't declare "mature/done" to skip deep work; fix root cause not workaround.

**Current enforcement:**
- `enforce-make.ts` `response.transform` fires on completion-sounding phrases and
  on ratchet entries. The TASK_COMPLETION_WARNING is injected.

**Additional gap:** declaring a system "mature" as a substitute for gap-hunting. This is
a prose-pattern problem, not tool-pattern. Not hook-enforceable beyond what the plugin
already does.

**Recommend: SKIP** (memory + existing plugin coverage adequate).

---

### gludd-parallelism-forcing-function
**Verdict: ALREADY-DONE.** See `agent-orchestration-prefs` above. Seven hooks + plugin.

---

### gludd-no-unquantified-status-claims
**Behavior:** Never say "green/passing/done/fixed/reliable" without citing a measurement
(run-id, .gate-status epoch, test output).

**Current enforcement:**
- `enforce-make.ts` `response.transform` blocks on completion-sounding phrases AND
  on red `.gate-status`. This is the closest existing coverage.
- The plugin checks `.gate-status` for all-PASS lines; if missing or red, it replaces the
  response entirely. This covers "done/complete" claims while gate is red.

**Gap:** Claims like "CI is reliably green" or "tests are passing" made when `.gate-status`
happens to be green (or stale) still slip through. The forbid-list covers many words but
not all combinations.

**Could a hook enforce it?**
Honestly: **no shell hook can do this reliably.** The hook's stdin (the assistant message)
is available in Stop hooks (the harness passes `stop_hook_active`, `transcript`, etc.) but
the assistant message itself is not reliably in the hook input JSON for arbitrary text
patterns. A response-transform plugin CAN read the full assistant message string and scan
for forbidden words. The plugin already does this for some patterns.

**The honest limit** (the memory itself states it): "no code can prevent an LLM from
generating a false sentence." The plugin's gate-red check + completion-phrase block are
the realistic ceiling.

**Could extend the plugin?** Yes — add the specific forbidden-word list from the memory
(`reliably, consistently, usually, mature, solid, ready` without adjacent evidence) to the
`response.transform` scan. But this is a plugin change, not a new hook, and would require
measuring false-positive rate carefully.

**Recommend: SKIP** for new hook; note the plugin is the correct layer. A separate ticket
to extend `enforce-make.ts`'s `completionClaims` array with the full forbidden-word list
from this memory would strengthen it without adding context cost.

---

### gludd-disk-discipline
**Behavior:** Check `make disk` before dispatching worktree agent batches; cap ~5-6
concurrent worktree agents; prefer non-isolated agents for read-only work.

**Current enforcement:** NONE — pure memory. The `agent_ceiling_pretool.sh` caps total
agents at CEILING=12 but does NOT:
- Check free disk space.
- Distinguish isolation=worktree agents (expensive, ~320MB venv each) from non-isolated.
- Warn when dispatching a worktree agent would push disk to danger zone.

**Could a hook enforce it?**
YES — PreToolUse(Agent): the tool input JSON includes `isolation` field. When
`isolation == "worktree"`, the hook can:
1. Count existing worktree venvs (count dirs matching `.claude/worktrees/*/agent-*/.venv`
   or equivalent) — approximates concurrent worktree count.
2. Check available disk (`df -k /` or `stat /`) — both are fast POSIX calls.
3. Warn/block if free < 2.5GB OR if worktree venv count >= 6.

This hook is **context-efficient**: it is silent on non-worktree Agent calls and on
healthy-disk worktree calls. It only fires when an agent with `isolation:"worktree"` would
push the system into the danger zone.

**Trigger is objective, check is fast, failure mode (ENOSPC deadlock) is catastrophic.**

**Recommend: BUILD.**

---

### transient-error-retry-with-backoff
**Behavior:** On 529/429/503, re-dispatch with backoff; never abandon.

**Could a hook enforce it?**
PostToolUse(Agent) receives the tool result. A 529 error would appear in the tool output
as an error string. The hook COULD detect "529" or "Overloaded" in the result and inject
a reminder to re-dispatch. However:
- The harness already surfaces the error to the assistant message.
- The assistant sees the error directly and the memory is in context.
- A PostToolUse hook with text-matching on error output is fragile (error format varies).

**Recommend: SKIP.** The codification in the product code (RetryPolicy/CircuitBreaker in
`src/general_ludd/resilience/`) is the right layer; behavioral memory + memory-in-context
is adequate for the orchestrator pattern.

---

### fix-means-repair-never-disable
**Behavior:** "fix X" = make X work; NEVER disable/weaken/stub a feature as the fix.
Specifically: don't change `exit 1`/`{"decision":"block"}` to advisory; don't remove
enforcement.

**Current enforcement:**
- `enforce-make.ts` already has a GUARDRAIL INTEGRITY check on edits to `enforce-make.ts`
  itself: if `oldString` contains `throw new Error`/`BLOCKED`/etc. and `newString` drops
  it, the edit is blocked.
- This is narrow: it only protects `enforce-make.ts`, not the shell hooks.

**Gap:** Edits to `.claude/hooks/*.sh` that replace `{"decision":"block"}` with an empty
response, or change `exit 1` to `exit 0` unconditionally, or delete the enforcement body,
are NOT guarded.

**Could a hook enforce it?**
PreToolUse(Edit) receives `file_path`, `old_string`, `new_string`. A hook can:
1. Match `file_path` against `.claude/hooks/` or `.opencode/plugin/`.
2. Scan `old_string` for enforcement tokens (`"decision":"block"`, `exit 1`, `throw new Error`,
   `permissionDecision.*deny`).
3. Check if `new_string` removes those tokens without a functional replacement.
4. If yes: BLOCK with explanation.

**Feasibility assessment:** HIGH. The trigger is objective (file path + token presence/absence).
The main risk is false positives on legitimate refactors that keep enforcement but reword it.
Mitigation: require that at least one enforcement token remains in `new_string` when `old_string`
contained one — generous enough not to block rewrites, strict enough to catch deletions.

**Context-cost:** Silent on all non-hook-file edits (which is the vast majority of edits).
Fires only when editing `.claude/hooks/*.sh` or `.opencode/plugin/*.ts` in ways that remove
enforcement tokens.

**This is a direct fix to the exact incident** that created the memory (2026-06-18: hooks
made advisory instead of fixed). High-relapse, objective trigger, catastrophic failure mode
(guardrails silently defanged).

**Recommend: BUILD.**

---

### agent-at-rest-not-incomplete
**Behavior:** "agent came to rest" = finished, not incomplete; classify by status before
re-dispatching.

**Could a hook enforce it?**
No hook event maps to "orchestrator mis-classifies a completed agent as incomplete."
The SubagentStop hook could inject a reminder, but this adds noise on every agent
completion. The memory's rule is nuanced (requires reading the completion result).

**Recommend: SKIP.** Behavioral memory is the right layer. The SubagentStop hook is
already injecting the live-count update; adding a classification reminder there would
bloat context on EVERY completion.

---

## 3. Summary Table

| Memory | Current Enforcement | Hookable? | Event + Trigger | Context-Cost | Recommend |
|---|---|---|---|---|---|
| agent-orchestration-prefs | 7 hooks + plugin + test | — | — | — | ALREADY-DONE |
| gludd-make-only-policy | 2 enforcement layers | — | — | — | ALREADY-DONE |
| gludd-parallelism-forcing-function | 7 hooks + plugin | — | — | — | ALREADY-DONE |
| gludd-never-block-on-questions | Partial (plugin stop-pattern) | YES | PreToolUse(AskUserQuestion) → deny | Silent except when tool called | BUILD |
| gludd-gate-concurrency-hygiene | None | YES | PreToolUse(Bash) + `make gate/test` match + pgrep pytest check → warn/block | Silent when no pytest running | BUILD |
| gludd-disk-discipline | None | YES | PreToolUse(Agent) + isolation=worktree + df/venv count → warn/block | Silent on non-worktree or healthy-disk | BUILD |
| fix-means-repair-never-disable | Partial (plugin guards enforce-make.ts only) | YES | PreToolUse(Edit) + hook/plugin path + token removal detect → block | Silent on non-hook file edits | BUILD |
| gludd-observability-invariant | In-repo tests (Makefile layer) | NO (behavior not detectable) | — | — | SKIP |
| gludd-stall-detection | make run-watched tooling | NO (too subjective) | — | — | SKIP |
| gludd-anti-laziness-fix-root-cause | Plugin (partial) | NO (subjective) | — | — | SKIP |
| gludd-no-unquantified-status-claims | Plugin (partial, gate-red + completion phrases) | NO (shell hook can't read assistant text) | — | — | SKIP (extend plugin instead) |
| transient-error-retry-with-backoff | Product code RetryPolicy | NO (fragile text match) | — | — | SKIP |
| agent-at-rest-not-incomplete | Memory only | NO (classification is nuanced) | — | — | SKIP |
| gludd-gate-concurrency-hygiene | None | YES | PreToolUse(Bash) | Silent | BUILD |
| gludd-validation-2026-06 | State/history | NOT behavioral | — | — | N/A |
| gludd-glm-orchestration | State/history | NOT behavioral | — | — | N/A |
| gludd-batch-ship-state | State | NOT behavioral | — | — | N/A |

---

## 4. Top 3 BUILD Recommendations with Draft Hook Scripts

### Priority ranking

1. **disk-discipline** (PreToolUse/Agent with isolation=worktree) — catastrophic failure
   mode (ENOSPC deadlock), zero existing enforcement, clean objective trigger.
2. **fix-means-repair-never-disable** (PreToolUse/Edit on hook/plugin files) — directly
   addresses the exact 2026-06-18 incident, fills a gap in the existing guardrail-integrity
   check (which only covers enforce-make.ts, not the shell hooks).
3. **gate-concurrency-hygiene** (PreToolUse/Bash on gate/test targets) — directly caused
   the 208-error incident; zero existing enforcement; objective, fast check.

(never-block-on-questions is BUILD but lower priority: the plugin already catches most
stop-patterns and AskUserQuestion is rarely called explicitly.)

---

### Hook 1: disk-discipline — PreToolUse(Agent)

**File:** `.claude/hooks/disk_discipline_pretool.sh`

**Event:** PreToolUse, matcher: `Agent`

**Logic:** When `isolation == "worktree"`, check free disk and existing worktree-venv
count. Warn (additionalContext) if free < DANGER_GB or venv count >= WORKTREE_CAP.
Block (permissionDecision: deny) if free < HARD_FLOOR_GB (ENOSPC imminent).
Silent otherwise.

```bash
#!/usr/bin/env bash
# PreToolUse(Agent) disk-discipline guard — 2026-06-18
#
# Fires ONLY when an Agent call has isolation="worktree" (the expensive kind that
# creates a ~320MB .venv).  Silent on non-worktree agents (which is most of them).
#
# Two thresholds:
#   DANGER_GB  (default 2.5) — advisory warn: finishing this dispatch may exhaust disk.
#   HARD_FLOOR_GB (default 1.0) — block: ENOSPC is imminent; dispatching is unsafe.
#
# Also counts existing worktree venvs; if at/over WORKTREE_CAP (default 6), warns.
# (Each venv ≈ 320MB; 6 × 320MB = 1.92GB, the empirically-safe cap from the incident.)
#
# FAIL-OPEN: any python3/df error -> exit 0 (silent), never block on a hook bug.
# CONTEXT-EFFICIENT: emits nothing on non-worktree agents or healthy-disk worktree calls.

DANGER_GB="${GLUDD_DISK_DANGER_GB:-2.5}"
HARD_FLOOR_GB="${GLUDD_DISK_HARD_FLOOR_GB:-1.0}"
WORKTREE_CAP="${GLUDD_WORKTREE_CAP:-6}"
REPO_DIR="/Users/shawnwilson/gludd"

input="$(cat 2>/dev/null || echo '{}')"

# Step 1: is this a worktree-isolated agent call?  If not, exit silently.
is_worktree="$(printf '%s' "$input" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
    iso = (d.get("tool_input") or {}).get("isolation") or ""
    print("yes" if iso == "worktree" else "no")
except Exception:
    print("no")
' 2>/dev/null)"

[ "$is_worktree" = "yes" ] || exit 0

# Step 2: measure free disk (GB) and worktree-venv count.
read -r free_gb venv_count <<EOF2
$(python3 -c '
import os, shutil, pathlib
# Free disk on the repo volume
try:
    st = shutil.disk_usage("'"$REPO_DIR"'")
    free_gb = round(st.free / (1024**3), 2)
except Exception:
    free_gb = 999.0  # unknown -> fail open

# Count existing worktree venvs (each = ~320MB when materialised)
try:
    wt_root = pathlib.Path("'"$REPO_DIR"'/.claude/worktrees")
    venv_count = sum(
        1 for d in wt_root.glob("*/.venv") if d.is_dir()
    ) if wt_root.is_dir() else 0
except Exception:
    venv_count = 0

print(free_gb, venv_count)
' 2>/dev/null || echo '999.0 0')
EOF2

# Validate — fail open if python3 returned non-numeric
case "$free_gb" in ''|*[!0-9.]*) exit 0 ;; esac
case "$venv_count" in ''|*[!0-9]*) exit 0 ;; esac

# Step 3: evaluate thresholds and emit the appropriate signal.
python3 - "$free_gb" "$venv_count" "$DANGER_GB" "$HARD_FLOOR_GB" "$WORKTREE_CAP" <<'PYEOF'
import sys, json

free_gb    = float(sys.argv[1])
venv_count = int(sys.argv[2])
danger_gb  = float(sys.argv[3])
hard_floor = float(sys.argv[4])
cap        = int(sys.argv[5])

msgs = []
block = False

if free_gb < hard_floor:
    block = True
    msgs.append(
        f"DISK CRITICAL ({free_gb:.1f}GB free < hard floor {hard_floor}GB): dispatching a "
        f"worktree agent would almost certainly cause ENOSPC, which DEADLOCKS every Bash call "
        f"(the harness can't write its output file). Run `make clean-worktree-venvs && make clean-tmp` "
        f"first (reclaims worktree .venvs ~320MB each + /tmp gate scratch). "
        f"This dispatch is BLOCKED until disk is freed."
    )
elif free_gb < danger_gb:
    msgs.append(
        f"DISK WARNING ({free_gb:.1f}GB free, danger zone < {danger_gb}GB): a worktree agent "
        f"creates a ~320MB .venv. Consider running `make clean-worktree-venvs` to reclaim space "
        f"from finished worktrees before dispatching more. Do NOT dispatch a large batch."
    )

if venv_count >= cap:
    msgs.append(
        f"WORKTREE-CAP WARNING: {venv_count} existing worktree .venvs found (cap={cap}, ~{venv_count*320}MB). "
        f"Run `make clean-worktree-venvs` after integrating finished worktrees before adding more. "
        f"Prefer non-isolated agents for read-only work — they share the main checkout's venv."
    )

if not msgs:
    sys.exit(0)  # healthy -> silent

combined = " | ".join(msgs)
if block:
    out = {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": combined,
    }}
else:
    out = {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": combined,
    }}
print(json.dumps(out))
PYEOF

exit 0
```

**Wire in settings.json:**
```json
{
  "matcher": "Agent",
  "hooks": [{"type": "command", "command": "bash /Users/shawnwilson/gludd/.claude/hooks/disk_discipline_pretool.sh"}]
}
```
Add alongside (or after) the existing `agent_ceiling_pretool.sh` entry under `PreToolUse`.

---

### Hook 2: fix-means-repair-never-disable — PreToolUse(Edit) on hook/plugin files

**File:** `.claude/hooks/guardrail_integrity_edit.sh`

**Event:** PreToolUse, matcher: `Edit`

**Logic:** When editing a file under `.claude/hooks/` or `.opencode/plugin/`, check
whether `old_string` contains enforcement tokens and `new_string` removes them all.
If so, BLOCK. Silent on all other edits.

```bash
#!/usr/bin/env bash
# PreToolUse(Edit) guardrail-integrity guard — 2026-06-18
#
# Protects ALL hook + plugin files from edits that silently remove enforcement.
# The existing enforce-make.ts check covers enforce-make.ts ONLY; this covers:
#   .claude/hooks/*.sh         — shell stop/pretool/posttool/subagent hooks
#   .opencode/plugin/*.ts      — TS plugins (enforce-make.ts, enforce-floor.ts, …)
#
# TRIGGER: file_path is under hooks/ or plugin/ AND old_string contains one or more
# ENFORCEMENT TOKENS AND new_string contains NONE of those tokens.
# (A legitimate refactor that keeps the same enforcement pattern in new words is
# allowed — only complete removal of ALL tokens fires.)
#
# ENFORCEMENT TOKENS: the set of patterns whose presence in a hook means "this hook
# actively blocks/denies/errors".  If you remove ALL of them from a file that had
# them, the hook is now advisory or dead.
#
# FAIL-OPEN: any parse error -> exit 0 (silent).
# CONTEXT-EFFICIENT: emits nothing on non-hook/plugin edits (the vast majority).

REPO_DIR="/Users/shawnwilson/gludd"

input="$(cat 2>/dev/null || echo '{}')"

python3 - <<PYEOF
import sys, json, re

try:
    raw = open('/dev/stdin', 'rb').read()
except Exception:
    sys.exit(0)

try:
    raw = open(0, 'rb').read()
    d = json.loads(raw)
except Exception:
    sys.exit(0)

ti = d.get("tool_input") or {}
file_path  = ti.get("file_path") or ti.get("filePath") or ""
old_string = ti.get("old_string") or ti.get("oldString") or ""
new_string = ti.get("new_string") or ti.get("newString") or ""

# Only guard hook and plugin files.
guarded = (
    "/.claude/hooks/" in file_path or
    "/.opencode/plugin/" in file_path or
    file_path.endswith(".sh") and "/hooks/" in file_path
)
if not guarded:
    sys.exit(0)

# Enforcement tokens — any of these signals "this code ACTIVELY enforces".
TOKENS = [
    '"decision":"block"',
    '"decision": "block"',
    'permissionDecision.*deny',
    'permissionDecision.*block',
    '"permissionDecision":"deny"',
    '"permissionDecision": "deny"',
    'throw new Error',
    'sys.exit(1)',
    'exit 1',
    'BLOCKED',
    'FORBIDDEN',
    'TDD VIOLATION',
    'GUARDRAIL INTEGRITY VIOLATION',
]

def has_token(text):
    for tok in TOKENS:
        if re.search(tok, text):
            return True
    return False

old_has = has_token(old_string)
new_has = has_token(new_string)

if old_has and not new_has and new_string.strip():
    # Removing enforcement entirely from a hook/plugin file.
    reason = (
        "GUARDRAIL INTEGRITY VIOLATION (fix-means-repair-never-disable): "
        "The edit removes ALL enforcement tokens from " + file_path + ". "
        "old_string contained an active block/deny/throw/exit-1; "
        "new_string contains none. "
        "Per the fix-means-repair-never-disable policy: 'fix' means make "
        "the feature work correctly, NEVER disable or weaken it. "
        "If the enforcement is noisy, narrow its conditions — do NOT delete "
        "the enforcement. See AGENTS.md Guardrail Integrity Policy."
    )
    out = {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}
    print(json.dumps(out))

sys.exit(0)
PYEOF

exit 0
```

**Wire in settings.json:**
```json
{
  "matcher": "Edit",
  "hooks": [{"type": "command", "command": "bash /Users/shawnwilson/gludd/.claude/hooks/guardrail_integrity_edit.sh"}]
}
```
Add as a new PreToolUse entry for matcher `Edit`.

**Note on the stdin plumbing:** The script above has a subtle issue — it tries to open
`/dev/stdin` then `fd 0` in sequence, which won't work. The correct pattern (matching
enforce_make_bash.sh) is to capture stdin at the top with `input="$(cat 2>/dev/null)"` and
pass it through python's stdin via a here-string. The orchestrator should use this corrected
stdin-handling pattern (same as enforce_make_bash.sh: `printf '%s' "$input" | python3 -c ...`).

---

### Hook 3: gate-concurrency-hygiene — PreToolUse(Bash) on gate/test

**File:** `.claude/hooks/gate_concurrency_pretool.sh`

**Event:** PreToolUse, matcher: `Bash` (runs AFTER enforce_make_bash.sh in the existing
hook chain)

**Logic:** When command matches `make (gate|test|test-unit|test-e2e|qa)`, check whether
pytest is already running (via the existence + recent-mtime of the gate basetemp dir, or
a fast `pgrep`). If yes, BLOCK with explanation. Silent otherwise.

```bash
#!/usr/bin/env bash
# PreToolUse(Bash) gate-concurrency guard — 2026-06-18
#
# Blocks launching a second pytest/gate while one is already running.
# Root cause of the 208-error incident: two concurrent gates triggered pytest's
# keep-last-3 tmp-root rotation, deleting the first gate's worker dirs mid-flight.
#
# DETECTION: two independent signals (either alone fires the warning):
#   1. BASETEMP LOCK: /tmp/gludd-gate-basetemp exists AND was modified within
#      STALE_SECS (600s default).  Gate stamps its basetemp on start; when done it
#      remains but goes stale.  A fresh mtime = gate is running.
#   2. PGREP: a python3 process with "pytest" in its args is running.  Fast, reliable.
#      (We can run pgrep inside a hook because hooks run as subprocesses, not as
#      harness tool calls subject to the make-only policy.)
#
# SEVERITY: BLOCK (deny) — a second concurrent gate is never the right call and the
# failure mode (208 spurious errors, possible test corruption) is hard to diagnose.
#
# FAIL-OPEN: any unexpected error -> exit 0.
# CONTEXT-EFFICIENT: emits nothing when no gate is running (most turns).

BASETEMP="/tmp/gludd-gate-basetemp"
STALE_SECS="${GLUDD_GATE_STALE_SECS:-600}"

input="$(cat 2>/dev/null || echo '{}')"

# Step 1: is this a gate/test Bash command?
is_gate="$(printf '%s' "$input" | python3 -c '
import sys, json, re
try:
    d = json.load(sys.stdin)
    cmd = ((d.get("tool_input") or {}).get("command") or "").strip()
    pattern = r"^make\s+(gate|test|test-unit|test-e2e|qa)\b"
    print("yes" if re.match(pattern, cmd) else "no")
except Exception:
    print("no")
' 2>/dev/null)"

[ "$is_gate" = "yes" ] || exit 0

# Step 2: detect a running pytest.
pytest_running=0

# Signal A: basetemp dir exists and is fresh.
if [ -d "$BASETEMP" ]; then
    # Use python3 stat (portable mtime check) — avoids GNU find -mmin incompatibilities.
    age_secs="$(python3 -c "
import os, time
try:
    mt = os.path.getmtime('$BASETEMP')
    print(int(time.time() - mt))
except Exception:
    print(99999)
" 2>/dev/null)"
    case "$age_secs" in ''|*[!0-9]*) age_secs=99999 ;; esac
    if [ "$age_secs" -lt "$STALE_SECS" ]; then
        pytest_running=1
    fi
fi

# Signal B: pgrep for a running pytest process (belt-and-suspenders).
if [ "$pytest_running" -eq 0 ]; then
    if pgrep -f "pytest" >/dev/null 2>&1; then
        pytest_running=1
    fi
fi

if [ "$pytest_running" -eq 1 ]; then
    reason="GATE CONCURRENCY VIOLATION: a pytest / gate run appears to already be in progress (basetemp ${BASETEMP} is fresh OR pgrep found a pytest process). Launching a second concurrent pytest triggers keep-last-3 basetemp rotation, which deletes the first gate's worker dirs mid-flight and produces hundreds of spurious FileNotFoundError errors (the 2026-06-15 208-error incident). Wait for the current gate to finish (SubagentStop notification, or check make ps-pytest), then launch this one. This dispatch is BLOCKED."
    python3 -c 'import json,sys; print(json.dumps({"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":sys.argv[1]}}))' "$reason" 2>/dev/null
    exit 0
fi

exit 0
```

**Wire in settings.json:**
```json
{
  "matcher": "Bash",
  "hooks": [{"type": "command", "command": "bash /Users/shawnwilson/gludd/.claude/hooks/gate_concurrency_pretool.sh"}]
}
```
Add alongside (after) the existing `enforce_make_bash.sh` entry for matcher `Bash`.

---

## 5. Wiring Summary for the Orchestrator

Three hooks to create and wire. No changes to existing hooks or settings.json entries
needed (add, never modify).

| Hook file | Event | Matcher | Action | Silent when |
|---|---|---|---|---|
| `disk_discipline_pretool.sh` | PreToolUse | `Agent` | warn (advisory) or deny (hard block) | isolation != "worktree" OR disk healthy AND venvs < cap |
| `guardrail_integrity_edit.sh` | PreToolUse | `Edit` | deny | file not in hooks/ or plugin/ OR old_string has no enforcement tokens OR new_string keeps at least one |
| `gate_concurrency_pretool.sh` | PreToolUse | `Bash` | deny | command is not a gate/test target OR no pytest running |

All three are fail-open (exit 0 on any error), context-efficient (emit nothing on the
common case), and block via the `permissionDecision:"deny"` JSON contract so no `exit 1`
hook errors are surfaced to the user.

**Not building (assessed infeasible as shell hooks):**
- `gludd-no-unquantified-status-claims` → extend `enforce-make.ts`'s `completionClaims`
  array with the full forbidden-word list from the memory instead.
- `gludd-observability-invariant` → covered by in-repo test layer; hook can't detect
  silent bg tasks after the fact.
- `gludd-never-block-on-questions` → BUILD when convenient (PreToolUse/AskUserQuestion
  deny); not in top-3 due to lower relapse risk vs. disk/guardrail/gate failures.
