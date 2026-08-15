"""Integration tests for the schema-driven renderer API surface.

Companion to ``tests/integration/test_render_api.py``. Covers the wiring
landed by the schema-driven rendering task (routers/render.py + runner.py):

  - GET /render/<name>          switches to ``schema_page.html.j2`` when a
    companion ``<name>.schema.json`` is present (and validates against it).
  - GET /render/<name>/schema   returns the companion JSON Schema as
    ``application/schema+json`` (404 when absent or unknown).
  - GET /api/renderers          includes ``has_schema: bool`` per renderer.
  - Schema-validation failure → 422 + ``schema_error.html.j2``.

The existing canonical-mode tests in ``test_render_api.py`` were broken by
the parallel landing of ``playbooks/renderers/system_facts.schema.json``
(production ``system_facts`` is now schema-driven, so the canonical-mode
stub-data test hits the schema validator and 422s). That file is owned by
the parallel task; this file deliberately does not touch it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from general_ludd.renderers.registry import RendererRegistry

PSK = "test-psk-secret"
AUTH = {"Authorization": f"Bearer {PSK}"}


@dataclass
class _StubRunner:
    """Stand-in renderer runner. ``output`` is a raw ``dict`` (render.json)."""

    output: Any
    calls: list[str] = field(default_factory=list)

    async def run(self, spec: Any) -> Any:
        self.calls.append(spec.name)
        return self.output


async def _make_app(
    monkeypatch,
    *,
    runner: _StubRunner | None = None,
    registry: RendererRegistry,
) -> tuple[Any, AsyncClient]:
    monkeypatch.setenv("GLUDD_AUTH_PSK", PSK)
    from general_ludd.daemon import create_daemon_app

    app = create_daemon_app(tick_interval=1.0)
    app.state._renderer_registry = registry
    if runner is not None:
        app.state._renderer_runner = runner
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    return app, client


def _write_fixture(
    tmp_path: Path,
    *,
    name: str,
    with_schema: bool,
    schema: dict[str, Any] | None = None,
) -> Path:
    """Write a ``<name>.yml`` (and optional ``<name>.schema.json``) renderer."""
    d = tmp_path / "renderers"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.yml").write_text(
        "---\n"
        "- name: Render fixture\n"
        "  hosts: localhost\n"
        "  connection: local\n"
        "  gather_facts: false\n"
        "  vars:\n"
        "    renderer: true\n"
        f"    renderer_description: 'fixture {name}'\n"
        "    artifact_dir: '{{ artifact_dir }}'\n"
        "  tasks: []\n"
    )
    if with_schema:
        if schema is None:
            schema = {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "title": "Foo Schema Title",
                "type": "object",
                "properties": {
                    "backlog": {"type": "integer", "description": "open work items"},
                    "owner": {"type": "string", "description": "team owner"},
                },
                "required": ["backlog"],
            }
        (d / f"{name}.schema.json").write_text(json.dumps(schema))
    return d


def _registry(d: Path) -> RendererRegistry:
    reg = RendererRegistry(bundled_dir=d, operator_dir=None)
    reg.discover()
    return reg


class TestSchemaDrivenRender:
    @pytest.mark.asyncio
    async def test_render_with_companion_schema_uses_schema_template(
        self, monkeypatch, tmp_path
    ):
        d = _write_fixture(tmp_path, name="foo", with_schema=True)
        runner = _StubRunner(output={"backlog": 7, "owner": "ops"})
        _app, client = await _make_app(
            monkeypatch, runner=runner, registry=_registry(d)
        )
        try:
            resp = await client.get("/render/foo")
            assert resp.status_code == 200, resp.text
            # Schema-driven template echoes the companion schema's title.
            assert "Foo Schema Title" in resp.text
            # Canonical-mode marker must NOT appear (no RenderDocument title).
            assert "System Facts Report" not in resp.text
            # Rendered data row.
            assert "backlog" in resp.text
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_render_with_schema_validation_failure_returns_422(
        self, monkeypatch, tmp_path
    ):
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "Strict Foo",
            "type": "object",
            "properties": {"backlog": {"type": "integer"}},
            "required": ["backlog"],
        }
        d = _write_fixture(
            tmp_path, name="foo", with_schema=True, schema=schema
        )
        runner = _StubRunner(output={"backlog": "not-an-int"})
        _app, client = await _make_app(
            monkeypatch, runner=runner, registry=_registry(d)
        )
        try:
            resp = await client.get("/render/foo")
            assert resp.status_code == 422, resp.text
            assert "Schema validation error" in resp.text
            assert "Strict Foo" in resp.text
            assert "backlog" in resp.text
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_get_schema_endpoint_returns_schema(self, monkeypatch, tmp_path):
        d = _write_fixture(tmp_path, name="foo", with_schema=True)
        _app, client = await _make_app(monkeypatch, registry=_registry(d))
        try:
            resp = await client.get("/render/foo/schema")
            assert resp.status_code == 200, resp.text
            assert resp.headers["content-type"].startswith("application/schema+json")
            body = resp.json()
            assert body["$schema"] == "https://json-schema.org/draft/2020-12/schema"
            assert body["title"] == "Foo Schema Title"
            assert "backlog" in body["properties"]
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_get_schema_endpoint_404_when_no_companion(
        self, monkeypatch, tmp_path
    ):
        d = _write_fixture(tmp_path, name="foo", with_schema=False)
        _app, client = await _make_app(monkeypatch, registry=_registry(d))
        try:
            resp = await client.get("/render/foo/schema")
            assert resp.status_code == 404, resp.text
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_get_schema_endpoint_404_when_renderer_missing(
        self, monkeypatch, tmp_path
    ):
        d = _write_fixture(tmp_path, name="foo", with_schema=True)
        _app, client = await _make_app(monkeypatch, registry=_registry(d))
        try:
            resp = await client.get("/render/nope/schema")
            assert resp.status_code == 404, resp.text
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_api_renderers_includes_has_schema(self, monkeypatch, tmp_path):
        # foo HAS a companion schema; bar does NOT.
        d = _write_fixture(tmp_path, name="foo", with_schema=True)
        _write_fixture(tmp_path, name="bar", with_schema=False)
        # Both fixtures share <tmp>/renderers — rediscover from that dir.
        _app, client = await _make_app(monkeypatch, registry=_registry(d))
        try:
            resp = await client.get("/api/renderers", headers=AUTH)
            assert resp.status_code == 200, resp.text
            by_name = {r["name"]: r for r in resp.json()["renderers"]}
            assert by_name["foo"]["has_schema"] is True, by_name
            assert by_name["bar"]["has_schema"] is False, by_name
        finally:
            await client.aclose()
