# supply_chain_verify

Supply chain verification role for the `general_ludd.agent` collection.

## Description

Verifies cosign signatures and SLSA attestation for an artifact. **FAIL-CLOSED:**
missing or invalid signature yields `verdict=fail`; `require_slsa=true` with no
attestation also yields `verdict=fail`. The heavy cosign binary is gated behind
`enable_cosign: false` with `cosign_output_override` and `slsa_output_override`
providing canned pass/fail responses for molecule testing. **REPORT-ONLY.**

## Variables

| Variable | Default | Description |
|---|---|---|
| `artifact_ref` | `""` | Artifact to verify |
| `expected_identity` | `""` | Expected Fulcio certificate Subject |
| `expected_oidc_issuer` | GitHub Actions OIDC | OIDC issuer |
| `enable_cosign` | `false` | Run real cosign (false = use overrides) |
| `cosign_output_override` | `""` (empty = unsigned) | Canned cosign stdout |
| `cosign_output_override_rc` | `1` | Canned cosign exit code |
| `require_slsa` | `true` | Fail if SLSA attestation absent |
| `slsa_output_override` | `""` | Canned SLSA attestation stdout |
| `slsa_output_override_rc` | `1` | Canned SLSA exit code |
| `enable_git_push` | `false` | Always false — report-only |

## Artifacts

- `<artifact_dir>/supply_chain_verify.json` — signature_valid, attestation_present, slsa_level, verdict
- `<artifact_dir>/supply_chain_verify.md` — human-readable report
