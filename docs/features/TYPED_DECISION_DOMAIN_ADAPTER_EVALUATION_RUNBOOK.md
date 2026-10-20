# Universal Domain-Adapter Evaluation Runbook

Status: operator contract for TD12-02 hermetic admission evidence
Runbook version: `1.0.0`
Corpus ID: `td12-domain-adapter-corpus`
Corpus version: `1.0.0`
Authority: recommendation-only, shadow admission only

This runbook operationalizes the
[typed-decision implementation contract](TYPED_DECISION_IMPLEMENTATION_CONTRACT.md)
for the universal domain adapters. It is deliberately narrower than a release
or production-promotion procedure. It explains how to pin one corpus and one
candidate, reproduce the deterministic report, verify the resulting typed
receipt, make a fail-closed shadow decision, and roll back without disturbing
the current path.

## Purpose and immutable inputs

The evaluator applies one provider-neutral contract to 28 cases. It has no
provider branch and no domain-specific branch in its registry. Domain-specific
logic remains inside the four reviewed native projections:

| Task slice | Native projection pinned by the route | Normalized answer |
| --- | --- | --- |
| chemistry/materials | [MolSSI QCSchema](https://molssi.org/software/qcschema-2/) output, integer version 2 | Choice |
| code/firmware | [OASIS SARIF 2.1.0](https://www.oasis-open.org/standard/sarifv2-1-os/) | Choice |
| hardware | [DMTF Redfish MetricReport 1.5.2](https://www.dmtf.org/standards/redfish) | Score |
| configuration | configuration-plan decision 1.0.0, with [CUE](https://cuelang.org/docs/) as the external validation model | Noul |

The corpus includes valid, abstain, malformed, adversarial, cross-domain,
cross-request, and replay-match/mismatch cases. Every case is assigned to the
metric dimensions that it is allowed to affect. Missing cases, duplicate IDs,
missing domains or categories, incomplete dimension assignments, invalid
replay pairs, and an unknown corpus version fail before evaluation.

The immutable inputs to one evaluation are:

1. the exact corpus manifest and its `corpus_digest`;
2. the exact sorted registry routes, including each adapter code digest, whose
   canonical identity is `adapter_bundle_digest`;
3. the exact evaluator source/build identity recorded as
   `evaluator_code_digest`;
4. the frozen thresholds carried by the corpus;
5. the typed requests, exact native transport bytes, measured byte digests,
   claimed redaction digests, and replay relationships in those 28 cases; and
6. operator-supplied receipt metadata: artifact ID, UTC creation time, run ID,
   project scope, and redaction-profile digest.

Do not substitute a branch name, model alias, endpoint label, mutable package
tag, or human-readable report for any digest. Those values are useful labels,
not identity.

## Pin the corpus and candidate

Pin all inputs before interpreting a result:

1. Check out or otherwise identify one reviewed source commit. Record its
   immutable commit digest in the change record.
2. Build the registry with the reviewed adapter code digest. All four routes
   must carry that exact digest or their own independently reviewed digests.
3. Call `build_hermetic_domain_adapter_corpus` with the pinned adapter code
   digest. Confirm `corpus_id == "td12-domain-adapter-corpus"`,
   `corpus_version == "1.0.0"`, and exactly 28 cases.
4. Record the returned `corpus.digest` as `corpus_digest`. Recompute it from the
   canonical manifest; never copy it from an earlier run.
5. Record the digest of the exact evaluator code that will produce the receipt
   as `evaluator_code_digest`.

The corpus identity is candidate-independent: it binds the requests, actual
transport bytes, expectations, replay pairs, deterministic logical-latency
samples, and thresholds, but excludes candidate adapter code digests. This
allows an old and a new candidate to face identical evidence.

The report identity is candidate-bound: `adapter_bundle_digest` binds the full
sorted route snapshots, including adapter IDs, versions, capabilities, native
schema/version pairs, task-profile bindings, and adapter code digests. A code or
route change must therefore change the report and receipt even when the corpus
does not change.

Never compare candidates if their `corpus_digest` values differ. Never treat
reports as belonging to the same candidate if their `adapter_bundle_digest`
values differ.

## Run the deterministic evaluation

Use the narrow, single-worker project target from the repository root:

```console
make test-specific TESTFILE=tests/unit/test_typed_decision_domain_adapter_evaluation.py GLUDD_XDIST_WORKERS=1 PYTEST_ARGS='-q -n 0 -W error'
```

For the focused coverage receipt, use the frozen evaluator configuration:

```console
make coverage-files COVERAGE_TESTFILES='tests/unit/test_typed_decision_domain_adapter_evaluation.py' COVERAGE_CONFIG=tests/unit/coverage_typed_decision_domain_adapter_evaluation.ini COVERAGE_REPORT=.gate-logs/typed-decision-domain-adapter-evaluation-coverage.json COVERAGE_AGGREGATE_MIN=85 COVERAGE_PER_FILE_MIN=75 OBSERVED_ROOT=.gate-logs/observed OBSERVED_HEARTBEAT_SECS=30 OBSERVED_QUIET_SECS=900 OBSERVED_MAX_SECS=3600 OBSERVED_RETAIN_RUNS=20
```

The application sequence is:

1. construct the pinned fixture with
   `build_hermetic_domain_adapter_corpus`;
2. call `evaluate_domain_adapter_corpus` with that corpus and its registry;
3. compare `report.corpus_digest` with the independently recorded digest;
4. inspect every content-free case outcome and all aggregate metrics;
5. call `require_evaluation_passed`; and
6. only after that check, call `build_evaluation_evidence_receipt` with the
   exact evaluator and operator metadata.

The evaluator has no model, network, filesystem, or wall-clock access. Running
the same corpus and registry returns the same outcomes, metrics, failure codes,
report digest, and disposition. Receipt identity also includes operator-supplied
artifact and time metadata, so compare the report digest rather than expecting
receipts created at different times to be byte-identical.

The thresholds are exact and fail closed:

| Report field | Required value | Interpretation |
| --- | ---: | --- |
| `routing_accuracy` | `1.0` | Every recommend, abstain, and rejection matches the pinned expectation. |
| `schema_rejection_rate` | `1.0` | Every assigned malformed and cross-domain case rejects exactly. |
| `redaction_safety_rate` | `1.0` | Secret, digest-mismatch, and failed-redaction behavior matches exactly without leakage. |
| `replay_safety_rate` | `1.0` | Every assigned offline semantic match or mismatch is correct. |
| `latency_budget_pass_rate` | `1.0` | Every frozen logical sample is at or below its case ceiling. |
| `latency_p95_ms` | `<= 20.0 ms` | Nearest-rank p95 over frozen logical samples only. |

No rounding tolerance, partial-credit policy, retry, case deletion, or operator
waiver may turn a failing rate into a pass. A changed threshold changes the
corpus digest and requires a new review.

## Verify the evidence receipt

`build_evaluation_evidence_receipt` returns the canonical typed artifact rather
than an ad hoc JSON report. Verify it as untrusted input even when it came from a
local run:

1. Parse it with the normative draft-2020-12 schema and semantic validator.
   Require schema `gludd.typed-decision.artifact`, version `1.0.0`, and kind
   `evidence_receipt`.
2. Recompute the artifact's RFC 8785/JCS-compatible SHA-256 identity. Reject
   non-canonical bytes, duplicate keys, unknown fields, and unknown versions.
3. Require `security.authority == "recommendation_only"`, content state
   `digest_only`, an exact project scope, the pinned redaction-profile digest,
   and a passed secret scan.
4. Require `payload.identity_fingerprint == adapter_bundle_digest`,
   `payload.corpus_manifest_digest == corpus_digest`, and
   `payload.evaluation_report_digest == evaluation_report_digest` recomputed
   from the full report.
5. Require the producer code digest to equal the pinned
   `evaluator_code_digest`. The in-toto test-result attestation subject must be
   `adapter_bundle_digest`; its attestation digest must be
   `evaluation_report_digest`; its verification state must be `verified`.
6. Require the extension's corpus ID, version, case count, thresholds,
   `failure_codes`, and passed flag to agree exactly with the report.
7. Require disposition `shadow_only` when every threshold passes and `reject`
   otherwise. The evaluator must never emit `canary_eligible`.

Case outcomes are intentionally content-free. They may contain a case ID,
stable reason code, result digest, and Boolean safety flags. They must not
contain native output, request state, secret category detail, credentials,
headers, hidden reasoning, or provider responses. Any mismatch in the checks
above invalidates the receipt; do not patch or re-sign it in place.

## Interpret abstention and rejection

Abstention and rejection have different meanings:

- `abstain` is a valid normalized result for a supported route that cannot or
  should not recommend. A blocked or failed redaction outcome abstains and
  releases zero native bytes. Expected abstentions count toward routing and
  replay correctness; they are not silently converted to recommendations.
- `reject` means the candidate input or evaluation evidence violated a
  contract. Unknown routes, wrong request bindings, malformed schemas,
  non-canonical bytes, digest mismatch, secret smuggling, non-finite values,
  and threshold regressions remain failures. They are not retried through a
  looser parser or another domain adapter.
- A replay mismatch says that two immutable results normalize to different
  decision semantics. Preserve both artifacts and the mismatch receipt. Do not
  rerun transport and do not overwrite either result.

The operator must inspect stable reason codes, never raw rejected content.
Unexpected abstention is a routing failure. Expected rejection that becomes an
abstention is also a routing failure because it weakens the contract.

## Shadow admission and promotion evidence

A green hermetic report admits a candidate to offline comparison and shadow
observation only. Its receipt says `shadow_only`; it does not replace or modify
the current authoritative route. A failed report says `reject` and blocks all
forward movement.

Before any canary stage, collect and bind at least the following separate
promotion evidence to the exact corpus, adapter bundle, evaluator, deployment,
and route generation:

- the verified green hermetic evaluation receipt;
- independent schema/runtime conformance for QCSchema, SARIF, Redfish, and
  configuration inputs observed in the proposed shadow slice;
- live tail latency and deadline evidence under bounded representative load;
- concurrency, cancellation, retry, resource, and teardown evidence;
- endpoint identity, provider authentication, immutable model/runtime identity,
  and build/signature attestation;
- redact-before-observe telemetry and seeded-secret evidence across transport,
  persistence, logs, metrics, and errors;
- malicious in-process producer resistance or an explicit isolation boundary
  that removes that producer from the trusted computing base;
- quality/calibration evidence for the exact task profile and decision type;
- a successful ZDD rollback drill with the prior route kept hot; and
- a signed/approved promotion receipt naming the exact forward stage and
  rollback target.

Forward stages remain manual: `shadow -> canary_1 -> canary_5 -> canary_25`.
Automatic action is demotion only. Missing, stale, differently digested, or
cross-project evidence keeps the candidate in shadow regardless of hermetic
score.

## ZDD rollback

The evaluator and corpus are unwired, so adding or removing them cannot affect
traffic. Once a separately approved shadow or canary route exists, preserve ZDD
with this order:

1. select the prior healthy route, or the original Gludd path, for all new work;
2. stop admitting new work to the candidate generation;
3. drain accepted work within its original deadlines without moving it between
   generations;
4. append an append-only rollback event naming the failed generation, the
   selected prior generation, the triggering evidence, and the operator;
5. preserve the evaluation receipt, report, native artifact references,
   promotion receipt, and failure evidence without editing or deleting them;
6. retire only candidate-owned resources after leases reach zero; and
7. prove candidate resource absence while the prior route remains healthy.

Rollback never regenerates a favorable report, weakens a threshold, rewrites a
terminal ledger event, reuses a conflicting idempotency key, or destroys the
evidence needed to diagnose the demotion. Re-admission requires a new candidate
digest and new evidence against the still-pinned corpus, or a reviewed new
corpus version.

## Decision table

| Condition | Required operator decision | Evidence action |
| --- | --- | --- |
| Domain/profile mismatch | Reject the unmatched exact route. | Preserve content-free `domain_adapter_not_found`; do not try another domain. |
| Request binding mismatch | Reject the output. | Preserve `domain_output_request_mismatch`; do not relabel or replay it against another request. |
| Native schema/version mismatch | Reject the native document. | Preserve content-free `domain_output_invalid`; do not coerce a nearby schema or version. |
| Secret detected | Reject secret-bearing purportedly sanitized bytes and send zero bytes downstream. | Preserve only content-free `domain_output_invalid` evidence; rotate any credential if exposure cannot be excluded. |
| Sanitizer blocked or failed | Abstain and send zero bytes. | Bind the status and content-free reason; never include source/redacted digests or categories in the abstention. |
| Claimed/actual digest mismatch | Reject before parsing or replay. | Preserve `domain_output_digest_mismatch` without the bytes. |
| Replay mismatch | Reject promotion and retain both immutable artifacts. | Record the semantic mismatch; never rerun external transport as replay. |
| Non-finite score | Reject the native document. | Preserve `domain_output_invalid`; NaN and Infinity never enter metrics or evidence. |
| Threshold regression | Reject the candidate. | Preserve every deterministic `failure_codes` entry and emit receipt disposition `reject`. |
| Live-performance evidence gap | Remain shadow-only. | The logical sample may pass, but missing live latency/load evidence blocks canary admission. |

## Trust boundaries

The evaluator trusts only reviewed local code and explicit immutable metadata:
the normative schema and semantic validator, canonicalization implementation,
pinned corpus manifest, pinned registry routes and code digests, evaluator code
digest, and operator identity used for later approval. The current in-process
caller and sanitizer are also trusted to issue truthful request-binding and
redaction metadata. That last trust is an acknowledged residual boundary, not a
property proved by the corpus.

Native output bytes, model/provider output, extensions, mutable aliases,
endpoint labels, claimed digests, branch names, and human-written summaries are
untrusted. The evaluator independently measures the actual bytes, checks
canonical form and schema, requires exact route and request binding, normalizes
through the selected adapter, and compares offline replay semantics.

The receipt proves how the pinned evaluator classified the pinned corpus with
the pinned adapter bundle. It does not authorize execution, deployment,
credential use, mutation, release, or promotion. It does not authenticate a malicious in-process producer.
It also does not prove the sanitizer implementation correct, prove a remote
endpoint's identity, or establish real-world quality outside the corpus.

Logical fixture latency is not production performance evidence. It is a frozen
admission sample used to make evaluator behavior deterministic. Production
claims require separate live tail latency, concurrency, deadline, cancellation,
provider authentication, resource, teardown, and malicious in-process producer
resistance evidence for the exact deployment generation.

## Operator checklist

Before evaluation:

- [ ] Record the reviewed source commit and `evaluator_code_digest`.
- [ ] Record every route and adapter code digest.
- [ ] Confirm corpus ID/version, 28 cases, thresholds, and `corpus_digest`.
- [ ] Confirm the candidate and baseline use the same `corpus_digest`.
- [ ] Confirm no live capability, provider credential, or production route is
      available to the hermetic process.

After evaluation:

- [ ] Require all five safety rates to equal `1.0` and logical p95 to be at most
      `20.0 ms`.
- [ ] Inspect every content-free outcome and require empty `failure_codes`.
- [ ] Run `require_evaluation_passed` before constructing admission evidence.
- [ ] Recompute the report and receipt digests independently.
- [ ] Verify corpus, adapter bundle, evaluator, project, redaction profile,
      attestation, thresholds, and disposition bindings.
- [ ] Store the immutable report and receipt; never store rejected native
      content as a diagnostic shortcut.
- [ ] Record `shadow_only` or `reject`; never infer canary eligibility.

Before promotion or rollback:

- [ ] Bind all live evidence to the exact deployment and route generation.
- [ ] Keep the current authoritative route healthy and selectable.
- [ ] Require human approval and a signed/approved promotion receipt.
- [ ] Exercise the rollback order and verify the append-only receipt.
- [ ] On demotion, preserve evidence and prove candidate resource absence.

## Practitioner-issues ledger

Status checked: 2026-09-27. The **Source fact** column paraphrases only what
the linked authoritative project tracker reports, including the visible issue
state. The next column is explicitly Gludd's inference from that fact and the
mitigation or test it motivates. A closed upstream issue remains useful history;
it does not prove every producer, consumer, or older deployment has adopted the
resolution. An open issue is a regression hypothesis, not proof that Gludd is
affected.

| Domain | Direct source, date, and status | Source fact | Gludd inference and mitigation/test mapping | Residual risk |
| --- | --- | --- | --- | --- |
| QCSchema / chemistry-materials | [MolSSI QCSchema #55](https://github.com/MolSSI/QCSchema/issues/55). Opened 2018-10-23; Open status as of 2026-09-27. | The issue asks whether bond and fragment lists may be reordered and whether differently ordered molecule arrays should resolve to the same hash. | **Inference:** byte identity and chemistry-semantic equality are not interchangeable. **Mitigation/test:** preserve the exact native digest, compare only normalized decision semantics during replay, reject a SARIF-shaped chemistry document in `chemistry_cross_schema`, and reject relabeling across the shared adapter in `materials_cross_request`. | The v1 projection does not decide full-molecule canonical equivalence. A later richer chemistry adapter needs domain-reviewed atom, bond, fragment, unit, and frame semantics before it may deduplicate molecular content. |
| QCSchema / chemistry-materials | [MolSSI QCSchema #68](https://github.com/MolSSI/QCSchema/issues/68). Opened 2020-01-23; Open status as of 2026-09-27. | The issue says the QCSchema repository documentation lagged the behavior and documentation implemented in QCElemental, creating a misleading view of the schema in practice. | **Inference:** an ecosystem name or generated model is not a sufficient compatibility claim. **Mitigation/test:** route on the exact QCSchema name and integer version, parse a deliberately narrow projection, and keep `chemistry_cross_schema` plus `materials_cross_request` in the immutable corpus. | A producer can still call a payload “QCSchema” while implementing different semantics. Live promotion needs pinned upstream fixtures and independent schema/runtime conformance for the exact producer revision. |
| SARIF / code-firmware | [OASIS SARIF #122](https://github.com/oasis-tcs/sarif-spec/issues/122). Opened 2018-03-12; Closed status as of 2026-09-27. | The issue records that path-derived fingerprint contributions can differ across runtime environments, making result fingerprints fail to match after path normalization. | **Inference:** a SARIF path or producer fingerprint must not become Gludd artifact identity. **Mitigation/test:** identity uses measured canonical bytes plus request and adapter digests; `code_cross_schema` verifies that another native schema cannot be routed as SARIF. | External SARIF consumers may still deduplicate or relocate the same finding differently. The narrow adapter intentionally does not promise cross-consumer alert continuity. |
| SARIF / code-firmware | [OASIS SARIF #615](https://github.com/oasis-tcs/sarif-spec/issues/615). Opened 2023-11-10; Open status as of 2026-09-27. | The issue reports unclear relationships among `guid`, `correlationGuid`, `fingerprints`, `partialFingerprints`, and `workItemUris`, especially when tools and result-management systems both assign identity. | **Inference:** those fields cannot silently supply replay, authorization, or correlation identity. **Mitigation/test:** the v1 adapter admits only its exact result/property projection, binds correlation outside SARIF, and `firmware_secret_smuggling` rejects an extra property-bag authorization value. | Rich SARIF correlation and external-property files remain unsupported. A future adapter must freeze their identity and dereference rules before widening the accepted projection. |
| Redfish / hardware | [DMTF Redfish Service Validator #80](https://github.com/DMTF/Redfish-Service-Validator/issues/80). Opened 2017-08-15; Closed status as of 2026-09-27. | The reporter showed the validator failing to resolve a `ServiceRoot.v1_0_2` schema alias from metadata and asked how metadata and local schema files had to be arranged. | **Inference:** remote metadata resolution and local schema lookup are separate, fallible interoperability boundaries. **Mitigation/test:** hermetic evaluation dereferences nothing, requires the exact Redfish MetricReport 1.5.2 discriminator, and `hardware_cross_schema` rejects a configuration document presented on the hardware route. | A live service can publish missing, stale, or vendor-specific metadata. This corpus does not validate service discovery, TLS, metadata retrieval, or OEM references. |
| Redfish / hardware | [OpenBMC bmcweb #305](https://github.com/openbmc/bmcweb/issues/305). Opened 2025-03-17; Open status as of 2026-09-27. | The issue reports that OpenBMC accepted a whole Sensor resource URI for `MetricProperties` but rejected the property-level URI described by DSP0268, limiting compliant arbitrary-property telemetry definitions. | **Inference:** advertised Redfish support does not prove identical telemetry semantics across implementations. **Mitigation/test:** Gludd consumes an already captured, exact-version MetricReport projection instead of creating definitions, and `hardware_semantic_match` verifies normalized replay independently of native numeric spelling. | The fixture does not prove that a vendor sampled the intended property, timestamped it correctly, or enforced profile/authentication rules. Those require live shadow evidence for each hardware generation. |
| CUE / configuration | [CUE #98](https://github.com/cue-lang/cue/issues/98). Opened 2021-07-03; Closed status as of 2026-09-27. | The report expected `cue vet` to reject YAML missing a schema field, but the shown invocation produced no error until a present field violated a constraint. | **Inference:** successful constraint unification is not automatically proof of a complete deployable artifact. **Mitigation/test:** the configuration adapter requires its closed exact schema and fields, while `configuration_malformed` rejects the wrong probability shape/version. | CUE command modes and versions can assign different meaning to incomplete values. Gludd does not treat an external “vet passed” result as its typed-decision validation receipt. |
| CUE / configuration | [CUE #1487](https://github.com/cue-lang/cue/issues/1487). Opened 2022-01-20; Open status as of 2026-09-27. | The report imports GitLab's JSON Schema into CUE, then finds that a configuration accepted by the source schema yields an incomplete-value error during concrete evaluation/export because of an ambiguous disjunction. | **Inference:** cross-schema translation can change acceptance or concreteness even when both endpoints are valid tools. **Mitigation/test:** the native contract is the exact configuration-plan decision 1.0.0 shape, not a generated round trip, and `configuration_nonfinite` proves non-standard numeric JSON cannot cross the boundary. | The corpus does not establish arbitrary CUE-to-JSON-Schema round-trip equivalence. Any generated schema must be digest-pinned and tested against golden positive and negative artifacts before shadow use. |

## Practitioner failure classes

The runbook turns long-lived user and practitioner reports into operator checks:

| Practitioner report | Runbook consequence |
| --- | --- |
| [Pydantic discriminated unions #619](https://github.com/pydantic/pydantic/pull/619), [list unions #3608](https://github.com/pydantic/pydantic/issues/3608), and [nested OpenAPI unions #7491](https://github.com/pydantic/pydantic/issues/7491) | Generated schema and runtime parsing may diverge for years. Verify both the normative schema and runtime behavior; never infer acceptance from generated OpenAPI alone. |
| [Temporal workflow determinism](https://community.temporal.io/t/workflow-determinism/4027) and [nondeterministic replay](https://community.temporal.io/t/why-cant-workflows-contain-non-deterministic-code-and-how-does-using-activity-solve-the-problem/3231) | Compare recorded immutable results offline. A new external call is a new activity and artifact, never replay. |
| [OpenTelemetry sanitization default #3104](https://github.com/open-telemetry/opentelemetry-specification/issues/3104) | Redact before persistence or observation. Do not assume downstream telemetry will remove sensitive content. |

These reports justify the fail-closed defaults; they are not evidence that this
candidate passes. Candidate evidence comes only from the exact immutable inputs
and checks above.
