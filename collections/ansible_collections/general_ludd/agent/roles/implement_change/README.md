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
| `mcp_sync_enabled` | `true` | Sync MCP docs/tools when the target repo supports it |
| `mcp_authoring_stub` | `true` | Generate missing module documentation stubs |

## MCP authoring capability

The role may author changes in repositories that do not contain Gludd's
`scripts/mcp_docs_check.py` or `scripts/gen_mcp_tools.py`. It probes each helper
with `ansible.builtin.stat` and skips only the unsupported operation when the
corresponding file is absent. A helper that is present but exits nonzero still
produces the existing non-fatal warning, so real documentation or generation
failures remain observable.

This follows the long-standing Ansible community guidance to stat an optional
file and condition the dependent task on `stat.exists`, rather than execute
against a known-missing path: [Ansible forum discussion on conditionally
handling optional files](https://forum.ansible.com/t/change-request-community-general-ini-file/3953).

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
