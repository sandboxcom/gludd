# Security Roles — Overview

Six Ansible roles in the `general_ludd.security` collection covering the full security
lifecycle: certificate management, hardware-backed key operations, compliance auditing,
and injection attack detection/remediation (SQL, command, LLM prompt).

## Role Reference

| Role | FQCN | Category | Status |
|------|------|----------|--------|
| `ssl_cert` | `general_ludd.security.ssl_cert` | Certificate lifecycle | Implemented |
| `hsm_operations` | `general_ludd.security.hsm_operations` | HSM / smartcard | Implemented |
| `audit_framework` | `general_ludd.security.audit_framework` | Compliance auditing | Scaffolded |
| `sql_injection` | `general_ludd.security.sql_injection` | SQLi attack/remediate/audit | Scaffolded |
| `command_injection` | `general_ludd.security.command_injection` | Command injection | Scaffolded |
| `prompt_injection` | `general_ludd.security.prompt_injection` | LLM prompt injection | Scaffolded |

### ssl\_cert — SSL/TLS Certificate Management

Full certificate lifecycle: mint self-signed certs, analyze existing certs (chain
validation, OCSP, trust store), evaluate algorithm strength, map CA jurisdiction,
parse ASN.1/OIDs, and enforce compliance profiles (FIPS 140, SOC2, HIPAA, PCI-DSS,
NIST SP 800-131A, CABF Baseline).

Data files: `vars/compliance.yml` (6 profiles), `vars/algorithms.yml` (RSA, ECDSA,
EdDSA, DSA, DH, post-quantum), `vars/ca_jurisdictions.yml` (18 CAs).

### hsm\_operations — HSM and Smartcard Operations

Detect HSMs (SoftHSM, YubiHSM, AWS CloudHSM, Azure HSM, nCipher, Utimaco, Thales),
manage keys (generate/import/export/list/delete), sign CSRs and data, and operate
smartcards via PC/SC (ATR parse, PIN management, certificate import/export, raw APDU).

Designed as a sub-role callable from `ssl_cert` via `include_role`. Private keys
never leave the HSM; PINs and secret material are `no_log` enforced throughout.

### audit\_framework — Compliance Auditing

Automated compliance auditing against security standards. Integrates with the
compliance profiles from `ssl_cert` and extends them with runtime checks for
system hardening, access control, encryption standards, and audit-log completeness.

Uses SearX to research current framework versions and updates before each run,
ensuring checks are against the latest revision of each standard.

### sql\_injection — SQL Injection Attack, Audit, and Remediation

Detects SQL injection vectors in source code via pattern matching across Python,
Go, and JavaScript codebases. Covers classic injection types (UNION, error-based,
blind boolean, blind time, stacked queries, out-of-band) plus second-order
injection and stored XSS→SQLi chains.

Generates parameterized query examples, ORM-safe alternatives, input validation
rules, and WAF rules. Tool-aware: sqlmap, jSQL, NoSQLMap, Burp Suite, Bandit,
Semgrep, Gitleaks. Report-only — never mutates source files.

### command\_injection — Command Injection Detection

Detects command injection vectors in source code, CI/CD pipelines, and system
configuration. Covers shell metacharacter injection, argument injection via
subprocess calls, template-based injection in IaC (Ansible, Terraform), and
eval/exec-style code injection in interpreted languages.

Generates safe alternatives (argument arrays vs. string concatenation, shlex.quote,
subprocess.run with list args). Tool-aware: Semgrep, Bandit, ShellCheck, Docker
Bench Security, kube-bench.

### prompt\_injection — LLM Prompt Injection Detection

Detects prompt injection vectors in systems that accept user input for LLM
prompts. Covers direct injection (user input in system context), indirect
injection (data poisoning via external sources), jailbreak patterns, and
context-window overflow attacks.

Generates mitigation strategies: input sanitization, output filtering, role-based
context separation, watermarking, and canary tokens. Tool-aware: Garak, LLM Guard,
Guardrails AI, NeMo Guardrails, Promptfoo.

