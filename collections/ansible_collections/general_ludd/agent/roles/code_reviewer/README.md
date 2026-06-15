# code_reviewer

Structured correctness/style/risk review of a diff or repo path. NOT a security review. Real grep-based heuristics: bare `except:`, `print(`, `# type: ignore`, oversized hunks, TODO/FIXME comments. Model narrative is gated behind `enable_model_call:false`.

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `diff_path` | `""` | Path to diff file (takes priority) |
| `repo_path` | `"."` | Repo path to scan (fallback) |
| `enable_model_call` | `false` | Enable model narrative call |
| `model_output_override` | `""` | Canned model output (for testing) |
| `artifact_dir` | `/tmp/gludd-code-reviewer` | Output directory |
| `daemon_url` | `http://localhost:8000` | Daemon URL |
| `psk` | `""` | Pre-shared key |

## Artifact

`code_reviewer.json`:
```json
{
  "role": "code_reviewer",
  "status": "completed",
  "findings": [{"category": "correctness", "severity": "high", "rule": "CR001", "location": "...", "description": "..."}],
  "verdict": "request_changes"
}
```

## Verdict

- `approve` — no findings
- `comment` — low/medium findings only
- `request_changes` — any high-severity finding
