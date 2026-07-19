# gdb_analyze

GDB automation role for the `general_ludd.binary_re` collection.

## Description

Automates GDB debugging: breakpoint management, stack trace analysis,
register dumps, and scripted analysis via the Python GDB API. Report-only
— never mutates the target binary.

## Variables

| Variable | Default | Description |
|---|---|---|
| `gdb_path` | `/usr/bin/gdb` | Path to GDB binary |
| `output_dir` | `/tmp/gludd-gdb-analyze` | Artifact output directory |
| `analysis_mode` | `basic` | Analysis depth (basic, full) |
| `enable_breakpoints` | `false` | Run breakpoint analysis |
| `enable_stack_trace` | `false` | Run stack trace analysis |
| `enable_register_dump` | `false` | Dump CPU registers |
| `enable_scripted_analysis` | `false` | Run custom GDB scripts |

## Artifacts

- `<output_dir>/gdb_analyze.json` — analysis summary artifact
