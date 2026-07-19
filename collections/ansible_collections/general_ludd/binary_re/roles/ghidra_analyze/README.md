# ghidra_analyze

Ghidra headless analysis role for the `general_ludd.binary_re` collection.

## Description

Automates Ghidra reverse engineering: headless auto-analysis via
`analyzeHeadless`, scripted exports, and function signature extraction.
Report-only — never mutates the target binary.

## Variables

| Variable | Default | Description |
|---|---|---|
| `ghidra_path` | `/opt/ghidra` | Path to Ghidra installation |
| `output_dir` | `/tmp/gludd-ghidra-analyze` | Artifact output directory |
| `analysis_mode` | `basic` | Analysis depth (basic, full) |
| `enable_headless_analysis` | `false` | Run headless auto-analysis |
| `enable_scripted_export` | `false` | Run scripted export |
| `enable_function_signature` | `false` | Extract function signatures |

## Artifacts

- `<output_dir>/ghidra_analyze.json` — analysis summary artifact
