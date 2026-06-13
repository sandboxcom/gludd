# report_metrics role

Model usage, success/failure rates, and throughput metrics — from live facts.

Gathers live facts via `gludd_facts`, computes derived metrics (throughput
tier, failure rate, queue depth), and writes structured JSON + markdown
reports. Report-only — never mutates the repo.

## Key variables

| Variable | Default | Notes |
|---|---|---|
| `artifact_dir` | `/tmp/gludd-report-metrics` | Output dir |
| `enable_git_push` | `false` | Always false |

## Throughput tiers

| Condition | Tier |
|---|---|
| `total_runs > 100` | high |
| `10 <= total_runs <= 100` | medium |
| `total_runs < 10` | low |

## Example

```yaml
- name: Generate metrics report
  ansible.builtin.include_role:
    name: general_ludd.agent.report_metrics
  vars:
    artifact_dir: /tmp/metrics-report
```
