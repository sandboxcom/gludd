# TLS 1.3 Authenticated State

## Purpose

The client-side TLS 1.3 state machine now advances only after it authenticates
the protected peer message that authorizes the transition. It uses the
maintained `cryptography` AEAD, ECDSA, Ed25519, HKDF, and HMAC primitives; it
does not implement a new cipher, signature algorithm, or comparison routine.

This contract covers record decryption, server `CertificateVerify`, server
`Finished`, client `Finished`, and directional application records. It follows
[RFC 9846](https://www.rfc-editor.org/rfc/rfc9846.html), the current TLS 1.3
specification.

## State and authentication contract

| Boundary | Required evidence | Failure behavior |
| --- | --- | --- |
| Encrypted server handshake message | AEAD tag and exact handshake frame | Keep the prior state; poison record protection after an invalid tag |
| `CertificateVerify` | Supported certificate key/scheme and signature over the RFC server context plus transcript digest | Keep `CERT_RCVD`; reject the connection's remaining inbound records |
| Server `Finished` | HMAC verification over the pre-Finished transcript | Keep `CV_RCVD`; reject the connection's remaining inbound records |
| Client `Finished` | Server `Finished` was authenticated first | Enter `CONNECTED` and initialize application protectors |
| Application record | `CONNECTED`, correct directional key, and the next nonce | Reject pre-authentication use, tampering, replay, and failed-state reuse |

Handshake frame type and length are checked after decryption and before the
transcript or state changes. Authentication exceptions contain a stable reason
but no key, plaintext, signature, or ciphertext material.

Application read and write protectors persist for the lifetime of the
connection. Their sequence numbers therefore produce distinct nonces for
repeated plaintext and keep client-to-server keys separate from
server-to-client keys. An AEAD authentication failure permanently invalidates
that protector; retrying on the same cryptographic state is not a recovery
path.

## Trust boundary

`process_certificate()` parses the supplied leaf-first PEM chain, and
`process_certificate_verify()` proves possession of its leaf key. This module
does not select a trust store, validate the chain, or match the requested host.
The network caller must complete those policy checks with the separate X.509
validator before treating the peer identity as trusted.

`do_full_handshake()` is an in-process peer simulator for tests and diagnostics,
not a network trust shortcut. With no certificate input it creates a temporary
self-signed certificate and matching key. A supplied certificate requires its
matching signing key so the simulator cannot manufacture an authenticated
transition with zero-filled proof.

## Zero-downtime delivery

The change has no database, file-format, queue, or cross-process state. New and
old workers can overlap during a rolling deployment because each handshake and
its sequence numbers are process-local. Deploy new workers, exercise a complete
handshake and bidirectional record check, then drain old workers. Do not migrate
an in-flight TLS state machine between versions; close it and reconnect.

`HandshakeCryptoError` remains in the public handshake hierarchy and is also a
`ValueError`, preserving callers that historically handled malformed AEAD
input while adding a stable TLS-domain error contract.

## Security and bounded resources

- Certificate possession and Finished key confirmation are fail closed; no
  state transition occurs on unauthenticated bytes.
- Four record protectors are retained per connected client: handshake and
  application directions. Memory is constant per connection, and no worker,
  subprocess, retry loop, or background task is introduced.
- The production path uses independent monotonically increasing read/write
  sequence numbers. A failed protector cannot be reset back into service.
- Tests use the project namespace and at most two pytest workers; the feature
  starts no daemon and consumes no shared network port.
- Certificate-chain and hostname validation remain explicit prerequisites at
  the caller boundary, avoiding a misleading partial-trust claim.

## Rollback

Rollback is a code-only revert because there is no persistent migration. Drain
new workers, terminate their in-flight handshakes, restore the prior artifact,
and reconnect clients. Never serialize or reuse record keys, IVs, sequence
numbers, or a failed protector across rollback. If rollback is caused by an
interoperability problem, keep CertificateVerify, Finished, and AEAD failures
closed while gathering the rejected peer's algorithm metadata through
sanitized diagnostics.

## Evidence and practitioner context

The authoritative six-node replay failed before repair. The repaired legacy
cluster plus four new authentication regressions passes 10/10 with warnings as
errors. The TLS 1.3 suite plus its public-error adjacency passes 79/79. Targeted
line-plus-branch coverage is 89% combined; the production module has 92.2% line
coverage and 76.3% branch coverage, above both project floors.

- [RFC 9846 CertificateVerify and Finished requirements](https://www.rfc-editor.org/rfc/rfc9846.html)
  require context-bound signature verification, Finished verification before
  connection use, separate read/write sequences, and termination on incorrect
  authentication.
- [`cryptography` authenticated-encryption guidance](https://cryptography.io/en/stable/hazmat/primitives/aead/)
  warns against nonce reuse and defines `InvalidTag` for modified data or the
  wrong key, nonce, or associated data. The implementation converts that
  backend exception into the sanitized handshake-domain contract.
- The long-lived practitioner report
  [CPython issue #91826](https://github.com/python/cpython/issues/91826), open
  since 2022, documents how users mistake encryption without certificate and
  hostname validation for authenticated security. That evidence is why this
  feature states the caller-owned trust boundary explicitly and never labels a
  parsed certificate as a trusted identity.
