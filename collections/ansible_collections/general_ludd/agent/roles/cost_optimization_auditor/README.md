# cost_optimization_auditor

Analyzes model/token/compute costs from gludd.metrics and gludd.traces. Performs real arithmetic: cost per phase, low-score/high-cost model profiles. Emits ranked savings recommendations targeting the priciest profiles.

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `cost_threshold_usd` | `0.01` | Cost threshold for flagging |
| `expensive_phase_pct` | `0.4` | Min fraction of total cost to flag a phase as expensive |
| `artifact_dir` | `/tmp/gludd-cost-optimization-auditor` | Output directory |
| `daemon_url` | `http://localhost:8000` | Daemon URL |
| `psk` | `""` | Pre-shared key |

## Artifact

`cost_optimization_auditor.json`:
```json
{
  "role": "cost_optimization_auditor",
  "status": "completed",
  "total_cost_usd": 0.0012,
  "hotspots": [{"key": "generate", "value": {"total_cost_usd": 0.0021}}],
  "recommendations": [{"action": "...", "est_savings_usd": 0.0006, "priority": "high"}],
  "verdict": "warn"
}
```

## Verdict

- `ok` — total cost below threshold
- `warn` — total cost above threshold
- `critical` — total cost > 10× threshold
