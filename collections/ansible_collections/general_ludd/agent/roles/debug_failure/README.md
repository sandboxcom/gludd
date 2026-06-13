# debug_failure role

Analyze a failing test or task return and propose a fix.

Gathers live system facts via `gludd_facts` (success rate, recent failures)
to provide context. Runs `gludd_agent_run` to produce a root cause analysis
and proposed fix, then sends the diagnosis to the `implement_change` role via
`gludd_message`.

## Key variables

| Variable | Default | Notes |
|---|---|---|
| `failure_description` | `""` | Human-readable failure description |
| `failure_output` | `""` | Raw output from failing test/command |
| `debug_handoff_recipient` | `"implement_change"` | Role to receive diagnosis |
| `enable_git_push` | `false` | Push DISABLED by default |

## Example

```yaml
- name: Debug a failing test
  ansible.builtin.include_role:
    name: general_ludd.agent.debug_failure
  vars:
    failure_description: "test_auth_token_validation fails with KeyError"
    failure_output: "{{ test_output }}"
    todo_id: "TODO-debug-001"
    debug_handoff_recipient: "implement_change"
```
