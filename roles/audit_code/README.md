# audit_code role

Runs a configurable code audit / SAST command and writes findings as a JSON artifact.

## Variables

| Variable | Default | Description |
|---|---|---|
| `project_dir` | `.` | Project root directory |
| `audit_cmd` | `make sast` | Audit command |
| `artifact_dir` | `/tmp/gludd-audit-code` | Artifact output directory |
| `fail_on_findings` | `false` | Fail play if audit has findings |
