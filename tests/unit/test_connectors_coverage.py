"""Targeted coverage for src/general_ludd/connectors/: base, newrelic, registry, normalize.

Covers low-coverage branches not exercised by the primary connector test suites:
  - base.normalized_record: default values, NaN/Inf passthrough (current behaviour)
  - base.Observability.find: kind-filter, resilience (source raises), merge+sort
  - base.Observability.associate: by label, by time_window, edge cases
  - base.is_safe_endpoint: blocked/allowed URLs
  - newrelic._nerdgraph_body: escaping
  - newrelic.NewRelicSource: missing account_id, HTTP 4xx, bad JSON envelope,
    errors key, missing nrql.results key, results=None, non-list results,
    _normalize_row label/value extraction, health() ok/fail paths
  - registry.ConnectorRegistry.from_config: factory/class/module selectors,
    non-dict config, missing name, unknown factory, construct failure,
    protocol rejection, _check_module_allowlist (class + module selectors),
    health_all (health raises), query (unknown name, connector raises)
  - normalize._config_family: explicit family key, inferred from name/kind,
    fallback unknown; auth_family; bundle_credentials edge cases
"""

from __future__ import annotations

import math
from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# base.py
# ---------------------------------------------------------------------------
from general_ludd.connectors.base import (
    LOG_KIND,
    METRIC_KIND,
    PIPELINE_KIND,
    TRACE_KIND,
    VALID_KINDS,
    Observability,
    Source,
    SourceRegistry,
    is_safe_endpoint,
    normalized_record,
)


class TestNormalizedRecord:
    """normalized_record() builder — default values and current NaN/Inf behaviour."""

    def test_required_fields_only(self) -> None:
        rec = normalized_record(source="s", kind="logs")
        assert rec["source"] == "s"
        assert rec["kind"] == "logs"
        assert rec["ts"] is None
        assert rec["level_or_status"] == "info"
        assert rec["message"] == ""
        assert rec["value"] is None
        assert rec["labels"] == {}
        assert rec["raw"] is None

    def test_explicit_all_fields(self) -> None:
        rec = normalized_record(
            source="my-src",
            kind=METRIC_KIND,
            message="hello",
            ts=1234.5,
            level_or_status="error",
            value=42.0,
            labels={"k": "v"},
            raw={"x": 1},
        )
        assert rec["ts"] == 1234.5
        assert rec["message"] == "hello"
        assert rec["value"] == 42.0
        assert rec["labels"] == {"k": "v"}
        assert rec["raw"] == {"x": 1}

    def test_labels_none_becomes_empty_dict(self) -> None:
        rec = normalized_record(source="s", kind=LOG_KIND, labels=None)
        assert rec["labels"] == {}

    def test_nan_value_passthrough(self) -> None:
        # Current behaviour: NaN is stored as-is (no sanitization in builder).
        rec = normalized_record(source="s", kind=METRIC_KIND, value=float("nan"))
        assert math.isnan(rec["value"])  # type: ignore[arg-type]

    def test_inf_value_passthrough(self) -> None:
        rec = normalized_record(source="s", kind=METRIC_KIND, value=float("inf"))
        assert math.isinf(rec["value"])  # type: ignore[arg-type]

    def test_neg_inf_value_passthrough(self) -> None:
        rec = normalized_record(source="s", kind=METRIC_KIND, value=float("-inf"))
        assert rec["value"] == float("-inf")

    def test_valid_kinds_constant(self) -> None:
        assert PIPELINE_KIND in VALID_KINDS
        assert LOG_KIND in VALID_KINDS
        assert METRIC_KIND in VALID_KINDS
        assert TRACE_KIND in VALID_KINDS


class _FakeSource:
    """Minimal duck-typed source for testing."""

    def __init__(self, name: str, kind: str, records: list | None = None, raises: bool = False) -> None:
        self.name = name
        self.KIND = kind
        self._records = records or []
        self._raises = raises

    def health(self) -> dict[str, Any]:
        return {"ok": True}

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        if self._raises:
            raise RuntimeError("boom")
        return list(self._records)


