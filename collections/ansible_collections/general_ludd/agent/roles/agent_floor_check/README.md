# agent_floor_check

Read-only observability role for the agent subagent floor — the ansible-side
mirror of the opencode [`enforce-floor.ts`](../../../.opencode/plugin/enforce-floor.ts)
plugin.

## Description

This role REPORTS and ALERTS on the agent subagent floor (default 10 concurrent
subagents, per `AGENTS.md` → "Minimum 10 Subagents at All Times"). It:

1. Gathers daemon facts via `gludd_facts` for system context.
2. Reads `/tmp/gludd-block-counter.json` (streak state written by the plugin).
3. Reads `/tmp/gludd-plugin-alive.json` (enforce-floor `last_seen` timestamp).
4. Probes `scripts/agent_liveness.py --count` for the ground-truth live count,
   falling back to a block-counter-derived estimate if the probe is missing.
5. Classifies status:
   - **healthy** — `live_count >= agent_floor`
   - **degraded** — `floor/2 <= live_count < floor` and `block_counter <= 4`
   - **critical** — `live_count < floor/2` OR `block_counter > 4`
6. Emits a `gludd_message` (`priority: high`, `topic: agent_floor`) on any
   non-healthy status (when `handoff_recipient` is set).
7. Writes `floor_check.json` artifact with the full state snapshot.
8. Optionally fails the play (`fail_on_breach: true`) on breach; default is
   warn-only.

### ⚠️ This role CANNOT enforce

The actual floor enforcement — blocking non-dispatch tool calls when the floor
is breached — happens in the opencode plugin layer
(`.opencode/plugin/enforce-floor.ts`). Ansible roles run as play tasks, not as
tool-call interceptors, so they cannot block agent tool calls. This role is the
**observability + alerting** complement to that enforcement: it surfaces the
same signal in CI / scheduled checks / incident reports.

## Variables

| Variable | Default | Description |
|---|---|---|
| `agent_floor` | `10` | Minimum concurrent subagents (mirrors `CLAUDE_AGENT_FLOOR`) |
| `block_counter_path` | `/tmp/gludd-block-counter.json` | Plugin streak-counter state file |
| `plugin_alive_path` | `/tmp/gludd-plugin-alive.json` | Plugin `last_seen` heartbeat file |
| `liveness_probe_path` | `scripts/agent_liveness.py` | Ground-truth live-agent probe |
| `fail_on_breach` | `false` | Fail the play on breach; `false` = warn-only |
| `emit_alert_message` | `true` | Send `gludd_message` priority=high on breach |
| `artifact_dir` | `/tmp/gludd-floor-check-artifacts` | Artifact output directory |
| `daemon_url` | `http://localhost:8000` | Daemon base URL (for `gludd_facts` / `gludd_message`) |
| `psk` | `""` | Pre-shared key for daemon auth |
| `repo_path` | `"."` | Repo root for `chdir` (probe lives under `scripts/`) |
| `handoff_recipient` | `""` | `gludd_message` recipient (empty = no message sent) |

## Artifacts

- `<artifact_dir>/floor_check.json` — structured snapshot with `status`,
  `live_count`, `floor`, `block_counter`, `plugin_last_seen`,
  `breach_duration_sec`, plus daemon context.

## Example

Warn-only health check (default — does not fail the play):

```yaml
- hosts: localhost
  roles:
    - role: general_ludd.agent.agent_floor_check
      vars:
        daemon_url: "http://localhost:8000"
        handoff_recipient: operator
        agent_floor: 10
```

CI gate mode (fails the play when the floor is breached):

```yaml
- hosts: localhost
  roles:
    - role: general_ludd.agent.agent_floor_check
      vars:
        fail_on_breach: true
        emit_alert_message: true
        handoff_recipient: operator
```
