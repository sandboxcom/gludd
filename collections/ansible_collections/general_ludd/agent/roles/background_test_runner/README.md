# background_test_runner

Background test execution role for the `general_ludd.agent` collection.

Ports the `.opencode/skills/background-test-runner/SKILL.md` capability into a
gludd ansible role.

## Description

Never run a test that takes >30s in the foreground — it blocks ALL subagent
dispatch. This role launches a test in the background via `make test-bg` and
polls `make test-bg-status` until a terminal marker (PASS/FAIL/FINISHED)
appears or the poll budget is exhausted.

## Why this role exists

The opencode skill codifies the workflow for an opencode CLI agent. A gludd
daemon agent executing ansible playbooks has no equivalent — the existing
`run_tests` role runs synchronously and blocks. This role provides the
non-blocking alternative.

## Variables

| Variable | Default | Description |
|---|---|---|
| `state` | `launch` | Mode: `launch` (start + poll), `status` (probe), `kill` (terminate) |
| `testfile` | `""` | Test file to run (e.g. `tests/unit/test_foo.py`) |
| `poll_interval_seconds` | `10` | Seconds between status polls |
| `poll_max_iterations` | `180` | Max poll iterations (180×10s = 30 min) |
| `fail_on_test_failure` | `true` | Fail the play when launched test reports FAIL |
| `artifact_dir` | `/tmp/gludd-background-test-runner` | Artifact output path |

## Artifacts

- `<artifact_dir>/background_test_runner.json` — state, verdict, raw output
- `<artifact_dir>/background_test_runner.md` — human-readable report

## Example

```yaml
- hosts: localhost
  roles:
    - role: general_ludd.agent.background_test_runner
      vars:
        state: launch
        testfile: "tests/unit/test_foo.py"
        poll_interval_seconds: 15
```

## Policy

- Poll from a subagent, never the main thread.
- Always verify the test reached a terminal marker before declaring it done.
- A timeout verdict means the test did not finish within the poll budget —
  investigate, do not assume success.
