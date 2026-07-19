# info_classification_check

Exposes the `info_classification.py` module_util to agents. Returns government
classification levels (Top Secret → Unclassified) or enterprise data
classification levels (Public → Restricted), with descriptions and handling
guidance. Can resolve a single level or dump the whole scheme.

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `info_classification_check_enabled` | `false` | Must be `true` to run |
| `info_classification_check_scheme` | `data` | `government` or `data` |
| `info_classification_check_level` | `null` | Optional single level name to resolve |
| `info_classification_check_output_dir` | `/tmp/gludd-governance-classification` | Artifact directory |

## Result facts

- `info_classification_check_result` — parsed JSON
- `info_classification_check_verdict` — compact summary (found, scheme, level)