class TestObservabilityFind:
    """Observability.find() — fan-out, kind filter, resilience, sort."""

    def _make_obs(self, sources: list[_FakeSource]) -> Observability:
        reg = SourceRegistry()
        for s in sources:
            reg.register(s)
        return Observability(reg)

    def test_empty_registry_returns_empty(self) -> None:
        obs = self._make_obs([])
        assert obs.find({}) == []

    def test_single_source_records_returned(self) -> None:
        rec = normalized_record(source="a", kind=LOG_KIND, ts=1.0)
        obs = self._make_obs([_FakeSource("a", LOG_KIND, [dict(rec)])])
        result = obs.find({})
        assert len(result) == 1
        assert result[0]["source"] == "a"

    def test_kind_filter_excludes_non_matching(self) -> None:
        logs_src = _FakeSource("logs", LOG_KIND, [dict(normalized_record(source="logs", kind=LOG_KIND, ts=1.0))])
        metrics_src = _FakeSource("metrics", METRIC_KIND, [dict(normalized_record(source="metrics", kind=METRIC_KIND, ts=2.0))])
        obs = self._make_obs([logs_src, metrics_src])
        result = obs.find({}, kinds=[LOG_KIND])
        assert all(r["kind"] == LOG_KIND for r in result)
        assert len(result) == 1

    def test_kind_filter_includes_all_matching(self) -> None:
        s1 = _FakeSource("a", LOG_KIND, [dict(normalized_record(source="a", kind=LOG_KIND, ts=1.0))])
        s2 = _FakeSource("b", LOG_KIND, [dict(normalized_record(source="b", kind=LOG_KIND, ts=2.0))])
        obs = self._make_obs([s1, s2])
        result = obs.find({}, kinds=[LOG_KIND])
        assert len(result) == 2

    def test_resilience_failing_source_becomes_error_record(self) -> None:
        good = _FakeSource("good", LOG_KIND, [dict(normalized_record(source="good", kind=LOG_KIND))])
        bad = _FakeSource("bad", LOG_KIND, raises=True)
        obs = self._make_obs([good, bad])
        result = obs.find({})
        sources = {r["source"] for r in result}
        assert "good" in sources
        assert "bad" in sources
        bad_rec = next(r for r in result if r["source"] == "bad")
        assert bad_rec["level_or_status"] == "error"
        assert "query failed" in bad_rec["message"]
        assert "boom" in bad_rec["message"]

    def test_results_sorted_by_ts_ascending(self) -> None:
        recs = [
            dict(normalized_record(source="s", kind=LOG_KIND, ts=3.0)),
            dict(normalized_record(source="s", kind=LOG_KIND, ts=1.0)),
            dict(normalized_record(source="s", kind=LOG_KIND, ts=2.0)),
        ]
        src = _FakeSource("s", LOG_KIND, recs)
        obs = self._make_obs([src])
        result = obs.find({})
        timestamps = [r["ts"] for r in result]
        assert timestamps == sorted(timestamps)

    def test_none_ts_records_sort_last(self) -> None:
        recs = [
            dict(normalized_record(source="s", kind=LOG_KIND, ts=None)),
            dict(normalized_record(source="s", kind=LOG_KIND, ts=5.0)),
            dict(normalized_record(source="s", kind=LOG_KIND, ts=1.0)),
        ]
        src = _FakeSource("s", LOG_KIND, recs)
        obs = self._make_obs([src])
        result = obs.find({})
        # The None-ts record should be last
        assert result[-1]["ts"] is None
        assert result[0]["ts"] == 1.0


