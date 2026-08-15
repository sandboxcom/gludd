# v0.1.0-beta.2 — next-release specification

Status: DRAFT v1 (2026-07-14). Sources: release-completeness incident audit
(see RELEASE_INTEGRITY_AND_ARTIFACT_COMPLETENESS.md), full design-docs-vs-code
gap audit, and the 2026-07-14 stub sweep (STUB_CLOSURE_SPEC.md). All statuses
below were re-verified against the current tree on 2026-07-14 — several design
docs (AGENTIC spec, Wave C) had drifted stale in BOTH directions and must not
be trusted without the file:line evidence cited here.

## Release goal

beta.2 is the "trustworthy pipeline + no dead gates" release: every publish
path fail-closed (done in-tree, must land green), the highest-risk authz and
correctness gaps closed, and the flagship-but-inert features either wired or
explicitly descoped in docs.

## Wave 0 — make a release mechanically possible again (HARD PREREQ)

**A beta.2 release cannot be cut today, and not because of policy — because the
release job can never fire.** Two blockers, both verified:

### 0a. CI concurrency evicts commits' verdicts (CRITICAL)
`build.yml:46-48` uses a **branch-keyed** concurrency group with
`cancel-in-progress: false` on push. GitHub keeps only one *pending* run per
group, so **each new push silently cancels the queued run for the previous
commit**. Run 29362980590 (`0b6237c4`) reports `"jobs":[]` — pending 19:44:34,
cancelled 19:47:16, **zero jobs executed**. Same for 29364608983 / 29364610894 /
29364661863. **A commit can therefore be tagged having never been tested** — the
absence of a gate, not a red one. This is the mechanism behind beta.1.
**Fix:** key the concurrency group on the SHA (or drop it for push), make
`require-ci-green` fail-closed on `cancelled`/missing, and use
`make ci-verdict SHA=<sha>` (never a branch-based await) in the release path.

### 0b. `test-shard` is chronically red, so `release` never runs
`build.yml:743-747` gates `release` on `needs: [..., test-shard, ...]`. On run
29363154848: both `gate` jobs **pass**, and **10 of 12 test-shards fail**. This is
a **long-standing baseline, not a regression** — the parents fail *harder*
(unit-3 py3.11: **404 failed** at `ad09cc0a` vs **376** at `079b7f5a`), and
`ad09cc0a` predates the verifier entirely. Zero failures come from any file this
session touched. Four tractable clusters:
1. **38× `FileNotFoundError: .opencode/plugin/shared.ts`** — stale test path; the
   file moved to `.opencode/lib/shared.ts` (the plugins already import
   `../lib/shared.ts`; only the tests point at the old location). Fails locally too.
2. **41× `AssertionError: Node exit 1`** — same root cause: the Node e2e tests
   (`test_floor_e2e.py`, `test_multitask_e2e.py`) shell out against that broken
   import graph. Fixing (1) should clear these.
3. **56× `TypeError: 'NoneType' object does not support item assignment`** —
   `tests/integration/test_bill1_slurm_billing_wiring.py:23-29`: an autouse fixture
   does `daemon_mod._daemon_state["todos"] = []` while `_daemon_state` is still
   `None` (it's created with the app). Also hits `test_bill2_*`/`test_bill3_*`.
4. **28× `403 != 200` / 21× `503 != 200`** — default `allowed_cidr` rejects
   TestClient's `testclient` pseudo-host. (Note this is an *improvement* over
   `ad09cc0a`, where it died with `ValueError: 'testclient' does not appear to be
   an IPv4 or IPv6 address` — the guard now fails cleanly.)
5. **84× `/tmp/pytest-of-runner/.../popen-gwN`** — the known xdist tmp-race class.
Molecule is **not** in `needs` and does not block releases — but its root-cause
logs are written to `/tmp/gludd-molecule-*.log` on the runner and **never
uploaded**, so CI failures there are undiagnosable. Fix that observability gap.

