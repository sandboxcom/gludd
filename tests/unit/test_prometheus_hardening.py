"""Tests for P2 hardening of the Prometheus connector.

Covers:
  1. NaN / Inf raw values are coerced to 0.0 in _sample_record.
  2. Payloads with >MAX_RESULT_SIZE series are capped at <=MAX_RESULT_SIZE+1 records
     (the +1 is the truncation error record appended on overflow).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from general_ludd.connectors.prometheus import MAX_RESULT_SIZE, PrometheusSource

if TYPE_CHECKING:
    from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_URL = "http://prometheus.example.com"


def _make_source(http_get: Any = None) -> PrometheusSource:
    """Return a PrometheusSource with an injectable transport."""

    def _noop(url: str, **kwargs: Any) -> tuple[int, Any]:  # pragma: no cover
        return 200, {}

    return PrometheusSource(
        {"base_url": _BASE_URL},
        http_get=http_get or _noop,
    )


def _vector_payload(series: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": series,
        },
    }


def _matrix_payload(series: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": series,
        },
    }


def _single_vector_series(raw_value: str) -> dict[str, Any]:
    return {"metric": {"__name__": "up"}, "value": [1_700_000_000.0, raw_value]}


# ---------------------------------------------------------------------------
# NaN / Inf guard
# ---------------------------------------------------------------------------


class TestNaNInfGuard:
    """_sample_record must map non-finite float strings to 0.0."""

    def test_nan_raw_value_yields_zero(self) -> None:
        src = _make_source()
        payload = _vector_payload([_single_vector_series("NaN")])
        records = src._normalize(200, payload)
        assert len(records) == 1
        assert records[0]["value"] == 0.0

    def test_positive_inf_raw_value_yields_zero(self) -> None:
        src = _make_source()
        payload = _vector_payload([_single_vector_series("+Inf")])
        records = src._normalize(200, payload)
        assert len(records) == 1
        assert records[0]["value"] == 0.0

    def test_negative_inf_raw_value_yields_zero(self) -> None:
        src = _make_source()
        payload = _vector_payload([_single_vector_series("-Inf")])
        records = src._normalize(200, payload)
        assert len(records) == 1
        assert records[0]["value"] == 0.0

    def test_finite_value_is_preserved(self) -> None:
        src = _make_source()
        payload = _vector_payload([_single_vector_series("42.5")])
        records = src._normalize(200, payload)
        assert len(records) == 1
        assert records[0]["value"] == pytest.approx(42.5)

    def test_nan_in_matrix_yields_zero(self) -> None:
        src = _make_source()
        payload = _matrix_payload(
            [
                {
                    "metric": {"__name__": "rate"},
                    "values": [[1_700_000_000.0, "NaN"], [1_700_000_001.0, "1.5"]],
                }
            ]
        )
        records = src._normalize(200, payload)
        # Two samples; first should be 0.0, second 1.5
        assert len(records) == 2
        assert records[0]["value"] == 0.0
        assert records[1]["value"] == pytest.approx(1.5)


# ---------------------------------------------------------------------------
# MAX_RESULT_SIZE cap
# ---------------------------------------------------------------------------


class TestMaxResultSizeCap:
    """Results exceeding MAX_RESULT_SIZE must be capped."""

    def _big_vector_payload(self, n: int) -> dict[str, Any]:
        series = [
            {"metric": {"__name__": f"m{i}"}, "value": [1_700_000_000.0, str(i)]}
            for i in range(n)
        ]
        return _vector_payload(series)

    def _big_matrix_payload(self, n_series: int, n_values: int) -> dict[str, Any]:
        series = [
            {
                "metric": {"__name__": f"m{i}"},
                "values": [
                    [1_700_000_000.0 + j, str(j)] for j in range(n_values)
                ],
            }
            for i in range(n_series)
        ]
        return _matrix_payload(series)

    def test_vector_exactly_at_limit_is_not_truncated(self) -> None:
        src = _make_source()
        payload = self._big_vector_payload(MAX_RESULT_SIZE)
        records = src._normalize(200, payload)
        # Exactly at limit — no truncation record
        assert len(records) == MAX_RESULT_SIZE
        assert all(r["level_or_status"] == "" for r in records)

    def test_vector_over_limit_is_capped(self) -> None:
        n = MAX_RESULT_SIZE + 500
        src = _make_source()
        payload = self._big_vector_payload(n)
        records = src._normalize(200, payload)
        # At most MAX_RESULT_SIZE data records + 1 truncation error record
        assert len(records) <= MAX_RESULT_SIZE + 1
        # Last record should be the truncation sentinel
        last = records[-1]
        assert last["level_or_status"] == "error"
        assert "truncated" in last["message"].lower()

    def test_matrix_over_limit_is_capped(self) -> None:
        # 100 series x 200 values each = 20 000 samples -> well over 10 000
        src = _make_source()
        payload = self._big_matrix_payload(n_series=100, n_values=200)
        records = src._normalize(200, payload)
        assert len(records) <= MAX_RESULT_SIZE + 1
        last = records[-1]
        assert last["level_or_status"] == "error"
        assert "truncated" in last["message"].lower()

    def test_small_payload_is_not_truncated(self) -> None:
        src = _make_source()
        payload = self._big_vector_payload(5)
        records = src._normalize(200, payload)
        assert len(records) == 5
        assert all(r["level_or_status"] == "" for r in records)
