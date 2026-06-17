"""OpenShift connector for general-ludd.

A self-contained, config-driven source that reads builds, pods, events, and pod
logs from an OpenShift cluster's REST API using a ServiceAccount Bearer token.

Design notes / safety contract
-------------------------------
* **No real cluster is contacted at import time.** All HTTP goes through an
  injectable transport (see :class:`HttpTransport`); tests supply a canned one.
* **SSRF / private-network guard.** OpenShift API servers are usually internal
  (RFC-1918 / loopback / link-local), so we *reject* internal/private
  ``api_server`` hosts by default. An operator must explicitly opt in with
  ``allow_private=True`` in the config. The check is on the *literal host* in
  the configured ``api_server`` (no DNS resolution is performed) — this is a
  deliberate, conservative block, not a full anti-SSRF resolver.
* **No shell.** Nothing here ever spawns a subprocess or uses ``shell=True``.
* **time-bound.** Every request carries a timeout.
* ``health()`` never raises — it returns ``{"ok": bool, "detail": str}``.
* ``query(spec)`` returns a list of normalized records with a stable schema:
  ``ts, source, kind, level_or_status, message, value, labels, raw``.

This module imports only from the standard library and ``httpx`` (already a
project dependency). It does not import from sibling connector modules, package
``__init__``, or any base class — it is intentionally standalone.
"""

from __future__ import annotations

import ipaddress
import os
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

import httpx

__all__ = ["HttpResponse", "HttpTransport", "OpenShiftSource"]


@runtime_checkable
class HttpResponse(Protocol):
    """Minimal response shape the connector relies on.

    ``httpx.Response`` satisfies this, and tests can supply a trivial stub.
    """

    @property
    def status_code(self) -> int: ...

    @property
    def text(self) -> str: ...

    def json(self) -> Any: ...


@runtime_checkable
class HttpTransport(Protocol):
    """Injectable HTTP transport.

    The connector only ever issues GETs. The transport must accept a fully
    composed URL, a headers mapping, and a timeout (seconds), and return
    something satisfying :class:`HttpResponse`. ``httpx.Client.get`` matches
    this signature when adapted by :class:`_HttpxTransport`.
    """

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> HttpResponse: ...


class _HttpxTransport:
    """Default transport backed by :class:`httpx.Client`.

    Created lazily only when no transport is injected, so importing this module
    (and constructing a source with an injected transport, as tests do) never
    opens a real client.
    """

    def __init__(self, *, verify: bool = True) -> None:
        self._verify = verify

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> HttpResponse:
        # A fresh client per call keeps the transport stateless and avoids
        # holding sockets open between queries; connection reuse is not a goal
        # for this low-frequency observability source.
        with httpx.Client(verify=self._verify, timeout=timeout) as client:
            return client.get(url, headers=headers)


class OpenShiftConfigError(ValueError):
    """Raised for invalid / unsafe connector configuration."""


def _host_is_private(host: str) -> bool:
    """True if ``host`` is a literal internal/private address or local name.

    No DNS resolution is performed. A bare hostname that is *not* an IP literal
    is treated as private when it has no dot (e.g. ``openshift``,
    ``localhost``) or ends in a known internal-cluster suffix
    (``.local``, ``.internal``, ``.cluster.local``, ``.svc``). IP literals are
    classified with :mod:`ipaddress`.
    """
    host = host.strip().lower()
    if not host:
        return True

    # Strip IPv6 brackets if present (urlsplit leaves them on .hostname? no —
    # .hostname strips them, but be defensive for direct callers).
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # Not an IP literal — apply hostname heuristics.
        if host in {"localhost"}:
            return True
        internal_suffixes = (".local", ".internal", ".cluster.local", ".svc")
        if any(host.endswith(suffix) for suffix in internal_suffixes):
            return True
        # A single-label name (no dot) cannot be a public FQDN.
        return "." not in host

    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_unspecified
        or ip.is_multicast
    )


