# report_audit role

Consolidates audit role outputs (security + dependency) into one unified report.

Gathers live system facts via `gludd_facts` for freshness context, reads the
JSON artifacts from `audit_security` and `audit_dependencies` roles, and
produces a consolidated JSON + markdown report.

Report-only — never mutates the repo.

## Key variables

| Variable | Default | Notes |
|---|---|---|
| `audit_security_artifact` | `""` | Path to audit_security_report.json |
| `audit_dependencies_artifact` | `""` | Path to audit_dependencies_report.json |
| `artifact_dir` | `/tmp/gludd-report-audit` | Output dir |
| `enable_git_push` | `false` | Always false |

## Example

```yaml
- name: Consolidate audit reports
  ansible.builtin.include_role:
    name: general_ludd.agent.report_audit
  vars:
    audit_security_artifact: /tmp/gludd-audit-security/audit_security_report.json
    audit_dependencies_artifact: /tmp/gludd-audit-dependencies/audit_dependencies_report.json
    artifact_dir: /tmp/consolidated-audit
```
