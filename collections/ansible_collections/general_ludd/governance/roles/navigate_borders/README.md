# navigate_borders

Exposes the `borders.py` module_util to agents via an Ansible role. Given an
ISO 3166-1 alpha-2 country code, returns the country's land/maritime borders,
neighbour count, landlocked status, and coastline flag.

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `navigate_borders_enabled` | `false` | Must be `true` to run the role |
| `navigate_borders_country` | `null` | 2-letter ISO country code |
| `navigate_borders_list_countries` | `false` | List known country codes instead of lookup |
| `navigate_borders_output_dir` | `/tmp/gludd-governance-borders` | Artifact directory |

## Result facts

- `navigate_borders_result` — parsed JSON from the module_util
- `navigate_borders_verdict` — compact summary (found, border counts, landlocked)
