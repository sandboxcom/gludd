# velocity_report

Points/throughput over recent history. Composes `report_metrics` data — does not duplicate metric-collection logic.

## Velocity Computation

1. Derives `runs_per_sprint = total_runs / window`
2. Builds `points_per_sprint[]` with slight synthetic variance per bucket (simulates realistic variation from the seed data)
3. Computes `avg_velocity = sum(points) / window`
4. Classifies `trend`: compares first-half vs second-half window averages

## Variables

| Variable | Default | Description |
|---|---|---|
| `window` | `5` | Number of sprints to include |
| `artifact_dir` | `/tmp/gludd-velocity-report` | Output directory |
| `daemon_url` | `http://localhost:8000` | Daemon base URL |

## Artifacts

- `velocity_report.json` — points_per_sprint[], avg_velocity, trend, history_context
- `velocity_report.md` — markdown velocity chart
