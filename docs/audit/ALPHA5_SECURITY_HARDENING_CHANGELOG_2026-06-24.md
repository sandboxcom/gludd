# Alpha.5 Security Hardening Changelog — 2026-06-24

> **NOT IN ALPHA.4.** These fixes were implemented in the alpha.4 green-the-gate
> session but were NOT part of the alpha.4 scope (first real-tagged release).
> They require their own gated release cycle: full-suite GREEN gate → explicit
> operator go → `v0.1.0-alpha.5` tag.

All fixes were written TDD (tests first) against `master` @ `10ee0d8`. Re-pin
line numbers at apply time — they drift with concurrent merges.

---

## CRITICAL

| # | Vulnerability | File | Fix |
|---|---------------|------|-----|
| C-1 | Alembic `fileConfig` raises `KeyError: 'formatters'`; migrations unrunnable | `alembic.ini` | Added standard `[loggers]/[handlers]/[formatters]` sections so `alembic upgrade head` succeeds |
| C-2 | `/api/status` leaks `db_url` (host/port/dialect) to unauthenticated callers | `routers/todos.py` | Removed `db_url` and `db_engine` from the public status payload |

---

## HIGH

### Secrets / Path-Traversal

| # | Vulnerability | File | Fix |
|---|---------------|------|-----|
| H-1 | Secret alias path validated at registration but not at read time; mutated alias could escape vault mount | `secrets/manager.py` | Re-validate `alias.path` regex + `alias.mount` in `PERMITTED_MOUNTS` inside `resolve()` before every hvac call |
| H-2 | `cosign verify` output written to caller-supplied directory with no confinement | `secrets/cosign.py` | Confine output dir to a `tempfile.mkdtemp()` subtree; reject paths that resolve outside it |
| H-3 | Cosign signing key persisted with world-readable permissions | `secrets/cosign.py` | `os.chmod(key_path, 0o600)` applied immediately after key write |
| H-4 | `gitsign` invocations did not scrub `GNUPGHOME`/`HOME` from inherited environment | `secrets/gitsign.py` | Explicit env dict passed to subprocess; `HOME`/`GNUPGHOME` removed |

### MCP Transport

| # | Vulnerability | File | Fix |
|---|---------------|------|-----|
| H-5 | MCP stderr reader (subprocess) could deadlock when stderr pipe fills and stdout isn't drained | `mcp/transport.py` | Drain stderr in a dedicated thread; never block on both pipes simultaneously |
| H-6 | MCP tool names accepted arbitrary strings; malformed names could poison the registry | `mcp/registry.py` | Validate tool names against `^[A-Za-z0-9_.-]{1,128}$` at registration; reject invalid names |
| H-7 | MCP incoming frames had no size limit; oversized frames buffered fully | `mcp/transport.py` | Hard cap of 4 MB per frame; raise `MCPFrameTooLargeError` and close the connection |
| H-8 | MCP `npx` invocations used floating package versions; supply-chain pinning absent | `mcp/transport.py` | Require explicit `@version` suffix in package spec; raise if unpinned |
| H-9 | `call_tool` dispatched to unregistered tool names without validation | `mcp/client.py` | Validate tool name exists in registry before dispatching; return structured error otherwise |

### Budget / Finance

| # | Vulnerability | File | Fix |
|---|---------------|------|-----|
| H-10 | Budget `reserve()` accepted NaN/Inf amounts, corrupting spend ledger | `controllers/budget.py` | Reject non-finite amounts with `ValueError` before any state mutation |
| H-11 | TOCTOU: `reserve()` read balance then wrote in separate steps; concurrent calls could overdraft | `controllers/budget_manager.py` | Moved reserve + reconcile into a single atomic section under an asyncio lock |
| H-12 | `spend()` and `lease()` on the event-loop budget path lacked lock protection | `models/gateway.py` | Wrapped spend/lease/ledger mutations in the existing gateway asyncio lock |

