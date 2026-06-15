# soc_analyst

Security Operations triage role. Slurps security finding artifacts
(security_review, secret_scan, audit_security JSON), correlates and
deduplicates findings by rule+file grouping, escalates by count, and produces
a prioritized incident list.

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `daemon_url` | `http://localhost:8000` | Daemon URL |
| `artifact_dir` | `/tmp/gludd-soc-analyst` | Output directory |
| `finding_artifacts` | `[]` | List of paths to finding JSON files |
| `severity_floor` | `high` | Ignore findings below this severity |
| `escalation_count_threshold` | `2` | Count threshold for incident escalation |
| `handoff_recipient` | `""` | Recipient for priority=high message on incidents |

## Artifacts

- `soc_analyst.json` — incidents[], open_count, verdict
- `soc_analyst.md` — human-readable incident report

## SAFE-BY-DEFAULT

Never mutates the repo. gludd_message only sent when `handoff_recipient` is set
and incidents are present and not in check_mode.
