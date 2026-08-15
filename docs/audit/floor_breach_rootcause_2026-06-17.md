> STATUS: POINT-IN-TIME analysis (dated). Reference/history — re-validate against current code/hooks before acting on its recommendations.

# Agent-Floor Breach Root-Cause Analysis
**Date:** 2026-06-17
**Author:** deep-read investigation (no Bash, no subagents)
**Subject:** Why the orchestrator repeatedly breaches floor=6 / target=10 despite five enforcement hooks

---

## 0. Source inventory

All citations below refer to line numbers in:

| Symbol | Path |
|--------|------|
| `liveness.py` | `scripts/agent_liveness.py` |
| `posttool` | `.claude/hooks/agent_floor_posttool.sh` |
| `stop` | `.claude/hooks/agent_floor_stop.sh` |
| `inc` | `.claude/hooks/agent_floor_inc.sh` |
| `dec` | `.claude/hooks/agent_floor_dec.sh` |
| `pretool` | `.claude/hooks/agent_floor_pretool.sh` |
| `ceiling` | `.claude/hooks/agent_ceiling_pretool.sh` |
| `userprompt` | `.claude/hooks/agent_floor_userprompt.sh` |
| `plugin` | `.opencode/plugin/enforce-floor.ts` |
| `settings` | `.claude/settings.json` |

---

## 1. How the ground-truth probe actually works

`liveness.py` implements a **delta-mtime probe**:

1. `snap()` records each `*.output` file's `mtime` (line 60-65).
2. Sleep `PROBE_SECS` (default 2.5 s; hooks override this to 0.5–0.8 s).
3. Re-`snap()` and compute:
   - **grew** = `m2[f] > m1[f]` — file was appended during the probe (line 77).
   - **recent** = `(now - m2[f]) < TAIL_SECS` — last write within the tail window (line 78-79).
4. A transcript counts as LIVE if `grew OR recent` (line 79-80).

Key parameters per hook invocation:

| Hook | `PROBE_SECS` | `TAIL_SECS` |
|------|-------------|------------|
| `pretool` | 0.5 | 4 |
| `posttool` | 0.6 | 4 |
| `dec` (SubagentStop) | 0.8 | default 6 |
| `stop` (Stop) | default 2.5 | default 6 |
| `userprompt` | 0.6 | 4 |
| `plugin` (enforce-floor.ts) | n/a — uses raw mtime<45 000 ms (lines 58-59) | — |

The plugin (`enforce-floor.ts`) does **NOT** use the delta-mtime probe at all. It counts any `.output` file whose `mtime` is within 45 s of now (line 59). This is the old mtime-window heuristic that `liveness.py`'s own docstring (lines 6-10) identifies as the original bug: a burst of completions all landing inside the window over-counts. The plugin and the shell hooks are measuring **fundamentally different things** and will frequently disagree.

---

## 2. Breach taxonomy: four distinct classes

### Class A — Wave-cliff drain

**Mechanism:**
When the orchestrator dispatches N agents simultaneously (as directed: "dispatch 8 DISJOINT agents NOW"), those agents receive similar workloads (read-only proposer tasks from the same backlog) and tend to have similar wall-clock lifetimes. Short read-only tasks complete in roughly 15–70 s based on observed durations (single-file reads, Grep/Glob searches, small analysis tasks). A wave dispatched together arrives together.

**Quantitative model:**
- Dispatch T=0: 10 agents launched, live=10.
- T≈15 s: first completions begin. Fast readers (single-file tasks) return.
- T≈30–70 s: bulk of wave completes. Live count drops from 10 toward 0 in a ~30–40 s window.
- That window is SHORTER than one `make gate` (~19 min), but also shorter than the orchestrator's own turn if the turn involves many sequential main-thread tool calls.

