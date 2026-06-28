> STATUS: POINT-IN-TIME analysis (dated). Reference/history — re-validate against current code/hooks before acting on its recommendations.

# Agent Floor Root-Cause Analysis

**Session:** 9780c7f7-9ec0-4337-8a8a-af36b8de929c
**Date:** 2026-06-16
**Method:** Empirical — hook code + live `make floor-status` sampling, file metadata only (no test run, no source edits)

---

## 1. Observed Data

Five consecutive `make floor-status` samples taken during this analysis session:

| Sample | maintained counter | ground-truth live (<90s mtime) | total task files |
|--------|-------------------|-------------------------------|-----------------|
| 1      | 6                 | 9                             | 1278            |
| 2      | 5                 | 9                             | 1279            |
| 3      | 5                 | 10                            | 1279            |
| 4      | 5                 | 9                             | 1279            |
| 5      | 4                 | 10                            | 1279            |

The counter is **persistently 4–6 agents lower** than ground truth. It never converged toward ground truth across the sampling window, and it drifted downward (6→4) while ground truth rose (9→10). This rules out random noise; the counter is structurally wrong.

Ground truth source: `/private/tmp/claude-501/-Users-shawnwilson-gludd/9780c7f7-9ec0-4337-8a8a-af36b8de929c/tasks/*.output` files with `mtime < 90s`.

---

## 2. Mechanism Summary (from hook code)

| Hook | File | Trigger | Action |
|------|------|---------|--------|
| `agent_floor_inc.sh` | `${TMPDIR:-/tmp}/claude-agent-floor.count` | PostToolUse(Agent) — agent dispatched | `n++` |
| `agent_floor_dec.sh` | same file | SubagentStop — agent returned/died | `n--`; emits systemMessage if `n < TARGET(7)` |
| `agent_floor_stop.sh` | reads harness tasks/ dir | Stop — turn ending | counts files with `mtime<90s`; blocks if `count < FLOOR(6)` |

The Stop hook uses **ground truth**. The dec hook uses the **counter** (which drifts). They are independent signals. Only the Stop hook will block a turn-end correctly.

---

## 3. Root Causes (ranked by impact)

### RC-1: Counter Undercount From Session Start — Structural Divergence (PRIMARY)

**Evidence:** Counter=4–6, ground truth=9–10 across all samples. The gap is larger than 1 (which "one dispatch between turns" would cause) and is not shrinking.

**Mechanism:** The counter file (`claude-agent-floor.count`) persists on disk across turns and sessions. If a session starts and agents are already running (re-dispatch after a prior session, harness restart, or worktree agents that were live before this session began), the counter starts at 0 or a stale low value while ground truth may already be high. Each inc fires only on a fresh dispatch in the *current* session. If this session did not dispatch all 9–10 live agents (some pre-existed), the counter can never reach ground truth.

**Code path:** `agent_floor_inc.sh` line 8: `echo $((n + 1)) > "$f"` — increments from whatever is currently in the file. If the file was left over from a prior run at 0, and this run dispatched 5 agents but 5 others are harness-level carryovers, counter=5 while ground truth=10.

**Fix (RC-1):** At the start of each turn (or each Stop hook evaluation), seed the counter from ground truth before decrementing. The Stop hook already computes ground truth — write it into the counter file before evaluating the dec hook chain, so they converge.

---

### RC-2: inc/dec Hook Counter Is Never Reset to Ground Truth — Drift Compounds

**Evidence:** Counter drifted 6→4 over ~10 minutes while ground truth rose 9→10. If dec fires faster than inc, the counter will only go lower over time with no correction mechanism.

**Mechanism:** There is no reconciliation step. The only truth-teller is `agent_floor_stop.sh`, which uses the `mtime`-based ground truth — but it uses this only to decide whether to block, not to correct the counter file. So the inc/dec counter can drift arbitrarily far below ground truth, and the orchestrator's SubagentStop messages ("you have N running / target 7, launch M more") are based on the wrong N.

