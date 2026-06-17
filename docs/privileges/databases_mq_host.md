# Least-Privilege Access Guide — Databases / Message Queues / Host OS

**Status:** DOC-ONLY, UNCOMMITTED. Read-only reference.
**Scope:** Minimal monitoring credentials for the database, message-queue, and host/OS
facilities gludd reads from — plus copy-pasteable grant artifacts, where to apply
them, env-var tables, and a read-only verification command per facility.

---

## 0. Codebase reality check (read this first)

This guide was requested under the assumption that gludd ships a `connectors/`
package with files like `postgres_stats.py`, `kafka_exporter.py`, `snmp.py`,
`redfish.py`, `journald.py`, `osquery.py`, etc. **Those files do not exist in this
repository.** The investigation that backs this document searched the whole `src/`
tree:

- There is **no** `src/general_ludd/connectors/` directory.
- There is **no** `*_stats.py`, `kafka_exporter.py`, `rabbitmq.py`, `nats.py`,
  `snmp.py`, `redfish.py`, `journald.py`, `windows_event.py`, `macos_log.py`,
  `proc_sys.py`, `dmesg.py`, or `osquery.py` anywhere in the source.
- `redis`, `clickhouse`, `cassandra`, `rabbitmq` appear only as **test-fixture
  strings** and **Ansible-Galaxy search examples** — not as live monitoring code.
- The MCP catalog (`src/general_ludd/mcp/catalog.py`) has a `postgres` entry that
  launches the third-party `@modelcontextprotocol/server-postgres` MCP server; that
  is an optional tool surface, not a gludd-authored telemetry connector.

**The only data store gludd itself connects to is its own application database**
(`src/general_ludd/db/session.py`):

- It is **SQLite-only by design.** `init_engine_from_config()` *explicitly refuses*
  any non-SQLite URL with `ValueError("... general_ludd is SQLite only ...")`.
- Real env vars: `GLUDD_DB_PATH`, `XDG_DATA_HOME`, `DATABASE_URL` (the last is read
  in `_compose_db_url`, but a Postgres URL is then rejected downstream).
- Default URL: `sqlite+aiosqlite:///<XDG_DATA_HOME or ~/.local/share>/general-ludd/general-ludd.db`.

Because the connectors do not exist, **Sections 2–4 below cannot be derived from
real config keys.** They are provided as a *forward-looking, ready-to-apply
least-privilege reference* — the exact, vetted minimal grant you would attach the
moment such a connector is added — and every facility is flagged
`(NOT YET WIRED IN gludd)`. The env-var names in those sections are *proposed
conventions* (prefixed `GLUDD_*`), not keys the code reads today. Apply the grants;
treat the env names as placeholders until a connector lands and fixes its own names.

Section 1 (the SQLite/app-DB facility) is the only section grounded in shipped code.

---

# DATABASES

## 1. gludd application database (REAL — the one gludd actually reads)

### 1a. SQLite (default, shipped path)

SQLite has no server and no network credential. Least privilege = **filesystem
permissions** on the DB file and its WAL/SHM siblings. Run gludd under a dedicated,
unprivileged OS user that owns only its data dir.

**Apply (host shell, as root once):**
```bash
# Dedicated service user, no login shell, owns only the data dir
useradd --system --home /var/lib/gludd --shell /usr/sbin/nologin gludd
install -d -o gludd -g gludd -m 0750 /var/lib/gludd/general-ludd
# Point gludd at it
echo 'GLUDD_DB_PATH=/var/lib/gludd/general-ludd/general-ludd.db' >> /etc/gludd/env
chmod 0640 /var/lib/gludd/general-ludd/general-ludd.db        # after first run
```

**Read-only verification:**
```bash
sudo -u gludd test -r /var/lib/gludd/general-ludd/general-ludd.db && echo "readable by gludd OK"
sudo -u gludd sqlite3 -readonly /var/lib/gludd/general-ludd/general-ludd.db '.tables'
```

