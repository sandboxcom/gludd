"""No-leak guardrail tests for RedfishSource.

Exception text must never reach output records or health() dicts.
"""

from __future__ import annotations

from typing import Any

from general_ludd.connectors.redfish import RedfishSource, TransportResponse


class _FaultTransport:
    def __call__(
        self,
        url: str,
        **kw: Any,
    ) -> TransportResponse:
        raise RuntimeError("connect to https://redfish.internal?token=SEKRET")


def _make_faulty_src() -> RedfishSource:
    return RedfishSource(
        {"base_url": "https://10.0.0.5", "allow_private": True},
        transport=_FaultTransport(),
        env={"REDFISH_USERNAME": "admin", "REDFISH_PASSWORD": "calvin"},
    )


def test_safe_no_exception_text_in_records() -> None:
    src = _make_faulty_src()
    recs = src.query({"what": "power"})
    for rec in recs:
        msg = str(rec.get("message", ""))
        raw = str(rec.get("raw", ""))
        assert "SEKRET" not in msg, f"secret leaked in message: {msg!r}"
        assert "SEKRET" not in raw, f"secret leaked in raw: {raw!r}"


def test_safe_returns_static_message() -> None:
    src = _make_faulty_src()
    recs = src.query({"what": "power"})
    err = [r for r in recs if r["level_or_status"] == "error"]
    assert err
    assert err[0]["message"] == "query error"


def test_health_no_exception_leak() -> None:
    src = _make_faulty_src()
    h = src.health()
    detail = str(h.get("detail", ""))
    assert "SEKRET" not in detail, f"secret leaked in detail: {detail!r}"
    assert "RuntimeError" not in detail, f"exception type leaked: {detail!r}"
    assert "connect" not in detail, f"exception text leaked: {detail!r}"
