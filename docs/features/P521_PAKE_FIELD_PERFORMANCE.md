# P-521 PAKE Field Performance

## Status

Implemented for beta.4. P-521 SPAKE2+ now uses the exact SEC2 field modulus,
returns the declared 133-byte uncompressed messages, and fails closed if the
bounded point search cannot find a valid candidate.

## Contract

Gludd applies these rules to its existing SPAKE2+ implementation:

1. The P-521 prime is represented as `2^521 - 1`, and the curve coefficient is
   derived as `a = p - 3`. This avoids manual hexadecimal width drift.
2. A P-521 coordinate occupies exactly 66 bytes. An uncompressed protocol point
   therefore occupies 133 bytes: one SEC1 prefix plus two coordinates.
3. The existing CPython three-argument `pow` performs the modular square root.
   For the SEC2 prime, `p mod 4 = 3`, so `rhs^((p + 1) / 4) mod p` is the
   applicable square-root test.
4. Try-and-increment examines at most 256 candidates. Exhaustion raises
   `PAKEError`; it never becomes an unbounded request.
5. P-256 and P-384 parameters, point selection, encodings, and derived keys are
   unchanged.

The previous P-521 `p` literal had two extra hexadecimal digits, while `a` had
four. The square-root identity was consequently evaluated in the wrong field.
The exact product replay spent more than 15 seconds in modular exponentiation
and hit its bounded test timeout; the release gate previously waited 180
seconds for the same node.

## Mature primitive decision

[SEC2 section 2.6.1](https://www.secg.org/sec2-v2.pdf) is authoritative for the
P-521 domain parameters. The implementation derives its two simple field
constants from that definition and retains Python's maintained
[modular `pow`](https://docs.python.org/3/library/functions.html#pow). It does
not add a custom square-root implementation or another cryptographic package.

The existing `cryptography` dependency's
[`from_encoded_point`](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ec/#cryptography.hazmat.primitives.asymmetric.ec.EllipticCurvePublicKey.from_encoded_point)
API was also evaluated because it delegates compressed-point decoding and
validation to a mature backend. Replacing point selection globally would pick
a parity-specific root and could change successful P-256/P-384 exchanges during
a rolling deployment. The narrow field correction preserves those established
protocol results while removing the P-521 failure.

[RFC 9383](https://www.rfc-editor.org/rfc/rfc9383.html) remains the protocol
reference. A future standards-interoperability change to fixed RFC M and N
points is deliberately separate from this compatibility-safe performance fix.

## Practitioner evidence

A long-lived
[2018 Stack Overflow P-521 coordinate-width report](https://stackoverflow.com/questions/50002149/why-p-521-public-key-x-y-some-time-is-65-bytes-some-time-is-66-bytes)
records the recurring interoperability problem caused by treating a minimal
integer encoding as the curve's fixed-width representation. A related
[2021 WebCrypto and Node.js P-521 report](https://stackoverflow.com/questions/67376794/ecdh-for-p-521-web-crypto-api-secp521r1-nodejs-crypto-generate-a-slightly)
shows a derived-secret mismatch resolved by handling P-521 coordinates as 66
bytes. These reports span multiple ecosystems and support pinning both the
521-bit field value and its 66-byte wire width in executable tests.

## Security and constant-time considerations

The field correction restores arithmetic to the approved P-521 group instead
of a malformed larger modulus. The attempt cap turns malformed internal
parameters or unexpected mapping behavior into a typed, fail-closed error. For
independent candidates on a prime-order curve, exhausting 256 attempts has
probability at most about `2^-256`; the cap therefore controls denial-of-service
cost without creating a practical availability downgrade.

The modulus, coefficient, and wire widths are public values. This repair adds no
new secret-dependent branch. CPython big-integer arithmetic and the surrounding
legacy pure-Python point operations are not claimed to be constant-time. The
change preserves their existing timing boundary; callers that require local
side-channel resistance must isolate the process and use an audited native PAKE
implementation. Network-facing controls must continue to apply request
deadlines and rate limits. Secret values are never logged by this path.

The search performs at most 256 SHA-256 calls and modular exponentiations and
uses constant-size local integers. It creates no process, thread, lock, cache,
network request, persistent record, or cleanup job.

## Zero-downtime delivery and rollback

There is no database, configuration, stored-record, or dependency migration.
P-256, P-384, and OPAQUE traffic may continue across mixed old and new workers.
P-521 was not a functioning path in the old worker set, so enable or route P-521
traffic only to the upgraded pool until old workers are drained. Then promote
after the bounded exchange, coverage, static checks, and full gate are green.

Rollback shifts traffic to the prior pool and disables P-521 routing; no data
repair, dual write, or compensating cleanup is required. Because the corrected
wire result is the already-declared 133-byte form, no successful legacy P-521
session format is displaced.

## Verification

- A failing-first assertion pins `p = 2^521 - 1`, `a = p - 3`, and the 66-byte
  coordinate width.
- The exact full exchange has a ten-second hard bound and asserts 133-byte
  messages plus a 66-byte shared result.
- A deterministic non-residue fixture proves search exhaustion raises after
  exactly 256 candidates.
- The complete PAKE suite passes with warnings treated as errors.
- Aggregate branch coverage remains at least 85 percent, and the touched
  production file remains at least 75 percent for line and branch coverage.
- Ruff, strict mypy, docstrings, Markdown, feature-spec, and task-ledger checks
  remain warning-free and suppression-free.
