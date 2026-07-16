# general_ludd.binary_re

Ansible collection for binary reverse engineering: analysis, debugging, fuzzing,
disassembly, deobfuscation, and prompt-injection defense.

## Roles (8)

| Role | Purpose |
|------|---------|
| `gdb_analyze` | GDB automation -- breakpoints, stack traces, register dumps |
| `radare2_analyze` | r2-based reversing -- disassembly, entropy scan, string search, CFG |
| `frida_instrument` | Frida hooking/tracing -- function interception, memory scanning |
| `ghidra_analyze` | Ghidra headless -- auto-analysis via `analyzeHeadless`, scripted exports |
| `fuzz_target` | Fuzzing harness -- AFL++/libFuzzer setup, corpus management, crash triage |
| `cyberchef_transform` | CyberChef API -- recipe execution, encoding/decoding/encryption pipelines |
| `deobfuscate` | Deobfuscation -- packing detection, CFG flattening, string deobfuscation |
| `prompt_injection_scan` | Scan binaries/scripts for embedded prompt-injection payloads |

## Knowledge Modules

| Module | Content |
|--------|---------|
| `obfuscation_techniques.py` | Enum of obfuscation techniques and detection heuristics |
| `fuzzing_strategies.py` | Enum of fuzzing strategies with corpus rules and crash triage |
| `prompt_injection_detector.py` | Regex and AST-based detection of prompt-injection payloads |

## Dependencies

- pip: `capstone>=5.0`, `unicorn>=2.0`, `r2pipe>=1.8`, `frida-tools>=12`, `angr>=9.2`, `esprima>=4.0`
- system: `gdb`, `radare2`, `afl++`/`libfuzzer`, `ghidra` (headless)

## Related Collections

| Collection | Shared Domain | Cross-Collection Modules |
|---|---|---|
| `general_ludd.physics` | Entropy math, statistical analysis | `math_modeler.py` (statistics, regression), `math_identities.py` |
| `general_ludd.security` | Vulnerability detection, prompt injection | `prompt_injection`, `command_injection`, `sql_injection` roles |
| `general_ludd.radio` | Signal analysis, protocol decoding | `modulation_schemes.py`, `protocol_decoder.py` for binary protocol RE |

Use `get_cross_collection_help("reverse_engineering")` from `physics.plugins.module_utils.cross_collection` to discover all related roles.
