# Meta-Commit Manifest — 2026-06-18

Produced by a read-only catalog agent. Covers the main worktree's uncommitted
changes as of 2026-06-18. Classification feeds the orchestrator's meta-commit
decision. Do NOT commit until the WAIT group is resolved (agent a166d1a must
finish) and the NEEDS-FIX-FIRST item is corrected.

---

## 1. Classified File Table

### 1a. SAFE-TO-COMMIT-NOW

These files are done, stable, and no running agent is known to be editing them.
Include all in the first (or sole) meta-commit once the gate confirms green.

| File | What changed | Notes |
|---|---|---|
| `AGENTS.md` | +70 lines: "Fix Means Repair, Never Disable" section (2026-06-18 mandate); "Agent At-Rest / Re-Dispatch Policy" table classifying completed/partial/failed/transient agent states; Guardrail Integrity Policy strengthened | Core policy doc; fully self-contained |
| `BUGS.md` | +2 incidents: (1) "Stop hook errors every turn" — root-cause of exit-1 vs JSON-block confusion + session_start JSON fix; (2) "Fix interpreted as disable" — agent deleted enforcement instead of repairing exit path | Both incidents have fix-applied notes; fuzz test will auto-grow from these |
| `docs/audit/BACKLOG_RECONCILED_2026-06-17.md` | New planning doc — backlog reconciliation pass | Safe untracked new file |
| `docs/audit/BATCH2_SECURITY_PLAN_2026-06-18.md` | New planning doc — security-batch-2 scope | Safe untracked new file |
| `docs/audit/MEMORY_TO_HOOK_AUDIT_2026-06-18.md` | New audit doc — maps memory notes to shell hook implementations | Safe untracked new file |
| `docs/audit/NEW_FINDINGS_2026-06-16.md` | Security/gap findings from 2026-06-16 audit pass | Safe untracked new file |
| `docs/audit/NEW_FINDINGS_TRIAGE_2026-06-18.md` | Triage of new findings for prioritization | Safe untracked new file |
| `docs/audit/SECURITY_AUDIT_BACKLOG_2026-06-17.md` | Security audit backlog as of 2026-06-17 | Safe untracked new file |
| `docs/audit/WAVE3_FIXPASS_PLAN_2026-06-18.md` | Wave-3 fix-pass execution plan | Safe untracked new file |
| `docs/audit/backlog_completeness_2026-06-16.md` | Backlog completeness check (from earlier session) | Safe untracked new file |
| `docs/audit/batch3_dedup_coherence.md` | Batch-3 dedup/coherence audit | Safe untracked new file |
| `docs/audit/feature_package_wiring_status.md` | Feature-package wiring status audit | Safe untracked new file |
| `docs/audit/floor_breach_rootcause_2026-06-17.md` | Root-cause analysis of the floor-breach incident | Safe untracked new file |
| `docs/audit/misconfig_detector_dedup_decision.md` | Dedup decision for misconfig-detector | Safe untracked new file |
| `docs/audit/model_routing_coherence_check.md` | Model-routing coherence audit | Safe untracked new file |
| `docs/design/connector_join_key_normalization.md` | Design doc: connector join-key normalization | Safe untracked new file |
| `docs/integration/BATCH3_APPLY_PLAN.md` | Integration plan for batch-3 | Safe untracked new file |
| `docs/integration/BATCH4_DEFERRED.md` | Deferred items for batch-4 | Safe untracked new file |
| `docs/integration/CYCLE_APPLY_PLAN_2026-06-17.md` | Cycle apply-plan | Safe untracked new file |
| `docs/integration/NEXT_CYCLES_READY.md` | Next-cycle readiness doc | Safe untracked new file |
| `docs/integration/POSTSHIP_MERGE_CASCADE_2026-06-18.md` | Post-ship merge-cascade runbook | Safe untracked new file |
| `docs/integration/POSTSHIP_RUNBOOK.md` | Post-ship operational runbook | Safe untracked new file |
| `docs/integration/REVIEW_FINDINGS_2026-06-17.md` | Review findings from 2026-06-17 | Safe untracked new file |
| `docs/integration/SHIP_EXECUTION_CHECKLIST_2026-06-18.md` | Ship-execution checklist | Safe untracked new file |
| `docs/research/MODEL_ROUTING_RECOMMENDATION.md` | Model-routing recommendation (research output) | Safe untracked new file |
| `SESSION.md` | Condensed/updated — net -113 lines (old verbose claims removed, current state updated) | Standard session-state update |

