# sprint_review

Completed-work demo summary from `gludd_facts.history` + `gludd_facts.traces`.

## Artifacts

- **completed[]**: high-level completion summary from history (total_runs, success_rate, done_count, trace_count)
- **highlights[]**: per-trace detail (trace_id, todo_id, cost_usd, tokens, span_count)
- **demo_notes**: per-phase aggregate (span_count, tokens, cost) from `by_phase`

## Variables

| Variable | Default | Description |
|---|---|---|
| `sprint_name` | `Sprint-1` | Sprint name |
| `artifact_dir` | `/tmp/gludd-sprint-review` | Output directory |
| `daemon_url` | `http://localhost:8000` | Daemon base URL |

## Outputs

- `sprint_review.json` — completed[], highlights[], demo_notes, history, traces
- `sprint_review.md` — markdown sprint review report
