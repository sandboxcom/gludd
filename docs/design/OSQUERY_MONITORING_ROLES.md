# osquery Monitoring Roles — direct host introspection + decisioning (design, 2026-07-10)

Status: **design-complete, not yet implemented** for the roles/packs/ingestion
layer below. The ad-hoc query primitive this design builds on (§1.1) is
**already shipped and tested** — this doc extends it, it does not replace it.
Line numbers are current-tree at authoring time — re-confirm with a Read
before implementing, they drift. Style/format mirrors
`docs/design/PIPELINE_INTERACTION_ROLES.md` and
`docs/design/CI_PIPELINE_MEDIC_ROLE.md`.

**Scope.** Two capabilities, both host-local (osquery has no remote-agent
mode in this design — "the system gludd runs on" is always `localhost`):

1. **Ad-hoc**: any role/model can run one read-only `SELECT` against the live
   host and branch on the rows immediately. **Already built** (§1.1) — reuse
   it, don't re-implement it.
2. **Scheduled**: curated query *packs* with interval schedules run inside a
   managed `osqueryd`, differential/snapshot rows land in osquery's own
   results/snapshot logs, and a new ingestion module turns those log lines
   into `ansible_facts` a decision role can branch on — this is the new
   surface this doc adds.

---

## 1. SURVEY — what exists today

### 1.1 Ad-hoc query module — `gludd_osquery` (FULLY IMPLEMENTED, do not duplicate)

`collections/ansible_collections/general_ludd/agent/plugins/modules/gludd_osquery.py:1-324`
already does exactly requirement 1 ("agents can run ad-hoc osquery SQL"):

- `argument_spec`: `query` (required str), `timeout` (int, default 10),
  `osquery_path` (str, default `""`), `daemon_url`/`psk` (present but
  currently **reserved/no-op** — `:215-224` warns if set, has no effect yet).
- **SELECT-only enforcement** (`validate_select_only`, `:147-165`): strips
  `/* */` and `--` comments (`:142-144`), rejects any of `INSERT UPDATE
  DELETE DROP CREATE ALTER ATTACH DETACH PRAGMA REPLACE VACUUM REINDEX
  TRIGGER` as whole words (`_FORBIDDEN_RE`, `:133-135`), rejects stacked
  statements (a second `;`-separated statement, `:156-158`), requires the body
  to start with `SELECT` or `WITH ... SELECT` (`:163`, `_SELECT_PREFIX_RE`
  `:132`).
- **Binary resolution** (`resolve_osquery_binary`, `:168-195`): explicit
  `osquery_path` param → daemon filestore (`BinaryBootstrapper().
  get_binary_path("osquery")`, `:181-186`, same-venv only) → `shutil.which
  ("osqueryi")` (`:192-194`). Non-executable explicit path fails with a
  precise `chmod +x` message (`:225-231`).
- **Execution**: `subprocess.run([binary, "--json", query], ...)` — always an
  argv list, never `shell=True` (`:269-275`). Timeout and non-JSON output
  fail clearly (`:276-281`, `:296-301`).
- **Check-mode safe**: validates + locates the binary but never executes
  (`:252-265`).
- **Returns**: `ansible_facts.gludd_osquery = {rows: [...], count: int,
  query: str}` (`:306-319`).
- **Tested**: `tests/unit/test_gludd_osquery_module.py:1-512` — 20+ cases
  covering SELECT-validation, injection-shaped queries, timeout, bad JSON,
  non-executable path, filestore-probe-exception warning, `daemon_url`/`psk`
  reserved-param warnings.

**This module is the building block every new role in this design calls —
none of them re-implement SELECT-validation or binary resolution.**

### 1.2 Read-only fan-out connector — `OsquerySource` (separate mechanism, also implemented)

