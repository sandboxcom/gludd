# Post-alpha.4 Security Wave — apply-ready specs (2026-06-24)

Verified read-only patch specs for the remaining OPEN MED/LOW items from
`ALPHA4_VERIFIED_BACKLOG_2026-06-24.md`. Each was produced against current
`src/general_ludd/` on branch `feature/alpha4-green-the-gate`. Re-pin line numbers
at apply time. These are the NEXT wave (after the alpha.4 green-the-gate set lands).

## M-4 — `db/repository.py` unbounded scans + cross-tenant + mass-assignment
- `list_all()` (~L258-279) and `status_summary()` (~L381-417): always apply a
  `DEFAULT_LIST_LIMIT` (~1000) when no explicit limit; never unbounded.
- `_resolve_pid()` (~L133-157): add `allow_cross_tenant` flag (default False) →
  raise ValueError when `project_id` is None and not explicitly admin-scoped
  (fail-closed). Add `scoped()` / `admin_unscoped()` factories.
- `_validate_create_data()` (~L160-177): reject any field not in
  `ALLOWED_TODO_CREATE_FIELDS` (whitelist, not just immutable-field block).
- Tests: default-limit cap, unscoped-raises, create-rejects-unknown-fields.

## M-7 — `connectors/base.py` find-cap + NaN/Inf sort
- `find()` (~L197/213-216): add `per_source_limit` param; cap each
  `source.query(spec)` before `merged.extend`.
- `_sort_by_ts()` (~L233-239): sanitize ts via a helper that coerces
  NaN/Inf/non-numeric → 0.0 before sorting.
- Tests: per-source-limit caps; NaN/Inf coerced and sort stable.

## SEC-4 — `events/hooks.py` webhook await + payload
- `_fire_webhook()` (~L229): make `async def`; `await loop.run_in_executor(...)`
  (~L243-255) instead of fire-and-forget so the POST is tracked.
- Emit a redacted SUMMARY (keys only) to logs, not the full payload envelope.
  (SSRF guard ~L34-38 and retry clamp ~L139 already present — leave intact.)
- Tests: executor awaited (httpx.post called); secret values absent from logs.

## SEC-5b — `secrets/manager.py` read-time revalidation
- `resolve()` (~L109-132): re-validate `alias.path` (regex
  `^[A-Za-z0-9_][A-Za-z0-9_/-]*$`, no `..`) and `alias.mount` (in
  `_PERMITTED_MOUNTS`) AFTER lookup and BEFORE the hvac call — defends against
  `_aliases` mutation post-registration.
- Test: mutate `_aliases[...].path = "../etc/passwd"` → `resolve()` raises, hvac
  never called.

## validation/runner.py — symlink confinement
- `run_validation()` (~L122-160): `resolved = os.path.realpath(worktree_path)`;
  assert it equals/nests under the allowed base before `subprocess.run`; use the
  resolved path as `cwd`. Reject symlink escapes with `CommandValidationError`.
- Tests: symlink-escape rejected; valid path accepted.

## PENDING (agents still running at write time)
- M-13 `db/models.py` project_id NOT NULL + version optimistic-lock + Text bounds
- `event_loop/loop.py` PID-cap-before-ACTIVE (~L752) + review-job timeout (~L528)
- live-z.ai 3-test failure root cause (CI skips these — non-blocker)
- MCP module presence on master (likely only on test/coverage-recovered)
- `daemon.py` prompt_registry.refresh timeout (~L846) + `/docs` prefix re-confirm
  (the `/docs_evil` bypass claim is probably FALSE — `startswith("/docs/")` fails)
