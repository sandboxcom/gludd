# New audit findings (2026-06-16) — fleet pass, verified file:line

Fresh real findings from the deeper-coverage audit fleet. All NEW (not in
BACKLOG_RECONCILED). Queue into batch-4D/4E worktree writers.

## db/models.py
- **P1** `TaskDecisionModel.return_id` (L191): no `ForeignKey("task_returns.return_id")`,
  no `unique=True` → dangling/conflicting decisions. Fix: FK(ondelete=CASCADE)+unique+index.
- **P2** `TodoModel.version` (L109): integer counter NOT wired as `__mapper_args__ =
  {"version_id_col": version}` → optimistic lock is a no-op (last-writer-wins silently).
- **P2** Unbounded `Text` blobs: `AuditEventModel.details` (L251),
  `TaskDecisionModel.todo_updates`/`child_todos`/`validation_requests` (L202-207) — no size
  guard; a runaway turn inserts MBs/row. Fix: table `CheckConstraint("length(col) <= 65536")`.

## secrets/manager.py
- **P1** `resolve()` (L112-115): leaks secret material via `str(exc)` in log + exception
  message (hvac exc can carry response body). Fix: use `type(exc).__name__` only. Same at
  `read_secret` L248.
- **P1** `register_alias`/`SecretAlias` (L66-91): caller-controlled `path`/`mount` stored
  verbatim → `resolve()` can read arbitrary backend paths (`../sys/policy`, mount `sys`).
  Fix: allowlist path (`^[A-Za-z0-9_/-]+$`, no `..`) + permitted mounts only.
- **P3** `connect()` (L140-141): dead None-check after `is_external_configured()`; replace
  with explicit assert (false-confidence latent gap).

## dispatch (routers/dispatch.py + dispatch/dynamic_dispatcher.py)
- **P2** No per-request `tool_calls` count cap (router L90 → `dispatch_all`): N calls = N×cost.
  Fix: `MAX_CALLS_PER_REQUEST=20`, HTTP 422 over cap.
- **P2** `UNRESTRICTED_ROLE` is a string sentinel (`"__unrestricted__"`): future mis-wire that
  copies role from request body = capability bypass. Fix: module-private singleton object.
- **P2** `call.name`/`kind` reflected verbatim into logs + `DispatchResult` error returned to
  caller (dyn L203-224) — log injection + unbounded. Fix: truncate (name 256, kind 64).

## worker (worker/app.py + ansible/runner.py)
- **P1** No workspace cleanup on failure (app.py L195-217): job dir leaks permanently if
  `run_playbook` raises. Fix: try/finally `shutil.rmtree(dirs["root"], ignore_errors=True)`.
- **P2** Per-job timeout not threaded through `asyncio.to_thread` (app.py L212): if thread
  stalls before runner's own 300s deadline, handler hangs. Fix: `asyncio.wait_for(..., timeout)`
  + `JobSpec.timeout` (server-capped).
- **P2** Duplicate `job_id` silently overwrites workspace (runner.py L82/L91 `exist_ok=True`):
  second job truncates first's extravars mid-run. Fix: `exist_ok=False` → HTTP 409.

## secrets/cosign.py
- **P1** `CosignKey` dataclass default `__repr__` leaks `private_key`+`password` into any log
  that touches the object (L12-17). Fix: `field(repr=False)` on both. (One `logger.debug(key)`
  away from a clear-text key leak.)

