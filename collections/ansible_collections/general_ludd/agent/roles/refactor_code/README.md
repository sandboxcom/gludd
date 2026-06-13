# refactor_code role

Model-driven code refactoring in an isolated git worktree.

Gathers live facts via `gludd_facts` to check model health and history
success rate. Runs `gludd_agent_run` to perform the refactoring (preserving
public API, removing duplication), commits, and cleans up the worktree.

## Key variables

| Variable | Default | Notes |
|---|---|---|
| `refactor_target` | `""` | File/module to refactor (required) |
| `refactor_goal` | `""` | What improvement to make |
| `refactor_constraints` | `""` | Constraints to respect |
| `enable_git_push` | `false` | Push DISABLED by default |
| `artifact_dir` | `/tmp/gludd-refactor-code` | Output dir |

## Example

```yaml
- name: Refactor the auth module
  ansible.builtin.include_role:
    name: general_ludd.agent.refactor_code
  vars:
    repo_path: /workspace/myrepo
    refactor_target: "src/auth.py"
    refactor_goal: "Remove duplicated token validation logic"
```
