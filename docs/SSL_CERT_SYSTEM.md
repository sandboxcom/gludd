# SSL Certificate Management System

Comprehensive PKI lifecycle management for the gludd agent platform. Provides
self-signed certificate minting, chain-of-trust research, compliance checking,
and hardware-backed signing via HSM integration.

## Architecture Overview

```text
┌──────────────────────────────────────────────────────────────────┐
│                        Agent / Playbook                          │
│  (FQCN: general_ludd.security.ssl_cert / general_ludd.security.hsm_*) │
└───────────────┬────────────────────────────────┬─────────────────┘
                │                                │
    ┌───────────▼───────────┐       ┌────────────▼────────────┐
    │   ssl_cert role       │       │  hsm_operations roles   │
    │  (mint, research,     │       │  (sign, attest, keygen) │
    │   verify, compliance) │       │                         │
    └───────────┬───────────┘       └────────────┬────────────┘
                │                                │
    ┌───────────▼────────────────────────────────▼────────────┐
    │                   Python Modules (src/)                  │
    │  certificate.py  asn1.py  algorithms.py  hsm.py         │
    │  compliance.py   pin.py                                  │
    └───────────┬─────────────────────────────────────────────┘
                │
    ┌───────────▼───────────┐
    │    Data Files (YAML)  │
    │  compliance.yml       │
    │  algorithms.yml       │
    │  ca_jurisdictions.yml │
    │  known_oids.yml       │
    └───────────────────────┘
```

### Data flow

1. **Agent** invokes a role via FQCN or calls Python module directly
2. **Role** delegates to `gludd_make` or `gludd_model_call` for heavy computation
3. **Python modules** read static data files, perform crypto operations via `cryptography`
4. **HSM path** goes through PKCS#11 via the `hsm.py` backend
5. **Compliance** reads `compliance.yml` for standard→requirement mappings and checks cert properties against them

## Role Reference

### `general_ludd.security.ssl_cert`

Primary role for SSL/TLS certificate lifecycle operations.

**FQCN:** `general_ludd.security.ssl_cert`

| Variable | Required | Default | Description |
|---|---|---|---|
| `ssl_action` | yes | `""` | One of: `mint`, `research`, `verify`, `compliance_check`, `chain_walk` |
| `ssl_common_name` | conditional | `""` | CN for certificate (required for `mint`) |
| `ssl_san_dns` | no | `[]` | Subject Alternative Name DNS entries |
| `ssl_san_ip` | no | `[]` | Subject Alternative Name IP entries |
| `ssl_key_type` | no | `rsa` | Key algorithm: `rsa`, `ec`, `ed25519` |
| `ssl_key_size` | no | `2048` | RSA key bits (ignored for EC/Ed25519) |
| `ssl_ec_curve` | no | `secp384r1` | EC curve when `ssl_key_type=ec` |
| `ssl_days_valid` | no | `397` | Certificate validity period in days |
| `ssl_org` | no | `""` | Organization (O) field |
| `ssl_country` | no | `""` | Country (C) field, ISO 3166-1 alpha-2 |
| `ssl_compliance_standard` | no | `""` | Standard to check against: `nist-800-52r2`, `fips-140-3`, `pci-dss-4`, `mozilla-intermediate` |
| `ssl_chain_pem` | no | `""` | PEM-encoded certificate chain (for `research`, `chain_walk`, `verify`) |
| `ssl_target_host` | conditional | `""` | Hostname:port for live chain research |
| `ssl_output_dir` | no | `/tmp/gludd-ssl` | Artifact output directory |
| `ssl_hsm_token` | no | `""` | HSM token label for signing operations |

**Output artifacts (written to `ssl_output_dir`):**

| File | Actions that produce it | Description |
|---|---|---|
| `cert.pem` | `mint` | Generated leaf certificate |
| `key.pem` | `mint` | Generated private key (mode 0600) |
| `csr.pem` | `mint` | Certificate signing request |
| `fullchain.pem` | `mint`, `chain_walk` | Leaf + intermediates concatenated |
| `chain_report.json` | `research`, `chain_walk` | Structured chain analysis |
| `compliance_report.json` | `compliance_check` | Compliance findings per standard |
| `cert_info.json` | `verify` | Parsed certificate fields |

