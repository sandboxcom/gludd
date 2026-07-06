# guardrail_pattern

Guardrail coverage audit role for the `general_ludd.agent` collection.

Ports the `.opencode/skills/guardrail-pattern/SKILL.md` capability into a gludd
ansible role.

## Description

Every agent restriction MUST exist at all three layers or it does not stick.
Given a guardrail name, this role audits whether all three enforcement layers
are present:

1. **Config permission** (`opencode.json`) — the hard gate
2. **Runtime hook** (`.opencode/plugin/*.ts`) — contextual feedback
3. **Agent prompt** (`AGENTS.md`) — proactive guidance

Reports which layers are present/missing and optionally drafts the missing
content via `gludd_model_call`.

## Why this role exists

The `enforcement_gate` role *enforces* an existing gate (gate check + push
guard). This role helps **create and verify** guardrails by codifying the
three-layer pattern. It is the meta-guardrail — a guardrail about guardrails.

## Variables

| Variable | Default | Description |
|---|---|---|
| `guardrail_name` | `""` | Guardrail name to audit (grep target) |
| `repo_path` | `.` | Repository root to scan |
| `fail_when_incomplete` | `false` | Fail the play when fewer than 3 layers present |
| `artifact_dir` | `/tmp/gludd-guardrail-pattern` | Artifact output path |
| `daemon_url` | `http://localhost:8000` | Daemon URL |
| `psk` | `""` | Pre-shared key (no_log) |
| `enable_model_call` | `false` | Call model to draft missing layer content |
| `model_profile` | `""` | Model profile hint (empty = daemon default) |

## Artifacts

- `<artifact_dir>/guardrail_pattern.json` — layer coverage, verdict, draft
- `<artifact_dir>/guardrail_pattern.md` — human-readable coverage table

## Example

```yaml
- hosts: localhost
  roles:
    - role: general_ludd.agent.guardrail_pattern
      vars:
        guardrail_name: "bash-make-only"
        fail_when_incomplete: true
```

## The Three-Layer Pattern

A single-layer restriction is insufficient. Layer 1 without layer 2 produces
silent denials the agent cannot learn from. Layer 3 without layer 1 is a
suggestion, not a rule. All three must reference each other.
