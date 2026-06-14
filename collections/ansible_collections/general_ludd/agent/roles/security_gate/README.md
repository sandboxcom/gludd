# security_gate

Composing fail-closed security gate for the `general_ludd.agent` collection.

## Description

Ingests per-check result JSON files from `results_dir` and applies a
**fail-closed gate**:
- ALL `required_checks` must have a `<check>.json` file present.
- No finding may have severity >= `block_on_severity`.
- No check may have `verdict: fail` or `gate_passed: false`.

If any condition fails, `gate_passed: false` and `next_action: BLOCK`. Emits
`gludd_message` with `priority: high` to `handoff_recipient` on block.
Mirrors `gate_triage` decision logic. **REPORT-ONLY.**

## Variables

| Variable | Default | Description |
|---|---|---|
| `results_dir` | `/tmp/gludd-security-results` | Dir with per-check JSON files |
| `required_checks` | `[secret_scan, security_review, sbom_generate, supply_chain_verify]` | Checks that must pass |
| `block_on_severity` | `high` | Block if any finding >= this severity |
| `handoff_recipient` | `""` | gludd_message recipient on block |
| `enable_git_push` | `false` | Always false — report-only |

## Artifacts

- `<artifact_dir>/security_gate.json` — per-check pass/fail, blocking_findings, gate_passed, next_action
- `<artifact_dir>/security_gate.md` — human-readable gate report