### `general_ludd.security.hsm_operations`

Hardware Security Module operations for cryptographic signing and key attestation.

**FQCN:** `general_ludd.security.hsm_operations`

| Variable | Required | Default | Description |
|---|---|---|---|
| `hsm_action` | yes | `""` | One of: `sign_cert`, `keygen`, `attest`, `list_keys`, `pin_change` |
| `hsm_module_path` | no | `"/usr/lib/softhsm/libsofthsm2.so"` | Path to PKCS#11 module |
| `hsm_slot` | no | `0` | HSM slot number |
| `hsm_token_label` | no | `"gludd-agent"` | Token label for PIN operations |
| `hsm_pin` | yes | `""` | User PIN (never write to disk; use `ansible_vault` or `env`) |
| `hsm_so_pin` | conditional | `""` | Security Officer PIN (for `pin_change`) |
| `hsm_new_user_pin` | conditional | `""` | New user PIN (for `pin_change`) |
| `hsm_key_label` | conditional | `""` | Key label for `sign_cert`, `attest` |
| `hsm_key_type` | no | `rsa` | Key type for `keygen`: `rsa`, `ec` |
| `hsm_key_size` | no | `2048` | RSA key size |
| `hsm_ec_curve` | no | `secp384r1` | EC curve |
| `hsm_csr_path` | no | `""` | Path to CSR file for `sign_cert` |
| `hsm_output_dir` | no | `/tmp/gludd-hsm` | Output directory |

**Output artifacts:**

| File | Actions | Description |
|---|---|---|
| `signed_cert.pem` | `sign_cert` | Certificate signed by HSM-backed key |
| `key_attestation.json` | `attest` | Key attestation report |
| `key_list.json` | `list_keys` | Enumerated keys on token |

## Usage Examples

### Mint a self-signed certificate

```yaml
- hosts: localhost
  vars:
    ssl_action: mint
    ssl_common_name: "agent-01.internal.gludd.local"
    ssl_san_dns:
      - "agent-01.internal.gludd.local"
      - "agent-01.gludd.local"
    ssl_key_type: ec
    ssl_ec_curve: secp384r1
    ssl_days_valid: 397
    ssl_org: "General Ludd"
    ssl_country: "US"
    ssl_output_dir: "/tmp/gludd-certs/agent-01"
  roles:
    - role: general_ludd.security.ssl_cert
```

Output: `cert.pem`, `key.pem`, `csr.pem`, `fullchain.pem` in `/tmp/gludd-certs/agent-01/`.

### Research a certificate chain

```yaml
- hosts: localhost
  vars:
    ssl_action: research
    ssl_target_host: "github.com:443"
    ssl_output_dir: "/tmp/gludd-certs/github-research"
  roles:
    - role: general_ludd.security.ssl_cert
```

Produces `chain_report.json` with leaf subject, issuer chain, expiry dates,
signature algorithms, and OCSP responder URLs for every certificate in the
chain.

### Check compliance

```yaml
- hosts: localhost
  vars:
    ssl_action: compliance_check
    ssl_chain_pem: "{{ lookup('file', '/path/to/fullchain.pem') }}"
    ssl_compliance_standard: "pci-dss-4"
    ssl_output_dir: "/tmp/gludd-certs/compliance"
  roles:
    - role: general_ludd.security.ssl_cert
```

Writes `compliance_report.json` listing each requirement, whether the chain
passes, and the evidence (or violation detail).

### Sign with an HSM

```yaml
- hosts: localhost
  vars:
    hsm_action: sign_cert
    hsm_pin: "{{ vault_hsm_pin }}"
    hsm_key_label: "intermediate-ca-2026"
    hsm_csr_path: "/tmp/gludd-certs/service-csr.pem"
    hsm_output_dir: "/tmp/gludd-certs/hsm-signed"
  roles:
    - role: general_ludd.security.hsm_operations
```

Signs the CSR with the HSM-resident intermediate CA key. The private key never
leaves the HSM.

## Data Files

Data files live in `collections/ansible_collections/general_ludd/security/data/ssl/`.

### `compliance.yml`

Maps compliance standards to minimum certificate requirements.

