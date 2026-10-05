# manage_processes role

Enumerate, monitor, and (opt-in) signal **gludd-managed** OS processes.

The role composes two gludd modules:

- `general_ludd.agent.gludd_process` — `action=list` enumerates managed
  processes (registers `ansible_facts.gludd_process.processes`),
  `action=signal` sends a signal (check_mode-safe), `action=status` re-checks
  liveness of a pid.
- `general_ludd.agent.gludd_proc_monitor` — gathers per-pid resource stats
  (registers `ansible_facts.gludd_proc_monitor.processes` with `cpu_percent`,
  `memory.rss`, `io`, `num_fds`, `status`, `locks`).

## Flow

1. **Gather** — `gludd_process action=list` (always).
2. **Monitor** — `gludd_proc_monitor pid={{ target_pid }}` (per-pid stats).
3. **Report** — debug cpu / mem / io / locks per process.
4. **Policy** (`manage_processes_action == "signal"`) — find pids breaching
   `cpu_percent_max` OR `memory_rss_mb_max`, send `signal_to_send`, then (if
   `escalate_to_kill`) wait `kill_grace_seconds`, re-check liveness, and
   `SIGKILL` anything still alive. Gated by `dry_run`.
5. **Reap** (`manage_processes_action == "reap"`) — no-op placeholder; the
   daemon owns reaping. The role only enumerates.

## Variables

| Variable | Default | Notes |
|---|---|---|
| `gludd_daemon_url` | `http://localhost:8000` | Daemon API base URL |
| `gludd_psk` | `""` | Pre-shared key; `no_log` in every task that passes it. Prefer `GLUDD_AUTH_PSK` env var |
| `manage_processes_action` | `monitor` | `monitor` \| `signal` \| `reap` |
| `cpu_percent_max` | `90.0` | Processes above this CPU% are breaching |
| `memory_rss_mb_max` | `2048` | Processes with RSS above this many MB are breaching |
| `signal_to_send` | `SIGTERM` | First signal sent to a breaching pid |
| `escalate_to_kill` | `true` | Send `SIGKILL` if still alive after the grace period |
| `kill_grace_seconds` | `10` | Seconds to wait before re-checking / escalating |
| `target_pid` | `0` | `0` = all managed processes; otherwise a single pid |
| `dry_run` | `true` | **SAFE default** — with dry_run on, the signal policy only logs the actions it *would* take and never signals |

## Examples

### Monitor only (read-only, the default)

```yaml
- name: Monitor gludd-managed processes
  hosts: localhost
  gather_facts: false
  tasks:
    - name: Enumerate + report cpu/mem/io/locks
      ansible.builtin.include_role:
        name: general_ludd.agent.manage_processes
      vars:
        manage_processes_action: monitor
        gludd_daemon_url: http://localhost:8000
```

### Threshold-based graceful-then-forced signal (dry_run disabled)

```yaml
- name: Reap runaway gludd processes
  hosts: localhost
  gather_facts: false
  tasks:
    - name: Graceful SIGTERM then escalate to SIGKILL on threshold breach
      ansible.builtin.include_role:
        name: general_ludd.agent.manage_processes
      vars:
        manage_processes_action: signal
        dry_run: false            # REQUIRED to actually signal
        cpu_percent_max: 85.0
        memory_rss_mb_max: 4096
        signal_to_send: SIGTERM
        escalate_to_kill: true
        kill_grace_seconds: 15
        target_pid: 0             # all managed processes
```

## Safety

- **`dry_run` defaults to `true`** — the role never signals unless the
  operator explicitly sets `dry_run: false`. Under dry_run the signal policy
  only logs the actions it would take.
- Signalling is **confined by the daemon to gludd-managed PIDs only**, enforced
  via the managed-process registry plus a PID-reuse identity guard — the role
  cannot signal arbitrary system processes even if a breaching pid is supplied.
- `gludd_psk` is marked `no_log` on every task that passes it to a module.
- The graceful → grace-period → forced-kill escalation only runs when
  `escalate_to_kill` is true and `dry_run` is false.
