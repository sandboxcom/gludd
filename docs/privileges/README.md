# Connector least-privilege access guide

Authoritative inventory of **every connector that actually exists** in
`src/general_ludd/connectors/` (verified by reading each module's source in the
live working tree). Each connector pulls observability data from one external
facility and normalizes it into the shared eight-key record shape
(`ts, source, kind, level_or_status, message, value, labels, raw`) defined in
`connectors/base.py`.

This index is the single source of truth for *what credential each connector
needs and how it is named*. Every value below — env-var names, endpoints, ports,
auth schemes — was extracted from the connector code, not invented.

## Cross-cutting credential rules (enforced in code)

- **Secrets are never inlined.** Every connector reads its secret at call time
  from the environment variable whose *name* is given in a `*_env` config key
  (`token_env`, `password_env`, `community_env`, `dsn_env`, `url_env`, …). Config
  carries the *name* of the env var, never the secret value.
- **SSRF guard.** HTTP connectors validate `base_url`/`org_url` against a
  literal-host block (loopback / private / link-local / reserved / cloud-metadata).
  Most reject private hosts unless `allow_private=True`; the BMC-oriented
  `redfish` and the SSH-LAN `snmp`/`docker_api` connectors invert this to opt-in.
- **No shell.** Subprocess-backed connectors (journald, dmesg, osquery,
  windows_event, macos_log, docker_api socket path, containerd, snmp) build an
  argv *list* and validate operator filters before invocation; `shell=True` is
  never used.
- **`health()` never raises** and never leaks the secret (snmp redacts the
  community string everywhere it could surface).

## Inventory (34 connectors present)

