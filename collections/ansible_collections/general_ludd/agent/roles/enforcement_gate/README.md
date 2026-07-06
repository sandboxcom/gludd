# enforcement_gate

Composing fail-closed enforcement gate for pre-commit/pre-push workflows in the `general_ludd.agent` collection.

## Description

Runs the enforcement checks the daemon should execute before committing or pushing:

1. Gathers live daemon facts (`gludd_facts`) for system context.
2. Verifies gate completion and freshness via `gludd_gate_check`:
   - Gate status file must exist and be within `gate_max_age_seconds` freshness window.
   - Optionally requires individual phases to have passed (lint 0, typecheck <= baseline, collect 0 errors, tests pass).
3. Validates push readiness via `gludd_push_guard`:
   - Push rate guard (CI-pending, cooldown, cancelled-run cap).
   - Optional CI-green requirement.
4. Optionally runs model analysis on enforcement state (`enable_model_call`).
5. Emits `gludd_message` with `priority: high` to `handoff_recipient` on block.
6. **FAILS the playbook** (`fail_json`) if ANY enforcement check blocks.

**FAIL-CLOSED:** any block → `next_action: BLOCK` → playbook fails.
**REPORT-ONLY:** never mutates the repo.

## Variables

| Variable | Default | Description |
|---|---|---|
| `gate_status_path` | `.gate-status` | Path to gate status file (relative to `repo_path`) |
| `gate_max_age_seconds` | `86400` | Max age in seconds for a valid gate result |
| `require_gate_passed` | `true` | Gate must have passed (not just present + fresh) |
| `require_lint_pass` | `true` | Require lint phase to have passed |
| `require_typecheck_pass` | `true` | Require typecheck phase at or below baseline |
| `require_collect_pass` | `true` | Require collect phase to have passed |
| `require_test_pass` | `true` | Require test phase to have passed |
| `enable_push_guard` | `true` | Enforce push rate guard |
| `require_ci_green` | `false` | Require CI-green verdict before push |
| `push_target` | `sandboxcom` | Push remote target for guard checks |
| `push_cooldown_seconds` | `1800` | Minimum seconds between pushes |
| `max_cancelled_runs` | `3` | Max cancelled CI runs before blocking |
| `cancelled_lookback_hours` | `2` | Lookback window for cancelled run detection |
| `enable_model_call` | `false` | Enable model call for analysis (safe-by-default) |
| `model_profile` | `""` | Explicit model profile ID for analysis |
| `analysis_task_type` | `enforcement_gate` | Task type for adaptive routing |
| `handoff_recipient` | `""` | gludd_message recipient on block |
| `enable_git_push` | `false` | Always false — report-only |

## Artifacts

- `<artifact_dir>/enforcement_gate.json` — structured enforcement result with gate and push guard outcomes, combined block reasons, model analysis, and system context.
- `<artifact_dir>/enforcement_gate.md` — human-readable enforcement gate report.

## Usage

### As a playbook

```yaml
- hosts: localhost
  roles:
    - role: general_ludd.agent.enforcement_gate
      vars:
        repo_path: "/path/to/repo"
        handoff_recipient: "integrator"
```

### With model analysis enabled

```yaml
- hosts: localhost
  roles:
    - role: general_ludd.agent.enforcement_gate
      vars:
        enable_model_call: true
        handoff_recipient: "integrator"
```

### Strict CI-green push gate

```yaml
- hosts: localhost
  roles:
    - role: general_ludd.agent.enforcement_gate
      vars:
        require_ci_green: true
```
