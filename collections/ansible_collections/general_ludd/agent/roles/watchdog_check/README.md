# watchdog_check

Wraps the `agent_watchdog.py` health check functionality as an Ansible role.

## Description

Reads daemon state via `gludd_facts` and checks local watchdog state files to
determine overall system health. Covers five check categories:

| Check | Source | What it detects |
|---|---|---|
| Stop count | `/tmp/gludd-watchdog-stop-count.json` | Agent stop/stall escalation |
| Gate status | `.gate-status` | Red gate, stale gate (>1h) |
| CI state | `make ci-verdict BRANCH=master` | CI pending >30min, CI red |
| Unpushed commits | `git log @{u}..HEAD` (via shell) | Unpushed work |
| Pending work | `TASKS.md`, `config/ratchet.yml` | Unchecked items, ratchet entries |

**Fail conditions (play stops):**
- `stop_count >= 3` — agent repeatedly stalled/stopped; escalation
- CI pending > 30min without progress — CI may be wedged
- Gate red for > 1 hour — broken gate not being fixed

**Pass when:** all checks green (stop_count=0, gate green, CI green, no unpushed alert, no pending work).

**SAFE-BY-DEFAULT:** `enable_model_call: false`, `enable_git_push: false`, `enable_gate_run: false`. This role only READS state and REPORTS — it never mutates the repo or makes external calls.

## Variables

| Variable | Default | Description |
|---|---|---|
| `daemon_url` | `http://localhost:8000` | Daemon base URL |
| `psk` | `""` | Pre-shared key for daemon auth |
| `artifact_dir` | `/tmp/gludd-watchdog-check` | Artifact output directory |
| `stop_count_escalate` | `3` | Stop count threshold for escalation |
| `ci_pending_stall_minutes` | `30` | Minutes before CI pending is considered stalled |
| `gate_red_max_hours` | `1` | Hours before red gate is considered critical |
| `enable_model_call` | `false` | Always false — no model calls |
| `enable_git_push` | `false` | Always false — no mutations |
| `handoff_recipient` | `""` | gludd_message recipient for alerts |

## Artifacts

- `<artifact_dir>/watchdog_check.json` — structured health report
- `<artifact_dir>/watchdog_check.md` — human-readable health report

## Alerts (gludd_message)

When `handoff_recipient` is set and a critical condition is detected, sends
a `gludd_message` with `priority: high` and topic `watchdog_alert` containing
the failing checks and their details.
