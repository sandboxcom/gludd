# enhancement_auditor

Proposes ranked enhancements based on gludd_facts history failure signals. Heuristic fallback when model is disabled: low success_rate → reliability, high backlog → throughput, failures → observability, high tokens → cost optimization. Proposals ranked by score (impact × 10 / effort).

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `target_area` | `"general"` | Target area for proposals |
| `max_proposals` | `5` | Maximum number of proposals |
| `enable_model_call` | `false` | Enable model analysis |
| `model_output_override` | `""` | Canned JSON list of proposals (for testing) |
| `handoff_recipient` | `""` | Recipient for gludd_message notification |
| `artifact_dir` | `/tmp/gludd-enhancement-auditor` | Output directory |
| `daemon_url` | `http://localhost:8000` | Daemon URL |
| `psk` | `""` | Pre-shared key |

## Artifact

`enhancement_auditor.json`:
```json
{
  "role": "enhancement_auditor",
  "status": "completed",
  "proposals": [{"title": "...", "rationale": "...", "impact": 9, "effort": 3, "score": 30.0}],
  "verdict": "has_proposals"
}
```
