# backlog_guard_audit

System-wide bug-class sweep + guard-coverage report.

## Description

Wraps `scripts/backlog_audit.py` to run the bug-class registry sweep over the
entire repo. Reports:
- Every occurrence of every registered bug class (never a point fix)
- Guard-coverage gaps (bug classes whose guard test is not in collected pytest ids)
- BacklogAuditor task verdicts (if the auditor is importable)

**Non-zero exit = occurrences OR guard gaps found** — can be wired into a gate.

## Variables

| Variable | Default | Description |
|---|---|---|
| `repo_path` | `.` | Path to the gludd repo root |
| `artifact_dir` | `/tmp/gludd-backlog-guard-audit` | Artifact output directory |
| `skip_collect` | `false` | Skip pytest collection (faster but all guards show as gaps) |

## Artifacts

- `<artifact_dir>/backlog_guard_audit.json` — structured audit result
- `<artifact_dir>/backlog_guard_audit.md` — human-readable audit report

## Example

```yaml
- hosts: localhost
  roles:
    - role: general_ludd.agent.backlog_guard_audit
```
