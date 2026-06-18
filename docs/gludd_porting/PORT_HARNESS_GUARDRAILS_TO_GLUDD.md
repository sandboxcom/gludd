# Port Harness Guardrails → gludd Product

**Date:** 2026-06-16
**Author:** porting audit (read-only; no gates run, nothing committed)
**Scope:** Take the agent-behavior guardrails this session codified for the *Claude Code harness*
(AGENTS.md rules + hooks + harness memories) and port the genuinely-useful ones into the
**gludd product itself** (the autonomous coding agent in this repo) using gludd's NATIVE
mechanisms, preferring codification (AgentBehavior rule / Ansible role) over passive memory —
mirroring the harness lesson "hooks/codification > memory".

This doc is the authoritative port-to-gludd plan. It lives here (not in `TASKS.md`) because
`TASKS.md` is a strict **evidence ledger of completed work** — every entry is `[x]` with a
commit hash. These are forward-looking `[ ]` tasks; they graduate into `TASKS.md` with evidence
once shipped.

---

## 0. The headline finding (read this first)

gludd **already has** the right codification mechanism, but it is **dead code**.

- `AgentBehavior` (Pydantic config model) + `BehaviorRenderer` (renders rules → prompt text)
  exist at `src/general_ludd/agents/behavior.py:37-245`. They are well-tested
  (`tests/unit/test_agent_behavior.py`, `tests/e2e/test_agent_behavior_e2e.py`).
- They are reachable via `AgentRegistry.render_behavior_prompt()` (`src/general_ludd/agents/registry.py:55-60`).
- **BUT** that method is **never called in the live dispatch/execution path.** The real system
  prompt is built by `_build_system_prompt()` (`src/general_ludd/execution/engine.py:58-71`,
  invoked at `engine.py:234`), which emits a generic 5-line prompt and **ignores the behavior
  system entirely.** Grep across `execution/`, `dispatch/`, `event_loop/` finds no caller of
  `render_behavior_prompt`.

**Consequence:** porting any new behavior rule into `AgentBehavior` is worthless until the
renderer is wired into the live prompt. So **task PG-0 (wire the renderer) is the gating
prerequisite** for PG-1, PG-2, PG-5, PG-6b. Do it first.

A secondary fact that changes the plan: gludd **also already has** a real runtime memory store —
`MemoryRecordModel` / `memory_records` table (`src/general_ludd/db/models.py:529-572`, migration
`alembic/versions/005_add_runtime_tables.py:124-146`), scoped project/global/role. So "port a
learning as gludd memory" is a *real* option — but per the user's stated preference and the
harness lesson, we choose codified `AgentBehavior` rules / roles over memory wherever a behavior
must be *enforced*. Memory is reserved for soft, accumulated knowledge.

---

## 1. Mechanism-mapping table (learning → gludd mechanism → exists? → priority)

