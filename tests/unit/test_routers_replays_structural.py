"""Structural tests for routers/replays.py — replay listing endpoint."""

from __future__ import annotations

from general_ludd.routers.replays import register


class TestRegister:
    def test_register_is_callable(self):
        assert callable(register)

    def test_register_accepts_two_args(self):
        from fastapi import FastAPI
        app = FastAPI()
        try:
            register(app, {})
        except Exception as exc:
            raise AssertionError(f"register raised: {exc}") from exc

    def test_registers_replays_route(self):
        from fastapi import FastAPI
        app = FastAPI()
        register(app, {})
        routes = [r.path for r in app.routes]
        assert "/api/replays" in routes
