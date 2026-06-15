# issue_reporter

Converts detected anomalies/findings from a `log_analyst` artifact into structured issues.

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `anomaly_artifact` | `""` | Path to log_analyst.json or compatible artifact (required) |
| `write_back` | `false` | When true, emits issue_created messages; when false, drafts |
| `handoff_recipient` | `""` | Recipient for gludd_message notifications |
| `artifact_dir` | `/tmp/gludd-issue-reporter` | Output directory |
| `daemon_url` | `http://localhost:8000` | Daemon URL |
| `psk` | `""` | Pre-shared key |

## Artifact

`issue_reporter.json`:

```json
{
  "role": "issue_reporter",
  "verdict": "issues_drafted | issues_created | no_issues",
  "issues": [
    {
      "title": "[HIGH] error_density: error_line_density_exceeded",
      "body": "Anomaly detected by log_analyst...",
      "severity": "high",
      "created": false
    }
  ],
  "issue_count": 1
}
```
