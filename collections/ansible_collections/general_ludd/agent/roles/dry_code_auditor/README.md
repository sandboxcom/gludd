# dry_code_auditor

Detects code duplication (DRY violations). Uses jscpd for clone detection, gated behind `enable_jscpd:false`. When disabled, `jscpd_output_override` provides canned JSON. Reports clone pairs, extraction suggestions, and duplication percentage.

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `scan_path` | `"."` | Path to scan for duplicates |
| `min_tokens` | `50` | Minimum token count for clone pair |
| `enable_jscpd` | `false` | Run real jscpd (requires jscpd in PATH) |
| `jscpd_output_override` | `""` | Canned jscpd JSON output (for testing) |
| `artifact_dir` | `/tmp/gludd-dry-code-auditor` | Output directory |
| `daemon_url` | `http://localhost:8000` | Daemon URL |
| `psk` | `""` | Pre-shared key |

## Artifact

`dry_code_auditor.json`:
```json
{
  "role": "dry_code_auditor",
  "status": "completed",
  "duplicates": [{"firstFile": {...}, "secondFile": {...}}],
  "extraction_suggestions": [...],
  "duplication_pct": 12.5,
  "verdict": "warn"
}
```

## Verdict

- `pass` — duplication < 5%
- `warn` — 5% ≤ duplication < 15%
- `fail` — duplication ≥ 15%