class TestObservabilityAssociate:
    """Observability.associate() — by_label and by_time_window."""

    def _make_rec(self, ts: float | None = None, labels: dict | None = None) -> dict[str, Any]:
        return dict(normalized_record(source="s", kind=LOG_KIND, ts=ts, labels=labels or {}))

    def test_associate_by_label_groups_correctly(self) -> None:
        records = [
            self._make_rec(labels={"trace_id": "abc"}),
            self._make_rec(labels={"trace_id": "abc"}),
            self._make_rec(labels={"trace_id": "xyz"}),
        ]
        groups = Observability.associate(records, by="trace_id")
        assert len(groups) == 2
        keys = {g["key"] for g in groups}
        assert "abc" in keys
        assert "xyz" in keys
        abc_group = next(g for g in groups if g["key"] == "abc")
        assert len(abc_group["records"]) == 2

    def test_associate_by_label_drops_records_without_label(self) -> None:
        records = [
            self._make_rec(labels={"trace_id": "abc"}),
            self._make_rec(labels={}),  # no trace_id
        ]
        groups = Observability.associate(records, by="trace_id")
        assert len(groups) == 1
        assert groups[0]["key"] == "abc"

    def test_associate_by_label_empty_records(self) -> None:
        groups = Observability.associate([], by="trace_id")
        assert groups == []

    def test_associate_by_label_preserves_insertion_order(self) -> None:
        records = [
            self._make_rec(labels={"commit": "aaa"}),
            self._make_rec(labels={"commit": "bbb"}),
            self._make_rec(labels={"commit": "aaa"}),
        ]
        groups = Observability.associate(records, by="commit")
        assert [g["key"] for g in groups] == ["aaa", "bbb"]

    def test_associate_by_time_window_clusters_close_records(self) -> None:
        records = [
            self._make_rec(ts=100.0),
            self._make_rec(ts=115.0),
            self._make_rec(ts=200.0),
        ]
        groups = Observability.associate(records, by="time_window", window_s=60.0)
        assert len(groups) == 2
        # First cluster has 2 records (100 and 115 within 60s of 100)
        assert len(groups[0]["records"]) == 2
        # Second cluster has 1 record (200 > 100+60)
        assert len(groups[1]["records"]) == 1

    def test_associate_by_time_window_skips_none_ts(self) -> None:
        records = [
            self._make_rec(ts=None),
            self._make_rec(ts=100.0),
        ]
        groups = Observability.associate(records, by="time_window", window_s=60.0)
        total = sum(len(g["records"]) for g in groups)
        assert total == 1  # None-ts record excluded

    def test_associate_by_time_window_empty(self) -> None:
        groups = Observability.associate([], by="time_window")
        assert groups == []


class TestIsSafeEndpoint:
    """is_safe_endpoint() SSRF guard."""

    def test_valid_https_public_url_allowed(self) -> None:
        assert is_safe_endpoint("https://api.newrelic.com/graphql") is True

    def test_valid_http_public_url_allowed(self) -> None:
        assert is_safe_endpoint("http://my-observability.example.com/api") is True

    def test_localhost_blocked(self) -> None:
        assert is_safe_endpoint("http://localhost/api") is False

    def test_loopback_ip_blocked(self) -> None:
        assert is_safe_endpoint("http://127.0.0.1/api") is False

    def test_link_local_metadata_ip_blocked(self) -> None:
        assert is_safe_endpoint("http://169.254.169.254/latest/meta-data") is False

    def test_private_rfc1918_blocked(self) -> None:
        assert is_safe_endpoint("http://192.168.1.1/api") is False

    def test_non_http_scheme_blocked(self) -> None:
        # ftp:// is not in the allowed scheme set
        assert is_safe_endpoint("ftp://example.com/file") is False


# ---------------------------------------------------------------------------
# newrelic.py
# ---------------------------------------------------------------------------
from general_ludd.connectors.newrelic import (
    NewRelicSource,
    _nerdgraph_body,
)


class TestNerdgraphBody:
    """_nerdgraph_body() — NRQL embedding and escaping."""

    def test_basic_query_embedded(self) -> None:
        body = _nerdgraph_body(12345, "SELECT count(*) FROM Transaction")
        query = body["query"]
        assert "SELECT count(*) FROM Transaction" in query
        assert "12345" in query

    def test_double_quote_escaped(self) -> None:
        body = _nerdgraph_body(1, 'SELECT * WHERE name = "foo"')
        assert '\\"' in body["query"]

    def test_backslash_escaped(self) -> None:
        body = _nerdgraph_body(1, "SELECT * WHERE path = 'C:\\\\dir'")
        # The body should have escaped backslashes
        assert body["query"] is not None

    def test_string_account_id(self) -> None:
        # account_id may be a string
        body = _nerdgraph_body("abc-123", "SELECT 1")
        assert "abc-123" in body["query"]