| # | Learning | Best gludd mechanism | Already in gludd? | Gap? | Priority |
|---|----------|----------------------|-------------------|------|----------|
| 0 | (prereq) behavior rules must reach the live prompt | wire `BehaviorRenderer.render_as_prompt` into `engine._build_system_prompt` | renderer exists but UNWIRED (`registry.py:55` never called; `engine.py:58` ignores it) | **YES — dead code** | **P0 (gating)** |
| 4 | **git tasks NON-BLOCKING + run AFTER task complete** | execution-engine ordering + `_run_git` hardening + commit/PR role | **DONE** — commit fires after `transition(COMPLETE)` (`event_loop/loop.py:1120→1134`); both commit+push via `asyncio.to_thread` (`loop.py:1201-1204`); failure non-blocking, retried next tick via `_pushed_work` (`loop.py:1152-1177`); `_run_git` has 60s timeout + non-interactive env + per-repo lock (`git_automation/repo.py:225-257`) | **No (mostly)** — see PG-4 residuals | **P0 to verify, then DONE** |
| 1 | never-block-on-questions (default to action) | `AgentBehavior` rule (rendered) | **PARTIAL** — renderer text "Do NOT pause to ask if the user wants you to continue" (`behavior.py:126`) + `self_directed_work` "Do NOT stop to ask whether to proceed" (`behavior.py:141`) exist but are (a) unwired and (b) not a first-class field | **YES — promote to field + wire** | **P0** |
| 2 | fix-means-repair-never-disable | `AgentBehavior` rule (rendered) + role-level guard | **NO** — no such rule anywhere in `behavior.py`; the only "disable" is dispatcher's own kill path (`agents/dispatcher.py:82`) | **YES** | **P0** |
| 3 | agent-at-rest / re-dispatch (classify done vs stalled/failed; capped-backoff re-dispatch of only stalled/failed) | keep `scripts/agent_watchdog.py` as the classifier; add `AgentBehavior.max_retries` → capped-backoff re-dispatch in the loop; surface stop-vs-retry as a rendered rule | **PARTIAL** — watchdog classifies ACTIVE/STALLED/DONE (`scripts/agent_watchdog.py`); `max_retries:int=3` field exists (`behavior.py:48`) but no backoff/re-dispatch consumer; resilience `RetryPolicy`/`CircuitBreaker` exists in gateway | **YES — wire max_retries→backoff; render rule** | P1 |
| 5 | codification > passive memory (enforced behavior ⇒ role/rule + test, not memory) | meta: make it a `BehaviorRenderer` section + a guard test that asserts each enforced rule renders | **PARTIAL** — `GuardrailConfig` already encodes the 3-layer doctrine (config/hook/prompt, `behavior.py:21-34,198-212`); but no rule says "new enforced behavior must be codified+tested" | **YES — add meta-rule + ledger test** | P1 |
| 6a | observability invariant ("no unseen events") | rendered `AgentBehavior` rule + keep existing tests | **PARTIAL** — principle lives in `pipeline/controller.py:15` + `tests/unit/test_observability_guardrails.py` (gate/CI tee+heartbeat) but is **not rendered into agent prompts** | **YES — render the agent-facing half** | P1 |
| 6b | stall-detection | Makefile watchdog (harness-level) + pytest per-test timeout | **DONE** — `make run-watched` STALL_SECS/MAX_SECS kill-tree (`Makefile:364-406`); pytest global `timeout` + `pytest-timeout` dep, asserted by `test_observability_guardrails.py::TestNoSilentStalls` | No | DONE |
| 6c | gate-concurrency hygiene (never 2 gates/pytest at once) | Makefile target + test | **DONE-ish** — `make ps-pytest`, `--basetemp` pinning referenced in memory/Makefile; covered operationally | mostly No | DONE (verify target) |
| 6d | disk-discipline (worktree venvs fill disk → ENOSPC deadlock) | Makefile targets | **DONE** — `make disk` / `make clean-worktree-venvs` exist per batch-ship state | No | DONE |
| 6e | no-unquantified-status-claims (paste the measurement) | rendered `AgentBehavior.evidence_required` rule | **DONE (in-product)** — `evidence_required=True` renders "Every factual claim MUST have supporting evidence… Unsupported claims are policy violations" (`behavior.py:168-177`); needs PG-0 to actually reach the prompt | No (gated on PG-0) | DONE-via-PG-0 |

**Net genuine gaps to port:** PG-0 (wire renderer), PG-1 (block-on-questions field), PG-2
(fix≠disable rule), PG-3 (max_retries→backoff re-dispatch), PG-5 (codification meta-rule),
PG-6a (observability agent rule). **PG-4 is essentially already done** — it becomes a
verify-and-record task plus two small residuals.

---

## 2. The tasks

Convention: each task gives the **learning**, the **chosen gludd mechanism**, a one-line
**implementation sketch**, and **acceptance criteria (a test)**. IDs are `PG-*` (Port to Gludd).

### [ ] PG-0 — Wire `BehaviorRenderer` into the live system prompt  **(P0, GATING)**
- **Learning:** the meta-prerequisite — codified rules are worthless if they never reach the model.
- **Mechanism:** `AgentBehavior` / `BehaviorRenderer` (already exist) — connect them.
- **Sketch:** in `execution/engine.py:_build_system_prompt`, look up the agent's behavior
  (via `AgentRegistry.render_behavior_prompt(name, task)` or a passed-in `AgentBehavior`) and
  prepend the rendered behavior block to the existing generic prompt. Thread `agent_name`
  through `JobSpec` if not already present. Keep the renderer the single source of rule text.