### 0c. Land the release-integrity work (already committed, needs a green run)
Verifier hardening + 34 tests; CI release job runs the verifier **blocking**;
`release-create` draft-only + CI-gated; `release-recut` CI-gated; both cut paths
end on `verify-release-completeness`; repair targets (`release-upload-assets`,
`release-set-prerelease`, `git-restore-from`); ripgrep sha pin; restored dist
tarball inputs.

**Acceptance for Wave 0:** a **completed, successful** Build-and-Release run on a
specific SHA (`make ci-verdict SHA=...`), then `make release-cut` producing a
literal `COMPLETENESS CHECK: PASS`.

## Wave 1 — security-critical (do first after Wave 0)

| ID | Defect (verified) | Fix sketch | Effort |
|---|---|---|---|
| A-ESCALATION-SELF-APPROVE | `human_reviewer`/`signer`/`human_resolver` are caller-supplied free text → requester self-approves and mints real STS | approvals must resolve to an authenticated principal (PSK-derived identity or human-todo owner), reject self-approval server-side | M |
| PSK-flat authz cluster | A-EXEC-ROUTES-PSK-ONLY, A-PERMSPEC-SELF-EDIT, A-STS-REVOKE-ANY, A-DEPLOY-DESTROY-ANY, A-ACCOUNT-ANY: any PSK holder has full mutating admin. **Audit narrows the ledger's claim:** the PSK reaches *Ansible-dispatched* agents only (`renderers/runner.py:226-232` re-injects past the scrub; `module_utils/gludd.py:146-154` ambient fallback), NOT the in-process dispatcher (`agents/dispatcher.py:370`, no process boundary) and NOT MCP tool servers (`mcp/transport.py:351-355` allowlist, test-pinned). "Every dispatched agent is admin" overstates it. | per-route capability check against the lattice; ownership columns on permission specs / STS tokens / deployments. Reuse `StsIssuer`/`STSRegistry` (real, subset-enforcing, TTL-clamping) — but **`ornith/client.py` is NOT a working exemplar**: it is constructed only in tests and calls `has_capability`/`mint`, neither of which exists (real API is `capability_for`/`is_denied` and `issue`/`resolve`/`revoke`). The daemon.py middleware wiring is genuinely new code. | L |
| **NEW — stream dispatch leaks the whole daemon env (HIGH, not in ledger)** | `routers/stream.py:81-87` `_run_subprocess` passes **no `env=`**, so the `ansible-playbook` CLI child inherits the entire daemon environment — not just `GLUDD_AUTH_PSK` but `ZAI_API_KEY`, `AWS_*`, `DATABASE_URL`. It bypasses `_PLAYBOOK_ENV_ALLOWLIST` entirely by shelling out to the CLI instead of going through `CoreAnsibleRunner`. Reachable via `POST /admin/stream/dispatch` with `wait_for_completion: true`. | pass an explicit scrubbed `env=` (reuse `_PLAYBOOK_ENV_ALLOWLIST`). Test: `test_stream_dispatch_child_env_is_scrubbed`. **Latent sibling:** `sandbox_exec/executor.py:12-19` also has no `env=`; inert today (loop.py:1814 passes a marker string, not a real command) but becomes a full-env leak on the MAIN dispatch path the moment a real command is routed through it. | M |
| **NEW — agent modules scavenge the PSK from the environment (HIGH, not in ledger)** | `module_utils/gludd.py:146-154`: `psk = self._psk or os.environ.get("GLUDD_AUTH_PSK", "")`. Shared by every `general_ludd.agent.*` module, so a role that never passes `psk:` explicitly still gets full-admin auth automatically — this is the channel that converts the env leak above into live admin calls. | delete the `or os.environ.get(...)` fallback: an agent module must present the scoped token it was handed, never scavenge a credential. Test: `test_gludd_client_does_not_scavenge_psk_from_env`. | S |
| A-TOOLEXEC-UNSANDBOXED | live tool-call path runs unconfined; OS sandbox fail-open | wire SandboxEnforcer into the tool-exec path, fail-closed when config demands isolation | L |
| A-COLLECTION-HANDLER-UNWRAP | transient-playbook `task_args` raw YAML bypasses `wrap_extravars` SSTI guard | route through the same wrapper; test with hostile payload | M |
| C-RELOAD residual | `is_protected_path` lexical only; `self_update/apply.py:124-139` raw resolve → symlink bypass live | realpath before compare at both sites | S |
| C-SELFIMP residual | approval flow reads `worktree_path` from live payload (`routers/self_improve.py:465`) not the enqueued artifact → bait-and-switch | bind approval to the enqueued `plan_artifact`; replay-409 test | M |
| C-INTEGRITY — **CONFIRMED FORGEABLE, no key needed** | All 4 sign/verify fns build the HMAC payload with unescaped `"\|".join(...)` (`integrity/scanner.py:602-603, 615-622, 661, 692-700`) — no escaping, no length prefix, no scheme tag. **Working collision:** `file_path="config"`, `change_type="app\|modified"` and `file_path="config\|app"`, `change_type="modified"` both serialize to `config\|app\|modified\|1111\|2222\|T1` — byte-identical payload, so a signature legitimately minted for one **verifies for the other**. Field-boundary-shift forgery, requires zero knowledge of `GL_INTEGRITY_KEY`. **Not contrived:** `change_type` comes straight from caller-supplied input (`routers/self_improve.py:258` → `str(spec.get("kind","config"))`, plain `str`, no allowlist) and `file_path` can contain `\|` on POSIX. The openbao variant is **worse** — `signer`/`reason` are free text, so an attacker can reassign *who approved a change and why* while keeping a valid signature (`signer="alice"`, `reason="approved\|by-admin"` collides with `signer="alice\|approved"`, `reason="by-admin"`). Attribution/authorization semantics live in exactly the forgeable fields. | canonical-JSON payload (`json.dumps(sort_keys, separators)`) + distinct scheme tags (`gl-integrity-v1` / `gl-integrity-openbao-v1`) to also block cross-scheme replay; keep `compare_digest`. The HWM anti-rollback machinery (`scanner.py:144-217`) is independent and must be preserved untouched. Tests: `test_sign_change_pipe_collision_no_longer_forges` (write it FIRST — it passes today, proving the bug), openbao signer/reason variant, cross-scheme replay, HWM regression guard. | M |
| **C-GITAUTO worktree lock — CONFIRMED NO-OP (highest-severity infra bug found)** | `locking.py:120-131` `_git_dir()` returns `None` when `<repo>/.git` is not a directory. **Inside a git worktree `.git` is a FILE**, so `_git_dir` returns `None`, and `git_repo_lock` (`locking.py:267-280`) then **skips the cross-process flock entirely**. The in-process `RLock` is keyed on `realpath(repo_path)` — the *worktree's own* dir — so it gives zero cross-process protection, and gludd runs **each worktree agent as a separate process**. Net: **concurrent git operations from separate worktree-agent processes against the same underlying repo have NO serialization at all, today.** This repo routinely runs 5+ concurrent worktree agents. `grep git-common-dir` over src/ → no matches, so no workaround exists. | `git rev-parse --git-common-dir` is the right fix — it resolves to the *shared* `.git` (objects/refs/packed-refs, exactly what needs cross-worktree serialization) so every worktree + the main checkout contend for one lock file. Two notes: (1) call it via a plain bounded `subprocess.run`, NOT `_run_git` (which itself takes `git_repo_lock` → recursion before the lock is held); (2) normalize the in-process lock key off the same common-dir so a daemon touching both a worktree and its main repo shares one RLock. Tests: `test_git_repo_lock_uses_common_dir_inside_worktree`, `test_git_repo_lock_serializes_concurrent_worktree_processes`. | M |
| C-GITAUTO tag + push residuals — **partially refuted, real gaps remain** | The blanket "all git ops use raw subprocess" claim is FALSE — `commit`/`create_branch`/`tag_release`/`push`/`merge_branch`/`gated_commit`/`gated_merge` all route through `_run_git` (lock + 60s timeout). Real gaps: **`push_to_remote` (repo.py:979-1013) is unlocked** — its own comment says it couldn't use `_run_git` (pinned to `self.repo_path`), but `git_repo_lock(repo_path)` is a free function other methods already call with an explicit path (`repo.py:761`, `887`), so the lock was simply never added. **`tag_release`/`tag_checkpoint` (repo.py:412-418) have no `_reject_leading_dash` and no `--` separator** — unlike every sibling method — so a caller-supplied tag like `-d`/`--force` is parsed by `git tag` as an option (argument injection). Also unlocked/untimed: `init_repo`, `create_worktree`, `remove_worktree`, `create_release_tag`, `create_checkpoint_tag`, `create_local_bare_mirror`. | wrap `push_to_remote` in `git_repo_lock(repo_path)`; add `_reject_leading_dash(tag)` + `--` to both tag fns; route the remaining raw-subprocess methods through the lock. Tests: `test_tag_release_rejects_leading_dash`, `test_push_to_remote_holds_git_repo_lock`. | S |
| Recursion-guard bypass | resumed agents restart at depth 0 (routers/pause.py:152-160, dispatcher.py:523-532, pause_controller.py:185-195 never persists depth) → unbounded nesting | persist + rehydrate `depth`; test resumes at depth N | S |

