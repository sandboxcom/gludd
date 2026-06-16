"""Red-team regression tests for daemon auth (findings A-1, A-2, A-3).

A read-only red-team found three daemon-auth weaknesses:

  A-1  PSK compared with `token != _psk` — a non-constant-time comparison
       leaks the secret one byte at a time via a timing side channel.
       FIX: hmac.compare_digest.

  A-2  A debug log printed `_psk[:4]` — the full PSK when it is <= 4 chars,
       and a 4-char crib otherwise. FIX: never log any portion of the PSK;
       log only a boolean `psk_configured`.

  A-3  When GLUDD_PSK is unset/empty, ALL auth is skipped silently and the
       entire /admin surface is open. A naive fail-closed would break the
       ~100 existing no-PSK tests, so the BACKWARD-COMPATIBLE fix is:
         (a) LOUD startup WARNING when no PSK is configured,
         (b) a `no_auth` / `auth_degraded` flag on /healthz,
         (c) opt-in env GLUDD_REQUIRE_AUTH=1 that makes no-PSK fail-closed
             (503 on non-public paths).
       Default posture stays OPEN-but-warned (the existing suite depends on
       open admin access without a PSK).

RESIDUAL (documented): the default no-PSK posture is still open. Operators
who want a hard fail-closed default must set GLUDD_REQUIRE_AUTH=1. This is a
deliberate back-compat concession, not an oversight — flipping the default
would break the no-PSK test corpus and any deployment relying on dev-mode
open access. The warning + healthz flag make the degraded posture observable.
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

import general_ludd.daemon as daemon_mod
from general_ludd.daemon import create_daemon_app

_PSK = "redteam-psk-supersecret-value"
_DAEMON_SRC = Path(inspect.getfile(daemon_mod)).read_text()


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# --- A-1: constant-time comparison ------------------------------------------

class TestA1ConstantTime:
    def test_source_uses_hmac_compare_digest(self):
        assert "hmac.compare_digest" in _DAEMON_SRC, (
            "A-1: PSK auth must use hmac.compare_digest for a constant-time "
            "comparison; a plain `token != _psk` leaks the secret via timing."
        )

    def test_source_has_no_nonconstant_psk_compare(self):
        assert "token != _psk" not in _DAEMON_SRC, (
            "A-1: the non-constant-time `token != _psk` comparison must be gone."
        )

    @pytest.mark.asyncio
    async def test_wrong_token_still_rejected(self):
        with patch.dict(os.environ, {"GLUDD_PSK": _PSK}):
            app = create_daemon_app(tick_interval=0.01)
        async with _client(app) as c:
            resp = await c.get(
                "/admin/anything",
                headers={"Authorization": "Bearer wrong-token"},
            )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_correct_token_authorized(self):
        with patch.dict(os.environ, {"GLUDD_PSK": _PSK}):
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


# --- A-3: degraded flag + opt-in fail-closed --------------------------------

class TestA3NoAuthDegraded:
    @pytest.mark.asyncio
    async def test_healthz_reports_no_auth_when_psk_unset(self):
        env = dict(os.environ)
        env.pop("GLUDD_PSK", None)
        env.pop("GLUDD_REQUIRE_AUTH", None)
        with patch.dict(os.environ, env, clear=True):
            app = create_daemon_app(tick_interval=0.01)
        async with _client(app) as c:
            resp = await c.get("/healthz")
        body = resp.json()
        assert resp.status_code == 200
        # Liveness status stays "healthy" for back-compat; the security posture
        # rides on the no_auth / auth_degraded flags.
        assert body.get("no_auth") is True
        assert body.get("auth_degraded") is True

    @pytest.mark.asyncio
    async def test_healthz_not_degraded_when_psk_set(self):
        with patch.dict(os.environ, {"GLUDD_PSK": _PSK}):
            app = create_daemon_app(tick_interval=0.01)
        async with _client(app) as c:
            resp = await c.get("/healthz")
        body = resp.json()
        assert body.get("no_auth") is False
        assert body.get("auth_degraded") is False

    def test_no_psk_logs_loud_warning(self, caplog):
        import logging

        env = dict(os.environ)
        env.pop("GLUDD_PSK", None)
        env.pop("GLUDD_REQUIRE_AUTH", None)
        with patch.dict(os.environ, env, clear=True), caplog.at_level(logging.WARNING):
            create_daemon_app(tick_interval=0.01)
        joined = " ".join(r.getMessage() for r in caplog.records).lower()
        assert "gludd_psk" in joined and (
            "disabled" in joined or "no_auth" in joined or "open" in joined
        ), "A-3: a LOUD startup WARNING must fire when GLUDD_PSK is unset."

    @pytest.mark.asyncio
    async def test_default_no_psk_keeps_admin_open(self):
        """Back-compat residual: default posture is OPEN-but-warned, NOT 503."""
        env = dict(os.environ)
        env.pop("GLUDD_PSK", None)
        env.pop("GLUDD_REQUIRE_AUTH", None)
        with patch.dict(os.environ, env, clear=True):
            app = create_daemon_app(tick_interval=0.01)
        async with _client(app) as c:
            resp = await c.get("/admin/does-not-exist")
        # No 503 (not fail-closed) and no 401 (no auth gate) — handler-level 404.
        assert resp.status_code != 503
        assert resp.status_code != 401

    @pytest.mark.asyncio
    async def test_require_auth_without_psk_fails_closed(self):
        env = dict(os.environ)
        env.pop("GLUDD_PSK", None)
        env["GLUDD_REQUIRE_AUTH"] = "1"
        with patch.dict(os.environ, env, clear=True):
            app = create_daemon_app(tick_interval=0.01)
        async with _client(app) as c:
            resp = await c.get("/admin/anything")
        assert resp.status_code == 503, (
            "A-3: GLUDD_REQUIRE_AUTH=1 with no PSK must refuse non-public paths."
        )

    @pytest.mark.asyncio
    async def test_require_auth_keeps_public_paths_open(self):
        env = dict(os.environ)
        env.pop("GLUDD_PSK", None)
        env["GLUDD_REQUIRE_AUTH"] = "1"
        with patch.dict(os.environ, env, clear=True):
            app = create_daemon_app(tick_interval=0.01)
        async with _client(app) as c:
            resp = await c.get("/healthz")
        assert resp.status_code == 200


# --- header / token-parsing hygiene -----------------------------------------

class TestAuthParsingHygiene:
    @pytest.mark.asyncio
    async def test_header_casing_holds(self):
        """HTTP header names are case-insensitive — lowercase must still auth."""
        with patch.dict(os.environ, {"GLUDD_PSK": _PSK}):
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
        with patch.dict(os.environ, {"GLUDD_PSK": _PSK}):
            app = create_daemon_app(tick_interval=0.01)
        async with _client(app) as c:
            resp = await c.get(
                "/admin/anything",
                headers={"Authorization": f"Bearer {_PSK}EXTRA"},
            )
        assert resp.status_code == 401
