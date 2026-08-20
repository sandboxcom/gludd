# Beta4 Protocol and Numeric OSS Decision

**Status:** Accepted for `v0.1.0-beta4` implementation gating  
**Decision date:** 2026-08-20  
**Scope:** Noise, PROXY protocol v2 TLVs, chat JSON, and budget arithmetic

## Context

The focused Python 3.14 run was:

```text
make test-files TESTFILES='tests/unit/test_noise_protocol_deep.py tests/unit/test_proxy_protocol_deep.py tests/unit/test_protocol_serialization_deep.py'
```

It produced **15 failed and 143 passed** tests in 4.49 seconds. The failures are
not 15 independent implementation bugs: six share one Noise role-mapping bug,
five are malformed PROXY test frames, two are invalid expectations, and two are
budget representation or validation defects.

Beta4 already freezes `cryptography` into the Gludd core artifact. The artifact
must not acquire an unproved interpreter, native-wheel, or collection dependency.
This decision therefore follows the dependency-plane contract in
[Python Runtime Boundaries](PYTHON_RUNTIME_BOUNDARIES.md): Ansible controller and
managed-host Python remain separate from Gludd core Python.

## Decision

Beta4 adds **no runtime dependency** for these failures.

1. Keep the local Noise state machine over the already-pinned PyCA
   `cryptography` primitives. Repair its role-dependent pre-message mapping,
   protocol name, and AES-GCM nonce construction, then gate it with the official
   Noise revision 34 vectors. A third-party Python Noise framework may be used
   only as a pinned, development-only differential oracle after it passes those
   same vectors.
2. Keep the bounded local PROXY v2 parser over `struct` and `ipaddress`. Correct
   the five invalid test frames. Do not make production parsing accept the extra
   byte or truncated values in those fixtures.
3. Keep standard-library `json` for `ChatMessage`. Correct the test to decode the
   outer document before comparing embedded JSON text.
4. Use standard-library `decimal.Decimal` for budget decisions and arithmetic,
   converting compatibility floats with `Decimal(str(value))`. Preserve beta4's
   public float-shaped response. A later versioned wire schema may expose
   canonical decimal strings.

This is the smallest dependency surface and the strongest conformance path. It
also avoids adding imports to model-task startup, frozen-binary collection, SBOM,
or vulnerability work while the beta4 core is frozen.

## Failure-to-action map

### Noise: seven nodes

| Failing node | Classification | Beta4 action |
|---|---|---|
| `TestHandshakeNK::test_nk_full_handshake` | Implementation defect | Map the responder's local static key to the responder pre-message before hashing. |
| `TestHandshakeNK::test_nk_transport` | Same implementation defect | Covered by the same state fix; retain bidirectional transport assertion. |
| `TestHandshakeKK::test_kk_full_handshake` | Implementation defect | Hash initiator then responder pre-message keys regardless of the local role. |
| `TestHandshakeKK::test_kk_transport` | Same implementation defect | Covered by the same state fix; retain bidirectional transport assertion. |
| `TestHandshakeIK::test_ik_full_handshake` | Implementation defect | Map responder local static and initiator remote static identities correctly. |
| `TestHandshakeIK::test_ik_transport` | Same implementation defect | Covered by the same state fix; retain bidirectional transport assertion. |
| `TestPrologue::test_different_prologues_produce_different_handshake` | Invalid expectation | Assert matching prologues succeed, mismatched prologues fail, and completed handshake hashes bind the prologue. Do not require empty-AD transport ciphertext to differ. |

The six real failures originate in initialization: local `s` is treated as the
initiator key and remote `rs` as the responder key for both roles. The official
specification instead requires pre-message public keys to be hashed in
**initiator-then-responder identity order**. On responder NK and IK the responder
key is omitted; on responder KK the two identities are reversed.

The seventh expectation contradicts the specification. A prologue is mixed into
handshake hash `h`, not into transport encryption keys. Its contract is agreement
and channel binding, not necessarily different ciphertext for the same transport
plaintext and empty associated data.

Source review exposed two conformance defects that the self-roundtrip tests do
not detect and that are release blockers with the role fix:

- the protocol name is hard-coded to `Noise_XX_25519_AESGCM_SHA256`, even for NK,
  KK, and IK;
- AES-GCM encodes the 64-bit nonce little-endian, while Noise requires four zero
  bytes followed by the nonce in big-endian order.

A pair of peers with the same defect can pass a roundtrip. Only official vectors
and an independent implementation can prove wire interoperability.

### PROXY protocol v2: five nodes

