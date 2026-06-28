"""Unit tests for the cross-source normalization layer.

Covers, per the task contract:

- heterogeneous connector labels fold into canonical join keys;
- the level/status/priority -> canonical severity mapping;
- ``correlate`` groups records by a join key (scalar and structured);
- the ``auth_family`` classification matrix;
- ``bundle_credentials`` collects only env-var NAMES (never values) per family;
- malformed-input safety: every public function is total (never raises).
"""

from __future__ import annotations

import pytest

from general_ludd.connectors.normalize import (
    AUTH_FAMILY_PREFIXES,
    CANONICAL_SEVERITIES,
    auth_family,
    bundle_credentials,
    correlate,
    normalize_join_keys,
    sanitize_metric_value,
)


def _rec(**labels: object) -> dict[str, object]:
    """Build a minimal normalized-record dict with the given labels."""
    return {
        "ts": 1.0,
        "source": "test",
        "kind": "logs",
        "level_or_status": "info",
        "message": "",
        "value": None,
        "labels": dict(labels),
        "raw": None,
    }


# --------------------------------------------------------------------------- #
# normalize_join_keys: heterogeneous label folding
# --------------------------------------------------------------------------- #
class TestNormalizeJoinKeys:
    def test_returns_join_subdict(self) -> None:
        out = normalize_join_keys(_rec(host="web-01"))
        assert "join" in out
        assert isinstance(out["join"], dict)

    def test_does_not_mutate_input(self) -> None:
        rec = _rec(host="web-01")
        normalize_join_keys(rec)
        assert "join" not in rec

    def test_preserves_original_fields(self) -> None:
        out = normalize_join_keys(_rec(host="web-01"))
        assert out["source"] == "test"
        assert out["kind"] == "logs"
        assert out["labels"] == {"host": "web-01"}

    @pytest.mark.parametrize("alias", ["trace_id", "traceId", "x-b3-traceid"])
    def test_trace_id_from_aliases(self, alias: str) -> None:
        out = normalize_join_keys(_rec(**{alias: "abc123"}))
        assert out["join"]["trace_id"] == "abc123"

    @pytest.mark.parametrize("alias", ["host", "hostname", "instance", "computer", "node"])
    def test_host_from_aliases(self, alias: str) -> None:
        out = normalize_join_keys(_rec(**{alias: "Web-01"}))
        assert out["join"]["host"] == "web-01"

    def test_host_lowercased_and_port_stripped(self) -> None:
        out = normalize_join_keys(_rec(host="API-Gateway:8443"))
        assert out["join"]["host"] == "api-gateway"

    def test_host_bracketed_ipv6_keeps_address_drops_port(self) -> None:
        out = normalize_join_keys(_rec(host="[::1]:9090"))
        assert out["join"]["host"] == "[::1]"

    def test_host_bare_ipv6_not_truncated(self) -> None:
        out = normalize_join_keys(_rec(host="fe80::1ff:fe23:4567:890a"))
        assert out["join"]["host"] == "fe80::1ff:fe23:4567:890a"

    @pytest.mark.parametrize("alias", ["service", "app", "job", "unit"])
    def test_service_from_aliases(self, alias: str) -> None:
        out = normalize_join_keys(_rec(**{alias: "checkout"}))
        assert out["join"]["service"] == "checkout"

    def test_k8s_coordinates(self) -> None:
        out = normalize_join_keys(_rec(namespace="prod", pod="checkout-abc", container="app"))
        assert out["join"]["k8s"] == {"namespace": "prod", "pod": "checkout-abc", "container": "app"}

    def test_k8s_partial_only_present_keys(self) -> None:
        out = normalize_join_keys(_rec(namespace="prod"))
        assert out["join"]["k8s"] == {"namespace": "prod"}

    def test_cloud_account_and_region(self) -> None:
        out = normalize_join_keys(_rec(account="123456789012", region="us-east-1"))
        assert out["join"]["cloud"] == {"account": "123456789012", "region": "us-east-1"}

    def test_cloud_gcp_project_and_azure_subscription(self) -> None:
        out = normalize_join_keys(_rec(project="my-proj", subscription="sub-xyz", location="westeurope"))
        assert out["join"]["cloud"] == {
            "project": "my-proj",
            "subscription": "sub-xyz",
            "region": "westeurope",
        }

    def test_case_insensitive_label_names(self) -> None:
        out = normalize_join_keys(_rec(HostName="Web-02", TraceID="t-9"))
        assert out["join"]["host"] == "web-02"
        assert out["join"]["trace_id"] == "t-9"

    def test_missing_keys_are_omitted_not_none(self) -> None:
        out = normalize_join_keys(_rec(host="web-01"))
        join = out["join"]
        assert "trace_id" not in join
        assert "service" not in join
        assert "k8s" not in join
        assert "cloud" not in join

    def test_first_alias_wins(self) -> None:
        # host beats instance when both present
        out = normalize_join_keys(_rec(host="primary", instance="secondary"))
        assert out["join"]["host"] == "primary"

    def test_empty_label_value_skipped(self) -> None:
        out = normalize_join_keys(_rec(host="", instance="real-host"))
        assert out["join"]["host"] == "real-host"

    def test_idempotent(self) -> None:
        once = normalize_join_keys(_rec(host="Web-01:80", traceId="t1"))
        twice = normalize_join_keys(once)
        assert twice["join"] == once["join"]

    # Group F3: service.name, application, container_name aliases for service.
    @pytest.mark.parametrize("alias", ["service.name", "application", "container_name"])
    def test_service_from_aliases_group_f3(self, alias: str) -> None:
        out = normalize_join_keys(_rec(**{alias: "checkout"}))
        assert out["join"]["service"] == "checkout"

    # Group F5: machine alias for host.
    def test_host_from_machine_alias_group_f5(self) -> None:
        out = normalize_join_keys(_rec(machine="es-node-01"))
        assert out["join"]["host"] == "es-node-01"

    def test_heterogeneous_records_share_canonical_keys(self) -> None:
        # CloudWatch-style + Loki-style + k8s-style records for the same host.
        aws = normalize_join_keys(_rec(instance="HOST-A", traceId="trace-1"))
        loki = normalize_join_keys(_rec(hostname="host-a", trace_id="trace-1"))
        k8s = normalize_join_keys(_rec(node="Host-A:10250", **{"x-b3-traceid": "trace-1"}))
        assert aws["join"]["host"] == loki["join"]["host"] == k8s["join"]["host"] == "host-a"
        assert aws["join"]["trace_id"] == loki["join"]["trace_id"] == k8s["join"]["trace_id"] == "trace-1"


