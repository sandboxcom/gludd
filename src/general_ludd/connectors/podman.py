"""Podman log/event connector (Docker-compatible API).

Podman exposes a Docker-compatible REST API on ``unix:///run/podman/podman.sock``
under the ``/v1.x.x/...`` *compat* namespace (and also un-versioned ``/...``).
The container-list, container-logs and events endpoints have the same response
shapes as the Docker Engine API, so this connector reuses the same
normalization. It is fully self-contained — no imports from sibling connectors.

Compat endpoints used (Podman serves both ``/<path>`` and ``/v4.0.0/libpod``;
we use the Docker-compatible un-versioned forms):
  * ``GET /containers/json``                         (list / ps)
  * ``GET /containers/{id}/logs?...&timestamps=1``   (multiplexed/timestamped)
  * ``GET /events?since=...``                        (lifecycle events)

Contract:
  * class attr ``KIND = "logs"``
  * ``name`` instance attr
  * ``__init__(config)`` — config-driven; ``base_url`` default is the Podman
    socket; a TCP base_url triggers a literal-host SSRF block (no DNS).
  * ``health() -> dict`` — never raises; ``{"ok": bool, "detail": str}``.
  * ``query(spec) -> list[dict]`` — normalized records.
"""

from __future__ import annotations

import ipaddress
import json
import socket
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit


# --------------------------------------------------------------------------- #
# Transport abstraction
# --------------------------------------------------------------------------- #
@dataclass
class Response:
    """Minimal HTTP response container returned by a transport."""

    status: int
    headers: dict[str, str] = field(repr=False)
    body: bytes


@runtime_checkable
class Transport(Protocol):
    """Injectable HTTP transport (Unix socket or TCP). Tests inject a fake."""

    def __call__(
        self,
        method: str,
        path: str,
        query: dict[str, Any] | None,
        base_url: str,
        timeout: float,
    ) -> Response: ...


# --------------------------------------------------------------------------- #
# SSRF guard (literal-host only, no DNS)
# --------------------------------------------------------------------------- #
def _is_internal_literal_host(host: str) -> bool:
    """True if ``host`` is a literal internal/loopback/private address.

    No DNS resolution is performed: only the literal string is inspected. A
    non-IP hostname is treated as blocked because we refuse to resolve names.
    """
    h = host.strip().lower()
    if not h:
        return True
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    if h in {"localhost", "ip6-localhost", "ip6-loopback"}:
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return True
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _default_transport(
    method: str,
    path: str,
    query: dict[str, Any] | None,
    base_url: str,
    timeout: float,
) -> Response:
    """Stdlib HTTP/1.1 transport over a Unix socket or TCP (no shell)."""
    from urllib.parse import urlencode

    full_path = path
    if query:
        full_path = f"{path}?{urlencode(query)}"

    if base_url.startswith("unix://"):
        sock_path = base_url[len("unix://") :]
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        host_header = "localhost"
        connect_arg: Any = sock_path
    else:
        parts = urlsplit(base_url)
        host = parts.hostname or ""
        port = parts.port or (443 if parts.scheme == "https" else 8080)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        host_header = host
        connect_arg = (host, port)

    sock.settimeout(timeout)
    try:
        sock.connect(connect_arg)
        request = (
            f"{method} {full_path} HTTP/1.1\r\n"
            f"Host: {host_header}\r\n"
            "Connection: close\r\n"
            "Accept: application/json\r\n\r\n"
        )
        sock.sendall(request.encode("ascii"))
        chunks: list[bytes] = []
        while True:
            data = sock.recv(65536)
            if not data:
                break
            chunks.append(data)
    finally:
        sock.close()

    raw = b"".join(chunks)
    header_blob, _, body = raw.partition(b"\r\n\r\n")
    header_lines = header_blob.split(b"\r\n")
    status = 0
    if header_lines:
        status_parts = header_lines[0].split(b" ", 2)
        if len(status_parts) >= 2 and status_parts[1].isdigit():
            status = int(status_parts[1])
    headers: dict[str, str] = {}
    for line in header_lines[1:]:
        key, sep, value = line.partition(b":")
        if sep:
            headers[key.decode("latin-1").strip().lower()] = value.decode("latin-1").strip()
    return Response(status=status, headers=headers, body=body)


