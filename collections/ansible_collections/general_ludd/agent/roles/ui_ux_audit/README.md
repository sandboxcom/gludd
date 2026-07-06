# ui_ux_audit

Accessibility and UX audit for HTML presentations (reveal.js or any HTML). **Report-only** — never mutates the repo.

Checks:
- **a11y**: missing alt text on `<img>`, ARIA labels on interactive elements, semantic HTML (nav/main/header/footer), contrast heuristics
- **UX**: readable fonts, clear navigation, responsive layout (via AI model call)
- Uses `gludd_facts` for system context, `gludd_model_call` for AI analysis

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `html_path` | `""` | Path to HTML presentation file |
| `html_content_override` | `""` | Canned HTML for testing |
| `artifact_dir` | `/tmp/gludd-ui-ux-audit` | Output directory |
| `daemon_url` | `http://localhost:8000` | Daemon URL |
| `psk` | `""` | Pre-shared key |
| `model_profile` | `""` | Model profile for AI analysis |
| `enable_model_call` | `false` | Enable AI-powered UX analysis |

## Artifacts

`ui_ux_audit.json`:
```json
{
  "role": "ui_ux_audit",
  "status": "completed",
  "score": 75,
  "verdict": "needs_work",
  "a11y_checks": {"alt_text_missing": 2, "aria_missing": 1, ...},
  "ux_findings": ["font-size too small on code blocks", ...]
}
```

## Verdict

- `good` — score >= 80
- `needs_work` — score 50–79
- `poor` — score < 50