# --------------------------------------------------------------------------- #
# normalize_join_keys: span / request correlation keys + Datadog tags folding
# --------------------------------------------------------------------------- #
class TestSpanAndRequestJoinKeys:
    @pytest.mark.parametrize("alias", ["span_id", "x-b3-spanid", "span.id"])
    def test_span_id_from_aliases(self, alias: str) -> None:
        out = normalize_join_keys(_rec(**{alias: "s-123"}))
        assert out["join"]["span_id"] == "s-123"

    @pytest.mark.parametrize("alias", ["request_id", "correlation_id", "x-request-id"])
    def test_request_id_from_aliases(self, alias: str) -> None:
        out = normalize_join_keys(_rec(**{alias: "r-456"}))
        assert out["join"]["request_id"] == "r-456"

    # Group F1: underscore/dot span_id aliases not already covered above.
    @pytest.mark.parametrize("alias", ["spanid", "x_b3_spanid"])
    def test_span_id_from_aliases_group_f1(self, alias: str) -> None:
        out = normalize_join_keys(_rec(**{alias: "s-999"}))
        assert out["join"]["span_id"] == "s-999"

    # Group F2: underscore/dot request_id aliases not already covered above.
    @pytest.mark.parametrize("alias", ["requestid", "request.id", "correlationid", "correlation.id", "x_request_id"])
    def test_request_id_from_aliases_group_f2(self, alias: str) -> None:
        out = normalize_join_keys(_rec(**{alias: "r-789"}))
        assert out["join"]["request_id"] == "r-789"


