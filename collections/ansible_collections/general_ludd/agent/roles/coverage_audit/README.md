# coverage_audit

Run per-file coverage audit on any project.

## Usage

```yaml
- name: Audit project coverage
  hosts: localhost
  roles:
    - general_ludd.agent.coverage_audit
  vars:
    project_dir: "/path/to/project"
    source_path: "src"
    threshold: 85             # fail when any file falls below this %
    fail_on_below: true       # fail the play when threshold breached
    artifact_dir: "/tmp/coverage-audit"
```

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `project_dir` | `.` | Root of the project to audit |
| `source_path` | `src` | Source tree to measure |
| `test_path` | `tests/` | pytest test directory |
| `threshold` | `85` | Per-file coverage % floor |
| `fail_on_below` | `true` | Fail play when files are below threshold |
| `python_bin` | `python3` | Python interpreter |
| `artifact_dir` | `/tmp/gludd-coverage-audit` | Output directory |

## Output

Writes `coverage_audit.json` to the artifact directory containing:

- `threshold` — the percentage floor applied
- `total_files` — number of source files measured
- `files_below_threshold` — count of files under the floor
- `passed` — bool: `true` when all files meet the floor
- `files_under_threshold` — array of files below threshold
- `per_file` — dict of `filename: percentage` for every source file
