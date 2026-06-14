# story_create

Converts a free-form feature request (`request_text`) into a structured user story with:

- **narrative**: "As a [role], I want [goal], so that [benefit]."
- **acceptance_criteria[]**: list of testable criteria

## Variables

| Variable | Default | Description |
|---|---|---|
| `request_text` | `"As a developer I want to improve the system"` | The free-form feature request |
| `enable_model_call` | `false` | Call model to draft story (template fallback when off) |
| `write_back` | `false` | Create todo in gludd_db |
| `artifact_dir` | `/tmp/gludd-story-create` | Output directory |
| `daemon_url` | `http://localhost:8000` | Daemon base URL |
| `psk` | `""` | Pre-shared key |

## Artifacts

- `story_create.json` — structured story with title, narrative, acceptance_criteria
- `story_create.md` — markdown story card

## Report-Only

By default this role never mutates the repo, database, or git. Set `write_back: true` to create a todo.