| Failing node | Classification | Beta4 action |
|---|---|---|
| `TestProxyV2TLVs::test_multiple_tlvs` | Malformed fixture | Encode `example.com` at its actual 11-byte length instead of truncating it with `9s`. |
| `TestProxyV2TLVs::test_ssl_tlv` | Malformed fixture | Use the top-level `0x20` SSL TLV and place `0x21` only in the SSL sub-TLV payload after client flags and verify. |
| `TestProxyTLVHelpers::test_tlv_utf8_accessor` | Malformed fixture | Remove the extra packed byte and use the actual hostname length. |
| `TestProxyTLVHelpers::test_tlv_lookup_by_type` | Malformed fixture | Remove extra packed bytes and stop truncating `h2-17` and `myhost.local`. |
| `TestProxyTLVEdgeCases::test_truncated_tlv_handled` | Malformed fixture | Encode `type:uint8,length:uint16` with declared length 100 and an actually short value. |

The generic wire shape is `type:uint8 + length:uint16 network order + value`.
The affected fixtures use `!BBH...`, inserting an extra zero byte before the
length, or fixed-size fields shorter than the supplied string. The parser
correctly reads the bytes it receives. Accommodating those bytes in production
would create a non-standard dialect and weaken bounds checking.

Nested SSL decoding and CRC32C verification are worthwhile follow-up features,
but neither is required to resolve these five nodes. Until implemented, unknown
TLVs remain opaque bytes and trusted-source policy stays above this parser.

### Serialization and budget: three nodes

| Failing node | Classification | Beta4 action |
|---|---|---|
| `TestChatMessageRoundtrip::test_json_serializable` | Invalid expectation | Decode the serialized document and compare `content`; valid outer JSON must escape quotes in the embedded JSON string. |
| `TestBudgetEnvelopeSerialization::test_try_spend_denied_shape` | Implementation defect | Perform limit, spend, and remaining calculations as `Decimal` constructed from string forms. |
| `TestBudgetEnvelopeInvalidInputs::test_negative_inf_limit_raises_on_init` | Implementation defect | Validate negativity before the accepted positive-infinity sentinel so `-inf` gets the non-negative diagnostic. |

`Decimal.from_float()` is not the migration path because it deliberately retains
the binary approximation. `Decimal(str(value))` preserves the caller's ordinary
decimal intent while the beta4 API continues returning floats. Integer micros
would be faster and JSON-native, but an implicit fixed scale risks rounding token
prices and cannot be adopted without a versioned currency/unit contract.

## Candidate evaluation

The versions and metadata below were checked on 2026-08-20. “Python 3.14” means
an explicit upstream classifier/wheel or the existing repository's own 3.14 run,
not merely an unconstrained `Requires-Python` field.

| Candidate | Maintenance and license | Python 3.14 and dependency cost | Security/performance ruling |
|---|---|---|---|
| Existing PyCA `cryptography` boundary | Production/stable; Apache-2.0 OR BSD-3-Clause; already pinned in `uv.lock` | Existing 3.14-tested artifact dependency; zero new import or wheel | **Select.** Vetted constant-time primitives remain below a small, vector-gated state machine. |
| `noiseprotocol` 0.3.1 | Released 2020-03-03; Alpha; one maintainer; MIT | Metadata covers Python 3.5–3.8; revisions 32/33; new runtime dependency | Reject for runtime. Optional differential oracle only; upstream vector failures remain open. |
| `dissononce` 0.34.3 | Released 2019-04-24; MIT | Requires Python no newer than 3.7 | Reject: incompatible with the beta4 Python floor and 3.14 gate. |
| `noiseframework` 1.3.0 | Released 2025-11-25; Alpha; one maintainer; MIT | Python >=3.8 but classifiers stop at 3.12; new API and frozen import graph | Reject for beta4. Its claimed vector suite can be evaluated later, but recency and maintenance concentration are migration risk. |
| Existing `struct` and `ipaddress` parser | CPython standard library; PSF-2.0 | Already frozen; no dependency or startup cost | **Select.** Current generic parser matches the five corrected wire fixtures. |
| `proxy-protocol` 0.11.3 | Released 2024-04-27; Alpha; one maintainer; MIT | Metadata/classifiers cover 3.8–3.12, not 3.14; pure Python | Reject for beta4 runtime. Consider a test-only oracle for nested SSL/CRC32C follow-up. |
| Existing `json` | CPython standard library; PSF-2.0 | Already frozen and 3.14-tested | **Select.** It is correct for the failing chat case; a replacement cannot fix an invalid assertion. |
| `orjson` 3.12.0 | Released 2026-08-14; stable; MPL-2.0 plus Apache-2.0/MIT terms | Has 3.14 wheels; adds a native Rust extension; returns bytes; Decimal needs a hook | Reject for beta4: no tiny-message benchmark justifies frozen/native and API migration risk. |
| `msgspec` 0.21.1 | Released 2026-04-12; Beta; one maintainer; BSD-3-Clause | Has 3.14 wheels; native extension; new schema API | Reject for beta4. Revisit only with measured validation/latency gain and a versioned schema. |
| `simplejson` 4.1.1 | Released 2026-04-24; stable; three maintainers; MIT OR AFL-2.1 | Tests 3.14; pure Python with optional C extension; supports Decimal | Mature, but reject for current chat JSON. Revisit only if a future wire contract requires Decimal JSON numbers. |
| Existing `decimal.Decimal` | CPython standard library; PSF-2.0 | Already frozen and 3.14-tested | **Select.** Exact decimal accounting without a package, process, or native-wheel cost. |
| `py-moneyed` 3.0 | Released 2022-11-27; three maintainers; BSD-3-Clause | Classifiers stop at 3.11; adds currency objects and data | Reject: the current envelope needs exact arithmetic, not multi-currency semantics. |

