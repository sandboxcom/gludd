"""Redfish (BMC) telemetry connector.

Self-contained source for out-of-band hardware metrics and events from a
baseboard management controller (BMC) speaking the DMTF Redfish protocol over
HTTPS.

Acquisition: HTTP GET against ``{base_url}/redfish/v1/...`` resource trees —
``Chassis/.../Thermal`` and ``.../Power`` for temperatures/fans/PSU/voltage,
``Systems/.../Memory`` summaries for ECC, and
``Systems/{id}/LogServices/.../Entries`` for hardware event logs.

Security posture:

* Basic auth credentials are resolved from the environment via ``*_env`` config
  indirection (``username_env`` / ``password_env``); never inlined.
* SSRF defense: the literal host in ``base_url`` is checked. By default a host
  that is an IP literal in a private/loopback/link-local/reserved range is
  rejected — BMCs are internal, so this is opt-in via ``allow_private=True``
  rather than the usual block-private default. A literal-host check (no DNS
  rebinding window) is performed on the configured URL.
* The transport is injectable for testing/DI; requests are time-bound.

:meth:`health` never raises. :meth:`query` returns normalized records with the
canonical fields ``ts, source, kind, level_or_status, message, value, labels,
raw``.
"""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from general_ludd.security.ssrf import is_url_blocked


@dataclass
class TransportResponse:
    """Minimal HTTP response surface an injected transport must return."""

    status: int
    body: str

    def json(self) -> Any:
        return json.loads(self.body) if self.body.strip() else {}


# An injected transport takes (url, headers, timeout) and returns a
# TransportResponse. No real network is required for tests.
Transport = Callable[..., TransportResponse]


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class RedfishConfig:
    """Configuration for :class:`RedfishSource`."""

    base_url: str = "https://127.0.0.1"
    username_env: str = "REDFISH_USERNAME"
    password_env: str = "REDFISH_PASSWORD"  # env var NAME, not a secret value
    # BMCs are internal; private/loopback hosts must be explicitly allowed.
    allow_private: bool = False
    verify_tls: bool = True
    timeout_seconds: float = 10.0
    # Resource ids to enumerate (defaults cover the common single-node BMC).
    system_ids: tuple[str, ...] = ("1",)
    chassis_ids: tuple[str, ...] = ("1",)

    @classmethod
    def from_dict(cls, config: dict[str, Any] | None) -> RedfishConfig:
        config = dict(config or {})
        kwargs: dict[str, Any] = {}
        for fname in (
            "base_url",
            "username_env",
            "password_env",
            "allow_private",
            "verify_tls",
            "timeout_seconds",
        ):
            if fname in config and config[fname] is not None:
                kwargs[fname] = config[fname]
        if config.get("system_ids"):
            kwargs["system_ids"] = tuple(config["system_ids"])
        if config.get("chassis_ids"):
            kwargs["chassis_ids"] = tuple(config["chassis_ids"])
        return cls(**kwargs)


