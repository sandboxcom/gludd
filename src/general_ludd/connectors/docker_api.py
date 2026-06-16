"""Docker Engine API log/event connector.

A self-contained :class:`DockerApiSource` that talks to the Docker Engine API
through an *injected* HTTP transport — over a confined UNIX socket (default) or a
TCP host (SSRF-guarded). It normalizes container listings, container log lines,
and engine events into the shared record shape
(``ts``/``source``/``kind``/``level_or_status``/``message``/``value``/``labels``/``raw``).

Endpoints used
--------------
- ``GET /containers/json`` — container states (running/exited/...).
- ``GET /containers/{id}/logs`` — a container's stdout/stderr log lines.
- ``GET /events`` — engine lifecycle events (create/start/die/...).

Safety
------
- **Socket confinement.** A configured ``socket_path`` must resolve under an
  allow-listed prefix (default ``/var/run`` / ``/run``); a path escaping it
  (``..`` traversal, arbitrary absolute path) is rejected at construction.
- **TCP SSRF guard.** A ``tcp_host`` URL is rejected when its literal host is a
  loopback / private / link-local / metadata target, *unless* ``allow_local`` is
  explicitly set (operator opt-in for a local daemon).
- **health() never raises.** Failures become ``{"ok": False, "detail": ...}``.

The transport is a structural callable so tests inject canned JSON; nothing here
performs real network I/O during tests, and no sibling connector is imported.
"""

from __future__ import annotations

import ipaddress
import json
import os
from typing import Any, Protocol
from urllib.parse import urlsplit

KIND_LOGS = "logs"
KIND_EVENTS = "events"

# Default class KIND (logs); a query selects logs vs events per-call.
KIND = KIND_LOGS

# Socket paths must live under one of these roots (no traversal outside).
_ALLOWED_SOCKET_ROOTS = ("/var/run", "/run")

# Docker event status -> coarse level.
_EVENT_LEVEL = {
    "die": "error",
    "kill": "error",
    "oom": "error",
    "destroy": "warning",
    "stop": "warning",
}


class HttpTransport(Protocol):
    """Structural contract for the injected Docker Engine HTTP transport.

    ``get(path, params)`` performs an HTTP GET against the Engine API (over the
    confined socket or TCP host the source was configured with) and returns the
    decoded text body. Tests inject a fake returning canned Engine-API JSON and
    record the ``(path, params)`` it was called with.
    """

    def get(self, path: str, params: dict[str, Any] | None = None) -> str:
        ...


