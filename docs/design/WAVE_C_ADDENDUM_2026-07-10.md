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

## C-SEC-1 CORRECTION (adversarial review — the parent design was INCOMPLETE)
The WAVE_C_DESIGNS §C-SEC-1 fix (subtract denied in intersection + deny-check at
_enforce_permission/StsIssuer.validate) closes the direct-spec case but NOT the
STS-delegation path it was motivated by. Required additions:
- **Hole 1 (critical):** `StsIssuer.issue()` (sts.py:142-165) stores `subject_spec_request`
  verbatim and `is_subset()` (permissions.py:400-417) never inspects `.denied`, so the
  minted token's `.denied` is whatever the CALLER supplied — the issuer's carve-out never
  enters. Two live callers: `POST /admin/sts/issue` (routers/security.py:298-315, parses
  caller YAML, denied defaults []) and the escalation endpoints (security.py:522/602)
  which `intersection(augmented, human_spec)` — `agent_spec`/issuer_spec is NEVER an
  intersection operand, so an agent/issuer-only deny is invisible. **FIX must also:** merge
  `issuer_spec.denied` (and the human spec's) into the minted spec in `issue()` before
  storing; add sts.py:issue + is_subset + security.py:522/602 to the fix file list.
- **Hole 2:** `StsIssuer.validate()` has ZERO prod callers (only tests); `daemon.py:415-441`
  uses `STSRegistry` which is never assigned (`state._sts_registry` unset) → that scoping
  path always falls through. Fixing validate() is future-proofing; the LIVE exposure is
  Hole 1 (minting) + `_enforce_permission` against directly-configured specs.
- **Hole 3:** empty `denied.actions` is legal + unvalidated → pin the semantics: **empty
  denied.actions == deny ALL actions** (mirror the "empty openbao_paths allow == deny-all"
  precedent). validate() won't catch omission, so encode it in the deny-match logic.
- **Hole 4:** glob "subtraction" in intersection() is NOT generally expressible (literal
  string-set diff leaves `build/*` unchanged when denying one literal key). So step-1
  subtraction is BEST-EFFORT/non-authoritative; the runtime fnmatch deny-checks
  (_enforce_permission / validate) are AUTHORITATIVE. Document this explicitly.
- **Hole 5 (C-SEC-1b ordering contradiction):** a hard `is_subset` reject on
  `requested.max_sts_ttl_seconds > issuer` makes the companion clamp-on-store dead AND
  regresses any sub-3600 issuer to PermissionDeniedError on legitimate requests. **Resolve:
  drop the is_subset hard-reject; keep ONLY the silent `dataclasses.replace` clamp-on-store**
  (max_sts_ttl_seconds is a re-delegation CEILING, not a TTL ask).
- Adjacent (flag, not C-SEC-1): `linux_selinux.py:52` renders denied as `dontaudit`
  (audit-suppression, NOT enforcement) + `typeattribute unconfined_t` → SELinux deny
  rendering is inert. Separate triage item.

## Test drafts ready (full content in task outputs, drop-in)
- `test_permissions_denied_enforced.py` (C-SEC-1, all 3 gates + TTL clamp)
- `test_self_modify_guards.py` + `test_hot_reload_code.py` additions (C-RELOAD)
- `test_integrity_sig_field_injection.py` (C-INTEGRITY)
- `test_model_gateway.py::TestGatewayTimeout` + `test_gateway_base_url_ssrf.py` leak tests (C-GATEWAY)
- `test_budget_guard.py`/`test_budget_guards.py`/`test_spend_limiter.py` reserve/rollover/dedup (C-BUDGET)
- `test_event_loop_dispatch_semaphore.py` (C-EVENTLOOP item 13) + C-SPD1 flush + 4 phase-count updates

## Adversarial review corrections — round 2 (2026-07-10)

### C-GATEWAY — timeout switch has no safe default
**Design being corrected:** C-GATEWAY's `_build_timeout_kwarg` (per-family timeout
plumbing, motivated by the M1 no-timeout bug).
**Hole:** as specced, `_build_timeout_kwarg` is a 3-way string match on
`provider_class_hint` (`ChatOpenAI` / `ChatAnthropic` / `HuggingFaceEndpoint`). But
`ProviderRegistry.from_profiles()` (`models/provider_registry.py:71-101`) lets any
`ModelProfile` register an arbitrary `provider_class_hint` — so any other family
(`ChatGoogleGenerativeAI`, `ChatMistralAI`, `ChatBedrock`, native `ChatVertexAI`, etc.)
falls through with ZERO timeout applied, reproducing the exact M1 bug the design was
meant to close.
**Required design change:** add an explicit fallback branch that attempts a bare-float
`timeout=` kwarg on the unrecognized family, plus a `logger.warning` on that path so
silent no-timeout is at least observable; add a test iterating every `provider_class`
in `PROVIDER_PRESETS` asserting none fall through unhandled. Note `langchain-anthropic`
and `langchain-huggingface` are NOT project deps (`pyproject.toml:47-52` only lists
`langchain-openai`) — those two timeout branches need the packages mocked in tests, not
imported for real.