---

## MEDIUM

### Worker / Ansible Environment

| # | Vulnerability | File | Fix |
|---|---------------|------|-----|
| M-1 | Worker subprocess inherited full parent environment including secrets in env vars | `ansible/core_runner.py`, `ansible/runner.py` | Build an explicit minimal env dict; strip all vars not in an explicit allowlist before `subprocess.run` |
| M-2 | Worker workspace directories were not cleaned up on task failure paths | `worker/app.py` | Added `finally` block in task handler to remove workspace on both success and failure |

### Gateway / Model Layer

| # | Vulnerability | File | Fix |
|---|---------------|------|-----|
| M-3 | Unknown model role resolved to default profile (fail-open) | `models/gateway.py` | Pass `strict=True` to `resolve_role()`; unknown roles now raise `ValueError` |
| M-4 | `record_success` called twice on fallback success path; double-counted spend | `models/gateway.py` | Removed duplicate `record_success` call at the fallback site |
| M-5 | Gateway `asyncio.Lock` not released on certain exception paths | `models/gateway.py` | Wrapped lock acquisition in `async with` context manager throughout |
| M-6 | Gateway `asyncio.sleep()` duration unbounded when backing off on transient errors | `models/gateway.py` | Capped sleep to `MAX_BACKOFF_SECONDS = 30` |

### Routing / Scoring

| # | Vulnerability | File | Fix |
|---|---------------|------|-----|
| M-7 | Routing score cache used stale health data without expiry check | `scoring/router.py` | Added TTL check on cache read; stale entries trigger a health re-probe before scoring |
| M-8 | `D-21` budget gate skipped when budget controller not yet initialized | `controllers/budget_manager.py` | Fail-closed: reject the dispatch if the budget controller is None rather than defaulting to "allow" |

### Job / Timeout

| # | Vulnerability | File | Fix |
|---|---------------|------|-----|
| M-9 | Job schema accepted zero/negative timeout values; zero-second jobs could DoS the queue | `schemas/job.py` | `timeout_seconds` field validated `> 0`; `Field(ge=1)` constraint added |
| M-10 | Review job dispatched `asyncio.to_thread(run_playbook)` with no timeout guard | (event_loop) `daemon.py` | Wrapped `asyncio.to_thread` call with `asyncio.wait_for(timeout=JOB_TIMEOUT_SECONDS)` |

### Circuit Breaker / Probe

| # | Vulnerability | File | Fix |
|---|---------------|------|-----|
| M-11 | Circuit-breaker probe calls counted toward downstream service error budget | `models/timeout_detector.py` | Probe requests tagged with `is_probe=True`; excluded from error-rate accounting |

### Self-Update

| # | Vulnerability | File | Fix |
|---|---------------|------|-----|
| M-12 | Self-update applier did not enforce CI-protected path restrictions before write | `self_update/applier.py` | Added `PROTECTED_PATHS` check; applier raises `ApplyError` if target is in the denied set |

### Integrity Scanner

| # | Vulnerability | File | Fix |
|---|---------------|------|-----|
| M-13 | Integrity scanner skipped files matching `.gitignore` patterns, creating blind spots | `integrity/scanner.py` | Scanner now explicitly enumerates all files regardless of git ignore status |
| M-14 | HMAC key loaded from env without validation; empty-string key accepted silently | `integrity/scanner.py` | Raise `ConfigurationError` if `INTEGRITY_HMAC_KEY` is absent or shorter than 32 bytes |
| M-15 | Integrity router returned unsigned hashes; no signed-hash endpoint existed | `routers/integrity.py` | Added `/integrity/signed-hash` endpoint that returns HMAC-signed digest |

### Deployment / Infra

