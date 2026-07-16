# `general_ludd.security` -- Security Agent Collection

Ansible collection providing agents with security assessment, cryptography,
injection testing, audit frameworks, and adversarial detection capabilities.

## Roles

| Role | Purpose |
|---|---|
| `ssl_cert` | SSL/TLS certificate management, PKI operations, certificate chain validation |
| `hsm_operations` | Hardware Security Module key generation, signing, and lifecycle |
| `sql_injection` | SQL injection detection and exploitation testing |
| `command_injection` | Command injection detection and exploitation testing |
| `prompt_injection` | Prompt injection detection for LLM-based systems |
| `audit_framework` | Security audit framework: compliance checks, reporting, evidence collection |

## Quick start

```yaml
- name: Audit SSL configuration
  hosts: localhost
  vars:
    ssl_audit_enabled: true
    ssl_target_host: example.com
  roles:
    - general_ludd.security.ssl_cert
```

## Related Collections

| Collection | Shared Domain | Cross-Collection Modules |
|---|---|---|
| `general_ludd.binary_re` | Reverse engineering, fuzzing, prompt injection scanning | `prompt_injection_detector.py` (regex/AST detection), `fuzzing_strategies.py` (AFL++/libFuzzer harnesses) |
| `general_ludd.physics` | Applied cryptography math, computer science foundations | `math_identities.py` (modular arithmetic, prime testing, algorithmic complexity), `quantum_computer.py` (post-quantum cryptography, Shor/Grover) |

Use `get_cross_collection_help("vulnerability")` or `get_cross_collection_help("computer_science")` from `physics.plugins.module_utils.cross_collection` to discover all related roles.
