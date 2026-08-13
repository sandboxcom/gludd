# ML-KEM Provider Boundary

## Scope and terminology

The repository exposes ML-KEM-512, ML-KEM-768, and ML-KEM-1024 through the
legacy Kyber-named Python API. FIPS 203 standardizes the algorithm name ML-KEM;
the old names remain only to avoid an unnecessary source-level break for callers.

Production key generation, encapsulation, and decapsulation are delegated to
`pqcrypto` 0.4, whose CFFI wheels wrap tested PQClean implementations. The
broken, unused pure-Python NTT and K-PKE implementation was removed. No other
production module imported those internals, and maintaining a second
cryptographic primitive would duplicate a mature upstream project.

## Wire and validation contract

The provider boundary pins the standard byte dimensions:

| Parameter set | Public key | Secret key | Ciphertext | Shared secret |
|---|---:|---:|---:|---:|
| ML-KEM-512 | 800 | 1,632 | 768 | 32 |
| ML-KEM-768 | 1,184 | 2,400 | 1,088 | 32 |
| ML-KEM-1024 | 1,568 | 3,168 | 1,568 | 32 |

Parameter selection is closed over the three canonical descriptors. Keys and
ciphertexts must be immutable `bytes` with the exact standard length; truncated,
extended, wrong-type, and unknown-parameter inputs raise `KyberError`. Provider
exceptions and malformed provider outputs are wrapped at the boundary so raw
CFFI errors do not escape.

FIPS 203 implicit rejection is preserved. A same-length altered ciphertext or
a different valid secret key returns a pseudorandom 32-byte secret that differs
from the valid encapsulated secret. It does not expose a validity oracle.

## Dependency and library choice

PyCA `cryptography` is the preferred maintained primitive boundary elsewhere
in the project, but its current ML-KEM API exposes only ML-KEM-768 and
ML-KEM-1024. The existing public contract includes ML-KEM-512. `pqcrypto`
supports all three FIPS 203 parameter sets, Python 3.11 through 3.14, and
prebuilt macOS, Linux, and Windows wheels. Its Apache-2.0 license and notice are
recorded in `THIRD_PARTY_LICENSES.md` and the generated SBOM.

## Zero-downtime deployment

The removed implementation failed before it could create interoperable key
pairs or ciphertexts, and there are no production consumers or persisted wire
records to migrate. The new boundary produces standard FIPS 203 encodings.

For a rolling deployment:

1. Stage and smoke-test the locked `pqcrypto` wheel on every target platform.
2. Deploy decapsulation-capable instances first and advertise
   `ml-kem/<level>/fips203` as a capability only after their health check passes.
3. Route ML-KEM traffic exclusively to capable instances during a mixed-version
   interval. Do not silently fall back to the removed implementation.
4. Generate or publish new public keys only after all intended decapsulators are
   upgraded. Keep the matching private keys available through the normal key
   rotation window.
5. Monitor provider-load errors, boundary-validation failures, and
   encapsulation/decapsulation mismatch counts by parameter set. Halt rollout on
   any nonzero valid-roundtrip mismatch.

A rollback after issuing new standard keys must retain at least one upgraded
decapsulator; an old instance cannot safely own that traffic.

## Standards and practitioner evidence

- [NIST FIPS 203](https://csrc.nist.gov/pubs/fips/203/final) defines ML-KEM and
  its three approved parameter sets.
- [Open Quantum Safe's ML-KEM inventory](https://openquantumsafe.org/liboqs/algorithms/kem/ml-kem.html)
  records the standard key, ciphertext, and shared-secret sizes.
- [pqcrypto on PyPI](https://pypi.org/project/pqcrypto/) documents its PQClean
  lineage, supported algorithms, Python versions, and prebuilt wheels.
- [PyCA's ML-KEM API](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/mlkem/)
  documents its current ML-KEM-768/1024 surface, explaining why it is not a
  complete replacement for this three-level contract.
- The long-running [liboqs-python macOS ARM build report #86](https://github.com/open-quantum-safe/liboqs-python/issues/86)
  shows real deployment friction when native libraries must be discovered or
  built at runtime. This informed the choice of locked, prebuilt wheels.
- A [practitioner discussion on implementing cryptographic primitives](https://www.reddit.com/r/cryptography/comments/1lvo5gb)
  reinforces the operational risk of maintaining bespoke primitive code when a
  reviewed implementation is available.
