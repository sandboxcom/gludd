# Authenticated CBC integrity

## Outcome

Gludd's CBC helper emits a versioned encrypt-then-MAC frame and authenticates
the complete header, IV, and ciphertext before decryption. A modified frame
therefore fails with one integrity error even when the modified plaintext would
otherwise end in valid PKCS#7 padding.

The beta4 wire format is:

```text
GLCBC\x01 || 16-byte IV || AES-CBC ciphertext || 32-byte HMAC-SHA256 tag
```

HKDF-SHA256 derives separate AES and HMAC keys from the caller's AES-sized key.
The input key remains 16, 24, or 32 bytes. The derived AES key preserves that
size, while the authentication key is 32 bytes. Decryption rejects unknown
versions and legacy unauthenticated `IV || ciphertext` values.

## Incident and failing-first evidence

On 2026-08-25, hosted GHA run `32904013362`, Python 3.11 job
`97985787888`, exposed the probabilistic flaw in
`TestTamperDetection.test_tampered_padding_block_fails`: flipping the final CBC
byte happened to produce another valid PKCS#7 suffix, so decryption did not
raise. Local runs had passed because their random key/ciphertext did not hit the
same valid-padding outcome.

A deterministic regression now changes the previous CBC block so padding `N`
becomes valid padding `1`. It failed against unauthenticated CBC and passes only
when the frame is authenticated. The original randomized regression remains and
is no longer probabilistic because every modified tag is rejected.

## Upstream and practitioner evidence

- The PyCA `cryptography` documentation says HMAC provides message integrity
  and authenticity and exposes constant-time tag verification through
  `HMAC.verify`: <https://cryptography.io/en/latest/hazmat/primitives/mac/hmac/>.
- PyCA's 2026 advisory states that CBC encrypted content is not authenticated,
  that tampering can yield a padding oracle, and that the property cannot be
  repaired inside PKCS#7 itself:
  <https://github.com/pyca/cryptography/security/advisories/GHSA-g6cj-pr64-35w5>.
- The long-running practitioner discussion on Cryptography Stack Exchange
  explains that authenticated encryption prevents forged valid ciphertext and
  that encrypt-then-MAC also works when the encryption component is padded CBC:
  <https://crypto.stackexchange.com/questions/90144/random-data-cbc-padding-scheme>.
- A second practitioner thread cautions against relying on padding validation
  and recommends encrypt-then-MAC when CBC must be retained:
  <https://crypto.stackexchange.com/questions/100538/is-disabling-padding-integrity-check-in-cbc-implementations-a-good-way-of-overco>.
- The `jose` padding-oracle advisory records a production repair that verifies
  the HMAC tag before CBC decryption:
  <https://github.com/panva/jose/security/advisories/GHSA-58f5-hfqc-jgch>.

## Zero-downtime deployment

Beta4 has no released CBC persistence format and repository search finds no
production caller outside this helper. The release can therefore switch to the
authenticated version without an online migration. During a rolling update:

1. new writers emit only `GLCBC\x01`;
2. new readers accept only the authenticated frame;
3. malformed, truncated, unknown-version, and legacy values fail closed;
4. callers receive the same `CBCError` integrity boundary for all authentication
   failures.

No compatibility fallback decrypts unauthenticated data. If a pre-release
environment retained experimental CBC values, it must decrypt and re-encrypt
them with the old trusted build while offline before beta4 is deployed.

## Rollback

Rollback is code-only before any beta4 ciphertext is written. After beta4 data
exists, rolling back to the unauthenticated reader is prohibited because it
cannot parse or authenticate the new frame. Restore the beta4 binary first,
then recover data from the normal backup path. Never strip the header or tag to
make a value acceptable to an older reader.

## Resource and performance boundaries

- Encryption and decryption allocate one bounded HKDF output and one 32-byte
  tag per operation.
- Authentication is linear in ciphertext size and runs before CBC decryption.
- No process, thread, socket, file, temporary directory, or background task is
  acquired.
- The additional stored size is 38 bytes: six version bytes and a 32-byte tag.
- HMAC verification uses the maintained PyCA/OpenSSL implementation rather than
  a handwritten comparison in the security boundary.

## Acceptance gates

- The hosted Python 3.11 failure and deterministic padding-forgery regression
  pass with warnings as errors.
- The complete CBC behavior file passes on Python 3.11 and the project default.
- Branch coverage is at least 85% overall and at least 75% for each measured
  implementation file.
- Ruff, strict mypy, docstring, and Markdown checks pass.
- `make test-count` reports zero collection errors before commit.
