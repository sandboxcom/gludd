# Ed25519 Compatibility Mathematics

## Contract

Gludd delegates key generation, signing, public-key derivation, and verification
to the maintained `cryptography` implementation. The local `EDPoint` arithmetic
exists only for compatibility with callers that inspect RFC 8032 point
operations; it must never become a silent production signing fallback.

The compatibility base point is the canonical RFC 8032 point. It must satisfy
the Edwards25519 curve equation, have subgroup order `Q`, encode to the
published compressed representation, and round-trip through point decoding.
Scalar multiplication must preserve identity, associativity, distributivity,
doubling, and negation.

## Security, observability, and ZDD

A single transcribed digit in the base-point x coordinate invalidated every
dependent group operation while the mature signing path remained healthy. The
release gate therefore exercises both layers: fixed-seed sign/verify behavior
through `cryptography`, plus curve membership, subgroup order, arithmetic, and
encoding through the compatibility API.

The correction changes no private-key, public-key, signature, or persisted-data
format. A zero-downtime rollout may compare the old and new instances using
fixed-seed public keys and the base-point health checks before shifting traffic.
Rollback is safe because no state migration occurs. Any failure is observable as
a focused contract-test failure; production code must fail closed rather than
substitute the educational arithmetic for the maintained backend.

## Practitioner evidence

Long-lived practitioner reports reinforce the boundary:

- [libsodium issue #170](https://github.com/jedisct1/libsodium/issues/170)
  documents how an incorrect public key supplied alongside Ed25519 signing can
  compromise the scheme, demonstrating why point identity must not drift.
- [python-pure25519](https://github.com/warner/python-pure25519) explicitly
  advises practitioners to use maintained NaCl bindings for normal signing and
  warns that its exposed point arithmetic is not constant-time.
- [Go issue #52221](https://github.com/golang/go/issues/52221) records the
  ecosystem move toward safe APIs where invalid curve states cannot be
  represented instead of general-purpose custom curve arithmetic.

Gludd consequently keeps maintained-library signing as the production boundary
and tests the retained mathematical surface against
[RFC 8032](https://www.rfc-editor.org/rfc/rfc8032).