## models/gateway.py + timeout_detector.py (resilience call paths)
- **P1** `call_model_with_fallback` (gateway L663-688) never consults `_health_tracker.is_healthy`
  → hammers an OPEN circuit on primary AND every fallback, unconditionally, each call =
  retry-storm amplifier. (`call_model_with_retry`/`_walk_fallbacks` DO gate — this path doesn't.)
  Fix: is_healthy guard before each `_try_call_model`.
- **P2** 429 `Retry-After` header never threaded: `_before_sleep` (gateway L496-499) always passes
  `retry_after=None` to `_compute_backoff` though the policy honours it → exponential 0.6s backoff
  ignores provider window, can re-trigger 429. Fix: extract `Retry-After` from httpx exc, pass it.
- **P2(arch)** `ModelHealthTracker` state is per-process dicts → under gunicorn `--workers N` each
  worker has its own breaker; a tripped provider still gets N× load. Fix: shared store, or document
  `--workers 1` (async-loop model already implies it). Classification logic itself is correct.

## models/gateway.py (model router / budget)
- **P1** `call_model_with_fallback` (L663-688): fallbacks go through `_try_call_model` which
  swallows `ValueError` (the budget-rejection type) and does NOT forward `estimated_cost`/
  `budget_remaining` (defaults 0.0/inf) → a costlier fallback runs even when over budget. Fix:
  thread estimated_cost+budget_remaining through every fallback call.
- **P2** Unknown role NOT fail-closed (gateway L626-650 + router.py L20-28): `resolve_role`
  silently returns `default_profile_id` for any unmapped role → arbitrary role string → default
  model. Fix: distinguish mapped vs fell-through; raise on unrecognised role.
- **P2** `check_budget` (L163-171) trusts caller `estimated_cost` (default 0.0) → metered profile
  invoked with 0 pre-flight cost; real cost only recorded post-call, never compared to remaining.
  Fix: compute worst-case `max_in*cost_in + max_out*cost_out` pre-call, or require >0 for metered.

## agents/tool_adapter.py + agents/dispatcher.py (permission enforcement)
- **P1** `AgentDispatcher.dispatch_one` (dispatcher.py L66-101) NEVER calls
  `registry.can_invoke` — the whole `allowed_subagents`/`can_dispatch_subagents` matrix is
  implemented + tested but **dead at runtime**. A restricted agent can dispatch a privileged one
  (can_edit/can_bash). Fix: add `invoker_name` to AgentTask + `can_invoke` check at top of
  dispatch_one.
- **P2** `AgentToolAdapter.list_agent_tools`/`get_agent_as_tool` (L11-31) advertise ALL agents as
  callable tools with no invoker filter — defense-in-depth bypass even if dispatch is fixed. Fix:
  take `invoker_name`, filter by `can_invoke`.
- **P2** `AgentConfig.enabled` (types.py L37) never checked at dispatch → disabling an agent has
  no runtime effect. Fix: reject disabled in dispatch_one + can_invoke.
- **P2** `AgentRegistry.register` (registry.py L21-23) is public + unsealed → any code can inject an
  all-powerful agent (`allowed_subagents=["*"]`, can_edit/can_bash) post-startup. Fix: `seal()`
  flag set at end of `default_registry()`/daemon startup; `register` raises when sealed.

## alembic migrations
- **P1** Migration drift: `001_initial_schema.py` creates 9 tables but ORM now defines 16+
  (projects, features, prompt_profiles, agent_messages, spend_records, role_runs,
  benchmark_results, memory_records) + new `project_id` FKs on 8 tables. `alembic upgrade head`
  on prod leaves schema inconsistent → runtime OperationalError. Fix: write 002+ (autogenerate).
- **P2** `001 downgrade()` drops all 9 tables unguarded → `alembic downgrade base` = instant
  silent data loss. Fix: env/confirm guard.
- **P3** `alembic.ini` hardcodes `sqlite:///./test.db`, env.py never reads `DATABASE_URL` → a prod
  `alembic upgrade` silently migrates a local test.db (false success). Fix: 3-line DATABASE_URL
  override in env.py.

## events/hooks.py (bundle with the queued SSRF/redirect fix — one writer)
- **P1** `_fire_webhook` (L159) calls **sync** `httpx.post` from an async publish path → freezes
  the event loop up to `timeout_seconds × retry_count`. Fix: `httpx.AsyncClient` + async, or
  `run_in_executor`.
- **P1** Full event payload forwarded verbatim to webhook (L155): `ModelAddedEvent.profile`
  (types.py:40) can carry model credentials → secret exfil to any registered URL. Fix: explicit
  payload-key allowlist before serialising.
- **P2** `register_webhook` `retry_count` (L62-69) caller-controlled, no upper bound → `10_000 ×
  timeout` loop = event-loop DoS (compounds the sync-call gap). Fix: clamp `min(max(1,n),5)`.
- NOTE: the queued hooks fix already adds `is_safe_fetch_url(config.url)` + `follow_redirects=
  False`; fold these three in so the `hooks.py` writer ships SSRF+redirect+async+clamp+allowlist.

## mcp/registry.py + mcp/client.py (tool dispatch gate)
- **P1** `register_tool` (registry.py L31) flat `_tools[name]=tool` → a second server advertising a
  colliding tool name silently hijacks routing. Fix: key on (server_id,name) or raise on collision.
- **P1** `MCPClient.call_tool` (client.py L54-58) checks transport exists but NEVER checks the tool
  is registered to that server → any tool name forwarded to any valid server's subprocess (registry
  gate bypassed). Fix: `tool=registry.get_tool(name); if tool is None or tool.server_id!=server_id: raise`.

