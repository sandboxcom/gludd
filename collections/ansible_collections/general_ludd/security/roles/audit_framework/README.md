# audit_framework

`general_ludd.security.audit_framework` — comprehensive audit framework knowledge and
execution role for the `general_ludd.agent` collection. Designed as a sub-role
callable from compliance and governance playbooks via `include_role`.

## Description

Provides structured compliance data and audit execution across 8 frameworks.
Supports five capability areas:

1. **Framework Knowledge** — structured data for PCI DSS 4.0, FIPS 140-3, SOC 2,
   HIPAA, FedRAMP, ISO 27001:2022, NIST 800-53 Rev 5, and GDPR requirements,
   controls, and mappings
2. **Audit Execution** — run audit commands against targets (AWS, Azure, GCP,
   on-prem, containers, code repositories), collect evidence, and parse results
   into structured findings
3. **SearX Integration** — query SearX metasearch to check for framework updates,
   new advisories, and regulatory changes without direct internet access
4. **Compliance Mapping** — gap analysis between framework requirements and
   audit results; cross-framework control mapping for multi-standard environments
5. **Tool Recommendations** — suggest the appropriate audit tool per target
   type and framework (OpenSCAP, Lynis, Trivy, Chef InSpec, Prowler,
   ScoutSuite, CIS-CAT, Wazuh, Falco, kube-bench)

`gludd_model_call` is used for AI-assisted gap analysis and remediation
planning. All sensitive operations are `no_log` enforced.

## Variables

| Variable | Default | Description |
|---|---|---|
| `audit_framework__artifact_dir` | `/tmp/gludd-audit-framework` | Artifact output directory |
| `audit_framework__daemon_url` | `http://localhost:8000` | Daemon URL for model calls |
| `audit_framework__psk` | `""` | Pre-shared key (no_log) |
| `audit_framework__mode` | `lookup` | Operation mode (see Modes table) |
| `audit_framework__framework` | `""` | Target framework: pci_dss_4, fips_140_3, soc2, hipaa, fedramp, iso_27001, nist_800_53, gdpr |
| `audit_framework__filter_category` | `""` | Filter requirements by category |
| `audit_framework__filter_severity` | `""` | Filter findings by severity |
| `audit_framework__enable_model_call` | `false` | Enable AI-assisted gap analysis |
| `audit_framework__model_profile` | `""` | Model profile for AI calls |
| `audit_framework__searx_url` | `http://localhost:8888` | SearX instance URL for framework updates |
| `audit_framework__search_query` | `""` | Custom SearX search query |
| `audit_framework__auto_update` | `false` | Auto-update framework data from SearX |
| `audit_framework__target` | `""` | Audit target (hostname, account ID, repo path) |
| `audit_framework__audit_scope` | `[]` | List of requirement IDs to audit |
| `audit_framework__evidence_path` | `""` | Path for collected audit evidence |
| `audit_framework__check_commands` | `[]` | Commands to run per control |
| `audit_framework__spec_path` | `""` | Path to Chef InSpec profile or compliance spec |
| `audit_framework__audit_results` | `""` | Pre-existing audit results to map |
| `audit_framework__target_type` | `""` | Target type: aws, azure, gcp, on_prem, container, code_repo |
| `audit_framework__output_format` | `json` | Output format: json, yaml, csv, markdown |

## Modes

| Mode | Description | Required vars |
|---|---|---|
| `lookup` | Look up framework requirements and control details | `framework` |
| `searx_update` | Query SearX for framework updates and advisories | `searx_url`, `search_query` |
| `audit_execute` | Run audit check commands against a target | `target`, `target_type`, `check_commands` or `spec_path` |
| `audit_parse` | Parse raw audit output into structured findings | `audit_results` |
| `remediate` | Generate remediation guidance from audit findings | `framework`, `audit_results`, `enable_model_call` (recommended) |
| `tool_recommend` | Recommend audit tools for target/framework combination | `target_type`, `framework` |

## Framework Data Files

| File | Framework |
|---|---|
| `vars/pci_dss_4.yml` | PCI DSS 4.0 requirements and controls |
| `vars/fips_140_3.yml` | FIPS 140-3 security levels and requirements |
| `vars/soc2.yml` | SOC 2 trust service criteria (TSC) |
| `vars/hipaa.yml` | HIPAA Security Rule safeguards |
| `vars/fedramp.yml` | FedRAMP High/Moderate/Low control baselines |
| `vars/iso_27001.yml` | ISO 27001:2022 Annex A controls |
| `vars/nist_800_53.yml` | NIST 800-53 Rev 5 control families |
| `vars/gdpr.yml` | GDPR articles and data protection requirements |

## Supported Audit Tools

| Tool | Best for |
|---|---|
| OpenSCAP | Linux system hardening (RHEL, CentOS, Fedora) |
| Lynis | Linux/macOS security auditing |
| Trivy | Container image and filesystem vulnerability scanning |
| Chef InSpec | Infrastructure compliance-as-code |
| Prowler | AWS security assessment (CIS, PCI, HIPAA, GDPR) |
| ScoutSuite | Multi-cloud (AWS, Azure, GCP) security posture |
| CIS-CAT | CIS Benchmark assessments |
| Wazuh | File integrity monitoring and intrusion detection |
| Falco | Container and Kubernetes runtime security |
| kube-bench | Kubernetes CIS Benchmark compliance |

## Artifacts

| File | Mode |
|---|---|
| `<artifact_dir>/framework_lookup.json` | lookup |
| `<artifact_dir>/searx_update.json` | searx_update |
| `<artifact_dir>/audit_findings.json` | audit_execute |
| `<artifact_dir>/parsed_results.json` | audit_parse |
| `<artifact_dir>/remediation_plan.json` | remediate |
| `<artifact_dir>/tool_recommendations.json` | tool_recommend |

## Usage

```yaml
- name: Look up PCI DSS 4.0 requirements
  ansible.builtin.include_role:
    name: general_ludd.security.audit_framework
  vars:
    audit_framework__mode: lookup
    audit_framework__framework: pci_dss_4
    audit_framework__filter_category: access_control

- name: Run AWS audit against CIS controls
  ansible.builtin.include_role:
    name: general_ludd.security.audit_framework
  vars:
    audit_framework__mode: audit_execute
    audit_framework__target: "123456789012"
    audit_framework__target_type: aws
    audit_framework__framework: nist_800_53
    audit_framework__audit_scope:
      - AC-1
      - AC-2
      - IA-1

- name: Generate remediation plan from audit findings
  ansible.builtin.include_role:
    name: general_ludd.security.audit_framework
  vars:
    audit_framework__mode: remediate
    audit_framework__framework: soc2
    audit_framework__audit_results: "{{ lookup('file', artifact_dir ~ '/audit_findings.json') }}"
    audit_framework__enable_model_call: true
```

## Security

- `audit_framework__psk` is `no_log: true` on every task accessing the daemon
- Audit evidence written to `audit_framework__artifact_dir` with restricted
  permissions; sensitive finding fields are redacted in log output
- SearX queries are proxied through the configured instance — no direct
  internet access from target hosts