The local model path benefits from the selected boundary: no serializer or
protocol package is imported into prompt execution, no additional model worker is
started, and no network or subprocess hop is introduced. Noise and PROXY parsing
remain linear in message/header length; budget operations are constant-sized
decimal arithmetic. Any serializer replacement requires a representative
benchmark showing a material p95 latency, CPU, or allocation improvement for
Gludd's small messages, not a third-party microbenchmark.

## Beta4 gate and follow-up slices

### Release-blocking beta4 slice

1. Correct the six Noise role failures and replace the invalid prologue assertion.
2. Derive the Noise protocol name from the selected pattern and encode AES-GCM
   nonces per revision 34.
3. Import official revision 34 vector data as test data and pass applicable
   `25519_AESGCM_SHA256` vectors for every supported pattern. Self-roundtrips alone
   do not satisfy this gate.
4. Correct the five PROXY fixtures without relaxing the parser.
5. Correct the chat JSON assertion, introduce internal Decimal budget arithmetic,
   and fix negative-infinity validation ordering while preserving public shapes.
6. Prove the focused 158 tests green on Python 3.14, then pass the repository
   coverage policy (at least 85% aggregate and 75% per file), dependency/SBOM
   checks, and frozen-artifact smoke tests. The lock file must be unchanged.

### Follow-up, not a beta4 dependency

- Evaluate a pinned Noise implementation as a development-only differential
  oracle after it independently passes the same official vectors.
- Add structured SSL sub-TLV accessors and CRC32C verification behind the current
  PROXY dataclasses; keep unknown TLVs round-trippable.
- Benchmark JSON alternatives only if model-task profiles identify serialization
  as a material bottleneck.
- Design an explicit, versioned decimal-string currency schema before changing
  public budget types or persisting amounts.

## ZDD, compatibility, and rollback

| Area | Compatibility and zero-downtime deployment | Rollback and resource bound |
|---|---|---|
| Noise | New and old implementations may not interoperate because corrected protocol names/nonces change wire bytes. Advertise a protocol version or capability, drain old handshakes, then switch new connections. Never silently fall back after authentication failure. | Roll back the binary and capability advertisement together. Handshake state is ephemeral; cap messages at the Noise 65,535-byte limit and terminate on nonce exhaustion. |
| PROXY | Fixture-only beta4 change has no runtime rollout. Future parser work must shadow-parse captured headers and compare typed and opaque TLVs before enabling. Accept PROXY only from explicitly trusted upstream addresses. | Feature-toggle a future parser adapter; the current parser remains the fallback. Enforce the 65,535-byte header bound and a read deadline. |
| Chat JSON | Parsed wire values and public dataclasses do not change. Deploy normally and verify old/new roundtrips before promotion. | Revert the test-only correction. Payload size/depth limits remain at the API boundary. |
| Budget Decimal | Shadow-compute float and Decimal decisions in one process, record mismatches without amounts, then atomically enable Decimal decisions. Return the existing float fields during beta4. | Revert the binary; no envelope schema is persisted. Decimal precision is fixed in code, and operations remain constant-sized. |

The Noise incompatibility is intentional standards correction, not a reason to
retain defective cryptography. ZDD means explicit connection draining and
capability coordination, not allowing a downgrade path.

## Acceptance matrix

