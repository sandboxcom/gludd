# lint_and_check role

Runs configurable lint and typecheck commands and writes a JSON artifact.

## Variables

| Variable | Default | Description |
|---|---|---|
| `project_dir` | `.` | Project root directory |
| `lint_cmd` | `make lint` | Lint command |
| `typecheck_cmd` | `make typecheck` | Typecheck command |
| `artifact_dir` | `/tmp/gludd-lint-check` | Artifact output directory |
| `fail_on_lint` | `true` | Fail play if lint fails |
| `fail_on_typecheck` | `false` | Fail play if typecheck fails |