`src/general_ludd/connectors/osquery.py:1-204` — a `KIND = "metrics"`
connector for the daemon's `ConnectorRegistry` fan-out (`connectors/
registry.py`), independent of the Ansible module above. Same SELECT-shell-
metachar guard philosophy but a *different, narrower* validator
(`_SHELL_METACHARS`/`_SHELL_CHAIN`, `:54-57` — rejects shell metacharacters,
not SQL-mutation keywords) and an **injected** `CommandRunner` protocol
(`:24-33`) rather than the module's direct `subprocess.run` — the connector
is unit-tested with a canned runner, no real binary
(`tests/unit/test_connector_osquery.py:1-151`). It normalizes each result row
into the connector spine's 8-key metric record (`ts/source/kind/
level_or_status/message/value/labels/raw`, `_normalize_row` `:159-181`) — one
record per COLUMN, not per row, so a 5-column/10-row query yields 50 metric
records. `config/examples/connectors_example.yml:1-63` shows the operator
config shape (`module: osquery` would slot in the same way `journald`/
`prometheus` do). **Per `docs/design/BINARY_BUNDLING.md:60,108-110`**: this
connector's binary resolution is currently **inconsistent** with `gludd_osquery`
— bare `self._binary = "osqueryi"` (`osquery.py:72`) with only a PATH health
check (`:109`), no filestore-first lookup. Out of scope to fix here (tracked
in BINARY_BUNDLING.md), but the new roles below use `gludd_osquery`'s
resolver, not this connector's.

### 1.3 Cheap availability probe — `_osquery_facet` (implemented, informational only)

`src/general_ludd/routers/facts.py:315-367` — `/api/facts` includes an
`osquery` facet: filestore-first then PATH (`:332-347`, mirrors the pattern
`gludd_osquery` also uses), runs `osqueryi --version` with a 2s timeout,
never blocks `/api/facts` (`:340-341,365-366` fail soft). Returns `{available,
version, source, path}`. This is a good model for a role/module that just
needs "is osquery here" without running a query — reused by the new
`osquery_bootstrap` role (§3.1) rather than re-probed.

### 1.4 Binary bundling status — `osqueryi` only; `osqueryd` is NOT bootstrapped (the load-bearing gap)

`src/general_ludd/filestore/bootstrap.py`:
- `BinaryBootstrapper.KNOWN_VERSIONS["osquery"] = OSQUERY_VERSION` (`:82`,
  `OSQUERY_VERSION = "5.10.2"` at `:52`).
- `_osquery_download_url` (`:250-275`) builds the GitHub release tarball URL
  `osquery-5.10.2.<macos|linux>_<x86_64|arm64>.tar.gz`. **Known bug already
  flagged in `docs/design/BINARY_BUNDLING.md:207-208`, still present**:
  `:270-271` `if osq_arch != "x86_64": return None` wrongly drops
  linux/arm64 (osquery DOES publish a linux arm64 tarball for 5.10.2); macOS
  correctly has no arm64 tarball (`:265-266` only branches on `darwin`/
  `linux`, never returns an arm64 asset for darwin — that part is right).
- `_TARBALL_BINARIES: ClassVar[dict[str, str]] = {"osquery": "osqueryi", ...}`
  (`:353-356`) — **the extraction map only pulls the `osqueryi` executable
  out of the downloaded tarball.** The same tarball also contains `osqueryd`
  (`opt/osquery/bin/osqueryd` on Linux, `.../MacOS/osqueryd` on macOS,
  matched by `_extract_executable_member`'s basename search, `:300-330`,
  which is arch/path agnostic) — **but nothing ever asks for it.** Query
  packs (§4) require `osqueryd` (it is the schedulable daemon; `osqueryi` is
  the interactive ad-hoc shell only and has no `--config_path`/`--pack_path`
  scheduling loop). **This is the concrete implementation gap this design's
  `osquery_bootstrap` role (§3.1) closes** — see §7.
- `KNOWN_SHA256` (`:40`, module-level) is **intentionally empty by default**
  (fail-closed — `:27-39` comment). `POST /admin/filestore/bootstrap?
  binary=osquery` (`routers/filestore.py:80-109`) will download-and-verify
  successfully **only if the operator has set `GLUDD_BINARY_SHA256={"osquery":
  "<pin>"}` or passed `known_sha256=`** — out of the box this path fails
  closed with no pin configured. This is deliberate existing security
  posture, not a bug to fix; the new roles must not weaken it (§7, §8).

### 1.5 What is MISSING — the actual gap this design fills

None of the following exist anywhere in `collections/` or `src/` today
(confirmed by direct read of the roles/modules above and their tests; no role
directory under `collections/ansible_collections/general_ludd/agent/roles/`
is named `osquery*`/`system_monitor*`/anything osquery-adjacent):

- **No query-pack files** (osquery's native `{"queries": {...}}` JSON
  schedule format) anywhere in the tree.
- **No `osqueryd` process management** — nothing starts, stops, or health-
  checks a long-running `osqueryd`.
- **No results/snapshot-log ingestion** — nothing reads `osqueryd.results.log`
  or `osqueryd.snapshots.log` and turns rows into facts.
- **No decision role** that branches on osquery data (rogue port, disk
  threshold, new setuid binary, etc.) — the closest existing precedent is
  `roles/report_status/tasks/main.yml:23-56` (branches on **gludd's own**
  `gludd_facts`, not host state) — that role's `set_fact` +
  classification-`when:` + JSON/markdown-artifact pattern is the template
  this design's decision roles (§3.5, §3.6) follow.
- **Adjacent but non-overlapping existing infra** (do not conflate): `playbooks/
  system_load_scrape.yml:1-63` collects `ansible_facts`/`psutil` CPU+memory
  numbers (no osquery, no packs, no thresholds/decisions — just a raw
  artifact dump); `collections/.../modules/gludd_proc_monitor.py:1-60+`
  monitors **gludd's own managed processes** (CPU/RSS/FDs) via the daemon's
  `/admin/processes` API, not host-wide osquery state — it explicitly says it
  "mirrors gludd_osquery" (`:23`) for the check-mode-safe pattern, confirming
  `gludd_osquery` as the house style. Neither of these two is a duplicate of
  what this design adds.

---

## 2. Role roster

New package: query-pack JSON files live under
`collections/ansible_collections/general_ludd/agent/roles/osquery_pack_deploy/files/packs/`
(the Ansible convention for role-owned static payloads).

| Role | One-line purpose |
|---|---|
| `osquery_bootstrap` | Detect/resolve `osqueryi` **and** `osqueryd` (PATH → filestore → bootstrap-download), verify both are executable, report privilege posture. Every other role in this design depends on it. |
| `osquery_adhoc_query` | Thin composable wrapper around the existing `gludd_osquery` module + artifact write, so ad-hoc queries fit the same role/playbook/artifact conventions as everything else in `collections/.../roles/`. |
| `osquery_pack_deploy` | Render the curated pack JSON(s) (§4) to disk, write/refresh the `osqueryd` flagfile, and (re)start the managed `osqueryd` process only when pack content actually changed (content-hash idempotency). |
| `osquery_results_ingest` | Auto-detect and tail `osqueryd.results.log` / `osqueryd.snapshots.log` since a checkpoint, parse JSON lines into per-query fact buckets. |
| `osquery_security_watch` | Decision role over the security pack's ingested facts: rogue listening ports, new setuid binaries, new kernel modules, new user accounts, fileless processes, unexpected egress → alert/branch. |
| `osquery_host_health_watch` | Decision role over the SDLC-host-health pack's ingested facts: disk/memory thresholds, service-down drift, critical-binary tamper check, orphaned gludd processes → alert/branch. |

A composing playbook, `playbooks/osquery_monitor.yml` (mirrors
`playbooks/system_report.yml`'s pattern of `include_role`-chaining a family of
roles into one artifact directory), runs `osquery_bootstrap` →
`osquery_pack_deploy` → `osquery_results_ingest` → both watch roles in
sequence and writes a consolidated `osquery_monitor_index.json`.

---

## 3. Roles in detail — module args, tasks, and returned facts

### 3.1 `osquery_bootstrap`

**New module**: `gludd_osquery_bootstrap.py` (mirrors `gludd_osquery.py`'s
DOCUMENTATION/argument_spec/RETURN shape and `resolve_osquery_binary`, adding
a second binary name and privilege probe).

```
argument_spec:
  ensure_daemon: {type: bool, default: true}   # also resolve/verify osqueryd, not just osqueryi
  allow_download: {type: bool, default: true}  # if PATH/filestore miss, POST /admin/filestore/bootstrap
  daemon_url: {type: str, default: "http://localhost:8000"}
  psk: {type: str, no_log: true, default: ""}
  timeout: {type: int, default: 30}
