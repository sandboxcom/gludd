# token_window_monitor

5-hour token-window monitor — throttles the subagent floor near the rate-limit window.

## Description

Wraps `scripts/token_window_monitor.py` which:

**SENSOR:** Sums token usage (input + output + cache_creation + cache_read) from
Claude Code session transcript JSONL files over a rolling 5-hour window with
deduplication (compaction/resume re-embeds are counted once).

**ACTUATOR:** Writes the subagent floor to `/tmp/gludd-floor-override` — the live
override the enforce-floor hooks read.

**POLICY (hysteresis):**
- `spend >= 95%` of budget → floor = 1 (drain subagents, conserve tokens)
- `spend < 90%` of budget → floor = 7 (restore full parallelism)
- `90%..95%` → hold (no change — prevents flapping)

## Modes

| Mode | Description |
|---|---|
| `once` | One evaluation cycle: sense → decide → act |
| `calibrate` | Anchor the budget to a known percentage reading |
| `breakdown` | Print per-component token breakdown (diagnostic) |
| `probe` | Discover rate-limit keys in transcript (diagnostic) |

## Variables

| Variable | Default | Description |
|---|---|---|
| `twm_mode` | `once` | Mode: once / calibrate / breakdown / probe |
| `repo_path` | `.` | Path to the gludd repo root |
| `calibrate_pct` | `90` | Calibration percentage |
| `twm_normal_floor` | `7` | Normal subagent floor |
| `artifact_dir` | `/tmp/gludd-token-window-monitor` | Artifact output directory |

## Example

```yaml
- hosts: localhost
  vars:
    twm_mode: once
  roles:
    - role: general_ludd.agent.token_window_monitor
```
