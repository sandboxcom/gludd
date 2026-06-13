# report_status role

Renders a system status report from live `gludd_facts` data.

**This is the concrete proof that "logic in the Ansible YAML decides what to do
next" using live facts.** The role:
- Gathers live facts via `gludd_facts`
- Classifies system health as `healthy` / `degraded` / `critical` based on
  the live success rate from `gludd.history.success_rate`
- Branches on backlog size, model health, and unread message counts
- Writes structured JSON + markdown reports
- Warns when health is critical

Report-only — never mutates the repo.

## Key variables

| Variable | Default | Notes |
|---|---|---|
| `artifact_dir` | `/tmp/gludd-report-status` | Output dir |
| `enable_git_push` | `false` | Always false |

## Health classification

| Condition | Status |
|---|---|
| `success_rate >= 80%` | healthy |
| `50% <= success_rate < 80%` | degraded |
| `success_rate < 50%` | critical |
| No history | unknown |

## Example

```yaml
- name: Generate system status report
  ansible.builtin.include_role:
    name: general_ludd.agent.report_status
  vars:
    artifact_dir: /tmp/status-report
```
