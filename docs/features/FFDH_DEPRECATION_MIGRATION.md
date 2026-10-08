# Legacy FFDH Deprecation Migration

## Status and scope

PyCA `cryptography` 50.0.0 deprecated all finite-field Diffie-Hellman
(FFDH) types and loaders. Gludd therefore no longer constructs PyCA
`DHParameters`, `DHParameterNumbers`, or backend objects in
`general_ludd.algorithms.diffie_hellman`.

This is a compatibility migration, not a new cryptographic protocol. The
module keeps its integer-based API for the pinned RFC 3526 group 14 and for
small educational examples. It does not introduce X25519 or reproduce an
existing provider-backed key exchange. Applications designing a new protocol
must choose a maintained provider and a modern, authenticated key-exchange
construction outside this legacy module.

## Upstream evidence

- The [PyCA 50.0.0 changelog](https://cryptography.io/en/50.0.2/changelog/#v50-0-0)
  deprecates all FFDH types and FFDH key or parameter loading APIs.
- The [PyCA FFDH documentation](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/dh/)
  says FFDH support will be removed and recommends a modern key exchange.
- [RFC 3526 section 3](https://datatracker.ietf.org/doc/html/rfc3526#section-3)
  defines the 2048-bit MODP group numbered 14, including its modulus and
  generator 2. That exact group remains Gludd's compatibility fixture.
- [RFC 7919 section 8.6](https://www.rfc-editor.org/rfc/rfc7919.html#section-8.6)
  warns that FFDHE modular exponentiation should be constant time. Python's
  integer `pow()` does not make that guarantee, so this compatibility path is
  not a general-purpose replacement for provider-backed production crypto.

Long-lived practitioner reports explain why compatibility needs an explicit,
narrow boundary instead of silently changing algorithms:

- [PyCA issue 15603](https://github.com/pyca/cryptography/issues/15603)
  records a real legacy protocol, Apple Screen Sharing/ARD, whose wire format
  still requires FFDH and cannot substitute a modern key exchange in place.
- [Server Fault operators discussing nginx DH parameters](https://serverfault.com/questions/345830/should-i-set-diffie-hellman-parameters-for-nginx-ssl)
  show that deployments have long reused explicit DH parameter files and must
  preserve their configured wire compatibility deliberately.
- [A Stack Overflow deployment report](https://stackoverflow.com/questions/61326004/how-to-check-my-server-diffie-hellman-modp-size-bits-and-increase-it)
  shows the recurring difficulty of distinguishing certificate key size from
  the negotiated finite-field group size.

## Enforced boundary

The legacy module now has two distinct operating modes:

1. `GROUP_2048` uses the exact RFC 3526 group-14 integers. Private values come
   from `secrets.randbelow()`, public values and shared secrets use three-argument
   `pow()`, and two peers retain the established integer round trip.
2. Runtime safe-prime generation is demonstration-only. It accepts 8 through
   64 bits and stops after 65,536 candidates. Exhaustion raises `DHError`
   instead of running forever.

The following inputs fail closed with `DHError`:

- runtime requests above 64 bits, including superficially secure-sized 512-
  and 2048-bit requests;
- large groups whose `(p, g, q)` tuple is not the pinned group-14 tuple; and
- direct shared-secret calculations using an unknown large modulus.

The 64-bit ceiling is a resource bound, not a security claim. Groups generated
inside that ceiling are intentionally too small for real security. Conversely,
the preserved 2048-bit group is a legacy interoperability surface whose Python
integer operations are not documented as constant time. Neither path should be
used to design a new authenticated protocol.

## Compatibility and migration

The public classes, function names, arguments, integer return values, and
`GROUP_2048` constant remain unchanged. A reconstructed `DHGroup` with the
exact group-14 numeric tuple is accepted even if its display name differs.
Existing demonstrations at 64 bits or below continue to work.

Callers that previously requested runtime groups above 64 bits must migrate to
a maintained protocol/provider rather than catching a warning or relying on a
long-running parameter search. The typed failure makes that incompatibility
visible before cryptography removes FFDH support entirely.

## Verification and rollback

Regression tests run with warnings promoted to errors. They pin the group-14
public-key equations, the two-peer shared-secret equation, the demo-size and
attempt ceilings, and rejection of unknown large groups and moduli.

Rollback means deploying the preceding Gludd revision or reverting this
migration as one unit. Restoring the deprecated PyCA calls is only a temporary
compatibility rollback: it reintroduces warnings today and will stop working
when PyCA removes FFDH. Do not weaken the size checks or add warning filters as
a rollback mechanism.