```

Resolution order per binary (osqueryi, and osqueryd when `ensure_daemon`):
explicit override → `BinaryBootstrapper().get_bundled_binary_path(name)` →
`get_binary_path(name)` (same-venv filestore) → `shutil.which(name)` → if
`allow_download`, `POST {daemon_url}/admin/filestore/bootstrap?binary=<name>`
(reuses the existing endpoint, `routers/filestore.py:80-109` — **fails
closed with a clear message if `GLUDD_BINARY_SHA256` has no pin for the
name**, per §1.4 — the role must surface that message verbatim, never retry
silently or fall back to an unverified path).

**Returns** `ansible_facts.gludd_osquery_bootstrap`:
```json
{
  "osqueryi_path": "/path/or/null",
  "osqueryi_source": "path|filestore|bundled|downloaded|null",
  "osqueryd_path": "/path/or/null",
  "osqueryd_source": "path|filestore|bundled|downloaded|null",
  "version": "5.10.2",
  "privileged": false,
  "privilege_probe": "unprivileged: kernel_modules/suid_bin returned 0 rows"
}
```

`privileged`/`privilege_probe` come from a one-shot check: run `SELECT count(*)
FROM kernel_modules` (Linux) / `SELECT count(*) FROM kernel_extensions`
(macOS) via `osqueryi`; a `0`-row result with no error is the standard
unprivileged-degrade signature (osquery does not error on missing privilege
for most tables, it silently returns fewer/no rows) — surfaced so
`osquery_security_watch` (§3.5) can annotate its findings as
"possibly-incomplete: unprivileged" rather than asserting a clean bill of
health it cannot actually vouch for (see §7 for the full privilege
discussion).

### 3.2 `osquery_adhoc_query`

No new module — wraps `gludd_osquery` directly.

```yaml
# roles/osquery_adhoc_query/tasks/main.yml
- name: Run ad-hoc osquery SELECT
  general_ludd.agent.gludd_osquery:
    query: "{{ osquery_adhoc_query }}"
    timeout: "{{ osquery_adhoc_timeout | default(10) }}"
  register: adhoc_result

- name: Write ad-hoc query artifact
  ansible.builtin.copy:
    content: "{{ adhoc_result.ansible_facts.gludd_osquery | to_nice_json }}"
    dest: "{{ artifact_dir }}/osquery_adhoc_{{ osquery_adhoc_label | default('query') }}.json"
    mode: "0644"
```

`defaults/main.yml`: `osquery_adhoc_query: "SELECT 1"`, `osquery_adhoc_label:
"query"`, `osquery_adhoc_timeout: 10`. Exists purely so a playbook composing
several roles (e.g. `debug_failure`-style diagnosis flows) can
`include_role: osquery_adhoc_query` with a `vars:` override the same way it
already does for `report_status`/`ci_pipeline_verify`, instead of hand-writing
a bespoke `gludd_osquery` task inline every time.

### 3.3 `osquery_pack_deploy`

**New module**: `gludd_osquery_pack.py`.

```
argument_spec:
  pack_name: {type: str, required: true}          # e.g. "gludd_security"
  pack_json: {type: str, required: true}           # the pack's JSON body (rendered by the role from files/packs/*.json.j2 or copied verbatim)
  config_dir: {type: str, default: "/etc/osquery"} # falls back to a user-writable dir when unprivileged, see below
  logger_path: {type: str, default: ""}            # "" = role default (see §5)
  osqueryd_path: {type: str, required: true}       # from osquery_bootstrap's ansible_facts
  manage_process: {type: bool, default: true}      # start/restart via the daemon's process API vs. "config-only, operator manages the daemon"
  daemon_url: {type: str, default: "http://localhost:8000"}
  psk: {type: str, no_log: true, default: ""}
```

Steps: (1) write `pack_json` to `<config_dir>/packs/<pack_name>.json`
(content-hash compare against the existing file first — `changed=false` if
identical, matching every other idempotent gludd module); (2) render/refresh
an `osquery.conf` flagfile pointing `--pack_path` at every deployed pack and
`--logger_path` at the results-log directory; (3) validate with `osqueryd
--config_check --config_path=<flagfile>` (never skip this — a broken pack
must never reach a running daemon); (4) if `manage_process` and the config
actually changed, (re)start `osqueryd` via `POST /api/dispatch` (kind=`process`,
mirroring `gludd_dispatch.py`'s existing generic dispatch shape, §1.4 of
`PIPELINE_INTERACTION_ROLES.md`'s sibling-module precedent) so the daemon's
own process registry tracks it (same registry `gludd_proc_monitor` already
reads) instead of an orphan subprocess Ansible loses track of after the play
ends.

**Returns** `ansible_facts.gludd_osquery_pack`:
```json
{
  "pack_path": "/etc/osquery/packs/gludd_security.json",
  "config_valid": true,
  "changed": true,
  "osqueryd_restarted": true,
  "osqueryd_pid": 41213
}
```

`config_check` failing must `fail_json` with osqueryd's own stderr verbatim
(it names the exact bad query) — never silently drop the pack or restart on
a config it couldn't validate.

### 3.4 `osquery_results_ingest`

**New module**: `gludd_osquery_results.py`.

```
argument_spec:
  logger_path: {type: str, default: ""}     # "" = auto-detect, see §5
  checkpoint_path: {type: str, default: ""} # "" = "<logger_path>/.gludd_osquery_checkpoint.json"
  max_lines: {type: int, default: 5000}     # cap per invocation so a huge backlog can't stall a play