**Why hooks don't prevent it:**
The cliff happens between tool calls. Each hook invocation (probe latency 0.5–0.8 s + probe sleep) fires at a **specific tool boundary**, not continuously. If six agents complete between two consecutive tool calls on the main thread, the floor is already breached by the time the next PreToolUse or PostToolUse fires. The signal is reactive: it notices the breach but cannot prevent the transient drop to zero.

**Is it real/unavoidable?**
Partially real — any reactive signal has this race. But the *severity* is exacerbated by dispatching all agents at once on similar-sized tasks (see Section 4, Fix i and Fix ii).

---

### Class B — Refill lag

**Mechanism:**
After the wave-cliff fires the floor-breach signal, the orchestrator must:

1. Receive the SubagentStop hook message for each returning agent (~N messages).
2. Read/apply the agent's output (Read tool calls, Edit tool calls, or simply parsing the task notification).
3. *Then* dispatch the refill batch.

Each step is a main-thread tool call. Between step 1 and step 3 there are potentially 2N or more tool calls (one read + one edit per agent result) during which the live count is zero or near zero, and the hooks are firing breach signals on every single one of them. The orchestrator is receiving breach signals faster than it can act on them.

**Quantitative estimate:**
- 10 agents returning: 10 SubagentStop events → 10 `dec` hook invocations.
- Each invocation costs ≈ 1.3 s (0.8 s probe sleep + overhead).
- Result-processing: suppose each result needs 1 Read + 1 Edit = 2 tool calls; 10 agents = 20 tool calls.
- Each of those 20 calls fires pretool (0.5 s probe) + posttool (0.6 s probe) = 1.1 s overhead each.
- Total lag between cliff and first possible refill dispatch: realistically 15–60 s depending on how deeply the orchestrator reads each result before deciding to dispatch.

**Why hooks don't prevent it:**
The hooks are signals; they cannot force an immediate Agent dispatch. If the orchestrator chooses (or is constrained by the task flow) to read all results before dispatching, the breach persists through that entire reading phase.

**Is it real/avoidable?**
Mostly avoidable. The orchestrator should dispatch the refill batch **immediately upon the first breach signal**, before reading remaining results. Reading results and dispatching refills should be interleaved, not sequential.

---

### Class C — Uninterruptible-op dip

**Mechanism:**
Certain main-thread operations are long, foreground, and contiguous from the dispatch standpoint:

- `make gate` / `make test`: ~19 min foreground. While this runs, the Bash tool is blocked. The harness fires no tool boundaries (no PreToolUse, no PostToolUse) because no new tools are being called — the single Bash call is still in flight.
- A long `Read` of a very large file: less extreme, but still a single tool call.
- A sequence of Edits on a large file.

During a foreground `make gate`, ALL agents dispatched before it started will complete (most in 15–70 s; the gate takes 19 min). By the time the Bash call returns, the live count is guaranteed to be zero. The Stop hook will then block turn-end, but the 19-minute gap had NO hook fire at all.

**Why hooks don't prevent it:**
PreToolUse fires BEFORE the Bash call starts. PostToolUse fires AFTER it returns. The gap between those two hook firings is 19 minutes. There is no mechanism to inject a dispatch mid-Bash-call. This is a structural limitation of the hook architecture: hooks fire at tool boundaries, not asynchronously.

**Is it real/avoidable?**
This class is **structurally unavoidable** if a foreground long-running op is required. However, it can be mitigated by:
- Running `make gate` in background (via `run_in_background`), which keeps tool boundaries available.
- Accepting a documented dip during the single uninterruptible op (see Section 4, Fix iv).

---

### Class D — Probe undercount of just-completed agents

**Mechanism:**
`liveness.py` is deliberately biased toward undercount (docstring line 22-26). An agent that is "live but momentarily quiet" — blocked waiting for a long `make test` inside a worktree, or paused between tool calls — may not grow its transcript during the 0.5–0.8 s probe window. It is then counted as dead even though it is still running.

