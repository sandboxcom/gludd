# Wave C Addendum — late-session verified findings (2026-07-10)

Extends `WAVE_C_DESIGNS_2026-07-10.md` with findings verified/designed in the
later part of the session. Each entry: verdict + exact locus + fix pointer.
Line numbers are current-tree; re-Read before editing. Same landing discipline
as the parent doc (single-writer per file, targeted tests, CI-green per batch).

## Verified ALREADY-FIXED (move to spec Already-Done; do NOT rework)
- **Integrity C5 H1 (store signing) + H2 (corrupt-store rebaseline)** — FIXED.
  `integrity/scanner.py` signs the baseline store with a sidecar HMAC (`.mac`) +
  monotonic-counter/high-water-mark anti-rollback; corrupt/truncated store raises
  `IntegrityStoreError` (no silent rebaseline); first-run vs deletion distinguished;
  key fail-closed via `GL_INTEGRITY_KEY`. Tests: `TestHashStoreSigned` (7),
  `TestHashStoreAntiRollback` (4), `TestHashStoreDeletedStore`.
- **Per-project secret isolation** — ENFORCED (the "0 callers / unscoped" premise is
  stale). Scoping is in `secrets/project_secrets.py` `ProjectSecretsManager`
  (`_scoped_path` prefixes `projects/<project_id>/`, rejects `/`/`..`), wired
  daemon→gateway→job via `daemon.build_secrets_resolver` `_LazyProjectSecrets.for_project`
  (daemon.py:322-397/1117), `ModelGateway._resolver_for_project` (gateway.py:729-764),
  threaded from tool_loop.py:391/407, job_invocation.py:155, worker/app.py:171,
  langgraph_agent.py:177. Tests: test_project_scoped_secret_wiring, test_secrets_redteam
  TestProjectSecretsScoping. **Residual (LOW, dead code):** `engine.py:637` sync
  `execute()` calls `call_model` without `project_id=job.project_id` — add it for
  consistency (execute() is dead per C-ENGINE).
- **Daemon bind / allowed_cidr** — FIXED. `cli.py:295` `--host` defaults `127.0.0.1`;
  non-loopback host auto-generates a PSK (cli.py:1154-1160); host/port validated
  (cli.py:3006-3049); `infra/compute.py:76` `allowed_cidr="127.0.0.1/32"`. Tests exist.

## STILL-OPEN — new designs

### C12 — events/EventBus: no locking + list-mutation-during-iteration + unbounded webhooks (MED)
**Files:** `events/bus.py`, `events/hooks.py`
- No lock anywhere. `EventBus.subscribe` `self._next_id += 1` (bus.py:24) + `HookSystem.register_callback` `self._next_cb_id += 1` (hooks.py:117) are non-atomic → duplicate ids under concurrency → `unsubscribe` removes the wrong/both. **Fix:** `threading.RLock` in each `__init__`; wrap subscribe/unsubscribe/register/unregister bodies; in `publish` wrap only the snapshot.
- `HookSystem.fire` (hooks.py:177) `hooks = self._hooks.get(event_name, [])` is NOT a snapshot; a callback that `register_callback`s the same event re-`sort()`s the list mid-iteration → silent skip/double-fire. **Fix:** `hooks = list(self._hooks.get(event_name, []))`.
- Webhook concurrency unbounded (`_pending_webhooks` plain set, fresh `httpx.AsyncClient()` per fire, hooks.py:249) → self-DoS. **Fix:** `_MAX_INFLIGHT_WEBHOOKS=50` drop-and-log cap + a shared client with `httpx.Limits`.
- **Tests:** `test_events_concurrency.py` — unique-ids-under-threads, reentrant-register-no-corruption, webhook-concurrency-bounded.

### Admin API rate-limiting + body-size cap (MED, OPEN) — [Finding B of bind/rate audit]
**Files:** new `rate_limit.py`, `daemon.py:2468` middleware, `config/user_config.py`
No rate limiter or body cap on the PSK-gated `/admin/*` `/api/*` surface (the
receiver's `_RateLimiter`+`MAX_BODY_BYTES` at `receiver/router.py:104-243` covers only
`/v1//ingest`). `/admin/self-improve/run` (unbounded LLM spend) + `/admin/security/scan-text`
(no `max_length`) are the concrete unbounded-cost/body endpoints. **Fix:** generalize
receiver's token-bucket into `general_ludd.rate_limit.RateLimiter(rate, burst)`; wire a
keyed rate check into `auth_and_stats_middleware` (429+Retry-After, exempt healthz) + a
separate ASGI body-size middleware (413), with per-path stricter overrides; config block
`admin_rate_limit` on UserConfig. Designs already in feature_package_wiring.md §9 +
observability_receiver.md §1.5.