### 1b. Postgres for the app DB — *currently REFUSED by code*

`init_engine_from_config()` raises `ValueError` on any non-SQLite URL. Do **not**
provision a Postgres role for the app DB until that restriction is lifted. If/when it
is, use the `pg_monitor`-style least-privilege role below (Section 1c) but with
`CONNECT` + table privileges scoped to gludd's own schema only — never superuser.

### 1c. Postgres *monitoring* role (reference template) `(NOT YET WIRED IN gludd)`

Minimal monitoring credential: a `LOGIN` role with the built-in `pg_monitor` role
granted (covers `pg_stat_*`, `pg_stat_activity` full rows, `pg_ls_*`), plus
`CONNECT`. **Not** superuser, **not** `pg_read_all_data`.

**Apply (psql, as a superuser/owner):**
```sql
CREATE ROLE gludd_mon LOGIN PASSWORD 'CHANGE_ME_STRONG';
GRANT pg_monitor TO gludd_mon;
GRANT CONNECT ON DATABASE your_db TO gludd_mon;
-- optional, if reading a specific app schema's row counts:
-- GRANT USAGE ON SCHEMA app TO gludd_mon;
```
Where: run on the target Postgres cluster as `postgres` (or DB owner).
`pg_hba.conf` should restrict `gludd_mon` to the monitoring host with `scram-sha-256`.

**Read-only verification:**
```bash
PGPASSWORD='...' psql -h DBHOST -U gludd_mon -d your_db \
  -c "SELECT count(*) FROM pg_stat_activity;"
```

| Env var (proposed)      | Meaning                 | How to obtain                       | Maps to grant/role        |
|-------------------------|-------------------------|-------------------------------------|---------------------------|
| `GLUDD_PG_HOST`         | Postgres host           | infra inventory / DNS               | `pg_hba.conf` host entry  |
| `GLUDD_PG_PORT`         | port (default `5432`)   | infra inventory                     | listener port             |
| `GLUDD_PG_DB`           | database name           | infra inventory                     | `GRANT CONNECT ON DATABASE` |
| `GLUDD_PG_USER`         | login role              | set to `gludd_mon`                  | `CREATE ROLE gludd_mon`   |
| `GLUDD_PG_PASSWORD`     | role password           | secrets manager                     | `PASSWORD` clause         |
| `GLUDD_PG_SSLMODE`      | TLS mode (`verify-full`)| CA + server cert config             | `pg_hba.conf` `hostssl`   |

---

## 2. MySQL / MariaDB `(NOT YET WIRED IN gludd)`

Minimal monitoring credential: `PROCESS` + `REPLICATION CLIENT` globally, and
`SELECT` scoped to `performance_schema` only — **no** global `SELECT`. Add
`SHOW VIEW` only if the connector queries view definitions.

**Apply (mysql, as an admin):**
```sql
CREATE USER 'gludd_mon'@'10.0.%' IDENTIFIED BY 'CHANGE_ME_STRONG';
GRANT PROCESS, REPLICATION CLIENT ON *.* TO 'gludd_mon'@'10.0.%';
GRANT SELECT ON performance_schema.* TO 'gludd_mon'@'10.0.%';
-- only if view defs are read:
-- GRANT SHOW VIEW ON performance_schema.* TO 'gludd_mon'@'10.0.%';
FLUSH PRIVILEGES;
```
Where: run on the MySQL/MariaDB server as a `GRANT`-capable admin. The host mask
(`'10.0.%'`) restricts the account to the monitoring subnet.

**Read-only verification:**
```bash
mysql -h DBHOST -u gludd_mon -p -e \
  "SELECT COUNT(*) FROM performance_schema.global_status; SHOW PROCESSLIST;"
```

