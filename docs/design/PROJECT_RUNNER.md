# Target-Project Toolchain Runner (`project_runner/`)

The keystone capability that lets gludd **work an external project** — run and
interpret that project's own tests, lints, builds, and security scans — instead
of only self-hosting on gludd's hardcoded `make test` + ruff/mypy/pytest.

Status: **slices 1-3 complete** (`src/general_ludd/project_runner/`, commit b410a5d0).
D2 `run_project_gate` now wired at `decision_applier.py:57-80`.

## Why

gludd's execution engine can edit code in a workspace that already points at an
external repo (jailed via realpath containment), but the only commands it ran
there were `make test`, `patch`, and `git`. To autonomously work a
Python/React/Postgres/Terraform/Redis/Mosquitto app it must run **that project's**
`npm test`, `terraform validate`, `ruff`, `semgrep`, `alembic upgrade`, etc., and
turn the outcome into a structured pass/fail. That's what this module adds.

## `project.yml`

A target repo declares its checks in a `project.yml` at its root:

```yaml
name: my-app
# argv[0] basenames gludd is permitted to execute in this repo. Fail-closed:
# a command whose executable is not listed is refused (unless the operator sets
# GLUDD_PROJECT_ALLOW_ANY_EXEC=1 for a fully-trusted target).
allowed_exec: [npm, pytest, ruff, mypy, terraform, alembic, semgrep, psql]
commands:
  test:      npm test
  lint:      ruff check .
  typecheck: npm run typecheck
  build:     npm run build
  migrate:   alembic upgrade head
  sast:      semgrep --config auto --error .
```

### Safety model (parity with the MCP launcher allowlist)

- Commands are parsed with `shlex` — **never** `shell=True`.
- **Shell metacharacters are rejected** (`; & | < > $ \` ( ) { } [ ] * ? ~` …) so a
  command can't chain, pipe, redirect, subshell, or glob-to-shell.
- `argv[0]` basename must be in `allowed_exec` — fail-closed.
- Execution is jailed to the workspace (realpath-contained cwd), runs in its own
  process group, and is killed (group SIGTERM→SIGKILL) on timeout so a hung
  `npm`/`terraform` and its children are reaped. Output is tail-bounded.

## API

```python
from general_ludd.project_runner import load_project_profile, ProjectCommandRunner

profile = load_project_profile("/path/to/target/repo")   # reads project.yml, fail-closed
runner = ProjectCommandRunner("/path/to/target/repo", profile)
result = runner.run("test")            # -> CheckResult
print(result.summary())                # "test: PASS (exit 0, 12.3s)"
# result.passed / exit_code / stdout_tail / stderr_tail / timed_out / duration_s / findings
```

## Roadmap

- **Slice 2** — `ExecutionEngine.run_project_check(check)` + register `run_project_check`
  as a model-callable tool so the agent can run a project check mid-task.
- **Slice 3** — parameterize `quality/preflight.py` + `gate.py` on a target
  `ProjectProfile` (default = gludd's own self-profile, so self-hosting is unchanged),
  reusing the already config-driven `QualityGateConfig`.
- **Later** — richer parsers (pytest/ruff/semgrep summary → structured `findings`),
  SAST/DAST presets, and service touchpoints (Postgres/Redis query, MQTT pub/sub —
  currently gaps).
