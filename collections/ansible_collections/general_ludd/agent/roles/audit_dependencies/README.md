# audit_dependencies role

Outdated/vulnerable dependency audit — **report-only, never mutates the repo**.

Gathers live facts via `gludd_facts`, runs `gludd_agent_run` to analyze
dependency manifests for outdated packages, known CVEs, and license
compliance. Writes structured JSON + markdown reports.

## Key variables

| Variable | Default | Notes |
|---|---|---|
| `dep_scan_path` | `"."` | Path with dependency manifests |
| `dep_ecosystem` | `"python"` | python / node / go / rust |
| `artifact_dir` | `/tmp/gludd-audit-dependencies` | Output dir |
| `enable_git_push` | `false` | Always false — no mutations |

## Example

```yaml
- name: Audit Python dependencies
  ansible.builtin.include_role:
    name: general_ludd.agent.audit_dependencies
  vars:
    dep_scan_path: /workspace/myrepo
    dep_ecosystem: python
    artifact_dir: /tmp/dep-audit
```