# --------------------------------------------------------------------------- #
# Normalization helpers
# --------------------------------------------------------------------------- #
def _record(
    *,
    ts: str | None,
    source: str,
    level_or_status: str | None,
    message: str,
    value: Any,
    labels: dict[str, Any],
    raw: Any,
) -> dict[str, Any]:
    """Build one normalized record in the canonical contract shape."""
    return {
        "ts": ts,
        "source": source,
        "kind": "logs",
        "level_or_status": level_or_status,
        "message": message,
        "value": value,
        "labels": labels,
        "raw": raw,
    }


def _split_rfc3339(line: str) -> tuple[str | None, str]:
    """Split ``"<rfc3339-ts> <message>"`` into (ts, message)."""
    line = line.rstrip("\r\n")
    if not line:
        return None, ""
    head, sep, rest = line.partition(" ")
    if sep and _looks_rfc3339(head):
        return head, rest
    return None, line


def _looks_rfc3339(token: str) -> bool:
    """Cheap RFC3339 sniff: ``YYYY-MM-DDT...``."""
    if len(token) < 20 or "T" not in token:
        return False
    return token[4] == "-" and token[7] == "-"


_STREAM_NAMES = {0: "stdin", 1: "stdout", 2: "stderr"}


def _iter_log_payload(body: bytes) -> list[tuple[str, str]]:
    """Yield ``(stream_name, raw_line)`` from a Docker-compatible log payload.

    Podman, like Docker, multiplexes stdout/stderr into 8-byte-framed chunks
    for non-TTY containers and emits plain bytes for TTY containers.
    """
    out: list[tuple[str, str]] = []
    if _is_multiplexed(body):
        offset = 0
        n = len(body)
        while offset + 8 <= n:
            stream_byte = body[offset]
            size = int.from_bytes(body[offset + 4 : offset + 8], "big")
            offset += 8
            frame = body[offset : offset + size]
            offset += size
            stream = _STREAM_NAMES.get(stream_byte, "stdout")
            for raw_line in frame.split(b"\n"):
                if raw_line:
                    out.append((stream, raw_line.decode("utf-8", "replace")))
    else:
        for raw_line in body.split(b"\n"):
            if raw_line:
                out.append(("stdout", raw_line.decode("utf-8", "replace")))
    return out


def _is_multiplexed(body: bytes) -> bool:
    """Heuristic: payload starts with a valid 8-byte stream-frame header."""
    if len(body) < 8:
        return False
    if body[0] not in (0, 1, 2):
        return False
    if body[1] != 0 or body[2] != 0 or body[3] != 0:
        return False
    size = int.from_bytes(body[4:8], "big")
    return 8 + size <= len(body) or size > 0