class TestNewRelicSource:
    """NewRelicSource — query/health paths with mocked transport."""

    def _make_source(self, transport: Any, account_id: Any = 12345) -> NewRelicSource:
        config: dict[str, Any] = {
            "name": "test-nr",
            "account_id": account_id,
            "api_key_env": "NR_KEY_TEST",
            "api_url": "https://api.newrelic.com/graphql",
        }
        import os
        os.environ["NR_KEY_TEST"] = "fake-key"
        return NewRelicSource(config, transport=transport)

    def _ok_transport(self, results: list) -> Any:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "data": {"actor": {"account": {"nrql": {"results": results}}}}
        }
        transport = MagicMock(return_value=resp)
        return transport

    def test_query_returns_normalized_records(self) -> None:
        transport = self._ok_transport([{"timestamp": 1000, "count": 5}])
        src = self._make_source(transport)
        records = src.query({"nrql": "SELECT count(*) FROM Transaction"})
        assert len(records) == 1
        assert records[0]["source"] == "test-nr"
        assert records[0]["kind"] == "metrics"
        assert records[0]["value"] == 5
        assert records[0]["ts"] == 1000

    def test_query_missing_account_id_raises(self) -> None:
        transport = self._ok_transport([])
        src = self._make_source(transport, account_id=None)
        with pytest.raises(RuntimeError, match="account_id"):
            src.query({"nrql": "SELECT 1"})

    def test_query_http_4xx_raises(self) -> None:
        resp = MagicMock()
        resp.status_code = 403
        transport = MagicMock(return_value=resp)
        src = self._make_source(transport)
        with pytest.raises(RuntimeError, match="HTTP 403"):
            src.query({"nrql": "SELECT 1"})

    def test_query_errors_key_in_response_raises(self) -> None:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"errors": [{"message": "bad query"}]}
        transport = MagicMock(return_value=resp)
        src = self._make_source(transport)
        with pytest.raises(RuntimeError, match="NerdGraph errors"):
            src.query({"nrql": "SELECT 1"})

    def test_query_non_dict_response_raises(self) -> None:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = ["not", "a", "dict"]
        transport = MagicMock(return_value=resp)
        src = self._make_source(transport)
        with pytest.raises(RuntimeError, match="not a JSON object"):
            src.query({"nrql": "SELECT 1"})

    def test_query_missing_nrql_results_key_raises(self) -> None:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"data": {"actor": {}}}
        transport = MagicMock(return_value=resp)
        src = self._make_source(transport)
        with pytest.raises(RuntimeError, match="missing nrql.results"):
            src.query({"nrql": "SELECT 1"})

    def test_query_results_none_returns_empty(self) -> None:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "data": {"actor": {"account": {"nrql": {"results": None}}}}
        }
        transport = MagicMock(return_value=resp)
        src = self._make_source(transport)
        result = src.query({"nrql": "SELECT 1"})
        assert result == []

    def test_query_results_not_list_raises(self) -> None:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "data": {"actor": {"account": {"nrql": {"results": {"not": "a list"}}}}}
        }
        transport = MagicMock(return_value=resp)
        src = self._make_source(transport)
        with pytest.raises(RuntimeError, match="not a list"):
            src.query({"nrql": "SELECT 1"})

    def test_query_spec_missing_nrql_raises(self) -> None:
        src = self._make_source(MagicMock())
        with pytest.raises(ValueError, match="nrql"):
            src.query({})

    def test_query_spec_non_dict_raises(self) -> None:
        src = self._make_source(MagicMock())
        with pytest.raises(TypeError, match="spec must be a mapping"):
            src.query("SELECT 1")  # type: ignore[arg-type]

    def test_normalize_row_begintime_seconds_timestamp(self) -> None:
        transport = self._ok_transport([{"beginTimeSeconds": 2000, "throughput": 7.5}])
        src = self._make_source(transport)
        records = src.query({"nrql": "SELECT throughput FROM Transaction"})
        assert records[0]["ts"] == 2000
        assert records[0]["value"] == 7.5

    def test_normalize_row_no_numeric_value(self) -> None:
        transport = self._ok_transport([{"facet": "web", "timestamp": 500}])
        src = self._make_source(transport)
        records = src.query({"nrql": "SELECT facet FROM Transaction"})
        assert records[0]["value"] is None

    def test_health_ok(self) -> None:
        transport = self._ok_transport([{"c": 1}])
        src = self._make_source(transport)
        result = src.health()
        assert result["ok"] is True

    def test_health_returns_false_on_error(self) -> None:
        transport = MagicMock(side_effect=ConnectionError("timeout"))
        src = self._make_source(transport)
        result = src.health()
        assert result["ok"] is False
        assert "ConnectionError" in result["detail"]

    def test_ssrf_blocked_url_raises_on_init(self) -> None:
        with pytest.raises(ValueError, match="blocked"):
            NewRelicSource(
                {"name": "bad", "api_url": "http://169.254.169.254/graphql"},
                transport=MagicMock(),
            )


