# civic_service_finder

Exposes the `civic_services.py` module_util to agents. Returns public/civic
services (health, identity, electoral, emergency, postal, social) for a
country, optionally filtered by category.

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `civic_service_finder_enabled` | `false` | Must be `true` to run |
| `civic_service_finder_country` | `null` | 2-letter ISO country code |
| `civic_service_finder_category` | `null` | Optional category (health, identity, electoral, ...) |
| `civic_service_finder_output_dir` | `/tmp/gludd-governance-civic` | Artifact directory |

## Result facts

- `civic_service_finder_result` — parsed JSON
- `civic_service_finder_verdict` — compact summary (found, service_count, category_filter)
