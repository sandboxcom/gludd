# process_audit

Audit codified processes for over-fitting — checking whether enforcement machinery, pattern lists, and guardrails are creating more overhead than value.

## FQCN

`general_ludd.agent.process_audit`

## What it checks

1. **Daemon state** — connects to the gludd daemon via `gludd_facts`
2. **Lint health** — runs `make lint`, counts errors (non-zero = process drift)
3. **Process overhead test** — runs `make test-specific TESTFILE='tests/unit/test_process_overhead.py'`
4. **State file health** — checks `/tmp/gludd-*` state files exist and have recent timestamps
5. **Plugin footprint** — counts lines in all 8 `.opencode/plugin/*.ts` files
6. **Pattern list bloat** — counts regex entries in NO_WAIT_PATTERNS, CLAIM_PATTERNS, COMPLETION_SOUNDING, CI_RED_PATTERNS
7. **Guardrail health score** — ratio of state-based checks to pattern-list checks; < 0.5 = over-fitted

## Example

```yaml
- hosts: localhost
  roles:
    - role: general_ludd.agent.process_audit
```

## Inputs

See `defaults/main.yml` for the full variable list with defaults.

## Outputs

- `process_audit_report` — fact containing all findings
- `process_audit_overfitted` — boolean, true when health score < 0.5
- JSON artifact written to `{{ artifact_dir }}/process_audit.json`
- Markdown artifact written to `{{ artifact_dir }}/process_audit.md`