- **Acceptance:** a test in `tests/unit/test_execution_engine_fixes.py` (or new
  `test_engine_behavior_wiring.py`) that calls the real `_build_system_prompt` for a job whose
  behavior has `completion_policy="complete_all"` and asserts the output **contains** the
  rendered marker text (e.g. "Do NOT pause to ask"). Today that assertion would FAIL — that is
  the proof this is a real gap.

### [ ] PG-1 — `never_block_on_questions` first-class rule  **(P0)**
- **Learning:** agents default to action; never gate work on a question; reserve questions for
  truly irreversible choices.
- **Mechanism:** `AgentBehavior` field + `BehaviorRenderer` section (preferred over memory).
- **Sketch:** add `never_block_on_questions: bool = True` to `AgentBehavior` (`behavior.py:37`);
  in `BehaviorRenderer.render`, emit a "## Never Block On Questions" section: *"Never pause work
  to ask the user a question. Default to action: make a reasonable assumption, state it, keep
  going. Only stop for a `stop_condition` (missing credentials / irreversible destructive
  action)."* Tie the escape hatch to existing `stop_conditions` so it composes.
- **Acceptance:** `test_agent_behavior.py::TestBehaviorRenderer` asserts the rendered prompt
  contains "Never pause" / "Default to action" when the field is True and omits it when False;
  after PG-0, an engine test asserts it reaches the live prompt.

### [ ] PG-2 — `fix_means_repair` (never disable to "fix")  **(P0)**
- **Learning:** never disable/delete/skip a feature or test to make a failure go away; repair the
  root cause; a disable is only legitimate with an explicit, tracked follow-up.
- **Mechanism:** `AgentBehavior` field + rendered rule; (optional later) a role-level lint guard.
- **Sketch:** add `repair_not_disable: bool = True` to `AgentBehavior`; render a "## Fix Means
  Repair, Never Disable" section: *"When something fails, repair the root cause. Do NOT disable,
  comment-out, `skip`, `xfail`, or delete the feature/test to turn the gate green. If a disable
  is genuinely required, it is a tracked decision with a follow-up todo, never a silent one."*
- **Acceptance:** renderer test asserts presence/absence by field; (stretch) an
  `audit`-style test that scans a diff for new `@pytest.mark.skip`/`xfail`/`continue-on-error`
  without an accompanying tracked todo id and flags it — mirrors the harness guardrail-integrity test.

### [ ] PG-3 — `max_retries` → capped-backoff re-dispatch of stalled/failed only  **(P1)**
- **Learning:** classify completed vs failed/stalled; re-dispatch ONLY failed/stalled, with
  capped exponential backoff; never re-run completed work; never abandon a transient-error kill.
- **Mechanism:** keep `scripts/agent_watchdog.py` as the classifier; wire the existing
  `AgentBehavior.max_retries` (`behavior.py:48`) into a backoff re-dispatch in the event loop
  using the resilience `RetryPolicy`/`CircuitBreaker` already in the gateway; render the policy.
