"""Structural tests for connectors/argo_workflows.py — Argo Workflows connector."""

from __future__ import annotations

from general_ludd.connectors.argo_workflows import (
    ArgoWorkflowsSource,
    _guard_base_url,
    _httpx_transport,
)


class TestGuardBaseUrl:
    def test_valid_public_url(self):
        result = _guard_base_url("https://argo.example.com", allow_private=False)
        assert result == "https://argo.example.com"

    def test_allows_private_with_opt_in(self):
        result = _guard_base_url("https://10.0.0.1", allow_private=True)
        assert result == "https://10.0.0.1"

    def test_rejects_bad_scheme(self):
        from general_ludd.connectors._errors import ConnectorConfigError
        try:
            _guard_base_url("ftp://argo.example.com", allow_private=False)
        except ConnectorConfigError:
            return
        raise AssertionError("expected ConnectorConfigError")

    def test_rejects_no_host(self):
        from general_ludd.connectors._errors import ConnectorConfigError
        try:
            _guard_base_url("http:///api", allow_private=False)
        except ConnectorConfigError:
            return
        raise AssertionError("expected ConnectorConfigError")

    def test_strips_trailing_slash(self):
        result = _guard_base_url("https://argo.example.com/", allow_private=False)
        assert result == "https://argo.example.com"


class TestHttpxTransport:
    def test_is_callable(self):
        assert callable(_httpx_transport)


class TestArgoWorkflowsSource:
    def test_minimal_construction(self):
        source = ArgoWorkflowsSource({"base_url": "https://argo.example.com"})
        assert source.KIND == "pipeline"
        assert source.name == "argo_workflows"

    def test_custom_name(self):
        source = ArgoWorkflowsSource(
            {"base_url": "https://argo.example.com", "name": "my-argo"}
        )
        assert source.name == "my-argo"

    def test_with_allow_private(self):
        source = ArgoWorkflowsSource(
            {"base_url": "https://10.0.0.1", "allow_private": True}
        )
        assert source.allow_private is True

    def test_health_returns_dict(self):
        def fake_transport(method, url, headers, timeout):
            return 200, b"{}"

        source = ArgoWorkflowsSource(
            {"base_url": "https://argo.example.com"}, transport=fake_transport
        )
        result = source.health()
        assert "ok" in result
        assert isinstance(result["ok"], bool)
        assert result["ok"] is True

    def test_health_failure_on_500(self):
        def fake_transport(method, url, headers, timeout):
            return 500, b"{}"

        source = ArgoWorkflowsSource(
            {"base_url": "https://argo.example.com"}, transport=fake_transport
        )
        result = source.health()
        assert result["ok"] is False

    def test_health_never_raises_on_transport_error(self):
        def broken_transport(method, url, headers, timeout):
            raise OSError("connection refused")

        source = ArgoWorkflowsSource(
            {"base_url": "https://argo.example.com"}, transport=broken_transport
        )
        result = source.health()
        assert result["ok"] is False

    def test_query_returns_list(self):
        def fake_transport(method, url, headers, timeout):
            return 200, b'{"items": [{"metadata": {"name": "wf1"}, "status": {"phase": "Succeeded"}}]}'

        source = ArgoWorkflowsSource(
            {"base_url": "https://argo.example.com"}, transport=fake_transport
        )
        result = source.query()
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["kind"] == "pipeline"

    def test_query_returns_empty_on_null_items(self):
        def fake_transport(method, url, headers, timeout):
            return 200, b'{"items": null}'

        source = ArgoWorkflowsSource(
            {"base_url": "https://argo.example.com"}, transport=fake_transport
        )
        result = source.query()
        assert result == []

    def test_fetch_jobs_is_alias(self):
        def fake_transport(method, url, headers, timeout):
            return 200, b'{"items": []}'

        source = ArgoWorkflowsSource(
            {"base_url": "https://argo.example.com"}, transport=fake_transport
        )
        assert source.fetch_jobs() == source.query()

    def test_namespace_default(self):
        source = ArgoWorkflowsSource({"base_url": "https://argo.example.com"})
        assert source.namespace == "argo"

    def test_custom_namespace(self):
        source = ArgoWorkflowsSource(
            {"base_url": "https://argo.example.com", "namespace": "custom-ns"}
        )
        assert source.namespace == "custom-ns"