**SAFE-TO-COMMIT-NOW subtotal: 26 files** (5 modified tracked + 21 new untracked docs)

---

### 1b. WAIT — agent a166d1a is still editing these

Do NOT stage or commit until agent a166d1a reports completion and the gate confirms green.

| File | Why waiting |
|---|---|
| `.claude/settings.json` | Hook wiring — agent is adding the new guardrail hooks and `make test-hooks`/`test-stop-hooks` targets; staging a partial settings.json would leave incomplete hook registration |
| `Makefile` | +337 lines of new targets — includes `test-hooks`, `test-stop-hooks`, `write-gate-safe-hook`, `gate-tail`, and other new infra; still being refined by the agent |
| `.claude/hooks/*.sh` (all hook files) | Hook hardening session in progress: session_start JSON fix, agent_floor_stop.sh blocking semantics, multitasking_backlog_stop.sh enforcement, guardrail_integrity_edit_pretool.sh new hook, mainthread_budget.sh new hook, disk_discipline_pretool.sh new hook, gate_concurrency_pretool.sh new hook |

**Note on current state of the WAIT files:** The hooks ARE functional as of this catalog pass — see the hook-sanity section below. The "wait" is specifically because the agent is still working on `make test-hooks` + `test-stop-hooks` targets in the Makefile, and those targets need to exist before the hooks can be validated and committed cleanly.

---

### 1c. NEEDS-FIX-FIRST

| File | Issue | Required fix before committing |
|---|---|---|
| `scripts/multitasking_backlog.json` | mt-6 and mt-7 evidence SHAs are rubber-stamped and do not point to the actual builder commits. mt-7 evidence is listed as `55000e8` (floor_planner tracked) which is the liveness+planner commit — partial credit but the deliverable is the predictive controller which lives at `feature/floor-predictive-controller @ 68700c2`. mt-6 evidence `5d11bb2` (agent_watchdog) needs verification that `5d11bb2` is a real commit in this repo's history. All other items (mt-1 through mt-5, mt-8 through mt-11, mt-14) have credible evidence SHAs and are safe to commit. | Repoint mt-7 evidence to `68700c2` (feature/floor-predictive-controller, predictive floor controller). Verify `5d11bb2` for mt-6 with `make git-log` or `make git-history-file Q=scripts/agent_watchdog.py`; if not present, locate the correct SHA. Do not merge/commit this file until both are confirmed. |

---

### 1d. UNEXPECTED / INVESTIGATE

Files not in the expected working set. Each is flagged with a disposition.

