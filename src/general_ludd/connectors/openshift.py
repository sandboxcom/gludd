"""OpenShift / Kubernetes connector — `OpenShiftSource`.

Self-contained connector that reads events, routes and pod logs from an
OpenShift (or vanilla Kubernetes) cluster in EITHER of two injected modes:

  * HTTP transport mode — direct REST against the API server:
        GET {api}/api/v1/events
        GET {api}/apis/route.openshift.io/v1/routes
        GET {api}/api/v1/namespaces/{ns}/pods/{pod}/log
    Bearer token comes from the env var NAMED by config['token_env'].

  * Runner mode — shelling `oc` / `kubectl` (argv list, no shell) via an
    injected runner that returns (rc, stdout, stderr).

All records normalize to the gludd connector record shape:

    {ts, source, kind, level_or_status, message, value, labels, raw}

Security / contract notes:
  * KIND class attribute = 'events' (a logs query yields kind='logs' records).
  * SSRF guard on the API base_url uses a LITERAL host block list (no DNS);
    clusters are internal so it is opt-in via config['allow_private'].
  * Bearer token is resolved only from the environment; never stored in config.
  * Runner receives an argv LIST — never a shell string, never shell=True.
  * `health()` NEVER raises — returns {'ok': bool, 'detail': str}.
  * Pod-log reads are time/size-bound (sinceSeconds / tailLines).

No imports from sibling connectors or any gludd base module.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
from typing import Any, Protocol
from urllib.parse import urlsplit


class _Response(Protocol):
    status_code: int
    text: str

    def json(self) -> Any: ...


class _Transport(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
        verify: str | bool = True,
    ) -> _Response: ...


class _Runner(Protocol):
    def __call__(self, argv: list[str]) -> tuple[int, str, str]: ...


_BLOCKED_NETS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]
_BLOCKED_HOSTNAMES = {"localhost", "metadata", "metadata.google.internal"}

_TYPE_LEVEL = {"Normal": "info", "Warning": "warning"}

# RFC1123 label, used to validate namespace / pod / route names before they go
# into a URL path or an `oc` argv.
_K8S_NAME = re.compile(r"[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?")


class SSRFError(ValueError):
    """Raised when a base_url host is blocked by the literal SSRF guard."""


def host_is_blocked(host: str) -> bool:
    """True if `host` is a literal private/loopback/metadata target (no DNS)."""
    h = host.strip("[]").lower()
    if h in _BLOCKED_HOSTNAMES:
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    return any(ip in net for net in _BLOCKED_NETS)


def assert_url_allowed(base_url: str, *, allow_private: bool) -> None:
    """Fail-closed SSRF guard on the literal host of `base_url`."""
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https"):
        raise SSRFError(f"unsupported URL scheme: {parts.scheme!r}")
    host = parts.hostname or ""
    if not host:
        raise SSRFError(f"base_url has no host: {base_url!r}")
    if not allow_private and host_is_blocked(host):
        raise SSRFError(
            f"refusing internal host {host!r} (set allow_private=True for clusters)"
        )


def token_from_env(env_key: str | None) -> str | None:
    """Resolve the Bearer token strictly from the named environment variable."""
    if not env_key:
        return None
    return os.environ.get(env_key)


def _sanitize_name(value: str, kind: str) -> str:
    """Validate a k8s/openshift object name before URL/argv interpolation."""
    if not _K8S_NAME.fullmatch(value):
        raise ValueError(f"invalid {kind} name: {value!r}")
    return value


class OpenShiftSource:
    """Connector over the OpenShift / Kubernetes API (HTTP or `oc` runner)."""

    KIND = "events"

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        transport: _Transport | None = None,
        runner: _Runner | None = None,
    ) -> None:
        self.config: dict[str, Any] = dict(config or {})
        self.name: str = str(self.config.get("name", "openshift"))
        self._transport = transport
        self._runner = runner
        self._base_url: str = str(self.config.get("base_url", "")).rstrip("/")
        self._allow_private: bool = bool(self.config.get("allow_private", False))
        self._token_env: str | None = self.config.get("token_env")
        self._ca_cert: str | bool = self.config.get("ca_cert", True)
        self._namespace: str | None = self.config.get("namespace")
        self._limit: int = int(self.config.get("limit", 500))
        self._timeout_seconds: int = int(self.config.get("timeout_seconds", 30))
        self._oc_binary: str = str(self.config.get("oc_binary", "oc"))
        # "http" (default when a transport is given) or "runner".
        self._mode: str = str(
            self.config.get("mode", "runner" if runner and not transport else "http")
        )
        if self._mode == "http" and self._base_url:
            assert_url_allowed(self._base_url, allow_private=self._allow_private)

    # -- HTTP helpers --------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        token = token_from_env(self._token_env)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _events_url(self) -> str:
        if self._namespace:
            ns = _sanitize_name(self._namespace, "namespace")
            return f"{self._base_url}/api/v1/namespaces/{ns}/events"
        return f"{self._base_url}/api/v1/events"

    def _routes_url(self) -> str:
        base = f"{self._base_url}/apis/route.openshift.io/v1"
        if self._namespace:
            ns = _sanitize_name(self._namespace, "namespace")
            return f"{base}/namespaces/{ns}/routes"
        return f"{base}/routes"

    def _pod_log_url(self, namespace: str, pod: str) -> str:
        ns = _sanitize_name(namespace, "namespace")
        p = _sanitize_name(pod, "pod")
        return f"{self._base_url}/api/v1/namespaces/{ns}/pods/{p}/log"

    # -- normalization -------------------------------------------------------

    def _normalize_event(self, event: dict[str, Any]) -> dict[str, Any]:
        ev_type = event.get("type", "Normal")
        level = _TYPE_LEVEL.get(ev_type, "info")
        involved = event.get("involvedObject", {}) or {}
        metadata = event.get("metadata", {}) or {}
        ts = (
            event.get("lastTimestamp")
            or event.get("eventTime")
            or event.get("firstTimestamp")
            or metadata.get("creationTimestamp")
        )
        labels = {
            "namespace": involved.get("namespace") or metadata.get("namespace"),
            "kind": involved.get("kind"),
            "name": involved.get("name"),
            "reason": event.get("reason"),
        }
        return {
            "ts": ts,
            "source": self.name,
            "kind": "events",
            "level_or_status": level,
            "message": event.get("message", ""),
            "value": event.get("count"),
            "labels": {k: v for k, v in labels.items() if v is not None},
            "raw": event,
        }

    def _normalize_log_line(
        self, line: str, namespace: str | None, pod: str | None
    ) -> dict[str, Any]:
        labels = {"namespace": namespace, "kind": "Pod", "name": pod}
        return {
            "ts": None,
            "source": self.name,
            "kind": "logs",
            "level_or_status": "info",
            "message": line,
            "value": None,
            "labels": {k: v for k, v in labels.items() if v is not None},
            "raw": {"line": line, "namespace": namespace, "pod": pod},
        }

    # -- public API: events --------------------------------------------------

    def query(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Dispatch by spec['resource'] in {'events','logs'} (default events)."""
        spec = dict(spec or {})
        resource = str(spec.get("resource", "events"))
        if resource == "logs":
            return self.query_pod_logs(spec)
        return self.query_events(spec)

    def query_events(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        spec = dict(spec or {})
        if self._mode == "runner":
            return self._query_events_runner(spec)
        return self._query_events_http(spec)

    def _query_events_http(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        if self._transport is None:
            raise RuntimeError("http mode requires an injected transport")
        base = str(spec.get("base_url", self._base_url)).rstrip("/")
        assert_url_allowed(base, allow_private=self._allow_private)
        params: dict[str, Any] = {
            "limit": int(spec.get("limit", self._limit)),
            "timeoutSeconds": int(spec.get("timeout_seconds", self._timeout_seconds)),
        }
        resp = self._transport.get(
            self._events_url(),
            headers=self._headers(),
            params=params,
            verify=self._ca_cert,
        )
        if resp.status_code != 200:
            return []
        payload = resp.json()
        items = payload.get("items", []) if isinstance(payload, dict) else []
        return [self._normalize_event(e) for e in items if isinstance(e, dict)]

    def _query_events_runner(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        if self._runner is None:
            raise RuntimeError("runner mode requires an injected runner")
        argv = [self._oc_binary, "get", "events", "-o", "json"]
        if self._namespace:
            argv += ["-n", _sanitize_name(self._namespace, "namespace")]
        rc, stdout, _stderr = self._runner(argv)
        if rc != 0:
            return []
        try:
            payload = json.loads(stdout) if stdout.strip() else {}
        except json.JSONDecodeError:
            return []
        items = payload.get("items", []) if isinstance(payload, dict) else []
        return [self._normalize_event(e) for e in items if isinstance(e, dict)]

    # -- public API: pod logs ------------------------------------------------

    def query_pod_logs(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        spec = dict(spec or {})
        namespace = spec.get("namespace", self._namespace)
        pod = spec.get("pod")
        if not pod:
            return []
        since_seconds = int(spec.get("since_seconds", self._timeout_seconds))
        tail_lines = int(spec.get("tail_lines", 500))
        if self._mode == "runner":
            return self._query_logs_runner(namespace, pod, since_seconds, tail_lines)
        return self._query_logs_http(namespace, pod, since_seconds, tail_lines)

    def _query_logs_http(
        self,
        namespace: str | None,
        pod: str,
        since_seconds: int,
        tail_lines: int,
    ) -> list[dict[str, Any]]:
        if self._transport is None:
            raise RuntimeError("http mode requires an injected transport")
        if not namespace:
            return []
        resp = self._transport.get(
            self._pod_log_url(namespace, pod),
            headers=self._headers(),
            # sinceSeconds + tailLines keep the read time/size bounded.
            params={"sinceSeconds": since_seconds, "tailLines": tail_lines},
            verify=self._ca_cert,
        )
        if resp.status_code != 200:
            return []
        lines = [ln for ln in resp.text.splitlines() if ln]
        return [self._normalize_log_line(ln, namespace, pod) for ln in lines]

    def _query_logs_runner(
        self,
        namespace: str | None,
        pod: str,
        since_seconds: int,
        tail_lines: int,
    ) -> list[dict[str, Any]]:
        if self._runner is None:
            raise RuntimeError("runner mode requires an injected runner")
        argv = [
            self._oc_binary,
            "logs",
            _sanitize_name(pod, "pod"),
            f"--since={since_seconds}s",
            f"--tail={tail_lines}",
        ]
        if namespace:
            argv += ["-n", _sanitize_name(namespace, "namespace")]
        rc, stdout, _stderr = self._runner(argv)
        if rc != 0:
            return []
        lines = [ln for ln in stdout.splitlines() if ln]
        return [self._normalize_log_line(ln, namespace, pod) for ln in lines]

    # -- health --------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Probe events (limit=1) in HTTP mode, or `oc whoami` in runner mode.

        NEVER raises.
        """
        try:
            if self._mode == "runner":
                if self._runner is None:
                    return {"ok": False, "detail": "no runner injected"}
                rc, _out, err = self._runner([self._oc_binary, "whoami"])
                if rc == 0:
                    return {"ok": True, "detail": "oc reachable"}
                return {"ok": False, "detail": f"oc whoami rc={rc}: {err[:200]}"}
            if self._transport is None:
                return {"ok": False, "detail": "no transport injected"}
            if not self._base_url:
                return {"ok": False, "detail": "no base_url configured"}
            resp = self._transport.get(
                self._events_url(),
                headers=self._headers(),
                params={"limit": 1},
                verify=self._ca_cert,
            )
            if resp.status_code == 200:
                return {"ok": True, "detail": "API reachable"}
            return {"ok": False, "detail": f"API returned HTTP {resp.status_code}"}
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"probe error: {type(exc).__name__}: {exc}"}