```

Read-only, check-mode safe (check mode reads the checkpoint and reports
`new_rows: 0` without mutating it — mirrors `gludd_osquery`'s check-mode
posture of "locate but don't act").

**Returns** `ansible_facts.gludd_osquery_results`:
```json
{
  "rows_by_query": {
    "listening_ports": [{"pid": "1234", "local_port": "4444", "action": "added", "unixTime": 1751234000}],
    "suid_bin":        [{"path": "/tmp/.hidden/x", "action": "added", "unixTime": 1751234010}],
    "mounts_root":      [{"pct_used": "92.3", "unixTime": 1751234020}]
  },
  "checkpoint": {"results_log_offset": 48213, "snapshots_log_offset": 9120},
  "truncated": false,
  "log_paths": {"results": "/etc/osquery/log/osqueryd.results.log", "snapshots": "/etc/osquery/log/osqueryd.snapshots.log"}
}
```

Full parsing mechanics in §5.

### 3.5 `osquery_security_watch`

No new module — consumes `osquery_results_ingest`'s facts (or, if the pack
isn't deployed yet, falls back to running the equivalent ad-hoc queries via
`gludd_osquery` directly — same graceful-degrade shape `report_status` uses
when a fact is `default(0)`).

```yaml
# roles/osquery_security_watch/tasks/main.yml (excerpt — full file follows
# the report_status set_fact/when/artifact pattern, roles/report_status/
# tasks/main.yml:23-121)
- name: Ingest osquery results
  ansible.builtin.include_role:
    name: osquery_results_ingest

- name: Flag rogue listening ports
  ansible.builtin.set_fact:
    _osw_rogue_ports: >-
      {{ ansible_facts.gludd_osquery_results.rows_by_query.listening_ports
         | default([])
         | selectattr('action', 'equalto', 'added')
         | rejectattr('local_port', 'in', osquery_allowed_ports)
         | list }}

- name: Flag new setuid binaries
  ansible.builtin.set_fact:
    _osw_new_suid: >-
      {{ ansible_facts.gludd_osquery_results.rows_by_query.suid_bin | default([])
         | selectattr('action', 'equalto', 'added') | list }}

# ... same shape for new_kernel_modules, new_users, fileless_processes,
# unexpected_egress (§4 pack 1, queries 3-7)

- name: Set overall security verdict
  ansible.builtin.set_fact:
    _osw_verdict: >-
      {{ 'critical' if (_osw_new_suid | length > 0 or _osw_fileless | length > 0 or _osw_new_root_user | length > 0)
         else 'warn' if (_osw_rogue_ports | length > 0 or _osw_new_kmods | length > 0 or _osw_unexpected_egress | length > 0)
         else 'clean' }}

- name: Alert on non-clean verdict
  general_ludd.agent.gludd_message:
    state: send
    sender: osquery_security_watch
    recipient: "{{ osquery_alert_recipient }}"
    topic: "osquery_security_watch.{{ _osw_verdict }}"
    body: "{{ {'rogue_ports': _osw_rogue_ports, 'new_suid': _osw_new_suid, ...} | to_json }}"
  when: _osw_verdict != 'clean'

- name: Escalate CRITICAL findings to a human todo
  general_ludd.agent.gludd_human_todo:
    state: create
    title: "osquery: CRITICAL host-security finding on {{ ansible_hostname | default('localhost') }}"
    description: "{{ {'new_suid': _osw_new_suid, 'fileless_processes': _osw_fileless, 'new_root_user': _osw_new_root_user} | to_nice_json }}"
    priority: high
  when: _osw_verdict == 'critical'
