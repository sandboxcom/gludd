# ui_ux_analyst

Heuristic TUI/CLI/API ergonomics analysis. **Non-authoritative** — findings are pattern-based and require human judgment. Checks: missing help text, inconsistent verb usage, long flags, missing quit/help key bindings in TUI. `enable_help_capture:false` by default; use `cli_help_override` for testing.

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `tui_source` | `""` | TUI source path to scan for key bindings |
| `cli_help_override` | `""` | Canned --help output (for testing) |
| `api_spec_path` | `""` | API spec path (OpenAPI/JSON) |
| `enable_help_capture` | `false` | Enable real `--help` capture |
| `artifact_dir` | `/tmp/gludd-ui-ux-analyst` | Output directory |
| `daemon_url` | `http://localhost:8000` | Daemon URL |
| `psk` | `""` | Pre-shared key |

## Artifact

`ui_ux_analyst.json`:
```json
{
  "role": "ui_ux_analyst",
  "status": "completed",
  "note": "heuristic, non-authoritative",
  "heuristics": [{"area": "cli", "issue": "...", "severity": "medium", "rule": "UX001"}],
  "score": 90,
  "verdict": "good"
}
```

## Verdict

- `good` — score ≥ 80
- `needs_work` — score 50–79
- `poor` — score < 50
