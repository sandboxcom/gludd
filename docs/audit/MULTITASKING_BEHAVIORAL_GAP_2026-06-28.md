# Audit: Multitasking Behavioral Gap — Why the Agent Serializes Despite the 10-Agent Floor

Date: 2026-06-28
Status: **DIAGNOSIS + FIX PLAN** (fix 1 of 3 landed in parallel)
Scope: agent orchestration behavior — the gap between the *policy* ("maintain
≥10 concurrent subagents") and the *observed behavior* (serial 1–3 task
dispatches followed by long foreground stalls).

---

## 1. Executive Summary

The gludd project has had a "minimum 10 concurrent subagents" policy in
`AGENTS.md` since 2026-06-22 (raised from 6 → 10 by direct user mandate). It
is enforced by three plugins: `enforce-floor.ts`, `enforce-delegate.ts`, and
`enforce-stop.ts`. Despite this, **the agent repeatedly serializes work** —
dispatching 1–3 subagents and then blocking on a foreground `make` command,
dropping the live count to 0 for tens of minutes at a time. The user has
interrupted multiple sessions to ask "why is the floor not maintained?".

This audit traces that gap to its root cause and proposes three
high-leverage fixes. **Fix #1 landed in parallel with this audit**; fixes
#2 and #3 are the remaining work.

**TL;DR — the floor plugin is advisory-only by default.** It exports only
`experimental.chat.response.transform`, which can *inject guidance* into the
model's context but **cannot block** a tool call. The blocking hook
(`GLUDD_FLOOR_ENFORCE=1`) is unread by the daemon's default environment, so
the plugin fires its warning, the model ignores it, and serialization
continues. The fix is to add a `tool.execute.before` export to
`enforce-floor.ts` that hard-denies non-dispatch bash/edit calls when the
live subagent count is below 10.

---

## 2. Root Cause Analysis

### 2.1 What the policy says

From `AGENTS.md` (the "Minimum 10 Subagents at All Times" section):

> You MUST maintain a MINIMUM of 10 concurrent subagent threads doing useful
> work at all times. … The moment ANY subagent completes (or fails), you
> MUST immediately dispatch a replacement.

And from the "Message-shape mechanical rule":

> A response with 1–4 task dispatches is a **policy violation** when ≥3
> known work items remain.

### 2.2 What actually happens

Observed pattern across at least four sessions (2026-06-22 through
2026-06-28):

1. Agent dispatches a wave of 3–5 subagents.
2. One returns. Agent writes 2–3 paragraphs of analysis.
3. Agent runs `make gate` or `make test-unit` on the **main thread**
   (30+ minute foreground operation).
4. Live subagent count drops to 0. No new dispatches for the duration.
5. User interrupts: "why is the floor not maintained?"

The policy is on the books; the plugins exist; the behavior does not match.

### 2.3 Why the existing plugins don't catch it

`enforce-floor.ts` (and its siblings) register **only** an
`experimental.chat.response.transform` hook. That hook fires *after* the
model has produced a response — it can inject a `console.warn` into the
tool stream, but:

- **It cannot prevent the response from being emitted.** The transform
  runs on a completed assistant message; the tool calls are already in it.
- **It cannot deny a specific tool call.** Denial requires a
  `tool.execute.before` hook (PreToolUse equivalent), which returns a
  `permissionDecision: "deny"` *before* the tool runs.
- **The model treats `console.warn` output as advisory.** Even when the
  warning is loud ("FLOOR BREACH: live=3, floor=10"), nothing forces the
  next assistant turn to be a 7-wide dispatch wave.

The env var that *would* elevate the hook to blocking,
`GLUDD_FLOOR_ENFORCE=1`, is:

- Documented in `AGENTS.md` as the opt-in blocker.
- **Not set** in `opencode.json`'s `env` block (verified — see §5).
- **Not set** in the user's shell environment by default.

So in the default configuration, the floor plugin is purely advisory, and
the agent — which is trained to maximize helpfulness per turn, not to
maintain a dispatch ratio — ignores it in favor of "answer the user, then
do the next obvious thing serially."

### 2.4 The message-shape gap

Separately, `AGENTS.md` codifies the "≥5 dispatches or zero" message-shape
rule. But **no plugin enforces it**. The `enforce-stop.ts` plugin checks
for *deferral patterns* and *open-backlog stops*, but it does not inspect
the *shape* of a multi-tool response to count task dispatches. So a
response with 2 task dispatches + 1 bash call passes every guardrail while
still serializing work.

### 2.5 The "main thread budget" interaction

The "Main-thread command restriction" rule (also in `AGENTS.md`) forbids
running `make gate`, `make test-unit`, etc. on the main thread — they
block all subagent dispatch. But:

- The rule is **prompt-only**. No plugin denies a `make gate` Bash call on
  the main thread.
