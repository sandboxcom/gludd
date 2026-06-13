# document_change role

Generate documentation for a code change via model agent.

Gathers live facts via `gludd_facts`, runs `gludd_agent_run` to produce
documentation, and writes the result to the artifact directory. Optionally
writes the documentation back to the repo when `write_to_repo: true`.

## Key variables

| Variable | Default | Notes |
|---|---|---|
| `doc_target` | `""` | File/module to document |
| `change_summary` | `""` | Summary of the change |
| `doc_type` | `"inline"` | inline / api / changelog / readme / architecture |
| `doc_format` | `"markdown"` | markdown / rst / plain |
| `write_to_repo` | `false` | Must be true to write to repo |
| `doc_output_file` | `""` | Relative path in repo for doc output |
| `enable_git_push` | `false` | Push DISABLED by default |

## Example

```yaml
- name: Document a new API endpoint
  ansible.builtin.include_role:
    name: general_ludd.agent.document_change
  vars:
    doc_target: "src/api/endpoints.py"
    change_summary: "Added POST /api/reports endpoint"
    doc_type: "api"
    doc_format: "markdown"
    write_to_repo: false
    artifact_dir: /tmp/my-docs
```