| # | Vulnerability | File | Fix |
|---|---------------|------|-----|
| M-16 | Deploy `env` dict passed unsanitized to subprocess; could inject shell metacharacters | `infra/deployment.py` | Validate each env key/value against `[A-Za-z0-9_]` allowlist before subprocess exec |
| M-17 | Terraform plan executed with no working-directory confinement; symlinks could escape | `infra/terraform.py` | Resolved working dir with `os.path.realpath()`; assert it nests under allowed base |
| M-18 | Terraform module sources allowed arbitrary remote URLs (potential RCE via malicious module) | `infra/terraform.py` | Added module-source allowlist; reject non-allowlisted registry or git URLs |
| M-19 | `infra/compute.py` auto-select wrote resource picks before budget reservation confirmed | `infra/compute.py` | Reordered: budget reservation committed atomically before resource registration |

### OpenBao / Vault

| # | Vulnerability | File | Fix |
|---|---------------|------|-----|
| M-20 | OpenBao client logged raw token value in debug output | `secrets/config.py` | Redact token to `***` in all log calls; only log token length |

### Routers — Rate / Size Caps

| # | Vulnerability | File | Fix |
|---|---------------|------|-----|
| M-21 | Issues-poll endpoint had no per-call result bound; unbounded DB scan possible | `routers/maintenance.py` | Added `limit` cap (default 100, max 500); query now always passes `LIMIT` |
| M-22 | Schedule router accepted negative/zero recurrence intervals | `routers/schedule.py` | Validate `interval_seconds >= 1` at the schema level; reject at validation |
| M-23 | Filestore write endpoint accepted arbitrarily large payloads | `routers/filestore.py` | Added `MAX_WRITE_BYTES = 10 MB` check; reject oversized body with HTTP 413 |

### Skills

| # | Vulnerability | File | Fix |
|---|---------------|------|-----|
| M-24 | `skills/fetcher.py` buffered full HTTP response body before size check | `skills/fetcher.py` | Stream response with `httpx.stream()`; abort and raise after reading > 1 MB |

### Daemon

| # | Vulnerability | File | Fix |
|---|---------------|------|-----|
| M-25 | Daemon started in degraded mode (missing health_tracker) without surfacing the condition | `daemon.py` | Added explicit degraded-mode detection in lifespan; sets `app.state.degraded = True` and logs `CRITICAL` |

---

## LOW

| # | Vulnerability | File | Fix |
|---|---------------|------|-----|
| L-1 | `timeout_detector.py` circuit-breaker state is per-process; undocumented single-worker assumption | `models/timeout_detector.py` | Added docstring and `AssertionError` guard if invoked under detected multi-worker gunicorn |

---

## Testing Coverage

Every fix above has a corresponding unit test in `tests/unit/` committed alongside
the implementation. New test files introduced this session:

- `test_secret_path_traversal.py`
- `test_mcp_transport_stderr.py`
- `test_mcp_registry_tool_name_validation.py`
- `test_mcp_transport_client_fixes.py`
- `test_budget_guards.py`
- `test_ansible_env_scrub.py`
- `test_job_timeout_validation.py`
- `test_signing_output_dir_confinement.py`
- `test_scoring_router_cache_health.py`
- `test_self_update_applier.py` (extended)
- `test_integrity.py` (extended)
- `test_model_gateway.py` (extended)
- `test_timeout_detector.py` (extended)
- `test_worker_workspace_cleanup.py` (extended)
- `test_maintenance_issues_poll_bound.py`
- `test_schedule_bounds.py`
- `test_filestore_write_size_cap.py`
- `test_fetcher_size_cap.py`

---

## Release Gate Checklist

Before cutting `v0.1.0-alpha.5`:

- [ ] `make gate-background` → `make gate-bg-check` shows 0 failures, 0 errors
- [ ] `make test-count` shows no collection errors
- [ ] `make typecheck` shows 0 mypy errors
- [ ] `make lint` passes
- [ ] Explicit operator go-ahead for the tag push
- [ ] `git tag v0.1.0-alpha.5` → push → CI "Build and Release" run GREEN confirmed