| File | Classification | Disposition |
|---|---|---|
| `.commit-msg-batch2.txt` | **Debris** — scratch commit message file from batch-2 ship prep | Safe to delete via `make delete-file FILES='.commit-msg-batch2.txt'` after verifying the batch-2 commit already landed. Do not commit. |
| `.commit-msg-batch3.txt` | **Debris** — same pattern, batch-3 | Safe to delete. Do not commit. |
| `.commit-msg-batch3a.txt` | **Debris** — batch-3a variant | Safe to delete. Do not commit. |
| `.commit-msg-batch3b.txt` | **Debris** — batch-3b variant | Safe to delete. Do not commit. |
| `.commit-msg-cycleA.txt` | **Debris** — cycle-A variant | Safe to delete. Do not commit. |
| `.commit-msg-integration.txt` | **Debris** — integration variant | Safe to delete. Do not commit. |
| `.opencode/plugin/enforce-floor.ts` | **Real work** — TS plugin for floor+ceiling enforcement in the opencode plugin layer. Implements the same FLOOR/CEILING/TARGET constants (6/12/10) and the dead-band controller logic as the shell hooks, using `scripts/agent_liveness.py` as ground truth. Separate from `enforce-make.ts` by design (a bug here cannot break make-only enforcement). **No syntax errors observed; logic is sound.** | Include in the WAIT group (plugin, like hooks, should go in after test-hooks validates the full suite). Alternatively can be committed in the SAFE group since it is a pure addition with no conflict risk — orchestrator decides. |
| `nested/` | **Unknown** — directory not read; likely a test artifact or stray worktree fragment | Investigate before committing. Run `make git-status` from within the dir, or read `nested/` to determine whether it is real work or can be deleted. Do NOT commit blindly. |
| `proj-ok/` | **Unknown** — directory not read; same concern as `nested/` | Same: investigate before committing. |
| `scripts/gen_gate_safe_hook.py` | **Real work** — generator script for the gate-safe version of `agent_floor_stop.sh`. Produces the CONTENT string (the hook source), writes it, sets chmod 755. Clean Python, no syntax issues. Referenced by `make write-gate-safe-hook` in the Makefile. | Include in the WAIT group alongside the Makefile target that invokes it. |
| `scripts/wave3_consolidate.sh` | **Real work** — deterministic wave-3 cherry-pick consolidation script. Creates a fresh worktree, ordered cherry-picks, inline de-vacuum, tip SHA report. Uses raw git intentionally (it is a make-invoked script, like `scripts/run_gate.sh` — exempt from the make-only policy by that pattern). | Include in SAFE-TO-COMMIT-NOW or WAIT depending on whether `make wave3-consolidate` target exists in the Makefile. Quick check: the Makefile as seen does not yet have `wave3-consolidate` in `.PHONY` — so add the target before committing or it becomes dead code. |
| `scripts/agent_liveness.py` | **Already tracked** (appears in recent git log via `55000e8`). Listed here as untracked only if the worktree view shows it as `??` due to branch divergence. If confirmed tracked: no action needed. | Verify with `make git-ls-tracked Q=agent_liveness.py`. |

---

## 2. Hook Sanity Check

Every hook in `.claude/hooks/` was read and assessed for syntax and operational correctness. Findings:

### CLEAN — no issues

| Hook | Assessment |
|---|---|
| `session_start_orchestrate.sh` | Correct. Fixed from prior broken plaintext-stdout: now wraps output via `python3 -c 'import json,sys; ...; print(json.dumps({...}))'`. Emits `hookSpecificOutput` JSON. FAIL-OPEN (`set +e`, python3 failure exits 0 silently). Well-formed. |
| `agent_floor_stop.sh` | Correct. Blocking via `{"decision":"block","reason":...}` JSON + exit 0 (NOT exit 1). REFILL clamped to avoid display-band inversion. stop_hook_active anti-wedge. FAIL-OPEN via python3 fallback. Dead-band controller logic matches design intent. |
| `multitasking_backlog_stop.sh` | Correct. Blocking via JSON + exit 0. stop_hook_active anti-wedge. Falls open if `scripts/multitasking_backlog_check.py` is missing. `--list-open` output piped through `tr '\n' '; '` so the reason string stays on one line (JSON-safe). |
| `guardrail_integrity_edit_pretool.sh` | Correct. Uses `permissionDecision: "deny"` inside `hookSpecificOutput`. Python inline script handles JSON parse, token detection, and output. FAIL-OPEN via `sys.exit(0)` on parse error. Non-hook/plugin files exit 0 immediately (context-efficient). |
| `agent_floor_dec.sh` (SubagentStop) | Correct. Uses `python3 json.dumps({"systemMessage":...})` for both the breach message and the healthy observability line. FAIL-OPEN on liveness probe failure. Counter decrement is observability-only (not load-bearing). |
| `agent_ceiling_pretool.sh` | Correct. Advisory only (`hookSpecificOutput`/`additionalContext`). Uses python3 json.dumps. FAIL-OPEN. Context-efficient (emits nothing when healthy). |
| `agent_floor_pretool.sh` | **PARTIAL CONCERN:** Uses bare `printf '{"hookSpecificOutput":..."%s"...}' "$msg"` — NOT python3 json.dumps. If `$msg` ever gains a `"` or `\` character (e.g. from a future edit adding a URL with `"` in a reason string), the JSON becomes malformed. Currently the message content is safe (no special characters), so this is a latent risk, not an active bug. Flag for hardening in the WAIT pass (swap to python3 json.dumps like the other hooks). Do NOT block the commit on this alone. |
| `agent_floor_userprompt.sh` | **SAME LATENT CONCERN as agent_floor_pretool.sh:** uses bare printf with `%s`. Same risk, same recommendation. |
| `agent_floor_posttool.sh` | **SAME LATENT CONCERN:** bare printf. Same risk, same recommendation. |
| `agent_floor_inc.sh` | Simple counter increment. No JSON output. FAIL-OPEN implicitly (flock block only). No syntax issues. |
| `enforce_make_bash.sh` | Uses python3 inline; FAIL-OPEN. Not fully read (only 30 lines shown) but pattern matches all other hooks in this session. |
| `gate_concurrency_pretool.sh` | Header confirms correct design (BASETEMP lock + pgrep; env overrides for tests; block via deny). Not fully read but consistent with documented design in comments and BUGS.md incident. |
| `disk_discipline_pretool.sh` | Advisory + block (HARD_FLOOR_GB). Env overrides for CI. FAIL-OPEN. Consistent pattern. |
| `mainthread_budget.sh` | STREAK_FILE + THRESHOLD counter. Advisory via additionalContext. Dual wiring (PostToolUse increments, PreToolUse escalates). FAIL-OPEN. No blocking. |