| Env var (proposed)   | Meaning             | How to obtain     | Maps to grant/role                 |
|----------------------|---------------------|-------------------|------------------------------------|
| `GLUDD_MYSQL_HOST`   | host                | inventory         | `'gludd_mon'@'<mask>'` host part   |
| `GLUDD_MYSQL_PORT`   | port (`3306`)       | inventory         | listener port                      |
| `GLUDD_MYSQL_USER`   | user (`gludd_mon`)  | set explicitly    | `CREATE USER`                      |
| `GLUDD_MYSQL_PASSWORD`| password           | secrets manager   | `IDENTIFIED BY`                    |
| `GLUDD_MYSQL_SOCKET` | unix socket path    | server config     | local-socket auth (alt to host)   |

---

## 3. Redis `(NOT YET WIRED IN gludd)`

Minimal monitoring credential: an ACL user, read-only, limited to `INFO`,
`SLOWLOG GET`, `CLIENT LIST`, and `PING`. No keyspace writes, no `CONFIG SET`,
no `FLUSHALL`.

**Apply (redis-cli, as default/admin):**
```redis
ACL SETUSER gludd_mon on >CHANGE_ME_STRONG ~* +info +slowlog|get +client|list +ping
ACL SAVE
```
Where: run against the Redis instance (or add the line to the `aclfile` /
`users.acl` referenced by `redis.conf`, then `ACL LOAD`). Bind Redis to an internal
interface and require TLS for remote scrapes.

**Read-only verification:**
```bash
redis-cli -h REDISHOST --user gludd_mon --pass 'CHANGE_ME_STRONG' INFO server
```

| Env var (proposed)   | Meaning           | How to obtain   | Maps to grant/role        |
|----------------------|-------------------|-----------------|---------------------------|
| `GLUDD_REDIS_HOST`   | host              | inventory       | bind interface            |
| `GLUDD_REDIS_PORT`   | port (`6379`)     | inventory       | listener port             |
| `GLUDD_REDIS_USER`   | ACL user          | set `gludd_mon` | `ACL SETUSER`             |
| `GLUDD_REDIS_PASSWORD`| ACL password     | secrets manager | `>password` in ACL        |
| `GLUDD_REDIS_TLS`    | enable TLS        | cert config     | `tls-port` in redis.conf  |

---

## 4. MongoDB `(NOT YET WIRED IN gludd)`

Minimal monitoring credential: a user with the built-in `clusterMonitor` role on
the `admin` database. No `read`/`readWrite` on app data, no `root`.

**Apply (mongosh, as an admin):**
```javascript
use admin
db.createUser({
  user: "gludd_mon",
  pwd: "CHANGE_ME_STRONG",
  roles: [ { role: "clusterMonitor", db: "admin" } ]
})
```
Where: run on a `mongos`/primary with an account holding `userAdmin`/`root`.
Restrict source IPs via `net.bindIp` and the firewall; require TLS.

**Read-only verification:**
```bash
mongosh "mongodb://gludd_mon:CHANGE_ME_STRONG@MONGOHOST:27017/admin" \
  --eval 'db.serverStatus().connections'
```

| Env var (proposed)    | Meaning              | How to obtain   | Maps to grant/role         |
|-----------------------|----------------------|-----------------|----------------------------|
| `GLUDD_MONGO_URI`     | full connection URI  | secrets manager | embeds user+host+TLS       |
| `GLUDD_MONGO_HOST`    | host (if not URI)    | inventory       | `net.bindIp`               |
| `GLUDD_MONGO_PORT`    | port (`27017`)       | inventory       | listener port              |
| `GLUDD_MONGO_USER`    | user (`gludd_mon`)   | set explicitly  | `db.createUser`            |
| `GLUDD_MONGO_PASSWORD`| password             | secrets manager | `pwd`                      |

---

## 5. ClickHouse `(NOT YET WIRED IN gludd)`

Minimal monitoring credential: a read-only user with a `readonly=1` settings profile
and `SELECT` granted only on `system.*`. No access to business databases.

