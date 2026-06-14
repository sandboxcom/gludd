# retrospective

Agile retrospective from `gludd_facts.metrics + gludd_facts.history + gludd_message` receive.

## Guaranteed non-empty outputs

For the seeded data (success_rate=0.92, total_runs=25, failures=2, backlog=3, inbox message):
- **well[]**: at least 3 items (high success rate, agents operational, traces captured)
- **ill[]**: at least 2 items (failures recorded, backlog remains) + inbox message item
- **actions[]**: at least 3 items (root-cause failures, groom backlog, standup daily) + velocity action

## Variables

| Variable | Default | Description |
|---|---|---|
| `enable_model_call` | `false` | Generate AI narrative |
| `artifact_dir` | `/tmp/gludd-retrospective` | Output directory |
| `daemon_url` | `http://localhost:8000` | Daemon base URL |

## Artifacts

- `retrospective.json` — well[], ill[], actions[], narrative, history, inbox_messages
- `retrospective.md` — markdown retrospective card with checkboxes