| module | class | KIND | facility | least-privilege credential | `*_env` var name(s) | base URL / endpoint / port / socket | auth scheme |
|---|---|---|---|---|---|---|---|
| `jaeger.py` | `JaegerSource` | traces | Jaeger | read-only trace query token (optional) | `token_env` | `{base_url}/api/traces`, `/api/services` (HTTP) | `Authorization: Bearer <token>` (optional) |
| `zipkin.py` | `ZipkinSource` | traces | Zipkin | read-only trace query token (optional) | `token_env` | `{base_url}/api/v2/traces` (HTTP) | `Authorization: Bearer <token>` (optional) |
| `tempo.py` | `TempoSource` | traces | Grafana Tempo | read-only search token (optional) | `token_env` | `{base_url}/api/search`, `/api/traces/{id}` (HTTP) | `Authorization: Bearer <token>` (optional) |
| `parca.py` | `ParcaSource` | traces | Parca (profiling) | read-only query token (optional) | `token_env` | `POST {base_url}/parca.query.v1alpha1.QueryService/QueryRange` (Connect/HTTP) | `Authorization: Bearer <token>` (optional) |
| `pyroscope.py` | `PyroscopeSource` | traces | Pyroscope (profiling) | read-only render token (optional) | `token_env` | `GET {base_url}/render` (HTTP) | `Authorization: Bearer <token>` (optional) |
| `kafka_exporter.py` | `KafkaExporterSource` | metrics | kafka_exporter | scrape token (optional) | `token_env` | `GET {base_url}/metrics` (Prometheus text, HTTP) | `Authorization: Bearer <token>` (optional) |
| `rabbitmq.py` | `RabbitMqSource` | metrics | RabbitMQ management API | monitoring user (read-only) | `user_env`, `password_env` | `{base_url}/api/overview`, `/api/queues`, `/api/nodes`; default port **15672** | `Authorization: Basic <user:pass>` |
| `nats.py` | `NatsSource` | metrics | NATS monitoring | bearer token (optional; usually unauth) | `token_env` | `{base_url}/varz`, `/connz`, `/subsz`; default port **8222** | `Authorization: Bearer <token>` (optional) |
| `prometheus.py` | `PrometheusSource` | metrics | Prometheus | read-only query token (optional) | `token_env` | `{base_url}/api/v1/query`, `/api/v1/query_range` (HTTP) | `Authorization: Bearer <token>` (optional) |
| `victoriametrics.py` | `VictoriaMetricsSource` | metrics | VictoriaMetrics | read-only basic-auth user (optional) | `username_env`, `password_env` | `{base_url}/api/v1/query`, `/api/v1/query_range` (HTTP) | `Authorization: Basic <user:pass>` (optional) |
| `thanos.py` | `ThanosSource` | metrics | Thanos Querier | read-only query token (optional) | `token_env` | `{base_url}/api/v1/query[_range]` (+`dedup`,`partial_response`) | `Authorization: Bearer <token>` (optional) |
| `opentsdb.py` | `OpenTsdbSource` | metrics | OpenTSDB | read-only basic-auth user (optional) | `username_env`, `password_env` | `POST {base_url}/api/query`, `GET /api/version` (HTTP) | `Authorization: Basic <user:pass>` (optional) |
| `journald.py` | `JournaldSource` | logs | systemd journal (local) | OS read access to the journal | — (none) | local exec `journalctl -o json --no-pager` | OS / filesystem (journal group) |
| `docker_api.py` | `DockerApiSource` | logs / events | Docker Engine API | read access to the Engine socket | — (none) | UNIX socket `/var/run/docker.sock` (default) **or** TCP host; `/containers/json`, `/containers/{id}/logs`, `/events` | UNIX socket perms (or unauth TCP) |
| `windows_event.py` | `WindowsEventSource` | logs | Windows Event Log (local) | Event Log Readers membership | — (none) | local exec `wevtutil qe …` or `Get-WinEvent` | OS / Windows ACL |
| `macos_log.py` | `MacosLogSource` | logs | macOS unified log (local) | OS access to `log show` | — (none) | local exec `log show --style json --last <dur>` | OS / filesystem |
| `proc_sys.py` | `ProcSysSource` | metrics | Linux `/proc` + `/sys` | OS read of kernel files | — (none) | confined reads under `/proc`, `/sys` | OS / filesystem |
| `dmesg.py` | `DmesgSource` | logs | kernel ring buffer (local) | read of `/dev/kmsg` (often `CAP_SYSLOG`) | — (none) | local exec `dmesg --json` | OS / capability |
| `osquery.py` | `OsquerySource` | metrics | osquery (local) | OS access to `osqueryi` | — (none) | local exec `osqueryi --json "<SQL>"` | OS / filesystem |
| `snmp.py` | `SnmpSource` | metrics | SNMP device / snmp_exporter | SNMPv2c read-only community **or** exporter scrape | `community_env` | UDP **161** (snmp mode) **or** `GET {base_url}/metrics` (exporter mode) | SNMP community (redacted) / none |
| `redfish.py` | `RedfishSource` | metrics / events | BMC (DMTF Redfish) | read-only BMC operator account | `username_env` (def `REDFISH_USERNAME`), `password_env` (def `REDFISH_PASSWORD`) | `{base_url}/redfish/v1/...` over HTTPS | `Authorization: Basic <user:pass>` |
| `containerd.py` | `ContainerdSource` | logs / metrics | containerd (CRI) | read access to the CRI socket | `auth_token_env` (optional) | crictl over `unix:///run/containerd/containerd.sock`; pod-log fallback `/var/log/pods` | UNIX socket perms (+ optional `--auth-token`) |
| `postgres_stats.py` | `PostgresStatsSource` | metrics | PostgreSQL `pg_stat_*` | `pg_monitor` role member | `dsn_env` | libpq DSN (default port 5432) | DSN-embedded (password/scram in DSN) |
| `mysql_stats.py` | `MysqlStatsSource` | metrics | MySQL status/perf-schema/replica | `PROCESS`, `REPLICATION CLIENT`, `SELECT` on perf_schema | `host_env`, `user_env`, `password_env`, `database_env` | TCP (default port 3306) | MySQL user/password |
| `redis_stats.py` | `RedisStatsSource` | metrics | Redis INFO / SLOWLOG | read-only ACL (`+info +slowlog +ping`) | `url_env` | Redis URL (default port 6379) | ACL user in URL (`redis://user:pass@…`) |
| `mongodb_stats.py` | `MongoDbStatsSource` | metrics | MongoDB admin commands | `clusterMonitor` role | `uri_env` (def `MONGODB_URI`) | MongoDB URI (default port 27017) | SCRAM user in URI |
| `clickhouse_stats.py` | `ClickHouseStatsSource` | metrics | ClickHouse `system.*` | read-only user with `SELECT` on `system.*` | `password_env` (def `CLICKHOUSE_PASSWORD`) | `GET {url}/` (default `http://localhost:8123`), `user` config (def `default`) | `Authorization: Basic <user:pass>` |
| `cassandra_stats.py` | `CassandraStatsSource` | metrics | Cassandra JMX exporter | scrape token (optional) | `token_env` (def `CASSANDRA_JMX_TOKEN`) | `GET {jmx_url}` (default `http://localhost:7070/metrics`) | `Authorization: Bearer <token>` (optional) |
| `pagerduty.py` | `PagerDutySource` | incidents | PagerDuty REST API | read-only API token | `token_env` (def `PAGERDUTY_TOKEN`) | `GET https://api.pagerduty.com/incidents` | `Authorization: Token token=<token>` |
| `opsgenie.py` | `OpsgenieSource` | incidents | Opsgenie REST API | read-only API key | `token_env` (def `OPSGENIE_API_KEY`) | `GET https://api.opsgenie.com/v2/alerts` | `Authorization: GenieKey <key>` |
| `grafana_oncall.py` | `GrafanaOnCallSource` | incidents | Grafana OnCall API | read-only API token | `token_env` (def `GRAFANA_ONCALL_TOKEN`) | `GET {base_url}/api/v1/alert_groups` (base_url required) | `Authorization: <raw token>` |
| `okta.py` | `OktaSource` | events | Okta System Log | read-only API token (System Log read) | `token_env` (required, no default) | `GET {org_url}/api/v1/logs` | `Authorization: SSWS <token>` |
| `cloudflare.py` | `CloudflareSource` | events | Cloudflare audit logs | API token with Audit Logs Read | `token_env` (required) | `GET https://api.cloudflare.com/client/v4/accounts/{id}/audit_logs` | `Authorization: Bearer <token>` |
| `entra_signin.py` | `EntraSignInSource` | events | Microsoft Entra ID sign-ins | Graph token with `AuditLog.Read.All` | `token_env` (required) | `GET https://graph.microsoft.com/v1.0/auditLogs/signIns` | `Authorization: Bearer <token>` (externally minted) |