class TestDatadogTagsFolding:
    def test_tags_list_folds_into_canonical_join_keys(self) -> None:
        out = normalize_join_keys(_rec(tags=["host:web-01", "trace_id:t1", "service:checkout"]))
        assert out["join"]["host"] == "web-01"
        assert out["join"]["trace_id"] == "t1"
        assert out["join"]["service"] == "checkout"

    def test_direct_label_beats_tags_list_entry(self) -> None:
        # A direct (non-tag) label of the same name always wins over the tag.
        out = normalize_join_keys(_rec(service="real-service", tags=["service:from-tag"]))
        assert out["join"]["service"] == "real-service"

    # Group F4: span_id and request_id entries in the Datadog tags list.
    def test_span_and_request_id_from_tags_group_f4(self) -> None:
        out = normalize_join_keys(_rec(tags=["span_id:s-001", "request_id:r-002"]))
        assert out["join"]["span_id"] == "s-001"
        assert out["join"]["request_id"] == "r-002"


# --------------------------------------------------------------------------- #
# Severity mapping
# --------------------------------------------------------------------------- #
class TestSeverityMapping:
    @pytest.mark.parametrize(
        ("token", "expected"),
        [
            ("DEBUG", "debug"),
            ("trace", "debug"),
            ("verbose", "debug"),
            ("info", "info"),
            ("Notice", "info"),
            ("success", "info"),
            ("succeeded", "info"),
            ("ok", "info"),
            ("WARN", "warn"),
            ("Warning", "warn"),
            ("degraded", "warn"),
            ("error", "error"),
            ("ERR", "error"),
            ("failed", "error"),
            ("failure", "error"),
            ("critical", "critical"),
            ("FATAL", "critical"),
            ("emergency", "critical"),
            ("panic", "critical"),
        ],
    )
    def test_level_tokens_map_to_canonical(self, token: str, expected: str) -> None:
        out = normalize_join_keys(_rec(level=token))
        assert out["join"]["severity"] == expected
        assert expected in CANONICAL_SEVERITIES

    @pytest.mark.parametrize(
        ("priority", "expected"),
        [("0", "critical"), ("3", "error"), ("4", "warn"), ("6", "info"), ("7", "debug")],
    )
    def test_syslog_numeric_priority(self, priority: str, expected: str) -> None:
        out = normalize_join_keys(_rec(priority=priority))
        assert out["join"]["severity"] == expected

    def test_severity_from_status_label(self) -> None:
        out = normalize_join_keys(_rec(status="failed"))
        assert out["join"]["severity"] == "error"

    def test_severity_falls_back_to_record_level_or_status(self) -> None:
        rec = _rec()
        rec["level_or_status"] = "error"
        out = normalize_join_keys(rec)
        assert out["join"]["severity"] == "error"

    def test_label_severity_beats_record_field(self) -> None:
        rec = _rec(level="critical")
        rec["level_or_status"] = "info"
        out = normalize_join_keys(rec)
        assert out["join"]["severity"] == "critical"

    def test_unknown_severity_token_omitted(self) -> None:
        rec = _rec(level="bananas")
        rec["level_or_status"] = "also-bananas"
        out = normalize_join_keys(rec)
        assert "severity" not in out["join"]


# --------------------------------------------------------------------------- #
# correlate
# --------------------------------------------------------------------------- #
class TestCorrelate:
    def test_groups_by_trace_id(self) -> None:
        records = [
            normalize_join_keys(_rec(trace_id="t1", host="a")),
            normalize_join_keys(_rec(trace_id="t2", host="b")),
            normalize_join_keys(_rec(traceId="t1", host="c")),
        ]
        groups = correlate(records, by="trace_id")
        assert set(groups) == {"t1", "t2"}
        assert len(groups["t1"]) == 2
        assert len(groups["t2"]) == 1

    def test_normalizes_raw_records_on_the_fly(self) -> None:
        # Records without a precomputed "join" still group correctly.
        records = [_rec(host="Web-01:80"), _rec(hostname="web-01")]
        groups = correlate(records, by="host")
        assert list(groups) == ["web-01"]
        assert len(groups["web-01"]) == 2

    def test_records_missing_key_are_dropped(self) -> None:
        records = [_rec(trace_id="t1"), _rec(host="no-trace")]
        groups = correlate(records, by="trace_id")
        assert set(groups) == {"t1"}
        assert len(groups["t1"]) == 1

    def test_group_by_severity(self) -> None:
        records = [_rec(level="error"), _rec(level="ERR"), _rec(level="info")]
        groups = correlate(records, by="severity")
        assert set(groups) == {"error", "info"}
        assert len(groups["error"]) == 2

    def test_group_by_structured_k8s_key(self) -> None:
        records = [
            _rec(namespace="prod", pod="p1"),
            _rec(namespace="prod", pod="p1"),
            _rec(namespace="prod", pod="p2"),
        ]
        groups = correlate(records, by="k8s")
        assert groups["namespace=prod,pod=p1"] and len(groups["namespace=prod,pod=p1"]) == 2
        assert len(groups["namespace=prod,pod=p2"]) == 1

    def test_insertion_order_preserved(self) -> None:
        records = [_rec(trace_id="z"), _rec(trace_id="a"), _rec(trace_id="z")]
        groups = correlate(records, by="trace_id")
        assert list(groups) == ["z", "a"]

    def test_empty_input(self) -> None:
        assert correlate([], by="trace_id") == {}


