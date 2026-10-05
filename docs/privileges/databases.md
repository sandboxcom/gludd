# Least-privilege: databases

Covers `postgres_stats`, `mysql_stats`, `redis_stats`, `mongodb_stats`,
`clickhouse_stats`, `cassandra_stats`. All have `KIND = "metrics"` and run
**static, read-only, parameter-free** introspection queries (kept as auditable
module constants). Each resolves its credential from a `*_env` config key and
guards the driver import so the module stays importable when the driver is
absent (`health()` reports `"driver unavailable"` instead of raising).

## Env-var table

| connector | `*_env` key(s) | driver | what it reads |
|---|---|---|---|
| postgres_stats | `dsn_env` | psycopg | `pg_stat_activity`, `pg_stat_replication`, `pg_stat_database`, `pg_stat_statements` |
| mysql_stats | `host_env`, `user_env`, `password_env`, `database_env` | pymysql | `SHOW GLOBAL STATUS`, `performance_schema.events_statements_summary_global_by_event_name`, `SHOW REPLICA STATUS` |
| redis_stats | `url_env` | redis-py | `INFO`, `SLOWLOG GET`, `PING` |
| mongodb_stats | `uri_env` (def `MONGODB_URI`) | pymongo | `serverStatus`, `currentOp`, `replSetGetStatus` |
| clickhouse_stats | `password_env` (def `CLICKHOUSE_PASSWORD`) + `url`, `user` config | httpx | `system.metrics`, `system.events`, `system.asynchronous_metrics`, `system.replicas` |
| cassandra_stats | `token_env` (def `CASSANDRA_JMX_TOKEN`) | httpx | JMX-exporter Prometheus scrape (compaction/table/threadpool) |

## postgres_stats — `pg_monitor`

The four `pg_stat_*` views (including `pg_stat_statements` and replication lag)
are exactly what PostgreSQL's built-in **`pg_monitor`** role exposes. Grant that
and nothing else.

```sql
-- Run as a superuser/role admin:
CREATE ROLE gludd_mon LOGIN PASSWORD 'CHANGE_ME_STRONG';
GRANT pg_monitor TO gludd_mon;          -- pg_read_all_stats + pg_read_all_settings + pg_stat_scan_tables
-- pg_stat_statements must be installed in the target DB:
-- CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
```
The connector reads a libpq **DSN** from the env var named by `dsn_env`:
```bash
export PG_DSN='postgresql://gludd_mon:CHANGE_ME_STRONG@db.example.com:5432/postgres'
```
```yaml
- module: postgres_stats
  config: { dsn_env: "PG_DSN" }
```
Verify (read-only): `health()` runs `SELECT 1`. Then:
```sql
SET ROLE gludd_mon;  SELECT * FROM pg_stat_activity LIMIT 1;
```

## mysql_stats — `PROCESS`, `REPLICATION CLIENT`, `SELECT` on perf_schema

`SHOW GLOBAL STATUS` needs no special grant; `performance_schema` needs `SELECT`;
`SHOW REPLICA STATUS` needs `REPLICATION CLIENT` (a.k.a. `REPLICATION_CLIENT` /
`BINLOG MONITOR`). `PROCESS` is the standard monitoring privilege.

```sql
CREATE USER 'gludd_mon'@'%' IDENTIFIED BY 'CHANGE_ME_STRONG';
GRANT PROCESS, REPLICATION CLIENT ON *.* TO 'gludd_mon'@'%';
GRANT SELECT ON performance_schema.* TO 'gludd_mon'@'%';
FLUSH PRIVILEGES;
```
Credentials are resolved from the named env vars (host/user/password/database):
```bash
export MYSQL_HOST='db.example.com'
export MYSQL_USER='gludd_mon'
export MYSQL_PASSWORD='CHANGE_ME_STRONG'
```
```yaml
- module: mysql_stats
  config:
    host_env: "MYSQL_HOST"
    user_env: "MYSQL_USER"
    password_env: "MYSQL_PASSWORD"
    # database_env optional
```
Verify (read-only): `health()` runs `SELECT 1`; then `SHOW GLOBAL STATUS;`.

## redis_stats — read-only ACL (`+info +slowlog +ping`)

The connector runs only `INFO`, `SLOWLOG GET`, and `PING`. Create a Redis 6+ ACL
user limited to exactly those commands, no keyspace access.

