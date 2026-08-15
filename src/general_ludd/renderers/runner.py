"""Renderer runner — executes a renderer playbook and validates its artifact.

The single public entry point is :func:`run_renderer`, an ``async`` wrapper
around the daemon's ``AnsibleRunnerAdapter`` (stored on ``app.state._runner``).
It:

1. Resolves the playbook via :class:`RendererSpec` (path / timeout / flags).
2. Creates a per-run ``artifact_dir``, passes it as ``artifact_dir`` extra-var.
3. Runs the playbook in a worker thread (``asyncio.to_thread``) under an
   ``asyncio.wait_for`` timeout (per-renderer ``timeout_seconds``).
4. Reads ``<artifact_dir>/render.json`` (size-capped at
   ``GLUDD_RENDER_MAX_BYTES``, default 1 MiB).
5. Validates the JSON against :class:`RenderDocument`.
6. Overwrites ``metadata.execution_ms`` and ``metadata.playbook`` so the
   playbook cannot lie about its own timing.

Failures surface as :class:`RendererTimeout` (504) or :class:`RendererFailure`
(500) — the router maps these to the right HTTP status and renders
``error.html.j2``.

Tests inject a stand-in via ``app.state._renderer_runner``: any object with
``async def run(spec) -> RenderDocument`` short-circuits the Ansible path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from general_ludd.renderers.registry import RendererSpec
from general_ludd.renderers.schema import RenderDocument
from general_ludd.renderers.schema_loader import (
    FieldMeta,
    extract_field_metadata,
    load_schema,
    validate_against_schema,
)

logger = logging.getLogger(__name__)

RENDER_ARTIFACT_NAME = "render.json"
DEFAULT_MAX_BYTES = 1024 * 1024  # 1 MiB


@dataclass
class RendererResult:
    """Output of :func:`run_renderer` for both canonical and schema-driven modes.

    ``schema`` / ``field_metadata`` are populated only when the renderer has a
    companion ``<name>.schema.json``; otherwise ``doc`` carries the validated
    :class:`RenderDocument` (canonical path). Exactly one of ``doc`` and
    ``schema`` is non-None.
    """

    data: dict[str, object] = field(default_factory=dict)
    schema: dict[str, object] | None = None
    field_metadata: list[FieldMeta] | None = None
    doc: RenderDocument | None = None


class RendererTimeout(Exception):
    """Raised when a renderer playbook exceeds its per-renderer timeout."""

    def __init__(self, name: str, timeout_s: float) -> None:
        """Initialize the timeout error with the renderer name and limit."""
        self.name = name
        self.timeout_s = timeout_s
        super().__init__(f"renderer {name!r} exceeded timeout {timeout_s}s")


class RendererFailure(Exception):
    """Raised on non-zero exit, missing render.json, oversize output, or validation error.

    The router maps this to HTTP 500.
    """

    def __init__(
        self,
        name: str,
        detail: str,
        *,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        """Initialize the failure with renderer name, detail, and captured output."""
        self.name = name
        self.detail = detail
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(f"renderer {name!r} failed: {detail}")


class SchemaValidationError(Exception):
    """Raised when renderer output fails companion JSON-Schema validation.

    The router maps this to HTTP 422 (Unprocessable Entity) and renders
    ``schema_error.html.j2`` with the validation errors. Distinct from
    :class:`RendererFailure` so the router can pick the right status/template.
    """

    def __init__(self, name: str, errors: list[str]) -> None:
        """Initialize the schema error with the renderer name and error list."""
        self.name = name
        self.errors = errors
        super().__init__(f"renderer {name!r} failed companion schema validation: {len(errors)} error(s)")


def _max_bytes() -> int:
    raw = os.environ.get("GLUDD_RENDER_MAX_BYTES")
    if not raw:
        return DEFAULT_MAX_BYTES
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning("GLUDD_RENDER_MAX_BYTES=%r not an int, defaulting", raw)
        return DEFAULT_MAX_BYTES


def _read_render_json(artifact_dir: Path, name: str) -> dict[str, object]:
    path = artifact_dir / RENDER_ARTIFACT_NAME
    if not path.is_file():
        raise RendererFailure(name, f"{RENDER_ARTIFACT_NAME} not written by playbook")
    size = path.stat().st_size
    cap = _max_bytes()
    if size > cap:
        raise RendererFailure(name, f"{RENDER_ARTIFACT_NAME} is {size} bytes (cap={cap})")
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RendererFailure(name, f"cannot parse {RENDER_ARTIFACT_NAME}: {exc}") from exc
    if not isinstance(data, dict):
        raise RendererFailure(name, f"{RENDER_ARTIFACT_NAME} root must be an object, got {type(data).__name__}")
    return data


def _run_sync(
    runner: Any,
    playbook_path: str,
    artifact_dir: str,
    timeout_s: float,
    extra_env: dict[str, str],
) -> dict[str, Any]:
    """Invoke AnsibleRunnerAdapter.run_playbook with renderer extra-vars."""
    playbook_name = Path(playbook_path).name
    try:
        runner.register_playbook(playbook_name, playbook_path)
    except Exception:  # pragma: no cover - best-effort
        logger.debug("renderer playbook %s registration skipped", playbook_name)
    extravars: dict[str, Any] = {"artifact_dir": artifact_dir}
    result: dict[str, Any] = runner.run_playbook(
        playbook_name=playbook_name,
        extravars=extravars,
        env=extra_env,
        timeout=timeout_s,
    )
    return result


async def run_renderer(app: FastAPI, spec: RendererSpec) -> RendererResult:
    """Execute ``spec`` and return the validated output as a :class:`RendererResult`.

    Two modes (§6):

    - **Schema-driven** (``spec.schema_path is not None``): the playbook's
      ``render.json`` is validated against the companion JSON Schema. On
      failure raises :class:`SchemaValidationError`. Returns ``RendererResult``
      with ``schema`` / ``field_metadata`` populated and ``doc=None``.
    - **Canonical**: ``render.json`` is validated against
      :class:`RenderDocument`. Returns ``RendererResult`` with ``doc`` set.

    Tests inject a stand-in via ``app.state._renderer_runner`` (any object
    exposing ``async def run(spec)``); when present, the Ansible path is
    bypassed entirely. The stub may return a :class:`RenderDocument` (canonical
    mode) or a raw ``dict`` (schema-driven / direct data).
    """
    stub = getattr(app.state, "_renderer_runner", None)
    if stub is not None:
        stub_out = await stub.run(spec)
        if isinstance(stub_out, RenderDocument):
            raw = stub_out.model_dump()
            start = time.monotonic()
            return _validate_canonical(spec, raw, start)
        raw = _coerce_stub_output(stub_out, spec)
        start = time.monotonic()
    else:
        runner = getattr(app.state, "_runner", None)
        if runner is None:
            raise RendererFailure(spec.name, "no AnsibleRunnerAdapter on app.state._runner")
        raw, start = await _execute_playbook(spec, runner)

    if spec.schema_path is not None:
        return _validate_with_schema(spec, raw)

    return _validate_canonical(spec, raw, start)


def _coerce_stub_output(stub_out: Any, spec: RendererSpec) -> dict[str, object]:
    """Normalize the stub runner's output into a raw ``render.json`` dict."""
    if isinstance(stub_out, RenderDocument):
        return stub_out.model_dump()
    if isinstance(stub_out, dict):
        return stub_out
    raise RendererFailure(spec.name, f"stub runner returned {type(stub_out).__name__}; expected dict or RenderDocument")