class RedfishSource:
    """Redfish metrics + hardware-events connector.

    :param config: a :class:`RedfishConfig` or a plain dict.
    :param transport: injected HTTP transport (for tests / DI).
    :param env: env mapping for credential resolution (defaults to os.environ).
    """

    KIND = "metrics"  # primary kind; event records carry kind="events"
    name = "redfish"

    def __init__(
        self,
        config: RedfishConfig | dict[str, Any] | None = None,
        *,
        transport: Transport | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        if isinstance(config, RedfishConfig):
            self.config = config
        else:
            self.config = RedfishConfig.from_dict(config)
        self._transport = transport
        self._env = env if env is not None else os.environ

    # ---- secret resolution -------------------------------------------------
    def _credentials(self) -> tuple[str | None, str | None]:
        user = self._env.get(self.config.username_env)
        pw = self._env.get(self.config.password_env)
        return user, pw

    def _auth_header(self) -> dict[str, str]:
        user, pw = self._credentials()
        if not user or not pw:
            return {}
        token = base64.b64encode(f"{user}:{pw}".encode()).decode("ascii")
        return {"Authorization": f"Basic {token}"}

    # ---- SSRF / host validation -------------------------------------------
    def _validate_host(self) -> str:
        """Validate base_url's literal host; return normalized base or raise.

        A literal IP host in a private/loopback/link-local/reserved range is
        rejected unless ``allow_private`` is set (BMCs are internal, so this is
        opt-in). Non-IP hostnames are allowed through (the BMC is reached by
        name on a management LAN); no DNS resolution is performed here, so there
        is no rebinding window in this check.
        """
        parts = urlsplit(self.config.base_url)
        if parts.scheme not in ("http", "https"):
            raise ValueError(f"unsupported scheme: {parts.scheme!r}")
        host = parts.hostname
        if not host:
            raise ValueError(f"base_url has no host: {self.config.base_url!r}")
        # Delegate the internal/private + cloud-metadata NAME decision to the
        # canonical shared guard so this connector cannot drift weaker than the
        # single source of truth. BMCs are internal, so private/loopback hosts
        # are opt-in via ``allow_private`` (metadata names too, when opted in).
        if not self.config.allow_private and is_url_blocked(self.config.base_url):
            raise ValueError(
                f"refusing internal/private host {host} (set allow_private "
                f"for internal BMCs)"
            )
        return self.config.base_url.rstrip("/")

    # ---- transport ---------------------------------------------------------
    def _get(self, path: str) -> TransportResponse:
        if self._transport is None:
            raise RuntimeError("no transport injected")
        base = self._validate_host()
        url = f"{base}/{path.lstrip('/')}"
        headers = {"Accept": "application/json", **self._auth_header()}
        return self._transport(
            url,
            headers=headers,
            timeout=self.config.timeout_seconds,
            verify=self.config.verify_tls,
        )

    # ---- health ------------------------------------------------------------
    def health(self) -> dict[str, Any]:
        """Never raises. Returns {'ok': bool, 'detail': str}."""
        try:
            self._validate_host()
        except ValueError as exc:
            return {"ok": False, "detail": f"host-validation: {exc}"}
        user, pw = self._credentials()
        if not user or not pw:
            return {
                "ok": False,
                "detail": (
                    f"missing credentials (env {self.config.username_env}/"
                    f"{self.config.password_env})"
                ),
            }
        if self._transport is None:
            return {"ok": True, "detail": "config valid; no transport to probe"}
        try:
            resp = self._get("/redfish/v1/")
            if 200 <= resp.status < 300:
                return {"ok": True, "detail": f"service root {resp.status}"}
            return {"ok": False, "detail": f"service root HTTP {resp.status}"}
        except Exception as exc:  # health never raises
            return {"ok": False, "detail": f"unreachable: {exc}"}

    # ---- query -------------------------------------------------------------
    def query(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Return normalized records.

        Spec keys: ``what`` in {'thermal','power','events','all'}.
        """
        spec = dict(spec or {})
        what = spec.get("what", "all")
        # Validate host confinement ONCE up front so an SSRF/blocked-host or bad
        # scheme is a hard error (it must not be swallowed by the per-resource
        # _safe() isolation below, which only contains transient resource faults).
        self._validate_host()
        recs: list[dict[str, Any]] = []
        for cid in self.config.chassis_ids:
            if what in ("thermal", "all"):
                recs += self._safe(self._query_thermal, cid)
            if what in ("power", "all"):
                recs += self._safe(self._query_power, cid)
        if what in ("events", "all"):
            for sid in self.config.system_ids:
                recs += self._safe(self._query_events, sid)
        return recs

    def _safe(
        self, fn: Callable[[str], list[dict[str, Any]]], arg: str
    ) -> list[dict[str, Any]]:
        try:
            return fn(arg)
        except Exception as exc:  # one bad resource never kills the whole query
            return [
                self._record(
                    kind="events",
                    source=f"redfish:{arg}",
                    level_or_status="error",
                    message=f"query error in {fn.__name__}: {exc}",
                    value=None,
                    labels={"member": arg, "sensor": ""},
                    raw={"error": str(exc)},
                    ts=_utcnow_iso(),
                )
            ]

    # ---- resource queries --------------------------------------------------
    def _query_thermal(self, chassis_id: str) -> list[dict[str, Any]]:
        data = self._get(f"/redfish/v1/Chassis/{chassis_id}/Thermal").json()
        recs: list[dict[str, Any]] = []
        for t in data.get("Temperatures", []) or []:
            recs.append(
                self._record(
                    kind="metrics",
                    source=f"redfish:Chassis/{chassis_id}/Thermal",
                    level_or_status=str(
                        (t.get("Status") or {}).get("Health", "")
                    ),
                    message=f"temperature {t.get('Name', '?')}",
                    value=_to_float(t.get("ReadingCelsius")),
                    labels={
                        "member": chassis_id,
                        "sensor": str(t.get("Name", "")),
                        "metric": "temperature_celsius",
                    },
                    raw=t,
                    ts=_utcnow_iso(),
                )
            )
        for f in data.get("Fans", []) or []:
            recs.append(
                self._record(
                    kind="metrics",
                    source=f"redfish:Chassis/{chassis_id}/Thermal",
                    level_or_status=str(
                        (f.get("Status") or {}).get("Health", "")
                    ),
                    message=f"fan {f.get('Name', '?')}",
                    value=_to_float(f.get("Reading")),
                    labels={
                        "member": chassis_id,
                        "sensor": str(f.get("Name", "")),
                        "metric": "fan_" + str(f.get("ReadingUnits", "rpm")).lower(),
                    },
                    raw=f,
                    ts=_utcnow_iso(),
                )
            )
        return recs

    def _query_power(self, chassis_id: str) -> list[dict[str, Any]]:
        data = self._get(f"/redfish/v1/Chassis/{chassis_id}/Power").json()
        recs: list[dict[str, Any]] = []
        for v in data.get("Voltages", []) or []:
            recs.append(
                self._record(
                    kind="metrics",
                    source=f"redfish:Chassis/{chassis_id}/Power",
                    level_or_status=str(
                        (v.get("Status") or {}).get("Health", "")
                    ),
                    message=f"voltage {v.get('Name', '?')}",
                    value=_to_float(v.get("ReadingVolts")),
                    labels={
                        "member": chassis_id,
                        "sensor": str(v.get("Name", "")),
                        "metric": "voltage_volts",
                    },
                    raw=v,
                    ts=_utcnow_iso(),
                )
            )
        for ps in data.get("PowerSupplies", []) or []:
            recs.append(
                self._record(
                    kind="metrics",
                    source=f"redfish:Chassis/{chassis_id}/Power",
                    level_or_status=str(
                        (ps.get("Status") or {}).get("Health", "")
                    ),
                    message=f"power supply {ps.get('Name', '?')}",
                    value=_to_float(ps.get("LastPowerOutputWatts")),
                    labels={
                        "member": chassis_id,
                        "sensor": str(ps.get("Name", "")),
                        "metric": "psu_output_watts",
                    },
                    raw=ps,
                    ts=_utcnow_iso(),
                )
            )
        return recs

    def _query_events(self, system_id: str) -> list[dict[str, Any]]:
        path = f"/redfish/v1/Systems/{system_id}/LogServices/Log/Entries"
        data = self._get(path).json()
        recs: list[dict[str, Any]] = []
        for e in data.get("Members", []) or []:
            recs.append(
                self._record(
                    kind="events",
                    source=f"redfish:Systems/{system_id}/LogServices",
                    level_or_status=str(e.get("Severity", "")),
                    message=str(e.get("Message", e.get("Name", ""))),
                    value=None,
                    labels={
                        "member": system_id,
                        "sensor": str(e.get("SensorType", "")),
                        "entry_id": str(e.get("Id", "")),
                    },
                    raw=e,
                    ts=str(e.get("Created") or _utcnow_iso()),
                )
            )
        return recs

    # ---- helpers -----------------------------------------------------------
    def _record(
        self,
        *,
        kind: str,
        source: str,
        level_or_status: str,
        message: str,
        value: float | None,
        labels: dict[str, Any],
        raw: Any,
        ts: str,
    ) -> dict[str, Any]:
        return {
            "ts": ts,
            "source": source,
            "kind": kind,
            "level_or_status": level_or_status,
            "message": message,
            "value": value,
            "labels": labels,
            "raw": raw,
        }


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
