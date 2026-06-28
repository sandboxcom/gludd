"""Integration tests for the playbook web renderer API (routers/render.py).

Exercises both endpoints through the REAL daemon app via ASGITransport with
PSK auth enabled:
  - GET /api/renderers  (PSK-authed list of registered renderers)
  - GET /render/<name>  (public read; executes renderer, returns HTML)

The renderer executor is injected on app.state so the test does NOT depend
on a live Ansible run — it uses a deterministic stub that returns the
canonical RendererOutput shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from general_ludd.renderers.schema import (
    MarkdownSection,
    MetricGridSection,
    RendererOutput,
)

PSK = "test-psk-secret"
AUTH = {"Authorization": f"Bearer {PSK}"}


@dataclass
class _StubExecutor:
    """Deterministic stand-in for RendererExecutor used by the router.

    The router only depends on `async def run(name) -> RendererOutput`,
    so a dataclass with a matching method satisfies the contract without
    needing the real Ansible-backed executor (which would require a live
    daemon + PSK + ansible-core in the test environment).
    """

    output: RendererOutput

    async def run(self, name: str) -> RendererOutput:
        return self.output


def _stub_output() -> RendererOutput:
    return RendererOutput(
        title="System Facts Report",
        sections=[
            MarkdownSection(body="## Overview\nAll systems nominal."),
            MetricGridSection(
                metrics=[
                    {"label": "Backlog", "value": "3"},
                    {"label": "Unread", "value": "1"},
                ]
            ),
        ],
        metadata={"renderer": "system_facts"},
    )


async def _make_app(monkeypatch) -> tuple[Any, AsyncClient]:
    """Build the real daemon app with the render router's executor stubbed.

    Mirrors the harness in test_messages_and_facts_api.py: PSK is set via
    monkeypatch (auto-reverted) so it does not leak into other tests, and
    the renderer executor is injected on app.state before any request.
    """
    monkeypatch.setenv("GLUDD_PSK", PSK)
    from general_ludd.daemon import create_daemon_app

    app = create_daemon_app(tick_interval=1.0)
    app.state._renderer_executor = _StubExecutor(output=_stub_output())
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    return app, client


class TestRenderApi:
    @pytest.mark.asyncio
    async def test_list_renderers_returns_system_facts(self, monkeypatch):
        app, client = await _make_app(monkeypatch)
        try:
            reg = getattr(app.state, "_renderer_registry", None)
            print(f"DEBUG registry={reg!r} type={type(reg).__name__}")
            if reg is not None:
                print(f"DEBUG playbooks_dir={reg.playbooks_dir}")
                renderers_dir = reg.playbooks_dir / "renderers"
                print(f"DEBUG renderers_dir exists={renderers_dir.is_dir()}")
                if renderers_dir.is_dir():
                    print(f"DEBUG files={list(renderers_dir.glob('*.yml'))}")
                print(f"DEBUG list_all={reg.list_all()}")
            resp = await client.get("/api/renderers", headers=AUTH)
            assert resp.status_code == 200, resp.text
            data = resp.json()
            names = [r["name"] for r in data["renderers"]]
            assert "system_facts" in names, names
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_list_renderers_requires_psk(self, monkeypatch):
        _app, client = await _make_app(monkeypatch)
        try:
            resp = await client.get("/api/renderers")
            assert resp.status_code == 401
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_render_system_facts_returns_html(self, monkeypatch):
        _app, client = await _make_app(monkeypatch)
        try:
            resp = await client.get("/render/system_facts")
            assert resp.status_code == 200, resp.text
            assert "text/html" in resp.headers.get("content-type", "")
            assert "System Facts Report" in resp.text
            assert "Backlog" in resp.text
        finally:
            await client.aclose()