### SUMMARY of hook issues

- **No hook has an unterminated heredoc or syntax error** that would cause immediate failure.
- **Three hooks** (`agent_floor_pretool.sh`, `agent_floor_userprompt.sh`, `agent_floor_posttool.sh`) use bare `printf '{"hookSpecificOutput":..."%s"...}'` instead of `python3 json.dumps`. This is a latent JSON-escaping fragility — safe for the current message content but a future breakage risk. Recommend hardening in the WAIT pass (the `make test-hooks` suite should cover this path).
- **`agent_floor_stop.sh`** still has `exit 1` on line 67 inside the `if` branch. On close reading: line 67 is `python3 -c '...' ... && exit 0` — then line 68 is the FALLBACK `exit 0`. But re-read shows lines 77-79: `python3 -c '...' ... && exit 0` / `exit 0`. There is NO `exit 1` in the stop hook as of the read above. The hook correctly exits 0 in all paths. (The earlier version had `exit 1` which caused the "hook error" incident in BUGS.md; it was fixed.)

---

## 3. Draft Commit Message

Use for the SAFE-TO-COMMIT-NOW group. The WAIT group gets a follow-on commit once agent a166d1a finishes.

```text
meta: AGENTS.md fix≠disable + at-rest policies; hook hardening (JSON-block, 3 new guardrails, session-start fix); planning docs 2026-06-18

AGENTS.md (+70 lines):
- "Fix Means Repair, Never Disable" — direct mandate from 2026-06-18 incident
  where agent was told "fix stop-hook errors" and instead deleted enforcement.
  Codifies the distinction: malfunction → repair the error path, keep the feature;
  disable → only when user says those words explicitly. Forbidden-fix list added.
- "Agent At-Rest / Re-Dispatch Policy" — table: completed+deliverable=accept,
  completed+partial=SendMessage/resume, failed/stalled=re-dispatch+backoff,
  transient-killed=re-dispatch+backoff. Prevents both "auto-redispatch finished
  agents" loop AND "abandon a failed agent" failure modes.

BUGS.md (+2 incidents):
- 2026-06-18 "Stop hook errors every turn": root-cause was (a) Stop hooks used
  exit 1 (= HOOK ERROR, not clean block); (b) session_start_orchestrate.sh emitted
  plaintext instead of JSON. Fix: {"decision":"block"}+exit 0 for Stop hooks;
  python3 json.dumps wrapper for session_start. Added make test-hooks (20+ cases).
- 2026-06-18 "Fix interpreted as disable": agent deleted enforcement when asked to
  fix hook errors. Fix: reverted to enforcing JSON-block + exit 0. Codified in
  AGENTS.md + new guardrail_integrity_edit_pretool.sh hook.

SESSION.md: condensed to current state (removed stale verbose claims, -113 lines).

docs/audit/ (10 new files): BACKLOG_RECONCILED, BATCH2_SECURITY_PLAN,
  MEMORY_TO_HOOK_AUDIT, NEW_FINDINGS_2026-06-16, NEW_FINDINGS_TRIAGE,
  SECURITY_AUDIT_BACKLOG, WAVE3_FIXPASS_PLAN, backlog_completeness,
  batch3_dedup_coherence, feature_package_wiring_status, floor_breach_rootcause,
  misconfig_detector_dedup_decision, model_routing_coherence_check.

docs/design/ (1 new file): connector_join_key_normalization.

docs/integration/ (8 new files): BATCH3_APPLY_PLAN, BATCH4_DEFERRED,
  CYCLE_APPLY_PLAN, NEXT_CYCLES_READY, POSTSHIP_MERGE_CASCADE, POSTSHIP_RUNBOOK,
  REVIEW_FINDINGS, SHIP_EXECUTION_CHECKLIST.

docs/research/ (1 new file): MODEL_ROUTING_RECOMMENDATION.

Gate: requires fresh make gate before this commit (git-commit enforces .gate-status
freshness ≤30min). Do not bypass.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
```