## models/gateway.py (cost recording — addl)
- **P2** `_non_negative_float` (L88-93) accepts NaN/inf rates → cost NaN/inf flows to record_spend
  (inf trips budget ceiling forever). Fix: also reject `not math.isfinite(v)`.
- **P3** `call_model_with_retry` (L517-518) double-records `tracker.record_success` (already done in
  `_invoke_and_bill` L338). Fix: drop the redundant call. (record_spend itself is exactly-once — sound.)

## db/repository.py
- **P2** Unbounded scans (no LIMIT): list_all/list_by_status/list_by_work_type + status_summary/
  work_summary (full-table → Python aggregate instead of SQL GROUP BY). Fix: limit/offset + SQL agg.
- **P2** `project_id` optional (`=None`) on every tenant method, no enforcement → omitting it silently
  queries cross-tenant. Fix: required param or bake into repo ctor.
- **P2** `create(dict)` splats `TodoModel(**todo_data)` — mass-assignment (id/version/status) + no
  text size cap. Fix: field allowlist + MAX_TEXT_BYTES.

## controllers/pid.py + event_loop saturation (#42)
- **P2** PID cap applied AFTER `claim_runnable` already set rows ACTIVE (loop.py L688-708) → truncated
  rows stay ACTIVE un-dispatched until the 15-min reaper; queue grows per tick under load. Fix: pass
  cap as `claim_runnable(limit=)` BEFORE the DB write.
- **P3** `count_active` for PID input read one phase before claim → PID input one tick stale.
- **P3** per-queue `hard_cap` never enforced at dispatch (only soft_cap drives PID). Fix: hard_cap
  clamp per queue in dispatch phase. (Math/deadlock checks PASS.)

## controllers/spend_limiter.py restore (#queued in batch-5 writer)
- **P1** `restore()` (L185-200) no validation → negative cost_usd deflates window spend (cap
  evasion); future ts survives pruning. Fix: drop negative/NaN, clamp future ts. (writer ade451 in flight)

## routers/todos.py (UNAUTHENTICATED public paths)
- **P1** `api_status()` (L177-215) returns to ANY unauth caller: `db_url` (full conn string, may
  contain `user:password@host`), `config_dir`/`config_files` (host abs paths), `filestore_root`.
  Fix: strip these 4 keys from the public response; move to an auth'd /admin/status or emit
  booleans/counts only.
- **P2** `POST /api/todos` (L62-99) in _PUBLIC_PATHS → unauth unbounded DB writes (todo flood).
  Fix: require auth or rate-limit + row cap.
