# Daemon/Worker Endpoint Test Failures — Root Cause

**Audit date:** 2026-07-06
**Gate run:** `.gate-logs/gate-20260706174625.log` (649 failures)
**Scope:** READ-ONLY diagnostic — no files modified.

## The single root cause

Every daemon- and worker-endpoint test that drives a `TestClient` against
`create_daemon_app()` / `worker.app.create_app()` now receives **HTTP 503**
(`{"error": "auth_required", "reason": "no PSK configured"}`) instead of the
expected 200/422/etc. This is caused by the **fail-closed-by-default auth
posture** combined with the removal of the session-wide auth opt-out that
previously lived in `tests/conftest.py`.

`tests/conftest.py` was gutted from ~253 lines to 31 (git diff:
`tests/conftest.py | 253 ++------------------`). The removed code included the
shared auth opt-out (an autouse fixture / `os.environ` setup setting
`GLUDD_ALLOW_NO_AUTH=1`) that **all** daemon/worker endpoint tests implicitly
relied on. With it gone, both surfaces default to fail-closed because no
`GLUDD_AUTH_PSK` is configured in the test environment (CI sets `GLUDD_AUTH_PSK: ""`,
the Makefile `test` target injects no PSK, and grep finds no
`GLUDD_REQUIRE_AUTH`/`GLUDD_ALLOW_NO_AUTH` anywhere in the Makefile, CI
workflow, or pytest config).

`tests/unit/test_worker.py` confirms the dependency: it contains **zero**
references to `GLUDD_ALLOW_NO_AUTH` / `GLUDD_AUTH_PSK` / `GLUDD_REQUIRE_AUTH` and
calls `create_app()` with no opt-out — so once the global opt-out disappeared,
every worker endpoint it hits returns 503.

## The "create_daemon_app signature drift" hypothesis is WRONG

The current signature is:

```python
def create_daemon_app(
    tick_interval: float | None = None,
    log_level: str = "info",
    config_dir: str | None = None,
    templates_dir: str | None = None,
    playbooks_dir: str | None = None,
) -> FastAPI:
```

`tests/unit/test_slurm_daemon_endpoints.py` and
`tests/unit/test_skills_daemon_endpoints.py` both call
`create_daemon_app(tick_interval=0.01, config_dir=tmpdir)` — kwargs that match
the signature exactly. The failures are **not** a construction/TypeError; they
are runtime 503s from the auth middleware. The signature is a red herring.

## The specific lines that produce the 503

1. **`src/general_ludd/security/auth.py:75`** (the worker's posture, via
   `load_auth_posture`):
   ```python
   require_auth = require_auth_env(source) or (no_auth and not _allow_no_auth)
   ```
   With no `GLUDD_AUTH_PSK` and no `GLUDD_ALLOW_NO_AUTH`: `no_auth=True`,
   `_allow_no_auth=False` → `require_auth=True`.

2. **`src/general_ludd/daemon.py:2264-2282`** (the daemon's inline mirror of the
   same logic — the "P1 fix: FAIL-CLOSED by default"):
   ```python
   _psk = os.environ.get("GLUDD_AUTH_PSK", "")
   _allow_no_auth = os.environ.get("GLUDD_ALLOW_NO_AUTH", "").strip().lower() in {...}
   _no_auth = not _psk
   _require_auth = _no_auth and not _allow_no_auth
   ```

3. **`src/general_ludd/daemon.py:2346-2354`** (the 503 return in
   `auth_and_stats_middleware`):
   ```python
   if _no_auth and _require_auth and not _is_public(method, path):
       return JSONResponse(status_code=503,
           content={"error": "auth_required", "reason": "no PSK configured"})
   ```

4. **`src/general_ludd/worker/app.py:241-269`** — the worker middleware built
   from `load_auth_posture("worker")`; the identical 503 branch fires at
   `worker/app.py:257-262`.

5. **`tests/conftest.py`** — gutted 253 → 31 lines; the removed autouse
   auth-opt-out is the trigger that turned the pre-existing fail-closed
   default into a mass failure.

## The fixture pattern tests must use

