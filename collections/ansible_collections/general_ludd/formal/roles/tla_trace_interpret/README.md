# tla_trace_interpret

Parse a TLC counterexample trace into structured steps + narrative. **Pure Ansible/Jinja2 — no Java required.**

## Input

Provide either `tlc_output` (string) or `trace_path` (file path) containing TLC output with the violation trace.

## TLC Trace Format Parsed

```
Error: Invariant NeverNegative is violated.
Error: The behavior up to this point is:
State 1: <Initial predicate>
/\ n = 0
State 2: <Next>
/\ n = -1
```

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `tlc_output` | `""` | TLC output string to parse |
| `trace_path` | `""` | Path to file containing TLC output |
| `artifact_dir` | `"/tmp/gludd-tla-trace"` | Output directory |
| `handoff_recipient` | `""` | gludd_message recipient |

## Artifacts

- `tla_trace.json` — `{invariant, step_count, steps[{state_n, label, vars}], narrative}`
- `tla_trace.md` — Human-readable counterexample report