## Interoperability Matrix

```text
                  ssl_cert   hsm_ops   audit_fw  sql_inj   cmd_inj   prompt_inj
ssl_cert            ─         ──▸       ▸         ·         ·         ·
hsm_operations      ◂──       ─         ▸         ·         ·         ·
audit_framework     ◂─        ◂─        ─         ▸         ▸         ▸
sql_injection       ·         ·         ◂─        ─         ·         ·
command_injection   ·         ·         ◂─        ·         ─         ·
prompt_injection    ·         ·         ◂─        ·         ·         ─
```

Legend: `─▸` caller, `◂─` callee, `▸` invokes, `·` no direct relationship.

### Key integration paths

1. **SSL → HSM** (`ssl_cert` → `hsm_operations`): ssl\_cert delegates key generation
   and signing to hsm\_operations when a PKCS#11 module is detected.
2. **Audit → All** (`audit_framework` → injection roles): audit\_framework dispatches
   sql\_injection, command\_injection, and prompt\_injection as sub-audits during a
   comprehensive compliance run.
3. **Audit → SSL** (`audit_framework` → `ssl_cert`): audit\_framework invokes
   ssl\_cert compliance checks to evaluate TLS configuration against standards.

## SearX Integration

All six roles can use a SearX managed server instance for framework and CVE
research without making direct internet calls. The SearX integration (Wave 34)
provides:

- **Web search** for current compliance framework versions (PCI-DSS, SOC2,
  NIST SP 800-series) before each audit run.
- **CVE lookup** for known vulnerabilities in detected software versions.
- **Tool update checks** — verifies that referenced tools (sqlmap, Semgrep,
  Bandit, Garak, etc.) are at their latest stable release.
- **CA jurisdiction data refresh** — SearX can research current CA ownership
  and jurisdiction for the ssl\_cert role's `ca_jurisdictions.yml`.

Role variable to enable SearX:
```yaml
security_roles__searx_enabled: true
security_roles__searx_url: "http://localhost:8888"
```

## Tool Awareness Matrix

Which external security tools each role knows about and can coordinate with:

| Tool | ssl_cert | hsm_ops | audit_fw | sql_inj | cmd_inj | prompt_inj |
|------|----------|---------|----------|---------|---------|------------|
| openssl / libressl | ✓ | | | | | |
| certbot / acme.sh | ✓ | | | | | |
| pkcs11-tool | | ✓ | | | | |
| opensc-tool / pcsc_scan | | ✓ | | | | |
| yubico-piv-tool | | ✓ | | | | |
| sqlmap | | | | ✓ | | |
| jSQL / NoSQLMap | | | | ✓ | | |
| Burp Suite | | | | ✓ | ✓ | |
| Bandit | | | ✓ | ✓ | | |
| Semgrep | | | ✓ | ✓ | ✓ | |
| Gitleaks | | | ✓ | | | |
| ShellCheck | | | | | ✓ | |
| Docker Bench Security | | | | | ✓ | |
| kube-bench | | | | | ✓ | |
| Garak | | | | | | ✓ |
| LLM Guard | | | | | | ✓ |
| Guardrails AI / NeMo | | | | | | ✓ |
| Promptfoo | | | | | | ✓ |
| Lynis | | | ✓ | | | |
| OpenSCAP | | | ✓ | | | |
| Trivy | | | ✓ | | | |

## Sample Usage Flow — Web App Security Audit

Below is the end-to-end flow for auditing a web application covering all six
roles. This can be orchestrated as a single playbook or dispatched as parallel
sub-agent tasks.

