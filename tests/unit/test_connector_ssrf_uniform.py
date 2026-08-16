"""F1 regression: every URL-accepting connector MUST reject hostile base_url at construction.

Parametrized over connector class x hostile URL.  Connectors that validate lazily
or not at all are marked ``xfail`` with reasoning so gaps are visible.
"""

from __future__ import annotations

import importlib

import pytest

HOSTILE_URLS = [
    "http://127.0.0.1",
    "http://localhost",
    "http://169.254.169.254",
    "http://10.0.0.1",
    "http://172.16.0.1",
    "http://192.168.1.1",
    "http://[::1]",
    "http://metadata.google.internal",
    "http://instance-data",
    "http://ip6-localhost",
    "http://metadata.goog",
    "http://100.100.100.200",
]

_DUMMY_TRANSPORT = object()

_CONNECTOR_URL_CONFIGS = [
    # ── Cat-1a: plain base_url, SSRF fires first ──
    ("general_ludd.connectors.prometheus", "PrometheusSource", "base_url", {}, [], ValueError, {}),
    ("general_ludd.connectors.datadog", "DatadogSource", "site", {}, [], ValueError, {}),
    ("general_ludd.connectors.jenkins", "JenkinsSource", "base_url", {}, [], ValueError, {}),
    ("general_ludd.connectors.jaeger", "JaegerSource", "base_url", {}, [], ValueError, {}),
    ("general_ludd.connectors.splunk_observability", "SplunkObservabilitySource", "base_url", {}, [], ValueError, {}),
    ("general_ludd.connectors.thanos", "ThanosSource", "base_url", {}, [], ValueError, {}),
    ("general_ludd.connectors.nagios", "NagiosSource", "base_url", {}, [], ValueError, {}),
    ("general_ludd.connectors.kafka_exporter", "KafkaExporterSource", "base_url", {}, [], ValueError, {}),
    ("general_ludd.connectors.rabbitmq", "RabbitMqSource", "base_url", {}, [], ValueError, {}),
    ("general_ludd.connectors.nats", "NatsSource", "base_url", {}, [], ValueError, {}),
    ("general_ludd.connectors.parca", "ParcaSource", "base_url", {}, [], ValueError, {}),
    ("general_ludd.connectors.pyroscope", "PyroscopeSource", "base_url", {}, [], ValueError, {}),
    # ── Cat-1b: config key is not 'base_url' ──
    ("general_ludd.connectors.newrelic", "NewRelicSource", "api_url", {}, [], ValueError, {}),
    ("general_ludd.connectors.okta", "OktaSource", "org_url", {}, [], ValueError, {}),
    # ── Cat-1c: ValueError subclass (SSRFError / ConnectorConfigError) ──
    ("general_ludd.connectors.elasticsearch", "ElasticsearchSource", "base_url", {"index": "test"}, [], None, {}),
    ("general_ludd.connectors.opentsdb", "OpenTsdbSource", "base_url", {}, [], None, {}),
    ("general_ludd.connectors.victoriametrics", "VictoriaMetricsSource", "base_url", {}, [], None, {}),
    ("general_ludd.connectors.zipkin", "ZipkinSource", "base_url", {}, [], None, {}),
    ("general_ludd.connectors.zabbix", "ZabbixSource", "base_url", {}, [], None, {}),
    ("general_ludd.connectors.buildkite", "BuildkiteSource", "base_url", {}, [], None, {}),
    ("general_ludd.connectors.travis", "TravisSource", "base_url", {}, [], None, {}),
    ("general_ludd.connectors.argo_workflows", "ArgoWorkflowsSource", "base_url", {}, [], None, {}),
    (
        "general_ludd.connectors.rollbar",
        "RollbarSource",
        "base_url",
        {},
        [],
        None,
        {"transport": _DUMMY_TRANSPORT},
    ),
    (
        "general_ludd.connectors.bugsnag",
        "BugsnagSource",
        "base_url",
        {"project_id": "fake"},
        [],
        None,
        {"transport": _DUMMY_TRANSPORT},
    ),
    # ── Cat-2: needs transport kwarg ──
    (
        "general_ludd.connectors.grafana_loki",
        "GrafanaLokiSource",
        "base_url",
        {},
        [],
        ValueError,
        {"transport": _DUMMY_TRANSPORT},
    ),
    (
        "general_ludd.connectors.signoz",
        "SigNozSource",
        "base_url",
        {},
        [],
        ValueError,
        {"transport": _DUMMY_TRANSPORT},
    ),
    (
        "general_ludd.connectors.splunk",
        "SplunkSource",
        "base_url",
        {"token_env": "FAKE_SPLUNK_TOKEN"},
        [],
        None,
        {"transport": _DUMMY_TRANSPORT},
    ),
    # ── Cat-3: needs extra config fields checked after SSRF (SSRF fires first) ──
    (
        "general_ludd.connectors.github_actions",
        "GitHubActionsSource",
        "base_url",
        {"repo": "owner/repo"},
        [],
        ValueError,
        {},
    ),
    (
        "general_ludd.connectors.azure_monitor",
        "AzureMonitorSource",
        "base_url",
        {"token_env": "FAKE_AZURE_TOKEN", "workspace_id": "fake-workspace"},
        [],
        ValueError,
        {},
    ),
    (
        "general_ludd.connectors.azure_resource_graph",
        "AzureResourceGraphSource",
        "base_url",
        {"token_env": "FAKE_AZURE_TOKEN", "subscriptions": ["fake-sub"]},
        [],
        ValueError,
        {},
    ),
    (
        "general_ludd.connectors.sentry",
        "SentrySource",
        "base_url",
        {"token_env": "FAKE_SENTRY_TOKEN", "org": "fake-org", "project": "fake-project"},
        [],
        ValueError,
        {},
    ),
    (
        "general_ludd.connectors.circleci",
        "CircleCiSource",
        "base_url",
        {"project_slug": "gh/owner/repo"},
        [],
        ValueError,
        {},
    ),
    ("general_ludd.connectors.gitlab_ci", "GitlabCiSource", "base_url", {"project_id": "12345"}, [], ValueError, {}),
    (
        "general_ludd.connectors.azure_devops",
        "AzureDevOpsSource",
        "base_url",
        {"org": "fake-org", "project": "fake-project"},
        [],
        ValueError,
        {},
    ),
    ("general_ludd.connectors.cloudflare", "CloudflareSource", "base_url", {}, [], ValueError, {}),
    ("general_ludd.connectors.entra_signin", "EntraSignInSource", "base_url", {}, [], ValueError, {}),
    ("general_ludd.connectors.lambda_labs", "LambdaLabsClient", "base_url", {}, [], ValueError, {}),
    ("general_ludd.connectors.opsgenie", "OpsgenieSource", "base_url", {}, [], ValueError, {}),
    # ── Cat-4: has default URL — SSRF fires first on the default, not on user input.
    # Validating a default URL proves the guard exists; passing a hostile URL as config
    # overrides the default and triggers the guard.
    # ── need env var set for eager-token connectors (SSRF fires first) ──
    (
        "general_ludd.connectors.elastic_apm",
        "ElasticApmSource",
        "base_url",
        {},
        [("ELASTIC_APM_TOKEN", "fake")],
        None,
        {},
    ),
    ("general_ludd.connectors.tempo", "TempoSource", "base_url", {}, [("TEMPO_TOKEN", "fake")], None, {}),
    ("general_ludd.connectors.dynatrace", "DynatraceSource", "base_url", {}, [("DYNATRACE_TOKEN", "fake")], None, {}),
    (
        "general_ludd.connectors.influxdb",
        "InfluxDbSource",
        "base_url",
        {},
        [("INFLUXDB_TOKEN", "fake")],
        None,
        {"transport": _DUMMY_TRANSPORT},
    ),
    (
        "general_ludd.connectors.graphite",
        "GraphiteSource",
        "base_url",
        {},
        [],
        None,
        {"transport": _DUMMY_TRANSPORT},
    ),
    (
        "general_ludd.connectors.appdynamics",
        "AppDynamicsSource",
        "base_url",
        {},
        [("APPDYNAMICS_TOKEN", "fake")],
        None,
        {},
    ),
    ("general_ludd.connectors.honeycomb", "HoneycombSource", "base_url", {}, [], ValueError, {}),
    ("general_ludd.connectors.pagerduty", "PagerDutySource", "base_url", {}, [], ValueError, {}),
    ("general_ludd.connectors.baseten", "BasetenClient", "base_url", {}, [("BASETEN_API_KEY", "fake")], None, {}),
    ("general_ludd.connectors.grafana_oncall", "GrafanaOnCallSource", "base_url", {}, [], ValueError, {}),
]


