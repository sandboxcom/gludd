# prompt_injection_scan

Prompt injection scanning role for the `general_ludd.binary_re` collection.

## Description

Scans binaries and scripts for embedded prompt-injection payloads across
multiple encoding formats: hex, ASCII, JS AST analysis, and base64.
Report-only — never mutates the target.

## Variables

| Variable | Default | Description |
|---|---|---|
| `target_path` | `"."` | Target file or directory to scan |
| `output_dir` | `/tmp/gludd-prompt-injection-scan` | Artifact output directory |
| `enable_hex_scan` | `false` | Scan for hex-encoded injection strings |
| `enable_ascii_scan` | `false` | Scan for ASCII injection strings |
| `enable_js_ast_scan` | `false` | AST-based JS injection analysis |
| `enable_base64_scan` | `false` | Scan for base64-encoded injection strings |
| `severity_threshold` | `medium` | Minimum severity to report (low, medium, high, critical) |

## Artifacts

- `<output_dir>/prompt_injection_scan.json` — scan summary artifact