**Apply (clickhouse-client, as admin):**
```sql
CREATE SETTINGS PROFILE gludd_ro SETTINGS readonly = 1;
CREATE USER gludd_mon IDENTIFIED WITH sha256_password BY 'CHANGE_ME_STRONG'
  SETTINGS PROFILE gludd_ro
  HOST IP '10.0.0.0/8';
GRANT SELECT ON system.* TO gludd_mon;
```
Where: run on the ClickHouse server as an admin (or define the user in
`users.xml`/`users.d/`). The `HOST IP` clause restricts the source subnet.

**Read-only verification:**
```bash
clickhouse-client --host CHHOST --user gludd_mon --password 'CHANGE_ME_STRONG' \
  --query "SELECT count() FROM system.metrics"
```

| Env var (proposed)    | Meaning            | How to obtain   | Maps to grant/role           |
|-----------------------|--------------------|-----------------|------------------------------|
| `GLUDD_CH_HOST`       | host               | inventory       | `HOST IP` clause             |
| `GLUDD_CH_PORT`       | native `9000`/`9440` TLS | inventory  | listener port                |
| `GLUDD_CH_HTTP_PORT`  | HTTP `8123`/`8443` | inventory       | http listener                |
| `GLUDD_CH_USER`       | user (`gludd_mon`) | set explicitly  | `CREATE USER`                |
| `GLUDD_CH_PASSWORD`   | password           | secrets manager | `IDENTIFIED WITH`            |

---

## 6. Cassandra `(NOT YET WIRED IN gludd)`

Minimal monitoring credential: a role with `SELECT` on the `system` and
`system_views` keyspaces only — no app keyspaces, no `CREATE`/`ALTER`, not superuser.

**Apply (cqlsh, as a superuser):**
```sql
CREATE ROLE gludd_mon WITH PASSWORD = 'CHANGE_ME_STRONG' AND LOGIN = true;
GRANT SELECT ON KEYSPACE system TO gludd_mon;
GRANT SELECT ON KEYSPACE system_views TO gludd_mon;
-- (Cassandra 4+: system_views/system_virtual_schema expose metrics)
```
Where: run via `cqlsh` authenticated as `cassandra`/a superuser. Restrict client
source IPs at the firewall; enable client-to-node TLS.

**Read-only verification:**
```bash
cqlsh CASSHOST -u gludd_mon -p 'CHANGE_ME_STRONG' \
  -e "SELECT cluster_name, release_version FROM system.local;"
```

| Env var (proposed)      | Meaning            | How to obtain   | Maps to grant/role     |
|-------------------------|--------------------|-----------------|------------------------|
| `GLUDD_CASS_HOSTS`      | seed host(s)       | inventory       | firewall allow         |
| `GLUDD_CASS_PORT`       | CQL port (`9042`)  | inventory       | listener port          |
| `GLUDD_CASS_USER`       | role (`gludd_mon`) | set explicitly  | `CREATE ROLE`          |
| `GLUDD_CASS_PASSWORD`   | password           | secrets manager | `WITH PASSWORD`        |

---

# MESSAGE QUEUES

## 7. Kafka via kafka-exporter `(NOT YET WIRED IN gludd)`

Two distinct trust boundaries:

1. **The exporter ↔ Kafka.** `kafka-exporter` holds Kafka credentials/ACLs to read
   broker, topic, and consumer-group lag. Grant it only `DESCRIBE`:
2. **gludd ↔ the exporter.** gludd would scrape the exporter's `/metrics` HTTP
   endpoint. **gludd holds NO Kafka credentials** — only network access to the
   exporter. Keep `/metrics` on an internal interface (optionally behind a
   read-only reverse proxy with basic-auth).