**Quantitative impact:**
- Probe window 0.5 s: an agent making one tool call every 2–5 s (normal reasoning cadence) has a 75–90% chance of NOT growing its transcript during any given 0.5 s probe.
- With 6 live agents, expected undercounted agents: 0.75 × 6 = 4–5 agents missed per probe.
- This means a genuinely healthy system with 10 agents may report live=2–4, immediately triggering a breach signal.

**The `recent` tail mitigates but does not eliminate this:**
TAIL_SECS=4 (in the pre/posttool hooks) means an agent that wrote anything in the last 4 s is counted. If an agent's tool calls are spaced more than 4 s apart (e.g. waiting for a `make test` result, or doing inference), it falls out of the tail window too.

**The plugin makes this worse:**
`enforce-floor.ts` uses a 45 s window (line 59) but no delta probe. A just-completed agent whose last write was 20 s ago would be counted as ACTIVE by the plugin but DEAD by `liveness.py`. The two systems are inverted in their error direction: the plugin over-counts, the shell hooks under-count.

**Is it real/avoidable?**
The undercount is real and intentional. The effect is that the hooks fire breach signals during periods when the floor is actually held. This causes unnecessary refill dispatches, which themselves churn through the ceiling and trigger ceiling-breach signals, creating an oscillation.

---

## 3. Interaction effects and compound failures

The four classes combine in predictable ways:

### 3.1 The oscillation loop

1. Wave of 10 agents dispatched (Class A cliff incoming).
2. Agents complete in 30–60 s → live count cliffs to 0–2 (Class A).
3. PostToolUse fires, probe returns live=2 (some quieter agents undercounted — Class D).
4. Breach signal: dispatch 8 more. Orchestrator must first read wave results (Class B lag).
5. During reading: 20+ more tool calls, each firing breach signals. Orchestrator dispatches refill mid-reading.
6. Refill wave of 8 dispatched; plus 2 agents from original wave that were undercounted and are still running = 10 live.
7. Ceiling hook fires (live=10; CEILING=12 — barely held). Or if any undercount was severe, 12+ triggers ceiling breach.
8. Back to step 1.

### 3.2 The plugin/hook disagreement

The plugin (`enforce-floor.ts`) uses `mtime < 45 000 ms` (line 59). The shell hooks use the delta-mtime probe. They will frequently produce different counts:

- Just after a wave completion: plugin counts all recently-completed agents as active; shell hooks count zero. Plugin says healthy; hooks say breach. The orchestrator sees conflicting signals.
- Just after a wave dispatch: plugin immediately counts all new agents (mtime updated on creation); shell hooks require actual file growth. Plugin may show 12; hooks show 2.

