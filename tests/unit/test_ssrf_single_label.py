from __future__ import annotations

import pytest

from general_ludd.security.ssrf import _is_single_label_hostname, host_is_blocked, is_url_blocked


def test_single_label_hostname_is_blocked():
    assert host_is_blocked("vault") is True
    assert host_is_blocked("grafana") is True
    assert host_is_blocked("internalhost") is True


def test_single_label_in_url_is_blocked():
    assert is_url_blocked("http://vault:8200/") is True
    assert is_url_blocked("http://grafana/") is True


def test_fqdn_still_allowed():
    assert host_is_blocked("api.datadoghq.com") is False
    assert host_is_blocked("cloudasset.googleapis.com") is False


def test_ip_literal_not_single_label():
    assert _is_single_label_hostname("127.0.0.1") is False


def test_metadata_azure_com_in_blocklist():
    assert host_is_blocked("metadata.azure.com") is True


def _stub_transport(*args, **kwargs):
    return 200, {}


_CONNECTOR_PARAMS = [
    pytest.param(
        "general_ludd.connectors.prometheus",
        "PrometheusSource",
        {"base_url": "http://vault"},
        False,
        id="prometheus",
    ),
    pytest.param(
        "general_ludd.connectors.datadog",
        "DatadogSource",
        {"site": "http://vault"},
        False,
        id="datadog",
    ),
    pytest.param(
        "general_ludd.connectors.elasticsearch",
        "ElasticsearchSource",
        {"base_url": "http://vault", "index": "test"},
        False,
        id="elasticsearch",
    ),
    pytest.param(
        "general_ludd.connectors.grafana_loki",
        "GrafanaLokiSource",
        {"base_url": "http://vault"},
        True,
        id="grafana_loki",
    ),
    pytest.param(
        "general_ludd.connectors.signoz",
        "SigNozSource",
        {"base_url": "http://vault"},
        True,
        id="signoz",
    ),
    pytest.param(
        "general_ludd.connectors.splunk",
        "SplunkSource",
        {"base_url": "http://vault", "token_env": "SPLUNK_TOKEN"},
        True,
        id="splunk",
    ),
    pytest.param(
        "general_ludd.connectors.splunk_observability",
        "SplunkObservabilitySource",
        {"base_url": "http://vault"},
        False,
        id="splunk_observability",
    ),
    pytest.param(
        "general_ludd.connectors.github_actions",
        "GitHubActionsSource",
        {"base_url": "http://vault", "repo": "owner/name"},
        False,
        id="github_actions",
    ),
]


@pytest.mark.parametrize(
    "module_path, class_name, config, needs_transport",
    _CONNECTOR_PARAMS,
)
def test_all_connectors_reject_single_label(
    module_path, class_name, config, needs_transport
):
    import importlib

    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)
    if needs_transport:
        with pytest.raises(ValueError):
            cls(config, transport=_stub_transport)
    else:
        with pytest.raises(ValueError):
            cls(config)