**Apply (Kafka ACLs for the exporter principal, kafka-acls.sh):**
```bash
kafka-acls.sh --bootstrap-server BROKER:9092 --add \
  --allow-principal User:kafka_exporter --operation Describe --cluster
kafka-acls.sh --bootstrap-server BROKER:9092 --add \
  --allow-principal User:kafka_exporter --operation Describe --topic '*'
kafka-acls.sh --bootstrap-server BROKER:9092 --add \
  --allow-principal User:kafka_exporter --operation Describe --group '*'
```
Where: ACLs run on a Kafka admin host. The exporter runs as a sidecar; gludd points
at `http://exporter:9308/metrics`.

**Read-only verification (the gludd side — no Kafka creds):**
```bash
curl -fsS http://EXPORTERHOST:9308/metrics | grep -m1 kafka_brokers
```

| Env var (proposed)        | Meaning                  | How to obtain        | Maps to grant/role            |
|---------------------------|--------------------------|----------------------|-------------------------------|
| `GLUDD_KAFKA_EXPORTER_URL`| exporter metrics URL     | infra inventory      | network ACL only (no Kafka creds) |
| *(exporter-side only)* `KAFKA_EXPORTER_SASL_USER` | exporter's Kafka principal | secrets mgr | `Describe` ACLs above |

---

## 8. RabbitMQ `(NOT YET WIRED IN gludd)`

Minimal monitoring credential: a user with the `monitoring` tag, which grants
read-only access to the management HTTP API (overview, queues, nodes) without
publish/consume rights. No `administrator` tag.

**Apply (rabbitmqctl, on a broker node):**
```bash
rabbitmqctl add_user gludd_mon 'CHANGE_ME_STRONG'
rabbitmqctl set_user_tags gludd_mon monitoring
# no set_permissions => no resource (configure/write/read) access
```
Where: run on any cluster node as an admin. Bind the management plugin
(`:15672`) to an internal interface; enable TLS (`:15671`).

**Read-only verification:**
```bash
curl -fsS -u gludd_mon:CHANGE_ME_STRONG http://RMQHOST:15672/api/overview
```

| Env var (proposed)     | Meaning                | How to obtain   | Maps to grant/role          |
|------------------------|------------------------|-----------------|-----------------------------|
| `GLUDD_RABBITMQ_URL`   | mgmt API base URL      | inventory       | `:15672` (or `:15671` TLS)  |
| `GLUDD_RABBITMQ_USER`  | user (`gludd_mon`)     | set explicitly  | `add_user`                  |
| `GLUDD_RABBITMQ_PASSWORD`| password             | secrets manager | `add_user` pwd              |
| `GLUDD_RABBITMQ_TAG`   | (informational) `monitoring` | n/a       | `set_user_tags monitoring`  |

---

## 9. NATS `(NOT YET WIRED IN gludd)`

NATS exposes an unauthenticated **monitoring HTTP port** (`:8222`, endpoints
`/varz`, `/connz`, `/subsz`, `/jsz`) that is *separate* from the client
protocol port (`:4222`). Least privilege:

- Bind `:8222` to `127.0.0.1` or an internal-only interface — no auth needed when it
  cannot be reached externally. **Network restriction is the control here.**
- If client-protocol metrics are needed instead, create a dedicated NATS user with
  read-only subject permissions (no `>` publish).

**Apply (nats-server.conf):**
```hocon
http: "127.0.0.1:8222"   # monitoring bound to loopback / internal only
# optional client user, subscribe-only:
authorization {
  users = [
    { user: "gludd_mon", password: "CHANGE_ME_STRONG",
      permissions: { subscribe: ">", publish: { deny: ">" } } }
  ]
}
```
Where: edit `nats-server.conf`, reload (`nats-server --signal reload`). Keep `:8222`
off any public interface.

**Read-only verification:**
```bash
curl -fsS http://127.0.0.1:8222/varz | grep -m1 '"server_id"'
```

