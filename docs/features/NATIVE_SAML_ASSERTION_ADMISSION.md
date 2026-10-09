# Native SAML assertion admission

## Outcome

S46 replaces the `xml.saml_processor` role's inline Python command, temporary
input, structural signature flag, and output file with one collection-native
controller action. The new boundary admits exactly one SAML 2.0 Response or
Assertion, verifies one assertion signature with an explicit certificate and
`signxml==5.1.0`, applies protocol bindings to SignXML's returned
`signed_xml`, and returns only bounded allowlisted claims.

The action is a pure, finite read. Normal and Ansible check mode perform the
same cryptographic and policy validation and report `changed: false`. There is
no network, metadata discovery, subprocess, temporary state, listener, worker,
cache, retry loop, database row, or schema mutation.

## Why SignXML and verified-subtree consumption

The implementation uses the maintained
[SignXML project](https://github.com/XML-Security/signxml) rather than a custom
XMLDSig implementation. Its verifier documentation makes the critical
application rule explicit: consume the verifier's returned signed node, not
the caller's original XML. S46 passes an exact signature location, one-reference
expectation, explicit X.509 certificate, `ID` attribute, and SHA-2-only
signature/digest sets. Policy reads then start from `VerifyResult.signed_xml`.

That distinction prevents a valid signature over one assertion from blessing a
different, attacker-controlled assertion in the same document. The outer
Response is used only to add restrictive status, issuer, destination, and
request checks; identity and claims always come from the verified assertion.

## Practitioner and upstream evidence

- [SignXML's maintained README](https://github.com/XML-Security/signxml#readme)
  documents the "see what is signed" rule and recommends an expected signature
  location. S46 mechanically pins both and has a regression where the raw and
  verified subjects differ.
- python3-saml
  [issue #282](https://github.com/SAML-Toolkits/python3-saml/issues/282), open
  since 2022, records practitioner confusion about certificates and what
  `validate_sign` actually establishes. S46 has no optional structural mode:
  callers supply one certificate and a cryptographic verification failure is a
  closed result.
- python3-saml
  [issue #39](https://github.com/SAML-Toolkits/python3-saml/issues/39), open
  since 2016, shows duplicate SAML attributes creating ambiguous application
  values. S46 rejects duplicate claim names, multiple attribute statements,
  nested values, excessive values, and every claim outside the explicit
  allowlist.
- The OASIS
  [SAML V2.0 security considerations](https://docs.oasis-open.org/security/saml/v2.0/saml-sec-consider-2.0-os.pdf)
  require recipients to validate audience, bearer confirmation, freshness,
  destination, and request correlation in addition to XML signature validity.
  S46 binds all of them before returning claims.

## Admission contract

1. Accept either a no-log inline value or a root-confined file, never both.
   File admission requires an absolute non-symlink root, no symlink path
   component, a regular single-link file, a no-follow descriptor, stable
   device/inode/size/mtime identity, and an exact lowercase SHA-256 digest.
2. Reject DTDs, entities, processing instructions, XSLT, encrypted assertions,
   malformed XML, deep/large trees, duplicate IDs, multiple assertions, and
   signatures outside the direct assertion boundary.
3. Require one reference to the selected assertion ID. Admit only RSA/ECDSA
   SHA-256/384/512 or their RSA-PSS variants and SHA-256/384/512 digests.
4. Verify with SignXML 5.1.0, exactly one supplied PEM certificate, no resolver,
   and one expected reference. Discard application access to the original
   assertion after verification.
5. Require exact issuer, one exact audience, Response Success status when a
   Response is supplied, exact destination/recipient, exact `InResponseTo`, a
   bearer confirmation, timezone-aware validity windows, and clock skew no
   greater than 120 seconds.
6. Require one bounded NameID and at most one attribute statement. Reject
   duplicate, nested, empty, oversized, excessive, or non-allowlisted claims.
7. Return only stable identifiers, the input digest, pinned bindings, bounded
   validity timestamps, NameID, and allowlisted scalar claim lists. Never
   return raw XML, a certificate, signature bytes, parser exceptions, or an
   ambient path.

## Resource ceilings

| Resource | Ceiling |
|---|---:|
| XML input | 1 MiB |
| Trusted certificate | 32 KiB / exactly one PEM certificate |
| XML elements / depth | 10,000 / 64 |
| XML IDs | 256, all unique |
| Allowed/returned claims | 32 |
| Values per claim | 16 |
| Bytes per claim value | 1,024 |
| Serialized result | 64 KiB |
| Clock skew | 120 seconds |
| Signature references | 1, same-document assertion ID |
| Network/process/listener/temp-state allocation | 0 |

## Zero-downtime delivery

1. Build SignXML and the XML collection into the digest-addressed controller
   execution environment. Run warning-fatal focused tests, real XML signature
   verification, the FQCN Molecule scenario, dependency/boundary checks, and
   the exact-head gate before admission to `development`.
2. Canary every configured identity provider with a fresh signed assertion,
   its exact request correlation, explicit trust anchor, and the candidate image
   digest. Emit only content-free admitted/rejected counts and bounded phase
   status; never log assertion, certificate, subject, or claims.
3. Shift new finite tasks to the candidate image while in-flight tasks drain on
   the old image. No shared state or listener handoff exists, so both images can
   run concurrently without conflicting ownership.
4. Drain the old image after its bounded tasks finish. Keep its digest and trust
   configuration addressable during the observation window.
5. Roll back by routing new tasks to the prior image. Reject candidate failures
   and repair forward; never re-enable the legacy inline parser or its
   `signature_valid: unverified` result.

## Verification

The focused suite signs an assertion with a freshly generated RSA certificate
and verifies it through the real SignXML API. It also proves verified-subtree
consumption, wrapping/duplicate-ID rejection, DTD/entity/XSLT/encrypted input
rejection, SHA-1 and external-reference rejection, exact protocol bindings,
freshness and skew handling, duplicate/non-allowlisted/nested claim rejection,
stable no-follow file reads, digest mismatch, symlink/hard-link refusal,
normal/check-mode equivalence, redacted action failures, remote-module bypass,
role/FQCN wiring, execution-environment ownership, and the absence of ambient
I/O escape hatches. The branch-aware selector enforces at least 85% aggregate
coverage and 75% for each production file.
