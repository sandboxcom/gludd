# validate_scenarios — validate E2E scenarios against real-world usage

Cross-references generated scenarios against web research (GitHub issues,
Stack Overflow, blogs) via the existing ResearcherAgent through the daemon
API. Prunes implausible scenarios and scores confidence per scenario based
on source corroboration.

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `scenarios_artifact` | `""` | Path to scenarios.json from generate_scenarios |
| `artifact_dir` | `/tmp/gludd-e2e-test-gen` | Input/output directory (expects scenarios.json) |
| `daemon_url` | `http://localhost:8000` | Daemon URL |
| `confidence_threshold` | `0.4` | Minimum confidence to retain a scenario |

## Artifact

`validated_scenarios.json`:
```json
{
  "valid": [{"name": "crud_lifecycle", "confidence": 0.85, "source_urls": [...]}],
  "discarded": [{"name": "daemon_restart", "reason": "no corroborating sources found"}],
  "research_queries": ["... how is create_resource used in production ..."],
  "sources_consulted": 42,
  "status": "completed"
}
```
