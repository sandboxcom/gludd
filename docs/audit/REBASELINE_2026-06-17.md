# Re-baseline analysis — 2026-06-17 (testfix branch is STALE vs master)

## TL;DR
The consolidated-security-merge line I was finalizing (branch `integration/batch45-testfix`,
base commit `995c194` + test-update commit `d9b426d`) is a **stale parallel line** of security
work that master has *already integrated through a different path*. Do **not** merge it wholesale.
Re-baseline on master `3223c67` and cherry-pick only the one fix master genuinely lacks.

## Ground truth (verified 2026-06-17, post-gate)
- `master` HEAD = `3223c67`. Its log already contains the batch-4/5 security hardening via:
  - `e982a81` Integration: C1 worker-model wiring + budget kill-switch + cost-aware routing + connector/security hardening
  - `f0dc262` Follow-up: breaker double-count + budget reset/projection + work_type + rg/SSRF hardening + connector localhost guards
  - `a2f0346` Batch 3a: 7 review/audit fixes (cost-metric + 6 security/correctness)
  - `5218549` Batch 3b: dispatch SSTI sandbox + key-collision encoding + markdown comment escaping
  - `3a26b63` Cycle A: GitAutomation.gated_commit / gated_merge
  - `3223c67` ai_parallel_dispatch role (native-async fan-out + barrier-join)
- The testfix branch (`995c194`/`d9b426d`) is NOT an ancestor of master — it diverged and was never merged.

## What master LACKS (the testfix line's unique value)
- **Status host-path removal (P1).** master `src/general_ludd/routers/todos.py:203-218` still returns
  `config_dir`, `config_files`, `filestore_root` (host absolute paths) in the public `/api/status`.
  The testfix branch removes those and replaces them with `config_file_count` (int) +
  `filestore_available` (bool). Asserted by `tests/unit/test_todos_status_redaction.py::TestStatusPathsAbsent`.
  → **This is the only clearly-missing fix worth porting forward.**
- master `db_url` = `str(engine.url)` — SQLAlchemy hides the password by default, so master PASSES
  `test_postgres_password_not_in_db_url`. The testfix branch's status rewrite *broke* its own db_url
  rendering (one of the 7 failures) — a regression introduced by that branch, not present on master.

## The 7 gate failures (on the stale testfix branch, gate run b3t9a4s04, 29m, 11120 passed / 7 failed)
1-3. `test_local_inference_command.py::{test_vllm_normal_config_builds_expected_argv, test_valid_host_accepted[0.0.0.0], test_valid_host_accepted[my-host.example.com]}`
4-5. `test_local_inference_serving.py::TestSlurmInference::{test_slurm_build_command_produces_sbatch, test_vllm_command_format}`
6.   `test_mcp_transport.py::TestMCPClientFacade::test_mcp_client_facade_call_tool`
7.   `test_todos_status_redaction.py::TestStatusDbUrlRedaction::test_postgres_password_not_in_db_url`
These reflect the testfix branch's intermediate code state, NOT regressions on master. Verify each
against master before deciding it's a real bug; most are expected to pass on master already.

## Audit findings caveat (IMPORTANT)
The 5 subsystem audits run earlier today (SSRF/secrets/budget/render/compute) executed against the
**stale worktree** `agent-ad51d3710658408c2` (testfix base). Several findings may already be fixed on
master `3223c67`:
- "Critical: RunBudgetGuard never injected into ModelGateway" — master `e982a81` added "C1 worker-model
  wiring + budget kill-switch + cost-aware routing"; `f0dc262` added "budget reset/projection". RE-VERIFY
  against master before banking/fixing — likely already addressed.
- SSRF connector localhost guards — `f0dc262` "connector localhost guards" may cover the `is_safe_endpoint`
  `instance-data` gap. RE-VERIFY.
- Web render DNS-rebinding + overall_deadline, compute-discovery auth fall-through — these live on the
  web/compute FEATURE worktree branches (not yet on master), so still open; re-audit after those land.

## Recommended plan (execute after session-limit reset @ 16:30 ET, with gate verification)
1. Drop `integration/batch45-testfix` as a merge candidate (keep for reference; do not merge).
2. From master `3223c67`, branch `security/status-hostpath-removal`; port ONLY the `/api/status`
   host-path removal + replacement keys (config_file_count/filestore_available) + the matching
   `test_todos_status_redaction.py` tests. Gate, then ff master.
3. Re-run the 5-subsystem DRY+security audit against master `3223c67` (the earlier run was stale).
   Bank only findings that reproduce on master.
4. Re-launch the DRY workflow (it died because all audit agents hit the session limit → plan=null).

## Blocker
Account hit its **session usage limit (resets 16:30 America/New_York)**. This is what killed: the DRY
workflow (all 10 audit agents failed "session limit" → `plan.fixUnits` null → script threw), the
findings-banking agent (0 tokens), and is unrelated to the gate result. Subagent dispatch will keep
failing until reset — the agent floor is temporarily UNFILLABLE for external-quota reasons (retry the
floor refill after backoff per the transient-error-retry policy), so do not burn cycles re-bursting now.
