# run_tests role

Runs a configurable test command and writes the result as a JSON artifact.

## Variables

| Variable | Default | Description |
|---|---|---|
| `test_dir` | `.` | Directory to run the test command in |
| `test_cmd` | `make test-count` | Command to run |
| `artifact_dir` | `/tmp/gludd-run-tests` | Where to write the result artifact |
| `allow_failure` | `false` | If true, continue even if tests fail |
