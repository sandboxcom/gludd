"""Playbook web renderer FastAPI router (§3.5).

Endpoints:

- ``GET /render/{name}``        — public read; executes the named renderer
  playbook and returns the rendered page. When a companion
  ``<name>.schema.json`` exists, the schema-driven template is used and the
  playbook's ``render.json`` is validated against that schema (422 on failure).
  Otherwise the canonical :class:`RenderDocument` path applies.
- ``GET /render/{name}/schema`` — public read; returns the companion JSON
  Schema for ``{name}`` as ``application/schema+json`` (404 if absent).
- ``GET /api/renderers``        — PSK-authed list of registered renderers
  (NOT in ``_PUBLIC_PATHS``; daemon middleware enforces the bearer PSK).

Wiring (done in ``daemon.create_daemon_app``):

- ``app.state._renderer_registry`` — :class:`RendererRegistry`.
- ``app.state._renderer_cache``    — :class:`RendererCache`.

On timeout the router returns ``504 Gateway Timeout``; on schema-validation
failure ``422``; on any other failure ``500``. Each renders an appropriate
template with the renderer name and detail (stdout/stderr tails are
operator-only — they only appear when the PSK-authenticated admin path
renders the error).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import FileSystemLoader, select_autoescape
from jinja2.sandbox import SandboxedEnvironment

from general_ludd.renderers.cache import RendererCache
from general_ludd.renderers.registry import RendererRegistry
from general_ludd.renderers.runner import (
    RendererFailure,
    RendererTimeout,
    SchemaValidationError,
    run_renderer,
)
from general_ludd.renderers.schema import RenderDocument

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = (
    Path(__file__).resolve().parent.parent / "templates" / "render"
)
_PAGE_TEMPLATE = "page.html.j2"
_SCHEMA_PAGE_TEMPLATE = "schema_page.html.j2"
_SCHEMA_ERROR_TEMPLATE = "schema_error.html.j2"
_ERROR_TEMPLATE = "error.html.j2"
_SCHEMA_CONTENT_TYPE = "application/schema+json"


def _dump_json(value: object) -> str:
    """JSON encoder filter tolerant of pydantic models (RenderMetadata)."""
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    return json.dumps(value, indent=2, default=str)


# Module-level Jinja2 environment: autoescape ON for all string output. Only
# the explicit ``| safe`` in sections/raw_html.html.j2 bypasses escaping, and
# only when the renderer spec opts in via ``allow_raw_html``. Sandboxed for
# defense-in-depth consistency with every other jinja2 environment in this
# codebase (registry/variable_store/ansible/skills all use
# SandboxedEnvironment) even though this one only ever loads fixed in-repo
# ``.j2`` files, not attacker-supplied template text.
_env = SandboxedEnvironment(
    loader=FileSystemLoader(searchpath=str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(default=True, default_for_string=True),
    trim_blocks=True,
    lstrip_blocks=True,
)
_env.filters["dump_json"] = _dump_json


def _get_registry(app: FastAPI) -> RendererRegistry | None:
    reg = getattr(app.state, "_renderer_registry", None)
    return reg if isinstance(reg, RendererRegistry) else None


def _get_cache(app: FastAPI) -> RendererCache | None:
    cache = getattr(app.state, "_renderer_cache", None)
    return cache if isinstance(cache, RendererCache) else None


def render_document(doc: RenderDocument, *, allow_raw_html: bool = False) -> str:
    """Render a :class:`RenderDocument` to an HTML string (exposed for tests)."""
    return _env.get_template(_PAGE_TEMPLATE).render(
        doc=doc, allow_raw_html=allow_raw_html
    )


def render_schema_page(
    *, schema: dict[str, object], data: dict[str, object], field_metadata: Sequence[object] | None
) -> str:
    """Render the schema-driven template (exposed for tests)."""
    return _env.get_template(_SCHEMA_PAGE_TEMPLATE).render(
        schema=schema,
        data=data,
        field_metadata=field_metadata or [],
    )


def render_schema_error(
    *,
    name: str,
    errors: list[dict[str, str]] | list[object],
    schema: dict[str, object] | None = None,
) -> str:
    """Render the schema-validation error template (exposed for tests).

    The companion ``schema_error.html.j2`` template reads ``schema.title`` and
    walks ``errors`` (messages or dicts with ``path`` / ``message``).
    """
    return _env.get_template(_SCHEMA_ERROR_TEMPLATE).render(
        name=name,
        errors=errors,
        schema=schema if schema is not None else {},
    )


def render_error(
    *,
    title: str,
    name: str,
    detail: str = "",
    timeout_s: float | None = None,
    stdout: str = "",
    stderr: str = "",
) -> str:
    """Render the error template (exposed for tests)."""
    return _env.get_template(_ERROR_TEMPLATE).render(
        title=title,
        name=name,
        detail=detail,
        timeout_s=timeout_s,
        stdout=stdout,
        stderr=stderr,
    )


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:

    @app.get(
        "/api/renderers",
        summary="List registered renderer playbooks (admin)",
        description="PSK-authed. Returns the catalog of discovered renderer playbooks.",
    )
    async def api_list_renderers() -> dict[str, object]:
        registry = _get_registry(app)
        if registry is None:
            return {"renderers": [], "count": 0}
        return {
            "renderers": [
                {**spec.model_dump(), "has_schema": spec.schema_path is not None}
                for spec in registry
            ],
            "count": len(registry),
        }

    @app.get(
        "/render/{name}/schema",
        summary="Return a renderer's companion JSON Schema",
        description=(
            "Public read. Returns the companion ``<name>.schema.json`` as "
            "``application/schema+json``. 404 if the renderer has no companion "
            "schema, or if the renderer does not exist."
        ),
    )
    async def get_renderer_schema(name: str) -> JSONResponse:
        registry = _get_registry(app)
        if registry is None:
            raise HTTPException(status_code=404, detail=f"renderer {name!r} not found")
        spec = registry.get(name)
        if spec is None or spec.schema_path is None:
            raise HTTPException(status_code=404, detail=f"no companion schema for {name!r}")
        import json as _json

        try:
            payload = _json.loads(Path(spec.schema_path).read_text())
        except (OSError, _json.JSONDecodeError) as exc:
            logger.warning("schema %s unreadable: %s", spec.schema_path, exc)
            raise HTTPException(status_code=500, detail=f"schema unreadable: {exc}") from exc
        return JSONResponse(content=payload, media_type=_SCHEMA_CONTENT_TYPE)

    @app.get(
        "/render/{name}",
        response_class=HTMLResponse,
        summary="Render a renderer playbook as HTML",
        description="Public read. Executes the renderer and returns the rendered page.",
    )
    async def render_by_name(name: str) -> HTMLResponse:
        registry = _get_registry(app)
        cache = _get_cache(app)
        if registry is None:
            return HTMLResponse(
                status_code=503,
                content=render_error(
                    title="Renderer not configured",
                    name=name,
                    detail="The renderer subsystem is not initialized on this daemon.",
                ),
            )
        spec = registry.get(name)
        if spec is None:
            raise HTTPException(status_code=404, detail=f"renderer {name!r} not found")

        # Cache hit short-circuits playbook execution entirely. The cache stores
        # rendered HTML (keyed by spec.name) so both canonical and schema-driven
        # modes share the same path. Cache stays keyed on spec.name per §6.
        if cache is not None:
            cached_html = cache.get(name)
            if cached_html is not None:
                return HTMLResponse(content=cached_html)

        try:
            result = await run_renderer(app, spec)
        except RendererTimeout as exc:
            return HTMLResponse(
                status_code=504,
                content=render_error(
                    title="Renderer timeout",
                    name=exc.name,
                    detail=f"playbook exceeded the {exc.timeout_s}s timeout",
                    timeout_s=exc.timeout_s,
                ),
            )
        except SchemaValidationError as exc:
            loaded_schema: dict[str, object] | None = None
            try:
                from general_ludd.renderers.schema_loader import load_schema

                if spec.schema_path is not None:
                    loaded_schema = load_schema(spec.schema_path)
            except Exception:  # pragma: no cover - best-effort title
                pass
            structured_errors: list[dict[str, str]] = []
            for msg in exc.errors:
                # Parallel task's validator emits "<path>: <message>"; split
                # on the first ": " so the template can show them separately.
                if ": " in msg:
                    p, _, m = msg.partition(": ")
                    structured_errors.append({"path": p, "message": m})
                else:
                    structured_errors.append({"path": "(root)", "message": msg})
            return HTMLResponse(
                status_code=422,
                content=render_schema_error(
                    name=exc.name,
                    errors=structured_errors,
                    schema=loaded_schema,
                ),
            )
        except RendererFailure as exc:
            logger.warning("renderer %s failed: %s", exc.name, exc.detail)
            return HTMLResponse(
                status_code=500,
                content=render_error(
                    title="Renderer failed",
                    name=exc.name,
                    detail=exc.detail,
                    stdout=exc.stdout,
                    stderr=exc.stderr,
                ),
            )
        except Exception:
            # Keep unexpected runner internals in the operator log. Rendering
            # exception text into this public endpoint would turn stack and
            # backend details into an unauthenticated disclosure surface.
            logger.exception("renderer %s crashed unexpectedly", name)
            return HTMLResponse(
                status_code=500,
                content=render_error(
                    title="Renderer failed",
                    name=name,
                    detail="An unexpected renderer error occurred.",
                ),
            )

        if result.schema is not None:
            html = render_schema_page(
                schema=result.schema,
                data=result.data,
                field_metadata=result.field_metadata,
            )
        else:
            doc = result.doc
            assert doc is not None  # canonical mode always populates doc
            html = render_document(doc, allow_raw_html=spec.allow_raw_html)

        if cache is not None:
            cache.set(name, html, ttl=spec.cache_ttl_seconds)

        return HTMLResponse(content=html)
