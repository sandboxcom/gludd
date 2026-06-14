# secret_scan

Secret scanning role for the `general_ludd.agent` collection.

## Description

Wraps `detect-secrets` or `gitleaks`. The heavy scan tool is gated behind
`enable_scan: false` with `scan_output_override` providing a canned clean
result for molecule testing. `no_log` is enforced on all tasks that may touch
raw scan output. Artifacts expose only findings **count** and affected
**filenames** — never secret values. **REPORT-ONLY.**

## Variables

| Variable | Default | Description |
|---|---|---|
| `artifact_dir` | `/tmp/gludd-secret-scan` | Artifact output path |
| `repo_path` | `"."` | Repository path to scan |
| `scan_tool` | `detect-secrets` | `detect-secrets` or `gitleaks` |
| `enable_scan` | `false` | Run the real tool (false = use override) |
| `scan_output_override` | clean detect-secrets JSON | Canned output when scan disabled |
| `enable_git_push` | `false` | Always false — report-only |

## Artifacts

- `<artifact_dir>/secret_scan.json` — finding_count, affected_files, verdict
- `<artifact_dir>/secret_scan.md` — human-readable report (no secret values)