```

`defaults/main.yml`: `osquery_allowed_ports: ["22","80","443","8000"]`,
`osquery_alert_recipient: "primary"`. Writes
`osquery_security_watch.json`/`.md` artifacts (same two-file pattern as every
`report_*` role).

### 3.6 `osquery_host_health_watch`

Same shape as §3.5 over the SDLC-host-health pack (§4 pack 2): disk
`pct_used` / memory `pct_used` threshold branches (`>90` → warn, `>97` →
critical), service-down detection against an operator-supplied
`osquery_expected_services` allowlist, critical-binary hash mismatch against
a baseline artifact (`osquery_binary_baseline.json`, written once on first run
and diffed thereafter — the same "baseline written first run, diffed on
subsequent runs" idiom used for `_osw_new_suid`-style "new since last known
state" detections, except here the state IS the differential log's own
`action` field so no separate baseline file is needed for suid/kmod/users —
only the `hash` table's on-demand rows need an explicit baseline since that
query is deliberately re-run fresh each interval rather than logged
differentially, see §4 pack 2 query 6), and an orphaned-gludd-process
cross-check against `gludd_proc_monitor`'s own managed-process list
(`ansible_facts.gludd_proc_monitor.processes | map(attribute='pid') | list`)
— a `processes` pack row named `gludd_self_processes` whose `pid` is absent
from that list is an **unmanaged gludd process** worth a warn-level alert
(process escaped the registry, or a stale/duplicate instance).

---

## 4. Query packs — concrete, ready-to-ship

Both packs use osquery's native pack JSON schema
(`{"platform": "...", "queries": {"<name>": {"query", "interval",
"platform", "snapshot", "description"}}}`). **Mode matters**: queries left as
plain differential (no `"snapshot"` key, the default) log only *changed*
rows to `osqueryd.results.log` with `"action":"added"/"removed"` — perfect
for "detect something NEW appearing" decisions, since osquery computes the
diff for you. Queries needing the **current absolute value every interval**
(thresholds) are marked `"snapshot": true` and land in the separate
`osqueryd.snapshots.log` instead (§5).

### Pack 1 — `gludd_security.json`

| Query name | SQL | Interval | Mode | Decision it drives |
|---|---|---|---|---|
| `listening_ports` | `SELECT pid, protocol, family, local_address, local_port, path FROM listening_ports WHERE address NOT IN ('127.0.0.1','::1');` | 300s | differential | A newly-`added` row whose `local_port` is outside `osquery_allowed_ports` → rogue-listener alert (§3.5). |
| `suid_bin` | `SELECT path, uid, gid, mode, mtime FROM suid_bin;` | 3600s | differential | Any `added` row → new setuid binary appeared → CRITICAL. |
| `kernel_modules_linux` (`platform: linux`) | `SELECT name, size, used_by, status FROM kernel_modules;` | 3600s | differential | `added` row whose `name` is not in an operator kmod allowlist → alert (rootkit/unexpected-driver signature). |
| `kernel_extensions_macos` (`platform: darwin`) | `SELECT name, version, path FROM kernel_extensions;` | 3600s | differential | Same decision, macOS table. |
| `privileged_users` | `SELECT username, uid, gid, shell, directory FROM users WHERE uid = 0 OR uid >= 1000;` | 3600s | differential | `added` row with `uid = 0` → CRITICAL (new root-equivalent account); `added` with `uid >= 1000` → warn (new local account). |
| `crontab_drift` | `SELECT command, path, minute, hour, day_of_month, month, day_of_week FROM crontab;` | 3600s | differential | `added` row whose `command`/`path` isn't in an operator allowlist → alert (persistence mechanism). |
| `fileless_processes` | `SELECT pid, name, path, cmdline, parent, uid FROM processes WHERE on_disk = 0;` | 300s | differential | ANY row (added or steady-state — re-check every interval regardless of `action`) → CRITICAL: a running process whose backing binary is gone from disk (deleted-after-exec / memfd execution). |
| `unexpected_egress` | `SELECT pid, remote_address, remote_port, state FROM process_open_sockets WHERE state = 'ESTABLISHED' AND remote_address NOT IN ('127.0.0.1','::1');` | 300s | differential | `added` row whose `remote_port` is outside an egress allowlist (443/80/22 + operator CIDR list) → possible C2/exfil alert. |

### Pack 2 — `gludd_sdlc_host_health.json`

| Query name | SQL | Interval | Mode | Decision it drives |
|---|---|---|---|---|
| `disk_root_pct` | `SELECT path, blocks, blocks_available, blocks_size, ROUND((1.0 - (blocks_available * 1.0 / blocks)) * 100, 1) AS pct_used FROM mounts WHERE path = '/';` | 300s | **snapshot** | `pct_used > 90` → warn (recommend `make clean-worktree-venvs`-style cleanup, per `gludd-disk-discipline` operating precedent); `> 97` → critical, throttle new dispatches. |
| `memory_pct` | `SELECT memory_total, memory_available, ROUND(((memory_total - memory_available) * 100.0 / memory_total), 1) AS pct_used FROM memory_info;` | 60s | **snapshot** | `pct_used > 90` → warn; sustained (3 consecutive polls) → throttle concurrent-agent floor. |
| `systemd_units_down` (`platform: linux`) | `SELECT name, load_state, active_state, sub_state FROM systemd_units WHERE name LIKE '%.service' AND load_state = 'loaded' AND active_state != 'active';` | 3600s | **snapshot** | Any row whose `name` is in `osquery_expected_services` (e.g. `sshd.service`, `docker.service`) → service-down alert. |
| `launchd_disabled_macos` (`platform: darwin`) | `SELECT name, program, path, disabled FROM launchd WHERE disabled = 0;` | 3600s | **snapshot** | Expected-service equivalent for macOS launchd. |
| `critical_binary_hash` | `SELECT path, sha256 FROM hash WHERE path IN ('/usr/sbin/sshd','/usr/bin/sudo','{{ gludd_binary_path }}');` | 21600s (6h) | **snapshot** | `sha256` differs from the value recorded in `osquery_binary_baseline.json` on a prior run → CRITICAL tamper alert; first run writes the baseline instead of alerting. |
| `os_and_packages` | `SELECT name, version, platform, arch FROM os_version;` | 3600s | **snapshot** | Version drift vs. the last recorded snapshot → informational note in the health artifact (no alert — context for a human/medic diagnosing an environment-drift CI failure, dovetails with `ci_medic_infra_red`'s taxonomy in `CI_PIPELINE_MEDIC_ROLE.md`). |
| `gludd_self_processes` | `SELECT pid, name, cmdline, resident_size FROM processes WHERE cmdline LIKE '%general_ludd%' OR cmdline LIKE '%gludd%';` | 300s | **snapshot** | Any `pid` absent from `ansible_facts.gludd_proc_monitor.processes` → unmanaged/orphan gludd process → warn. |

Both packs are copied to the operator-facing location as
`collections/ansible_collections/general_ludd/agent/roles/osquery_pack_deploy/files/packs/gludd_security.json`
and `.../gludd_sdlc_host_health.json` — the role's default `pack_names` var
lists both, so `include_role: osquery_pack_deploy` with no overrides deploys
the full curated set turnkey.

---

## 5. Results-log ingestion — path auto-detect, parsing, checkpointing

**Path auto-detect** (`gludd_osquery_results.py`, called with
`logger_path=""`): search order —
1. The path last written by `osquery_pack_deploy`'s flagfile (read back from
   `<config_dir>/osquery.conf`'s `--logger_path=` line, if that file exists).
2. Platform default: `/var/log/osquery/` (Linux/macOS package default) —
   check for `osqueryd.results.log` there.
3. Fallback, unprivileged dev-box path: `~/.local/share/general-ludd/osquery/log/`
   (created by `osquery_pack_deploy` when `config_dir` isn't root-writable —
   see §7's unprivileged-mode branch).

If none of the three has a results or snapshots log, the module returns
`{"rows_by_query": {}, "log_paths": {"results": null, "snapshots": null}}`
rather than failing — a decision role consuming this must treat "no pack
deployed yet" as "no findings," not as an error (fail-soft here, matching
`_osquery_facet`'s posture, §1.3).

**Line formats** (osquery's own filesystem logger plugin, unmodified —
nothing here invents a new log format):
- `osqueryd.results.log` (differential queries): one JSON object per line,
  `{"name": "<pack>/<query>", "hostIdentifier": "...", "calendarTime": "...",
  "unixTime": 1751234000, "columns": {...}, "action": "added"|"removed"}`.
- `osqueryd.snapshots.log` (snapshot queries): one JSON object per **query
  execution** (not per row), `{"name": "<pack>/<query>", "unixTime": ...,
  "snapshot": [{...}, {...}, ...]}` — the module flattens `snapshot` into
  one fact-row per array element, injecting the parent `unixTime` onto each.

**Checkpointing**: a small JSON state file (`checkpoint_path`, default
colocated with the logs) records `{"results_log_offset": N,
"snapshots_log_offset": M}` — **byte offsets**, not line counts (osquery
never rewrites earlier bytes, only appends, so a byte offset is a safe resume
point even if a line was only partially flushed at the last read — the
module seeks to the last **complete newline** at or before the recorded
offset, never trusting a possibly-truncated final line). `max_lines` bounds
how many NEW lines a single invocation parses (default 5000) so a large
backlog (e.g. after the ingest role wasn't run for days) can't stall a play
— `truncated: true` in the return signals more remain, and re-running the
role (it's naturally idempotent/incremental) drains the rest on the next
invocation.

**Log rotation**: osquery's own `--logger_max_line_size`/external logrotate
can rotate the file out from under a byte-offset checkpoint. The module
detects this by comparing the file's current size against the checkpoint
offset — if `current_size < checkpoint_offset`, the file was rotated/
truncated; the module resets to offset 0 for that file and logs a warning
(never crashes, never silently loses the reset detection).

---

## 6. Bundling / binary auto-detect

Extends `BinaryBootstrapper` per the gap identified in §1.4:

1. **Add `osqueryd` as a second manifest name.** `KNOWN_VERSIONS["osqueryd"]
   = OSQUERY_VERSION` (same pin as `"osquery"` — literally the same upstream
   tarball, just extracting a different member) at `bootstrap.py:82`'s
   sibling line; `_TARBALL_BINARIES["osqueryd"] = "osqueryd"` at
   `bootstrap.py:353-356`'s sibling line. `get_download_url` (`:223-249`)
   gets a new `if name == "osqueryd": return self._osquery_download_url(...)`
   branch identical to the existing `"osquery"` branch (`:227-228`) — same
   URL, because it's the same asset; only `_TARBALL_BINARIES` changes which
   member gets extracted after download. **No change needed to `download()`
   itself** (`:371-427`) — it is already generic over `name`.
2. **Checksum pin**: `KNOWN_SHA256["osqueryd"]` must equal
   `KNOWN_SHA256["osquery"]` (both verify the identical downloaded tarball
   bytes, `_verify_digest` runs before extraction, `:117-140` — extraction
   member choice is irrelevant to the pin). Document this explicitly so an
   operator setting `GLUDD_BINARY_SHA256` doesn't reasonably (but wrongly)
   assume `osqueryd` needs a separate pin value.
3. **Fix the linux/arm64 drop** (`_osquery_download_url:270-271`) — per
   `docs/design/BINARY_BUNDLING.md:207-208`'s already-verified finding,
   osquery 5.10.2 publishes a linux arm64 tarball; the current `if osq_arch
   != "x86_64": return None` incorrectly refuses it. Fix in the same PR as
   (1)-(2) since both touch the identical function.
4. **`osquery_bootstrap` role auto-detect order** (§3.1) is PATH → bundled →
   filestore → bootstrap-download-if-allowed, matching `BinaryPathResolver`'s
   target design in `BINARY_BUNDLING.md §2` — but note (per that doc's
   central finding) `BinaryPathResolver` itself is NOT yet wired
   bundle-first; the new `gludd_osquery_bootstrap` module calls
   `BinaryBootstrapper` methods directly (same as `gludd_osquery.py`
   already does, `:181-186`), sidestepping that unrelated, separately-tracked
   gap rather than depending on its fix landing first.
5. **`make dist`/release packaging**: once (1)-(3) land, `osqueryd` is a
   `KNOWN_VERSIONS` entry like every other bundled binary and is
   automatically covered by `BINARY_BUNDLING.md §4`'s "extend
   `bundle-binaries`/`gludd.spec` datas" fix — no separate packaging work is
   needed for this design specifically, it rides the existing binary-bundling
   remediation once that lands.

---

## 7. Security / permission handling

**osquery frequently wants root — this design does not assume it has root,
and is explicit about what degrades without it.**

- **SELECT-only enforcement is the primary blast-radius control**, already
  shipped (§1.1) and unchanged by anything here: even a compromised/buggy
  pack or ad-hoc call cannot mutate the host through osquery's own SQL
  surface (`validate_select_only` rejects every mutating keyword before the
  binary ever runs). Root only expands *read* visibility for osquery tables —
  it does not create a write path, because none exists.
- **Unprivileged degrade, not failure**: most osquery tables silently return
  fewer/zero rows without root rather than erroring (confirmed pattern used
  by `_osquery_facet`'s own probe philosophy, §1.3). `osquery_bootstrap`'s
  `privileged`/`privilege_probe` facts (§3.1) make this VISIBLE rather than
  letting a decision role assume a clean 0-row `suid_bin` result means "no
  setuid binaries" when it might mean "couldn't see them." Every decision
  role's artifact (§3.5, §3.6) must echo `privileged: false` prominently when
  applicable so a human doesn't over-trust an unprivileged clean bill of
  health.
- **No blanket "run gludd as root."** Two supported privilege postures,
  operator-selected via `osquery_pack_deploy`'s `manage_process`/`config_dir`
  and a new `osquery_privileged: bool` var (default `false`):
  1. **Unprivileged (default)**: `osqueryd` runs as the same user as the
     gludd daemon, `config_dir` falls back to a user-writable path
     (`~/.local/share/general-ludd/osquery/`), and `osquery_bootstrap`'s
     privilege probe reports the degrade. Fine for the security pack's
     network/process-visibility queries (most are readable unprivileged on
     Linux/macOS); `kernel_modules`/`kernel_extensions`/some `suid_bin`
     visibility is reduced.
  2. **Privileged (opt-in)**: operator grants the specific capability below;
     `osqueryd` runs with elevated access via the **narrowest available
     mechanism per platform** — Linux: `setcap cap_dac_read_search,
     cap_sys_ptrace+ep` on the `osqueryd` binary (NOT full root — osquery's
     own packaging uses this for most of its "needs root" tables); macOS/
     Windows: document that full admin/root IS required for kernel-extension
     and some process-internals visibility (no capability-style narrowing
     exists on those platforms) — the role never silently escalates; it
     `fail_json`s with a clear message if `osquery_privileged: true` is set
     but the invoking user cannot actually apply the elevation (no passwordless
     `sudo`/`setcap` grant configured), rather than falling back to
     unprivileged and claiming success.
- **Capability gate** (mirrors `security/permissions.py`'s `Capability`
  model, same shape `PIPELINE_INTERACTION_ROLES.md §1.5/§5` uses for
  `pipeline:<provider>`): new resource `"system:osquery"` with
  `actions=["adhoc_query","deploy_pack","manage_daemon"]` and
  `constraints={"allow_privileged": bool}`. `osquery_pack_deploy` and
  `osquery_bootstrap` refuse `manage_process`/privileged elevation unless the
  calling role's `PermissionSpec` grants `manage_daemon` with
  `allow_privileged: true` — **fail closed**, same posture as every
  mutating-verb gate in `PIPELINE_INTERACTION_ROLES.md §5`. Ad-hoc
  `adhoc_query` needs only the base `"adhoc_query"` action (unprivileged,
  read-only, SELECT-only — low risk, broadly grantable, e.g. to the default
  `subagent` spec).
- **No secrets involved directly** — osquery itself needs no API token/PSK
  (it's a local binary). The only `no_log: true` fields in this design's
  modules are the pass-through `psk` params kept for daemon-API calls
  (process start/stop dispatch, mirrors every other `gludd_*` module's
  convention) — never a query result or binary path, which are not secrets.
- **Never let a pack query leak secrets it happens to read.** osquery CAN
  read environment variables (`process_envs` table) and file contents
  (`file`/`hash` tables) which may include secret material on a
  misconfigured host. None of the curated packs (§4) query `process_envs` or
  read arbitrary file *contents* (only path/hash/mode metadata) specifically
  to avoid this; a future custom pack that does must route its results
  through the same `no_log`/redaction discipline as everything else — flag
  this explicitly in `osquery_pack_deploy`'s role README so an operator
  adding a custom pack doesn't accidentally log a `.env` file's contents into
  a plaintext results log.

---

## 8. Config schema

New `UserConfig` sibling field (parallels `pipeline_drive` in
`PIPELINE_INTERACTION_ROLES.md §6` and the existing `connectors: list[dict]`
at `config/user_config.py:174`):

```yaml
osquery_monitor:
  enabled: true
  privileged: false                 # see §7 — opt-in, fails closed if elevation unavailable
  config_dir: ""                    # "" = auto (root-writable /etc/osquery, else ~/.local/share/general-ludd/osquery)
  manage_daemon: true                # false = config-only, operator's own systemd/launchd unit runs osqueryd
  packs: [gludd_security, gludd_sdlc_host_health]   # default = both curated packs
  security:
    allowed_ports: ["22", "80", "443", "8000"]
    allowed_kernel_modules: []       # operator allowlist for kernel_modules/extensions
    allowed_cron_commands: []
    egress_allowed_ports: ["443", "80", "22"]
    egress_allowed_cidrs: ["10.0.0.0/8"]
  host_health:
    disk_warn_pct: 90
    disk_critical_pct: 97
    memory_warn_pct: 90
    expected_services: ["sshd.service", "docker.service"]
    critical_binary_paths: ["/usr/sbin/sshd", "/usr/bin/sudo"]
  alert_recipient: "primary"          # gludd_message recipient for non-clean verdicts
