# audit_security role

Security scan oriented audit — **report-only, never mutates the repo**.

Gathers live system facts via `gludd_facts`, runs `gludd_agent_run` to
analyze the codebase for security issues (hardcoded secrets, injection
vulnerabilities, path traversal, auth gaps, CVE patterns), and writes
structured JSON + markdown reports.

## Key variables

| Variable | Default | Notes |
|---|---|---|
| `scan_target` | `"."` | Path to scan |
| `security_focus_areas` | (see defaults) | List of areas to focus on |
| `artifact_dir` | `/tmp/gludd-audit-security` | Output dir |
| `enable_git_push` | `false` | Always false — no mutations |
| `apply_changes` | `false` | Always false — report-only |

## Security guarantees

This role NEVER:
- Uses `gludd_git` (no commits)
- Updates todo status
- Pushes to any remote
- Uses `shell:` with templated user input

## Example

```yaml
- name: Run security audit
  ansible.builtin.include_role:
    name: general_ludd.agent.audit_security
  vars:
    scan_target: /workspace/myrepo
    security_focus_areas:
      - "hardcoded secrets"
      - "SQL injection"
    artifact_dir: /tmp/security-report
```
