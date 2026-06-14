# gate_triage

Codifies the recurring agentic-SDLC "run gate → triage failures → decide next action" workflow as a reusable Ansible role.

## What it does

1. Gathers live daemon facts (`gludd_facts`) for system context (backlog, success rate).
2. **Optionally** runs `make gate` — ONLY when `enable_gate_run: true` AND NOT in check mode. Default is `false` (safe by default).
3. Classifies failure signals: flaky (XPASS-strict, FSEvents, timeout-under-load, xfailed) vs real (FAILED lines, typecheck errors, lint errors).
4. Determines next action: `commit_immediately` / `fix_failures` / `rerun_or_quarantine` / `investigate`.
5. Writes `gate_triage.json` + `gate_triage.md` artifacts.
6. Sends a `gludd_message` handoff to `handoff_recipient` when failures are found (optional).

## Key variables

| Variable | Default | Description |
|---|---|---|
| `enable_gate_run` | `false` | Set `true` to actually run `make gate` (40-min op) |
| `gate_output_override` | `"ALL PASSED..."` | Used when gate doesn't run; inject known gate output |
| `repo_path` | `"."` | Path where `make gate` is invoked |
| `daemon_url` | `http://localhost:8000` | Daemon for gludd_facts |
| `artifact_dir` | `/tmp/gludd-gate-triage` | Where to write artifacts |
| `handoff_recipient` | `""` | Agent/role to notify via gludd_message (empty = no send) |

## Safety model

- `enable_gate_run: false` (default) → gate never runs; uses `gate_output_override`
- `ansible_check_mode: true` → gate never runs even if `enable_gate_run: true`
- No file mutations outside `artifact_dir`
- No git push, no repo edits

## Artifact

`gate_triage.json`:
```json
{
  "role": "gate_triage",
  "status": "completed",
  "gate_passed": true,
  "gate_ran": false,
  "next_action": "commit_immediately",
  "flaky_signatures_found": 0,
  "real_failure_signals": 0,
  "system_context": { "backlog_size": 3, "success_rate": 0.92 }
}
```