```

Wiring path mirrors `connectors`/`pipeline_drive`: `config/general-ludd.yml`
→ `UserConfig.osquery_monitor` → a `wire_osquery_monitor()` builder called
from the same daemon-startup site as `wire_observability()`
(`routers/observe.py:249-286`) — this builder does NOT construct a
`ConnectorRegistry` entry (osquery's `OsquerySource` connector, §1.2, is a
separate, already-wireable mechanism via the plain `connectors:` list); it
only resolves the `osquery_monitor` block into role `vars` defaults consumed
by `playbooks/osquery_monitor.yml`, so a fully-config-driven operator never
has to hand-edit playbook variables.

---

## 9. Test plan

**Module unit tests** (mirror `tests/unit/test_gludd_osquery_module.py`'s
`_FakeAnsibleModule` + `importlib`-load-the-real-file pattern — no real
osquery binary in any test):

- `gludd_osquery_bootstrap`: resolution-order precedence (explicit → bundled
  → filestore → PATH → download-if-allowed) each mocked independently;
  download-refused-when-unpinned surfaces the exact `BinaryBootstrapper`
  fail-closed message; `ensure_daemon=false` skips `osqueryd` resolution
  entirely; privilege probe correctly reports `privileged: false` when the
  mocked `kernel_modules` query returns 0 rows and `true` when it returns ≥1.
- `gludd_osquery_pack`: content-hash idempotency (`changed: false` on an
  identical re-run); `config_check` failure surfaces osqueryd's stderr and
  never restarts the daemon; `manage_process: false` never calls the daemon
  dispatch endpoint (assert the mocked `GluddClient.post` was never invoked).
- `gludd_osquery_results`: differential-log parsing (`action` field
  preserved per row); snapshot-log flattening (N rows out of one
  `{"snapshot":[...]}` line, each carrying the parent `unixTime`); checkpoint
  byte-offset resume (write partial content, checkpoint mid-line, assert the
  next read starts from the last complete newline, not mid-object); rotation
  detection (`current_size < checkpoint_offset` resets to 0, logs a warning,
  does not crash); `max_lines` truncation sets `truncated: true` and a
  second invocation drains the remainder; missing log files return the
  fail-soft empty-facts shape rather than erroring.
- SELECT-only / injection coverage for any NEW query surface this design
  adds is already fully covered transitively (every role calls
  `gludd_osquery`'s existing validated path, or emits static SQL baked into
  the pack JSON — no role constructs a query from unsanitized input) — a
  regression test asserts every pack-JSON query string in `files/packs/*.json`
  passes `gludd_osquery.validate_select_only` (parse the JSON, run the
  existing validator over each `query` value) so a future pack edit can never
  accidentally add a mutating query into a file that ships as "trusted,
  pre-validated."

**Role/playbook tests** (mirror the existing Ansible-role test precedents —
`tests/unit/test_gludd_git_module.py`-style `_FakeAnsibleModule` for module
logic, plus a syntax-check level test for the roles themselves,
`make ansible-syntax`):

- `osquery_security_watch`/`osquery_host_health_watch`: feed a canned
  `ansible_facts.gludd_osquery_results` fixture (via `set_fact` in a test
  play, no real ingestion) covering each decision branch (rogue port
  present/absent, new suid binary present/absent, disk pct above/below both
  thresholds) and assert the resulting `_osw_verdict`/`_ohh_verdict` and that
  `gludd_message`/`gludd_human_todo` fire only on the expected branches —
  mirrors `report_status`'s three-tier health-classification test shape.
- Registry/allowlist test: a `PermissionSpec` lacking `manage_daemon` on
  `system:osquery` is refused BEFORE `osquery_pack_deploy` ever calls
  `POST /api/dispatch` (assert the mocked client was never invoked) — same
  fail-closed shape as `PIPELINE_INTERACTION_ROLES.md §7`'s capability-gate
  test.
- Bundling regression: extend `BINARY_BUNDLING.md §7`'s planned
  manifest-completeness test to also assert `osqueryd` has a `KNOWN_VERSIONS`/
  `_TARBALL_BINARIES` entry once §6 lands (a new shell-out to `osqueryd`
  without a manifest entry fails that test, same as any other binary).
- `make ansible-syntax` and `make molecule-test` (existing targets) run
  against all six new role directories once they exist, same gate every
  other role goes through.

---

## 10. Registration checklist (implementer)

1. Land the `osqueryd` manifest extension + linux/arm64 fix in
   `bootstrap.py` (§6, items 1-3) — this is the one item every role below
   depends on for the privileged/managed-daemon path (unprivileged
   PATH-installed `osqueryd` still works without it, just not
   bootstrap-downloadable).
2. Write `gludd_osquery_bootstrap.py`, `gludd_osquery_pack.py`,
   `gludd_osquery_results.py` under `collections/.../plugins/modules/` (§3.1,
   §3.3, §3.4) — `make gen-mcp-tools` picks them up automatically as MCP
   tools once they exist (same auto-generation `PIPELINE_INTERACTION_ROLES.md
   §1.4/§4` documents — no separate MCP registration step).
3. Add the six role directories (§2) with `tasks/main.yml`, `defaults/
   main.yml`, and a `README.md` following the `ci_pipeline_verify`/
   `report_status` convention (§3.2-§3.6's sketches are the tasks/main.yml
   starting point).
4. Drop the two curated pack JSON files (§4) under
   `roles/osquery_pack_deploy/files/packs/`.
5. Add `playbooks/osquery_monitor.yml` composing the six roles (mirrors
   `playbooks/system_report.yml`'s `include_role` chain).
6. Add the `osquery_monitor` config block + `wire_osquery_monitor()` builder
   (§8), and the `system:osquery` capability resource (§7) to
   `security/permissions.py`'s resource-constraint table alongside
   `secret:openbao`/`file:`/`net:`/`agent:` (`permissions.py:32-37`).
7. Tests per §9, including the pack-JSON-passes-`validate_select_only`
   regression test — run it before anything else lands, since every other
   test assumes the packs are trusted-safe SQL.