| Env var (proposed)     | Meaning                   | How to obtain   | Maps to grant/role             |
|------------------------|---------------------------|-----------------|--------------------------------|
| `GLUDD_NATS_MONITOR_URL`| monitoring URL (`:8222`) | inventory       | internal-bind network ACL      |
| `GLUDD_NATS_URL`       | client URL (`:4222`)      | inventory       | optional subscribe-only user   |
| `GLUDD_NATS_USER`      | client user (`gludd_mon`) | set explicitly  | `authorization.users`          |
| `GLUDD_NATS_PASSWORD`  | client password           | secrets manager | user `password`                |

---

# HOST / OS

## 10. SNMP `(NOT YET WIRED IN gludd)`

Least privilege: **SNMPv3 only** with auth+priv (`authPriv`), a read-only user, and a
restricted view. **Never** an SNMPv1/v2c community string in production.

**Apply (snmpd.conf, on the monitored host):**
```conf
# create the v3 user (run once while snmpd stopped, or via net-snmp-create-v3-user)
createUser gludd_mon SHA-512 "AUTH_PASSPHRASE_CHANGE_ME" AES "PRIV_PASSPHRASE_CHANGE_ME"
# restricted read-only view: only the system + interface subtrees
view  gludd_view  included  .1.3.6.1.2.1.1     # system
view  gludd_view  included  .1.3.6.1.2.1.2     # interfaces
rouser gludd_mon priv -V gludd_view
```
Where: edit `/etc/snmp/snmpd.conf` (user line goes in
`/var/lib/snmp/snmpd.conf` after `createUser`), then `systemctl restart snmpd`.

**Read-only verification:**
```bash
snmpget -v3 -l authPriv -u gludd_mon -a SHA-512 -A 'AUTH...' -x AES -X 'PRIV...' \
  HOST sysDescr.0
```

| Env var (proposed)        | Meaning              | How to obtain   | Maps to grant/role        |
|---------------------------|----------------------|-----------------|---------------------------|
| `GLUDD_SNMP_HOST`         | target host          | inventory       | `snmpd` listen addr       |
| `GLUDD_SNMP_USER`         | v3 user (`gludd_mon`)| set explicitly  | `createUser`/`rouser`     |
| `GLUDD_SNMP_AUTH_PROTO`   | `SHA-512`            | policy          | `createUser` auth         |
| `GLUDD_SNMP_AUTH_PASS`    | auth passphrase      | secrets manager | `createUser`              |
| `GLUDD_SNMP_PRIV_PROTO`   | `AES`                | policy          | `createUser` priv         |
| `GLUDD_SNMP_PRIV_PASS`    | priv passphrase      | secrets manager | `createUser`              |

---

## 11. Redfish / IPMI (BMC) `(NOT YET WIRED IN gludd)`

Least privilege: a BMC account with the `ReadOnly` role (or `Operator` if power-state
reads require it) — **never** `Administrator`. Most BMCs (iDRAC/iLO/XCC) support a
read-only Redfish role.

**Apply (Redfish API or BMC console):**
```bash
# Redfish: create a ReadOnly account
curl -ks -u admin:ADMINPW -X POST \
  https://BMC/redfish/v1/AccountService/Accounts \
  -H 'Content-Type: application/json' \
  -d '{"UserName":"gludd_mon","Password":"CHANGE_ME_STRONG","RoleId":"ReadOnly","Enabled":true}'
```
Where: BMC web console → Users → add `gludd_mon` with role `ReadOnly`; or the
Redfish `AccountService` POST above. Restrict the BMC management network (out-of-band
VLAN, no internet exposure).

**Read-only verification:**
```bash
curl -ks -u gludd_mon:CHANGE_ME_STRONG https://BMC/redfish/v1/Systems | head
```

| Env var (proposed)        | Meaning            | How to obtain   | Maps to grant/role        |
|---------------------------|--------------------|-----------------|---------------------------|
| `GLUDD_REDFISH_URL`       | BMC base URL       | inventory       | mgmt-network ACL          |
| `GLUDD_REDFISH_USER`      | user (`gludd_mon`) | set explicitly  | account `RoleId=ReadOnly` |
| `GLUDD_REDFISH_PASSWORD`  | password           | secrets manager | account password          |
| `GLUDD_REDFISH_VERIFY_TLS`| verify BMC cert    | BMC CA          | TLS trust                 |