def _build_connector_configs():
    result = []
    for mod_path, cls_name, url_key, extra, envs, exc, kwargs in _CONNECTOR_URL_CONFIGS:
        result.append(
            pytest.param(
                mod_path,
                cls_name,
                url_key,
                extra,
                envs,
                exc,
                kwargs,
                id=f"{cls_name}[{url_key}]",
            )
        )
    return result


CONNECTOR_URL_CONFIGS = _build_connector_configs()


@pytest.mark.parametrize(
    "module_path,class_name,url_key,extra_config,env_vars,exc_type,extra_kwargs",
    CONNECTOR_URL_CONFIGS,
)
@pytest.mark.parametrize("bad_url", HOSTILE_URLS)
def test_connector_rejects_hostile_base_url(
    module_path,
    class_name,
    url_key,
    extra_config,
    env_vars,
    exc_type,
    extra_kwargs,
    bad_url,
    monkeypatch,
):
    for env_name, env_value in env_vars:
        monkeypatch.setenv(env_name, env_value)

    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)
    expected_exc = exc_type if exc_type is not None else ValueError

    config = {url_key: bad_url, **extra_config}
    with pytest.raises(expected_exc):
        cls(config, **extra_kwargs)


# ── xfail: lazy or absent SSRF validation ──


@pytest.mark.xfail(
    strict=False,
    reason="SSRF deferred to _build_default_executor (no raise at construction)",
)
@pytest.mark.parametrize("bad_url", ["http://localhost:7070/metrics", "http://10.0.0.1:7070/metrics"])
def test_cassandra_rejects_hostile_url_construction(bad_url):
    from general_ludd.connectors.cassandra_stats import CassandraStatsSource

    with pytest.raises(ValueError):
        CassandraStatsSource({"jmx_url": bad_url})