**Dec hook flaw (line 10):** `n=$(cat "$f" 2>/dev/null || echo 1)` — fallback is 1 if the file is unreadable, then immediately decrements to 0. If the file is momentarily locked or missing, this fires and drops the counter to 0 regardless of ground truth.

**Fix (RC-2):** The dec hook should read ground truth before emitting its systemMessage so the message to the orchestrator is accurate. Or: add a correction step at the start of every Stop hook evaluation that writes the ground-truth count back to the counter file.

---

### RC-3: TARGET=7 vs FLOOR=6 Off-By-One — Orchestrator Dispatches to the Wrong Target

**Evidence:** `agent_floor_dec.sh` line 7: `TARGET="${CLAUDE_AGENT_FLOOR_TARGET:-7}"`. `agent_floor_stop.sh` line 12: `FLOOR="${CLAUDE_AGENT_FLOOR:-6}"`. These are different constants with no coordination.

**Mechanism:** The dec hook tells the orchestrator to hold TARGET=7 agents. If the orchestrator dispatches to exactly 7, then one completion drops to 6 (at the FLOOR, but OK). A second near-simultaneous completion drops to 5 (FLOOR breach). The Stop hook then fires and reports `deficit = FLOOR - live = 1`, prompting dispatch of exactly 1 agent — back to 6. Another completion immediately drops to 5 again. The system oscillates around 5–7, spending much of its time below 6.

**Stop hook line 42:** The breach message says "refill to 8+" but `deficit` is only `FLOOR - live` (e.g. 1 if live=5, FLOOR=6). The orchestrator is being asked to dispatch 1 agent ("at least ${deficit} Agent dispatch tool call(s)") while also being told "refill to 8+". These conflict. An orchestrator dispatching exactly `deficit=1` to satisfy the math will reach 6, not 8.

**Fix (RC-3a):** Change the deficit formula to `TARGET_HEADROOM - live` where `TARGET_HEADROOM = 10` (or at least 8), so the Stop hook prompts enough dispatches to stay above the floor through the next burst of completions.

**Fix (RC-3b):** Set `CLAUDE_AGENT_FLOOR_TARGET` (used by dec hook) to match the 8+ goal. Currently it is 7; change to 10 so that each SubagentStop completion message prompts dispatch to maintain 10, not 7.

---

### RC-4: Stop Hook Fires Only at Turn-End — Mid-Turn Completions Create Undetected Deficit

**Evidence:** The Stop hook is wired to the `Stop` event (settings.json line 14–21). It fires exactly once: when the orchestrator is about to end its turn. Agents complete continuously mid-turn.

**Mechanism:** If 4 agents complete mid-turn, the ground truth drops from 10 to 6. The Stop hook fires at turn-end, sees 6 (at floor, not below), and does not block. The orchestrator gets no nudge to refill. Next turn starts with 6; more completions happen before the orchestrator dispatches; the Stop hook fires at the end of that turn and may see 4 (below floor) — but by then the orchestrator has already not acted for an entire turn's duration.

**The SubagentStop hook exists for this purpose** — it fires on every completion and emits a systemMessage. However, systemMessages are context injections, not blocking signals. The orchestrator must choose to act on them. If the orchestrator is mid-task (writing code, analyzing files), it will defer the dispatch until the turn ends, by which time more completions may have fired.

**Fix (RC-4):** The SubagentStop hook's systemMessage must be more aggressive: it should command an immediate dispatch in the same message turn, not just inform. Additionally, the orchestrator should have a standing policy: when a SubagentStop systemMessage fires with deficit > 0, the very next action is dispatch (not analysis, not file reads).

---

### RC-5: Burst Completions Simultaneously Drop the Ground-Truth Count Below Floor

