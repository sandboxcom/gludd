"""Structural tests for connectors/normalize.py — cross-source normalization layer."""

from __future__ import annotations

import math

from general_ludd.connectors.normalize import (
    AUTH_FAMILY_PREFIXES,
    CANONICAL_SEVERITIES,
    auth_family,
    bundle_credentials,
    correlate,
    normalize_join_keys,
    sanitize_metric_value,
)


class TestSanitizeMetricValue:
    def test_finite_float(self):
        assert sanitize_metric_value(3.14) == 3.14

    def test_int_to_float(self):
        assert sanitize_metric_value(42) == 42.0

    def test_string_number(self):
        assert sanitize_metric_value("2.5") == 2.5

    def test_none_returns_none(self):
        assert sanitize_metric_value(None) is None

    def test_bool_returns_none(self):
        assert sanitize_metric_value(True) is None

    def test_nan_returns_none(self):
        assert sanitize_metric_value(float("nan")) is None

    def test_inf_returns_none(self):
        assert sanitize_metric_value(float("inf")) is None

    def test_unparseable_returns_none(self):
        assert sanitize_metric_value("not-a-number") is None

    def test_zero_is_valid(self):
        assert sanitize_metric_value(0.0) == 0.0
        assert sanitize_metric_value(0) == 0.0

    def test_negative_zero_is_valid(self):
        result = sanitize_metric_value(-0.0)
        assert result == 0.0 or (isinstance(result, float) and math.copysign(1, result) == -1.0)


class TestCanonicalSeverities:
    def test_is_tuple_with_five_elements(self):
        assert isinstance(CANONICAL_SEVERITIES, tuple)
        assert len(CANONICAL_SEVERITIES) == 5

    def test_contains_expected_severities(self):
        assert "debug" in CANONICAL_SEVERITIES
        assert "info" in CANONICAL_SEVERITIES
        assert "warn" in CANONICAL_SEVERITIES
        assert "error" in CANONICAL_SEVERITIES
        assert "critical" in CANONICAL_SEVERITIES


class TestAuthFamilyPrefixes:
    def test_is_dict(self):
        assert isinstance(AUTH_FAMILY_PREFIXES, dict)

    def test_has_expected_families(self):
        assert "aws" in AUTH_FAMILY_PREFIXES
        assert "azure" in AUTH_FAMILY_PREFIXES
        assert "gcp" in AUTH_FAMILY_PREFIXES
        assert "grafana" in AUTH_FAMILY_PREFIXES
        assert "datadog" in AUTH_FAMILY_PREFIXES

    def test_family_values_are_tuples(self):
        for family, tokens in AUTH_FAMILY_PREFIXES.items():
            assert isinstance(tokens, tuple), f"{family} tokens not a tuple"


class TestAuthFamily:
    def test_aws_match(self):
        assert auth_family("aws_cloudwatch") == "aws"

    def test_azure_match(self):
        assert auth_family("azure_monitor") == "azure"

    def test_gcp_match(self):
        assert auth_family("gcp_stackdriver") == "gcp"

    def test_grafana_match(self):
        assert auth_family("grafana_loki") == "grafana"

    def test_unknown_returns_unknown(self):
        assert auth_family("nonexistent_source") == "unknown"

    def test_empty_returns_unknown(self):
        assert auth_family("") == "unknown"

    def test_case_insensitive(self):
        assert auth_family("AWS_S3") == "aws"


class TestNormalizeJoinKeys:
    def test_non_dict_returns_empty_dict(self):
        assert normalize_join_keys(None) == {}  # type: ignore[arg-type]
        assert normalize_join_keys(42) == {}  # type: ignore[arg-type]

    def test_empty_record(self):
        result = normalize_join_keys({})
        assert isinstance(result, dict)
        assert "join" in result

    def test_preserves_original_keys(self):
        record = {"ts": 123, "source": "test"}
        result = normalize_join_keys(record)
        assert result["ts"] == 123
        assert result["source"] == "test"

    def test_adds_join_sub_dict(self):
        result = normalize_join_keys({})
        assert isinstance(result["join"], dict)


class TestCorrelate:
    def test_empty_records(self):
        result = correlate([], "host")
        assert result == {}

    def test_non_list_returns_empty(self):
        assert correlate(None, "host") == {}  # type: ignore[arg-type]

    def test_groups_by_join_key(self):
        records = [
            {"labels": {"host": "web-01"}},
            {"labels": {"host": "web-01"}},
            {"labels": {"host": "db-01"}},
        ]
        result = correlate(records, "host")
        assert len(result) == 2
        assert len(result["web-01"]) == 2
        assert len(result["db-01"]) == 1


class TestBundleCredentials:
    def test_empty_configs(self):
        assert bundle_credentials([]) == {}

    def test_non_list_returns_empty(self):
        assert bundle_credentials(None) == {}  # type: ignore[arg-type]

    def test_collects_env_names(self):
        configs = [
            {"source": "datadog", "token_env": "DD_API_KEY"},
        ]
        result = bundle_credentials(configs)
        assert isinstance(result, dict)