```yaml
---
- name: Full web app security audit
  hosts: localhost
  gather_facts: true
  vars:
    app_source_dir: /path/to/webapp
    app_url: https://webapp.example.com
    app_port: 443
    searx_url: "http://localhost:8888"
    daemon_url: "http://localhost:8000"

  tasks:
    # 1. SSL/TLS posture
    - name: Analyze SSL certificate and compliance
      ansible.builtin.include_role:
        name: general_ludd.security.ssl_cert
      vars:
        ssl_cert_remote_host: "{{ app_url }}"
        ssl_cert_remote_port: "{{ app_port }}"
        ssl_cert_compliance_profile: pcidss
        ssl_cert_ca_jurisdiction_lookup: true
        ssl_cert_searx_url: "{{ searx_url }}"

    # 2. HSM key protection audit (if HSM available)
    - name: Detect and audit HSM
      ansible.builtin.include_role:
        name: general_ludd.security.hsm_operations
      vars:
        hsm_operations__mode: detect
      ignore_errors: true

    # 3. SQL injection scan
    - name: Scan for SQL injection vectors
      ansible.builtin.include_role:
        name: general_ludd.security.sql_injection
      vars:
        scan_target: "{{ app_source_dir }}"
        scan_languages: [python, javascript]
        web_log_path: "/var/log/nginx/access.log"
        fail_on_critical: true
        fail_on_high: true

    # 4. Command injection scan
    - name: Scan for command injection vectors
      ansible.builtin.include_role:
        name: general_ludd.security.command_injection
      vars:
        scan_target: "{{ app_source_dir }}"
        scan_languages: [python, javascript]
        scan_ci_configs: true
        fail_on_critical: true

    # 5. Prompt injection audit (if LLM integration detected)
    - name: Audit LLM prompt injection surface
      ansible.builtin.include_role:
        name: general_ludd.security.prompt_injection
      vars:
        scan_target: "{{ app_source_dir }}"
        scan_prompt_templates: true
        scan_api_endpoints: true
        scan_external_data_sources: true

    # 6. Comprehensive compliance audit
    - name: Run compliance framework audit
      ansible.builtin.include_role:
        name: general_ludd.security.audit_framework
      vars:
        audit_standards: [pcidss, soc2, nist_sp800_53]
        audit_searx_url: "{{ searx_url }}"
        audit_include_ssl: true
        audit_include_injection: true

    # 7. Aggregate findings
    - name: Read all artifacts
      ansible.builtin.slurp:
        src: "/tmp/gludd-{{ item }}/"
      loop:
        - ssl-cert
        - hsm-operations
        - sql-injection
        - command-injection
        - prompt-injection
        - audit-framework

    # 8. Handoff to remediation
    - name: Send findings to remediation queue
      general_ludd.agent.gludd_message:
        recipient: remediation_dispatch
        subject: "Security audit complete for {{ app_url }}"
        body: "{{ findings | to_nice_json }}"
```

### Expected artifacts per role

| Role | Artifact directory | Key output |
|------|--------------------|------------|
| ssl_cert | `/tmp/gludd-ssl-cert/` | cert-analysis.json, compliance-report.json, ca-jurisdiction.json |
| hsm_operations | `/tmp/gludd-hsm-operations/` | hsm_detect.json, hsm_key_ops.json |
| sql_injection | `/tmp/gludd-sql-injection/` | findings.json (by file+line), remediation.md |
| command_injection | `/tmp/gludd-command-injection/` | findings.json, safe-alternatives.yml |
| prompt_injection | `/tmp/gludd-prompt-injection/` | findings.json, mitigations.yml |
| audit_framework | `/tmp/gludd-audit-framework/` | compliance-report.json, gap-analysis.md |

## Security Considerations

- **Never auto-patch**: All injection roles are report-only by default
  (`enable_auto_patch: false`). Remediation output is advisory.
- **no_log enforcement**: HSM PINs, private keys, and credential material use
  `no_log: true` on every task.
- **SearX isolation**: Framework research goes through the managed SearX
  instance, not direct internet calls — audit traffic is observable.
- **Fail-closed**: `fail_on_critical: true` and `fail_on_high: true` by
  default — gate blocks deployment if critical findings exist.
- **Artifact immutability**: All findings are written to JSON artifacts in
  `/tmp/gludd-*` directories. Artifacts are auditable and can be consumed by
  downstream remediation or reporting roles.