@pytest.mark.xfail(
    strict=False,
    reason="SSRF deferred to _build_default_executor (no raise at construction)",
)
@pytest.mark.parametrize("bad_url", ["http://localhost:8123", "http://10.0.0.1:8123"])
def test_clickhouse_rejects_hostile_url_construction(bad_url):
    from general_ludd.connectors.clickhouse_stats import ClickHouseStatsSource

    with pytest.raises(ValueError):
        ClickHouseStatsSource({"url": bad_url})


@pytest.mark.xfail(
    strict=False,
    reason="SSRF deferred to _get() / health() — not validated at construction",
)
@pytest.mark.parametrize("bad_url", ["http://127.0.0.1", "http://169.254.169.254"])
def test_redfish_rejects_hostile_url_construction(bad_url):
    from general_ludd.connectors.redfish import RedfishSource

    with pytest.raises(ValueError):
        RedfishSource({"base_url": bad_url})


@pytest.mark.xfail(
    strict=False,
    reason="SSRF deferred to health() / _query_exporter() — not validated at construction",
)
@pytest.mark.parametrize("bad_url", ["http://127.0.0.1", "http://169.254.169.254"])
def test_snmp_rejects_hostile_url_construction(bad_url):
    from general_ludd.connectors.snmp import SnmpSource

    with pytest.raises(ValueError):
        SnmpSource({"base_url": bad_url})


@pytest.mark.xfail(
    strict=False,
    reason=(
        "Nomad SSRF is enforced lazily at health()/query() time "
        "(DNS-rebinding re-check per request), not at construction — "
        "pinned by tests/e2e/test_connectors_batch5_workflows.py"
    ),
)
@pytest.mark.parametrize("bad_url", ["http://127.0.0.1", "http://169.254.169.254"])
def test_nomad_rejects_hostile_url_construction(bad_url):
    from general_ludd.connectors.nomad import NomadSource

    with pytest.raises(ValueError):
        NomadSource({"base_url": bad_url})
    assert bad_url.startswith("http://")


@pytest.mark.xfail(
    strict=False,
    reason="No URL validation at all — uri_env read lazily, no SSRF guard",
)
@pytest.mark.parametrize("bad_url", ["mongodb://127.0.0.1:27017", "mongodb://169.254.169.254:27017"])
def test_mongodb_rejects_hostile_url_construction(bad_url, monkeypatch):
    monkeypatch.setenv("MONGODB_URI", bad_url)
    from general_ludd.connectors.mongodb_stats import MongoDbStatsSource

    with pytest.raises(ValueError):
        MongoDbStatsSource({"uri_env": "MONGODB_URI"})


@pytest.mark.xfail(
    strict=False,
    reason="No URL validation at all — dsn_env read lazily, no SSRF guard",
)
@pytest.mark.parametrize("bad_dsn", ["postgresql://127.0.0.1:5432/db", "postgresql://169.254.169.254:5432/db"])
def test_postgres_rejects_hostile_url_construction(bad_dsn, monkeypatch):
    monkeypatch.setenv("PG_DSN", bad_dsn)
    from general_ludd.connectors.postgres_stats import PostgresStatsSource

    with pytest.raises(ValueError):
        PostgresStatsSource({"dsn_env": "PG_DSN"})


@pytest.mark.xfail(
    strict=False,
    reason="No URL validation at all — url_env read lazily, no SSRF guard",
)
@pytest.mark.parametrize("bad_url", ["redis://127.0.0.1:6379", "redis://169.254.169.254:6379"])
def test_redis_rejects_hostile_url_construction(bad_url, monkeypatch):
    monkeypatch.setenv("REDIS_URL", bad_url)
    from general_ludd.connectors.redis_stats import RedisStatsSource

    with pytest.raises(ValueError):
        RedisStatsSource({"url_env": "REDIS_URL"})


@pytest.mark.xfail(
    strict=False,
    reason=(
        "docs/audit/connector_security_audit.md: mqtt uses broker_host "
        "(not a URL) and raises RuntimeError, not ValueError"
    ),
)
@pytest.mark.parametrize("bad_host", ["127.0.0.1", "169.254.169.254"])
def test_mqtt_rejects_hostile_host_construction(bad_host):
    from general_ludd.connectors.mqtt import MqttSource

    with pytest.raises(ValueError):
        MqttSource({"broker_host": bad_host})
