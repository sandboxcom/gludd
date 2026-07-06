# deletion_gate

Threshold-based **fail-closed** deletion gate. Ansible port of the opencode
`enforce-deletion-gate.ts` plugin.

Compares a file's line count before and after a mutation. When the removed
line count exceeds a threshold (percentage **OR** absolute) and no
`deletion_reason` is supplied, the role **blocks** the playbook. Writes a
`.deletion-audit.log` entry and a JSON artifact on every invocation.

## When to use

Run this role **before any large refactor or cleanup that removes code** —
dead-code deletion, file consolidation, framework migration, dependency
pruning. It is the structural answer to the recurring failure mode where an
agent (or human) silently deletes a feature, a guardrail, a test class, or a
wiring block, and the loss is only noticed sessions later.

Typical placement: at the top of a cleanup playbook, immediately after the
before/after line counts have been measured, and before any commit/push role.

## Verdict matrix

| Verdict | Condition | Behavior |
|---------|-----------|----------|
| `skip` | `baseline_lines == 0` OR `current_lines == 0` | Warn; gate does not evaluate |
| `pass` | Under both thresholds | Allow |
| `allowed-with-reason` | Over threshold AND `deletion_reason != ""` | Allow; reason logged |
| `blocked` | Over threshold AND `deletion_reason == ""` | Fail playbook when `fail_closed=true` |

## Threshold defaults

| Variable | Default | Meaning |
|----------|---------|---------|
| `threshold_pct` | `0.20` | Block when `removed / baseline > 20%` |
| `absolute_threshold` | `50` | Block when `removed > 50 lines`, regardless of pct |

A deletion is "over threshold" when **either** bound is crossed. The absolute
threshold catches wholesale deletions of small files (e.g. a 30-line guard
function deleted is 100% but only 30 lines — set `absolute_threshold` lower
to catch these).

## Escape hatch

Set `deletion_reason` to document a legitimate large deletion:

```yaml
- hosts: localhost
  vars:
    target_file: "src/general_ludd/legacy/old_pipeline.py"
    baseline_lines: 420
    current_lines: 0
    deletion_reason: "Removed legacy pipeline; superseded by EventLoop v2 (ADR-007)"
  roles:
    - role: general_ludd.agent.deletion_gate
```

With `deletion_reason` set, the verdict is `allowed-with-reason` and the role
logs the reason to `.deletion-audit.log` instead of blocking.

## Variables

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `target_file` | `""` | **yes** | The file being deleted from / shrunk |
| `baseline_lines` | `0` | yes (caller must supply) | Pre-mutation line count; `0` skips the gate |
| `current_lines` | `0` | yes (caller must supply) | Post-mutation line count; `0` skips the gate |
| `deletion_reason` | `""` | when over threshold | Operator/agent reason; empty = block |
| `threshold_pct` | `0.20` | no | Percentage-of-baseline threshold |
| `absolute_threshold` | `50` | no | Absolute removed-lines threshold |
| `audit_log_path` | `{{ playbook_dir }}/.deletion-audit.log` | no | Where the audit line is appended |
| `artifact_dir` | `/tmp/gludd-deletion-gate-artifacts` | no | Where the JSON artifact is written |
| `fail_closed` | `true` | no | When `false`, blocked = advisory debug only |

## Audit log format

One line per invocation, pipe-delimited:

```
2026-07-06T14:30:00Z | src/foo.py | baseline=200 | current=120 | removed=80 | pct=40.0 | verdict=allowed-with-reason | reason="refactor: extracted helpers"
```

## Artifact

`{{ artifact_dir }}/deletion_gate.json` contains the full verdict detail
(timestamp, line counts, percentages, threshold config, verdict, reason) for
programmatic consumption by downstream roles.

## Notes

- **Fail-closed by default.** A `blocked` verdict fails the playbook. Set
  `fail_closed=false` to make the role advisory-only during exploratory work.
- **Append-only audit log.** The log uses `shell: printf >> file` because
  `copy` cannot append atomically — see the inline comment in
  `tasks/main.yml` for the rationale.
- **Counterpart to the opencode plugin.** The TypeScript plugin
  (`.opencode/plugin/enforce-deletion-gate.ts`) protects live agent edits;
  this role protects operator-driven ansible refactors. Same semantics,
  different execution surface.
