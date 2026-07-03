# ci_pipeline_verify

Verifies CI pipeline status for a given branch or commit. Pushes the branch (optionally),
polls CI until completion or timeout, and registers a `ci_verdict` fact.

## What it does

1. Gathers live daemon facts (`gludd_facts`) for system context.
2. Optionally pushes the branch (`ci_push: true`).
3. Polls CI status via a configurable endpoint (`ci_status_endpoint`) or the `gh` CLI.
4. Registers a `ci_verdict` fact with:
   - `commit_sha` — the commit being verified
   - `run_id` — the CI run identifier
   - `conclusion` — `success`, `failure`, `timed_out`, or similar
   - `passed` — boolean, true if CI passed
   - `timed_out` — boolean, true if polling exhausted the timeout
   - `branch` — the branch checked
   - `failed_job_logs` — list of failed job details (when available)

## Key variables

| Variable | Default | Description |
|---|---|---|
| `ci_branch` | `"master"` | Branch to verify |
| `ci_commit_sha` | `""` | Commit SHA (falls back to HEAD) |
| `ci_push` | `false` | Set true to push before verifying |
| `ci_poll_timeout_seconds` | `600` | Max seconds to wait for CI |
| `ci_poll_interval` | `15` | Seconds between poll attempts |
| `ci_gh_repo` | `"sandboxcom/gludd"` | GitHub repo for `gh` CLI fallback |
| `ci_status_endpoint` | `""` | Custom endpoint URL (overrides `gh` CLI) |

## Safety model

- `ci_push: false` (default) — no git push occurs
- Poll timeout prevents infinite waits
- Fail-closed: timeout or missing data produces `ci_verdict.timed_out = true`
