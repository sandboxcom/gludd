---
{
  "name": "background-test-runner",
  "description": "Launch long-lived tests in the background and poll their status, so no task thread is ever blocked waiting for a test.",
  "tags": ["testing", "background", "parallel", "pipeline"],
  "category": "engineering"
}
---

# Background Test Runner

Never run a test that takes >30s in the foreground. Use the background test
runner to keep the subagent pool full while tests execute.

## Workflow

### 1. Launch a test in the background

```
make test-bg TESTFILE='tests/unit/test_foo.py'
```

This runs `make test-specific` via `nohup`, writing logs to `.gate-logs/` and
the PID to `.gate-logs/.test-<sanitized>.pid`.

### 2. Poll its status (non-blocking)

```
make test-bg-runner ACTION=status TESTFILE='tests/unit/test_foo.py'
```

Prints: RUNNING/FINISHED, PID, current terminal marker (PASS/FAIL), last 15
log lines.

### 3. List all background tests

```
make test-bg-runner ACTION=poll-all
```

Shows every tracked test with RUNNING/FINISHED status, PID, and timestamp.

### 4. Kill a running test

```
make test-bg-runner ACTION=kill TESTFILE='tests/unit/test_foo.py'
```

Sends SIGTERM, waits 5s, then SIGKILL if still alive. Cleans up the PID file.

### 5. Programmatic access (Python)

```python
from general_ludd.runner.background_test_runner import BackgroundTestRunner

runner = BackgroundTestRunner()
runner.launch("tests/unit/test_foo.py")
status = runner.status("tests/unit/test_foo.py")
all_statuses = runner.poll_all()
results = runner.results("tests/unit/test_foo.py")
```

### 6. Blocking wait (for scripts that MUST wait)

```python
result = runner.launch("tests/unit/test_foo.py", wait=True)
# or:
result = runner._wait("tests/unit/test_foo.py", timeout_min=30)
```

## Rules

1. **Never run a test that takes >30s in the foreground** — it blocks ALL
   subagent dispatch. Use `make test-bg` instead.
2. **Never wait for a test result without dispatching other work** — launch the
   test in background, dispatch other subagents, then poll from a subagent.
3. **Poll from subagents, not the main thread** — use `make test-bg-runner ACTION=status` in a
   read-only research subagent, or call `runner.poll_all()` from a subagent task.
4. **Always verify the test finished** — poll until the terminal marker appears
   (PASS or FAIL) and log the result before declaring the test done.

## Integration with gate-background

| Need | Command |
|---|---|
| Full project gate | `make gate-background` + `make gate-status-check` |
| Single test file | `make test-bg TESTFILE=...` + `make test-bg-runner ACTION=status TESTFILE=...` |
| List all running | `make test-bg-runner ACTION=poll-all` |
| Kill a stuck test | `make test-bg-runner ACTION=kill TESTFILE=...` |
| Kill all background | `make test-bg-runner ACTION=poll-all` to list, then `ACTION=kill` per test |
