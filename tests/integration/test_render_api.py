"""Integration tests for the playbook web renderer API (routers/render.py).

Exercises both endpoints through the REAL daemon app via ASGITransport with
PSK auth enabled:
  - GET /api/renderers  (PSK-authed list of registered renderers)
  - GET /render/<name>  (public read; executes renderer, returns HTML)

The renderer runner is injected on ``app.state._renderer_runner`` so the test
does NOT depend on a live Ansible run — it uses a deterministic stub that
returns the canonical RenderDocument shape. The router uses ``run_renderer``
which checks for the stub hook before falling back to AnsibleRunnerAdapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from general_ludd.renderers.schema import (
    MarkdownSection,
    Metric,
    MetricGridSection,
    RenderDocument,
    RenderMetadata,
)

PSK = "test-psk-secret"
AUTH = {"Authorization": f"Bearer {PSK}"}


@dataclass
class _StubRunner:
    """Deterministic stand-in for the Ansible-backed renderer runner.

    ``run_renderer`` consults ``app.state._renderer_runner`` first; if set,
    the Ansible path is bypassed entirely. The stub records every call so
    cache-hit tests can assert the runner was invoked exactly once.
    """

    output: RenderDocument
    calls: list[str] = field(default_factory=list)
    fail: bool = False
    timeout: bool = False

    async def run(self, spec: Any) -> RenderDocument:
        self.calls.append(spec.name)
        if self.timeout:
            from general_ludd.renderers.runner import RendererTimeout

            raise RendererTimeout(spec.name, float(spec.timeout_seconds))
        if self.fail:
            from general_ludd.renderers.runner import RendererFailure

            raise RendererFailure(spec.name, "stub failure", stdout="boom", stderr="oops")
        return self.output


def _stub_document() -> RenderDocument:
    return RenderDocument(
        title="System Facts Report",
        sections=[
            MarkdownSection(content="## Overview\nAll systems nominal."),
            MetricGridSection(
                metrics=[
                    Metric(label="Backlog", value=3),
                    Metric(label="Unread", value=1, unit="msg"),
                ]
            ),
        ],
        metadata=RenderMetadata(renderer_version=1),
    )


async def _make_app(
    monkeypatch,
    *,
    runner: _StubRunner | None = None,
) -> tuple[Any, AsyncClient]:
    monkeypatch.setenv("GLUDD_AUTH_PSK", PSK)
    from general_ludd.daemon import create_daemon_app

    app = create_daemon_app(tick_interval=1.0)
    if runner is not None:
        app.state._renderer_runner = runner
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    return app, client


class TestRenderApi:
    @pytest.mark.asyncio
    async def test_render_known_renderer_returns_html(self, monkeypatch):
        runner = _StubRunner(output=_stub_document())
        _app, client = await _make_app(monkeypatch, runner=runner)
        try:
            resp = await client.get("/render/system_facts")
            assert resp.status_code == 200, resp.text
            assert "text/html" in resp.headers.get("content-type", "")
            assert "System Facts Report" in resp.text
            assert "Backlog" in resp.text
            assert "## Overview" in resp.text  # Phase 1: rendered as <pre>
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_render_unknown_returns_404(self, monkeypatch):
        runner = _StubRunner(output=_stub_document())
        _app, client = await _make_app(monkeypatch, runner=runner)
        try:
            resp = await client.get("/render/does_not_exist")
            assert resp.status_code == 404
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_api_renderers_lists_renderer(self, monkeypatch):
        runner = _StubRunner(output=_stub_document())
        _app, client = await _make_app(monkeypatch, runner=runner)
        try:
            resp = await client.get("/api/renderers", headers=AUTH)
            assert resp.status_code == 200, resp.text
            data = resp.json()
            names = [r["name"] for r in data["renderers"]]
            assert "system_facts" in names, names
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_api_renderers_requires_psk(self, monkeypatch):
        runner = _StubRunner(output=_stub_document())
        _app, client = await _make_app(monkeypatch, runner=runner)
        try:
            resp = await client.get("/api/renderers")
            assert resp.status_code == 401
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_renderer_cache_hit_skips_execution(self, monkeypatch):
        runner = _StubRunner(output=_stub_document())
        _app, client = await _make_app(monkeypatch, runner=runner)
        try:
            r1 = await client.get("/render/system_facts")
            r2 = await client.get("/render/system_facts")
            assert r1.status_code == 200 and r2.status_code == 200
            # Cache hit: runner only invoked on the first request.
            assert runner.calls == ["system_facts"], runner.calls
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_renderer_failure_returns_500(self, monkeypatch):
        runner = _StubRunner(output=_stub_document(), fail=True)
        _app, client = await _make_app(monkeypatch, runner=runner)
        try:
            resp = await client.get("/render/system_facts")
            assert resp.status_code == 500, resp.text
            assert "Renderer failed" in resp.text
            assert "stub failure" in resp.text
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_renderer_timeout_returns_504(self, monkeypatch):
        runner = _StubRunner(output=_stub_document(), timeout=True)
        _app, client = await _make_app(monkeypatch, runner=runner)
        try:
            resp = await client.get("/render/system_facts")
            assert resp.status_code == 504, resp.text
            assert "timeout" in resp.text.lower()
        finally:
            await client.aclose()
