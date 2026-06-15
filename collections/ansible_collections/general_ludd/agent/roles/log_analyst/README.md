# log_analyst

Analyses log files and daemon traces for anomalies.

## Anomaly types

| Type | Signal | Source |
|------|--------|--------|
| `error_density` | `error_line_density_exceeded` | Log files (grep ERROR/CRITICAL/FATAL) |
| `phase_failure_rate` | `phase_failure_rate_exceeded` | `gludd_facts` traces.by_phase |
| `cost_outlier` | `phase_cost_outlier` | `gludd_facts` traces.by_phase |

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `daemon_url` | `http://localhost:8000` | Daemon URL |
| `psk` | `""` | Pre-shared key |
| `artifact_dir` | `/tmp/gludd-log-analyst` | Output directory |
| `log_source` | `/var/log/gludd/*.log` | Glob for log files |
| `error_rate_threshold` | `0.1` | Fraction of error lines that triggers anomaly |
| `cost_outlier_factor` | `2.0` | Multiple of mean cost that triggers outlier |

## Artifact

`log_analyst.json`:

```json
{
  "role": "log_analyst",
  "verdict": "anomalies_detected | clean",
  "anomalies": [
    {"type": "error_density", "signal": "...", "severity": "high", "evidence": "..."}
  ],
  "anomaly_count": 1
}
```