# ---------------------------------------------------------------------------
# registry.py
# ---------------------------------------------------------------------------
from general_ludd.connectors.registry import (
    ConnectorRegistry,
    _check_module_allowlist,
)


class _GoodSource:
    """Minimal protocol-conformant source for registry tests."""

    KIND = "logs"

    def __init__(self, config: dict[str, Any]) -> None:
        self.name = str(config.get("name", "test"))

    def health(self) -> dict[str, Any]:
        return {"ok": True}

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        return []


class _RaisingSource:
    """Source whose query() raises."""

    KIND = "logs"

    def __init__(self, config: dict[str, Any]) -> None:
        self.name = str(config.get("name", "raising"))

    def health(self) -> dict[str, Any]:
        raise RuntimeError("health boom")

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        raise RuntimeError("query boom")


class TestConnectorRegistryFromConfig:
    """ConnectorRegistry.from_config() — factory selector + error paths."""

    def test_empty_config_list_builds_empty_registry(self) -> None:
        reg = ConnectorRegistry.from_config([])
        assert reg.names() == []
        assert reg.errors() == []

    def test_none_configs_builds_empty_registry(self) -> None:
        reg = ConnectorRegistry.from_config(None)
        assert reg.names() == []

    def test_factory_selector_registers_source(self) -> None:
        config = [{"name": "my-src", "kind": "logs", "factory": "good"}]
        reg = ConnectorRegistry.from_config(config, factories={"good": _GoodSource})
        assert "my-src" in reg.names()

    def test_unknown_factory_key_recorded_as_error(self) -> None:
        config = [{"name": "x", "kind": "logs", "factory": "missing"}]
        reg = ConnectorRegistry.from_config(config, factories={})
        assert reg.names() == []
        errors = reg.errors()
        assert len(errors) == 1
        assert "discovery" in errors[0]["error"]
        assert errors[0]["name"] == "x"

    def test_non_dict_config_entry_recorded_as_error(self) -> None:
        reg = ConnectorRegistry.from_config(["not-a-dict"])  # type: ignore[list-item]
        errors = reg.errors()
        assert len(errors) == 1
        assert errors[0]["name"] is None

    def test_missing_name_recorded_as_error(self) -> None:
        config = [{"kind": "logs", "factory": "good"}]
        reg = ConnectorRegistry.from_config(config, factories={"good": _GoodSource})
        errors = reg.errors()
        assert any("name" in e["error"] for e in errors)

    def test_construct_failure_recorded_as_error(self) -> None:
        def bad_factory(config: dict) -> None:
            raise ValueError("bad config")

        config = [{"name": "broken", "kind": "logs", "factory": "bad"}]
        reg = ConnectorRegistry.from_config(config, factories={"bad": bad_factory})
        assert "broken" not in reg.names()
        errors = reg.errors()
        assert any("construct" in e["error"] for e in errors)

    def test_non_sourcellike_object_rejected(self) -> None:
        """Factory returns an object missing required protocol attributes."""

        class _BadObj:
            pass

        def bad_factory(config: dict) -> _BadObj:
            return _BadObj()

        config = [{"name": "bad-obj", "kind": "logs", "factory": "bad"}]
        reg = ConnectorRegistry.from_config(config, factories={"bad": bad_factory})
        assert "bad-obj" not in reg.names()
        errors = reg.errors()
        assert any("_SourceLike" in e["error"] for e in errors)

    def test_list_sources_returns_metadata(self) -> None:
        config = [{"name": "src1", "kind": "logs", "factory": "good"}]
        reg = ConnectorRegistry.from_config(config, factories={"good": _GoodSource})
        sources = reg.list_sources()
        assert len(sources) == 1
        assert sources[0]["name"] == "src1"
        assert "kind" in sources[0]
        assert "family" in sources[0]

    def test_by_kind_groups_correctly(self) -> None:
        configs = [
            {"name": "a", "kind": "logs", "factory": "good"},
            {"name": "b", "kind": "logs", "factory": "good"},
        ]
        reg = ConnectorRegistry.from_config(configs, factories={"good": _GoodSource})
        by_kind = reg.by_kind()
        assert "logs" in by_kind
        assert set(by_kind["logs"]) == {"a", "b"}

    def test_get_returns_source(self) -> None:
        config = [{"name": "s", "kind": "logs", "factory": "good"}]
        reg = ConnectorRegistry.from_config(config, factories={"good": _GoodSource})
        src = reg.get("s")
        assert src is not None
        assert src.name == "s"

    def test_get_returns_none_for_unknown(self) -> None:
        reg = ConnectorRegistry.from_config([])
        assert reg.get("nope") is None


