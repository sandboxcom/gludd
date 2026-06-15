# tla_parse

Run SANY (TLA+ syntax/semantic checker) against a `.tla` spec.

## Graceful Degradation

- `enable_tla: false` (default) → use `parse_output_override`; `status=skipped`
- Java or jar absent → `status=skipped` (non-fatal)
- Errors in SANY output → `parsed=false`, `errors[]` populated

## SANY Output Classification

- Success: stdout contains `"Parsing completed. No errors."`
- Error: stdout contains `"***"` (SANY error marker)

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `spec_path` | `""` | Path to .tla spec |
| `tla_tools_jar` | `"/opt/tla/tla2tools.jar"` | Path to tla2tools.jar |
| `enable_tla` | `false` | Run real SANY (requires Java + jar) |
| `parse_output_override` | `"...No errors."` | Canned output for testing |
| `artifact_dir` | `"/tmp/gludd-tla-parse"` | Output directory |

## Artifact: `tla_parse.json`

```json
{
  "role": "tla_parse",
  "status": "skipped|ran",
  "parsed": true,
  "errors": []
}
```
