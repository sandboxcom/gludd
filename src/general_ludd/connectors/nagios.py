"""Self-contained Nagios Core monitoring connector.

Reads host/service state from the Nagios Core ``statusjson.cgi`` HTTP endpoint
and normalizes every host- or service-state into a flat record. The HTTP
transport is *injectable* so the connector can be driven entirely from mocked
responses in tests (no real network, no DNS, no shell).

Design constraints honoured here:

* No imports from sibling connectors, the package ``__init__``, or any
  connector base class — this module stands alone.
* SSRF protection blocks loopback / private / link-local / cloud-metadata
  *literal* hosts on ``base_url``. We deliberately do **not** resolve DNS
  (so no DNS-rebinding surface and no network at construction time), and we
  **allow** ``http`` because Nagios CGIs are frequently exposed on plain HTTP
  inside an allowlisted internal network.
* HTTP Basic auth credentials come from the environment
  (``username_env`` / ``password_env``) — never from inline config.
* ``health()`` never raises and returns ``{"ok", "detail"}``.
  ``query()`` never raises: transport / protocol failures are surfaced as a
  single normalized error record.
* No ``shell=True`` and no subprocess use anywhere.
* Every transport call is time-bound via an injected ``timeout``.

Record shape (one dict per host/service state)::

    {
        "ts": float,                 # last-check unix seconds
        "source": str,               # connector name
        "kind": "metrics",
        "level_or_status": str,      # "OK" / "WARNING" / "CRITICAL" / "UNKNOWN"
                                     #   (hosts: "OK"/"DOWN"/"UNREACHABLE")
        "message": str,              # "<host>" or "<host>/<service>"
        "value": float,              # numeric Nagios state code
        "labels": dict[str, str],    # {"host": ..., "service": ...}
        "raw": Any,                  # original per-entity payload
    }
"""

from __future__ import annotations

import base64
import json as _json
import os
import time
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode, urlsplit

from general_ludd.security.ssrf import is_url_blocked

# Injectable transport signature: (url, params, headers, timeout) -> (status, json)
HttpGet = Callable[..., "tuple[int, Any]"]

KIND = "metrics"

_DEFAULT_TIMEOUT = 10.0

# Nagios numeric state codes -> human label.
# Service states: 0 OK, 1 WARNING, 2 CRITICAL, 3 UNKNOWN.
_SERVICE_STATE = {0: "OK", 1: "WARNING", 2: "CRITICAL", 3: "UNKNOWN"}
# Host states: 0 UP, 1 DOWN, 2 UNREACHABLE.
_HOST_STATE = {0: "OK", 1: "DOWN", 2: "UNREACHABLE"}


def _default_http_get(
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> tuple[int, Any]:
    """Real, time-bound stdlib transport used when none is injected.

    Matches the ``(url, params, headers, timeout) -> (status, json)`` contract.
    Only ``http``/``https`` are allowed; the request is bounded by an explicit
    timeout. The mocked tests never reach this path.
    """
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"unsupported url scheme: {parsed.scheme!r}")
    if params:
        sep = "&" if parsed.query else "?"
        url = f"{url}{sep}{urlencode(params, doseq=True)}"
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        status = int(resp.getcode() or 0)
        body = resp.read()
    parsed_body: Any = _json.loads(body) if body else {}
    return status, parsed_body


def _validate_base_url(base_url: str) -> str:
    if not base_url or not isinstance(base_url, str):
        raise ValueError("base_url is required")

    parts = urlsplit(base_url)
    # Always-on scheme + present-host check (explicit message; also independent
    # of the SSRF host decision below).
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"unsupported scheme: {parts.scheme!r} (only http/https)")

    host = (parts.hostname or "").strip().lower()
    if not host:
        raise ValueError("base_url has no host")

    # Private/loopback/metadata literal decision delegates to the canonical
    # SSRF guard so this connector's blocklist can never drift.
    if is_url_blocked(base_url, scheme_allowlist=("http", "https")):
        raise ValueError(f"blocked internal/metadata address: {host!r}")

    return base_url.rstrip("/")


def _coerce_state(raw_state: Any, table: dict[int, str]) -> tuple[str, float]:
    """Return ``(label, numeric)`` for a Nagios state code.

    Accepts an int code or an already-textual state. Unknown codes degrade to
    ``("UNKNOWN", code-or-0)`` rather than raising.
    """
    if isinstance(raw_state, bool):
        raw_state = int(raw_state)
    if isinstance(raw_state, (int, float)):
        code = int(raw_state)
        return table.get(code, "UNKNOWN"), float(code)
    if isinstance(raw_state, str):
        text = raw_state.strip()
        # Numeric-as-string?
        try:
            code = int(text)
            return table.get(code, "UNKNOWN"), float(code)
        except ValueError:
            upper = text.upper()
            # Reverse map by label (e.g. already "CRITICAL").
            for code, label in table.items():
                if label == upper:
                    return label, float(code)
            return upper or "UNKNOWN", 0.0
    return "UNKNOWN", 0.0