**Evidence:** 1279 total task files, 9–10 live at any moment. With short-lived read-only or analysis agents, many may complete within the same 90s window. When a cluster of agents started at the same time (e.g. a batch dispatch) all finish near-simultaneously, the ground truth can drop from 10 to 3–4 within minutes.

**Mechanism:** The 90s activity window means an agent that wrote its last output 91 seconds ago disappears from the ground-truth count instantly. If 6 agents were dispatched together and all complete within a 5-minute window (typical for read-only analysis), they exit the 90s window together. The ground truth drops from 10 to 4 in one inter-turn gap.

**Fix (RC-5):** Dispatch agents in staggered waves (e.g. dispatch 4 now, 3 more after the first partial results arrive) rather than all at once. The orchestrator should never dispatch all agents in a single message when the task types have similar expected runtimes.

---

### RC-6: No Auto-Redispatch on Transient 529 Agent Death

**Evidence:** The dec hook fires on `SubagentStop`, which includes both normal completion and error/death. A 529-rate-limited agent will trigger SubagentStop, the counter decrements, and the systemMessage says "launch more." But the orchestrator may not know the agent died (vs completed its work), and may not redispatch the same task.

**Mechanism:** The dec hook does not distinguish completion reason. Its message is "LAUNCH N more now" — it doesn't say "re-dispatch the failed task." The orchestrator sees only "launch more" and dispatches a new agent on *different* work (satisfying the floor number), leaving the failed task's work undone.

**Fix (RC-6):** The SubagentStop hook should receive agent completion reason (if the harness exposes it) and include it in the systemMessage. Separately, the orchestrator should have a policy: any agent that died on a transient error (not a work-complete signal) must be re-queued with the same task spec.

---

### RC-7: Orchestrator Dispatches to Floor (6), Not to Target (8+)

**Evidence:** The Stop hook breach message says "at least ${deficit} Agent dispatch tool call(s)" where deficit = FLOOR - live (typically 1). An orchestrator reading this literally will dispatch 1 agent to reach 6 (the floor), not 8–10. The next two completions immediately breach again.

**Mechanism:** The deficit calculation `FLOOR - live` yields a minimum, not a target. Dispatching the minimum satisfies the math but leaves no headroom for in-flight completions between the current turn-end and the next turn-start.

**Fix (RC-7):** Change the Stop hook deficit formula to:
```bash
TARGET=10
deficit=$((TARGET - live)); [ "$deficit" -lt 0 ] && deficit=0
```
And update the message to: "Your VERY NEXT action MUST be exactly ${deficit} Agent dispatch tool call(s) to reach ${TARGET} running agents."

---

## 4. Secondary / Lower-Confidence Factors

### RC-8: FLOOR Env Var Changed Mid-Session
The FLOOR default is 6 (hardcoded in stop hook line 12). If `CLAUDE_AGENT_FLOOR` was set to 1 or 3 in an earlier session and the orchestrator grew accustomed to that, raising it to 6 would break existing behavior without clearing the counter. **Evidence: none observed during this session** — FLOOR appears stable at 6.

### RC-9: SubagentStop Not Wired for All Agent Types
The settings.json registers SubagentStop unconditionally (no matcher). However, if some agent dispatches happen through a code path that doesn't fire the standard SubagentStop event (e.g. background agents killed externally), the dec hook never fires, and the counter stays artificially high (overcounting live agents). This is the inverse of the primary problem. **Evidence: not confirmed** — the counter is undercounting, not overcounting.

---

## 5. Fix Priority List