async def _execute_playbook(spec: RendererSpec, runner: Any) -> tuple[dict[str, object], float]:
    """Run the playbook via AnsibleRunnerAdapter; return ``(raw_dict, start_time)``."""
    artifact_dir = Path(tempfile.mkdtemp(prefix=f"gludd-render-{spec.name}-")) / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    extra_env: dict[str, str] = {}
    psk = (os.environ.get("GLUDD_AUTH_PSK") or "").strip()
    if psk:
        extra_env["GLUDD_AUTH_PSK"] = psk
    daemon_url = os.environ.get("GLUDD_DAEMON_URL")
    if daemon_url:
        extra_env["GLUDD_DAEMON_URL"] = daemon_url

    job_id = f"render-{spec.name}-{uuid.uuid4().hex[:8]}"
    start = time.monotonic()

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                _run_sync,
                runner,
                str(spec.path),
                str(artifact_dir),
                float(spec.timeout_seconds),
                extra_env,
            ),
            timeout=float(spec.timeout_seconds),
        )
    except TimeoutError as exc:
        raise RendererTimeout(spec.name, spec.timeout_seconds) from exc

    logger.debug("renderer %s job_id=%s result=%s", spec.name, job_id, result.get("status"))
    status = str(result.get("status", "")).lower()
    rc = int(result.get("rc", 1) or 0)
    if status not in ("successful", "ok", "completed") and rc != 0:
        raise RendererFailure(
            spec.name,
            f"playbook exited status={status!r} rc={rc}",
            stdout=str(result.get("stdout", "")),
            stderr=str(result.get("stderr", "") or result.get("error", "")),
        )

    raw = _read_render_json(artifact_dir, spec.name)
    return raw, start


def _validate_with_schema(spec: RendererSpec, raw: dict[str, object]) -> RendererResult:
    """Validate ``raw`` against the companion schema and return a result."""
    if spec.schema_path is None:  # pragma: no cover - caller guards this
        raise RendererFailure(spec.name, "no companion schema path on spec")
    schema = load_schema(spec.schema_path)
    if schema is None:
        raise RendererFailure(spec.name, f"companion schema {spec.schema_path} not found at run time")
    ok, errors = validate_against_schema(raw, schema)
    if not ok:
        raise SchemaValidationError(spec.name, errors)
    fm = extract_field_metadata(schema)
    return RendererResult(data=raw, schema=schema, field_metadata=fm, doc=None)


def _validate_canonical(spec: RendererSpec, raw: dict[str, object], start: float) -> RendererResult:
    """Validate ``raw`` against :class:`RenderDocument` (canonical path)."""
    try:
        doc = RenderDocument.model_validate(raw)
    except Exception as exc:
        raise RendererFailure(spec.name, f"schema validation failed: {exc}") from exc

    # The runner — not the playbook — owns these metadata fields so the
    # playbook cannot lie about its own timing or identity.
    doc.metadata.execution_ms = int((time.monotonic() - start) * 1000)
    doc.metadata.playbook = Path(spec.path).name
    return RendererResult(data=doc.model_dump(), schema=None, field_metadata=None, doc=doc)
