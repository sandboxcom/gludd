# Threshold Signature Compatibility

## Scope

This contract covers the repository's FROST-compatible Schnorr verification and
threshold ECDSA verification boundary for secp256k1 and P-256. The production
ECDSA primitive remains `cryptography`; the repository code coordinates shares
and aggregation around that maintained implementation.

## Encoding and hashing invariants

SEC 1 compressed points use prefix `0x02` for an even y-coordinate and `0x03`
for an odd y-coordinate. Decoding recovers y from x, selects the root whose
parity matches that prefix, and rejects malformed or non-curve encodings. Reversing
that parity makes a valid aggregate commitment decode to its inverse and causes
every otherwise-valid FROST signature to fail verification.

Threshold ECDSA hashes the original message exactly once with SHA-256. The signer
uses that digest in the ECDSA equation, while the `cryptography` verifier receives
the original message with `ECDSA(SHA256())`. A caller that already has a digest
must instead use `Prehashed`; passing a digest to `ECDSA(SHA256())` hashes it a
second time and is not wire-compatible.

Verification is fail-closed: malformed encodings, the wrong key or message, and
tampered signature components return false. There is no compatibility fallback
that retries alternate parity or hash conventions.

## Mature-library boundary

The project keeps `cryptography` as the maintained key and ECDSA verification
backend. Native `secp256k1-frost` and Rust FROST implementations are useful
interoperability references, but no maintained, cross-platform Python package
provides the existing dual-curve secp256k1/P-256 API as a drop-in replacement.
The bounded share-aggregation layer therefore remains local and is pinned by
cross-curve behavioral tests.

## Zero-downtime deployment

This correction changes neither stored key material nor the signature wire
format. Existing signing code already emitted canonical compressed commitments
and standard DER ECDSA signatures; the old verification paths rejected them.

Deploy corrected verifiers across the fleet before enabling new producers or
replaying stored signatures. During a mixed-version interval, old instances can
still reject valid signatures, so routing must prefer the updated verifier pool.
Track verification failures by algorithm and verifier version, and stop rollout
if the updated cohort shows any increase. Do not silently accept legacy
double-hash or inverted-parity variants.

## Standards and practitioner evidence

- [RFC 9591](https://www.rfc-editor.org/rfc/rfc9591.html) defines FROST and its
  compressed P-256/secp256k1 element encodings.
- [PyCA ECDSA documentation](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ec/)
  distinguishes normal message hashing from the explicit `Prehashed` API.
- The [secp256k1-frost practitioner discussion](https://news.ycombinator.com/item?id=47164855)
  shows active interoperability work around native FROST implementations and
  motivates keeping the wire boundary standards-based.
- The [NEAR threshold-signatures implementation](https://github.com/near/threshold-signatures)
  documents real verifier integration differences, including the risk of
  accidental double hashing at prehashed-message boundaries.
