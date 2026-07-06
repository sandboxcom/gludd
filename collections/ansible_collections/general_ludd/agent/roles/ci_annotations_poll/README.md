# ci_annotations_poll

Live GitHub CI annotations poller — surfaces per-line failure annotations as they appear.

## Description

Wraps `scripts/ci_annotations_poll.py` to poll a GitHub Actions CI run and
report per-line failure/error annotations as jobs complete. Polls at a configurable
interval until all jobs finish or the timeout is reached.

Distinct from `ci_pipeline_verify` which polls job-level status only.

## Variables

| Variable | Default | Description |
|---|---|---|
| `ci_run_id` | `""` | GitHub Actions run ID (required) |
| `repo_path` | `.` | Path to the gludd repo root |
| `artifact_dir` | `/tmp/gludd-ci-annotations-poll` | Artifact output directory |
| `poll_interval_seconds` | `20` | Seconds between poll intervals |
| `poll_max_seconds` | `3600` | Maximum total poll duration |
| `early_exit_on_failure` | `0` | Exit on first failure annotation |

## Example

```yaml
- hosts: localhost
  vars:
    ci_run_id: "1234567890"
  roles:
    - role: general_ludd.agent.ci_annotations_poll
```