- **P2** `GET /api/todos` (L101-122) `repo.list_all()` no LIMIT → unauth full-table scan per
  request. Fix: limit/offset (default 100, cap 500).

## connectors/base.py + normalize.py (observability ingest)
- **P1** `_config_family`/`bundle_credentials` (normalize.py L452-493): `config["family"]` read
  verbatim (`.lower()`, no allowlist) → a poisoned family string mis-associates credential env-var
  names across backends (or injects arbitrary dict key). Fix: allowlist against AUTH_FAMILY_PREFIXES,
  else "unknown".
- **P2** `Observability.find()` (base.py L213-231) `merged.extend(source.query(spec))` no bound →
  malicious connector returns millions of records, unbounded memory + double-copy in sort. Fix:
  per-source cap + global ceiling + MAX_RECORD_BYTES on message/raw.
- **P2** `normalized_record()` (base.py L81-106) admits NaN/Inf `value`/`ts` → JSON serialize breaks
  downstream + non-deterministic sort. Fix: `math.isfinite` guard → None.

## skills/ loader+fetcher (no exec/eval found — sound there)
- **P2** `GitHubSkillSource.download_skill`/`RemoteSkillFetcher.fetch` (fetcher.py L130-150): no
  response-size cap → 500MB SKILL.md materialized via `resp.text`. Fix: `httpx.stream` + 512KB cap.
- **P3** `SkillCatalog.download_skill` (catalog.py L77) writes `{name}.md` with no `is_path_within`
  confinement (currently safe via curated-dict keys, but unguarded if extended). Fix: mirror
  RemoteSkillFetcher.install's `_safe_skill_filename`+confine check.

## daemon.py auth middleware
- **P1** Fail-OPEN default (L1030/1039): PSK unset + `GLUDD_REQUIRE_AUTH` unset → `_no_auth`, every
  non-public path served with NO credential (warning only). Forgetting one env var exposes /admin.
  Fix: fail closed unless explicit `GLUDD_NO_AUTH=1`, or treat missing PSK as startup error.
- **P2** `_is_public` (L1019) uses `path.startswith("/docs")` → matches `/docs-admin`, `/docsecret`,
  `/docs/../admin`. Fix: explicit set membership for the docs paths.
- **P3** `check_bearer_token` (auth.py L85-88) `.startswith("Bearer ")` not constant-time — minor
  prefix timing oracle (not on the secret). Fix: doc the scope or feed dummy to compare_digest.

## connectors/registry.py
- **P1** `class`/`module` config selectors (L153-163) call `importlib.import_module` on
  operator-controlled strings with NO allowlist → arbitrary code exec at registration (`"module":
  "os.system"` passes; a dotted value bypasses the weak `general_ludd.connectors.` scoping). Fix:
  enforce prefix allowlist before any import_module.
- **P2** `_build_one` (L125) never `isinstance(source, _SourceLike)`-checks after construction →
  non-conforming object stored, fails only at call time. Fix: pre-flight protocol check.
- **P3** `from_config` (L105) no bound on `len(configs)` → unbounded import/construct. Fix: cap ~256.

## routers/spend.py
- **P2** `POST /api/spend/configure` (L79-83): builds a fresh `SpendLimiter` with empty
  `_records` and replaces `app.state._spend_limiter` → any PSK-holder wipes the rolling-window
  spend history mid-window (cap-reset evasion). Auth + math are otherwise sound (clamped
  `max(0.0,...)`, `math.isfinite` guard, `gt=0.0` request validation). Fix: carry prior records
  via `new.restore(old.snapshot())`, or gate hard-reset behind a separate admin token.

## reload/worker_broadcast.py
- **P3** 401 from a worker logged as plain `success=False` (L75/L94), masking PSK/auth
  misconfig. Fix: distinct `logger.error(...PSK mismatch...)` + `error="Unauthorized"`.
