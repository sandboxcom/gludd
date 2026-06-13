# dependency_update role

Analyze dependencies for outdated packages and vulnerabilities.

Gathers live system facts via `gludd_facts`, runs `gludd_agent_run` to
produce a structured analysis of dependency state (outdated, vulnerable,
breaking-change risks). Report-only by default — actual package mutations
require `apply_updates: true`.

## Key variables

| Variable | Default | Notes |
|---|---|---|
| `dep_package` | `""` | Specific package (empty = all) |
| `dep_ecosystem` | `"python"` | python / node / go / rust |
| `apply_updates` | `false` | Must be true to apply changes |
| `enable_git_push` | `false` | Push DISABLED by default |
| `artifact_dir` | `/tmp/gludd-dependency-update` | Output dir |

## Example

```yaml
- name: Analyze Python dependencies
  ansible.builtin.include_role:
    name: general_ludd.agent.dependency_update
  vars:
    repo_path: /workspace/myrepo
    dep_ecosystem: python
    apply_updates: false
    artifact_dir: /tmp/dep-report
```