| Gate | Required evidence |
|---|---|
| Focused regression | All 158 tests in the three focused files pass on Python 3.14. |
| Noise conformance | Applicable official revision 34 vectors pass with fixed keys for all supported AES-GCM/SHA-256 patterns, including NK, KK, and IK. Matching prologues succeed; mismatches fail at authenticated processing; handshake hashes bind the prologue. |
| Noise negative/security | Invalid public keys, truncated messages, tampering, wrong roles/turns, nonce exhaustion, and the 65,535-byte limit fail closed. Protocol names and nonce bytes have direct unit assertions. |
| PROXY conformance | Correct v2 headers cover IPv4/IPv6/UNIX/LOCAL, multiple TLVs, unknown TLVs, nested SSL layout, and declared-length truncation. Invalid headers never receive special compatibility parsing. |
| Serialization/numeric | `json.loads(json.dumps(message-shape))` preserves embedded content. Budget decisions are exact at decimal boundaries and reject NaN, negative infinity, and negative finite limits with stable diagnostics. |
| Performance/resources | No new runtime dependency, process, or model-path import. Focused p95 regression stays within the existing test tolerance; parser memory is bounded by the protocol maximum. |
| Packaging/release | Dependency lock and SBOM show no new package; Python 3.14 source tests and frozen-binary smoke pass; aggregate coverage is at least 85% with no touched file below 75%. |

## Upstream and practitioner evidence

- The [Noise revision 34 specification][noise-spec] (2018-07-11) defines
  initiator-first pre-message hashing, prologue semantics, per-pattern protocol
  names, the 65,535-byte message limit, and the AES-GCM big-endian nonce. The
  [official Noise site][noise-site] is the authority for framework specifications
  and vector resources.
- `noiseprotocol` issue
  [#43, “Testing additional mcginty/snow test vectors results in a failure”][noise-43]
  was opened 2021-06-23 and remained open when checked 2026-08-20. The reporter
  expanded from 104 to 408 vectors and observed 304 failures. This long-lived
  interoperability issue is why it cannot be the beta4 production oracle.
- The [HAProxy PROXY protocol specification][proxy-spec], checked 2026-08-20,
  defines the exact v2 TLV header and nested SSL layout. A
  [HAProxy community request][haproxy-forum] from 2017-03-22 and Envoy issue
  [#18520][envoy-18520], opened 2021-10-08 and still open when checked, show that
  custom TLV interoperability remains a long-lived operational concern.
- The [Python 3.14 JSON documentation][python-json] requires quotes, backslashes,
  and control characters in strings to be escaped. The failing chat assertion is
  therefore not a serializer defect.
- The [Python 3.14 Decimal documentation][python-decimal] explains exact decimal
  representation and that float conversion retains the binary value. CPython
  issue [#16535][cpython-decimal-json], opened 2012-11-22 and migrated still open,
  records the long-running request for standard JSON Decimal support. A 2022-12
  [Python community discussion][json-magic] illustrates the unresolved ambiguity
  of a generic `__json__` hook; beta4 should not invent one.
- Packaging metadata was checked on 2026-08-20 for
  [`noiseprotocol`][noiseprotocol-pypi], [`dissononce`][dissononce-pypi],
  [`noiseframework`][noiseframework-pypi],
  [`proxy-protocol`][proxy-protocol-pypi], [`cryptography`][cryptography-pypi],
  [`orjson`][orjson-pypi], [`msgspec`][msgspec-pypi],
  [`simplejson`][simplejson-pypi], and [`py-moneyed`][moneyed-pypi].

[noise-spec]: https://github.com/noiseprotocol/noise_spec/blob/master/noise.md
[noise-site]: https://www.noiseprotocol.org/
[noise-43]: https://github.com/plizonczyk/noiseprotocol/issues/43
[proxy-spec]: https://github.com/haproxy/haproxy/blob/master/doc/proxy-protocol.txt
[haproxy-forum]: https://discourse.haproxy.org/t/add-custom-tlv-to-proxy-protocol-sent-to-backend-servers/1098
[envoy-18520]: https://github.com/envoyproxy/envoy/issues/18520
[python-json]: https://docs.python.org/3.14/library/json.html
[python-decimal]: https://docs.python.org/3.14/library/decimal.html
[cpython-decimal-json]: https://github.com/python/cpython/issues/60739
[json-magic]: https://discuss.python.org/t/introduce-a-json-magic-method/21768
[noiseprotocol-pypi]: https://pypi.org/project/noiseprotocol/
[dissononce-pypi]: https://pypi.org/project/dissononce/
[noiseframework-pypi]: https://pypi.org/project/noiseframework/
[proxy-protocol-pypi]: https://pypi.org/project/proxy-protocol/
[cryptography-pypi]: https://pypi.org/project/cryptography/
[orjson-pypi]: https://pypi.org/project/orjson/
[msgspec-pypi]: https://pypi.org/project/msgspec/
[simplejson-pypi]: https://pypi.org/project/simplejson/
[moneyed-pypi]: https://pypi.org/project/py-moneyed/
