# standup_report

Daily standup artifact from live facts + message inbox.

## What it surfaces

- **done[]**: completed work summary from `facts.history` (total_runs, success_rate, failures)
- **in_progress[]**: active/queued jobs from `facts.work`
- **blockers[]**: messages from `gludd_message` inbox (blocker messages surfaced from /api/messages)

## Variables

| Variable | Default | Description |
|---|---|---|
| `artifact_dir` | `/tmp/gludd-standup-report` | Output directory |
| `daemon_url` | `http://localhost:8000` | Daemon base URL |
| `psk` | `""` | Pre-shared key |

## Artifacts

- `standup_report.json` — done[], in_progress[], blockers[], history, message_count
- `standup_report.md` — markdown standup card