## Wave 2 — correctness / dead-gate closure

| ID | Defect (verified) | Effort |
|---|---|---|
| A-COMPLETION-GATE-UNWIRED — **PARTIALLY REFUTED, re-scoped** | There are **two easily-conflated gate systems**. **System B (`quality/project_gate.py` `run_project_gate`, the target-project lint/test gate) IS WIRED** at both completion-transition sites — `review/decision_applier.py:57-110` and `event_loop/loop.py:3347-3395` — and downgrades `complete → needs_more_work` on failure. It is powered by the real P-1 toolchain runner (`project_runner/detect.py` → `runner.py:186`) and is fail-closed by design. **System A (`quality/gate.py` `QualityGateChecker.enforce()`) is the dead one**: zero production callers; the only prod use of the class is `routers/maintenance.py:118-126`, which calls `check_python_coverage()` and never `enforce()`. So the original "nothing gates completion" claim is WRONG — do not re-plan from it. **What IS real:** (1) `block_todo_complete` (`schemas/quality_gate.py:62`) is dead config **everywhere** — System B's downgrade is hardcoded/unconditional, and so are its 5 siblings `block_commit`/`block_merge`/`block_tag`/`block_push`/`block_reload`; (2) both call sites pre-check `(workspace/"project.yml").is_file()`, so they **never reach `load_project_profile`'s auto-detect fallback** — target projects without an explicit `project.yml` get **no completion gate at all**, though the runner supports them; (3) self_update reload (`loop.py:3141`) and self-improve config-tier approval (`routers/self_improve.py:299-300`) bypass System B entirely. **Fix:** wire `block_todo_complete` into System B (not System A) at decision_applier.py:83 / loop.py:3369; drop the `project.yml` pre-check so auto-detected profiles are gated; plumb `QualityGateConfig` from daemon config (nothing constructs it with non-defaults today). | M |
| **NEEDS_MORE_WORK is a permanent dead end — COREQUISITE, must land with the gate fix** | CONFIRMED: `VALID_TRANSITIONS` legally allows `NEEDS_MORE_WORK → {QUEUED, ACTIVE}` (`db/repository.py:74`, `schemas/todo.py:100`), but the **only** production transition-to-QUEUED site is `self_improve/approval.py:91-96`, which fires solely on human approval-release from `APPROVAL_REQUIRED`/`MANUAL_HOLD` — never from `NEEDS_MORE_WORK`. `claim_runnable()` (`db/repository.py:442`) claims only `QUEUED`, so **NEEDS_MORE_WORK todos are invisible to the dispatcher forever**. `remediation/blocker_detector.py:246-298` sees them but only emits a read-only human finding. Since System B's gate-failure path lands todos exactly there (`decision_applier.py:92`, `loop.py:3395`), **strengthening the gate without this fix strands MORE work permanently.** **Fix:** a requeue sweep (new EventLoop phase or an action off `remediation_scheduler.py`) moving NEEDS_MORE_WORK → QUEUED after a cooldown, respecting `max_requeues_before_chronic` so chronic failures park instead of looping. | M |
| **C-BUDGET — KEYSTONE: model cost rates are structurally $0 (HIGH, real money loss)** | **NEW, and it is the actual root cause.** `ModelProfile.cost_per_input_token` / `cost_per_output_token` default to `0.0` (`gateway.py:186-187`) and **no production code ever sets them non-zero**. So `_invoke_and_bill` (`gateway.py:1034-1037`) computes `cost_estimate == $0` and the one working reservation reconciles to zero **every single time**. Every other budget fix is cosmetic until this lands. (Already known to the test suite as CA-T12 in `tests/integration/test_budget_integrity.py`.) | seed the per-token rates from a real pricing catalog at profile-load time. **Fix this FIRST.** Test: `test_model_profile_rates_nonzero_by_default`; extend CA-T12 to assert a non-zero, cap-tripping reconcile. | M |
| C-BUDGET F3 — **REFUTED as written; real gap is different** | The reserve→reconcile path **exists and is correct**: `daemon.py:2056-2114` (`_gateway_executor`) does `check_daily_budget_reserved()` + `check_todo_budget()` with a real non-zero projection (computed at `daemon.py:2041-2054`), `record_spend()` on success, `release_reservation()` on failure — tested in `test_c4_budget_fixes.py`. **Do NOT "add a reserve path" — it's there.** The real gap: it is wired **only** at the AgentDispatcher layer. The **primary code-gen path bypasses BudgetManager entirely** — `execution/engine.py:528-531`, `:684-686`, and `execution/langgraph_agent.py:225-231` call `call_model` with no `estimated_cost`/`budget_remaining` and never touch BudgetManager. | wrap the engine's `call_model` in the same reserve→reconcile→release cycle `daemon.py:2056-2114` already implements. Tests: `test_execute_async_threads_estimated_cost_to_gateway`, `test_execute_async_reserves_and_reconciles_todo_budget`, `test_execute_async_releases_reservation_on_model_call_failure`. | M |
| C-BUDGET F1 — CONFIRMED | `budget_guard_check.py:38-51` is the *definition* (`projected_cost: float = 0.0`); its own docstring names the 3 unfixed callers, all of which call `budget_pre_check(guard)` with no projection: `models/job_invocation.py:119`, `execution/tool_loop.py:212`, `review/reviewer.py:219`. Reactive-only — **can never block the call that itself breaches the cap.** Related: `ModelGateway.check_budget` (gateway.py:590-638) is real and threaded on the failover path, but its `estimated_cost`/`budget_remaining` kwargs default `0.0`/`inf` and **no production caller supplies them** → inert in prod, exercised only by tests. | thread a real projection at the 3 sites using the `token_cost_usd` helper already used at `daemon.py:2041-2054`. Test: `test_budget_pre_check_nonzero_projection_blocks_over_cap_call` (×3). | S |
| C-ENGINE — **`defer_commit` race REFUTED**; shutdown + dead-code CONFIRMED | The commit/test race claim is **wrong** — `engine.py:444-474` is a deliberate fire-and-forget: `asyncio.create_task`, tracked in `self._background_tasks`, `_commit_lock` held, done-callback logs exceptions; files are written by `_write_file` **before** `defer_commit` (`:595`), so `_run_tests` (`:597-599`) reads disk, not git state. No data race — drop this from the plan. **CONFIRMED:** (1) `shutdown()` (`engine.py:476-489`) cancels+awaits background tasks but has **zero production callers** — daemon shutdown (`daemon.py:2246-2351`) stops Slurm/pipeline/mcp/event_loop/otel but never the engine → in-flight `defer_commit` tasks are abandoned on SIGTERM, code written to disk with the commit never landing (**data-loss window**); (2) sync `execute()` (`engine.py:647-811`) duplicates `execute_async` minus the async fixes, has zero prod callers, and even calls `asyncio.create_task` inside a non-async fn behind `except RuntimeError: pass` — migration residue. | add `await engine.shutdown()` to the daemon shutdown sequence (~`daemon.py:2295-2310`); delete sync `execute()` and port its 2 e2e callers to `execute_async`. Test: `test_daemon_shutdown_drains_execution_engine_background_tasks`. | S |
| C-LANGGRAPH | `make_langgraph_tool_loop` (capabilities.py:199-236) passes no budget_guard + `chat_model=None`; `_resolve_chat_model` never calls `get_chat_model` | M |
| C-EVENTLOOP | no dedicated bounded executors (`concurrency/executors.py` absent); ~20 sites share default pool | M |
| C-GATEWAY | no per-family timeouts; `get_chat_model` ctor has no timeout | M |
| C-TODOMODEL/C-FILESTORE | no todo blob caps; no bounded decompression; no atomic write | M |
| C-CONNECTORS | `_associate_by_window` NaN/Inf poisoned-anchor (base.py:379-398) | S |
| QualityGateChecker fail-open | `g.get("passed", True)` at quality/gate.py:79 (sibling preflight.py:379 is fail-closed) | S |
| ck_todos_priority_range | model CHECK (models.py:286) missing from migration chain — parity test RED today (test_alembic_create_all_parity.py:363) | S |
| AgentCapabilities default | bare `AgentRegistry()` at capabilities.py:82 (same class as the fixed daemon bug) → `default_registry()` | S |
| Open alpha4 residuals | validation/runner.py:122-160 cwd not symlink-confined; loop.py:752-773 PID cap after ACTIVE mark; loop.py:528-561 review to_thread no timeout | M |
| NEW_FINDINGS residuals | gateway fallback ignores health/budget (C28); TodoModel.version not `version_id_col` (C30); auth fail-open when GLUDD_REQUIRE_AUTH unset + `/docs` startswith over-match | M |

