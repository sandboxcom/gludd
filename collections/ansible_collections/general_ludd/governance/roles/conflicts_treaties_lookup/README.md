# conflicts_treaties_lookup

Exposes the `conflicts_treaties.py` module_util to agents. Returns active
conflicts and/or ratified multilateral treaties for a country. Data is a static
knowledge base curated for agent lookup — **not** real-time intelligence.

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `conflicts_treaties_lookup_enabled` | `false` | Must be `true` to run |
| `conflicts_treaties_lookup_country` | `null` | 2-letter ISO country code |
| `conflicts_treaties_lookup_scope` | `both` | `conflicts`, `treaties`, or `both` |
| `conflicts_treaties_lookup_output_dir` | `/tmp/gludd-governance-conflicts` | Artifact directory |

## Result facts

- `conflicts_treaties_lookup_result` — parsed JSON
- `conflicts_treaties_lookup_verdict` — compact summary (conflicts_count, treaties_count)
