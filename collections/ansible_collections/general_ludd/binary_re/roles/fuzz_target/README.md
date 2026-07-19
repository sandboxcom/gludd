# fuzz_target

Fuzzing harness role for the `general_ludd.binary_re` collection.

## Description

Sets up and runs fuzzing: AFL++/libFuzzer harness configuration, corpus
management, coverage-guided and mutation fuzzing, and crash triage.
Report-only.

## Variables

| Variable | Default | Description |
|---|---|---|
| `fuzzer` | `afl++` | Fuzzer engine (afl++, libfuzzer) |
| `fuzzer_path` | `/usr/local/bin/afl-fuzz` | Path to fuzzer binary |
| `output_dir` | `/tmp/gludd-fuzz-target` | Artifact output directory |
| `corpus_dir` | `/tmp/gludd-fuzz-corpus` | Corpus directory |
| `enable_coverage_guided` | `false` | Run coverage-guided fuzzing |
| `enable_mutation` | `false` | Run mutation fuzzing |
| `enable_crash_triage` | `false` | Triage crashes |
| `crash_retention_days` | `30` | Days to retain crash data |

## Artifacts

- `<output_dir>/fuzz_target.json` — fuzzing summary artifact