class NagiosSource:
    """A metrics source backed by the Nagios Core ``statusjson.cgi`` API."""

    KIND = KIND

    def __init__(
        self,
        config: dict[str, Any],
        http_get: HttpGet | None = None,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        base_url = config.get("base_url", "")
        self._base_url = _validate_base_url(base_url)
        # Basic-auth credentials are resolved from the environment only.
        self._username_env = config.get("username_env")
        self._password_env = config.get("password_env")
        self._http_get = http_get or _default_http_get
        self._timeout = float(timeout)
        self.kind = KIND
        host = urlsplit(self._base_url).netloc
        self.name = config.get("name") or f"nagios:{host}"

    # -- helpers ----------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self._username_env and self._password_env:
            user = os.environ.get(self._username_env)
            pw = os.environ.get(self._password_env)
            if user is not None and pw is not None:
                token = base64.b64encode(f"{user}:{pw}".encode()).decode("ascii")
                headers["Authorization"] = f"Basic {token}"
        return headers

    def _error_record(self, message: str, raw: Any) -> dict[str, Any]:
        return {
            "ts": time.time(),
            "source": self.name,
            "kind": KIND,
            "level_or_status": "error",
            "message": message,
            "value": 0.0,
            "labels": {},
            "raw": raw,
        }

    def _state_record(
        self,
        host: str,
        service: str | None,
        raw_state: Any,
        last_check: Any,
        raw: Any,
    ) -> dict[str, Any]:
        table = _SERVICE_STATE if service else _HOST_STATE
        label, value = _coerce_state(raw_state, table)
        try:
            ts_f = float(last_check)
        except (TypeError, ValueError):
            ts_f = 0.0
        # Nagios reports last_check in ms in some versions; normalize obvious ms.
        if ts_f > 1e12:
            ts_f = ts_f / 1000.0
        labels: dict[str, str] = {"host": str(host)}
        message = str(host)
        if service:
            labels["service"] = str(service)
            message = f"{host}/{service}"
        return {
            "ts": ts_f,
            "source": self.name,
            "kind": KIND,
            "level_or_status": label,
            "message": message,
            "value": value,
            "labels": labels,
            "raw": raw,
        }

    # -- public API -------------------------------------------------------

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """Fetch a host or service list and normalize states.

        ``spec["query"]`` selects ``"servicelist"`` (default) or ``"hostlist"``.
        Never raises: failures become a single error record.
        """
        which = spec.get("query", "servicelist")
        if which not in ("servicelist", "hostlist"):
            return [
                self._error_record(
                    f"unsupported query: {which!r} (servicelist|hostlist)",
                    {"spec": spec},
                )
            ]

        params: dict[str, Any] = {"query": which}
        # Optional passthrough filters supported by statusjson.cgi.
        for key in ("hostname", "hostgroup", "servicegroup", "details"):
            if key in spec:
                params[key] = spec[key]

        url = f"{self._base_url}/nagios/cgi-bin/statusjson.cgi"
        try:
            status, payload = self._http_get(
                url, params=params, headers=self._headers(), timeout=self._timeout
            )
        except Exception as exc:  # surfaced as a record, never raised
            return [self._error_record(f"transport error: {exc}", {"url": url})]

        return self._normalize(which, status, payload)

    def _normalize(
        self, which: str, status: int, payload: Any
    ) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return [self._error_record(f"non-dict payload (status {status})", payload)]

        if not (200 <= int(status) < 300):
            return [
                self._error_record(f"nagios returned status {status}", payload)
            ]

        data = payload.get("data") or {}
        records: list[dict[str, Any]] = []

        if which == "hostlist":
            hostlist = data.get("hostlist") or {}
            # statusjson.cgi hostlist maps hostname -> numeric state.
            for hostname, entry in hostlist.items():
                raw_state, last_check, raw = self._unpack_entry(entry)
                records.append(
                    self._state_record(hostname, None, raw_state, last_check, raw)
                )
        else:  # servicelist
            servicelist = data.get("servicelist") or {}
            # servicelist maps hostname -> {service_desc -> state-or-detail}.
            for hostname, services in servicelist.items():
                if not isinstance(services, dict):
                    continue
                for service_desc, entry in services.items():
                    raw_state, last_check, raw = self._unpack_entry(entry)
                    records.append(
                        self._state_record(
                            hostname, service_desc, raw_state, last_check, raw
                        )
                    )

        return records

    @staticmethod
    def _unpack_entry(entry: Any) -> tuple[Any, Any, Any]:
        """Return ``(state, last_check, raw)`` from a statusjson entry.

        statusjson.cgi returns either a bare numeric state, or a detail object
        carrying ``status``/``last_check`` (with ``details=true``).
        """
        if isinstance(entry, dict):
            state = entry.get("status", entry.get("state"))
            last_check = entry.get("last_check", entry.get("last_state_change", 0))
            return state, last_check, entry
        return entry, 0, entry

    def health(self) -> dict[str, Any]:
        """Return ``{"ok", "detail"}``. Never raises.

        Probes a cheap hostlist query as a liveness check.
        """
        url = f"{self._base_url}/nagios/cgi-bin/statusjson.cgi"
        try:
            status, payload = self._http_get(
                url,
                params={"query": "hostlist"},
                headers=self._headers(),
                timeout=self._timeout,
            )
        except Exception as exc:  # health must never raise
            return {
                "ok": False,
                "detail": f"transport error: {exc}",
                "source": self.name,
            }

        ok = isinstance(payload, dict) and 200 <= int(status) < 300
        if ok:
            detail = f"ok (status {status})"
        elif isinstance(payload, dict):
            detail = str(payload.get("error") or f"unhealthy (status {status})")
        else:
            detail = f"unhealthy (status {status})"
        return {"ok": ok, "detail": detail, "source": self.name, "status": status}
