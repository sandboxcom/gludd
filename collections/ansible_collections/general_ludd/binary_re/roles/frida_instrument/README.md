# frida_instrument

Frida dynamic instrumentation role for the `general_ludd.binary_re` collection.

## Description

Automates Frida hooking and tracing: function interception, memory scanning,
SSL pinning bypass, and runtime tracing. Report-only — never mutates the target.

## Variables

| Variable | Default | Description |
|---|---|---|
| `frida_tools_path` | `/usr/local/bin` | Path to Frida tools |
| `target_binary` | `""` | Target binary path |
| `process_spec` | `""` | Process name or PID to attach (defaults to target_binary) |
| `output_dir` | `/tmp/gludd-frida-instrument` | Artifact output directory |
| `analysis_mode` | `basic` | Analysis depth (basic, full) |
| `enable_function_interception` | `false` | Hook and intercept functions |
| `enable_memory_scanning` | `false` | Scan process memory |
| `enable_ssl_pinning_bypass` | `false` | Bypass SSL certificate pinning |
| `enable_tracing` | `false` | Runtime tracing via Stalker |
| `frida_intercept_targets` | `open,read,write` | Comma-separated hook symbols |
| `frida_memory_pattern` | `""` | Hex byte pattern e.g. `AA BB CC DD` |
| `frida_memory_hit_cap` | `1000` | Max matches before scan stops |
| `frida_trace_ms` | `5000` | Stalker trace duration (ms) |
| `frida_trace_ret` | `false` | Trace return events |
| `frida_trace_exec` | `false` | Trace exec events (noisy) |
| `frida_trace_call_cap` | `50000` | Max retained trace events |

## Artifacts

- `<output_dir>/frida_instrument.json` — instrumentation summary artifact
