# lookup_governing_body

Exposes the `governing_bodies.py` module_util to agents. Returns national and
supranational governing bodies (legislature, judiciary, executive) for a
country, optionally filtered by body type.

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `lookup_governing_body_enabled` | `false` | Must be `true` to run |
| `lookup_governing_body_country` | `null` | 2-letter ISO country code |
| `lookup_governing_body_type` | `null` | Optional: `legislature`, `judiciary`, `executive` |
| `lookup_governing_body_output_dir` | `/tmp/gludd-governance-bodies` | Artifact directory |

## Result facts

- `lookup_governing_body_result` — parsed JSON
- `lookup_governing_body_verdict` — compact summary (found, body_count, type_filter)
