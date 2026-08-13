# XMSS Backend Safety Boundary

## Purpose

Gludd previously exposed RFC 8391-shaped XMSS functions through a custom Python
WOTS+/Merkle implementation. That implementation was neither interoperable nor
resource safe: key generation expanded every leaf of a tree with up to 2^20
leaves, authentication paths did not cover sibling subtrees, and the version-1
public encoding included the same seed used to derive one-time secret material.

The release boundary now keeps the import-level API stable while refusing to
create, sign, inspect, or serialize XMSS material until a maintained RFC 8391
backend is packaged and verified.

## Behavioral contract

- Valid heights remain 4 through 20 and valid digest names remain SHA256,
  SHA512, SHAKE256, and SHAKE512.
- Invalid heights and digest names fail validation before capability detection.
- Key generation, signing, state inspection, and serialization raise
  XMSSError with one stable unavailable-capability message.
- Verification returns False for every input and never parses legacy or
  attacker-controlled bytes.
- The public function names, parameter order, and default constants remain
  import compatible.
- No fallback performs entropy generation, WOTS chaining, tree traversal,
  dynamic native builds, or network access.

This is intentionally not a substitute signature scheme. Mapping the API to
Ed25519, SPHINCS+, or SLH-DSA would create a second non-interoperable format
while continuing to call it XMSS.

## Standards and maintained implementation evidence

RFC 8391 states that the example key-generation algorithm is very inefficient,
requires 2^h leaves, and requires private state to be updated before a signature
is released. It points implementers to an optimized BDS reference
implementation:

https://www.rfc-editor.org/rfc/rfc8391.html

NIST explains that stateful hash-based signatures are not suitable for general
use, require carefully controlled state, and in its approved profile require
key generation and signing inside non-exporting hardware cryptographic modules:

https://csrc.nist.gov/Projects/Stateful-Hash-Based-Signatures

The maintained Open Quantum Safe Python binding exposes a stateful-signature
API, but its own documentation requires liboqs to be compiled with the
OQS_HAZARDOUS_EXPERIMENTAL_ENABLE_SIG_STFL_KEY_SIG_GEN option before XMSS key
or signature generation is available. Gludd must not silently download and
compile such a backend at runtime:

https://github.com/open-quantum-safe/liboqs-python

FoxCrypto publishes a dedicated RFC 8391 C implementation and is a candidate
for a future explicitly packaged backend. Integration still requires a
versioned Python/native ABI, reproducible builds, cross-platform artifacts,
known-answer vectors, and atomic state persistence:

https://github.com/FoxCryptoNL/xmss-library

## Practitioner evidence

wolfSSL issue #7427 is a long-lived user report from an attempted real build of
experimental XMSS and LMS support. The reporter found incompatible feature
configuration, a broken CMake path, and manual coupling to the XMSS reference
library. That experience demonstrates why a release must not infer that a
system crypto provider has portable XMSS support:

https://github.com/wolfSSL/wolfssl/issues/7427

The report supports an explicit capability error until one backend is pinned,
built, and exercised by the same release pipeline on every target platform.

## Security and compatibility

Version-1 keys and signatures from the removed fallback are untrusted and are
not migrated. Operators must revoke them because the public encoding exposed
the derivation seed and the authentication-path construction was incomplete.
New code denies their verification and deserialization.

This is an intentional behavioral break for direct callers, but repository
search finds no production consumer of these functions. Import compatibility
lets callers handle XMSSError without an ImportError. A future backend must use
a new versioned wire format and must not accept version-1 material.

## Zero-downtime delivery

The rest of the service remains available during rolling deployment because
the module has no startup side effects and no production call site. New workers
immediately deny XMSS operations in constant space while unrelated requests
continue normally.

A mixed fleet must not expose XMSS as a successful capability: route-level
capability discovery must report it unavailable before traffic is sent to any
worker. Rollback must target another fail-closed build, never the removed
custom implementation. Introducing a future backend will use additive
capability advertisement, canary verification, atomic state storage, and
rollback to denial without changing unrelated traffic.

## Resource and observability contract

Unsupported requests complete in constant time and constant memory relative to
tree height. They start no worker process, acquire no external lock, emit no
warning, and generate no secret bytes. The stable exception text distinguishes
capability absence from invalid parameters; verification has a simple Boolean
denial result suitable for metrics at the caller boundary.

## Verification

tests/unit/test_security_xmss.py covers all accepted parameter combinations,
invalid type and range boundaries, every public operation, legacy-looking
material, large attacker-controlled inputs, warning-free denial, stable call
signatures, and removal of custom WOTS/tree helpers. The focused suite contains 52 bounded tests and measures 100% branch-aware
coverage for src/general_ludd/security/xmss.py. Ruff, scoped mypy, the feature
spec linter, task integrity, and task-ledger validation pass. Collection and
the release-wide gate remain mandatory before promotion.