---

## 12. journald / systemd journal (Linux) `(NOT YET WIRED IN gludd)`

Least privilege: add the gludd OS user to the **`systemd-journal`** group, which
grants read access to the system journal without root.

**Apply (host shell, as root):**
```bash
usermod -aG systemd-journal gludd
# (re-login / restart the gludd service for the group to take effect)
```
Where: run on each monitored Linux host. No journal *write* is granted; group
membership is read-only.

**Read-only verification:**
```bash
sudo -u gludd journalctl -n 5 --no-pager
```

| Env var (proposed)     | Meaning              | How to obtain   | Maps to grant/role             |
|------------------------|----------------------|-----------------|--------------------------------|
| `GLUDD_JOURNAL_UNITS`  | unit filter (opt.)   | policy          | n/a (read scope only)          |
| *(no credential)*      | access is by OS group| `usermod -aG`   | `systemd-journal` group        |

---

## 13. Windows Event Log `(NOT YET WIRED IN gludd)`

Least privilege: add the gludd service account to the local **`Event Log Readers`**
group, which grants read-only access to the event logs without Administrator.

**Apply (elevated PowerShell on the host):**
```powershell
Add-LocalGroupMember -Group "Event Log Readers" -Member "DOMAIN\gludd_svc"
# (restart the gludd service for the token to pick up the new group)
```
Where: run on each monitored Windows host (or push via GPO Restricted Groups for a
fleet).

**Read-only verification (as the service account):**
```powershell
Get-WinEvent -LogName System -MaxEvents 5
```

| Env var (proposed)     | Meaning              | How to obtain   | Maps to grant/role             |
|------------------------|----------------------|-----------------|--------------------------------|
| `GLUDD_WINEVENT_LOGS`  | log names (opt.)     | policy          | n/a (read scope only)          |
| *(no credential)*      | access is by group   | `Add-LocalGroupMember` | `Event Log Readers` group |

---

## 14. macOS unified log `(NOT YET WIRED IN gludd)`

`log show` / `log stream` reading the system log generally requires **root or admin**;
there is no read-only group equivalent to `systemd-journal`. Least-privilege options,
best first:

1. **Run the collector as a LaunchDaemon under a dedicated admin-group user**, not as
   the console user, and scope it to `log show` with a predicate filter — do not give
   it broader root capabilities.
2. If only specific subsystems are needed, use a `--predicate` to narrow the data.
3. Avoid granting Full Disk Access / TCC unless a private log store path is required.

**Apply (LaunchDaemon plist `/Library/LaunchDaemons/dev.gludd.logcollector.plist`):**
```xml
<key>UserName</key><string>gludd</string>
<key>GroupName</key><string>admin</string>
<key>ProgramArguments</key>
<array>
  <string>/usr/bin/log</string><string>show</string>
  <string>--last</string><string>5m</string>
  <string>--predicate</string><string>subsystem == "com.apple.network"</string>
</array>
```
Where: install the plist, `launchctl load` it. Keep the `gludd` account in `admin`
only (not a sudoer with NOPASSWD ALL).

**Read-only verification:**
```bash
sudo log show --last 1m --style compact | head
```

| Env var (proposed)     | Meaning                 | How to obtain   | Maps to grant/role            |
|------------------------|-------------------------|-----------------|-------------------------------|
| `GLUDD_MACOS_LOG_PREDICATE` | log predicate filter | policy        | narrows read scope            |
| *(no credential)*      | access is by privilege  | LaunchDaemon `UserName`+`admin` | admin-group / root  |

---

## 15. /proc + /sys, dmesg, osquery (Linux host internals) `(NOT YET WIRED IN gludd)`

