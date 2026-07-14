# ci_pipeline_repair

Codifies the recurring CI pipeline inspection and repair workflow (W11: PEP 440 version fix, W5.2: artifact upload, W1.7: CI gate structure).

## What it does

1. Gathers live daemon facts (`gludd_facts`) for context.
2. Scans `.github/workflows/*.yml` for common breakage patterns:
   - Version strings with leading `v` or hyphen separators (non-PEP 440)
   - Gate job not required by downstream jobs
   - Build steps without artifact upload
   - Deprecated `set-output` command usage
3. Scans `pyproject.toml` + `version_init_path` for version format issues.
4. Classifies CI health: `healthy` / `degraded` / `broken` / `no_workflows`.
5. Writes `ci_pipeline_repair.json` + `ci_pipeline_repair.md` with findings and suggested fixes.

## Safety model

- `enable_auto_fix: false` (default) — report only, no file edits
- `enable_git_push: false` (default) — no pushes
- `ansible_check_mode: true` — all file operations skipped

## Key variables

| Variable | Default | Description |
|---|---|---|
| `repo_path` | `"."` | Repo root (where `.github/workflows/` lives) |
| `enable_auto_fix` | `false` | Set true to apply suggested fixes |
| `version_init_path` | `""` | Path to `__init__.py` with `__version__` |
| `artifact_dir` | `/tmp/gludd-ci-pipeline-repair` | Artifact output dir |

## Artifact

`ci_pipeline_repair.json`:
```json
{
  "role": "ci_pipeline_repair",
  "status": "completed",
  "ci_health": "healthy",
  "total_findings": 0,
  "high_severity": 0,
  "workflow_findings": [],
  "version_findings": []
}
```