- The agent rationalizes it: "I need to verify this before committing" →
  runs `make test-unit` inline → 27-minute stall → floor collapses.
- The recommended workaround (`make gate-background`) exists but the agent
  doesn't reach for it because the foreground path "works."

This is the **compounding failure**: a serialized dispatch pattern (fix #1)
+ a tolerated main-thread stall (this section) = the floor at 0 for the
duration of any verification step.

---

## 3. The Three Highest-Leverage Fixes

### Fix #1 — Add `tool.execute.before` to `enforce-floor.ts` ✅ (DONE)

**Status:** Landed in parallel with this audit (see commit referenced in
`TASKS.md`).

**Change:** `enforce-floor.ts` now exports a second hook,
`tool.execute.before`, that:

1. Counts live subagents via the existing `agent_liveness.py` helper
   (already used by the response-transform hook).
2. Matches the incoming tool call against the "non-dispatch mutating"
   pattern (Bash targets other than read-only, Edit/Write to non-memory
   paths, mutating make targets like `git-commit`, `gate`, `ship`).
3. **Denies** the call with a clean
   `{permissionDecision: "deny", message: "..."}` JSON + `exit 0` when
   `live < CLAUDE_AGENT_FLOOR` (default 10).
4. Allows: Agent/Workflow/Task dispatch (always), read-only tools
   (Read/Glob/Grep), memory-path writes, and a bounded escape after
   `GLUDD_FLOOR_MAXBLOCK` (default 4) consecutive denials so the agent is
   never permanently wedged.

**Why this is the fix:** it converts the floor from advisory to mechanical
in the default configuration. The agent *cannot* run a foreground `make`
command while the floor is below 10 — it must dispatch first. The
env-var-gated version remains for operators who want advisory mode
(`GLUDD_FLOOR_ENFORCE=0`).

**Verification:** `tests/unit/test_opencode_plugin_ports.py` extended with
a static check that `enforce-floor.ts` exports both
`experimental.chat.response.transform` AND `tool.execute.before`. A
behavioral test (`scripts/test_floor_hook.py`) covers the deny/allow
matrix.

### Fix #2 — Message-shape guard (TODO)

**Problem:** Even with fix #1, an agent can dispatch 3 subagents, write a
paragraph of analysis, and then wait for results — never reaching the ≥5
wave that keeps the pipeline primed. Fix #1 only fires when the floor is
*already* breached (live < 10); it does not enforce the *shape* of each
dispatch wave.

**Fix:** Add a message-shape check to `enforce-stop.ts` (or a new
`enforce-shape.ts`). The hook inspects each assistant response that
contains tool calls:

- Count `task` / `agent` / `workflow` dispatches in the response.
- If count is in 1..4 AND the known-work backlog (read from
  `TASKS.md` / a shared state file) has ≥3 open items → **inject a loud
  directive** (transform hook) or, under `GLUDD_SHAPE_ENFORCE=1`, **deny
  the response** and force a re-issue with a wider wave.

**Why:** This catches the "2 dispatches + analysis prose + wait" pattern
*before* the floor collapses, rather than after. It is the proactive layer
that fix #1 (the reactive floor block) cannot provide.

**Risk:** False positives when the agent legitimately has only 2–3 items
left. Mitigation: the backlog-count gate (`open_work >= 3`) ensures the
shape rule only fires when there is clearly more to fan out.

### Fix #3 — Wire `opencode.json` env block + AGENTS.md mechanical rule (RESOLVED-MOOT)

**Status:** RESOLVED-MOOT — the opencode.json schema
(`https://opencode.ai/config.json`, `$defs.Config`) sets
`additionalProperties: false`, so a top-level `env` key is **not a valid
config key** and opencode silently ignores it. Adding the block would
have had zero effect.

**Resolution path (the one actually in place):**

1. **Shell environment** carries `CLAUDE_AGENT_FLOOR=10`,
   `GLUDD_FLOOR_ENFORCE=1`, `GLUDD_TASK_DEADLINE_ENABLED=1`. These are
   read by the plugins the standard way (`process.env.<NAME>`); they do
   not flow through opencode.json.
2. **Regression test** — `tests/unit/test_opencode_json_schema.py`
   pins this: `ALLOWED_TOP_LEVEL_KEYS` excludes `env`,
   `test_known_bad_keys_are_rejected` asserts `env` is classified
   unknown, and `test_current_config_has_no_env_top_level` asserts the
   live config has no `env` key. Re-introducing the bad key fails the
   gate.
3. **AGENTS.md mechanical rewrite** — the "Minimum 10 Subagents" and
   "Message-shape mechanical rule" sections already describe the
   machine-enforced behavior (deny conditions, `GLUDD_FLOOR_MAXBLOCK`
   escape). No further rewrite is required for this item; the residual
   mechanical-rule work is tracked under §5 (pull behavioral rules into
   the Mechanical Contract) — that is independent of the env-block
   question and remains open.

**Why this is the correct resolution, not a workaround:** the original
fix proposal assumed opencode.json supported a top-level `env`. It does
not. The 3-layer guardrail still holds — config permission (shell env +
`.opencode/plugin` permission rules in `opencode.json`) + runtime hook
(`tool.execute.before` in `enforce-floor.ts`) + agent prompt (AGENTS.md
mechanical sections). The "config permission" layer is just expressed
through the shell + the `permission` block in opencode.json rather than
a fictional `env` key.

---

## 4. The "Main Thread Budget" Interaction

Even after fixes #1–#3, there is a residual failure mode: the agent
dispatches 10 subagents, the floor is satisfied, and *then* it runs
`make gate` on the main thread — blocking all dispatch for 40 minutes
while the floor technically stays at 10 (no new dispatches, no
completions processed).

This is the "main-thread command restriction" rule, and it is currently
**prompt-only**. The recommended fixes:

1. **Make `enforce-floor.ts` deny long-running Bash targets on the main
   thread** regardless of live count. The deny list: `gate`, `test`,
   `test-unit`, `test-e2e`, `qa`, `validate`, `lint`, `typecheck`,
   `collect-check`, `smoke`, `git-add-all`, `commit-no-verify`,
   `git-push-branch-nv`. The allow list: `ci-verdict-fast`, `ship-commit`
   (which is itself meant to be dispatched), read-only targets.
2. **Make `make ship-commit` the only sanctioned commit path** — it
   internally dispatches the commit+push to a subagent. Document this in
   the AGENTS.md "Main-thread command restriction" section with a
   cross-reference to fix #1.
3. **Add a `make gate-background` target** (if it doesn't exist) that runs
   the gate via `run_in_background` and emits heartbeat progress, so the
   agent has a sanctioned non-blocking path.

The interaction matters because **fix #1 alone can mask the symptom**:
the floor stays at 10 (because no subagents complete during the stall),
but the orchestrator is effectively frozen. The behavioral fix in §5
addresses this at the prompt level; the fixes above address it
mechanically.

---

## 5. Behavioral Fix (Process-Level Rules)

The mechanical fixes (§3) are necessary but not sufficient. The agent
also needs internalized behavioral rules — the kind that survive even
when a plugin is misconfigured. These belong in AGENTS.md as **short,
imperative, mechanically-checkable rules**, not prose:

1. **Between waves, write zero analysis.** When a batch of subagent
   results returns, scan them in <5 seconds and dispatch the next wave.
   Do NOT write prose between waves. The analysis happens in the
   dispatch decisions themselves.
2. **Always have the next wave ready.** Before the current batch returns,
   know what the next 10 tasks will be. If you don't, dispatch research
   tasks as filler — never let "I don't know what to dispatch next" be a
   reason to serialize.
3. **Prefer uniform-duration tasks.** If 9 tasks take 2 min and 1 takes
   5 min, you're at 1 live agent for 3 minutes. Split the 5-minute task
   or batch the 2-minute ones to land together.
4. **Never run `make gate` on the main thread.** Use `make gate-background`
   or let CI be the gate (`GLUDD_CI_IS_GATE=1`). The main thread is for
   dispatch + integration only.
5. **If you catch yourself writing a paragraph, stop.** Paragraphs between
   tool calls are the leading indicator of a serialized stall. Replace
   the paragraph with a tool call.

These mirror the "Steady-state dispatch" subsection already in AGENTS.md,
but they should be pulled into the *Mechanical Contract* (the numbered
priority list at the top) so they are not buried in an appendix.

---

## 6. Evidence

- `enforce-floor.ts` exports (pre-fix #1): only
  `experimental.chat.response.transform`. Verified by
  `tests/unit/test_opencode_plugin_ports.py` (the test that prompted this
  audit).
- `opencode.json` has no top-level `env` block. Verified by reading the
  file.
- `AGENTS.md` "Minimum 10 Subagents" section states the policy in
  advisory terms ("MUST", "will be interrupted") rather than mechanical
  terms ("the plugin will DENY").
- Observed agent behavior: 4+ sessions with live-count drops to 0 during
  foreground `make` runs, documented in `BUGS.md` premature-stop entries.

---

## 7. Next Steps (tracked in TASKS.md)

- [x] Fix #1 — `tool.execute.before` in `enforce-floor.ts` (landed).
- [ ] Fix #2 — message-shape guard (`enforce-shape.ts` or extend
      `enforce-stop.ts`).
- [ ] Fix #3 — wire `opencode.json` env block + AGENTS.md mechanical
      rewrite.
- [ ] §4 — deny long-running Bash on main thread in `enforce-floor.ts`.
- [ ] §5 — pull behavioral rules into the Mechanical Contract section of
      AGENTS.md.

Each item gets its own commit; each commit cites this audit doc in its
message.