### rg_search root unconfined + unbounded output (MED, OPEN)
**File:** `code_intelligence/rg_search.py`. `search(root=".")` passes `root` to argv with
zero validation (line 199/129); no output cap. **Fix:** `workspace_root` param;
`Path(root).resolve()` + `is_relative_to(workspace_root)` fail-closed; `MAX_MATCHES` cap
+ `truncated` flag; `--max-filesize` on argv. (Latent — no live MCP tool wraps it yet.)

### Runtime bundle signing self-referential (HIGH, OPEN)
**File:** `runtime/release.py:37-98`. `MANIFEST.json`+`CHECKSUMS.sha256` are co-located
and cross-check each other only — no external signature; tamper-then-rewrite-both passes
(`validate_release` returns valid). **Fix:** mirror `integrity/scanner.py`'s sidecar HMAC:
sign the serialized manifest with `GL_INTEGRITY_KEY` at build (`MANIFEST.json.mac`), verify
with `hmac.compare_digest` before trusting it in `_check_pip_bundle`, fail-closed on
missing/mismatch. Bind release `version` into the signed message for anti-downgrade.

### Ansible process_isolation missing-binary = daemon CRASH, not fail-open (HIGH, OPEN)
**File:** `ansible/core_runner.py`. ansible-runner's own preflight does `sys.exit(1)` when
the isolation executable is absent — a `SystemExit` (BaseException) that slips past every
`except Exception` (core_runner.py:647, runner.py:210, loop.py:754) → propagates uncaught
through `_run_phases`→`tick`→`run_forever`, killing the daemon. **Fix:** preflight
`shutil.which(iso.executable)` and return a failed `AnsibleResult` (fail-closed) when
missing unless an explicit `allow_unconfined_fallback` opt-in; widen the catch to
`(Exception, SystemExit)` as defense-in-depth. Tests: missing-binary refuses run + never
runs unconfined; SystemExit caught not propagated.

### TaskReturnRepository.get_by_id unscoped + TodoRepository.update status-mass-assign (MED/LOW, OPEN)
**File:** `db/repository.py`. `TaskReturnRepository.get_by_id` (621) has no `project_id`
param (unlike every sibling on the class) → a future by-id route leaks another tenant's
return. `TodoRepository.update` `_IMMUTABLE_UPDATE_FIELDS` already blocks `project_id` but
NOT `status` (deliberately mutable) — latent state-machine bypass (no live prod caller
sets status via update). **Fix:** add optional `project_id` to `get_by_id` (default None
for back-compat); add `status` to `_IMMUTABLE_UPDATE_FIELDS` (status only via `transition()`)
+ migrate ~5 test seed sites to `transition()`. Tests: get_by_id scopes; update rejects
status/project_id.

### NEEDS_MORE_WORK dead-end status — failed tasks stall (MED, OPEN)
**Files:** `review/decision_applier.py`, `db/repository.py:424-506`, `event_loop/loop.py`.
A failed task set to `NEEDS_MORE_WORK` is never re-selected (`claim_runnable` only takes
QUEUED) and never auto-promoted → it stalls until the coarse `RemediationDispatcher` spawns
a brand-new todo; the specific test/diagnostic output is never fed back to retry the
original. **Fix:** a bounded auto-retry phase that re-promotes NEEDS_MORE_WORK→QUEUED with
the failure diagnostics attached to the todo context, capped at N attempts, then escalates
to a human-notification (ties to P-2b). Add an attempt-counter field. (Design agent
dispatched — see task output.)

## Cross-cutting design decision (folds three Wave P items into one)
**Unify the P-4 edit-repair loop and P-5 LSP-diagnostics loop into ONE in-engine
fix-loop** in `execution/engine.py` (execute_async 455-598 / execute 600-755): a single
`while attempt < _MAX_FIX_ATTEMPTS` wrapping call_model→apply-edits(collect failures)→
run-tests(P-1)+LSP-diagnostics(P-5), re-prompting with combined feedback (failing hunk +
current file + test/LSP errors) as a new user turn, budget-rechecked per attempt,
partial-success-aware (don't resend applied edits), final exit_code=1 if unresolved. P-4,
P-5, and test-retry must NOT each build a competing loop.

## Test drafts ready (full content in task outputs, drop-in)
- `test_permissions_denied_enforced.py` (C-SEC-1, all 3 gates + TTL clamp)
- `test_self_modify_guards.py` + `test_hot_reload_code.py` additions (C-RELOAD)
- `test_integrity_sig_field_injection.py` (C-INTEGRITY)
- `test_model_gateway.py::TestGatewayTimeout` + `test_gateway_base_url_ssrf.py` leak tests (C-GATEWAY)
- `test_budget_guard.py`/`test_budget_guards.py`/`test_spend_limiter.py` reserve/rollover/dedup (C-BUDGET)
- `test_event_loop_dispatch_semaphore.py` (C-EVENTLOOP item 13) + C-SPD1 flush + 4 phase-count updates