```yaml
nist-800-52r2:
  min_key_size: { rsa: 2048, ec: 256 }
  allowed_sig_algs: [sha256WithRSAEncryption, sha384WithRSAEncryption,
                     ecdsa-with-SHA256, ecdsa-with-SHA384, ecdsa-with-SHA512]
  max_validity_days: 397
  require_san: true
  require_ca_basic_constraints: true
  min_tls_version: "tls12"

pci-dss-4:
  min_key_size: { rsa: 2048, ec: 256 }
  allowed_sig_algs: [sha256WithRSAEncryption, sha384WithRSAEncryption,
                     ecdsa-with-SHA256, ecdsa-with-SHA384]
  max_validity_days: 397
  require_san: true
  require_ocsp_or_crl: true
  forbid_sha1: true
  forbid_md5: true
  forbid_self_signed_for_public: true

mozilla-intermediate:
  min_key_size: { rsa: 2048, ec: 256 }
  allowed_sig_algs: [sha256WithRSAEncryption, sha384WithRSAEncryption,
                     ecdsa-with-SHA256, ecdsa-with-SHA384, ecdsa-with-SHA512]
  max_validity_days: 397
  require_san: true

fips-140-3:
  min_key_size: { rsa: 2048, ec: 256 }
  allowed_sig_algs: [sha256WithRSAEncryption, sha384WithRSAEncryption,
                     ecdsa-with-SHA256, ecdsa-with-SHA384, ecdsa-with-SHA512]
  forbid_ecdsa_sha1: true
  require_fips_approved_curve: true
  approved_curves: [secp256r1, secp384r1, secp521r1]
```

### `algorithms.yml`

Known cryptographic algorithm mappings for signature and key type resolution.

```yaml
signature_algorithms:
  sha256WithRSAEncryption:
    oid: "1.2.840.113549.1.1.11"
    hash: sha256
    key_type: rsa
    fips_approved: true
  ecdsa-with-SHA384:
    oid: "1.2.840.10045.4.3.3"
    hash: sha384
    key_type: ec
    fips_approved: true
  rsassaPss:
    oid: "1.2.840.113549.1.1.10"
    hash: null
    key_type: rsa
    fips_approved: true
  sha1WithRSAEncryption:
    oid: "1.2.840.113549.1.1.5"
    hash: sha1
    key_type: rsa
    fips_approved: false
    deprecated: true

key_algorithms:
  rsaEncryption:
    oid: "1.2.840.113549.1.1.1"
    type: rsa
    valid_sizes: [2048, 3072, 4096, 8192]
  id-ecPublicKey:
    oid: "1.2.840.10045.2.1"
    type: ec

elliptic_curves:
  secp256r1:
    oid: "1.2.840.10045.3.1.7"
    nist_name: P-256
    fips_approved: true
  secp384r1:
    oid: "1.3.132.0.34"
    nist_name: P-384
    fips_approved: true
```

### `ca_jurisdictions.yml`

Maps well-known Certificate Authorities to their organizational details and
root certificate identifiers.

```yaml
"DigiCert Inc":
  jurisdiction: US
  root_certs:
    - subject: "/C=US/O=DigiCert Inc/CN=DigiCert Global Root G2"
      fingerprint_sha256: "df3c24f9bfd666761b268073fe06d1cc..."
      valid_from: "2013-08-01"

"Let's Encrypt":
  jurisdiction: US
  root_certs:
    - subject: "/C=US/O=Internet Security Research Group/CN=ISRG Root X1"
      fingerprint_sha256: "96bcec06264976f37460779acf28c5a7..."
      valid_from: "2015-06-04"
    - subject: "/C=US/O=Internet Security Research Group/CN=ISRG Root X2"
      fingerprint_sha256: "69729b8e15a86efc177a57b9f607ee86..."
      valid_from: "2020-09-04"

"Sectigo Limited":
  jurisdiction: GB
  root_certs:
    - subject: "/C=GB/O=Sectigo Limited/CN=Sectigo ECC Organization Validation Secure Server CA"
      fingerprint_sha256: "5cfab1d0a8cb3fae6977a0b3e4ef3b9b..."
      valid_from: "2018-11-02"
```