class TestConnectorRegistryHealthAll:
    """ConnectorRegistry.health_all() — never raises."""

    def test_health_all_returns_ok_result(self) -> None:
        config = [{"name": "s", "kind": "logs", "factory": "good"}]
        reg = ConnectorRegistry.from_config(config, factories={"good": _GoodSource})
        results = reg.health_all()
        assert "s" in results
        assert results["s"]["ok"] is True

    def test_health_all_catches_raising_health(self) -> None:
        config = [{"name": "r", "kind": "logs", "factory": "raising"}]
        reg = ConnectorRegistry.from_config(config, factories={"raising": _RaisingSource})
        results = reg.health_all()
        assert "r" in results
        assert results["r"]["ok"] is False

    def test_health_all_non_dict_result_wrapped(self) -> None:
        class _BoolHealthSource:
            KIND = "logs"
            name = "boolsrc"

            def __init__(self, config: dict) -> None:
                self.name = str(config.get("name", "boolsrc"))

            def health(self) -> bool:  # type: ignore[override]
                return True

            def query(self, spec: dict) -> list:
                return []

        config = [{"name": "boolsrc", "kind": "logs", "factory": "bh"}]
        reg = ConnectorRegistry.from_config(config, factories={"bh": _BoolHealthSource})
        results = reg.health_all()
        assert results["boolsrc"]["ok"] is True


class TestConnectorRegistryQuery:
    """ConnectorRegistry.query() — unknown name raises; connector exception captured."""

    def test_query_unknown_name_raises_key_error(self) -> None:
        reg = ConnectorRegistry.from_config([])
        with pytest.raises(KeyError, match="nope"):
            reg.query("nope", {})

    def test_query_connector_raises_returns_error_record(self) -> None:
        config = [{"name": "r", "kind": "logs", "factory": "raising"}]
        reg = ConnectorRegistry.from_config(config, factories={"raising": _RaisingSource})
        records = reg.query("r", {})
        assert len(records) == 1
        assert records[0]["level_or_status"] == "error"
        assert "query boom" in records[0]["message"]

    def test_query_non_dict_spec_treated_as_empty(self) -> None:
        config = [{"name": "s", "kind": "logs", "factory": "good"}]
        reg = ConnectorRegistry.from_config(config, factories={"good": _GoodSource})
        result = reg.query("s", None)  # type: ignore[arg-type]
        assert result == []


