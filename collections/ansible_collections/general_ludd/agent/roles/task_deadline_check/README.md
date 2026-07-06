# task_deadline_check

Read-only audit of subagent task wall-clock deadlines. The gludd/ansible
equivalent of the opencode `.opencode/plugin/enforce-deadline.ts` plugin.

## Description

Reads the three state files written by the deadline-enforcement stack and
reports any task whose elapsed wall-clock time exceeds the configured timeout:

| File | Writer | Shape |
|---|---|---|
| `/tmp/gludd-task-deadlines.json` | `enforce-deadline.ts` plugin | `{task_id: epoch_ms}` |
| `/tmp/gludd-task-stale.json` | `enforce-deadline.ts` plugin | `[{task_id, start_ms, elapsed_ms, stale_at}]` |
| `/tmp/gludd-task-killed.json` | `scripts/task_watchdog.py` | `[{task_id, pid, elapsed_ms, reason, killed_at}]` |

A task is **breached** when:

```
elapsed_ms = now_ms - start_ms   >   task_timeout_ms
AND task_id NOT in killed list
```

**Status thresholds:**

| Status | Breached count |
|---|---|
| `healthy` | 0 |
| `degraded` | 1–2 |
| `critical` | 3+ |

## READ-ONLY — does not kill tasks

This role only **audits and reports**. Actual task killing is performed by
`scripts/task_watchdog.py` — the daemon-side killing layer that reads the same
stale file and `SIGTERM`/`SIGKILL`s matching `pytest`/`make`/`ansible-runner`
processes. This role surfaces breaches so the orchestrator agent can
re-dispatch or re-split the work.

Missing state files are handled gracefully: when the deadlines file does not
exist, the role reports `status: healthy` with note `"no active tasks"`.

## Variables

| Variable | Default | Description |
|---|---|---|
| `task_deadlines_path` | `/tmp/gludd-task-deadlines.json` | Dispatch-time state file (dict `{task_id: epoch_ms}`) |
| `task_stale_path` | `/tmp/gludd-task-stale.json` | Plugin-recorded breach list |
| `task_killed_path` | `/tmp/gludd-task-killed.json` | Watchdog kill audit log |
| `task_timeout_ms` | `300000` (5 min) | Wall-clock deadline; must match plugin `GLUDD_TASK_TIMEOUT_MS` |
| `fail_on_breach` | `false` | Fail the play when `status == critical` |
| `emit_alert_message` | `true` | Send `gludd_message` on non-healthy status |
| `artifact_dir` | `/tmp/gludd-task-deadline-check-artifacts` | Artifact output path |
| `daemon_url` | `http://localhost:8000` | Daemon base URL (for `gludd_facts` / `gludd_message`) |
| `psk` | `""` | Pre-shared key for daemon auth |
| `handoff_recipient` | `""` | `gludd_message` recipient (empty = no message sent) |

## Artifacts

- `<artifact_dir>/task_deadline_check.json` — structured report
  (`{status, total_tasks, breached_tasks, stale_count, killed_count, ...}`)
- `<artifact_dir>/task_deadline_check.md` — human-readable report with a
  breached-tasks table

## Example

```yaml
- hosts: localhost
  roles:
    - role: general_ludd.agent.task_deadline_check
      vars:
        task_timeout_ms: 300000        # 5 min — must match plugin default
        fail_on_breach: true           # fail the play on critical (3+ breaches)
        emit_alert_message: true
        handoff_recipient: orchestrator
        artifact_dir: /tmp/gludd-task-deadline-check-artifacts
```

## See also

- `.opencode/plugin/enforce-deadline.ts` — plugin source of truth
- `scripts/task_watchdog.py` — killing layer (reads same stale file)
- `scripts/task_ttl_check.py` — CLI mirror (`make task-ttl-check`)
