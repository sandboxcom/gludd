# agent_task role

Full task lifecycle: fetch todo → create worktree → render skill → run agent
model → quality gate → git commit → mark todo done → write artifact.

Uses `block/rescue/always` so the worktree is always removed and the todo
status is always updated (to `done` or `failed`) regardless of errors.

## Required variables

| Variable | Description |
|---|---|
| `repo_path` | Absolute path to the git repository root |
| `todo_id` | Todo identifier string |

## Important defaults

| Variable | Default | Notes |
|---|---|---|
| `enable_git_push` | `false` | Push is DISABLED by default — must be explicitly enabled |
| `quality_gate_cmd` | `make test-count` | Run in the worktree before committing |
| `psk` | `""` | Prefer `GLUDD_PSK` env var over inline value |

## Example

```yaml
- name: Run an agent task
  hosts: localhost
  gather_facts: false
  tasks:
    - name: Execute todo
      ansible.builtin.include_role:
        name: general_ludd.agent.agent_task
      vars:
        repo_path: /workspace/myrepo
        todo_id: "TODO-abc123"
        work_type: code
        skill_name: code-review
        skill_vars:
          project_name: myproject
```

## Security

- `psk` is marked `no_log` in all tasks that pass it to modules.
- Push is disabled by default (`enable_git_push: false`) — roles must not
  push to remotes without explicit operator opt-in.
- The worktree is always removed in the `always` block, even on hard failures.
