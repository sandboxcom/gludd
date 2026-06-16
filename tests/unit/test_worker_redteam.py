"""Red-team regression tests for worker auth (W5.6 + AUTH hardening).

Covers the worker's pre-shared-key gate after it was moved onto the shared
``general_ludd.security.auth`` helpers:

  * constant-time PSK comparison (no `token != _psk` timing leak),
  * GLUDD_REQUIRE_AUTH opt-in fail-closed (503 on non-public paths, no PSK),
  * LOUD no-PSK startup warning,
  * default no-PSK posture stays OPEN (back-compat),
  * create_app is import-safe and does NO blocking startup work (no sockets,
    no DNS, no sleeps) — the regression that hung the suite under xdist.

All traffic goes through ASGITransport (in-process); NO real sockets, NO real
network, NO sleeps.
"""

from __future__ import annotations

import inspect
import os
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

import general_ludd.worker.app as worker_mod
from general_ludd.worker.app import create_app

_PSK = "worker-redteam-psk-supersecret"
_WORKER_SRC = inspect.getsource(worker_mod)


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# --- constant-time comparison -----------------------------------------------


class TestConstantTimeCompare:
    def test_source_has_no_nonconstant_psk_compare(self):
        assert "token != _psk" not in _WORKER_SRC, (
            "worker PSK auth must not use the non-constant-time `token != _psk`."
        )

    def test_source_uses_shared_verify_psk(self):
        assert "verify_psk" in _WORKER_SRC, (
            "worker must use the shared constant-time verify_psk helper."
        )

    @pytest.mark.asyncio
    async def test_wrong_token_rejected(self):
        with patch.dict(os.environ, {"GLUDD_PSK": _PSK}):
            app = create_app(gateway=None)
        async with _client(app) as c:
            resp = await c.post(
                "/ping", headers={"Authorization": "Bearer wrong-token"}
            )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_correct_token_accepted(self):
        with patch.dict(os.environ, {"GLUDD_PSK": _PSK}):
            app = create_app(gateway=None)
        async with _client(app) as c:
            resp = await c.post(
                "/ping", headers={"Authorization": f"Bearer {_PSK}"}
            )
        assert resp.status_code != 401

    @pytest.mark.asyncio
    async def test_missing_token_rejected_when_psk_set(self):
        with patch.dict(os.environ, {"GLUDD_PSK": _PSK}):
            app = create_app(gateway=None)
        async with _client(app) as c:
            resp = await c.post("/ping")
        assert resp.status_code == 401


# --- public paths stay open even with a PSK ---------------------------------


class TestPublicPaths:
    @pytest.mark.asyncio
    async def test_healthz_open_with_psk(self):
        with patch.dict(os.environ, {"GLUDD_PSK": _PSK}):
            app = create_app(gateway=None)
        async with _client(app) as c:
            resp = await c.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "healthy"}


# --- GLUDD_REQUIRE_AUTH fail-closed -----------------------------------------


class TestRequireAuthFailClosed:
    @pytest.mark.asyncio
    async def test_require_auth_without_psk_returns_503(self):
        env = dict(os.environ)
        env.pop("GLUDD_PSK", None)
        env["GLUDD_REQUIRE_AUTH"] = "1"
        with patch.dict(os.environ, env, clear=True):
            app = create_app(gateway=None)
        async with _client(app) as c:
            resp = await c.post("/ping")
        assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_require_auth_keeps_healthz_open(self):
        env = dict(os.environ)
        env.pop("GLUDD_PSK", None)
        env["GLUDD_REQUIRE_AUTH"] = "1"
        with patch.dict(os.environ, env, clear=True):
            app = create_app(gateway=None)
        async with _client(app) as c:
            resp = await c.get("/healthz")
        assert resp.status_code == 200


# --- no-PSK default posture (back-compat) -----------------------------------


class TestDefaultPosture:
    @pytest.mark.asyncio
    async def test_no_psk_keeps_endpoints_open(self):
        env = dict(os.environ)
        env.pop("GLUDD_PSK", None)
        env.pop("GLUDD_REQUIRE_AUTH", None)
        with patch.dict(os.environ, env, clear=True):
            app = create_app(gateway=None)
        async with _client(app) as c:
            resp = await c.post("/ping")
        # No auth gate (open) and no fail-closed (503) — handler runs.
        assert resp.status_code not in (401, 503)

    def test_no_psk_logs_loud_warning(self, caplog):
        import logging

        env = dict(os.environ)
        env.pop("GLUDD_PSK", None)
        env.pop("GLUDD_REQUIRE_AUTH", None)
        with patch.dict(os.environ, env, clear=True), caplog.at_level(logging.WARNING):
            create_app(gateway=None)
        joined = " ".join(r.getMessage() for r in caplog.records).lower()
        assert "gludd_psk" in joined and (
            "disabled" in joined or "auth" in joined
        ), "a LOUD no-PSK startup WARNING must fire for the worker."


# --- hang guard: create_app must do NO blocking startup work ----------------


class TestCreateAppImportSafe:
    def test_create_app_does_not_bind_sockets_or_block(self):
        """create_app must be pure object construction — no socket bind, no DNS,
        no network, no sleep. We patch the obvious blocking primitives and assert
        none of them are touched during app construction."""
        import socket as socket_mod
        import time as time_mod

        env = dict(os.environ)
        env.pop("GLUDD_PSK", None)
        env.pop("GLUDD_REQUIRE_AUTH", None)
        sock_bind = AssertionError("socket.bind in create_app")
        sock_conn = AssertionError("socket.connect in create_app")
        create_conn = AssertionError("create_connection in create_app")
        sleep_err = AssertionError("time.sleep in create_app")
        with patch.dict(os.environ, env, clear=True), \
                patch.object(socket_mod.socket, "connect", side_effect=sock_conn), \
                patch.object(socket_mod.socket, "bind", side_effect=sock_bind), \
                patch.object(socket_mod, "create_connection", side_effect=create_conn), \
                patch.object(time_mod, "sleep", side_effect=sleep_err):
            app = create_app(gateway=None)
        assert app is not None

    def test_create_app_explicit_none_gateway_no_config_load(self):
        """Passing gateway=None must NOT trigger build_gateway_from_config (which
        reads disk); that path stays off the construction hot path."""
        called = {"n": 0}

        def _boom(*_a, **_k):
            called["n"] += 1
            return None

        with patch.object(worker_mod, "build_gateway_from_config", _boom):
            create_app(gateway=None)
        assert called["n"] == 0
