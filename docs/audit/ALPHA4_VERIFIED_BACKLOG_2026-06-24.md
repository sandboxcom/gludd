# Alpha.4 Verified Backlog — 2026-06-24

Live verification pass against **current master** (`10ee0d8`). Each prior "OPEN"
finding from the stale docs (`NEW_FINDINGS_2026-06-16.md`,
`POST_SHIP_BACKLOG_PREP_2026-06-21.md`, completion-integrity audit) was re-checked
against the actual `src/general_ludd/` tree. **Most were already fixed** — the
docs were derived from an out-of-date snapshot. This file is the apply-ready
ground truth; re-pin line numbers at apply time (they drift).

## Release reality (verify before any ship)
- **alpha.3 was never actually shipped.** Only `alpha.1` git tags exist. CI
  release triggers on `tags: ["v*"]`; no `v0.1.0-alpha.3` tag was ever created,
  so no GitHub Release was built. The version bump + CHANGELOG + commit message
  `…release-alpha3-shipped` are doc-claims, not facts.
- **"alpha.4" is undefined** anywhere in the repo (no milestone/scope/schedule).
- Fast gates GREEN on master: mypy 0, ruff clean, 12,798 tests collected, no
  collection errors. Full-suite pass was being confirmed by a background gate at
  write time (some `F`s visible mid-run — final result TBD).

## Genuinely OPEN (verified present on master)

### HIGH
- **ALEMBIC MIGRATIONS UNRUNNABLE** — `alembic/env.py:13` `fileConfig(config.config_file_name)`
  raises `KeyError: 'formatters'` because `alembic.ini` lacks the
  `[loggers]/[handlers]/[formatters]` logging sections. `make alembic-check`
  (and any real `alembic upgrade head`) fails on bootstrap. Fix: add the standard
  alembic logging sections to `alembic.ini` (or guard `fileConfig` in `env.py`).
  Then re-run parity (18 ORM tables in `db/models.py` vs migration chain coverage).
- **SEC-8** `routers/todos.py:207-230` — `api_status()` returns `db_url`
  (host/port/dialect even with `hide_password=True`) and `db_engine` to ANY
  unauthenticated caller (`/api/status` is public). Fix: drop `db_url`/`db_engine`
  from the public payload, or gate the endpoint to authenticated callers.

### MED
- **M-3** `gateway.py:~899` `call_model_by_role` — unknown role fails *open*:
  `resolve_role(role, strict=False)` (`models/router.py:20-48`) returns the
  default profile for an unrecognized role. Fix: pass `strict=True` so unknown
  roles are rejected.
- **gateway.py:~839** — `record_success` called twice on the fallback success
  path (`_invoke_and_bill` already records at `:534`). Double success accounting.
  Fix: remove the `:839` call.
- **SEC-4** `events/hooks.py:~241-256` — webhook `loop.run_in_executor(...)` is
  not awaited (detached fire-and-forget HTTP POST); payload redaction only strips
  secret-pattern keys, not the envelope. SSRF guard (`:34-38`) and retry clamp
  (`:139`) ARE present. Fix: await the executor result; emit a redacted summary.
- **M-7** `connectors/base.py:231-238` — `find()` does an unbounded `merged.extend`
  with no per-source cap; sort key admits NaN/Inf. Fix: per-source result cap +
  numeric sanitation before sort.
- **M-4** `db/repository.py:891-894` — `list_all()` has no default `LIMIT` and
  `project_id` optional → unbounded cross-tenant scans when `_pid=None`. Also
  freeze `create()` allowed-fields (mass-assignment).
- **M-13** `db/models.py:74-76,109` — `project_id` nullable (orphan/cross-tenant
  risk); `TodoModel.version` declared but not enforced as an optimistic lock on
  concurrent create.
- **validation/runner.py:122-160** — `subprocess.run(cwd=worktree_path)`; path is
  validated but not symlink-confined post-validation (escape via symlink → `..`).
