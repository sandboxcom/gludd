---
name: osquery_system_state
description: >
  Query live system state (processes, users, mounts, network, installed
  packages, system_info, and ~200 other tables) with read-only osquery SQL.
  Use when you need real facts about the host the job runs on — what is
  running, who is logged in, what is installed, how the network is configured —
  rather than guessing.
trigger_patterns:
  - "osquery"
  - "system state"
  - "what processes are running"
  - "installed packages"
  - "system info"
  - "logged in users"
  - "network interfaces"
  - "query the host"
tags: [system, facts, osquery, read-only]
category: system
tools: [gludd_osquery]
---

# osquery System-State Skill

You can inspect the live system the job is running on by issuing **read-only
SQL** through osquery (the `gludd_osquery` Ansible module, or
`osqueryi --json "<query>"`). osquery exposes the operating system as a
relational database: roughly **200 virtual tables** you can `SELECT` from.

## Hard rules

- **SELECT only.** `INSERT`, `UPDATE`, `DELETE`, `DROP`, `CREATE`, `ALTER`,
  `ATTACH`, `PRAGMA`, etc. are rejected before the query ever reaches the
  binary. Do not attempt to mutate state — osquery is for *observing*.
- **One statement per query.** No stacked statements / semicolon chains.
- Always add a `LIMIT` when a table can be large (`processes`, `file`,
  `process_open_sockets`) so output stays bounded.

## Commonly useful tables

| Table | What it tells you |
| --- | --- |
| `processes` | Running processes: `pid`, `name`, `path`, `cmdline`, `state`, `uid` |
| `users` | Local user accounts: `uid`, `username`, `description`, `directory`, `shell` |
| `logged_in_users` | Active sessions: `user`, `tty`, `host`, `time` |
| `system_info` | Host facts: `hostname`, `cpu_brand`, `cpu_logical_cores`, `physical_memory` |
| `os_version` | OS: `name`, `version`, `platform`, `arch` |
| `mounts` | Mounted filesystems: `device`, `path`, `type`, `blocks_free` |
| `interface_addresses` | Network addressing: `interface`, `address`, `mask` |
| `interface_details` | NIC details: `interface`, `mac`, `mtu` |
| `listening_ports` | Open listeners: `pid`, `port`, `protocol`, `address` |
| `process_open_sockets` | Per-process sockets: `pid`, `local_port`, `remote_address` |
| `deb_packages` / `rpm_packages` / `homebrew_packages` | Installed packages by manager |
| `apps` (macOS) | Installed applications |
| `kernel_info` | Kernel `version`, `arguments` |
| `uptime` | System uptime in days/hours/minutes |
| `crontab` | Scheduled cron jobs |

## Example queries

```sql
-- Top processes by name
SELECT pid, name, state, uid FROM processes ORDER BY name LIMIT 25;

-- Is a specific service running?
SELECT pid, name FROM processes WHERE name = 'nginx';

-- Who is logged in right now
SELECT user, tty, host, time FROM logged_in_users;

-- Host summary
SELECT hostname, cpu_brand, cpu_logical_cores, physical_memory FROM system_info;

-- What is listening on the network
SELECT pid, port, protocol, address FROM listening_ports WHERE port != 0 LIMIT 50;

-- Installed Debian packages matching a name
SELECT name, version FROM deb_packages WHERE name LIKE 'python%' LIMIT 50;

-- Free space per mount
SELECT path, type, blocks_free, blocks FROM mounts;
```

## How to run it

Ansible:

```yaml
- name: Check what is listening
  general_ludd.agent.gludd_osquery:
    query: "SELECT pid, port, protocol FROM listening_ports WHERE port != 0 LIMIT 50"
  register: ports

- name: Branch on the result
  ansible.builtin.debug:
    msg: "{{ ports.ansible_facts.gludd_osquery.count }} listeners found"
```

The result is injected as `ansible_facts.gludd_osquery` with keys `rows`
(list of dicts), `count`, and the validated `query`. A fast availability +
version probe is also surfaced at `GET /api/facts` under the `osquery` key.
