"""Docker Engine API log/event connector.

Self-contained connector for the Docker Engine HTTP API. Talks to the engine
over a Unix domain socket (default) or a TCP host, using an *injectable* HTTP
transport so the connector can be unit-tested without a real daemon or network.

Contract:
  * class attr ``KIND = "logs"``
  * ``name`` instance attr
  * ``__init__(config)`` — config-driven, ``base_url`` default points at the
    local Docker socket; if a TCP base_url is given, a literal-host SSRF block
    is applied (no DNS resolution — the literal host/IP is checked directly).
  * ``health() -> dict`` — never raises; returns ``{"ok": bool, "detail": str}``.
  * ``query(spec) -> list[dict]`` — returns normalized records with keys
    ``ts, source, kind, level_or_status, message, value, labels, raw``.

The HTTP transport is a simple callable injected via ``config["transport"]``.
It is called as ``transport(method, path, query, base_url, timeout)`` and must
return a ``Response`` (``.status``, ``.headers``, ``.body`` bytes). A default
transport built on the stdlib is provided for real use; tests inject a fake.
"""

from __future__ import annotations

import ipaddress
import json
import socket
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit


# --------------------------------------------------------------------------- #
# Transport abstraction
# --------------------------------------------------------------------------- #
@dataclass
class Response:
    """Minimal HTTP response container returned by a transport."""

    status: int
    headers: dict[str, str]
    body: bytes


@runtime_checkable
class Transport(Protocol):
    """Injectable HTTP transport.

    Implementations must work over a Unix socket *or* a TCP host depending on
    ``base_url``. Tests supply a fake that returns canned :class:`Response`
    objects keyed off ``method``/``path``.
    """

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
    """Return True if ``host`` is a *literal* internal/loopback/private address.

    No DNS resolution is performed: only the literal string is inspected. A bare
    hostname (non-IP literal) is treated as internal-blocked unless it is an
    explicit public IP, because we refuse to resolve names (SSRF safety).
    """
    h = host.strip().lower()
    if not h:
        return True
    # Strip IPv6 brackets if present.
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    if h in {"localhost", "ip6-localhost", "ip6-loopback"}:
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        # Not an IP literal. We do not resolve DNS; refuse non-IP TCP hosts.
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
    """Stdlib HTTP/1.1 transport over a Unix socket or TCP.

    Used for real connections; unit tests inject a fake instead. Implemented
    with raw sockets so it can speak HTTP over an ``AF_UNIX`` socket (which
    ``http.client`` does not support out of the box). Never uses a shell.
    """
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
        port = parts.port or (443 if parts.scheme == "https" else 2375)
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
# Normalization helpers (shared shape for logs / events / ps)
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
    """Split a ``"<rfc3339-ts> <message>"`` line into (ts, message).

    Docker writes log lines as ``2024-01-02T03:04:05.123456789Z message...``
    when ``timestamps=1``. The timestamp is the first whitespace-delimited
    token; everything after the first space is the message.
    """
    line = line.rstrip("\r\n")
    if not line:
        return None, ""
    head, sep, rest = line.partition(" ")
    if sep and _looks_rfc3339(head):
        return head, rest
    return None, line


def _looks_rfc3339(token: str) -> bool:
    """Cheap RFC3339 sniff: ``YYYY-MM-DDT...`` with date separators."""
    if len(token) < 20 or "T" not in token:
        return False
    return token[4] == "-" and token[7] == "-"


_STREAM_NAMES = {0: "stdin", 1: "stdout", 2: "stderr"}


def _iter_log_payload(body: bytes) -> list[tuple[str, str]]:
    """Yield ``(stream_name, raw_line)`` pairs from a Docker log payload.

    Docker multiplexes stdout/stderr into a framed stream when the container has
    no TTY: each frame is an 8-byte header ``[stream, 0,0,0, size(4 BE)]``
    followed by ``size`` bytes. When a TTY is attached the payload is plain
    bytes with no framing. We detect framing heuristically and fall back to a
    plain newline split.
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
    """Heuristic: a multiplexed payload starts with a valid 8-byte frame header.

    Header byte 0 is the stream id (0/1/2), bytes 1-3 are zero padding, and the
    declared frame size must not overrun the buffer.
    """
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
class DockerEngineSource:
    """Docker Engine API connector (logs / events / ps).

    ``query`` dispatches on ``spec["mode"]``:
      * ``"ps"``     -> GET /containers/json
      * ``"logs"``   -> GET /containers/{id}/logs?stdout=1&stderr=1&timestamps=1&tail=N
      * ``"events"`` -> GET /events?since=<ts>
    """

    KIND = "logs"

    _DEFAULT_BASE_URL = "unix:///var/run/docker.sock"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        config = config or {}
        self.name: str = str(config.get("name", "docker-engine"))
        self.base_url: str = str(config.get("base_url", self._DEFAULT_BASE_URL))
        self.timeout: float = float(config.get("timeout", 10.0))
        transport = config.get("transport")
        self._transport: Transport = transport if callable(transport) else _default_transport
        self._ssrf_error: str | None = self._check_base_url(self.base_url)

    # -- base_url / SSRF validation ---------------------------------------- #
    def _check_base_url(self, base_url: str) -> str | None:
        """Return an error string if a TCP base_url points at an internal host."""
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
        """Probe the engine; never raises."""
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
        """Dispatch on ``spec['mode']`` and return normalized records."""
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
Connector: Callable[..., DockerEngineSource] = DockerEngineSource
