# backlog_groom

Prioritizes/estimates/splits backlog todos from `gludd_facts.todos`. Composes `report_status` data — does not duplicate health-classification logic.

## What it computes

- **ranked[]**: backlog items with estimated_points (1-5pt Fibonacci heuristic)
- **split_candidates[]**: items with `estimated_points > max_split_points`
- **under_specified[]**: items with very short titles (< 15 chars) indicating missing description
- **actions[]**: recommended actions (split, clarify, prune, none)

## Variables

| Variable | Default | Description |
|---|---|---|
| `max_split_points` | `8` | Items above this are split candidates |
| `write_back` | `false` | Write back to gludd_db |
| `artifact_dir` | `/tmp/gludd-backlog-groom` | Output directory |
| `daemon_url` | `http://localhost:8000` | Daemon base URL |
| `psk` | `""` | Pre-shared key |

## Artifacts

- `backlog_groom.json` — ranked[], split_candidates[], under_specified[], actions[]
- `backlog_groom.md` — markdown grooming report