- **Sketch:** on a tick, for each at-rest agent: if watchdog says DONE → leave it; if STALLED or
  FAILED and `attempts < max_retries` → re-dispatch after `base * 2**attempts` (capped) backoff;
  on `max_retries` exceeded → mark failed and surface (don't silently drop). Render a
  "## Agent At-Rest / Re-Dispatch" section stating the done-vs-stalled rule.
- **Acceptance:** unit test: a DONE-classified agent is never re-dispatched; a STALLED one is
  re-dispatched at most `max_retries` times with monotonically increasing delay; the backoff is
  capped. Reuse watchdog's classifier in the assertion.

### [ ] PG-4 — VERIFY + record: non-blocking, post-completion git  **(P0 verify → DONE)**
- **Learning:** git tasks must be NON-BLOCKING and run AFTER a task is considered complete.
- **Status:** **already implemented** — record it as DONE-with-evidence once the gate confirms:
  - commit/push trigger AFTER DB `transition(…COMPLETE…)` — `event_loop/loop.py:1120` then
    `_attempt_completed_push` at `loop.py:1134`.
  - non-blocking — `asyncio.to_thread(repo.commit,…)` + `asyncio.to_thread(repo.push,…)`,
    `loop.py:1201-1204` (guarded by `test_execution_engine_fixes.py:70-117`: "to_thread used ≥2").
  - failure non-blocking — task stays COMPLETE, retried next tick, not silently lost
    (`loop.py:1152-1177`; `test_event_loop_reconcile_fixes.py:128-161` F3).
  - `_run_git` 60s timeout + `GIT_TERMINAL_PROMPT=0`/`GIT_ASKPASS=echo` + per-repo lock,
    timeout→`CalledProcessError` fail-closed (`git_automation/repo.py:225-257`).
- **Residuals (the only real work here):**
  - **PG-4a:** the secondary `_run_git` in `code_intelligence/git_intel.py:80-114` has a 30s
    timeout but **no non-interactive env** — harden it to match `repo.py` (env + timeout→clean
    failure). Test: a hung-credential git in that path fails closed, never prompts/hangs.
  - **PG-4b:** add an explicit guard test asserting the *ordering* invariant directly (commit is
    attempted strictly after the COMPLETE transition), so a future refactor can't reorder it.
- **Acceptance:** PG-4a/PG-4b tests pass; then append a `[x]` PG-4 entry to `TASKS.md` citing the
  file:line evidence above + the commit hash.

### [ ] PG-5 — Codification-over-memory meta-rule + ledger test  **(P1)**
- **Learning:** when a behavior must be enforced, codify it as a rule/role with a test — not a memory.
- **Mechanism:** extend the existing `GuardrailConfig` 3-layer doctrine (`behavior.py:21-34`) into
  a rendered meta-rule + a guard test that ties enforced rules to tests.
- **Sketch:** render a "## Codification Policy" section: *"A behavior that must be ENFORCED is
  codified as an `AgentBehavior` rule or an Ansible role with a test, enforced at ≥1 of
  {config, hook, prompt} layers. Passive memory is for soft, accumulated knowledge only — never
  the enforcement mechanism for a hard rule."*
- **Acceptance:** a "ledger" test parametrized over the enforced behavior fields that asserts each
  one (a) renders a section when enabled and (b) has a corresponding renderer-output assertion —
  i.e. no enforced rule is rule-without-a-test. Mirrors harness `test_guardrails.py` three-layer tests.

### [ ] PG-6a — Observability invariant as a rendered agent rule  **(P1)**
- **Learning:** "unseen events aren't events" — never launch a silent long-running op; stream or
  heartbeat; restate results in text.
- **Mechanism:** `AgentBehavior` field + rendered rule. The gate/CI half already exists
  (`test_observability_guardrails.py`, `pipeline/controller.py:15`); this ports the *agent-facing*
  half into the prompt.
- **Sketch:** add `observable_progress: bool = True`; render "## Observability — No Unseen Events":
  *"Every long-running operation must stream output or emit a heartbeat; never go silent on a
  background task. Restate results in your own text, not only in tool output."*
- **Acceptance:** renderer test asserts the section by field; cross-link the existing
  `test_observability_guardrails.py` so the agent-rule and the gate-rule are co-tested.

### Already-DONE (recorded for completeness; no new work)
- **PG-6b stall-detection** — `make run-watched` (`Makefile:364-406`) + pytest `timeout` +
  `pytest-timeout` dep, asserted by `test_observability_guardrails.py::TestNoSilentStalls`.
- **PG-6c gate-concurrency hygiene** — `make ps-pytest` + `--basetemp` pinning (operational).
- **PG-6d disk-discipline** — `make disk` / `make clean-worktree-venvs`.
- **PG-6e no-unquantified-status-claims** — `evidence_required` renders the rule
  (`behavior.py:168-177`); reaches the prompt once PG-0 lands.

---

## 3. Suggested order

1. **PG-0** (gating — without it PG-1/2/5/6a render into a vacuum).
2. **PG-4a/PG-4b** (cheap; closes the one git residual + locks the ordering invariant), then
   record PG-4 as `[x]` in `TASKS.md`.
3. **PG-1, PG-2** (highest-value rules per the brief).
4. **PG-3, PG-5, PG-6a**.

All work follows repo policy: TDD (failing test first), `make`-only Bash, gate green +
`TASKS.md` evidence entry per shipped task. Do not run gates while a release gate is active.
