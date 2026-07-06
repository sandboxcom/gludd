# generate_status_table

Generates the README "Feature & Task Completion Status" table from `docs/features.yml`.

## Description

Wraps `scripts/gen_status_table.py` which:
1. Reads `docs/features.yml` manifest
2. Verifies each feature's evidence refs via `FeatureVerifier`
3. Renders a per-section Markdown status table
4. Injects/replaces between `<!-- STATUS-TABLE:START -->` and `<!-- STATUS-TABLE:END -->` markers

## Modes

| Mode | Description |
|---|---|
| `check` | Verify README table is current (exit 0 = current, exit 1 = stale) |
| `write` | Generate and write table into README.md |
| `print` | Print table to stdout without modifying README |

## Variables

| Variable | Default | Description |
|---|---|---|
| `gen_mode` | `check` | Mode: check / write / print |
| `repo_path` | `.` | Path to the gludd repo root |
| `artifact_dir` | `/tmp/gludd-status-table` | Artifact output directory |
| `manifest_path` | `""` | Override manifest path (empty = docs/features.yml) |
| `output_path` | `""` | Also write to this file |

## Example

```yaml
- hosts: localhost
  vars:
    gen_mode: check
  roles:
    - role: general_ludd.agent.generate_status_table
```
