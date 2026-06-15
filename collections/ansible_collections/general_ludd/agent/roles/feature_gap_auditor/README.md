# feature_gap_auditor

Diffs intended features (from docs/specs) vs implemented features. Reads spec_paths for feature lines (lines starting with `- ` or `* `). Reads impl_inventory_path for implemented feature names (one per line). Computes gap = intended - implemented.

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `spec_paths` | `[]` | List of spec/doc file paths to read intended features from |
| `impl_inventory_path` | `""` | Path to file listing implemented features (one per line) |
| `enable_model_call` | `false` | Enable model analysis call |
| `model_output_override` | `""` | Canned model output (for testing) |
| `artifact_dir` | `/tmp/gludd-feature-gap-auditor` | Output directory |
| `daemon_url` | `http://localhost:8000` | Daemon URL |
| `psk` | `""` | Pre-shared key |

## Artifact

`feature_gap_auditor.json`:
```json
{
  "role": "feature_gap_auditor",
  "status": "completed",
  "intended": ["login", "logout", "dashboard"],
  "implemented": ["login", "logout"],
  "gaps": ["dashboard"],
  "coverage_pct": 66.7,
  "verdict": "warn"
}
```

## Verdict

- `pass` — no gaps
- `warn` — coverage ≥ 50%
- `fail` — coverage < 50%
