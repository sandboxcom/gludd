# Wave-3 + Integration Delivery Record
**Date:** 2026-06-18
**Branch:** integration/wave3-final
**Prepared by:** Agentic Harness

---

## 1. Summary

Master was at `3223c67`. `integration/wave3-final` delivers:

- 15 security/quality fix branches merged and reconciled (base fixes, commit `4ad7edc` + recovered SSRF)
- Wave-3 hardenings across secrets, host-path redaction, schemas, CLI, connector SSRF consolidation, SSRF blocklist, and budget default-DENY
- The complete subagent-architecture system (mt-1 through mt-8), all with tests

Final gate reading: **11157 passed / 2 failed** (both failures were stale gate-machinery tests, since fixed). Final ff-merge to master is pending re-gate confirmation.

---

## 2. The 15 Base Fixes

Landed in commit `4ad7edc` plus the separately recovered SSRF branch:

| # | Area | Fix |
|---|------|-----|
| 1 | Budget | B1 gate — budget enforcement gate path |
| 2 | Budget | B3 gate — budget enforcement gate path |
| 3 | Budget | B4 gate — budget enforcement gate path |
| 4 | Budget | B5 gate — budget enforcement gate path |
| 5 | Security | SSRF canonical `security/ssrf.py` — **recovered**: dropped from first integration pass, caught by pre-merge adversarial verification, re-merged |
| 6 | Status | Host-path removal from status responses |
| 7 | DB | `db/repository` dedup |
| 8 | Secrets | Secrets protocol + dedup |
| 9 | MCP | MCP validator dedup |
| 10 | Quality | Coverage shim |
| 11 | Inference | `local_inference`/slurm command dedup |
| 12 | Event loop | `event_loop` budget-block dedup |
| 13 | Models | Routing dedup |
| 14 | Git | `git_automation` gate-runner dedup |
| 15 | CLI | Table-builder dedup; schemas validator dedup; mainthread-budget hook |

---

## 3. Wave-3 Hardenings

| Commit | Area | Description |
|--------|------|-------------|
| `85c59d9` | Secrets | Secrets hardening |
| `95f219b` | Host-path | `db_engine` host-path redaction |
| `d8c1caa` | Schemas | Regression-locks for schemas |
| `4ad9207` | CLI | `_cmd_status` fix |
| `c1f85d2` + `9993253` + `aa1af01` | Connector SSRF | Consolidation of 22 connectors onto canonical `security/ssrf.py`; ~900 lines removed; drift closed |
| `3d660ca` | SSRF blocklist | Added `metadata.goog`, `ip6-localhost`, `ip6-loopback`; corrected `is_global` check |
| `33bb792` | Budget | Default-DENY hardening |

---

## 4. Subagent-Architecture System (mt-1 through mt-8)

All items delivered with tests.

| Item | Capability |
|------|-----------|
| mt-1 | `mainthread_budget` hook — enforces per-agent spend ceiling |
| mt-2 | Floor=6 — minimum live-agent floor advisory |
| mt-3 | Gate concurrency lock — `run_gate.sh` uses `flock` + `mktemp` basetemp + `trap` cleanup |
| mt-4 | `agent_liveness` + `floor_planner` tracking — real-time live count + dispatch recommendations |
| mt-5 | `agent_watchdog` — detects stalled agents |
| mt-6 | Gate-guard — `run_gate.sh` refuses to run when invoked from inside a subagent context |
| mt-7 | Backlog enforcement Stop-hook — blocks completion when backlog enforcement criteria unmet |
| mt-8 | Full integration + wiring of mt-1..mt-7 |

---

## 5. Incidents & Lessons

All five incidents are codified to agent memory and AGENTS.md.

### (a) Silent SSRF branch drop during integration merge
The `security/ssrf.py` canonical SSRF branch was silently dropped during the first integration pass — it did not appear in the merged tree. Pre-merge adversarial verification caught the omission. Recovery: explicit re-merge of the branch. **Lesson:** always run adversarial pre-merge diff verification against the expected branch list before declaring integration complete.

### (b) Over-provisioning caused compute starvation
Running 10+ concurrent worktree agents simultaneously with an active gate caused compute starvation: all agents stalled and the gate was OOM-killed. **Lesson:** the agent floor (6) yields to the resource ceiling (~5-6 concurrent worktree agents max). Never launch a gate while at or above the worktree-agent ceiling.

### (c) Subagent-launched gates get orphan-reaped
Gates launched from inside a subagent were reaped when the subagent exited, leaving no gate result. **Lesson (mt-5/mt-6):** gates must be launched exclusively by the main session; `run_gate.sh` now refuses to run when called from a subagent context.

### (d) Detached-HEAD reverted mid-run
A worktree agent operating on a detached HEAD had its state reverted mid-run when another operation touched the ref. **Lesson:** always use a stable named branch in worktrees; never operate on detached HEAD for anything durable.

### (e) Pre-merge adversarial verification caught a live regression
Before merging connector SSRF consolidation, adversarial review caught a loosened blocklist (missing `metadata.goog` + IPv6 loopback entries). The regression was fixed in `3d660ca` before reaching master. **Lesson:** connector-level SSRF changes require explicit blocklist diff review, not just unit-test pass.

---

## 6. Status

| Metric | Value |
|--------|-------|
| Gate reading (integration/wave3-final) | 11157 passed / 2 failed |
| Failures | Both stale gate-machinery tests — fixed |
| Connectors migrated to canonical SSRF | 22 |
| Lines removed (connector consolidation) | ~900 |
| Subagent-architecture items (mt-1..mt-8) | 8 / 8 done with tests |
| Final ff-merge to master | Pending re-gate confirmation |

Next step: re-run gate on `integration/wave3-final` with the two stale-test fixes applied, confirm all-green, then fast-forward master.