# --------------------------------------------------------------------------- #
# auth_family classification matrix
# --------------------------------------------------------------------------- #
class TestAuthFamily:
    @pytest.mark.parametrize(
        ("name", "family"),
        [
            ("cloudwatch", "aws"),
            ("aws_s3", "aws"),
            ("prod-cloudwatch", "aws"),
            ("x-ray", "aws"),
            ("xray", "aws"),
            ("azure_monitor", "azure"),
            ("entra", "azure"),
            ("graph", "azure"),
            ("gcp_logging", "gcp"),
            ("stackdriver", "gcp"),
            ("loki", "grafana"),
            ("tempo", "grafana"),
            ("grafana_oncall", "grafana"),
            ("mimir", "grafana"),
            ("datadog", "datadog"),
            ("elasticsearch", "elastic"),
            ("github_actions", "github"),
            ("gitlab", "gitlab"),
            ("splunk", "splunk"),
            ("newrelic", "newrelic"),
            ("pagerduty", "pagerduty"),
        ],
    )
    def test_classification_matrix(self, name: str, family: str) -> None:
        assert auth_family(name) == family

    def test_case_insensitive(self) -> None:
        assert auth_family("CloudWatch") == "aws"
        assert auth_family("LOKI") == "grafana"

    def test_unknown_name(self) -> None:
        assert auth_family("some-random-thing") == "unknown"

    def test_empty_name(self) -> None:
        assert auth_family("") == "unknown"

    def test_every_family_has_at_least_one_token(self) -> None:
        for family, tokens in AUTH_FAMILY_PREFIXES.items():
            assert tokens, f"family {family} has no tokens"
            for token in tokens:
                assert auth_family(token) == family


# --------------------------------------------------------------------------- #
# bundle_credentials: env-var NAMES only, never values
# --------------------------------------------------------------------------- #
class TestBundleCredentials:
    def test_collects_env_names_per_family(self) -> None:
        configs = [
            {"source": "cloudwatch", "token_env": "AWS_ACCESS_KEY_ID", "secret_env": "AWS_SECRET_ACCESS_KEY"},
            {"source": "loki", "token_env": "GRAFANA_API_TOKEN"},
        ]
        bundle = bundle_credentials(configs)
        assert bundle["aws"] == ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"]
        assert bundle["grafana"] == ["GRAFANA_API_TOKEN"]

    def test_one_bundle_fans_out_to_family(self) -> None:
        # Two AWS connectors sharing one credential set -> deduped name list.
        configs = [
            {"source": "aws_cloudwatch", "key_env": "AWS_ACCESS_KEY_ID"},
            {"source": "aws_xray", "key_env": "AWS_ACCESS_KEY_ID"},
        ]
        bundle = bundle_credentials(configs)
        assert bundle["aws"] == ["AWS_ACCESS_KEY_ID"]

    def test_only_env_suffixed_keys_collected(self) -> None:
        configs = [{"source": "datadog", "api_key_env": "DD_API_KEY", "endpoint": "https://api.datadoghq.com"}]
        bundle = bundle_credentials(configs)
        assert bundle["datadog"] == ["DD_API_KEY"]

    def test_never_emits_secret_values(self) -> None:
        # A config that (wrongly) also carries a raw secret value must never have
        # that value leak into the bundle — only the *_env NAME is collected.
        secret_value = "super-secret-token-value-DO-NOT-LEAK"
        configs = [{"source": "splunk", "token_env": "SPLUNK_HEC_TOKEN", "token": secret_value}]
        bundle = bundle_credentials(configs)
        assert bundle["splunk"] == ["SPLUNK_HEC_TOKEN"]
        flat = [v for names in bundle.values() for v in names]
        assert secret_value not in flat

    def test_explicit_family_key_wins(self) -> None:
        configs = [{"family": "AWS", "token_env": "MY_TOKEN"}]
        bundle = bundle_credentials(configs)
        assert bundle["aws"] == ["MY_TOKEN"]

    def test_list_valued_env_key(self) -> None:
        configs = [{"source": "azure_monitor", "creds_env": ["AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"]}]
        bundle = bundle_credentials(configs)
        assert bundle["azure"] == ["AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"]

    def test_config_with_no_env_keys_registers_empty_family(self) -> None:
        configs = [{"source": "loki", "endpoint": "https://loki"}]
        bundle = bundle_credentials(configs)
        assert bundle == {"grafana": []}

    def test_empty_configs(self) -> None:
        assert bundle_credentials([]) == {}