### `known_oids.yml`

Extended attribute OID directory for extension auditing.

```yaml
extensions:
  basicConstraints:
    oid: "2.5.29.19"
    purpose: "CA identification and path-length"
  subjectAltName:
    oid: "2.5.29.17"
    purpose: "Bound host/identity names"
  keyUsage:
    oid: "2.5.29.15"
    purpose: "Permitted cryptographic operations"
  extendedKeyUsage:
    oid: "2.5.29.37"
    purpose: "Certificate purpose constraints"
  crlDistributionPoints:
    oid: "2.5.29.31"
    purpose: "CRL retrieval endpoint"
  authorityInfoAccess:
    oid: "1.3.6.1.5.5.7.1.1"
    purpose: "OCSP responder and issuer URI"

eku_values:
  serverAuth: "1.3.6.1.5.5.7.3.1"
  clientAuth: "1.3.6.1.5.5.7.3.2"
  codeSigning: "1.3.6.1.5.5.7.3.3"
  emailProtection: "1.3.6.1.5.5.7.3.4"
  timestamping: "1.3.6.1.5.5.7.3.8"
  ocspSigning: "1.3.6.1.5.5.7.3.9"
```

## Python Module API

Modules live under `src/general_ludd/ssl/`.

### `certificate.py`

Core certificate generation, parsing, and chain operations.

```python
def generate_self_signed(
    common_name: str,
    san_dns: list[str] | None = None,
    san_ip: list[str] | None = None,
    key_type: str = "rsa",
    key_size: int = 2048,
    ec_curve: str = "secp384r1",
    days_valid: int = 397,
    org: str = "",
    country: str = "",
) -> tuple[bytes, bytes, bytes]:
    """Returns (cert_pem, key_pem, csr_pem)."""

def parse_certificate(cert_pem: bytes) -> dict[str, object]:
    """Parse PEM-encoded X.509 certificate into a structured dict.
    Returns: subject, issuer, serial, not_before, not_after,
             san_dns, san_ip, key_type, key_size, sig_alg, extensions."""

def parse_chain(chain_pem: bytes) -> list[dict[str, object]]:
    """Parse a PEM chain (leaf first) into list of per-cert dicts."""

def fetch_chain(hostname: str, port: int = 443, timeout: float = 10.0) -> list[bytes]:
    """Connect to host:port, perform TLS handshake, return peer cert chain as list of DER bytes."""

def verify_chain(chain_pem: bytes) -> dict[str, object]:
    """Verify chain: expiry, signature chain, hostname match.
    Returns: valid (bool), errors (list[str]), warnings (list[str])."""

def walk_chain(chain_pem: bytes) -> dict[str, object]:
    """Walk the chain from leaf to root. Returns chain depth,
    each cert's trust status, and the trust anchor identifier."""

def check_compliance(
    chain_pem: bytes,
    standard: str,
    hostname: str = "",
) -> dict[str, object]:
    """Check a certificate chain against a named compliance standard.
    Returns: standard, overall_pass (bool), requirements (list of
    {id, name, pass, evidence, detail})."""
```

### `asn1.py`

DER parsing and OID resolution for certificate extension inspection.

```python
def parse_der(der_bytes: bytes) -> object:
    """Parse DER-encoded ASN.1 into a nested structure."""

def oid_to_name(oid: str) -> str:
    """Resolve dotted OID string to human-readable name using known_oids.yml."""

def name_to_oid(name: str) -> str:
    """Reverse lookup: human-readable name → dotted OID."""

def parse_extension(oid: str, extension_der: bytes) -> dict[str, object]:
    """Parse a specific X.509 extension by OID. Returns parsed value dict."""

def parse_generalized_time(asn1_bytes: bytes) -> datetime.datetime:
    """Parse ASN.1 GeneralizedTime to Python datetime."""

def parse_san_extension(extension_der: bytes) -> dict[str, list[str]]:
    """Parse Subject Alternative Name extension. Returns dns_names and ip_addresses."""

def parse_key_usage(extension_der: bytes) -> dict[str, bool]:
    """Parse Key Usage extension into flag dict."""

def parse_eku(extension_der: bytes) -> list[str]:
    """Parse Extended Key Usage extension into list of purpose OIDs."""
```