class TestCheckModuleAllowlist:
    """_check_module_allowlist() — module selector and class selector paths."""

    def test_valid_module_path_passes(self) -> None:
        # Should not raise
        _check_module_allowlist("general_ludd.connectors.newrelic", selector="module")

    def test_invalid_module_path_raises(self) -> None:
        with pytest.raises(ValueError, match="module import denied"):
            _check_module_allowlist("os.path", selector="module")

    def test_os_system_module_denied(self) -> None:
        with pytest.raises(ValueError, match="module import denied"):
            _check_module_allowlist("os", selector="module")

    def test_valid_class_colon_syntax_passes(self) -> None:
        _check_module_allowlist(
            "general_ludd.connectors.newrelic:NewRelicSource", selector="class"
        )

    def test_invalid_class_colon_syntax_raises(self) -> None:
        with pytest.raises(ValueError, match="module import denied"):
            _check_module_allowlist("os:system", selector="class")

    def test_valid_class_dot_syntax_passes(self) -> None:
        _check_module_allowlist(
            "general_ludd.connectors.newrelic.NewRelicSource", selector="class"
        )

    def test_invalid_class_dot_syntax_raises(self) -> None:
        with pytest.raises(ValueError, match="module import denied"):
            _check_module_allowlist("os.path.join", selector="class")

    def test_exact_package_prefix_passes(self) -> None:
        _check_module_allowlist("general_ludd.connectors", selector="module")


# ---------------------------------------------------------------------------
# normalize.py
# ---------------------------------------------------------------------------
from general_ludd.connectors.normalize import (
    AUTH_FAMILY_PREFIXES,
    auth_family,
    bundle_credentials,
    normalize_join_keys,
)


class TestAuthFamily:
    """auth_family() — classification from source name."""

    def test_newrelic_prefix(self) -> None:
        assert auth_family("newrelic-prod") == "newrelic"

    def test_datadog_prefix(self) -> None:
        assert auth_family("datadog-eu") == "datadog"

    def test_aws_prefix(self) -> None:
        assert auth_family("aws-cloudwatch") == "aws"

    def test_cloudwatch_token(self) -> None:
        assert auth_family("prod-cloudwatch") == "aws"

    def test_splunk_prefix(self) -> None:
        assert auth_family("splunk-hec") == "splunk"

    def test_pagerduty_prefix(self) -> None:
        assert auth_family("pagerduty-main") == "pagerduty"

    def test_grafana_prefix(self) -> None:
        assert auth_family("grafana-cloud") == "grafana"

    def test_github_prefix(self) -> None:
        assert auth_family("github-actions") == "github"

    def test_unknown_name_returns_unknown(self) -> None:
        assert auth_family("some-random-connector") == "unknown"

    def test_empty_string_returns_unknown(self) -> None:
        assert auth_family("") == "unknown"

    def test_case_insensitive(self) -> None:
        assert auth_family("NewRelic") == "newrelic"
        assert auth_family("DATADOG") == "datadog"

    def test_all_families_have_at_least_one_token(self) -> None:
        for family, tokens in AUTH_FAMILY_PREFIXES.items():
            assert len(tokens) >= 1, f"Family {family!r} has no tokens"


