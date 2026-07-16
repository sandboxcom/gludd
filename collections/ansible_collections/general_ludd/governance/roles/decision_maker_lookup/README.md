# decision_maker_lookup

Exposes the `decision_makers.py` module_util to agents. Returns key political
decision-maker roles (head of state, executive, legislative leaders, senior
judiciary) for a country. Describes the **office** and how it is filled — not
the current individual holder — so the knowledge base does not go stale.

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `decision_maker_lookup_enabled` | `false` | Must be `true` to run |
| `decision_maker_lookup_country` | `null` | 2-letter ISO country code |
| `decision_maker_lookup_branch` | `null` | Optional branch filter (executive, legislative, judicial, ...) |
| `decision_maker_lookup_output_dir` | `/tmp/gludd-governance-deciders` | Artifact directory |

## Result facts

- `decision_maker_lookup_result` — parsed JSON
- `decision_maker_lookup_verdict` — compact summary (found, role_count, branch_filter)