- **event_loop/loop.py:752-773** — PID/concurrency cap applied AFTER rows are
  marked ACTIVE in DB → queue grows past cap (rows stay ACTIVE on restart). Fix:
  cap inside `claim_runnable()` query before marking ACTIVE.
- **event_loop/loop.py:528-561** — `_dispatch_review_job()` runs a blocking
  `asyncio.to_thread(run_playbook)` with no timeout on the async loop.
- **skills/fetcher.py:128-156** — `httpx.get` has no response-size cap; a 500MB
  body is fully buffered before parse. Fix: stream + size check (~1MB).
- **daemon.py:~846** — `prompt_registry.refresh()` called synchronously in
  lifespan startup; no timeout; blocks if templates dir is slow.

### LOW
- **SEC-5b** `secrets/manager.py:97-121` — alias path validated at registration
  but not re-validated at read time (defensive hardening; private dict, low risk).
- **alembic.ini** — hardcoded `sqlite:///./test.db` URL + missing logging sections
  (ties to the HIGH alembic item).
- **M-5** `daemon.py:1489` `/docs` prefix check — the agent's repro (`/docs_evil`
  bypass) is **likely wrong**: `"/docs_evil".startswith("/docs/")` is False.
  Re-confirm; probably NOT vulnerable.
- **M-10** `timeout_detector.py:209-230` — circuit-breaker state is per-process
  (`RLock`); under gunicorn multi-worker it isn't shared. Doc the single-worker
  assumption or back it with a shared store.

## ALREADY FIXED — do NOT redo
SEC-1 (registry sealed, `agents/registry.py:20-137`), SEC-2 (daemon uses
`default_registry()` `daemon.py:1050`), dispatcher race (asyncio.Lock,
`agents/dispatcher.py:44`), UNRESTRICTED_ROLE (`object()` sentinel,
`dispatch/dynamic_dispatcher.py:39`), SEC-3 (health-gate + budget threaded,
`gateway.py:936-1022`), circuit-breaker bypass (`gateway.py:663-671`), SEC-5a
(logs `type(exc).__name__`), SEC-6 (`field(repr=False)` on cosign key), SEC-7
(import allowlist, `connectors/registry.py:182`), SEC-14 (family allowlist,
`normalize.py:486`), SEC-9 (auth fail-closed default, `daemon.py:1440-1443`),
SEC-11 (workspace cleanup `worker/app.py:315`), SEC-12 (spend `restore()`
validation), M-8 (retry clamp `events/hooks.py:139`), M-9 (spend history
carryover), self_update applier confinement (`:105-140`), routers/integrity
confinement (`:44-76`), auth.py constant-time compare, worker_broadcast 401.

## INCONCLUSIVE / not on master
- **MCP** (`mcp/registry.py`, `client.py`, `transport.py`, `tool_loop.py`) — not
  found on master. Per memory the MCP tool-call loop work lives on
  `test/coverage-recovered` (uncommitted). Confirm whether MCP is meant to land
  on master for alpha.4.
- **INERT features** (pricing_intel source stubs, `collection_handler=None`,
  scoring cache/quant penalty) — not re-verified (agent bailed). Known
  low-priority, non-blocking.
- **TASK#8 cheap wins** (unused langchain/langgraph deps in pyproject, gate
  missing `--cov`, W5.3-CVE tick) — not verified.

## Proposed alpha.4 = first ACTUALLY-shipped release
1. Confirm full gate GREEN on master for the exact SHA.
2. Land the clean, isolated fixes: alembic.ini logging sections, SEC-8 db_url
   leak, M-3 strict role, gateway double-record, skills fetcher size cap.
   (TDD: test first, gated commit.)
3. Re-confirm gate GREEN.
4. Cut a real `v0.1.0-alpha.4` tag → CI builds the GitHub Release. **The public
   tag-push is the one irreversible step — hold for explicit operator go.**
