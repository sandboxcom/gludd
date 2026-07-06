# account_lifecycle role

Create / cleanup **ephemeral** cloud accounts (AWS IAM user, GCP service
account, Azure service principal) on demand, scoped to a budget, and tear
them down once the workload that requested them completes.

The role never mutates a repo. It produces a JSON + markdown report and
announces the result on the daemon's message queue so other agents in the
project can observe the lifecycle decision.

## Key variables

| Variable | Default | Notes |
|---|---|---|
| `mode` | `create` | `create` provisions a fresh ephemeral account; `cleanup` sweeps expired ones |
| `provider` | `aws` | aws / gcp / azure |
| `budget_limit` | `10.0` | Per-account USD cap |
| `retention_period_hours` | `24` | Age after which an account is eligible for cleanup |
| `auto_delete_after_use` | `true` | If false, accounts are never torn down automatically |
| `artifact_dir` | `/tmp/gludd-account-lifecycle` | Output dir for the JSON + md report |
| `enable_git_push` | `false` | Always false — role never mutates a repo |

## Example

```yaml
- name: Provision an ephemeral AWS account
  ansible.builtin.include_role:
    name: general_ludd.agent.account_lifecycle
  vars:
    mode: create
    provider: aws
    budget_limit: 5.0
    retention_period_hours: 8
    auto_delete_after_use: true
```

```yaml
- name: Sweep expired ephemeral accounts
  ansible.builtin.include_role:
    name: general_ludd.agent.account_lifecycle
  vars:
    mode: cleanup
```