## Wave 3 — flagship features that are silently inert (wire or descope)

Highest leverage first; each is "plumbing exists, production never populates/
consumes it" — the recurring failure shape this release must break:

1. **P-3 retrieval indexer** (deep-audit CONFIRMED; design ready):
   `CodebaseIndexer.index_files()` has zero src/ callers —
   `app.state._codebase_indexer` is set at daemon.py:1126 and **never read
   again**. The consumer side is fully wired (daemon.py:1387-1394 →
   engine.py:508/664 → `_inject_retrieval_context` engine.py:127-164), so
   `SemanticSearcher.search` always returns `[]` and the prompt is returned
   unmodified — a silent no-op through the whole chain. Single biggest quality
   lever. Design:
   - **Cold-start bulk index** after daemon.py:1125 via `asyncio.create_task` +
     `asyncio.to_thread(indexer.index_files, …)` (indexing does sync
     `read_text()` + regex tokenization per file — must never run on the loop).
     Discover files with `git ls-files --cached --others --exclude-standard`
     (there is no gitignore parser in `src/` — reuse git's own logic).
   - **Incremental refresh** as a new `_phase_refresh_codebase_index` EventLoop
     phase near loop.py:1052, throttled (`retrieval.reindex_interval_s`,
     default 300s). Note the phase list is asserted by
     test_event_loop.py:60-76 — that test must be updated.
   - **Manifest** `{relpath: mtime_ns}` under a reserved `"__manifest__"` key in
     the same diskcache.
   - **BLOCKER — purge does not exist**: `CodebaseIndexer` (retrieval/indexer.py:35-126)
     has **no delete/purge method at all**. Two correctness holes: deleted files'
     chunks stay retrievable forever, and a file that shrinks orphans its old
     trailing chunk keys (`path:3`, `path:4`, …) because `index_files` only
     overwrites keys it re-emits. Must add `remove_file(path)` (diskcache prefix
     iteration) called for deletions **and before re-indexing any changed file**.
   - Also flagged (separate scope): `ExecutionEngine.workspace_path`/`searcher`
     are single global instances (daemon.py:1387-1394) — not per-project.
   - **Proof test**: the existing test_codebase_indexer_wiring.py:94-102 only
     asserts the *constructor line exists in source*; it does not prove indexing
     runs. Add an e2e that goes through the real startup path and asserts the
     system prompt actually contains `"Relevant Codebase Context:"` with no
     manual `index_files()` call in the test. (M)
2. **ApprovalGate**: whole class returns PENDING forever (approval/gate.py:31-33);
   G7 HITL has no decision mechanism. Wire to human-todos resolve. (M)
3. **Pause/resume conversation history**: snapshot.messages always `[]`
   (pause_controller.py:185-195) — resumed agents restart cold while the API
   says `"resumed": true`. Persist and rehydrate messages. (M)
4. **EvidenceChecker**: any non-empty source list marks any claim "supported"
   (evidence_checker.py:45-47, 64-70). Per-claim source matching. (M)
5. **Estimation feedback loop**: reviewer.py:150-157 hardcodes zero actuals;
   `record_estimate()` zero prod callers → calibration never fires. (S)
6. **StallWatchdog action**: stall events published, zero consumers — wire a
   consumer (re-dispatch / human-todo / kill) or the watchdog is a log line. (M)
7. **code_quality_score=0.5 constant** feeds live model routing
   (recorder.py:25 → loop.py:2804 → repository.py:995-1002): pass real test
   results or exclude the constant from scoring. (S)
8. **P-7 watch mode**: `WorktreeMonitor` constructed (daemon.py:1988) but
   `start_watching()` never called. (M)
9. **Self-improve outputs discarded**: loop.py:4416-4433 reduces suggestions
   to a log line; error-patterns written but never read. Persist + surface. (S)
10. **HibernationController.parked()** never called from dispatch path. (M)
11. **security_backlog.py probes**: zero CI callers — wire into `make security`. (S)

Descope-or-delete candidates (dead code enshrined by tests): ssh_key_rotation
stub keys, ReloadManager.rollback() fake status flip, ReflexionLoop,
OutcomeObserver, GrindingDetector class-half, MetricsExporter.gauge_set,
classifier llm_route scaffold. Each either gets a real implementation ticket
in beta.3 planning or is deleted with its stub-asserting tests.

## Wave 4 — parity (Wave P) increments

P-2a `gludd run` autonomous entrypoint (M); P-2b unified `notifications
list/resolve` surface (M); P-4 SEARCH/REPLACE + repair loop and fix
`_apply_unified_diff` silent `[]` failure (M); P-6 rollback subcommand (S);
P-8 recipe/TaskTemplate over the live cron scheduler (M); P-9 `--parallel`
flag over the live worktree fan-out (S). P-5 LSP / P-10 repl_eval / P-11
reference graph: defer to beta.3 unless capacity remains (L each).

## Wave 5 — ledger + docs hygiene

- Re-triage `docs/audit/SESSION_2026-07-10_AUDIT_LEDGER.md`: stamp per-item
  status (many were fixed by TASKS.md Phase H, mapping in the gap audit).
- TASKS.md: summary table says "1 pending" but zero unchecked boxes exist;
  ~20 checked items carry unquantified wave-label evidence (violates the
  repo's own evidence rule) — backfill test counts/run ids or re-open.
- Correct stale design docs both directions (SPD-1, D7.2, C-TOOLLOOP, half of
  C29 are DONE but described as dead; C-INTEGRITY/C-GITAUTO described as
  designed-turnkey remain unbuilt).
- Project-memory correction: the "ansible-collection-shadowing RCE" does not
  exist under that name in docs/audit/; closest is H-SKILL-SHADOW (MED,
  prompt-injection, not RCE). Do not re-plan work from that memory claim.

## Sequencing & landing protocol

Wave 0 lands first and alone (it unblocks trustworthy releases). Waves 1-2
land as file-disjoint parallel batches per PARALLEL_IMPLEMENTATION_AND_PARITY
Part 1 (single-writer-per-file matrix), keeping CI green batch-by-batch.
Wave 3 items 1-4 are user-visible headliners for beta.2 notes. Every item
needs a failing-test-first commit trail and a `| evidence:` line in TASKS.md.