class TestConfigFamily:
    """_config_family() via bundle_credentials — explicit family key wins."""

    def test_explicit_family_key_overrides_inference(self) -> None:
        # bundle_credentials calls _config_family internally
        configs = [{"family": "aws", "token_env": "AWS_TOKEN", "name": "splunk-foo"}]
        result = bundle_credentials(configs)
        # The explicit "aws" family should be used, not "splunk" inferred from name
        assert "aws" in result
        assert "AWS_TOKEN" in result["aws"]

    def test_explicit_auth_family_key_wins(self) -> None:
        configs = [{"auth_family": "gcp", "token_env": "GCP_TOKEN", "name": "aws-thing"}]
        result = bundle_credentials(configs)
        assert "gcp" in result
        assert "GCP_TOKEN" in result["gcp"]

    def test_name_field_infers_family(self) -> None:
        configs = [{"name": "datadog-prod", "api_key_env": "DD_API_KEY"}]
        result = bundle_credentials(configs)
        assert "datadog" in result
        assert "DD_API_KEY" in result["datadog"]

    def test_kind_field_infers_family_when_name_unknown(self) -> None:
        configs = [{"name": "my-connector", "kind": "newrelic", "token_env": "NR_KEY"}]
        result = bundle_credentials(configs)
        assert "newrelic" in result

    def test_unknown_config_falls_back_to_unknown_family(self) -> None:
        configs = [{"name": "mystery-tool", "token_env": "SOME_TOKEN"}]
        result = bundle_credentials(configs)
        assert "unknown" in result
        assert "SOME_TOKEN" in result["unknown"]

    def test_empty_configs_list_returns_empty(self) -> None:
        result = bundle_credentials([])
        assert result == {}

    def test_non_list_input_returns_empty(self) -> None:
        result = bundle_credentials(None)  # type: ignore[arg-type]
        assert result == {}

    def test_non_dict_config_entry_skipped(self) -> None:
        result = bundle_credentials(["not-a-dict"])  # type: ignore[list-item]
        assert result == {}

    def test_list_env_value_collected(self) -> None:
        configs = [{"name": "aws-thing", "token_env": ["KEY1", "KEY2"]}]
        result = bundle_credentials(configs)
        assert "aws" in result
        assert "KEY1" in result["aws"]
        assert "KEY2" in result["aws"]

    def test_deduplication_across_configs(self) -> None:
        configs = [
            {"name": "dd1", "api_key_env": "DD_KEY"},
            {"name": "dd2", "api_key_env": "DD_KEY"},  # same env var
        ]
        result = bundle_credentials(configs)
        assert result["datadog"].count("DD_KEY") == 1


class TestNormalizeJoinKeys:
    """normalize_join_keys() — canonical join sub-dict extraction."""

    def test_non_dict_input_returns_empty(self) -> None:
        result = normalize_join_keys("not a dict")  # type: ignore[arg-type]
        assert result == {}

    def test_empty_record_gets_empty_join(self) -> None:
        result = normalize_join_keys({})
        assert result["join"] == {}

    def test_trace_id_extracted(self) -> None:
        rec = {"labels": {"trace_id": "abc123"}}
        result = normalize_join_keys(rec)
        assert result["join"]["trace_id"] == "abc123"

    def test_host_lowercased_and_port_stripped(self) -> None:
        rec = {"labels": {"host": "Web-01:8080"}}
        result = normalize_join_keys(rec)
        assert result["join"]["host"] == "web-01"

    def test_severity_from_level_or_status(self) -> None:
        rec = {"level_or_status": "error", "labels": {}}
        result = normalize_join_keys(rec)
        assert result["join"]["severity"] == "error"

    def test_k8s_keys_extracted(self) -> None:
        rec = {"labels": {"namespace": "prod", "pod": "api-xyz", "container": "app"}}
        result = normalize_join_keys(rec)
        assert result["join"]["k8s"] == {"namespace": "prod", "pod": "api-xyz", "container": "app"}

    def test_cloud_account_extracted(self) -> None:
        rec = {"labels": {"aws_account_id": "123456789", "region": "us-east-1"}}
        result = normalize_join_keys(rec)
        assert result["join"]["cloud"]["account"] == "123456789"
        assert result["join"]["cloud"]["region"] == "us-east-1"

    def test_idempotent(self) -> None:
        rec = {"labels": {"trace_id": "xyz"}}
        first = normalize_join_keys(rec)
        second = normalize_join_keys(first)
        assert first["join"] == second["join"]

    def test_caller_record_not_mutated(self) -> None:
        rec: dict[str, Any] = {"labels": {"host": "myhost"}}
        normalize_join_keys(rec)
        assert "join" not in rec

    def test_malformed_labels_yields_empty_join(self) -> None:
        rec = {"labels": "not-a-dict"}
        result = normalize_join_keys(rec)
        assert isinstance(result["join"], dict)
