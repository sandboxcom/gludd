# tla_check

Run TLC model checker and classify outcome. **FAIL-CLOSED** on violation/deadlock.

## Outcome Classification (by STDOUT MARKERS)

| Outcome | Marker |
|---------|--------|
| `success` | `"Model checking completed. No error has been found."` |
| `invariant_violated` | `"Error: Invariant <Name> is violated."` + trace |
| `deadlock` | `"Error: Deadlock reached."` |
| `parse_error` | `"***"` or `"Error:"` in output (no violation/deadlock) |
| `skipped` | `enable_tla=false` or Java/jar absent |

## Fail-Closed Behavior

The role calls `ansible.builtin.fail` on `invariant_violated` or `deadlock` outcomes. This is intentional: a TLC violation is a **hard block** in the formal-methods workflow.

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `spec_path` | `""` | Path to .tla spec |
| `cfg_path` | `""` | Path to .cfg |
| `tla_tools_jar` | `"/opt/tla/tla2tools.jar"` | tla2tools.jar path |
| `enable_tla` | `false` | Run real TLC |
| `tlc_output_override` | `"...No error has been found."` | Canned output for testing |
| `tla_check_timeout` | `300` | TLC timeout in seconds |
| `artifact_dir` | `"/tmp/gludd-tla-check"` | Output directory |
| `handoff_recipient` | `""` | gludd_message recipient on violation |

## Artifact: `tla_check.json`

```json
{
  "role": "tla_check",
  "outcome": "success|invariant_violated|deadlock|parse_error|skipped",
  "violated_invariant": "NeverNegative",
  "raw_trace": "..."
}
```
