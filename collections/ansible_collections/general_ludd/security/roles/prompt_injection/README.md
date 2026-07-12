# prompt_injection

LLM prompt injection awareness role for the `general_ludd.agent` collection.

## Description

Analyzes LLM prompt injection attack vectors (direct, indirect, jailbreak),
compiles defense strategies across four categories (input sanitization, output
filtering, adversarial detection, system design), and catalogs defensive tools.
**REPORT-ONLY** — never mutates the repo.

## Variables

| Variable | Default | Description |
|---|---|---|
| `artifact_dir` | `/tmp/gludd-prompt-injection` | Artifact output path |
| `target_path` | `"."` | Target path (for report metadata only) |
| `enable_scan` | `false` | Always false — read-only analysis role |
| `enable_git_push` | `false` | Always false — report-only |
| `defensive_tools` | 6-tool catalog | Defensive tool inventory (name, type, description) |

## Artifacts

- `<artifact_dir>/prompt_injection.json` — vector analysis, tool catalog, remediation categories
- `<artifact_dir>/prompt_injection.md` — human-readable report with attack vectors, tools, and strategies
