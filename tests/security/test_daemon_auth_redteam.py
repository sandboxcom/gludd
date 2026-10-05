"""Red-team regression tests for daemon auth (findings A-1, A-2, A-3, P1).

A read-only red-team found three daemon-auth weaknesses:

  A-1  PSK compared with `token != _psk` — a non-constant-time comparison
       leaks the secret one byte at a time via a timing side channel.
       FIX: hmac.compare_digest.

  A-2  A debug log printed `_psk[:4]` — the full PSK when it is <= 4 chars,
       and a 4-char crib otherwise. FIX: never log any portion of the PSK;
       log only a boolean `psk_configured`.

  A-3  When GLUDD_AUTH_PSK is unset/empty, ALL auth is skipped silently and the
       entire /admin surface is open.
       FIX (P1): FAIL-CLOSED by default — no PSK → 503 on non-public paths.
       GLUDD_ALLOW_NO_AUTH=1 is the explicit dev/test opt-out that restores
       the open posture (with a LOUD warning).
       GLUDD_REQUIRE_AUTH=1 is kept for back-compat; it also forces fail-closed
       and overrides GLUDD_ALLOW_NO_AUTH.

NOTE: the test suite conftest sets GLUDD_ALLOW_NO_AUTH=1 globally so that
the ~100 existing no-PSK tests continue to exercise daemon logic rather than
middleware rejection. Tests below that verify fail-closed behaviour must
explicitly unset GLUDD_ALLOW_NO_AUTH before creating the app.
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

import general_ludd.daemon as daemon_mod
import general_ludd.security.auth as auth_mod
from general_ludd.daemon import create_daemon_app

_PSK = "redteam-psk-supersecret-value"
_DAEMON_SRC = Path(inspect.getfile(daemon_mod)).read_text()
# A-1's constant-time compare now lives in the SHARED security.auth helper so
# the daemon and worker auth surfaces cannot drift; assert against it too.
_AUTH_SRC = Path(inspect.getfile(auth_mod)).read_text()


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# --- A-1: constant-time comparison ------------------------------------------

class TestA1ConstantTime:
    def test_source_uses_hmac_compare_digest(self):
        # The compare now lives in the shared security.auth helper (used by both
        # the daemon and the worker), and the daemon calls it via
        # check_bearer_token.
        assert "hmac.compare_digest" in _AUTH_SRC, (
            "A-1: PSK auth must use hmac.compare_digest for a constant-time "
            "comparison; a plain `token != _psk` leaks the secret via timing."
        )
        assert "check_bearer_token" in _DAEMON_SRC, (
            "A-1: the daemon must route its PSK check through the shared "
            "constant-time check_bearer_token helper."
        )

    def test_source_has_no_nonconstant_psk_compare(self):
        assert "token != _psk" not in _DAEMON_SRC, (
            "A-1: the non-constant-time `token != _psk` comparison must be gone."
        )

    @pytest.mark.asyncio
    async def test_wrong_token_still_rejected(self):
        with patch.dict(os.environ, {"GLUDD_AUTH_PSK": _PSK}):
            app = create_daemon_app(tick_interval=0.01)
        async with _client(app) as c:
            resp = await c.get(
                "/admin/anything",
                headers={"Authorization": "Bearer wrong-token"},
            )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_correct_token_authorized(self):
        with patch.dict(os.environ, {"GLUDD_AUTH_PSK": _PSK}):
            app = create_daemon_app(tick_interval=0.01)
        async with _client(app) as c:
            resp = await c.get(
                "/admin/does-not-exist",
                headers={"Authorization": f"Bearer {_PSK}"},
            )
        # Auth passed -> handler runs -> 404 (route missing), NOT 401.
        assert resp.status_code != 401


# --- A-2: no PSK material in logs -------------------------------------------

class TestA2NoPskInLogs:
    def test_source_does_not_slice_psk(self):
        assert "_psk[:4]" not in _DAEMON_SRC, (
            "A-2: the daemon must never log any slice of the PSK; `_psk[:4]` "
            "leaks the whole key when it is <= 4 chars."
        )

    def test_source_logs_psk_configured_boolean(self):
        assert "psk_configured" in _DAEMON_SRC, (
            "A-2: the auth debug log must report only a boolean psk_configured."
        )


# --- A-3 / P1: fail-closed by default, GLUDD_ALLOW_NO_AUTH=1 opt-out ------

class TestA3NoAuthDegraded:
    @pytest.mark.asyncio
    async def test_healthz_reports_no_auth_when_psk_unset_allow_no_auth(self):
        """With GLUDD_ALLOW_NO_AUTH=1 (dev opt-out), healthz reports auth_degraded."""
        env = dict(os.environ)
        env.pop("GLUDD_AUTH_PSK", None)
        env.pop("GLUDD_REQUIRE_AUTH", None)
        env["GLUDD_ALLOW_NO_AUTH"] = "1"
        with patch.dict(os.environ, env, clear=True):
            app = create_daemon_app(tick_interval=0.01)
        async with _client(app) as c:
            resp = await c.get("/healthz")
        body = resp.json()
        assert resp.status_code == 200
        # Liveness status stays "healthy" for back-compat; the security posture
        # rides on the no_auth / auth_degraded flags.
        assert body.get("no_auth") is True
        # auth_degraded = no PSK AND opted-out of fail-closed (open dev mode).
        assert body.get("auth_degraded") is True

    @pytest.mark.asyncio
    async def test_healthz_not_degraded_when_psk_set(self):
        with patch.dict(os.environ, {"GLUDD_AUTH_PSK": _PSK}):
            app = create_daemon_app(tick_interval=0.01)
        async with _client(app) as c:
            resp = await c.get("/healthz")
        body = resp.json()
        assert body.get("no_auth") is False
        assert body.get("auth_degraded") is False

    def test_no_psk_logs_loud_warning(self, caplog):
        """A loud warning fires whether fail-closed or dev-open; PSK absence is always warned."""
        import logging

        env = dict(os.environ)
        env.pop("GLUDD_AUTH_PSK", None)
        env.pop("GLUDD_REQUIRE_AUTH", None)
        env.pop("GLUDD_ALLOW_NO_AUTH", None)
        records: list[logging.LogRecord] = []

        class _LocalCapture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        # Attach a handler directly to the daemon logger so the warning is
        # captured regardless of root-logger state.
        daemon_logger = logging.getLogger("general_ludd.daemon")
        # xdist-order pollution: alembic/env.py's fileConfig(alembic.ini) (run by
        # ANY test that exercises the real daemon lifespan against SQLite, e.g.
        # `with TestClient(app) as client:`) defaults to disable_existing_loggers
        # =True. alembic.ini's [loggers] only lists root/sqlalchemy/alembic, so
        # this logger — already created at collection time — gets `.disabled =
        # True` for the rest of the xdist worker process. Neither conftest's
        # `_isolate_root_logger` (snapshots level/propagate/handlers only, not
        # `.disabled`) nor pytest's own caplog machinery ever restores it, so a
        # bare `propagate = True` is powerless — `Logger.isEnabledFor()`
        # short-circuits on `.disabled` before it ever looks at level/propagate.
        # Force it back on here so this test passes regardless of what ran
        # before it on this worker (see test_worker_broadcast_401.py for the
        # first instance of this fix).
        daemon_logger.disabled = False
        daemon_logger.propagate = True
        # Python 3.10+ caches Logger.isEnabledFor() results per-logger. A prior
        # TestClient test on the same xdist worker can mutate the ROOT logger's
        # level (e.g. via /admin/log-level or pytest's caplog), which causes
        # `isEnabledFor(WARNING)` on the daemon logger to cache `False`. Setting
        # the level explicitly clears this stale cache so the warning is emitted.
        daemon_logger.setLevel(logging.WARNING)
        capture = _LocalCapture(level=logging.WARNING)
        daemon_logger.addHandler(capture)
        try:
            with patch.dict(os.environ, env, clear=True):
                create_daemon_app(tick_interval=0.01)
        finally:
            daemon_logger.removeHandler(capture)
        joined = " ".join(r.getMessage() for r in records).lower()
        assert "gludd_psk" in joined and (
            "refuse" in joined or "fail-closed" in joined or "fail_closed" in joined
        ), "P1: a LOUD startup WARNING must fire when GLUDD_AUTH_PSK is unset (fail-closed mode)."

    def test_no_psk_allow_no_auth_logs_loud_warning(self, caplog):
        """With GLUDD_ALLOW_NO_AUTH=1, a loud warning fires that auth is disabled."""
        import logging

        env = dict(os.environ)
        env.pop("GLUDD_AUTH_PSK", None)
        env.pop("GLUDD_REQUIRE_AUTH", None)
        env["GLUDD_ALLOW_NO_AUTH"] = "1"
        records: list[logging.LogRecord] = []

        class _LocalCapture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        # See note above: attach handler directly + clear stale isEnabledFor cache
        # + force .disabled=False (alembic fileConfig xdist-order pollution).
        daemon_logger = logging.getLogger("general_ludd.daemon")
        daemon_logger.disabled = False
        daemon_logger.propagate = True
        daemon_logger.setLevel(logging.WARNING)
        capture = _LocalCapture(level=logging.WARNING)
        daemon_logger.addHandler(capture)
        try:
            with patch.dict(os.environ, env, clear=True):
                create_daemon_app(tick_interval=0.01)
        finally:
            daemon_logger.removeHandler(capture)
        joined = " ".join(r.getMessage() for r in records).lower()
        assert "gludd_psk" in joined and (
            "disabled" in joined or "no_auth" in joined or "open" in joined
        ), "P1: a LOUD startup WARNING must fire when GLUDD_ALLOW_NO_AUTH=1 and no PSK."

    @pytest.mark.asyncio
    async def test_default_no_psk_fails_closed(self, monkeypatch):
        """P1: default posture (no PSK, no opt-out) is FAIL-CLOSED → 503."""
        monkeypatch.delenv("GLUDD_AUTH_PSK", raising=False)
        monkeypatch.delenv("GLUDD_REQUIRE_AUTH", raising=False)
        monkeypatch.delenv("GLUDD_ALLOW_NO_AUTH", raising=False)
        app = create_daemon_app(tick_interval=0.01)
        async with _client(app) as c:
            resp = await c.get("/admin/does-not-exist")
        assert resp.status_code == 503, (
            "P1: no PSK + no GLUDD_ALLOW_NO_AUTH must refuse non-public paths (503)."
        )

    @pytest.mark.asyncio
    async def test_allow_no_auth_opt_out_grants_access(self, monkeypatch):
        """P1: GLUDD_ALLOW_NO_AUTH=1 opt-out keeps admin open (dev mode)."""
        monkeypatch.delenv("GLUDD_AUTH_PSK", raising=False)
        monkeypatch.delenv("GLUDD_REQUIRE_AUTH", raising=False)
        monkeypatch.setenv("GLUDD_ALLOW_NO_AUTH", "1")
        app = create_daemon_app(tick_interval=0.01)
        async with _client(app) as c:
            resp = await c.get("/admin/does-not-exist")
        # No PSK + allow_no_auth: handler-level 404 (not 503, not 401).
        assert resp.status_code == 404, (
            "P1: GLUDD_ALLOW_NO_AUTH=1 must allow unauthenticated access to admin paths."
        )

    @pytest.mark.asyncio
    async def test_require_auth_without_psk_fails_closed(self):
        env = dict(os.environ)
        env.pop("GLUDD_AUTH_PSK", None)
        env["GLUDD_REQUIRE_AUTH"] = "1"
        env.pop("GLUDD_ALLOW_NO_AUTH", None)
        with patch.dict(os.environ, env, clear=True):
            app = create_daemon_app(tick_interval=0.01)
        async with _client(app) as c:
            resp = await c.get("/admin/anything")
        assert resp.status_code == 503, (
            "A-3: GLUDD_REQUIRE_AUTH=1 with no PSK must refuse non-public paths."
        )

    @pytest.mark.asyncio
    async def test_require_auth_overrides_allow_no_auth(self, monkeypatch):
        """GLUDD_REQUIRE_AUTH=1 forces fail-closed even when GLUDD_ALLOW_NO_AUTH=1."""
        monkeypatch.delenv("GLUDD_AUTH_PSK", raising=False)
        monkeypatch.setenv("GLUDD_REQUIRE_AUTH", "1")
        monkeypatch.setenv("GLUDD_ALLOW_NO_AUTH", "1")
        app = create_daemon_app(tick_interval=0.01)
        async with _client(app) as c:
            resp = await c.get("/admin/anything")
        assert resp.status_code == 503, (
            "GLUDD_REQUIRE_AUTH=1 must override GLUDD_ALLOW_NO_AUTH=1 — fail-closed wins."
        )

    @pytest.mark.asyncio
    async def test_require_auth_keeps_public_paths_open(self):
        env = dict(os.environ)
        env.pop("GLUDD_AUTH_PSK", None)
        env["GLUDD_REQUIRE_AUTH"] = "1"
        env.pop("GLUDD_ALLOW_NO_AUTH", None)
        with patch.dict(os.environ, env, clear=True):
            app = create_daemon_app(tick_interval=0.01)
        async with _client(app) as c:
            resp = await c.get("/healthz")
        assert resp.status_code == 200


# --- gateway-health-budget P1: no numeric spend/budget leak on /healthz -----

class TestHealthzBudgetLeak:
    """/healthz is an UNAUTHENTICATED public path. It may surface a coarse
    `budget_exhausted` boolean, but it must NEVER leak the numeric spend/budget
    figures (daily_spend, daily_limit, daily_pct, per_todo_limit) that
    BudgetManager.get_status() returns — those reveal the operator's spend
    posture and remaining headroom to any anonymous caller.
    """

    @staticmethod
    def _app_with_spend(*, paused: bool):
        """Daemon app with a BudgetManager carrying real, distinctive spend
        numbers injected onto app.state (the lifespan wires this in prod; we
        inject directly so the absence assertion is meaningful without a DB).
        """
        from general_ludd.controllers.budget_manager import BudgetManager

        env = dict(os.environ)
        env.pop("GLUDD_AUTH_PSK", None)
        env.pop("GLUDD_REQUIRE_AUTH", None)
        with patch.dict(os.environ, env, clear=True):
            app = create_daemon_app(tick_interval=0.01)
        bm = BudgetManager(daily_limit_usd=137.50, per_todo_limit_usd=9.25)
        bm.record_spend("todo-x", 42.75)
        if paused:
            # Trip the kill switch so budget_exhausted is True.
            bm._paused = True
        app.state._budget_manager = bm
        return app

    @staticmethod
    def _numeric_leaf_values(obj):
        """Yield every int/float leaf in a nested JSON structure (excluding
        bools, which are a coarse, intentionally-public signal)."""
        if isinstance(obj, bool):
            return
        if isinstance(obj, (int, float)):
            yield obj
        elif isinstance(obj, dict):
            for v in obj.values():
                yield from TestHealthzBudgetLeak._numeric_leaf_values(v)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                yield from TestHealthzBudgetLeak._numeric_leaf_values(v)

    @pytest.mark.asyncio
    async def test_healthz_exposes_no_numeric_budget_values(self):
        app = self._app_with_spend(paused=False)
        async with _client(app) as c:
            resp = await c.get("/healthz")
        assert resp.status_code == 200
        body = resp.json()
        # The whole numeric-bearing `budget` dict must be gone entirely.
        assert "budget" not in body, (
            "gateway-health-budget P1: the numeric BudgetManager.get_status() "
            "dict must NOT be embedded in the unauthenticated /healthz response."
        )
        # No spend/budget numeric figures may appear anywhere in the body.
        for forbidden in ("daily_spend", "daily_limit", "daily_pct", "per_todo_limit"):
            assert forbidden not in body, (
                f"gateway-health-budget P1: '{forbidden}' must not be exposed "
                "on the unauthenticated /healthz endpoint."
            )
        numerics = list(self._numeric_leaf_values(body))
        # The known-injected spend figures must not surface.
        assert 42.75 not in numerics
        assert 137.50 not in numerics
        assert 9.25 not in numerics
        # And there must be NO numeric leaves at all — only string status fields
        # and boolean posture flags are public.
        assert numerics == [], (
            f"gateway-health-budget P1: /healthz leaked numeric value(s) "
            f"{numerics} to an unauthenticated caller; only string status and "
            "boolean flags are permitted."
        )

    @pytest.mark.asyncio
    async def test_healthz_keeps_coarse_budget_exhausted_boolean(self):
        # Coarse boolean stays public and tracks the kill-switch state.
        app = self._app_with_spend(paused=True)
        async with _client(app) as c:
            resp = await c.get("/healthz")
        body = resp.json()
        assert resp.status_code == 200
        assert body.get("budget_exhausted") is True, (
            "the coarse `budget_exhausted` boolean must remain public so probes "
            "can observe the kill-switch without seeing the numbers."
        )

    def test_source_does_not_embed_budget_status_in_healthz(self):
        # Regression guard: the `"budget": budget_status` line that leaked the
        # numeric dict must not return to the handler.
        assert '"budget": budget_status' not in _DAEMON_SRC, (
            "gateway-health-budget P1: the numeric budget_status dict must not "
            "be embedded in the /healthz response payload."
        )


# --- header / token-parsing hygiene -----------------------------------------

class TestAuthParsingHygiene:
    @pytest.mark.asyncio
    async def test_header_casing_holds(self):
        """HTTP header names are case-insensitive — lowercase must still auth."""
        with patch.dict(os.environ, {"GLUDD_AUTH_PSK": _PSK}):
            app = create_daemon_app(tick_interval=0.01)
        async with _client(app) as c:
            resp = await c.get(
                "/admin/does-not-exist",
                headers={"authorization": f"Bearer {_PSK}"},
            )
        assert resp.status_code != 401, (
            "lowercase 'authorization' header must be honored (HTTP headers are "
            "case-insensitive)."
        )

    @pytest.mark.asyncio
    async def test_trailing_data_rejected(self):
        """A token with extra trailing bytes must NOT authenticate."""
        with patch.dict(os.environ, {"GLUDD_AUTH_PSK": _PSK}):
            app = create_daemon_app(tick_interval=0.01)
        async with _client(app) as c:
            resp = await c.get(
                "/admin/anything",
                headers={"Authorization": f"Bearer {_PSK}EXTRA"},
            )
        assert resp.status_code == 401
