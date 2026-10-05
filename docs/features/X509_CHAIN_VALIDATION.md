# X.509 Chain Validation

## Problem and contract

The certificate issuer copied extensions requested by a CSR, but a request for
`keyCertSign` did not also declare the certificate as a CA. Certificates minted
by the project therefore had CA key use without the critical `BasicConstraints`
extension that the validator correctly requires. Valid two- and three-level
chains failed, while the key-use failure used a diagnostic spelling that callers
could not classify reliably. The expiry regression also depended on sleeping for
two seconds even though it issued a certificate for one day.

The repaired contract is deliberately explicit:

- a CSR requesting `key_cert_sign` also receives critical
  `BasicConstraints(ca=True, path_length=None)`;
- an ordinary CSR receives no CA constraint and cannot become a CA merely by
  being placed in the middle of a chain;
- every leaf-first link must match issuer-to-subject and pass signature
  verification for its actual key family;
- every non-leaf certificate must assert CA basic constraints; when `KeyUsage`
  is present, it must allow `key_cert_sign`;
- validity is checked in UTC against either the caller's explicit instant or the
  current time, and path-length constraints are enforced;
- the last certificate is the caller-supplied trust boundary. This API does not
  discover system trust or silently download a missing issuer; and
- `verify_chain` remains a structural direct-issuance check. Callers that need
  validity, CA, key-use, and path checks must use `validate_chain`.

The direct CA-intent mapping is not a new privilege path: callers already had to
request the certificate-signing key use explicitly. It makes that request
internally consistent and lets the existing fail-closed validator accept only
the certificates intended to act as issuers.

## Maintained primitives and practitioner evidence

Signature dispatch now uses cryptography's maintained
[`Certificate.verify_directly_issued_by`](https://cryptography.io/en/stable/x509/reference/#cryptography.x509.Certificate.verify_directly_issued_by)
primitive. Its documented boundary is intentionally narrow: it verifies the
issuer name and signature, while the caller remains responsible for validity,
issuer authorization, and key strength. `validate_chain` supplies the relevant
validity, basic-constraint, key-use, and path-length checks instead of duplicating
RSA, ECDSA, and EdDSA verification code.

Cryptography's higher-level
[`PolicyBuilder` and `Store`](https://cryptography.io/en/46.0.5/x509/verification/)
are the mature choice when a protocol has a separate trust store and a concrete
server or client identity policy. This local API instead receives an ordered
chain whose terminal certificate is the explicit trust input, so it does not
invent a server name or a client-auth purpose.

The long-lived practitioner report
[`pyca/cryptography#10276`](https://github.com/pyca/cryptography/issues/10276),
opened in January 2024, records the need to validate a chain without asserting a
server subject name and the maintainers' discussion of a client/generic verifier.
That history supports keeping identity authorization outside this generic chain
helper and documenting the distinction. Cryptography's 2026
[name-constraints advisory](https://github.com/pyca/cryptography/security/advisories/GHSA-m959-cc7f-wv43)
also reinforces using the maintained package rather than expanding custom
signature or identity logic; the project floor is already newer than its fixed
release.

## Security and resource boundaries

Malformed PEM, unsupported signatures, mismatched issuers, invalid signatures,
expired or future certificates, unauthorized issuers, and exceeded path lengths
all fail closed. Validation never fetches AIA URLs, consults ambient host trust,
or accepts a missing intermediate. Error strings identify the chain position and
failed policy without exposing key material or raw certificate bytes.

Each PEM certificate is parsed once per operation and the ordered chain is walked
linearly. Work is `O(n)` in chain length with `O(n)` parsed-certificate state; the
repair adds no network access, retries, workers, caches, file writes, or background
processes. Existing request-size, timeout, and worker controls remain the outer
resource boundary.

## ZDD rollout, observability, and rollback

No database, wire schema, runtime configuration, or dependency migration is
required. Deploy through the normal rolling replacement with the warning-strict
certificate family green. During a mixed-version window, new workers issue
standards-consistent CA certificates; both old and new workers continue to parse
their extensions. Existing malformed CA certificates remain rejected rather
than being grandfathered in.

Observe chain-validation success and the bounded error categories by certificate
position during the canary. A rise in missing-basic-constraints errors identifies
external or previously issued CA material that must be reissued, not bypassed.
The validator performs no hidden asynchronous work, so request completion is the
deployment drain boundary.

Rollback is a source-only release rollback with no data reversal. It restores the
old CA issuance behavior and manual signature dispatch; certificates already
issued with critical basic constraints remain readable. Before rollback, drain
in-flight issuance and retain the validation metrics so any newly produced CA can
be traced and, if necessary, reissued after roll-forward.
