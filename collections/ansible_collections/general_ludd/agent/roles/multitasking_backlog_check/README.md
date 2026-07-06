# multitasking_backlog_check

Inspects the gludd multitasking backlog JSON file.

## Description

Wraps `scripts/multitasking_backlog_check.py` to load and validate the
multitasking backlog (`scripts/multitasking_backlog.json`). Supports:

| Mode | What it does |
|---|---|
| `assert-done` | Exit 0 if all items done, exit 1 if any open (anti-rubber-stamp) |
| `open-count` | Print integer count of non-done items |
| `list-open` | Print `id: title [status]` for each non-done item |

Anti-rubber-stamp rule: a "done" item with empty `evidence` field is treated as
still-open. A missing/malformed file exits 2.

## Variables

| Variable | Default | Description |
|---|---|---|
| `backlog_check_mode` | `assert-done` | Mode: assert-done / open-count / list-open |
| `repo_path` | `.` | Path to the gludd repo root |
| `backlog_file` | `""` | Override backlog file path (empty = default) |
| `artifact_dir` | `/tmp/gludd-multitasking-backlog-check` | Artifact output directory |

## Example

```yaml
- hosts: localhost
  vars:
    backlog_check_mode: assert-done
  roles:
    - role: general_ludd.agent.multitasking_backlog_check
```
