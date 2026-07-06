# enforcement_verify

Behavioral enforcement verification — validates that enforcement mechanisms are actively producing side effects, not just statically present. Complements `process_audit` (which does static measurement) with runtime behavioral checks.

## FQCN

`general_ludd.agent.enforcement_verify`

## What it verifies

1. **Text-complete counter** (`/tmp/gludd-text-complete-counter.json`) — confirms enforce-stop plugin is alive and incrementing
2. **Block-counter** (`/tmp/gludd-block-counter.json`) — confirms enforcement is actively blocking violations
3. **Watchdog heartbeat** (`/tmp/gludd-watchdog-heartbeat.json`) — confirms agent_watchdog.py daemon is alive and recent
4. **Gate status terminal marker** (`.gate-status`) — confirms gate runs complete and produce a PASS/FAIL verdict
5. **Force-push track file** (`.gate-logs/force-push-track.json`) — confirms push guard is tracking bypasses
6. **State file survey** (`/tmp/gludd-*.json`) — confirms all enforcement state files exist and are fresh

**Pass:** all 6 mechanisms show recent activity (side effects within threshold window).
**Fail:** any mechanism is silent — no side effects within the configured window.

## Example

```yaml
- hosts: localhost
  roles:
    - role: general_ludd.agent.enforcement_verify
```

### With relaxed thresholds

```yaml
- hosts: localhost
  roles:
    - role: general_ludd.agent.enforcement_verify
      vars:
        enforcement_verify_max_age_seconds: 600
        enforcement_verify_watchdog_max_age_seconds: 120
```

### Report-only (no fail on silence)

```yaml
- hosts: localhost
  roles:
    - role: general_ludd.agent.enforcement_verify
      vars:
        enforcement_verify_fail_on_silence: false
```

## Inputs

See `defaults/main.yml` for the full variable list with defaults.

## Outputs

- `enforcement_verify_report` — fact containing all mechanism health data, verdict, and silent mechanism list
- JSON artifact written to `{{ artifact_dir }}/enforcement_verify.json`
- Markdown artifact written to `{{ artifact_dir }}/enforcement_verify.md`

## Silent mechanisms (what they mean)

| Mechanism | Silent means |
|-----------|-------------|
| `text_complete` | enforce-stop.ts plugin is not running or not incrementing its counter |
| `block_counter` | No enforcement blocks have occurred recently — plugins may be advisory-only |
| `watchdog` | agent_watchdog.py may have crashed or is not started |
| `gate_status` | No gate run has completed recently or gate status is missing |
| `push_track` | Push guard tracking file doesn't exist or is stale |
| `state_files` | No enforcement state files in /tmp/gludd-*.json or they're all stale |
