# Least-privilege: host & OS sources

Covers `journald`, `docker_api`, `windows_event`, `macos_log`, `proc_sys`,
`dmesg`, `osquery`, `snmp`, `redfish`, `containerd`. Most of these run on the
host and read OS facilities — their "credential" is OS/group/capability access,
not an API token. Only `snmp`, `redfish`, and (optionally) `containerd` read a
secret from a `*_env` config key.

Subprocess-backed connectors build an **argv list** (never a shell string),
validate operator-supplied filters against an allow-list (reject leading-dash
flag-injection and shell metacharacters), and call a runner that uses
`shell=False`. Path/socket-backed connectors confine reads under allow-listed
roots.

## Env-var / privilege table

| connector | KIND | secret `*_env` | access mechanism | least-privilege grant |
|---|---|---|---|---|
| journald | logs | — | exec `journalctl -o json --no-pager` | membership in `systemd-journal` group |
| docker_api | logs/events | — | UNIX socket `/var/run/docker.sock` (default) or TCP host | read on the docker socket (e.g. `docker` group) |
| windows_event | logs | — | `wevtutil qe …` or `Get-WinEvent` | **Event Log Readers** group |
| macos_log | logs | — | exec `log show --style json --last <dur>` | local user able to run `log` |
| proc_sys | metrics | — | confined reads under `/proc`, `/sys` | normal OS read of kernel files |
| dmesg | logs | — | exec `dmesg --json` | read of `/dev/kmsg` (often `CAP_SYSLOG` / `dmesg_restrict=0`) |
| osquery | metrics | — | exec `osqueryi --json "<SQL>"` | local user able to run `osqueryi` |
| snmp | metrics | `community_env` | UDP **161** (snmp) or `GET {base_url}/metrics` (exporter) | SNMPv2c **read-only** community / scrape token |
| redfish | metrics/events | `username_env`, `password_env` | `{base_url}/redfish/v1/...` over HTTPS | read-only BMC operator account |
| containerd | logs/metrics | `auth_token_env` (optional) | crictl over `unix:///run/containerd/containerd.sock`; pod-log fallback `/var/log/pods` | read on the CRI socket / pod-log dir |

## journald

`JournaldSource` runs `journalctl -o json --no-pager` with validated `--unit`,
`--priority`, `--since`, `-n` filters. No env var, no network.

- **Least privilege:** add the agent's service account to the `systemd-journal`
  group so it can read the journal without root.
```bash
sudo usermod -aG systemd-journal gludd
```
Verify (read-only): `journalctl -o json --no-pager -n 1` returns one entry —
exactly what `health()` runs.

## docker_api

Reads `GET /containers/json`, `/containers/{id}/logs`, `/events`. The default
target is the UNIX socket `/var/run/docker.sock` (socket path is confined to
`/var/run` or `/run`; traversal/escape is rejected). A TCP `tcp_host` is
SSRF-guarded and refuses local/internal hosts unless `allow_local=True`.

- **Least privilege:** grant read on the docker socket via the `docker` group
  (note: docker-socket access is effectively root-on-host — prefer a rootless or
  read-only socket proxy in production). No write/exec endpoints are used.
```bash
sudo usermod -aG docker gludd
```
Verify (read-only):
```bash
curl -fsS --unix-socket /var/run/docker.sock http://localhost/containers/json?limit=1
```

## windows_event

Reads a channel via `wevtutil qe <Log> /f:json` or
`Get-WinEvent -LogName <Log> | ConvertTo-Json`. Channel names are validated
(allow-list `[A-Za-z0-9 _\-/]+`, leading dash rejected). No env var.

- **Least privilege:** add the service account to the built-in **Event Log
  Readers** group (grants read of System/Application/etc. without admin).
```powershell
Add-LocalGroupMember -Group "Event Log Readers" -Member "DOMAIN\gludd_svc"
```
Verify (read-only): `wevtutil qe System /f:json /c:1 /rd:true` — the
`health()` probe.

## macos_log

Runs `log show --style json --last <dur>` with a validated duration
(`\d+[smhd]`) and an optional validated predicate (shell/log metacharacters
rejected). No env var, no network.

- **Least privilege:** any local user can read the unified log via `log show`;
  no elevation needed for system logs in most configs.
Verify: `log show --style json --last 1s` — the `health()` probe.

## proc_sys

Reads kernel-exported files confined to `/proc` and `/sys` (named selectors:
`stat`, `meminfo`, `loadavg`, `pressure_*`, `net_dev`, `diskstats`). Any path
that normalizes outside the allow-listed roots is refused **before** the reader
is called. No env var, no network, no subprocess.