# --------------------------------------------------------------------------- #
# Malformed-input safety: never raise
# --------------------------------------------------------------------------- #
class TestMalformedSafety:
    @pytest.mark.parametrize("bad", [None, 42, "string", [], object()])
    def test_normalize_join_keys_non_dict(self, bad: object) -> None:
        assert normalize_join_keys(bad) == {}  # type: ignore[arg-type]

    def test_normalize_join_keys_labels_not_a_dict(self) -> None:
        rec = _rec()
        rec["labels"] = ["not", "a", "dict"]
        out = normalize_join_keys(rec)
        assert out["join"] == {} or "severity" in out["join"]

    def test_normalize_join_keys_missing_labels_key(self) -> None:
        out = normalize_join_keys({"source": "x"})
        assert out["join"] == {}

    def test_normalize_join_keys_non_string_label_value(self) -> None:
        out = normalize_join_keys(_rec(host=12345, trace_id=None))
        assert out["join"]["host"] == "12345"
        assert "trace_id" not in out["join"]

    @pytest.mark.parametrize("bad", [None, 42, "string", {}])
    def test_correlate_non_list(self, bad: object) -> None:
        assert correlate(bad, by="trace_id") == {}  # type: ignore[arg-type]

    def test_correlate_skips_non_dict_members(self) -> None:
        records = [_rec(trace_id="t1"), None, 7, "x"]
        groups = correlate(records, by="trace_id")  # type: ignore[list-item]
        assert set(groups) == {"t1"}

    @pytest.mark.parametrize("bad", [None, 42, {}])
    def test_auth_family_non_string(self, bad: object) -> None:
        assert auth_family(bad) == "unknown"  # type: ignore[arg-type]

    @pytest.mark.parametrize("bad", [None, 42, "string", {}])
    def test_bundle_credentials_non_list(self, bad: object) -> None:
        assert bundle_credentials(bad) == {}  # type: ignore[arg-type]

    def test_bundle_credentials_skips_non_dict_members(self) -> None:
        configs = [{"source": "loki", "token_env": "T"}, None, 7, "x"]
        bundle = bundle_credentials(configs)  # type: ignore[list-item]
        assert bundle == {"grafana": ["T"]}


# --------------------------------------------------------------------------- #
# sanitize_metric_value: finite-float coercion, NaN/Inf/bool -> None
# --------------------------------------------------------------------------- #
class TestSanitizeMetricValue:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (0, 0.0),
            (0.0, 0.0),
            (42, 42.0),
            (-3.5, -3.5),
            ("12.5", 12.5),
            (float("nan"), None),
            (float("inf"), None),
            (float("-inf"), None),
            (None, None),
            (True, None),
            (False, None),
            ("not-a-number", None),
            (object(), None),
        ],
    )
    def test_sanitize_metric_value(self, raw: object, expected: float | None) -> None:
        assert sanitize_metric_value(raw) == expected