# --------------------------------------------------------------------------- #
# Connector
# --------------------------------------------------------------------------- #
class PodmanSource:
    """Podman connector over the Docker-compatible API (logs / events / ps)."""

    KIND = "logs"

    _DEFAULT_BASE_URL = "unix:///run/podman/podman.sock"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        config = config or {}
        self.name: str = str(config.get("name", "podman"))
        self.base_url: str = str(config.get("base_url", self._DEFAULT_BASE_URL))
        self.timeout: float = float(config.get("timeout", 10.0))
        transport = config.get("transport")
        self._transport: Transport = transport if callable(transport) else _default_transport
        self._ssrf_error: str | None = self._check_base_url(self.base_url)

    # -- base_url / SSRF validation ---------------------------------------- #
    def _check_base_url(self, base_url: str) -> str | None:
        if base_url.startswith("unix://"):
            return None
        parts = urlsplit(base_url)
        host = parts.hostname or ""
        if _is_internal_literal_host(host):
            return f"refused TCP base_url to internal/literal host: {host!r}"
        return None

    # -- HTTP plumbing ----------------------------------------------------- #
    def _get(self, path: str, query: dict[str, Any] | None = None) -> Response:
        return self._transport("GET", path, query, self.base_url, self.timeout)

    def _get_json(self, path: str, query: dict[str, Any] | None = None) -> Any:
        resp = self._get(path, query)
        if resp.status < 200 or resp.status >= 300:
            raise RuntimeError(f"HTTP {resp.status} for {path}")
        if not resp.body:
            return []
        return json.loads(resp.body.decode("utf-8"))

    # -- health ------------------------------------------------------------ #
    def health(self) -> dict[str, Any]:
        """Probe the Podman compat ``/_ping`` endpoint; never raises."""
        if self._ssrf_error is not None:
            return {"ok": False, "detail": self._ssrf_error}
        try:
            resp = self._get("/_ping")
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        ok = 200 <= resp.status < 300
        detail = "ok" if ok else f"unexpected status {resp.status}"
        return {"ok": ok, "detail": detail}

    # -- query ------------------------------------------------------------- #
    def query(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Dispatch on ``spec['mode']`` (ps | logs | events)."""
        if self._ssrf_error is not None:
            raise RuntimeError(self._ssrf_error)
        spec = spec or {}
        mode = str(spec.get("mode", "ps"))
        if mode == "ps":
            return self._query_ps(spec)
        if mode == "logs":
            return self._query_logs(spec)
        if mode == "events":
            return self._query_events(spec)
        raise ValueError(f"unknown mode: {mode!r}")

    # -- ps ---------------------------------------------------------------- #
    def _query_ps(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        query: dict[str, Any] = {}
        if spec.get("all"):
            query["all"] = "1"
        payload = self._get_json("/containers/json", query or None)
        records: list[dict[str, Any]] = []
        for entry in payload:
            names = entry.get("Names") or []
            container_name = names[0].lstrip("/") if names else ""
            cid = str(entry.get("Id", ""))
            state = entry.get("State")
            status = entry.get("Status", "")
            image = entry.get("Image", "")
            records.append(
                _record(
                    ts=None,
                    source=self.name,
                    level_or_status=str(state) if state is not None else None,
                    message=f"{container_name} {status}".strip(),
                    value=None,
                    labels={
                        "container_id": cid,
                        "container_name": container_name,
                        "image": image,
                    },
                    raw=entry,
                )
            )
        return records

    # -- logs -------------------------------------------------------------- #
    def _query_logs(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        cid = spec.get("container_id") or spec.get("id")
        if not cid:
            raise ValueError("logs mode requires spec['container_id']")
        container_name = str(spec.get("container_name", ""))
        tail = spec.get("tail", 100)
        query = {
            "stdout": "1",
            "stderr": "1",
            "timestamps": "1",
            "tail": str(tail),
        }
        resp = self._get(f"/containers/{cid}/logs", query)
        if resp.status < 200 or resp.status >= 300:
            raise RuntimeError(f"HTTP {resp.status} for logs of {cid}")
        records: list[dict[str, Any]] = []
        for stream, line in _iter_log_payload(resp.body):
            ts, message = _split_rfc3339(line)
            records.append(
                _record(
                    ts=ts,
                    source=self.name,
                    level_or_status=stream,
                    message=message,
                    value=None,
                    labels={
                        "container_id": str(cid),
                        "container_name": container_name,
                        "stream": stream,
                    },
                    raw=line,
                )
            )
        return records

    # -- events ------------------------------------------------------------ #
    def _query_events(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        query: dict[str, Any] = {}
        since = spec.get("since")
        if since is not None:
            query["since"] = str(since)
        until = spec.get("until")
        if until is not None:
            query["until"] = str(until)
        resp = self._get("/events", query or None)
        if resp.status < 200 or resp.status >= 300:
            raise RuntimeError(f"HTTP {resp.status} for /events")
        return self._parse_events(resp.body)

    def _parse_events(self, body: bytes) -> list[dict[str, Any]]:
        """Parse a newline-delimited JSON event stream into records."""
        records: list[dict[str, Any]] = []
        for raw_line in body.split(b"\n"):
            if not raw_line.strip():
                continue
            event = json.loads(raw_line.decode("utf-8"))
            etype = str(event.get("Type", ""))
            action = str(event.get("Action", ""))
            ts = self._event_ts(event)
            actor = event.get("Actor") or {}
            attributes = actor.get("Attributes") or {}
            records.append(
                _record(
                    ts=ts,
                    source=self.name,
                    level_or_status=action,
                    message=f"{etype} {action}".strip(),
                    value=None,
                    labels={
                        "id": str(event.get("id", actor.get("ID", ""))),
                        "from": str(event.get("from", attributes.get("image", ""))),
                    },
                    raw=event,
                )
            )
        return records

    @staticmethod
    def _event_ts(event: dict[str, Any]) -> str | None:
        """Prefer nanosecond ``timeNano`` then second ``time`` epoch fields."""
        from datetime import UTC, datetime

        nano = event.get("timeNano")
        if isinstance(nano, (int, float)) and nano:
            return datetime.fromtimestamp(nano / 1e9, tz=UTC).isoformat()
        secs = event.get("time")
        if isinstance(secs, (int, float)) and secs:
            return datetime.fromtimestamp(secs, tz=UTC).isoformat()
        return None


# Convenience alias for callers that prefer a generic name.
Connector: Callable[..., PodmanSource] = PodmanSource
