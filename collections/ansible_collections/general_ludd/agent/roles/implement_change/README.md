# implement_change role

Apply a model-generated code change in an isolated git worktree.

Gathers live system facts via `gludd_facts` before acting — skips if
backlog is empty and no `todo_id` is provided. Announces start/completion
via `gludd_message` so downstream roles can react. Uses `gludd_agent_run`
to produce the change, `gludd_git` to commit, and removes the worktree in
an `always` block.

## Key variables

| Variable | Default | Notes |
|---|---|---|
| `change_description` | `""` | What to implement (required) |
| `todo_id` | `""` | Optional todo linkage |
| `repo_path` | `""` | Absolute path to git repo root |
| `enable_git_push` | `false` | Push DISABLED by default |
| `model_profile` | `""` | Model routing profile |
| `artifact_dir` | `/tmp/gludd-implement-change` | Output dir |

## Security

- `psk` is `no_log` everywhere.
- `enable_git_push: false` — never pushes without explicit opt-in.
- Change runs in an isolated worktree, always cleaned up.

## Example

```yaml
- name: Implement a bugfix
  ansible.builtin.include_role:
    name: general_ludd.agent.implement_change
  vars:
    repo_path: /workspace/myrepo
    todo_id: "TODO-abc123"
    change_description: "Fix the off-by-one error in pagination"
    artifact_dir: /tmp/my-artifact
```