| Priority | Fix | Type | Complexity |
|----------|-----|------|------------|
| P0 | Stop hook: change deficit to `TARGET(10) - live` instead of `FLOOR(6) - live`; change blocking threshold from `< FLOOR` to `< FLOOR` but dispatch to TARGET | hook-code | minimal |
| P0 | Stop hook: reconcile counter file to ground truth at evaluation time (`echo $live > $f`) so dec hook messages are accurate on next turn | hook-code | 1 line |
| P1 | Set `CLAUDE_AGENT_FLOOR_TARGET=10` in settings.json (or hook env) so dec hook targets 10, not 7 | hook-code / config | 1 line |
| P1 | Orchestrator standing policy: SubagentStop with deficit > 0 = the very next action is dispatch, not analysis | orchestrator behavior | prompt/AGENTS.md |
| P2 | Stop hook message: remove "at least N" phrasing; replace with "exactly N to reach TARGET=10" | hook-code | wording |
| P2 | Stagger batch dispatches: send first half now, second half after first results arrive | orchestrator behavior | protocol |
| P3 | Dec hook: include completion reason in systemMessage (failure vs success) to enable re-dispatch of failed tasks | hook-code (requires harness API check) | medium |
| P3 | Add a reconcile-counter Makefile target that writes ground truth to the counter file | Makefile | minor |

---

## 6. Concrete Hook Changes (Exact Lines)

### agent_floor_stop.sh — two changes

**Change 1: Reconcile counter to ground truth before blocking check (after line 34)**
```bash
# Reconcile the inc/dec counter to ground-truth so SubagentStop messages are accurate.
[ -n "$live" ] && echo "$live" > "${TMPDIR:-/tmp}/claude-agent-floor.count" 2>/dev/null || true
```

**Change 2: Change deficit and TARGET in the breach block**
```bash
# Current (lines 40-45):
if [ "$live" -lt "$FLOOR" ]; then
  deficit=$((FLOOR - live))
  reason="... refill to 8+ ..."

# Replace with:
TARGET_DISPATCH="${CLAUDE_AGENT_FLOOR_TARGET:-10}"
if [ "$live" -lt "$FLOOR" ]; then
  deficit=$((TARGET_DISPATCH - live)); [ "$deficit" -lt 1 ] && deficit=1
  reason="AGENT-FLOOR BREACH: only ${live} subagent(s) streaming, floor is ${FLOOR}, target is ${TARGET_DISPATCH}. Do NOT end the turn. Your VERY NEXT action MUST be exactly ${deficit} Agent dispatch tool call(s) on DISJOINT work to reach ${TARGET_DISPATCH} running agents. Merge/reclaim completed worktrees. Re-dispatch any agent that died. Do not deliberate; dispatch."
```

### agent_floor_dec.sh — one change

**Change: Update default TARGET from 7 to 10**
```bash
# Current line 7:
TARGET="${CLAUDE_AGENT_FLOOR_TARGET:-7}"

# Replace with:
TARGET="${CLAUDE_AGENT_FLOOR_TARGET:-10}"
```

### settings.json — optional env addition

Add to the hooks block to lock in the target across all shells:
```json
"env": {
  "CLAUDE_AGENT_FLOOR": "6",
  "CLAUDE_AGENT_FLOOR_TARGET": "10"
}
```
(settings.json currently has no env section.)

---

## 7. Timeline Reconstruction

The session has 1279 total task files with 9–10 live at any moment. Given a 90s activity window, this implies agents have an average active duration well under the full session length — consistent with short-lived read-only agents (analysis, file reads). With ~1279 completed + 9–10 live, the average agent lifetime is on the order of minutes.

A burst pattern is plausible: the orchestrator dispatched ~10 agents simultaneously in earlier turns; they completed within a narrow window; the stop hook fired on the next turn-end and saw 3–5 live (below floor); it prompted "dispatch N"; the orchestrator dispatched exactly N=deficit (1–3); repeat.

The net result is the orchestrator spending most of its time at 5–7 active agents instead of the intended 10+. The counter drift (counter=4, ground-truth=10) shows the counter is not providing useful signal for the SubagentStop messages — the orchestrator is being told "you have 4 running, launch 6 more" when it actually has 10, causing it to over-dispatch and then be surprised when completions arrive faster than expected.
