# Least-privilege: messaging

Covers `kafka_exporter`, `rabbitmq`, and `nats`. All three have
`KIND = "metrics"` and read monitoring/management endpoints only — never
produce, consume, publish, or administer. All three SSRF-guard `base_url`
(reject loopback/private/metadata literal hosts) and allow plain `http`
(internal exporters/monitoring ports are commonly HTTP).

## Env-var table

| connector | `*_env` key(s) | auth header sent | endpoints / default port |
|---|---|---|---|
| kafka_exporter | `token_env` (optional) | `Authorization: Bearer <token>` | `GET {base_url}/metrics` (Prometheus text) |
| rabbitmq | `user_env`, `password_env` | `Authorization: Basic <b64(user:pass)>` | `{base_url}/api/overview`, `/api/queues`, `/api/nodes`; default port **15672** |
| nats | `token_env` (optional) | `Authorization: Bearer <token>` | `{base_url}/varz`, `/connz`, `/subsz`; default port **8222** |

## kafka_exporter

Scrapes `danielqsj/kafka_exporter`'s `/metrics` (consumer-group lag,
under-replicated partitions, broker throughput). The exporter itself talks to
Kafka with its own credentials; this connector only scrapes the exporter's HTTP
endpoint. No Kafka credential is handled here.

- **Least privilege:** none required for an open exporter. If the exporter sits
  behind an auth proxy, mint a read-only scrape token.
- The `token_env` value, when set, is sent as `Authorization: Bearer <token>`.

```bash
export KAFKA_EXPORTER_TOKEN='<optional-scrape-token>'
```
```yaml
- module: kafka_exporter
  config: { base_url: "http://kafka-exporter:9308", token_env: "KAFKA_EXPORTER_TOKEN" }
```

Verify (read-only scrape):
```bash
curl -fsS "http://kafka-exporter:9308/metrics" | grep -m1 kafka_
```

## rabbitmq

Reads the RabbitMQ **management** HTTP API. The connector appends `:15672` when
`base_url` has no explicit port. It uses HTTP Basic auth built from
`user_env`/`password_env`.

- **Least privilege:** a RabbitMQ user with the **`monitoring`** tag (and *no*
  administrator/management write tags). The `monitoring` tag grants read of
  `/api/overview`, `/api/nodes`, and all-vhost `/api/queues` without management
  write or policy/user admin.

Copy-pasteable RabbitMQ user (run by an admin via `rabbitmqctl`):
```bash
rabbitmqctl add_user gludd_mon 'CHANGE_ME_STRONG'
rabbitmqctl set_user_tags gludd_mon monitoring
# read-only access to the default vhost's objects:
rabbitmqctl set_permissions -p / gludd_mon "^$" "^$" ".*"
```
(The empty `configure`/`write` regexes `^$` grant no config/write; the read
regex `.*` permits reading queue/exchange state the mgmt API surfaces.)

```bash
export RABBITMQ_USER='gludd_mon'
export RABBITMQ_PASSWORD='CHANGE_ME_STRONG'
```
```yaml
- module: rabbitmq
  config:
    base_url: "http://rabbitmq.example.com"   # :15672 auto-appended
    user_env: "RABBITMQ_USER"
    password_env: "RABBITMQ_PASSWORD"
```

Verify (read-only):
```bash
curl -fsS -u "$RABBITMQ_USER:$RABBITMQ_PASSWORD" \
  "http://rabbitmq.example.com:15672/api/overview" | head -c 200
```
`health()` probes `/api/overview` and reports the `rabbitmq_version`.

## nats

Reads the NATS **monitoring** server (`/varz`, `/connz`, `/subsz`). The
connector appends `:8222` when `base_url` has no port. Auth is an **optional**
bearer token (`token_env`) — NATS monitoring is usually unauthenticated, but may
sit behind a proxy.

- **Least privilege:** none on the monitoring port itself; the monitoring
  endpoints are read-only stats. If proxied, mint a read-only token.

```bash
export NATS_MONITOR_TOKEN='<optional-token>'
```
```yaml
- module: nats
  config:
    base_url: "http://nats.example.com"   # :8222 auto-appended
    token_env: "NATS_MONITOR_TOKEN"
```

Verify (read-only):
```bash
curl -fsS "http://nats.example.com:8222/varz" | head -c 200
```
`health()` probes `/varz` and reports the NATS server version.

## Read-only guarantee

None of the three issue any write/admin call. `query()` accepts an optional
`endpoints`/`metrics` allowlist (rabbitmq, nats, kafka_exporter respectively) to
narrow the scrape further, and every failure becomes a single normalized error
record rather than an exception.