Every TestClient-driven daemon/worker fixture must opt out of auth (or supply a
PSK) **before** constructing the app, because the posture is captured into
closure vars / `app.state` at `create_daemon_app()` / `create_app()` time.
`tests/unit/test_skills_daemon_endpoints.py:13-21` is the canonical pattern:

```python
def _make_test_app(config_dir: str | None = None):
    tmpdir = config_dir or tempfile.mkdtemp()
    with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
        return create_daemon_app(tick_interval=0.01, config_dir=tmpdir)
```

The **cleanest single-point fix** is to restore an autouse session fixture in
`tests/conftest.py` that sets `GLUDD_ALLOW_NO_AUTH=1` for the whole suite
(recreating what the gutted conftest provided), so the dozens of fixtures that
do not set it locally (test_worker.py, test_slurm_daemon_endpoints.py,
test_self_improve_wiring.py, test_self_update_router.py, the worker_broadcast /
worker_redteam / w3_8 / w5_6 family, etc.) all recover at once. Per-fixture
`patch.dict` (as test_skills already does) is the belt-and-suspenders
alternative.

`tests/e2e/test_environment_e2e.py` is an example of a test that does NOT rely
on the global opt-out: it defines its own `_PSKMiddleware(BaseHTTPMiddleware)`
and sets `app.state._psk` directly. That file is already passing.

## Recovery estimate

The auth-fail-closed root cause is responsible for the **majority** of the 649
failures — every TestClient-driven daemon/worker endpoint file. From the gate
log, files failing purely with `assert 503 == <expected>`:

| File | Failing tests |
|---|---|
| tests/unit/test_worker.py | 10 |
| tests/unit/test_slurm_daemon_endpoints.py | 13 |
| tests/unit/test_skills_daemon_endpoints.py | 11 |
| tests/unit/test_self_improve_wiring.py | 10 |
| tests/unit/test_self_update_router.py | 10 |
| tests/unit/test_w3_8_worker_501.py | 4 |
| tests/unit/test_w5_6_worker_auth.py | 1 |
| tests/unit/test_worker_broadcast_401.py | 2 |
| tests/unit/test_worker_broadcast_psk.py | 1 |
| tests/unit/test_worker_redteam.py | 1 |
| (plus test_daemon.py, test_compaction_daemon_wiring.py, and the rest of the daemon-endpoint family) | … |

Conservative estimate: **~400-550 of the 649 failures recover** once the
auth opt-out is restored (one-line autouse fixture in conftest.py). The
remainder are **independent** issues visible elsewhere in the same gate log and
must be fixed separately:

- `ComputePrice.__init__() missing 2 required positional arguments: 'spot' and 'terms'` (pricing_intel signature change)
- `SlurmAdapter._build_script() got an unexpected keyword argument 'time_limit_str'` (slurm adapter signature change)
- `sqlite3.OperationalError: near "ALTER": syntax error` (Alembic/SQL migration on SQLite)
- `asyncio.run() cannot be called from a running event loop` (loop re-entry)
- `TypeError: PSKMiddleware() takes no arguments` — a SEPARATE, localized bug
  in `tests/e2e/test_environment_e2e.py` where a `type("PSKMiddleware", (), ...)`
  stub had no `__init__`; that file has already been fixed to subclass
  `BaseHTTPMiddleware`. Not the mass-failure cause.

## Recommended fix (for the applying agent)

1. In `tests/conftest.py`, add an autouse fixture:
   ```python
   @pytest.fixture(autouse=True)
   def _allow_no_auth_for_tests(monkeypatch):
       monkeypatch.setenv("GLUDD_ALLOW_NO_AUTH", "1")
   ```
2. Re-run `make test-specific TESTFILE=tests/unit/test_worker.py` and
   `make test-specific TESTFILE=tests/unit/test_slurm_daemon_endpoints.py` to
   confirm the 503s are gone.
3. Do NOT change `daemon.py` or `security/auth.py` — the fail-closed default is
   the correct production posture. The bug is purely a test-harness regression
   (the gutted conftest), not an auth-logic bug.