def _is_blocked_host(host: str) -> bool:
    """Return True if a literal host is an internal/metadata SSRF target."""
    host_l = host.lower()
    if host_l in {"localhost", "localhost.localdomain", "metadata", "metadata.google.internal"}:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False  # a DNS name we do not resolve — not literally blocked
    return bool(
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


class DockerApiSource:
    """Read + normalize Docker Engine container/log/event data."""

    KIND: str = KIND

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        transport: HttpTransport | None = None,
    ) -> None:
        config = config or {}
        self.name: str = str(config.get("name", "docker"))
        self._config = config
        self._allow_local = bool(config.get("allow_local", False))

        tcp_host = config.get("tcp_host")
        socket_path = config.get("socket_path", "/var/run/docker.sock")

        # Exactly one transport target is confined here. TCP takes precedence
        # when provided; otherwise the UNIX socket path is validated.
        if tcp_host is not None:
            self._target = self._confine_tcp(str(tcp_host))
            self._socket_path: str | None = None
        else:
            self._socket_path = self._confine_socket(str(socket_path))
            self._target = f"unix://{self._socket_path}"

        self._transport: HttpTransport = transport if transport is not None else _default_transport(self._target)
        self._default_tail = int(config.get("tail", 200))

    # -- confinement ------------------------------------------------------- #
    @staticmethod
    def _confine_socket(socket_path: str) -> str:
        norm = os.path.normpath(socket_path)
        if not os.path.isabs(norm):
            raise ValueError(f"socket_path must be absolute: {socket_path!r}")
        if ".." in norm.split(os.sep):
            raise ValueError(f"socket_path traversal rejected: {socket_path!r}")
        if not any(norm == root or norm.startswith(root + os.sep) for root in _ALLOWED_SOCKET_ROOTS):
            raise ValueError(
                f"socket_path {socket_path!r} escapes allowed roots {_ALLOWED_SOCKET_ROOTS}"
            )
        return norm

    def _confine_tcp(self, tcp_host: str) -> str:
        url = tcp_host if "://" in tcp_host else f"http://{tcp_host}"
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            raise ValueError(f"tcp_host scheme must be http(s): {tcp_host!r}")
        host = parts.hostname
        if not host:
            raise ValueError(f"tcp_host has no host: {tcp_host!r}")
        if _is_blocked_host(host) and not self._allow_local:
            raise ValueError(
                f"tcp_host {host!r} is a local/internal target; set allow_local=True to permit it"
            )
        return url

    # -- normalization helpers -------------------------------------------- #
    def _record(
        self,
        *,
        kind: str,
        message: str,
        ts: float | None,
        level_or_status: str,
        labels: dict[str, Any],
        raw: Any,
    ) -> dict[str, Any]:
        return {
            "ts": ts,
            "source": self.name,
            "kind": kind,
            "level_or_status": level_or_status,
            "message": message,
            "value": None,
            "labels": labels,
            "raw": raw,
        }

    @staticmethod
    def _container_labels(name: str | None, image: str | None, cid: str | None) -> dict[str, Any]:
        return {"container": cid, "image": image, "name": name}

    # -- containers -------------------------------------------------------- #
    def _query_containers(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"all": "true" if spec.get("all", True) else "false"}
        body = self._transport.get("/containers/json", params)
        data = self._loads(body)
        records: list[dict[str, Any]] = []
        if not isinstance(data, list):
            return records
        for c in data:
            if not isinstance(c, dict):
                continue
            names = c.get("Names") or []
            name = names[0].lstrip("/") if names else c.get("Name")
            state = str(c.get("State", "unknown"))
            records.append(
                self._record(
                    kind=KIND_LOGS,
                    message=f"container {name} state={state}",
                    ts=self._to_float(c.get("Created")),
                    level_or_status=state,
                    labels=self._container_labels(name, c.get("Image"), c.get("Id")),
                    raw=c,
                )
            )
        return records

    # -- logs -------------------------------------------------------------- #
    def _query_logs(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        cid = spec.get("container")
        if not cid:
            raise ValueError("logs query requires a 'container' id")
        safe_id = self._safe_id(str(cid))
        params: dict[str, Any] = {
            "stdout": "true",
            "stderr": "true",
            "tail": str(int(spec.get("tail", self._default_tail))),
        }
        if "since" in spec:
            params["since"] = str(int(spec["since"]))
        body = self._transport.get(f"/containers/{safe_id}/logs", params)
        records: list[dict[str, Any]] = []
        for raw_line in body.splitlines():
            line = raw_line.rstrip("\n")
            if not line:
                continue
            level = "error" if "stderr" in spec.get("_stream_hint", "") else "info"
            records.append(
                self._record(
                    kind=KIND_LOGS,
                    message=line,
                    ts=None,
                    level_or_status=level,
                    labels=self._container_labels(None, None, safe_id),
                    raw=line,
                )
            )
        return records

    # -- events ------------------------------------------------------------ #
    def _query_events(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if "since" in spec:
            params["since"] = str(int(spec["since"]))
        if "until" in spec:
            params["until"] = str(int(spec["until"]))
        body = self._transport.get("/events", params)
        records: list[dict[str, Any]] = []
        # /events streams newline-delimited JSON objects.
        for raw_line in body.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(ev, dict):
                continue
            status = str(ev.get("status") or ev.get("Action") or "event")
            actor = ev.get("Actor") or {}
            attrs = actor.get("Attributes") if isinstance(actor, dict) else {}
            attrs = attrs if isinstance(attrs, dict) else {}
            cid = ev.get("id") or ev.get("Actor", {}).get("ID") if isinstance(ev.get("Actor"), dict) else ev.get("id")
            records.append(
                self._record(
                    kind=KIND_EVENTS,
                    message=f"{ev.get('Type', 'container')} {status}",
                    ts=self._to_float(ev.get("time")),
                    level_or_status=_EVENT_LEVEL.get(status, "info"),
                    labels=self._container_labels(attrs.get("name"), attrs.get("image"), cid),
                    raw=ev,
                )
            )
        return records

    # -- public API -------------------------------------------------------- #
    def query(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Dispatch a query by ``spec['kind']`` (``logs`` default, or ``events``).

        For ``logs``: if a ``container`` id is given, fetch that container's log
        lines; otherwise list container states. For ``events``: fetch engine
        events. Time-bounding (``since``/``until``/``tail``) is honored.
        """
        spec = spec or {}
        kind = spec.get("kind", KIND_LOGS)
        if kind == KIND_EVENTS:
            return self._query_events(spec)
        if spec.get("container"):
            return self._query_logs(spec)
        return self._query_containers(spec)

    def health(self) -> dict[str, Any]:
        """Probe the Engine API (``GET /containers/json``). MUST NOT raise."""
        try:
            body = self._transport.get("/containers/json", {"limit": "1"})
            ok = body is not None
            return {"ok": ok, "detail": f"docker engine reachable via {self._target}"}
        except Exception as exc:  # never raise out of health()
            return {"ok": False, "detail": f"docker engine unavailable: {exc}"}

    # -- small utils ------------------------------------------------------- #
    @staticmethod
    def _safe_id(cid: str) -> str:
        if not cid or cid[0] == "-" or not all(c.isalnum() or c in "_.-" for c in cid):
            raise ValueError(f"unsafe container id: {cid!r}")
        return cid

    @staticmethod
    def _loads(body: str) -> Any:
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


def _default_transport(target: str) -> HttpTransport:
    """Real transport over the configured UNIX socket or TCP host.

    Imported lazily and constructed only when no transport is injected (tests
    always inject their own), so the module stays import-light and never opens a
    real connection during unit tests.
    """

    class _RealTransport:
        def __init__(self, target: str) -> None:
            self._target = target

        def get(self, path: str, params: dict[str, Any] | None = None) -> str:
            import urllib.parse
            import urllib.request

            query = ("?" + urllib.parse.urlencode(params)) if params else ""
            if self._target.startswith("unix://"):
                # Engine API over a UNIX socket via a minimal raw HTTP request.
                import http.client
                import socket as _socket

                sock_path = self._target[len("unix://"):]
                conn = http.client.HTTPConnection("localhost")
                conn.sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
                conn.sock.connect(sock_path)
                conn.request("GET", path + query)
                resp = conn.getresponse()
                data = resp.read().decode("utf-8", "replace")
                conn.close()
                return data
            url = self._target.rstrip("/") + path + query
            with urllib.request.urlopen(url, timeout=30) as resp:  # http(s) confined above
                body: str = resp.read().decode("utf-8", "replace")
                return body

    return _RealTransport(target)
