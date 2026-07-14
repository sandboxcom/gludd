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

## Wave 0 — land the in-tree release-integrity work (prereq, S)

Already implemented in the working tree this session, needs commit + green CI:
- Verifier hardening + 34-test suite; CI release job runs the verifier
  blocking; `release-create` draft-only + CI-gated; `release-recut` CI-gated;
  both cut paths end on `verify-release-completeness`; repair targets
  (`release-upload-assets`, `release-set-prerelease`, `git-restore-from`);
  ripgrep sha pin fixed; dist tarball inputs restored.
- Acceptance: green Build-and-Release run on the landing SHA; then cut beta.2
  through `release-cut` and require literal `COMPLETENESS CHECK: PASS`.

## Wave 1 — security-critical (do first after Wave 0)

| ID | Defect (verified) | Fix sketch | Effort |
|---|---|---|---|
| A-ESCALATION-SELF-APPROVE | `human_reviewer`/`signer`/`human_resolver` are caller-supplied free text → requester self-approves and mints real STS | approvals must resolve to an authenticated principal (PSK-derived identity or human-todo owner), reject self-approval server-side | M |
| PSK-flat authz cluster | A-EXEC-ROUTES-PSK-ONLY, A-PERMSPEC-SELF-EDIT, A-STS-REVOKE-ANY, A-DEPLOY-DESTROY-ANY, A-ACCOUNT-ANY: any PSK holder has full mutating admin. **Audit narrows the ledger's claim:** the PSK reaches *Ansible-dispatched* agents only (`renderers/runner.py:226-232` re-injects past the scrub; `module_utils/gludd.py:146-154` ambient fallback), NOT the in-process dispatcher (`agents/dispatcher.py:370`, no process boundary) and NOT MCP tool servers (`mcp/transport.py:351-355` allowlist, test-pinned). "Every dispatched agent is admin" overstates it. | per-route capability check against the lattice; ownership columns on permission specs / STS tokens / deployments. Reuse `StsIssuer`/`STSRegistry` (real, subset-enforcing, TTL-clamping) — but **`ornith/client.py` is NOT a working exemplar**: it is constructed only in tests and calls `has_capability`/`mint`, neither of which exists (real API is `capability_for`/`is_denied` and `issue`/`resolve`/`revoke`). The daemon.py middleware wiring is genuinely new code. | L |
| **NEW — stream dispatch leaks the whole daemon env (HIGH, not in ledger)** | `routers/stream.py:81-87` `_run_subprocess` passes **no `env=`**, so the `ansible-playbook` CLI child inherits the entire daemon environment — not just `GLUDD_PSK` but `ZAI_API_KEY`, `AWS_*`, `DATABASE_URL`. It bypasses `_PLAYBOOK_ENV_ALLOWLIST` entirely by shelling out to the CLI instead of going through `CoreAnsibleRunner`. Reachable via `POST /admin/stream/dispatch` with `wait_for_completion: true`. | pass an explicit scrubbed `env=` (reuse `_PLAYBOOK_ENV_ALLOWLIST`). Test: `test_stream_dispatch_child_env_is_scrubbed`. **Latent sibling:** `sandbox_exec/executor.py:12-19` also has no `env=`; inert today (loop.py:1814 passes a marker string, not a real command) but becomes a full-env leak on the MAIN dispatch path the moment a real command is routed through it. | M |
| **NEW — agent modules scavenge the PSK from the environment (HIGH, not in ledger)** | `module_utils/gludd.py:146-154`: `psk = self._psk or os.environ.get("GLUDD_PSK", "")`. Shared by every `general_ludd.agent.*` module, so a role that never passes `psk:` explicitly still gets full-admin auth automatically — this is the channel that converts the env leak above into live admin calls. | delete the `or os.environ.get(...)` fallback: an agent module must present the scoped token it was handed, never scavenge a credential. Test: `test_gludd_client_does_not_scavenge_psk_from_env`. | S |
| A-TOOLEXEC-UNSANDBOXED | live tool-call path runs unconfined; OS sandbox fail-open | wire SandboxEnforcer into the tool-exec path, fail-closed when config demands isolation | L |
| A-COLLECTION-HANDLER-UNWRAP | transient-playbook `task_args` raw YAML bypasses `wrap_extravars` SSTI guard | route through the same wrapper; test with hostile payload | M |
| C-RELOAD residual | `is_protected_path` lexical only; `self_update/apply.py:124-139` raw resolve → symlink bypass live | realpath before compare at both sites | S |
| C-SELFIMP residual | approval flow reads `worktree_path` from live payload (`routers/self_improve.py:465`) not the enqueued artifact → bait-and-switch | bind approval to the enqueued `plan_artifact`; replay-409 test | M |
| C-INTEGRITY | 4 HMAC payloads still `"|".join` (scanner.py:603,622,661,700) → pipe-collision forgery | canonical-JSON payload + scheme tag, keep HWM | M |
| Recursion-guard bypass | resumed agents restart at depth 0 (routers/pause.py:152-160, dispatcher.py:523-532, pause_controller.py:185-195 never persists depth) → unbounded nesting | persist + rehydrate `depth`; test resumes at depth N | S |

## Wave 2 — correctness / dead-gate closure

| ID | Defect (verified) | Effort |
|---|---|---|
| A-COMPLETION-GATE-UNWIRED | quality gates exist but COMPLETE-transition never calls them; `block_todo_complete` is dead config | M |
| C-BUDGET F3+F1 | no reserve/commit path in gateway (TOCTOU); projected-cost defaults 0.0 at 3 sites (budget_guard_check.py:38-51) | M |
| C-ENGINE | commit/test race (`defer_commit` unawaited); sync `execute()` dead code at engine.py:647; `shutdown()` never called from daemon | M |
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