### `algorithms.py`

Algorithm registry and strength enforcement.

```python
def get_algorithm_info(signature_oid: str) -> dict[str, object]:
    """Look up a signature algorithm by OID. Returns name, hash, key_type, fips_approved."""

def is_algorithm_deprecated(signature_oid: str) -> bool:
    """Check if algorithm is deprecated (SHA-1, MD5, etc)."""

def get_key_algorithm_info(key_oid: str) -> dict[str, object]:
    """Look up key algorithm by OID."""

def get_curve_info(curve_oid: str) -> dict[str, object]:
    """Look up EC curve by OID. Returns name, nist_name, fips_approved."""

def check_key_strength(key_type: str, key_size: int | str, min_required: dict[str, int]) -> bool:
    """Check if key size meets minimum requirement for its type."""

def get_minimum_key_size(standard: str, key_type: str) -> int:
    """Get minimum key size for a given standard and key type from compliance.yml."""
```

### `hsm.py`

PKCS#11 HSM backend for hardware-backed signing.

```python
def list_slots(module_path: str) -> list[dict[str, object]]:
    """Enumerate available PKCS#11 slots. Returns slot_id, token_label, hardware, firmware."""

def open_session(module_path: str, slot: int, pin: str) -> object:
    """Open a PKCS#11 session on the specified slot. Returns opaque session handle."""

def list_keys(session: object) -> list[dict[str, object]]:
    """List key objects on the token. Returns label, key_type, key_size, id."""

def sign_certificate(
    session: object,
    key_label: str,
    csr_der: bytes,
    issuer_cert_pem: bytes,
    days_valid: int = 397,
) -> bytes:
    """Sign a CSR with an HSM-resident key. Returns PEM certificate."""

def generate_key_pair(
    session: object,
    label: str,
    key_type: str = "rsa",
    key_size: int = 2048,
    ec_curve: str = "secp384r1",
) -> dict[str, object]:
    """Generate a key pair inside the HSM. Returns key_label, key_type, key_size, public_key_pem."""

def attest_key(session: object, key_label: str) -> dict[str, object]:
    """Produce attestation report for an HSM-resident key.
    Returns key_label, created_at, never_exported, hsm_model, firmware_version."""

def change_pin(
    module_path: str,
    slot: int,
    old_pin: str,
    new_pin: str,
) -> bool:
    """Change user PIN on the token. Returns True on success."""

def check_hsm_health(module_path: str, slot: int, pin: str) -> dict[str, object]:
    """Quick health check: slot available, logged in, token not locked out.
    Returns healthy (bool), token_label, free_space_bytes, max_pin_attempts_left."""

def close_session(session: object) -> None:
    """Close PKCS#11 session and release resources."""
```

### `compliance.py`

Standards enforcement engine.

```python
def load_standard(standard_name: str) -> dict[str, object]:
    """Load a compliance standard definition from compliance.yml.
    Raises ValueError if standard not found."""

def list_standards() -> list[str]:
    """Return names of all defined compliance standards."""

def check_chain_against_standard(
    chain: list[dict[str, object]],
    standard: dict[str, object],
    hostname: str = "",
) -> dict[str, object]:
    """Apply all requirements of a standard to a certificate chain.
    Returns result dict with overall_pass and per-requirement findings."""

def report_compliance(
    check_result: dict[str, object],
    format: str = "json",
) -> str:
    """Render compliance check result. format: json, markdown, text."""

def check_expiry(cert: dict[str, object], max_days: int = 397) -> dict[str, object]:
    """Check certificate expiry against maximum validity."""

def check_signature_algorithm(
    cert: dict[str, object],
    allowed: list[str],
    forbidden: list[str] | None = None,
) -> dict[str, object]:
    """Check signature algorithm against allowlist/denylist."""

def check_san_present(cert: dict[str, object]) -> dict[str, object]:
    """Verify SAN extension exists and is non-empty."""

def check_basic_constraints(cert: dict[str, object]) -> dict[str, object]:
    """Verify CA:TRUE certs have basicConstraints, pathLenConstraint set."""

def check_ocsp_or_crl(cert: dict[str, object]) -> dict[str, object]:
    """Verify at least one revocation mechanism (OCSP or CRL) is configured."""
```

