# Feature: Binary Reverse Engineering Collection

**Status: COMPLETE** | **Created: 2026-07-14** | **Completed: 2026-08-03** | **Target: v0.1.0-beta.3**

## 1. Overview

Ansible collection `general_ludd.binary_re` providing roles for binary analysis,
debugging, fuzzing, disassembly, deobfuscation, and prompt-injection defense.
Report-only roles (audit + guidance); Python modules under `plugins/module_utils/`
hold deep domain knowledge.

## 2. Roles (8)

| Role | Purpose |
|------|---------|
| `gdb_analyze` | GDB automation — breakpoints, stack traces, register dumps, Python GDB API |
| `radare2_analyze` | r2-based reversing — disassembly, entropy scan, string search, CFG |
| `frida_instrument` | Frida hooking/tracing — function interception, memory scanning, SSL pinning bypass |
| `ghidra_analyze` | Ghidra headless — auto-analysis via `analyzeHeadless`, scripted exports |
| `fuzz_target` | Fuzzing harness — AFL++/libFuzzer setup, corpus management, crash triage |
| `cyberchef_transform` | CyberChef API — recipe execution, encoding/decoding/encryption pipelines |
| `deobfuscate` | Deobfuscation — packing detection, CFG flattening, string deobfuscation |
| `prompt_injection_scan` | Scan binaries/scripts for embedded prompt-injection payloads (hex, JS, base64) |

Each role: `tasks/main.yml`, `defaults/main.yml`, `meta/main.yml`, `vars/main.yml`, `README.md`.

## 3. Knowledge Modules

| Module | Content |
|--------|---------|
| `obfuscation_techniques.py` | Enum: PACKING, VIRTUALIZATION, CFG_FLATTENING, STRING_ENCRYPTION, ANTI_DEBUG, OPAQUE_PREDICATES. Detection heuristics per technique. |
| `fuzzing_strategies.py` | Enum: MUTATION, GENERATION, COVERAGE_GUIDED, SYMBOLIC_CONCOLIC. Corpus rules, crash triage, seed selection. |
| `prompt_injection_detector.py` | Regex for prompt-injection strings in ASCII/UTF-16/hex. AST-based JS analysis for eval-obfuscated injection chains. Severity scoring. |

## 4. Implementation Plan

| Phase | Scope | Status |
|-------|-------|--------|
| A | Scaffold: galaxy.yml, dir layout, meta ×8. gdb_analyze + radare2_analyze tasks drafted. | COMPLETE |
| B | fuzzing_strategies.py + obfuscation_techniques.py modules. fuzz_target + deobfuscate + frida_instrument + ghidra_analyze. | COMPLETE |
| C | prompt_injection_detector.py + prompt_injection_scan. cyberchef_transform. | COMPLETE |
| D | Molecule tests for all 8 roles. Pytest for 3 modules. Integration tests. | COMPLETE |

**Evidence:** 8 roles (ghidra_analyze, gdb_analyze, radare2_analyze, frida_instrument, deobfuscate, fuzz_target, cyberchef_transform, prompt_injection_scan) with tasks/defaults/meta/vars/README. 3 knowledge modules (`obfuscation_techniques.py`, `fuzzing_strategies.py`, `prompt_injection_detector.py`). 12 model_capabilities + 8 role_capabilities in `galaxy.yml` (19 tags). 6 parser modules (disassembler, elf_parser, macho_parser, pe_analyzer, entropy_analyzer, pattern_database, yara_generator). 16 test files, **503 tests PASS**. Collection wired into CapabilityRegistry (14 capability-router verification tests PASS). Molecule scenario for collection-level validation.

## 5. Files

```text
collections/ansible_collections/general_ludd/binary_re/
├── galaxy.yml, README.md
├── plugins/module_utils/
│   ├── obfuscation_techniques.py
│   ├── fuzzing_strategies.py
│   └── prompt_injection_detector.py
├── roles/
│   ├── gdb_analyze/{tasks,defaults}/main.yml + README.md
│   ├── radare2_analyze/{...}
│   ├── frida_instrument/{...}
│   ├── ghidra_analyze/{...}
│   ├── fuzz_target/{...}
│   ├── cyberchef_transform/{...}
│   ├── deobfuscate/{...}
│   └── prompt_injection_scan/{...}
└── tests/unit/test_obfuscation_techniques.py, test_fuzzing_strategies.py, test_prompt_injection_detector.py
```

## 6. Dependencies

pip: `capstone>=5.0`, `unicorn>=2.0`, `r2pipe>=1.8`, `frida-tools>=12`, `angr>=9.2`, `esprima>=4.0`
system: `gdb`, `radare2`, `afl++`/`libfuzzer`, `ghidra` (headless)

## 7. Test Plan

- Molecule per role: ansible syntax, variable expansion, artifact output
- pytest: knowledge modules (enum values, detection heuristics, regex/AST analysis)
- Gate: lint + typecheck + collect-check on binary_re files
