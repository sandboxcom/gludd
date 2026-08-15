r"""Push-side ingest router — OTLP / webhook / gelf / fluent / beats endpoints.

This module provides a ``register(app, _daemon_state)`` router in the SAME shape
as :mod:`general_ludd.routers.todos`, so the daemon can mount it with one call.
It terminates the push side of #82: log shippers and OTLP exporters POST telemetry
here, it is parsed (reusing the pure fail-soft parsers), normalized
(:func:`general_ludd.connectors.normalize.normalize_join_keys`) and dropped into a
bounded :class:`~general_ludd.receiver.buffer.ReceiverBuffer` for a consumer to
drain.

Endpoints (all POST):

* ``/v1/logs`` / ``/v1/metrics`` / ``/v1/traces`` — OTLP/HTTP (JSON; guarded
  protobuf).
* ``/ingest/webhook`` — generic JSON webhook (single object or array).
* ``/ingest/gelf`` / ``/ingest/fluent`` / ``/ingest/beats`` — Graylog GELF /
  Fluent Forward / Elastic Beats, reusing
  :mod:`general_ludd.connectors.ingest_formats`.

Per-request pipeline: **auth -> size-cap -> rate-limit -> parse -> normalize ->
buffer.offer (backpressure)**.

Security invariants
-------------------
* **Ingest-token auth, separate from admin PSK.** The receiver authenticates with
  its OWN token (``GLUDD_INGEST_TOKEN`` by default — env-var NAME only, never the
  value, is referenced/logged), NOT the admin ``GLUDD_AUTH_PSK``. This is least-
  privilege: a leaked ingest token can push telemetry but cannot touch ``/admin``.
* **Fail-closed.** With no ingest token configured every endpoint returns **503**
  ``ingest_disabled`` — the receiver refuses to run as an unauthenticated open
  relay. There is no "degraded but open" default.
* **Body cap BEFORE allocation.** The ``Content-Length`` header is checked against
  :data:`MAX_BODY_BYTES` (8 MiB) and rejected (**413**) before the body is read,
  and the streamed read is also hard-capped, so a lying / chunked sender cannot
  force an unbounded allocation.
* **Per-token rate limit** (token-bucket) -> **429** when exceeded.
* **Buffer backpressure** -> **503** when a REJECT-policy buffer is full; nothing
  already buffered is lost.
* No SSTI / template rendering of any raw payload; the raw bytes only ever reach
  the pure parsers and the in-memory buffer. No outbound egress is performed.

Daemon mount (documented, NOT applied this wave)
------------------------------------------------
Wiring into ``general_ludd.daemon`` is intentionally left to a follow-up wave; do
NOT edit ``daemon.py`` here. When mounting, two changes are required:

1. **Construct the buffer in the lifespan** (next to ``_recent_traces`` in
   ``_get_or_create_extended_subsystems``)::

       from general_ludd.receiver.buffer import ReceiverBuffer, OverflowPolicy
       if getattr(app.state, "_receiver_buffer", None) is None:
           app.state._receiver_buffer = ReceiverBuffer(
               maxlen=10_000,
               overflow=OverflowPolicy.REJECT,   # signal 503 backpressure
               retention_s=3600,
           )

   and expose it on ``_daemon_state`` (the dict passed to ``register``)::

       _daemon_state["receiver_buffer"] = app.state._receiver_buffer

2. **Register the router** alongside the other ``*.register(app, _daemon_state)``
   calls in ``create_daemon_app``::

       from general_ludd.receiver import router as receiver_router
       receiver_router.register(app, _daemon_state)

3. **Add the ingest paths to the daemon's ``_PUBLIC_PATHS``** middleware set so
   the admin-PSK gate does NOT also challenge them (the receiver does its OWN
   ingest-token auth). Because ``_is_public`` only treats SAFE methods as public,
   and these endpoints are POST, you must additionally allow them through the PSK
   middleware for POST — e.g. extend ``_is_public`` to treat ``/v1/*`` and
   ``/ingest/*`` as receiver-owned (auth handled in-router). Keeping admin PSK and
   the ingest token as DISTINCT credentials is the whole point of the separation.

A drained-consumer seam (the event loop or a connector bridge calling
``buffer.drain()``) is likewise out of scope for this wave.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from general_ludd.connectors import ingest_formats
from general_ludd.connectors.normalize import normalize_join_keys
from general_ludd.receiver import parsers
from general_ludd.receiver.buffer import ReceiverBuffer

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)

__all__ = ["register"]

# Hard body cap, checked against Content-Length BEFORE the body is read and again
# while streaming. Matches the parser-layer payload cap (8 MiB).
MAX_BODY_BYTES = parsers.MAX_PAYLOAD_BYTES

# Env var that NAMES the ingest token (the value lives in the env; only the NAME
# appears in code/logs). Separate from GLUDD_AUTH_PSK (admin) by design.
INGEST_TOKEN_ENV = "GLUDD_INGEST_TOKEN"  # env-var NAME, not a secret value

# Default per-token rate limit (requests per second, with a small burst).
DEFAULT_RATE_PER_SEC = 50.0
DEFAULT_RATE_BURST = 100.0


class _TokenBucket:
    """A simple monotonic token-bucket rate limiter, one per ingest token."""

    def __init__(self, rate_per_sec: float, burst: float) -> None:
        self._rate = rate_per_sec
        self._burst = burst
        self._tokens = burst
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        now = time.monotonic()
        with self._lock:
            elapsed = now - self._last
            self._last = now
            self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False


class _RateLimiter:
    """Per-token token buckets created lazily on first sight of a token."""

    def __init__(self, rate_per_sec: float, burst: float) -> None:
        self._rate = rate_per_sec
        self._burst = burst
        self._buckets: dict[str, _TokenBucket] = {}
        self._lock = threading.Lock()

    def allow(self, token_key: str) -> bool:
        with self._lock:
            bucket = self._buckets.get(token_key)
            if bucket is None:
                bucket = _TokenBucket(self._rate, self._burst)
                self._buckets[token_key] = bucket
        return bucket.allow()


def _configured_token() -> str | None:
    """Return the configured ingest token VALUE from its env var, or None.

    Reading the env var by NAME (``INGEST_TOKEN_ENV``) keeps the secret out of the
    codebase. An empty/blank value counts as *not configured* (fail-closed).
    """
    value = os.environ.get(INGEST_TOKEN_ENV, "").strip()
    return value or None


def _extract_bearer(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth.removeprefix("Bearer ").strip()
    # Common shipper convention: a dedicated header.
    return request.headers.get("X-Ingest-Token", "").strip()


def register(
    app: FastAPI,
    _daemon_state: dict[str, Any],
    *,
    rate_per_sec: float = DEFAULT_RATE_PER_SEC,
    rate_burst: float = DEFAULT_RATE_BURST,
) -> None:
    """Mount the push-side ingest endpoints on ``app``.

    ``_daemon_state["receiver_buffer"]`` SHOULD hold a
    :class:`~general_ludd.receiver.buffer.ReceiverBuffer` (constructed in the
    daemon lifespan — see this module's docstring). If absent, a private bounded
    buffer is created so the router is self-contained and testable in isolation;
    in that case it is also exposed back on ``_daemon_state`` so a consumer can
    find it.
    """
    import hmac

    buffer = _daemon_state.get("receiver_buffer")
    if not isinstance(buffer, ReceiverBuffer):
        buffer = ReceiverBuffer()
        _daemon_state["receiver_buffer"] = buffer

    limiter = _RateLimiter(rate_per_sec, rate_burst)

    def _auth_or_error(request: Request) -> Response | str:
        """Return the validated token on success, or a JSONResponse error."""
        configured = _configured_token()
        if configured is None:
            # FAIL-CLOSED: no ingest token configured -> refuse (never open relay).
            logger.warning(
                "receiver: ingest rejected — no ingest token configured "
                "(set %s to enable). Failing closed.",
                INGEST_TOKEN_ENV,
            )
            return JSONResponse(
                status_code=503,
                content={"error": "ingest_disabled", "reason": "no ingest token configured"},
            )
        presented = _extract_bearer(request)
        if not presented or not hmac.compare_digest(presented, configured):
            return JSONResponse(status_code=401, content={"error": "unauthorized"})
        return presented

    async def _read_capped_body(request: Request) -> bytes | Response:
        """Read the body with a hard cap applied BEFORE/while allocating."""
        # 1) Reject on a declared Content-Length over the cap, before reading.
        cl = request.headers.get("Content-Length")
        if cl is not None:
            try:
                if int(cl) > MAX_BODY_BYTES:
                    return JSONResponse(
                        status_code=413,
                        content={"error": "payload_too_large", "max_bytes": MAX_BODY_BYTES},
                    )
            except ValueError:
                return JSONResponse(status_code=400, content={"error": "bad_content_length"})
        # 2) Stream-read with a hard cap so a lying/chunked sender can't overrun it.
        chunks: list[bytes] = []
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > MAX_BODY_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={"error": "payload_too_large", "max_bytes": MAX_BODY_BYTES},
                )
            chunks.append(chunk)
        return b"".join(chunks)

    def _ingest(records: list[dict[str, Any]]) -> Response:
        """Normalize + buffer parsed records; answer 503 on backpressure."""
        accepted = 0
        for record in records:
            normalized = normalize_join_keys(record)
            if buffer.offer(normalized):
                accepted += 1
            else:
                # REJECT-policy buffer is full -> backpressure. Report what we took.
                return JSONResponse(
                    status_code=503,
                    content={
                        "error": "buffer_full",
                        "accepted": accepted,
                        "rejected": len(records) - accepted,
                    },
                )
        return JSONResponse(
            status_code=202,
            content={"accepted": accepted, "received": len(records)},
        )

    async def _handle(
        request: Request,
        parse: Callable[[bytes, str | None], list[dict[str, Any]]],
    ) -> Response:
        """Shared pipeline: auth -> rate-limit -> size-cap -> parse -> ingest."""
        auth = _auth_or_error(request)
        if isinstance(auth, Response):
            return auth
        # Rate-limit per presented token (hashed so the token itself is not a key
        # we hold in plaintext maps any longer than necessary).
        token_key = hmac.new(b"receiver-rl", auth.encode("utf-8"), "sha256").hexdigest()
        if not limiter.allow(token_key):
            return JSONResponse(status_code=429, content={"error": "rate_limited"})
        body = await _read_capped_body(request)
        if isinstance(body, Response):
            return body
        content_type = request.headers.get("Content-Type")
        records = parse(body, content_type)
        return _ingest(records)

    # --- OTLP ------------------------------------------------------------- #
    @app.post("/v1/logs")
    async def otlp_logs(request: Request) -> Response:
        return await _handle(
            request, lambda b, ct: parsers.parse_otlp_logs(b, content_type=ct)
        )

    @app.post("/v1/metrics")
    async def otlp_metrics(request: Request) -> Response:
        return await _handle(
            request, lambda b, ct: parsers.parse_otlp_metrics(b, content_type=ct)
        )

    @app.post("/v1/traces")
    async def otlp_traces(request: Request) -> Response:
        return await _handle(
            request, lambda b, ct: parsers.parse_otlp_traces(b, content_type=ct)
        )

    # --- generic webhook + shipper formats -------------------------------- #
    @app.post("/ingest/webhook")
    async def ingest_webhook(request: Request) -> Response:
        return await _handle(request, lambda b, ct: _parse_webhook(b))

    @app.post("/ingest/gelf")
    async def ingest_gelf(request: Request) -> Response:
        return await _handle(request, lambda b, ct: ingest_formats.parse_gelf(b))

    @app.post("/ingest/fluent")
    async def ingest_fluent(request: Request) -> Response:
        return await _handle(request, lambda b, ct: ingest_formats.parse_fluent_forward(b))

    @app.post("/ingest/beats")
    async def ingest_beats(request: Request) -> Response:
        return await _handle(request, lambda b, ct: _parse_beats(b))

    # --- health ------------------------------------------------------------ #
    @app.get("/api/receive/health")
    async def receiver_health() -> JSONResponse:
        return JSONResponse(content=buffer.snapshot())


def _parse_webhook(payload: bytes) -> list[dict[str, Any]]:
    """Parse a generic JSON webhook body into normalized log records, fail-soft.

    Accepts a single JSON object or an array of objects. Each object becomes one
    log record; ``message``/``msg`` and ``level``/``severity``/``status`` are
    surfaced, everything else lands in ``labels``. NEVER raises.
    """
    import json

    if not isinstance(payload, (bytes, bytearray)):
        return []
    payload = bytes(payload)
    if len(payload) > MAX_BODY_BYTES:
        return []
    try:
        doc = json.loads(payload)
    except (ValueError, TypeError):
        return []

    items = doc if isinstance(doc, list) else [doc]
    records: list[dict[str, Any]] = []
    for item in items:
        if len(records) >= parsers.MAX_EVENTS:
            break
        if not isinstance(item, dict):
            continue
        message = item.get("message") or item.get("msg") or ""
        labels: dict[str, Any] = {}
        for k, v in item.items():
            if k in ("message", "msg"):
                continue
            labels[str(k)] = v
        records.append(
            {
                "ts": item.get("ts") or item.get("timestamp") or item.get("time"),
                "source": str(item.get("source") or item.get("host") or "webhook"),
                "kind": "log",
                "level_or_status": (
                    item.get("level") or item.get("severity") or item.get("status")
                ),
                "message": message if isinstance(message, str) else str(message),
                "value": None,
                "labels": labels,
                "raw": item,
            }
        )
    return records


def _parse_beats(payload: bytes) -> list[dict[str, Any]]:
    """Decode a Beats/Lumberjack JSON body (object or array) then normalize it.

    The transport (window framing / compression) is the shipper's concern; here we
    accept the decoded JSON events and hand them to
    :func:`general_ludd.connectors.ingest_formats.parse_beats_lumberjack`.
    """
    import json

    if not isinstance(payload, (bytes, bytearray)):
        return []
    payload = bytes(payload)
    if len(payload) > MAX_BODY_BYTES:
        return []
    try:
        doc = json.loads(payload)
    except (ValueError, TypeError):
        return []
    events = doc if isinstance(doc, list) else [doc]
    return ingest_formats.parse_beats_lumberjack(events)