### C-GATEWAY ↔ C-BUDGET — try/finally scope gap
**Design being corrected:** C-BUDGET's reserve()/release() wrapping of
`_invoke_and_bill`.
**Hole:** the existing try/except in `_invoke_and_bill` (`gateway.py:912-938`) wraps
ONLY `chat_model.invoke()`. It does NOT wrap provider construction
(`gateway.py:824-878`, including the SSRF raises at 833-836) nor the empty-200 guard
raise (`gateway.py:958-961`, which fires AFTER a successful invoke but BEFORE
`record_spend` at 988-989). C-BUDGET's spec is under-specified on which lines the new
try/finally spans, so a naive implementation either reserves budget it never releases
on a construction failure, or fails to release on the empty-200 path.
**Required design change:** C-BUDGET must widen the guarded region to also cover the
empty-200 raise (958-961), releasing (not committing — no real cost is known there) on
that path. `reserve()` must be placed AFTER provider construction (at ~911), never
before line 824, so construction failures (incl. the SSRF raises) fail closed without
ever taking a reservation. Sequencing: land C-GATEWAY first (diff confined to 824-878),
then implement C-BUDGET against a fresh Read of the post-C-GATEWAY file — do not design
C-BUDGET's line ranges against the pre-C-GATEWAY file.

### C-TOOLLOOP — per-response cap burns iteration budget
**Design being corrected:** the per-response tool-call cap for code/bug_fix/refactor/
feature/test work types.
**Hole:** those work types get only `CODE_MAX_ITERATIONS=5` (`execution/tool_loop.py:30`).
A legitimate large honest bundle (e.g. 25 file reads) capped to the default per-response
limit forces the model to re-issue the tail across additional iterations — up to ~40% of
a 5-iteration budget can be spent just recovering the capped remainder, risking a
spurious `ToolLoopExhausted` on otherwise-honest work.
**Required design change:** either add compensating iteration headroom when a
cap-rejection occurred in a prior round, or an explicit non-goal statement accepting the
risk — and either way, a test asserting "capped-then-retried consumes iteration budget"
so the tradeoff is pinned down, not silently rediscovered later.

### C-TOOLLOOP — compaction id-invariant ordering must be explicit
**Design being corrected:** the cap-rejection message injection relative to
`_compact_history`'s verbatim-prefix boundary.
**Hole:** `_compact_history` (`tool_loop.py:340-374`) treats everything from
`open_round_start` (captured at line 241) onward as verbatim/uncompactable. If the
cap-rejection messages are appended BEFORE `open_round_start` is captured, they fall
into the compactable prefix and lose their `tool_call_id` — breaking the id invariant
compaction depends on.
**Required design change:** state as a hard requirement (assert or comment at the
capture site) that cap-rejection messages must be appended AFTER `open_round_start` is
captured, never before.

### C-TOOLLOOP — jsonschema validation is over-strict vs gludd's own builtin
**Design being corrected:** the Draft202012Validator gate in front of `call_tool`.
**Hole:** `WEB_RETRIEVE_TOOL.input_schema` declares `timeout_seconds: integer`
(`mcp/builtins.py:88-93`), but the handler (`mcp/builtins.py:209-213`) tolerates a
stringified `"30"` via `int(timeout_seconds)` + try/except. A strict
Draft202012Validator gate placed in front of `call_tool` would reject the string before
the handler ever runs, narrowing behavior that gludd's own builtin currently accepts.
**Required design change:** add a type-coercion pass before validation, or explicitly
document this as an intentional behavior change (stringified numerics no longer
accepted) so it isn't discovered as a regression later. Confirmed non-issues, no design
change needed: `jsonschema>=4.21` IS a hard dep (`pyproject.toml:44`); the empty-dict
schema skip is safe since `MCPTool.input_schema` defaults to `{}` and is never `None`.