### 15a. /proc and /sys

Most of `/proc` and `/sys` is world-readable; a dedicated unprivileged `gludd` user
can read CPU/mem/net stats with **no extra grant**. A few entries (e.g.
`/proc/<pid>/...` of other users, some `/sys` paths) are restricted — leave them
restricted rather than escalating.

**Read-only verification:**
```bash
sudo -u gludd cat /proc/loadavg /proc/meminfo | head
```

### 15b. dmesg (kernel ring buffer)

If `kernel.dmesg_restrict=1` (the common hardened default), unprivileged `dmesg`
fails. Two least-privilege options, **prefer the capability**:

- **Grant `CAP_SYSLOG` to the gludd process** (via systemd
  `AmbientCapabilities=CAP_SYSLOG` or `setcap cap_syslog+ep` on a wrapper) instead of
  loosening the sysctl globally.
- Only if capabilities are unavailable, set `kernel.dmesg_restrict=0` — but that
  exposes the ring buffer to *all* local users, so it is the weaker choice.

**Apply (systemd unit drop-in, preferred):**
```ini
[Service]
AmbientCapabilities=CAP_SYSLOG
```
**Read-only verification:**
```bash
sudo -u gludd dmesg --read-only 2>&1 | head   # works once CAP_SYSLOG is granted
```

### 15c. osquery

osqueryd runs as root to populate its tables; gludd should **not** run osqueryd
itself. Least privilege = read-only access to the osquery results, two ways:

- **Read the osquery results log** (`/var/log/osquery/osqueryd.results.log`): make it
  group-readable and add `gludd` to that group — no root for gludd.
- Or query the **read-only extension socket** with a non-root client that has only
  socket access (`--allow_unsafe=false`, socket mode restricted to a group).

**Apply (log-tail path):**
```bash
chgrp gludd /var/log/osquery/osqueryd.results.log
chmod 0640 /var/log/osquery/osqueryd.results.log
usermod -aG osquery gludd   # if osqueryd writes group-owned logs
```
**Read-only verification:**
```bash
sudo -u gludd test -r /var/log/osquery/osqueryd.results.log && echo "osquery log readable OK"
```

| Env var (proposed)        | Meaning                    | How to obtain   | Maps to grant/role                 |
|---------------------------|----------------------------|-----------------|------------------------------------|
| `GLUDD_PROC_ROOT`         | proc mount (default `/proc`)| host config    | filesystem read (world-readable)   |
| `GLUDD_SYS_ROOT`          | sys mount (default `/sys`) | host config     | filesystem read                    |
| `GLUDD_DMESG_CAP`         | (informational) `CAP_SYSLOG`| systemd unit   | `AmbientCapabilities=CAP_SYSLOG`   |
| `GLUDD_OSQUERY_RESULTS_LOG`| osquery results log path   | osquery config  | group-read on the log file         |
| `GLUDD_OSQUERY_SOCKET`    | osquery extension socket   | osquery config  | socket group access                |

---

## Appendix — global least-privilege rules

1. **Never superuser/Administrator/root** where a scoped role exists
   (`pg_monitor`, `clusterMonitor`, `monitoring` tag, `ReadOnly` BMC role,
   `systemd-journal`/`Event Log Readers` groups, `CAP_SYSLOG`).
2. **Network is a privilege.** Bind monitoring ports (`:8222`, `:9308`, `:15672`,
   BMC, SNMP) to internal interfaces; firewall by source subnet; prefer TLS.
3. **Secrets live in a secrets manager**, injected as env vars at runtime — never
   committed. The `CHANGE_ME_STRONG` placeholders above must be replaced per host.
4. **Rotate** monitoring credentials on the same cadence as operator keys.
5. The `GLUDD_*` env names in Sections 2–15 are **proposed conventions**, not keys the
   current code reads. When a real connector lands, reconcile its actual config keys
   against this table before relying on the names.
