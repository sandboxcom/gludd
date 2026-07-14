# hsm_operations

HSM and smartcard operations role for the `general_ludd.agent` collection.
Designed as a sub-role callable from `ssl_cert` via `include_role`.

## Description

Manages hardware security module (HSM) and smartcard operations across
multiple vendor platforms. Supports:

1. **HSM Detection** — auto-detect SoftHSM, YubiHSM, AWS CloudHSM, Azure HSM,
   nCipher, Utimaco, Thales; configure OpenSSL PKCS#11 engine
2. **Key Management** — generate RSA/ECDSA/Ed25519 keys on HSM, import keys,
   export public keys, list/delete keys (private keys never leave HSM)
3. **Signing Operations** — sign CSRs using HSM-protected keys, sign/verify
   arbitrary data, create self-signed certificates
4. **Smartcard Operations** — detect cards via PC/SC, parse ATR, PIN
   management (verify/change/unblock), read/import certificates, APDU commands

`gludd_model_call` is used for AI-assisted diagnostics; Python modules for
deterministic PKCS#11/PCSC operations. PINs and secret material are `no_log`
enforced throughout.

## Variables

| Variable | Default | Description |
|---|---|---|
| `hsm_operations__artifact_dir` | `/tmp/gludd-hsm-operations` | Artifact output path |
| `hsm_operations__daemon_url` | `http://localhost:8000` | Daemon URL for model calls |
| `hsm_operations__psk` | `""` | Pre-shared key (no_log) |
| `hsm_operations__mode` | `detect` | Operation mode (see below) |
| `hsm_operations__pkcs11_module` | `""` | PKCS#11 module path |
| `hsm_operations__slot_id` | `0` | HSM slot number |
| `hsm_operations__key_label` | `""` | Key label on token |
| `hsm_operations__key_type` | `RSA` | Key type: RSA, ECDSA, Ed25519 |
| `hsm_operations__key_size` | `2048` | RSA key size in bits |
| `hsm_operations__ec_curve` | `prime256v1` | EC curve name |
| `hsm_operations__sign_mechanism` | `""` | PKCS#11 mechanism (auto if empty) |
| `hsm_operations__sign_hash` | `SHA256` | Hash algorithm for signing |
| `hsm_operations__csr_path` | `""` | Path to CSR file for signing |
| `hsm_operations__cert_output` | `""` | Path for output certificate |
| `hsm_operations__pin_retry_max` | `3` | Max PIN attempts before lockout |
| `hsm_operations__enable_model_call` | `false` | Enable AI diagnostics |

## Modes

| Mode | Description | Required vars |
|---|---|---|
| `detect` | Detect HSM + list slots/tokens | `hsm_operations__pkcs11_module` (optional, auto-detected) |
| `gen_key` | Generate key on HSM | `pkcs11_module`, `key_label`, `key_type` |
| `import_key` | Import private key to HSM | `pkcs11_module`, `key_label`, `key_import_path` |
| `export_pubkey` | Export public key (never private) | `pkcs11_module`, `key_label` |
| `list_keys` | List keys on token | `pkcs11_module` |
| `delete_key` | Delete key from HSM | `pkcs11_module`, `key_label` |
| `sign_csr` | Sign CSR with HSM key | `pkcs11_module`, `key_label`, `csr_path`, `cert_output` |
| `sign_data` | Sign arbitrary data | `pkcs11_module`, `key_label`, `data_to_sign` |
| `verify_sig` | Verify signature | `pkcs11_module`, `key_label`, `data_signed` |
| `self_sign` | Create self-signed cert | `pkcs11_module`, `key_label`, `cert_output` |
| `card_detect` | Detect smartcards | (none) |
| `card_atr` | Parse card ATR | `smartcard_reader` (optional) |
| `card_pin` | PIN verify/change/unblock | `pkcs11_module`, `card_pin_op` |
| `card_read_cert` | Read cert from card | `pkcs11_module`, `key_label` |
| `card_import_cert` | Import cert to card | `pkcs11_module`, `key_label`, `cert_to_import` |
| `card_apdu` | Send raw APDU command | `pkcs11_module`, `apdu_command` |

## Artifacts

| File | Phase |
|---|---|
| `<artifact_dir>/hsm_detect.json` | HSM detection |
| `<artifact_dir>/hsm_key_ops.json` | Key operations |
| `<artifact_dir>/hsm_sign_ops.json` | Signing operations |
| `<artifact_dir>/hsm_smartcard.json` | Smartcard operations |

## Vendor configs

See `vars/hsm_vendors.yml` for PKCS#11 module paths per vendor.
Set `hsm_operations__hsm_vendor` to one of: `softhsm2`, `yubihsm2`,
`cloudhsm`, `azure_dedicated_hsm`, `nethsm`, `ncipher`, `utimaco`, `thales`,
`opencryptoki`.

## Security

- PINs and private keys: `no_log: true` on every task touching secret material
- Private keys never leave the HSM — only public keys are exportable
- Audit logging: operations written to `hsm_operations__audit_log_path`
- Retry counting enforced; token lockout detected and reported

## Usage from ssl_cert

```yaml
- name: Detect HSM for SSL operations
  ansible.builtin.include_role:
    name: general_ludd.security.hsm_operations
  vars:
    hsm_operations__mode: detect

- name: Generate key for SSL cert
  ansible.builtin.include_role:
    name: general_ludd.security.hsm_operations
  vars:
    hsm_operations__mode: gen_key
    hsm_operations__pkcs11_module: "/usr/lib/softhsm/libsofthsm2.so"
    hsm_operations__key_label: "ssl-cert-key"
    hsm_operations__key_type: RSA
    hsm_operations__key_size: 4096
```
