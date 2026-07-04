# gha_usage

Query GitHub Actions usage and billing for a repository. Reads recent runs,
workflow definitions, and org-level billing; computes run statistics and
free-tier utilisation warnings.

## FQCN

`general_ludd.agent.gha_usage`

## Example

```yaml
- hosts: localhost
  roles:
    - role: general_ludd.agent.gha_usage
      vars:
        gha_repo: "sandboxcom/gludd"
        gha_org: "sandboxcom"
```

## Outputs

Registered as `gha_usage_report`:

| Field | Description |
|---|---|
| `runs_last_24h` | Number of workflow runs in the last 24 hours |
| `runs_succeeded` | Runs that completed successfully |
| `runs_failed` | Runs with a failure conclusion |
| `success_rate_pct` | Percentage of runs that succeeded |
| `estimated_minutes` | Estimated minutes used (runs_last_24h * gha_avg_minutes_per_run) |
| `free_minutes` | Free-tier minutes limit (2000 for private repos, 0 = unlimited for public) |
| `pct_used` | Percentage of free tier consumed |
| `usage_warning` | True when estimated usage exceeds the warn threshold |
| `workflow_count` | Number of workflows defined in the repo |
| `billing_available` | Whether billing data was fetched successfully |
| `billing` | Raw billing response (empty dict when unavailable) |

## Inputs

See `defaults/main.yml` for the full variable list with defaults.