### `pin.py`

PIN credential security: vault integration, format enforcement, and lockout protection.

```python
def validate_pin_format(pin: str) -> dict[str, bool | str]:
    """Validate PIN: minimum length 6, max 128, no common patterns.
    Returns {valid: bool, errors: list[str]}."""

def resolve_pin(pin_spec: str) -> str:
    """Resolve PIN from source: direct value, ansible_vault reference, or env var.
    Format: 'vault:label' | 'env:VAR_NAME' | 'literal:value'."""

def check_token_lockout(module_path: str, slot: int) -> dict[str, object]:
    """Check if HSM token is locked. Returns locked (bool), max_attempts, attempts_left."""
```

## Compliance Quick Reference

| Standard | Min RSA | Min EC | Max Validity | Revocation | SHA-1 | SAN |
|---|---|---|---|---|---|---|
| **NIST 800-52r2** | 2048 | 256 | 397 days | Recommended | Forbidden | Required |
| **PCI DSS 4.0** | 2048 | 256 | 397 days | Required | Forbidden | Required |
| **FIPS 140-3** | 2048 | 256 | N/A | Recommended | Forbidden | Required |
| **Mozilla Intermediate** | 2048 | 256 | 397 days | Recommended | Forbidden | Required |
| **ETSI EN 319 412** | 2048 | 256 | 825 days | Required | Forbidden | Required |
| **ISO 27001 (Annex A.10.1)** | 2048 | 256 | 397 days | Required | Forbidden | Required |

## Security Considerations

### Private key handling

- Private keys are written with mode `0600` (owner read/write only).
- When using HSM signing, private keys are generated INSIDE the HSM and never
  leave the token — the `sign_certificate` function operates on CSRs, never on
  raw keys.
- Self-signed key artifacts in `ssl_output_dir/key.pem` are temporary — the
  role documents the file path so the caller can delete or vault it after use.
- Keys are never logged. The `certificate.py` module redacts key material from
  debug output.

### PIN security

- HSM PINs must use the `resolve_pin()` interface — never hardcoded in
  playbooks. Supported sources:
  - `env:GLUDD_HSM_PIN` — read from environment variable
  - `vault:gludd.hsm_pin` — read from ansible-vault encrypted file
  - `literal:...` — inline (discouraged; emits a warning)

- PIN format validation (`validate_pin_format`) enforces:
  - Minimum 8 characters (up from the PKCS#11 minimum of 4)
  - Not one of the vendor default PINs (`"1111"`, `"0000"`, `"123456"`,
    `"UserPIN"`, `"SecretPIN"`)
  - Not the same as the SO PIN (checked at runtime)

- After `hsm_action=pim_change`, the old PIN is zeroed from memory.

### Audit logging

All certificate operations emit structured audit events:

| Event | Fields |
|---|---|
| `ssl.cert.minted` | cn, san_count, key_type, validity_days, output_dir |
| `ssl.chain.researched` | target_host, chain_depth, leaf_subject, root_issuer |
| `ssl.compliance.checked` | standard, overall_pass, failures_count |
| `hsm.keygen` | key_label, key_type, slot, token_label |
| `hsm.sign` | key_label, csr_fingerprint, signed_fingerprint |
| `hsm.pin_change` | slot, token_label (no PIN values) |

Each event includes `timestamp`, `agent_id`, `project_id`, and `correlation_id`.

### Trust store isolation

- Certificate operations never modify the system trust store (`/etc/ssl/certs`,
  macOS Keychain, Windows Certificate Store).
- Root CAs from `ca_jurisdictions.yml` are verified via pinned SHA-256
  fingerprints before being trusted.
- The `fetch_chain()` function uses a temporary, isolated SSL context — it
  never inherits system CA trust for validation during research.

### Resource isolation

- PKCS#11 sessions are explicitly opened and closed; no global session state.
- The `hsm.py` module uses `atexit` handlers as a safety net to close sessions
  on abnormal exit.
- Concurrent HSM access from multiple agents is serialized via slot-level
  locking (PKCS#11 `C_Login` is exclusive per slot by spec).
