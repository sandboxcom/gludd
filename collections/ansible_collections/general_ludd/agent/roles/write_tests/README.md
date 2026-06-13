# write_tests role

Generate and place tests via model agent, then run them.

Gathers live facts via `gludd_facts` and branches on `history.success_rate`
to decide how strict to be. Uses `gludd_agent_run` to produce test code, then
runs the tests with `test_run_cmd`. Writes a JSON result artifact.

## Key variables

| Variable | Default | Notes |
|---|---|---|
| `test_subject` | `""` | What to test (required) |
| `test_framework` | `"pytest"` | Test framework |
| `test_style` | `"unit"` | unit / integration / e2e |
| `test_run_cmd` | `""` | Command to run tests (empty = skip run) |
| `enable_git_push` | `false` | Push DISABLED by default |
| `artifact_dir` | `/tmp/gludd-write-tests` | Output dir |

## Security

- `psk` is `no_log` everywhere.
- Push is disabled by default.

## Example

```yaml
- name: Write tests for the auth module
  ansible.builtin.include_role:
    name: general_ludd.agent.write_tests
  vars:
    repo_path: /workspace/myrepo
    test_subject: "Authentication token validation in src/auth.py"
    test_run_cmd: "make test-unit"
    artifact_dir: /tmp/my-artifact
```