## Per-facility least-privilege guides

- [tracing_profiling.md](tracing_profiling.md) — jaeger, zipkin, tempo, parca, pyroscope
- [messaging.md](messaging.md) — kafka_exporter, rabbitmq, nats
- [metrics_stores.md](metrics_stores.md) — prometheus, victoriametrics, thanos, opentsdb
- [host_os.md](host_os.md) — journald, docker_api, windows_event, macos_log, proc_sys, dmesg, osquery, snmp, redfish, containerd
- [databases.md](databases.md) — postgres_stats, mysql_stats, redis_stats, mongodb_stats, clickhouse_stats, cassandra_stats
- [incidents_idp.md](incidents_idp.md) — pagerduty, opsgenie, grafana_oncall, okta, cloudflare, entra_signin

## Connectors not present (omitted)

Every connector named in the task brief exists in the tree. No connector was
fabricated or omitted. The `base.py` module is the connector *contract* (Source
Protocol, registry, `is_safe_endpoint` SSRF guard) and is not itself a backend
connector, so it has no row above.

## Grounding note

Values were extracted by reading each module's `__init__` (the `config.get(...)`
and `*_env` keys), the `_headers()`/auth helpers (auth scheme), and the
`query()`/`health()` paths (endpoints, ports, sockets). Default env-var names
(e.g. `PAGERDUTY_TOKEN`, `REDFISH_USERNAME`, `MONGODB_URI`,
`CLICKHOUSE_PASSWORD`, `CASSANDRA_JMX_TOKEN`) are the literal defaults coded in
those modules; where no default exists (okta/cloudflare/entra_signin) the
`token_env` config key is required at construction.
