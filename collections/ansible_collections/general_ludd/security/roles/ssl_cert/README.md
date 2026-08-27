# `general_ludd.security.ssl_cert` — SSL/TLS Certificate Management

Comprehensive Ansible role for SSL/TLS certificate lifecycle: mint, analyze, validate, research CAs, evaluate compliance, work with HSMs, and parse ASN.1.

## Quick start

```yaml
- name: Generate a self-signed certificate
  hosts: localhost
  vars:
    ssl_cert_generate: true
    ssl_cert_cn: "myapp.example.com"
    ssl_cert_sans:
      - "DNS:myapp.example.com"
      - "DNS:www.example.com"
    ssl_cert_key_type: RSA
    ssl_cert_key_size: 3072
  roles:
    - general_ludd.security.ssl_cert
```

## Categories

Each category is enabled by variables and tagged for selective execution:

| Category | Enable var | Tag |
|---|---|---|
| Certificate minting | `ssl_cert_generate: true` | `mint` |
| Certificate analysis | Always runs | `analyze` |
| Algorithm evaluation | Always runs | `algorithms` |
| Compliance checks | `ssl_cert_compliance_profile` set | `compliance` |
| CA jurisdiction lookup | `ssl_cert_ca_jurisdiction_lookup: true` | `ca_jurisdictions` |
| CA chain research | Always runs (model calls) | `chain_research` |
| HSM / smartcard | When PKCS#11 module detected | `hsm` |
| ASN.1 / OID | `ssl_cert_parse_asn1: true` | `asn1` |

## Key types

```yaml
# RSA (default)
ssl_cert_key_type: RSA
ssl_cert_key_size: 3072

# ECDSA
ssl_cert_key_type: ECDSA
ssl_cert_ec_curve: secp384r1

# Ed25519
ssl_cert_key_type: Ed25519
```

## CA-signed certificates

```yaml
ssl_cert_sign_as_ca: true
ssl_cert_ca_cert: /path/to/ca.crt
ssl_cert_ca_key: /path/to/ca.key
```

## Compliance

```yaml
ssl_cert_compliance_profile: fips140   # or soc2, hipaa, pcidss, nist_sp800_131a, cabf_baseline
```

## Certificate analysis

```yaml
- name: Analyze an existing certificate
  hosts: localhost
  vars:
    ssl_cert_cert_to_analyze: /path/to/cert.crt
    ssl_cert_verify_chain: true
    ssl_cert_trust_store: /etc/ssl/certs
    ssl_cert_check_ocsp: true
    ssl_cert_ocsp_url: http://ocsp.example.com
  roles:
    - general_ludd.security.ssl_cert
```

## HSM / Smartcard

```yaml
ssl_cert_pkcs11_module: /usr/lib/opensc-pkcs11.so
ssl_cert_pkcs11_pin: "123456"
ssl_cert_hsm_slot: 0
ssl_cert_import_hsm: true
ssl_cert_hsm_import_label: "my-key-label"
```

## CA jurisdiction research

```yaml
ssl_cert_ca_jurisdiction_lookup: true
ssl_cert_ca_name: DigiCert
ssl_cert_filter_jurisdiction: US
ssl_cert_ca_jurisdiction: US
```

## ASN.1 parsing

```yaml
ssl_cert_parse_asn1: true
ssl_cert_asn1_file: /path/to/cert.der
```

Managed-host inspection uses `openssl asn1parse`. Collection plugins that need
deterministic DER encoding, parsing, or OID lookup import the collection-owned
`ansible_collections.general_ludd.security.plugins.module_utils.asn1` utility.
Gludd core intentionally carries no duplicate ASN.1 implementation.

## Key defaults

| Variable | Default | Description |
|---|---|---|
| `ssl_cert_key_size` | `3072` | RSA key size |
| `ssl_cert_ec_curve` | `secp384r1` | ECDSA curve |
| `ssl_cert_digest` | `sha384` | Signature hash |
| `ssl_cert_days_valid` | `398` | Certificate lifetime |
| `ssl_cert_security_level` | `128` | Security bits |
| `ssl_cert_output_dir` | `/tmp/gludd-certs` | Output directory |
| `ssl_cert_compliance_profile` | `null` | fips140, soc2, hipaa, pcidss, etc. |

## Data files

- `vars/compliance.yml` — 6 compliance profiles with algorithm approval lists
- `vars/algorithms.yml` — RSA, ECDSA, EdDSA, DSA, DH, post-quantum metadata
- `vars/ca_jurisdictions.yml` — 18 CAs with jurisdiction and ownership data

## Artifacts

All operations write JSON artifacts to `ssl_cert_artifact_dir` (default: `/tmp/gludd-ssl-cert`):
- `cert-analysis.json` — parsed certificate details
- `ca-jurisdiction.json` — CA ownership and sovereignty assessment
- `ca-chain-research.json` — AI-researched CA background
- `hsm-status.json` — HSM/smartcard detection results
- `asn1-analysis.json` — ASN.1 structure and OID mappings
