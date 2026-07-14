# frida_instrument

Frida dynamic instrumentation role for the `general_ludd.binary_re` collection.

## Description

Automates Frida hooking and tracing: function interception, memory scanning,
SSL pinning bypass, and runtime tracing. Report-only — never mutates the target.

## Variables

| Variable | Default | Description |
|---|---|---|
| `frida_tools_path` | `/usr/local/bin` | Path to Frida tools |
| `output_dir` | `/tmp/gludd-frida-instrument` | Artifact output directory |
| `analysis_mode` | `basic` | Analysis depth (basic, full) |
| `enable_function_interception` | `false` | Hook and intercept functions |
| `enable_memory_scanning` | `false` | Scan process memory |
| `enable_ssl_pinning_bypass` | `false` | Bypass SSL certificate pinning |
| `enable_tracing` | `false` | Runtime tracing |

## Artifacts

- `<output_dir>/frida_instrument.json` — instrumentation summary artifact