- **Least privilege:** ordinary OS read access; these files are world-readable on
  a default Linux host.
Verify: read `/proc/loadavg` (the `health()` probe).

## dmesg

Runs `dmesg --json` with validated `--facility`/`--level` filters. No env var.

- **Least privilege:** on kernels with `kernel.dmesg_restrict=1`, grant
  `CAP_SYSLOG` to the binary/service (or set `dmesg_restrict=0`); otherwise no
  special privilege is needed.
```bash
sudo setcap cap_syslog+ep "$(command -v dmesg)"
```
Verify: `dmesg --json` exits 0 (the `health()` probe).

## osquery

Runs `osqueryi --json "<SQL>"`. The SQL is validated for shell metacharacters
and command-chaining before becoming a single argv element. No env var.

- **Least privilege:** a local user able to execute `osqueryi`. Read-only SQL
  only; the validator rejects `;`-chained shell injection.
Verify: `osqueryi --json "SELECT 1"` (the `health()` probe).

## snmp

Two modes (`config["mode"]`):

- **`snmp`** (default): pulls OIDs over UDP **161** via pysnmp. The community
  string is read **only** from `community_env` and is **redacted everywhere** it
  could surface (labels/raw/errors use `***redacted***`).
- **`exporter`**: scrapes an `snmp_exporter`-style `GET {base_url}/metrics`; this
  path is SSRF-guarded (resolves the host and rejects private/loopback unless
  `allow_private`).

- **Least privilege:** create a dedicated **SNMPv2c read-only community** scoped
  to the needed OID subtree on the device. Never reuse a read-write community.
```bash
export SNMP_COMMUNITY='gludd_ro'   # read-only community
```
```yaml
- module: snmp
  config:
    mode: "snmp"
    host: "192.0.2.10"
    port: 161
    community_env: "SNMP_COMMUNITY"
    oids: ["1.3.6.1.2.1.1.3.0"]   # sysUpTime
```
Verify (read-only, off-box): `snmpget -v2c -c "$SNMP_COMMUNITY" 192.0.2.10 sysUpTime.0`.
`health()` returns `"pysnmp unavailable"` (not an exception) if the driver is
missing.

## redfish

Reads BMC telemetry over HTTPS: `Chassis/{id}/Thermal`, `.../Power`,
`Systems/{id}/LogServices/Log/Entries`. Basic-auth credentials come from
`username_env` (default `REDFISH_USERNAME`) and `password_env` (default
`REDFISH_PASSWORD`). Because BMCs are internal, the SSRF default is **inverted**:
private/loopback literal hosts are rejected unless `allow_private=True`.

- **Least privilege:** a BMC account with the **ReadOnly** (operator/monitor)
  Redfish role — enough to GET Thermal/Power/Log resources, no config/power-action
  rights.
```bash
export REDFISH_USERNAME='gludd_ro'
export REDFISH_PASSWORD='CHANGE_ME'
```
```yaml
- module: redfish
  config:
    base_url: "https://bmc.example.internal"
    allow_private: true            # BMCs live on a management LAN
    username_env: "REDFISH_USERNAME"
    password_env: "REDFISH_PASSWORD"
    chassis_ids: ["1"]
    system_ids: ["1"]
```
Verify (read-only): `health()` GETs `/redfish/v1/` and reports the service-root
status.

## containerd

Two acquisition paths, both confined and shell-free:
1. crictl over the runtime endpoint socket (default
   `/run/containerd/containerd.sock`, validated to live under `/run` or
   `/var/run`): `ps -a`, `logs --tail`, `stats -a` (`-o json`).
2. path-confined read of CRI pod logs under `/var/log/pods` (fallback / when no
   runner is injected).

An optional `auth_token_env` is appended to crictl argv as `--auth-token
<value>` (the secret is read from the env var name, never inlined).

- **Least privilege:** read access to the CRI socket (group ownership on
  `containerd.sock`) and/or read on `/var/log/pods`. No container exec/run.
```yaml
- module: containerd
  config:
    runtime_endpoint: "/run/containerd/containerd.sock"
    pod_log_root: "/var/log/pods"
    auth_token_env: "CRICTL_AUTH_TOKEN"   # optional
    tail_lines: 200
```
Verify (read-only): `health()` runs `crictl version -o json` (or confirms the
pod-log root is a directory when no runner is wired).
