# scrum_leader

Pure composer role over agile planning ceremony roles. Includes each ceremony
in sequence and consolidates sub-artifacts into a unified sprint index.

No new decision logic — this role only wires `include_role` calls and
consolidates the resulting artifacts.

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `daemon_url` | `http://localhost:8000` | Daemon URL propagated to sub-roles |
| `artifact_dir` | `/tmp/gludd-scrum-leader` | Root output dir; each ceremony gets a subdir |
| `sprint_name` | `Sprint-1` | Sprint name propagated to sub-roles |
| `ceremonies` | `[backlog_groom, sprint_plan, standup_report, retrospective]` | Which ceremonies to run |
| `write_back` | `false` | Forwarded to sub-roles (safe-by-default) |
| `enable_model_call` | `false` | Forwarded to retrospective (safe-by-default) |
| `sprint_capacity` | `0` | Forwarded to sprint_plan (0=derive from velocity) |
| `max_split_points` | `8` | Forwarded to backlog_groom |

## Artifacts

- `scrum_leader.json` — ceremonies_run[], sprint_plan_summary, retro_summary, verdict
- `scrum_leader.md` — index linking all sub-artifacts
- `<ceremony>/` — sub-directory per ceremony with its own artifacts

## SAFE-BY-DEFAULT

Never mutates the repo. All sub-roles run with `write_back:false` and
`enable_model_call:false` unless explicitly overridden.
