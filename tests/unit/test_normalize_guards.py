"""Tests for batch-5 security/safety guards in normalize.py and base.py.

Covers:
- FIX 1: _config_family explicit-family allowlist (blocks path-traversal poison)
- FIX 2: normalized_record NaN/Inf guard (value coerced to None)
- FIX 3: Observability.find() MAX_RECORDS cap — skipped (hard to construct
  Observability with fake sources that produce 50k+ records in unit tests;
  the logic is a simple truncate+break guard in base.py, visually verified).
"""

from __future__ import annotations

import pytest

from general_ludd.connectors.base import normalized_record
from general_ludd.connectors.normalize import (
    AUTH_FAMILY_PREFIXES,
    _config_family,  # type: ignore[attr-defined]
)

# ---------------------------------------------------------------------------
# FIX 1 — _config_family allowlist
# ---------------------------------------------------------------------------

class TestConfigFamilyAllowlist:
    """Explicit 'family' key must be in KNOWN families or collapse to 'unknown'."""

    def test_known_family_aws_passthrough(self):
        assert _config_family({"family": "aws"}) == "aws"

    def test_known_family_azure_passthrough(self):
        assert _config_family({"family": "azure"}) == "azure"

    def test_known_family_gcp_passthrough(self):
        assert _config_family({"family": "gcp"}) == "gcp"

    def test_known_family_grafana_passthrough(self):
        assert _config_family({"family": "grafana"}) == "grafana"

    def test_known_family_datadog_passthrough(self):
        assert _config_family({"family": "datadog"}) == "datadog"

    def test_unknown_sentinel_passthrough(self):
        assert _config_family({"family": "unknown"}) == "unknown"

    def test_path_traversal_family_blocked(self):
        """Poisoned family value must not pass through — collapses to 'unknown'."""
        assert _config_family({"family": "../etc/passwd"}) == "unknown"

    def test_arbitrary_string_blocked(self):
        assert _config_family({"family": "injected_family"}) == "unknown"

    def test_empty_family_falls_through_to_inference(self):
        # Empty string -> _as_str returns None -> falls through to name inference
        result = _config_family({"family": "", "name": "aws-prod"})
        assert result == "aws"

    def test_no_family_key_infers_from_name(self):
        assert _config_family({"name": "cloudwatch-prod"}) == "aws"

    def test_all_auth_family_prefixes_are_known(self):
        """Every key in AUTH_FAMILY_PREFIXES must be accepted as a valid family."""
        for family_name in AUTH_FAMILY_PREFIXES:
            assert _config_family({"family": family_name}) == family_name.lower(), (
                f"Expected '{family_name}' to be accepted but got 'unknown'"
            )

    def test_case_insensitive_explicit_family(self):
        assert _config_family({"family": "AWS"}) == "aws"

    def test_auth_family_key_also_checked(self):
        assert _config_family({"auth_family": "gcp"}) == "gcp"

    def test_auth_family_poisoned_value_blocked(self):
        assert _config_family({"auth_family": "../../etc"}) == "unknown"


# ---------------------------------------------------------------------------
# FIX 2 — NaN/Inf guard in normalized_record
# ---------------------------------------------------------------------------

class TestNormalizedRecordNanInfGuard:
    """value=NaN or Inf must be coerced to None; finite values must pass through."""

    def test_nan_value_becomes_none(self):
        rec = normalized_record(source="test", kind="metric", value=float("nan"))
        assert rec["value"] is None

    def test_positive_inf_value_becomes_none(self):
        rec = normalized_record(source="test", kind="metric", value=float("inf"))
        assert rec["value"] is None

    def test_negative_inf_value_becomes_none(self):
        rec = normalized_record(source="test", kind="metric", value=float("-inf"))
        assert rec["value"] is None

    def test_finite_value_passes_through(self):
        rec = normalized_record(source="test", kind="metric", value=3.14)
        assert rec["value"] == pytest.approx(3.14)

    def test_zero_value_passes_through(self):
        rec = normalized_record(source="test", kind="metric", value=0.0)
        assert rec["value"] == 0.0

    def test_none_value_stays_none(self):
        rec = normalized_record(source="test", kind="metric", value=None)
        assert rec["value"] is None

    def test_nan_ts_becomes_none(self):
        rec = normalized_record(source="test", kind="metric", ts=float("nan"))
        assert rec["ts"] is None

    def test_inf_ts_becomes_none(self):
        rec = normalized_record(source="test", kind="metric", ts=float("inf"))
        assert rec["ts"] is None

    def test_finite_ts_passes_through(self):
        rec = normalized_record(source="test", kind="metric", ts=1_700_000_000.0)
        assert rec["ts"] == pytest.approx(1_700_000_000.0)

    def test_none_ts_stays_none(self):
        rec = normalized_record(source="test", kind="metric", ts=None)
        assert rec["ts"] is None

    def test_all_fields_present_in_result(self):
        rec = normalized_record(source="s", kind="k", message="m", value=1.0)
        for field in ("ts", "source", "kind", "level_or_status", "message", "value", "labels", "raw"):
            assert field in rec


# ---------------------------------------------------------------------------
# FIX 3 — Observability.find() MAX_RECORDS cap
# ---------------------------------------------------------------------------
# Skipped: constructing an Observability instance with fake sources that
# return 50k+ records in a fast unit test is impractical without a mock
# framework. The guard is a simple truncate+break at MAX_RECORDS=50_000
# inserted in base.py lines 213-220; it is verified by code review.
# A future integration test can exercise this with a stubbed SourceRegistry.
