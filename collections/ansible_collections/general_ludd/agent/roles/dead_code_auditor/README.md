# dead_code_auditor

Surfaces unreferenced code by composing an existing completion/coverage audit artifact.

## Key design: composition, not re-detection

This role does NOT scan the codebase. It slurps a `completion_audit_artifact` (JSON
produced by another role) and surfaces its `unreferenced`, `uncovered`, `dead_code`,
or `unused` entries. Optional vulture integration supplements the audit artifact.

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `completion_audit_artifact` | `""` | Path to completion audit JSON (required) |
| `enable_vulture` | `false` | Also run vulture to supplement |
| `vulture_output_override` | `""` | Path to pre-generated vulture JSON (skips command) |
| `artifact_dir` | `/tmp/gludd-dead-code-auditor` | Output directory |
| `daemon_url` | `http://localhost:8000` | Daemon URL |
| `psk` | `""` | Pre-shared key |

## Artifact

`dead_code_auditor.json`:

```json
{
  "role": "dead_code_auditor",
  "verdict": "unreferenced_found | clean",
  "source": "completion_audit | completion_audit+vulture",
  "unreferenced": [
    {"name": "foo", "kind": "function", "file": "src/foo.py", "source": "completion_audit"}
  ],
  "unreferenced_count": 1
}
```