This disagreement means the text the orchestrator actually sees (plugin output appended to the model's response) contradicts the hook system messages (PreToolUse context), creating noise.

### 3.3 The SubagentStop hook cannot block

`agent_floor_dec.sh` line 44 notes: "decision:block on SubagentStop is not honored, so surface as a high-signal systemMessage." The dec hook emits a `systemMessage` but cannot stop the orchestrator from continuing. Combined with the fact that N agents returning generates N simultaneous SubagentStop firings, the orchestrator receives a flood of N identical "breach" messages at once, all at the same live count. These N messages consume context and may cause the model to "respond" to them rather than dispatch agents first.

---

## 4. Concrete fixes ranked by impact

### Fix i — Larger batches: minutes not seconds (HIGHEST IMPACT)

**Problem addressed:** Class A (wave-cliff), Class D (undercount oscillation).
**Mechanism:** If each agent task takes 3–10 minutes instead of 15–70 seconds, the cliff is dramatically slower. A 5-minute task means the wave drains over 5 minutes, not 30–60 seconds. The orchestrator has 5 minutes of tool boundaries to refill at a measured pace.

**Implementation:**
Instead of dispatching 8 single-file-read proposers, dispatch fewer agents with LARGER batch assignments: "read and analyze files X, Y, Z; cross-reference with A, B; produce a consolidated patch proposal." Each agent should have 10–20 minutes of useful work.

**Expected improvement:** The cliff drops from "live=10 → 0 in 60 s" to "live=10 → 7 in 5 min → 4 in 10 min," giving the orchestrator many tool-boundary opportunities to refill gradually. Floor breaches become brief and infrequent rather than total and sustained.

---

### Fix ii — Staggered dispatch (HIGH IMPACT)

**Problem addressed:** Class A (synchronous cliff).
**Mechanism:** If the orchestrator dispatches agents in sub-batches with different task types/sizes, completions are staggered:

- Batch A (3 agents): fast readers, 30–60 s.
- Batch B (4 agents): medium analysis, 2–4 min.
- Batch C (3 agents): deep multi-file work, 5–10 min.

The cliff is replaced by a rolling drain where at any given time some agents from each batch are live. When Batch A completes, Batch B and C are still running; the orchestrator refills Batch A slots while Batch B/C hold the floor.

**Implementation:**
The orchestrator's dispatch logic should explicitly assign tasks of varying scope/depth to each batch rather than distributing a uniform set of single-file reads.

**Expected improvement:** Eliminates the total-drain cliff. Even a two-tier stagger (fast/slow in ~5:5 ratio) keeps live count above floor during the fast-batch drain window.

---

### Fix iii — Over-provision to ceiling, not just to target (MEDIUM IMPACT)

**Problem addressed:** Class B (refill lag), Class A (cliff).
**Mechanism:** The stop hook already says "refill to TARGET=10, not FLOOR=6" (stop.sh line 13). But if the orchestrator's refill itself takes N tool calls (reading results before dispatching), by the time it dispatches, live might be 0. If it instead dispatches to CEILING=12 upfront, even losing 2–3 agents to undercount gives a buffer above the floor.

**Implementation:**
When a breach is detected, dispatch to `min(CEILING, TARGET + estimated_refill_lag_drain)`. A conservative estimate: if 3 agents might complete during the 5 tool calls it takes to process results and dispatch, dispatch 13 (capped at CEILING=12). This is already the intent of TARGET > FLOOR, but the implementation should be explicit: "dispatch to ceiling immediately on cliff detection" rather than "dispatch to target."

**Expected improvement:** Buys 1–3 extra agents of buffer to absorb the refill-lag window. Not a root fix — it compensates for Class B but does not eliminate it.

---

### Fix iv — Run long ops in background (MEDIUM IMPACT)

**Problem addressed:** Class C (uninterruptible dip).
**Mechanism:** `make gate` (~19 min) as a foreground Bash call eliminates all tool boundaries for 19 minutes. Running it with `run_in_background=true` means the Bash tool returns immediately, tool boundaries resume, and the orchestrator can dispatch refills during the gate run.

**Implementation:**
Any Bash call expected to take more than ~60 s should use `run_in_background`. The orchestrator should then use the Monitor tool or periodic status checks rather than waiting on the result inline.

**Caveat:** The gate result is needed before committing. The orchestrator must track the background job ID and wait for it before the commit step. This is more complex but structurally necessary for floor maintenance.

**Expected improvement:** Eliminates the entire Class C breach class for `make gate`. The 19-minute dead zone becomes a 19-minute window of normal floor maintenance.

---

### Fix v — Reconcile plugin and hook counting methods (MEDIUM IMPACT)

**Problem addressed:** Class D (probe undercount), plugin/hook disagreement.
**Mechanism:** `enforce-floor.ts` (line 59) uses `mtime < ACTIVE_WINDOW_MS = 45 000 ms` — the old heuristic that `liveness.py` was written to replace. This causes:
- Plugin over-counts freshly-completed agents (up to 45 s after completion).
- Shell hooks under-count quiet-but-live agents (probe window 0.5–0.8 s).
- The two systems give opposite errors, producing contradictory signals simultaneously.

**Implementation:**
Option A: Replace the plugin's `countActiveAgents()` with a call to `scripts/agent_liveness.py --count` (via Node.js `child_process.execSync`). This gives a single consistent count across all enforcement points.

Option B: Lengthen `TAIL_SECS` in the shell hooks to 10–15 s (currently 4–6 s). This reduces undercount of quiet-but-live agents. Cost: more false positives (dead agents counted for longer after completion). Acceptable given undercount is the more harmful direction.

Option C: Remove the plugin entirely and rely solely on the shell hooks. The plugin fires on response-transform (after the model has already responded), whereas the hooks fire on PreToolUse/PostToolUse (before the model acts). The hooks are more timely; the plugin adds noise.

**Expected improvement of Option A:** Eliminates the directional disagreement between plugin and hooks. Single source of truth = `liveness.py`. Breach signals become consistent; the orchestrator no longer sees "healthy" from one enforcer and "breach" from another in the same turn.

---

### Fix vi — Increase TAIL_SECS to match typical inter-tool intervals (LOWER IMPACT, QUICK WIN)

**Problem addressed:** Class D (undercount of quiet-but-live agents).
**Mechanism:** A typical agent making one tool call every 2–5 s will write to its transcript every 2–5 s. The current TAIL_SECS=4 means an agent is dropped from the live count if it was quiet for just 4 s between two tool calls. Increasing to 10–15 s would retain agents through typical reasoning pauses without significantly increasing false-count of completed agents (completed agents stop writing permanently).

**Implementation:**
In `pretool` and `posttool` hooks, change `FLOOR_TAIL_SECS=4` to `FLOOR_TAIL_SECS=12`. In `dec` hook, the default TAIL_SECS=6 (from `liveness.py` line 40) should also be raised. In `liveness.py` line 40, change `TAIL_SECS = 6.0` to `TAIL_SECS = 12.0`.

**Trade-off:** A just-completed agent will be overcounted for up to 12 s instead of 4–6 s. Given probe interval ~0.5 s per hook call, and hooks fire approximately every 2–10 s during active orchestration, this means a completed agent might be counted as live for 1–6 additional hook cycles. Acceptable; the Stop hook catches any resulting over-provision.

---

### Fix vii — Accept and document dip during single uninterruptible op (LOWEST IMPACT, PROCESS)

**Problem addressed:** Class C residual (one remaining uninterruptible op even after backgrounding).
**Mechanism:** There will always be some operations that genuinely cannot be backgrounded (e.g., a blocking user-prompt response, or the final commit step requiring gate results). During these, a floor dip is structurally inevitable.

**Implementation:**
Add to `AGENTS.md` and `CLAUDE.md`: "A floor dip to live<floor is acceptable if and only if: (a) a foreground uninterruptible op is in progress AND (b) it is a singleton (only one such op is running at once). The Stop hook will re-establish the floor at turn-end. Do not spawn additional agents to compensate for a dip caused by a single blocking op; the ceiling would be breached on the op's return."

---

## 5. Recommended changes to `agent_liveness.py` and hook cadence

### 5.1 liveness.py

**Line 40:** Increase `TAIL_SECS` default from 6.0 to 12.0.
```python
TAIL_SECS = float(os.environ.get("FLOOR_TAIL_SECS", "12.0"))  # was 6.0
```
Rationale: reduces undercount of agents that are reasoning (not writing) between tool calls.

**Line 39:** `PROBE_SECS` default of 2.5 is used only by the `stop` hook (which doesn't override it). This is fine — the Stop hook can afford 2.5 s for a high-fidelity count since it fires at turn-end.

**No other changes needed to liveness.py logic.** The delta-mtime approach is sound.

### 5.2 enforce-floor.ts (plugin)

**Lines 37–65:** Replace the 45 000 ms mtime-window heuristic with an exec call to `liveness.py --count`. Example:

```typescript
import { execSync } from "node:child_process"

function countActiveAgents(): number | null {
  try {
    const result = execSync(
      "python3 /Users/shawnwilson/gludd/scripts/agent_liveness.py --count",
      { timeout: 5000, cwd: "/Users/shawnwilson/gludd", encoding: "utf8" }
    )
    const n = parseInt(result.trim(), 10)
    return isNaN(n) ? null : n
  } catch {
    return null
  }
}
```

This makes the plugin use the same ground-truth probe as all shell hooks. The 2.5 s default probe sleep may make response-transform noticeably slow; override with `FLOOR_PROBE_SECS=0.6` in the exec environment to match posttool cadence.

### 5.3 Hook cadence

**pretool and posttool:** Current 0.5/0.6 s probes are appropriate for latency. Raising TAIL_SECS to 12 (Fix vi) is the only recommended change.

**dec (SubagentStop):** Current 0.8 s probe is appropriate. Consider raising TAIL_SECS to 12 via env var: `FLOOR_TAIL_SECS="${FLOOR_TAIL_SECS:-12}"`.

**The N-simultaneous SubagentStop problem:** When a wave of N agents completes simultaneously, N copies of the dec hook run concurrently. Each independently reads liveness.py and emits a breach message. The orchestrator receives N identical breach messages. Consider adding a flock guard in `agent_floor_dec.sh` so only the FIRST of N concurrent completions emits the breach message (subsequent ones see the message already queued and are silent). This deduplicates the breach signal flood.

Example:
```bash
# Near top of dec hook, after the observability tally:
lock="${TMPDIR:-/tmp}/claude-floor-dec.lock"
# Only the first concurrent caller emits the breach signal
exec 9>"$lock"
if ! flock -n 9; then exit 0; fi  # another dec is already handling it
```
Add `flock -u 9` after the breach signal is emitted. This prevents the N-message flood that wastes context and can cause the orchestrator to "respond" to breach messages rather than dispatch agents.

---

## 6. Summary table: breach classes vs. fixes

| Breach Class | Avoidable? | Primary Fixes |
|---|---|---|
| A — Wave-cliff drain | Partially | Fix i (larger batches), Fix ii (stagger) |
| B — Refill lag | Mostly | Fix iii (over-provision to ceiling), dispatch-first discipline |
| C — Uninterruptible-op dip | No (structural) | Fix iv (background ops), Fix vii (document) |
| D — Probe undercount of quiet agents | Partially | Fix vi (longer TAIL_SECS), Fix v (reconcile plugin) |
| Compound: plugin/hook disagreement | Yes | Fix v (use liveness.py in plugin) |
| Compound: SubagentStop flood | Yes | Fix 5.3 (flock dedup in dec hook) |

---

## 7. Root cause summary

The enforcement architecture is sound in design but has four compounding failure modes:

1. **The probe is reactive, not predictive.** It detects breaches after they happen, not before. A cliff from 10→0 in 60 s cannot be stopped by a hook that fires every few seconds; it can only be detected and signaled.

2. **Short task lifetimes make waves synchronous.** When all 8–10 dispatched agents do similar-sized read-only work, they complete together, producing a total drain cliff. The fix is task heterogeneity (Fix i, ii), not more aggressive signaling.

3. **The plugin uses the old discredited heuristic.** `enforce-floor.ts` lines 37–65 implement exactly the mtime-window bug that `liveness.py` was written to replace (see `liveness.py` docstring lines 6-10). It silently contradicts the shell hooks, producing opposite errors simultaneously.

4. **TAIL_SECS is too short for the reasoning cadence.** At 4 s (pre/posttool hooks), an agent that thinks for 5 s between tool calls falls out of the live window and is undercounted. This causes false breach signals during healthy operation, producing an oscillation of dispatch→ceiling→drain→dispatch.

The single highest-leverage fix is **Fix i: larger agent batches with 5–15 minute lifetimes**. This converts the synchronous cliff into a rolling drain, giving the reactive hook system time to refill gradually. Fixes ii–vi compound the improvement; none requires architectural changes to the hook system itself.
