# deobfuscate

Deobfuscation analysis role for the `general_ludd.binary_re` collection.

## Description

Detects obfuscation techniques in binaries: packing, control-flow flattening,
string encryption, and opaque predicates. Report-only — never mutates the
target binary.

## Variables

| Variable | Default | Description |
|---|---|---|
| `output_dir` | `/tmp/gludd-deobfuscate` | Artifact output directory |
| `enable_packing_detection` | `false` | Detect packers |
| `enable_cfg_flattening_detection` | `false` | Detect CFG flattening |
| `enable_string_deobfuscation` | `false` | Deobfuscate strings |
| `enable_opaque_predicate_detection` | `false` | Detect opaque predicates |

## Artifacts

- `<output_dir>/deobfuscate.json` — deobfuscation summary artifact
