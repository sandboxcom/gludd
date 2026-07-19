# radare2_analyze

Radare2-based reverse engineering role for the `general_ludd.binary_re` collection.

## Description

Automates r2 analysis: disassembly, entropy scanning, string search, and
control-flow graph extraction. Report-only — never mutates the target binary.

## Variables

| Variable | Default | Description |
|---|---|---|
| `r2_path` | `/usr/bin/r2` | Path to radare2 binary |
| `output_dir` | `/tmp/gludd-radare2-analyze` | Artifact output directory |
| `analysis_mode` | `basic` | Analysis depth (basic, full) |
| `enable_disassembly` | `false` | Run disassembly |
| `enable_entropy_scan` | `false` | Run entropy scan |
| `enable_string_search` | `false` | Run string search |
| `enable_cfg_analysis` | `false` | Run CFG analysis |

## Artifacts

- `<output_dir>/radare2_analyze.json` — analysis summary artifact
