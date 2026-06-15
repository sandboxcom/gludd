# tla_pluscal

Translate PlusCal algorithm to TLA+ via `pcal.trans`. Graceful skip if jar absent.

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `spec_path` | `""` | Path to .tla spec containing PlusCal |
| `tla_tools_jar` | `"/opt/tla/tla2tools.jar"` | Path to tla2tools.jar |
| `enable_tla` | `false` | Run real pcal.trans |
| `pcal_output_override` | `"Translation completed."` | Canned output for testing |
| `artifact_dir` | `"/tmp/gludd-tla-pluscal"` | Output directory |

## pcal.trans Classification

- **Success**: `"Translation completed."` or `"Translated"` in output
- **Error**: `"Unknown error"` or `"Error"` in output

## Graceful Degradation

- `enable_tla: false` (default) → use `pcal_output_override`; `status=skipped`
- Java or jar absent → `status=skipped` (non-fatal)

## Artifact: `tla_pluscal.json`

```json
{
  "role": "tla_pluscal",
  "status": "skipped|ran",
  "translated": true,
  "error": false
}
```