class OpenShiftSource:
    """Config-driven OpenShift observability source.

    Parameters (via the ``config`` mapping passed to ``__init__``):

    * ``api_server`` / ``base_url`` (str, required): the API server base URL,
      e.g. ``https://api.openshift.example.com:6443``.
    * ``namespace`` (str, required): target namespace.
    * ``token_env`` (str): name of the environment variable holding the
      ServiceAccount Bearer token. Defaults to ``OPENSHIFT_TOKEN``.
    * ``allow_private`` (bool): opt-in to allow an internal/private
      ``api_server`` host. Defaults to ``False`` (reject internal hosts).
    * ``timeout`` (float): per-request timeout in seconds. Defaults to ``30``.
    * ``verify_tls`` (bool): TLS verification for the default transport.
      Defaults to ``True`` (ignored when a transport is injected).
    * ``name`` (str): instance name. Defaults to ``"openshift"``.

    ``transport`` may be injected to bypass the real network entirely.
    """

    # Default KIND for the source. The ``query`` records carry their own
    # per-mode kind ("logs" for logs/pods/events, "pipeline" for builds), but
    # the class advertises 'logs' as its primary capability and 'pipeline' as a
    # secondary one.
    KIND: str = "logs"
    KINDS: tuple[str, ...] = ("logs", "pipeline")

    def __init__(
        self,
        config: dict[str, Any],
        *,
        transport: HttpTransport | None = None,
    ) -> None:
        api_server = config.get("api_server") or config.get("base_url")
        if not api_server or not isinstance(api_server, str):
            raise OpenShiftConfigError("config requires 'api_server' (or 'base_url')")

        parts = urlsplit(api_server.rstrip("/"))
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise OpenShiftConfigError(f"invalid api_server URL: {api_server!r}")

        self.allow_private: bool = bool(config.get("allow_private", False))
        if _host_is_private(parts.hostname) and not self.allow_private:
            raise OpenShiftConfigError(
                f"api_server host {parts.hostname!r} is internal/private; "
                "set allow_private=True to permit it (OpenShift API servers are "
                "usually internal — this is opt-in by design)"
            )

        namespace = config.get("namespace")
        if not namespace or not isinstance(namespace, str):
            raise OpenShiftConfigError("config requires 'namespace'")

        self.name: str = str(config.get("name", "openshift"))
        self.api_server: str = api_server.rstrip("/")
        self.namespace: str = namespace
        self.token_env: str = str(config.get("token_env", "OPENSHIFT_TOKEN"))
        self.timeout: float = float(config.get("timeout", 30.0))
        self._verify_tls: bool = bool(config.get("verify_tls", True))
        self._transport: HttpTransport = transport or _HttpxTransport(verify=self._verify_tls)

    # -- helpers --------------------------------------------------------------

    def _bearer(self) -> str | None:
        """Return the Bearer token from the configured env var (or ``None``)."""
        token = os.environ.get(self.token_env)
        return token or None

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        token = self._bearer()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _get(self, path: str) -> HttpResponse:
        """GET ``{api_server}{path}`` with auth + timeout."""
        url = f"{self.api_server}{path}"
        return self._transport.get(url, headers=self._headers(), timeout=self.timeout)

    @staticmethod
    def _items(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, dict):
            items = payload.get("items")
            if isinstance(items, list):
                return [it for it in items if isinstance(it, dict)]
        return []

    @staticmethod
    def _dig(obj: Any, *keys: str) -> Any:
        cur = obj
        for key in keys:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(key)
        return cur

    def _record(
        self,
        *,
        ts: Any,
        kind: str,
        level_or_status: Any,
        message: Any,
        value: Any = None,
        labels: dict[str, Any] | None = None,
        raw: Any,
    ) -> dict[str, Any]:
        return {
            "ts": ts,
            "source": self.name,
            "kind": kind,
            "level_or_status": level_or_status,
            "message": message,
            "value": value,
            "labels": labels or {},
            "raw": raw,
        }

    # -- normalizers ----------------------------------------------------------

    def _normalize_builds(self, payload: Any) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for item in self._items(payload):
            status = item.get("status") if isinstance(item.get("status"), dict) else {}
            ts = self._dig(status, "completionTimestamp") or self._dig(status, "startTimestamp")
            phase = self._dig(status, "phase")
            name = self._dig(item, "metadata", "name")
            labels = {
                "buildconfig": self._dig(item, "metadata", "labels", "buildconfig")
                or self._dig(item, "metadata", "labels", "openshift.io/build-config.name"),
                "namespace": self._dig(item, "metadata", "namespace") or self.namespace,
            }
            records.append(
                self._record(
                    ts=ts,
                    kind="pipeline",
                    level_or_status=phase,
                    message=name,
                    labels=labels,
                    raw=item,
                )
            )
        return records

    def _normalize_pods(self, payload: Any) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for item in self._items(payload):
            ts = self._dig(item, "metadata", "creationTimestamp")
            phase = self._dig(item, "status", "phase")
            name = self._dig(item, "metadata", "name")
            labels = {
                "node": self._dig(item, "spec", "nodeName"),
                "namespace": self._dig(item, "metadata", "namespace") or self.namespace,
            }
            records.append(
                self._record(
                    ts=ts,
                    kind="logs",
                    level_or_status=phase,
                    message=name,
                    labels=labels,
                    raw=item,
                )
            )
        return records

    def _normalize_events(self, payload: Any) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for item in self._items(payload):
            ts = (
                self._dig(item, "lastTimestamp")
                or self._dig(item, "eventTime")
                or self._dig(item, "firstTimestamp")
                or self._dig(item, "metadata", "creationTimestamp")
            )
            etype = self._dig(item, "type")
            reason = self._dig(item, "reason")
            msg_body = self._dig(item, "message")
            message = " ".join(p for p in (reason, msg_body) if p) or None
            labels = {
                "involvedObject.kind": self._dig(item, "involvedObject", "kind"),
                "involvedObject.name": self._dig(item, "involvedObject", "name"),
                "reason": reason,
            }
            records.append(
                self._record(
                    ts=ts,
                    kind="logs",
                    level_or_status=etype,
                    message=message,
                    labels=labels,
                    raw=item,
                )
            )
        return records

    def _normalize_logs(
        self,
        text: str,
        *,
        pod: str,
        container: str | None,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for idx, line in enumerate(text.splitlines()):
            if line == "":
                continue
            labels = {
                "pod": pod,
                "container": container,
                "namespace": self.namespace,
                "line": idx,
            }
            records.append(
                self._record(
                    ts=None,
                    kind="logs",
                    level_or_status=None,
                    message=line,
                    labels=labels,
                    raw=line,
                )
            )
        return records

    # -- public API -----------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Probe the API server's namespace endpoint. Never raises.

        Returns ``{"ok": bool, "detail": str}``.
        """
        if not self._bearer():
            return {
                "ok": False,
                "detail": f"no Bearer token in env var {self.token_env!r}",
            }
        try:
            resp = self._get(f"/api/v1/namespaces/{self.namespace}")
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"request failed: {exc}"}

        status = getattr(resp, "status_code", None)
        if status == 200:
            return {"ok": True, "detail": f"namespace {self.namespace!r} reachable"}
        return {"ok": False, "detail": f"unexpected status {status}"}

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """Run a query against the cluster and return normalized records.

        ``spec['mode']`` selects the data source:

        * ``"builds"`` -> build.openshift.io/v1 builds (kind=``pipeline``)
        * ``"pods"``   -> core/v1 pods (kind=``logs``)
        * ``"events"`` -> core/v1 events (kind=``logs``)
        * ``"logs"``   -> a single pod's log lines (kind=``logs``); requires
          ``spec['pod']``; optional ``spec['container']`` and
          ``spec['tail_lines']``.
        """
        mode = str(spec.get("mode", "")).lower()
        ns = self.namespace

        if mode == "builds":
            resp = self._get(f"/apis/build.openshift.io/v1/namespaces/{ns}/builds")
            return self._normalize_builds(_payload_or_empty(resp))

        if mode == "pods":
            resp = self._get(f"/api/v1/namespaces/{ns}/pods")
            return self._normalize_pods(_payload_or_empty(resp))

        if mode == "events":
            resp = self._get(f"/api/v1/namespaces/{ns}/events")
            return self._normalize_events(_payload_or_empty(resp))

        if mode == "logs":
            pod = spec.get("pod")
            if not pod or not isinstance(pod, str):
                raise OpenShiftConfigError("logs mode requires spec['pod']")
            container = spec.get("container")
            tail_lines = spec.get("tail_lines")
            qs: list[str] = []
            if container:
                qs.append(f"container={container}")
            if tail_lines is not None:
                qs.append(f"tailLines={int(tail_lines)}")
            query_str = ("?" + "&".join(qs)) if qs else ""
            resp = self._get(f"/api/v1/namespaces/{ns}/pods/{pod}/log{query_str}")
            text = getattr(resp, "text", "") or ""
            return self._normalize_logs(
                text,
                pod=pod,
                container=container if isinstance(container, str) else None,
            )

        raise OpenShiftConfigError(f"unknown spec['mode']: {mode!r}")


def _payload_or_empty(resp: HttpResponse) -> Any:
    """Parse JSON from a response, returning ``{}`` on non-200 or parse error."""
    if getattr(resp, "status_code", None) != 200:
        return {}
    try:
        return resp.json()
    except Exception:  # tolerate malformed bodies
        return {}
