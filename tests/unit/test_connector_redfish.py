"""Unit tests for the self-contained RedfishSource connector.

Mocked transport over canned Redfish payloads; no real network. Covers
normalization, SSRF/private-host blocking + allow_private opt-in, Basic auth
from env, and health.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest

from general_ludd.connectors.redfish import RedfishSource, TransportResponse

# ---- canned Redfish payloads ----------------------------------------------

THERMAL = {
    "Temperatures": [
        {
            "Name": "CPU1 Temp",
            "ReadingCelsius": 47,
            "Status": {"Health": "OK"},
        }
    ],
    "Fans": [
        {
            "Name": "Fan1",
            "Reading": 4800,
            "ReadingUnits": "RPM",
            "Status": {"Health": "OK"},
        }
    ],
}

POWER = {
    "Voltages": [
        {"Name": "VCORE", "ReadingVolts": 1.2, "Status": {"Health": "OK"}}
    ],
    "PowerSupplies": [
        {
            "Name": "PSU1",
            "LastPowerOutputWatts": 210,
            "Status": {"Health": "Warning"},
        }
    ],
}

EVENTS = {
    "Members": [
        {
            "Id": "1",
            "Severity": "Critical",
            "Message": "Memory ECC uncorrectable error",
            "SensorType": "Memory",
            "Created": "2024-01-01T00:00:00Z",
        }
    ]
}

SERVICE_ROOT = {"RedfishVersion": "1.6.0"}


class RecordingTransport:
    """Canned transport keyed by URL suffix; records requests + headers."""

    def __init__(self, routes: dict[str, dict[str, Any]], status: int = 200) -> None:
        self.routes = routes
        self.status = status
        self.requests: list[tuple[str, dict[str, str]]] = []

    def __call__(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        verify: bool | None = None,
    ) -> TransportResponse:
        self.requests.append((url, dict(headers or {})))
        for suffix, payload in self.routes.items():
            if url.endswith(suffix):
                return TransportResponse(self.status, json.dumps(payload))
        return TransportResponse(404, json.dumps({"error": "not found"}))


def _transport(status: int = 200) -> RecordingTransport:
    return RecordingTransport(
        {
            "/Thermal": THERMAL,
            "/Power": POWER,
            "/Entries": EVENTS,
            "/redfish/v1/": SERVICE_ROOT,
        },
        status=status,
    )


def _env() -> dict[str, str]:
    return {"REDFISH_USERNAME": "admin", "REDFISH_PASSWORD": "calvin"}


def _src(**cfg: Any) -> RedfishSource:
    base = {"base_url": "https://10.0.0.5", "allow_private": True}
    base.update(cfg)
    return RedfishSource(base, transport=_transport(), env=_env())


# ---- contract surface ------------------------------------------------------


def test_kind_and_name_class_attrs() -> None:
    src = RedfishSource({"allow_private": True})
    assert RedfishSource.KIND == "metrics"
    assert src.name == "redfish"


def test_query_thermal_normalizes_temps_and_fans() -> None:
    recs = _src().query({"what": "thermal"})
    by_metric = {r["labels"]["metric"]: r for r in recs}
    temp = by_metric["temperature_celsius"]
    assert set(temp) == {
        "ts",
        "source",
        "kind",
        "level_or_status",
        "message",
        "value",
        "labels",
        "raw",
    }
    assert temp["kind"] == "metrics"
    assert temp["value"] == 47.0
    assert temp["level_or_status"] == "OK"
    assert temp["labels"]["sensor"] == "CPU1 Temp"
    assert by_metric["fan_rpm"]["value"] == 4800.0


def test_query_power_normalizes_voltage_and_psu() -> None:
    recs = _src().query({"what": "power"})
    by_metric = {r["labels"]["metric"]: r for r in recs}
    assert by_metric["voltage_volts"]["value"] == 1.2
    psu = by_metric["psu_output_watts"]
    assert psu["value"] == 210.0
    assert psu["level_or_status"] == "Warning"


def test_query_events_normalizes_hardware_log() -> None:
    recs = _src().query({"what": "events"})
    assert len(recs) == 1
    e = recs[0]
    assert e["kind"] == "events"
    assert e["level_or_status"] == "Critical"
    assert "ECC" in e["message"]
    assert e["labels"]["sensor"] == "Memory"
    assert e["ts"] == "2024-01-01T00:00:00Z"


def test_query_all_covers_every_resource() -> None:
    recs = _src().query({"what": "all"})
    kinds = {r["kind"] for r in recs}
    assert kinds == {"metrics", "events"}


# ---- Basic auth from env ---------------------------------------------------


def test_basic_auth_header_from_env() -> None:
    transport = _transport()
    src = RedfishSource(
        {"base_url": "https://10.0.0.5", "allow_private": True},
        transport=transport,
        env=_env(),
    )
    src.query({"what": "thermal"})
    _, headers = transport.requests[0]
    expected = base64.b64encode(b"admin:calvin").decode()
    assert headers["Authorization"] == f"Basic {expected}"


def test_missing_credentials_health_not_ok() -> None:
    src = RedfishSource(
        {"base_url": "https://10.0.0.5", "allow_private": True},
        transport=_transport(),
        env={},
    )
    h = src.health()
    assert h["ok"] is False
    assert "missing credentials" in h["detail"]


# ---- SSRF / private-host blocking ------------------------------------------


def test_private_host_blocked_by_default() -> None:
    src = RedfishSource(
        {"base_url": "https://10.0.0.5"},  # allow_private defaults False
        transport=_transport(),
        env=_env(),
    )
    with pytest.raises(ValueError, match="internal/private host"):
        src.query({"what": "thermal"})


def test_loopback_host_blocked_by_default() -> None:
    src = RedfishSource(
        {"base_url": "https://127.0.0.1"},
        transport=_transport(),
        env=_env(),
    )
    with pytest.raises(ValueError, match="internal/private host"):
        src.query({"what": "thermal"})


def test_private_host_allowed_with_opt_in() -> None:
    recs = _src(base_url="https://192.168.1.10").query({"what": "thermal"})
    assert recs, "private host should be reachable when allow_private=True"


def test_public_host_allowed_without_opt_in() -> None:
    src = RedfishSource(
        {"base_url": "https://8.8.8.8"},
        transport=_transport(),
        env=_env(),
    )
    recs = src.query({"what": "thermal"})
    assert recs


def test_hostname_allowed_without_opt_in() -> None:
    # Non-IP host on a management LAN reached by name.
    src = RedfishSource(
        {"base_url": "https://bmc.internal.example"},
        transport=_transport(),
        env=_env(),
    )
    recs = src.query({"what": "thermal"})
    assert recs


def test_bad_scheme_rejected() -> None:
    src = RedfishSource(
        {"base_url": "ftp://10.0.0.5", "allow_private": True},
        transport=_transport(),
        env=_env(),
    )
    with pytest.raises(ValueError, match="unsupported scheme"):
        src.query({"what": "thermal"})


# ---- health ----------------------------------------------------------------


def test_health_ok() -> None:
    h = _src().health()
    assert h["ok"] is True
    assert "service root" in h["detail"]


def test_health_not_ok_on_http_error() -> None:
    transport = _transport(status=500)
    src = RedfishSource(
        {"base_url": "https://10.0.0.5", "allow_private": True},
        transport=transport,
        env=_env(),
    )
    h = src.health()
    assert h["ok"] is False
    assert "500" in h["detail"]


def test_health_never_raises_on_transport_explosion() -> None:
    def boom(url: str, **kw: Any) -> TransportResponse:
        raise ConnectionError("no route to host")

    src = RedfishSource(
        {"base_url": "https://10.0.0.5", "allow_private": True},
        transport=boom,
        env=_env(),
    )
    h = src.health()
    assert h["ok"] is False
    assert "unreachable" in h["detail"]


def test_health_not_ok_on_blocked_host() -> None:
    src = RedfishSource(
        {"base_url": "https://10.0.0.5"},
        transport=_transport(),
        env=_env(),
    )
    h = src.health()
    assert h["ok"] is False
    assert "host-validation" in h["detail"]


def test_query_does_not_raise_on_single_bad_resource() -> None:
    # 404 route for events -> .json() returns {} with no Members -> no events,
    # but a transport that throws for one chassis must be isolated by _safe.
    class PartlyBroken(RecordingTransport):
        def __call__(self, url: str, **kw: Any) -> TransportResponse:
            if url.endswith("/Power"):
                raise TimeoutError("slow PSU read")
            return super().__call__(url, **kw)

    transport = PartlyBroken(
        {"/Thermal": THERMAL, "/Entries": EVENTS, "/redfish/v1/": SERVICE_ROOT}
    )
    src = RedfishSource(
        {"base_url": "https://10.0.0.5", "allow_private": True},
        transport=transport,
        env=_env(),
    )
    recs = src.query({"what": "all"})
    err = [r for r in recs if r["level_or_status"] == "error"]
    assert err and "slow PSU read" in err[0]["message"]
    # thermal + events still came through
    assert any(r["labels"].get("metric") == "temperature_celsius" for r in recs)