```markdown
# redis-cli (or in redis.conf as a `user` line):
ACL SETUSER gludd_mon on >CHANGE_ME_STRONG ~* &* -@all +info +slowlog|get +ping
# (~* needs no key reads here; restrict further with allkeys off if desired)
```
The connection URL is read from the env var named by `url_env`:
```bash
export REDIS_URL='redis://gludd_mon:CHANGE_ME_STRONG@redis.example.com:6379/0'
```
```yaml
- module: redis_stats
  config: { url_env: "REDIS_URL" }
```
Verify (read-only): `health()` runs `PING`; then
`redis-cli -u "$REDIS_URL" INFO server | head`.

## mongodb_stats — `clusterMonitor`

`serverStatus`, `currentOp`, and `replSetGetStatus` are exactly the actions
covered by the built-in **`clusterMonitor`** role. Grant it on `admin` and
nothing else.

```javascript
// in the mongo shell, as an admin:
db.getSiblingDB("admin").createUser({
  user: "gludd_mon",
  pwd: "CHANGE_ME_STRONG",
  roles: [ { role: "clusterMonitor", db: "admin" } ]
});
```
The connection URI is read from `uri_env` (default `MONGODB_URI`):
```bash
export MONGODB_URI='mongodb://gludd_mon:CHANGE_ME_STRONG@mongo.example.com:27017/admin?authSource=admin'
```
```yaml
- module: mongodb_stats
  config: { uri_env: "MONGODB_URI" }
```
Verify (read-only): `health()` runs `db.adminCommand("serverStatus")`.

## clickhouse_stats — read-only `SELECT` on `system.*`

Reads `system.metrics`, `system.events`, `system.asynchronous_metrics`, and
`system.replicas` over the HTTP interface (port **8123** by default) with HTTP
Basic auth. `url` and `user` are plain config; only the **password** is a secret
(env var named by `password_env`, default `CLICKHOUSE_PASSWORD`).

```sql
CREATE USER gludd_mon IDENTIFIED BY 'CHANGE_ME_STRONG';
GRANT SELECT ON system.metrics              TO gludd_mon;
GRANT SELECT ON system.events               TO gludd_mon;
GRANT SELECT ON system.asynchronous_metrics TO gludd_mon;
GRANT SELECT ON system.replicas             TO gludd_mon;
-- (or simply: GRANT SELECT ON system.* TO gludd_mon;)
```
```bash
export CLICKHOUSE_PASSWORD='CHANGE_ME_STRONG'
```
```yaml
- module: clickhouse_stats
  config:
    url: "http://clickhouse.example.com:8123"
    user: "gludd_mon"
    password_env: "CLICKHOUSE_PASSWORD"
```
Verify (read-only): `health()` runs `SELECT 1 AS metric, 1 AS value`. Then:
```bash
curl -fsS "http://clickhouse.example.com:8123/?query=SELECT+1" \
  -u "gludd_mon:$CLICKHOUSE_PASSWORD"
```

## cassandra_stats — JMX-exporter scrape (no Cassandra credential)

This connector does **not** run `nodetool` and does **not** connect to Cassandra
directly. It scrapes a **JMX Prometheus exporter** endpoint
(`jmx_url`, default `http://localhost:7070/metrics`) and maps samples for the
`compactionstats` / `tablestats` / `tpstats` groups. An **optional** bearer token
(env var named by `token_env`, default `CASSANDRA_JMX_TOKEN`) is sent only if the
exporter is behind an auth proxy.

- **Least privilege:** none on Cassandra itself (the exporter holds JMX access).
  Only mint a scrape token if the exporter is proxied.
```bash
export CASSANDRA_JMX_TOKEN='<optional-scrape-token>'
```
```yaml
- module: cassandra_stats
  config:
    jmx_url: "http://cassandra-jmx:7070/metrics"
    token_env: "CASSANDRA_JMX_TOKEN"
```
Verify (read-only): `health()` scrapes the `tpstats`-relevant subset; or
`curl -fsS http://cassandra-jmx:7070/metrics | head`.

## Read-only guarantee

All SQL/command strings are static module constants — no user input is ever
interpolated. `query()` returns `[]` on driver/transport failure (mongodb,
clickhouse, cassandra) or raises only on an unknown spec name (postgres, mysql,
redis); `health()` never raises.