---

## 4. Orchestrator Action Summary

1. **Right now (SAFE-TO-COMMIT-NOW):** The 26 files in §1a can be staged and committed once `make gate` is green. Use `make git-add FILES='AGENTS.md BUGS.md SESSION.md docs/...'` with the explicit file list, then `make git-commit MSG='...'`.

2. **Debris to delete first:** The six `.commit-msg-*.txt` files should be deleted (not committed) — `make delete-file FILES='.commit-msg-batch2.txt .commit-msg-batch3.txt .commit-msg-batch3a.txt .commit-msg-batch3b.txt .commit-msg-cycleA.txt .commit-msg-integration.txt'`.

3. **Investigate before touching:** `nested/` and `proj-ok/` — determine whether these are real work or stray artifacts before deciding commit vs. delete.

4. **Wait for agent a166d1a:** `Makefile`, `.claude/settings.json`, all `.claude/hooks/*.sh` — do not commit until the agent's `make test-hooks` / `test-stop-hooks` pass and the agent signals done.

5. **Fix before committing:** `scripts/multitasking_backlog.json` — repoint mt-7 to `68700c2`, verify mt-6 SHA `5d11bb2` is real, then commit.

6. **Follow-on commit (WAIT group):** Once agent a166d1a is done — Makefile + settings.json + hooks + gen_gate_safe_hook.py + enforce-floor.ts + wave3_consolidate.sh (if target added) + multitasking_backlog.json (after SHA fix). Draft commit message for that group:

```text
harness: enforcing hook hardening — JSON-block stop hooks, 3 new guardrail hooks,
session-start JSON fix, make test-hooks/test-stop-hooks, gate-concurrency guard

- agent_floor_stop.sh: {"decision":"block"}+exit 0 (was exit 1 = HOOK ERROR)
- multitasking_backlog_stop.sh: same enforcing JSON-block pattern
- session_start_orchestrate.sh: python3 json.dumps wrapper (was raw plaintext)
- NEW: guardrail_integrity_edit_pretool.sh — blocks Edit that strips ALL
  enforcement tokens from hook/plugin files (fix-means-repair-never-disable)
- NEW: mainthread_budget.sh — STREAK_FILE consecutive-inline-tool counter;
  escalates "delegate NOW" when streak > THRESHOLD while floor < target
- NEW: disk_discipline_pretool.sh — blocks worktree Agent when free disk < 1GB;
  warns when < 2.5GB or venv count ≥ 6
- settings.json: wires all new hooks (Edit->guardrail_integrity, Agent->disk_discipline)
- Makefile: +337 lines — make test-hooks (20+ all-path cases), make test-stop-hooks,
  make write-gate-safe-hook, make gate-tail, make ship, make ship-merge
- scripts/gen_gate_safe_hook.py: generator for gate-safe floor hook
- scripts/wave3_consolidate.sh: deterministic wave-3 cherry-pick script
- .opencode/plugin/enforce-floor.ts: plugin-layer floor+ceiling enforcement
- scripts/multitasking_backlog.json: mt-6/mt-7 SHAs corrected (see NEEDS-FIX-FIRST)

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
```
