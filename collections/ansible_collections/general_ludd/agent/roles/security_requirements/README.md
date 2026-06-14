# security_requirements

Security acceptance criteria derivation role for the `general_ludd.agent` collection.

## Description

Fetches a story from `gludd_db` (todo_get) or uses an inline `story_text`, then
derives security acceptance criteria across four categories: authn/authz, input
validation, secrets handling, and logging. Optionally augments criteria via
`gludd_model_call` (gated behind `enable_model_call: false`) and optionally
writes criteria back to the story (gated behind `write_back: false`).
**REPORT-ONLY by default.**

## Variables

| Variable | Default | Description |
|---|---|---|
| `story_id` | `""` | todo_id to fetch from gludd_db |
| `story_text` | (default story) | Inline story text |
| `enable_model_call` | `false` | Augment via model call |
| `write_back` | `false` | Write criteria back to story |
| `enable_git_push` | `false` | Always false — report-only |

## Artifacts

- `<artifact_dir>/security_requirements.json` — criteria[], story_id
- `<artifact_dir>/security_requirements.md` — human-readable criteria
