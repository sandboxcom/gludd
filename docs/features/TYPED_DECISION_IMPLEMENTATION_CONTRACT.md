# TD12-01 Typed-Decision Implementation Contract

Status: frozen contract; core and local persistence implemented, runtime unwired
Contract version: `1.0.0`
Schema: [`typed-decision-artifact-v1.schema.json`](../schemas/typed-decision-artifact-v1.schema.json)
Review date: 2026-09-27
Runtime authority: none

## Decision and precedence

TD12-01 defines one Gludd-owned, provider-neutral artifact contract for bounded
typed decisions. It is task-general at the data boundary and recommendation-only
at the authority boundary. Chemistry, code, hardware, configuration, and future
domains use the same envelope, provenance, security, identity, replay, and
compatibility rules. A task profile supplies domain semantics; a backend adapter
supplies wire translation. Neither may grant execution authority.

This document is normative for TD12-01. It narrows and resolves four earlier
statements:

1. The provider-admission decision correctly selects
   `failure_classification` as the first runtime canary. That is a rollout
   choice, not a limitation of the artifact contract.
2. The backlog's former phrase "no generic task router" means no
   content-derived, model-name-based, or authorization-bearing router. A pure
   route over a reviewed task-profile digest and an admitted backend identity is
   allowed and is required to avoid domain-specific provider branches.
3. The integration manifest's three-file package sketch is superseded by the
   exact ownership map below. It omitted canonicalization, validation, task
   profiles, collection projection, and their tests.
4. Typed-decision replay validates or compares immutable artifacts offline. It
   does not replace `gludd.run-bundle/v1`, reconstruct a whole run, or perform
   the guarded re-execution defined by
   [`SPEC_RUN_REPLAY_FORENSICS`](../design/specs/SPEC_RUN_REPLAY_FORENSICS.md).

The existing `SmallModelTaskPolicy`, task impact ceiling, deterministic
acceptance checks, and current authoritative path remain above this contract.
No artifact kind in v1 authorizes a command, repository or host mutation,
credential access, network write, deployment, release, completion, or security
decision.

## Canonical artifact envelope

Every durable or exchanged artifact validates against JSON Schema draft
2020-12 and contains exactly these top-level fields:

| Field | Rule |
| --- | --- |
| `schema` | Literal `gludd.typed-decision.artifact`. |
| `schema_version` | Literal `1.0.0` for the first writer. |
| `kind` | One of the eleven registered artifact kinds below. |
| `artifact_id` | Opaque, safe, unique identifier; never a filename or authority token. |
| `created_at` | UTC RFC 3339 time. Time is evidence, never part of routing. |
| `correlation` | Request, run, idempotency, and parent-artifact references. |
| `task` | Exact task profile or `null` where the artifact is task-independent. |
| `provenance` | Producer, used artifacts, sources, and attestations. |
| `security` | Project scope, classification, redaction, persistence, retention, and fixed recommendation-only authority. |
| `payload` | Strict kind-specific data. |
| `extensions` | URI-namespaced, non-authoritative metadata only. |

Objects reject unknown fields except within `extensions` and the explicitly
opaque request `state`. Duplicate JSON keys, NaN, Infinity, lone surrogates,
negative zero, invalid UTF-8, cyclic values, and values above implementation
bounds fail before hashing or transport. Semantic validation additionally checks
unique question and option IDs, exact response-to-request binding, finite
scores, declared labels, probability bounds and sums, and cross-field limits
that JSON Schema cannot express.

Canonical bytes use RFC 8785 JSON Canonicalization Scheme (JCS), with the
verified negative-zero erratum applied, followed by SHA-256. The content digest
is stored beside the artifact by the repository or in an `artifactReference`;
it is not embedded in the object it hashes. Implementers must evaluate a
maintained RFC 8785 implementation before writing a serializer. Ordinary
`json.dumps(sort_keys=True)` is not a claimed cross-language JCS implementation.

`artifact_id` distinguishes records. A digest establishes immutable content
identity. Equality, deduplication, replay, migration, promotion, and parent links
always compare both where both exist; an ID collision with different bytes is a
hard integrity failure.

### Registered kinds

| Kind | Owner and purpose | Earliest runtime tranche |
| --- | --- | --- |
| `task_profile` | Reviewed task semantics, schemas, impacts, checks, and trusted routing inputs | TD12-01 |
| `backend_identity` | Full immutable implementation/model/runtime/scoring identity | TD12-01 |
| `request` | One bounded state plus Choice, Score, or Noul questions | TD12-01 |
| `result` | Normalized answer or typed abstain/invalid/unavailable outcome | TD12-01 fixtures; TD12-03 live shadow |
| `batch_plan` | Isolation-preserving ordered grouping plan | TD12-07 |
| `residency_plan` | Advisory preload/keep/evict plan over exact identities | TD12-08 |
| `redaction_receipt` | Content-free proof of pre-transport sanitization | TD12-09; fixture in TD12-01 |
| `replay_receipt` | Offline validation/comparison with transport forbidden | TD12-01 |
| `evidence_receipt` | Corpus, report, metrics, and disposition binding | TD12-02 |
| `promotion_receipt` | Manual forward stage or automatic demotion with exact rollback | TD12-05 |
| `migration_receipt` | Source/target artifacts and deterministic migrator identity | TD12-01 |

TD12-01 implements strict models, validation, canonical digesting, fixture
adapters, and offline replay for every registered shape. Later tranches may
produce the corresponding runtime artifact, but may not invent a parallel
envelope.

## Task-general profiles and routing

A task profile binds domain semantics without coupling them to a model or
provider. Its identity includes `profile_id`, semantic version, profile digest,
domain, existing `task_kind`, owning Ansible collection, decision type, impact
ceiling, deterministic acceptance checks, and digest-bound input/output schema
references.

The first fixture set freezes these distinct profiles:

| Domain | Profile ID | Example question | Owning collection | Allowed impact |
| --- | --- | --- | --- | --- |
| Chemistry | `gludd.chemistry.reaction_triage` | Choice among reviewed reaction-risk classes over a QCSchema-shaped subject | `general_ludd.physics` | Read source; write recommendation artifact |
| Code | `gludd.code.failure_classification` | Choice over the existing Gludd failure taxonomy, with SARIF-shaped findings permitted as evidence | `general_ludd.agent` | Read source; write recommendation artifact |
| Hardware | `gludd.hardware.health_triage` | Score or Choice over a pinned Redfish observation snapshot | `general_ludd.os_expert` | Read source; write recommendation artifact |
| Configuration | `gludd.configuration.change_risk` | Noul or Choice over a validated configuration plan | `general_ludd.configuration` | Read source; write recommendation artifact |

These examples deliberately reuse external domain schemas instead of flattening
every subject into prose. MolSSI QCSchema defines common quantum-chemistry input
and output structures; OASIS SARIF defines interoperable static-analysis
results; DMTF Redfish defines a schema-based hardware-management model; and CUE
provides a mature configuration validation model. TD12-01 stores the external
schema URI and digest and treats the subject as opaque validated data. It does
not reimplement those standards.

Routing is a pure lookup with this precedence:

1. exact operator route over task-profile digest;
2. exact task-profile capability evidence;
3. trusted explicit language or domain hint declared by the profile;
4. reviewed generic identity; then
5. current Gludd path.

Untrusted state content may recommend a route only in shadow evaluation. It may
not choose an endpoint, identity, model alias, redaction policy, calibration,
authority, or secret. Unknown, mixed, stale, or conflicting inputs fall back.
Task policy never branches on `openjev`, `laya`, or another provider name.

### Domain output adapters

`DomainAdapterRegistry` is the provider-neutral bridge from reviewed domain
outputs into the same immutable `result`, evidence, and offline-replay contract.
It performs one exact-key lookup over profile ID/version/digest, domain, decision
type, adapter ID/version/code digest, capability, and native schema/version. The
registry snapshots that immutable route at registration, so a mutable protocol
implementation cannot rewrite result provenance later. There is no substring
match, compatible-version guess, provider fallback, or domain branch in the
registry. Concrete adapters own their native projections:

| Adapter | Admitted native projection | Exact capability | Result answer |
| --- | --- | --- | --- |
| chemistry/materials | bounded QCSchema output v2 | `choice_probabilities` | Choice |
| code/firmware | bounded SARIF 2.1.0 results | `sarif_choice_probabilities` | Choice |
| hardware | Redfish MetricReport 1.5.2 values | `redfish_score` | Score |
| configuration | configuration-plan decision 1.0.0 | `noul_probability` | Noul |

The public registry accepts no raw mapping. `RedactedDomainOutput` binds the
exact request digest and adapter code digest to route metadata plus a
`RedactionOutcome`; an output from one request, domain twin, or implementation
revision cannot be relabeled for another. Passed bytes must be strict canonical
JSON whose measured SHA-256 equals the redaction digest. Blocked or failed
redaction releases zero bytes and produces a typed abstention; non-applicable
source/redacted digests, categories, output identifiers, and failure details are
not copied into that abstention. Malformed, extra-field, non-canonical,
mismatched-digest, capability, version, profile, label, or request states fail
closed with content-free errors. Native content is not copied into the result.
Its digest-addressed reference, request-bound schema/capability route, sanitizer
status, and optional evidence references are recorded in provenance and a
non-authoritative extension.

Each projection is deliberately narrower than its upstream interchange format.
QCSchema admits the exact output name, integer version `2`, Boolean success, and
decision projection; SARIF admits exact 2.1.0 runs/results/properties; Redfish
admits the exact MetricReport 1.5.2 discriminator and finite scores within the
request's declared levels; configuration admits the exact 1.0.0 decision
projection. Cross-schema documents, numeric discriminator coercion, unknown or
duplicate questions, non-finite/out-of-range scores, invalid probabilities, and
nested secret-bearing extra fields are rejected. This keeps upstream extension
points from becoming an implicit Gludd authority surface.

Every successful adapter result is independently schema-validated and bound to
the exact request before return. The result artifact digest and provenance bind
the exact redacted native digest and adapter code digest. `offline_replay`
separately compares normalized decision semantics: two distinct native encodings
that yield the same answer may replay-match while retaining different artifact
digests, whereas changed answers mismatch without transport access. The adapters
import neither `self_improve` nor a provider SDK, perform no I/O, and remain
unwired for ZDD; adding or removing them cannot change the current authoritative
route.

The residual boundary is explicit: the in-process caller that constructs
`RedactedDomainOutput` and the sanitizer that issues `RedactionOutcome` remain
trusted to assign truthful binding metadata. The adapter prevents accidental or
cross-run reuse and makes later tampering digest-visible; it does not authenticate
a malicious in-process producer. Signature/attestation verification belongs at
artifact admission and promotion, not in these provider-neutral parsers.

### Hermetic adapter evaluation and evidence

Operators use the
[universal domain-adapter evaluation runbook](TYPED_DECISION_DOMAIN_ADAPTER_EVALUATION_RUNBOOK.md)
to pin the corpus and candidate, reproduce deterministic metrics, verify the
typed receipt, make a shadow-only admission decision, and execute ZDD rollback.
The runbook does not widen this contract or confer promotion authority.

`build_hermetic_domain_adapter_corpus` commits the TD12-02 offline corpus in
code, with no model, network, filesystem, wall clock, or provider capability.
Its 28 cases cover chemistry/materials QCSchema, code/firmware SARIF, hardware
Redfish, and configuration projections across valid, abstaining, malformed,
adversarial, and cross-domain/request-confusion states. Every case declares the
metric dimensions it exercises, and corpus construction fails before execution
if a required domain, category, dimension, replay reference, or unique case ID is
missing.

The corpus digest binds request identity, exact transport-byte count and digest,
claimed redaction digests, route metadata, expectations, replay pairs, frozen
logical-latency samples, and thresholds. It intentionally excludes candidate
adapter code digests so two implementations can be evaluated against the same
corpus identity. The report separately binds the sorted exact adapter routes,
including every code digest, as `adapter_bundle_digest`; changing either corpus
bytes or candidate code therefore changes the resulting evidence identity.

The frozen release thresholds are fail closed:

| Metric | Threshold | Deterministic meaning |
| --- | ---: | --- |
| routing accuracy | 1.0 | Every expected recommend, abstain, and exact rejection matches. |
| schema rejection rate | 1.0 | Every assigned malformed, adversarial, and cross-domain shape rejects exactly. |
| redaction safety rate | 1.0 | Digest mismatch/secret cases reject and failed redaction abstains without metadata leakage. |
| replay safety rate | 1.0 | Validation is transport-free; semantic match/mismatch pairs agree with the corpus. |
| latency budget pass rate | 1.0 | Every frozen logical-latency sample is within the approved ceiling. |
| latency p95 | <= 20 ms | Nearest-rank p95 over frozen samples; no nondeterministic wall-clock timing enters evidence. |

`evaluate_domain_adapter_corpus` returns content-free per-case outcomes and a
JCS report digest. `build_evaluation_evidence_receipt` emits the same immutable
`evidence_receipt` kind used by the core contract, binding corpus, adapter bundle,
report, thresholds, and evaluator code digest. A green hermetic report is only
`shadow_only`; any metric regression is `reject`, and this evaluator can never
emit `canary_eligible`. `require_evaluation_passed` is the explicit fail-closed
gate.

This evidence is ZDD-safe because evaluation remains offline and unwired. A
candidate may be added, evaluated, and reverted without selecting a production
route. Later promotion still requires a separate signed/approved promotion
receipt and the staged `shadow -> canary_1 -> canary_5 -> canary_25` process.
Rollback selects the prior route, appends a rollback ledger event, and preserves
both evaluation receipts; it never edits a prior result to manufacture success.

The long-lived practitioner findings below remain the design inputs: Pydantic
schema/runtime drift motivates independent exact rejection; Temporal replay
reports motivate transport-free recorded-result comparison; OpenTelemetry's
sanitization issue motivates pre-observation redaction and content-free reports.
Logical latency is a deterministic admission fixture, not a performance claim;
live tail latency, concurrency, provider authentication, and malicious
in-process producer resistance remain later shadow/canary evidence boundaries.

## Identity, evidence, and provenance

`backend_identity` binds every fact whose change can alter a result:

- Gludd contract and backend protocol versions;
- implementation identifier and server code/container digests;
- endpoint trust class, but no host, token, header, or credential;
- immutable model revision and artifact digest;
- tokenizer, prompt renderer, scoring contract, calibration, and runtime
  digests; and
- declared capabilities.

The fingerprint is the SHA-256 JCS digest of those fields in schema order after
semantic validation. A missing required digest makes the identity ineligible;
a moving alias may appear only as non-authoritative extension metadata.

Provenance follows the W3C PROV distinction between entities, activities, and
responsible agents without copying the full PROV serialization. `used_artifacts`
records derivation edges; `producer` names the generating software; `sources`
records URI, content digest, retrieval time, license disposition, and review
state. Attestations use in-toto/SLSA-style subject digests and predicate types.
An attestation is evidence about an artifact, not proof that its decision is
correct. Verification state is explicit.

Every promotion-relevant result must be traceable to:

- exact request, task profile, backend identity, policy, schema, and redaction
  profile digests;
- calibration corpus and evaluation report digests where calibration applies;
- source-family, license, sensitivity, and reviewer disposition for evaluation
  data; and
- immutable test, coverage, security, lifecycle, and rollback receipts.

Raw provider confidence retains its declared semantics URI. Gludd does not
translate entropy confidence, calibrated probability, conformal set size, or a
vendor score into one another. A policy consumes only fields proven for the
exact identity and task slice.

## Batching and residency contracts

Batching and residency are artifact shapes in v1 but have no TD12-01 runtime
effect.

A `batch_plan` may group requests only when these values match exactly:

- backend identity and task-profile digests;
- request and question schema versions/digests;
- calibration artifact and scoring semantics;
- project scope, classification, endpoint trust class, and redaction profile;
- token/byte budget and deadline bucket.

It retains original request order, caps group size at 64 and wait at 1,000 ms,
and never extends an original deadline. Partial, unsupported, cancelled, or
failed batches fan out to bounded individual attempts with the same idempotency
keys. Numeric drift beyond the declared tolerance, or any threshold crossing,
abstains. Cross-project or cross-classification grouping is invalid even if the
payloads are otherwise identical.

A `residency_plan` contains exact admitted identity digests, declared artifact
bytes, measured resident bytes, capacity, reserve, current lease counts, and
`preload`, `keep`, or `evict` recommendations. It cannot download an artifact,
increase a budget, evict a leased generation, invoke framework-specific garbage
collection, or kill a process. The universal model-worker lifecycle remains the
only executor and must prove absence after retirement.

The separation is intentional. Laya's current router demonstrates
schema-grouped heterogeneous batches, preloading, and bounded loaded-model
counts, while OpenJev exposes configurable encoder batching and routes that can
list a stopped backend. Those are useful workload hypotheses, not lifecycle or
readiness guarantees.

## Replay and idempotency

The idempotency key is the SHA-256 JCS digest of exactly:

```text
schema_version
task profile digest
backend identity digest or explicit shadow-null
state digest
question schema digest and ordered questions
policy digest
redaction profile digest
project scope and classification
```

Timestamps, attempt number, endpoint location, transport headers, trace IDs,
and mutable aliases are excluded. Retries reuse the same key. The durable ledger
implements these outcomes:

| Existing key | Incoming semantic digest | Outcome |
| --- | --- | --- |
| None | Any valid digest | Reserve atomically, then record one terminal result. |
| Terminal | Same | Return the existing artifact reference; do not call transport. |
| In progress | Same | Join or return typed `already_in_progress`; do not duplicate. |
| Any | Different | Fail `idempotency_conflict`; never overwrite. |
| Incomplete/expired | Same | Reconcile recorded effect before a bounded retry. |

`replay_receipt` supports `validate` and `offline_compare` only. It verifies
schema, canonical digest, identity, request/result binding, redaction, and
deterministic normalization without network, model, tool, secret, Git, or
workspace access. Provider inference is not claimed deterministic. A live
rerun is a new request and, if part of a whole-run re-execution, belongs to the
run-bundle subsystem with a new run ID and parent link.

Temporal practitioners have repeatedly encountered nondeterminism when replayed
code takes a different path from recorded history. TD12-01 therefore replays
recorded typed artifacts, never re-invokes an external model under the word
"replay," and compares a separately authorized rerun as a new artifact.

### Append-only local ledger adapter

`LocalAppendOnlyLedger` is the durable local implementation of the table above.
It remains provider-, model-, task-, and domain-neutral: its records contain only
the idempotency key, semantic digest, event type, immutable artifact references,
redacted evidence, and hash-chain identity. It cannot call a backend, select a
route, authorize an effect, or persist request state.

Each idempotency key owns one directory named by the validated SHA-256 hex. Each
event is a canonical `gludd.typed-decision.ledger-event` v1 envelope at the exact
monotonic path `NNNNNNNN.json`. The event digest covers the whole event payload,
including the prior event digest. Publication writes and `fsync`s a same-directory
temporary file, creates the final name with an atomic no-overwrite hard link, and
then `fsync`s the directory. A crash before the link leaves only an ignored
`.pending-*.tmp`; a crash after the link leaves a complete replayable event. The
adapter never truncates, replaces, renames over, or deletes a final event.

The filesystem capability is pinned, not re-resolved during an operation. The
constructor records the owned root's device and inode. Every operation reopens
that exact root with no-follow directory flags and rejects replacement. It then
opens the digest-named key directory relative to the verified root descriptor.
Scanning, bounded reads, temporary creation, hard-link publication, cleanup, and
directory `fsync` all stay relative to that same key-directory descriptor. A
concurrent rename or symlink swap therefore cannot redirect an append or cleanup
outside the owned directory; the operation either completes against the pinned
directory or fails closed on the next access.

Write failures have an explicit uncertainty contract. A file-`fsync` or link
failure before publication leaves no final event; an error reported after the
link or during directory `fsync` may leave one complete event, so callers retry
by replaying the same idempotency key rather than assuming failure. Failed
temporary cleanup can leave a `.pending-*.tmp`, but replay never reads pending
content and rejects more than 16 names. All OS error text and paths are replaced
by stable content-free reason codes.

Replay is bounded and fail-closed. Defaults admit at most 64 final events, 16
orphaned pending files, 16 KiB per event, and three reconciled retries per key.
Callers may lower those bounds. Unknown files, symlinks, sequence gaps,
non-canonical or duplicate-key JSON, oversized records, wrong versions, invalid
references, digest/predecessor mismatches, illegal state transitions, and a key
bound to another semantic digest all stop with a content-free typed reason. A
partial write is never interpreted as the next state. Restarted processes derive
`already_in_progress`, `reconcile_required`, or the exact terminal artifact only
from the verified chain.

Every append requires `RedactedLedgerEvidence`: one bounded reason code, one
digest of the already-redacted evidence, and one exact `redaction_receipt`
artifact reference. There is no arbitrary text, bytes, provider response, state,
endpoint, domain, or extension field in the ledger format. This enforces the
redact-before-persist lesson recorded in OpenTelemetry issue 3104 rather than
assuming downstream observation will sanitize a leak.

Rollback appends a `RollbackReceipt` binding the terminal artifact, restored
generation reference, semantic digest, evidence, sequence, and event digest. It
does not erase or replace the accepted terminal result. The local receipt is
operational evidence, not a twelfth artifact kind and not execution authority;
cross-system ZDD promotion/demotion evidence remains a canonical
`promotion_receipt`. Exact duplicate rollback requests return the existing
receipt, while any different replacement conflicts. This matches the
[long-lived Temporal replay reports](https://community.temporal.io/t/workflow-determinism/4027):
recorded history is replayed as evidence instead of silently re-running a
possibly different external activity.

## Redaction, isolation, and security

The canonical `general_ludd.security.redaction` boundary runs before hosted
transport, hashing of persisted content, logging, metrics, or provenance capture.
TD12-01 must reuse it rather than create typed-decision-only secret regexes.

Security rules are fail-closed:

- every artifact has an opaque project scope and classification;
- raw credentials, bearer headers, cookies, STS tokens, credential-bearing
  URLs, environment dumps, hidden reasoning, and raw OpenBao responses are
  forbidden in every field, including extensions;
- persisted request state is reviewed inline, redacted, or digest-only;
- hosted transport requires a passed redaction receipt; sanitizer failure sends
  zero bytes;
- extension fields cannot affect normalized answers, route, calibration,
  authorization, idempotency, batching, residency, or promotion;
- task text cannot install hooks or callbacks;
- observers receive immutable normalized results and cannot replace them; and
- malformed, oversized, recursive, cross-scope, or unknown-version artifacts
  return stable content-free reason codes.

OpenTelemetry issue 3104 records users' concern that instrumentation can begin
exporting unsanitized database statements unexpectedly. The durable implication
is explicit opt-in persistence and redact-before-observe, not a promise that
downstream telemetry will repair sensitive data. OWASP's LLM prompt-injection
guidance also supports retaining deterministic authorization outside model
output; sanitization alone does not make an endpoint trusted.

## Versioning, compatibility, and migration

`schema_version` is semantic, but compatibility is deliberately stricter than
ordinary SemVer:

- **major** changes may alter meaning or remove/rename fields; unknown majors
  fail closed;
- **minor** changes may add artifact kinds or optional semantics only through a
  new reviewed schema URI; readers must explicitly advertise the minor; and
- **patch** changes may clarify descriptions or add stricter fixtures but cannot
  change accepted canonical bytes for an existing valid artifact.

The v1.0 writer emits only `1.0.0`. Forward metadata belongs in URI-namespaced
`extensions`, must survive lossless relay, and is never authoritative. Readers
never silently coerce a new version to an older model.

Wire compatibility is adapter-specific and weaker than artifact compatibility.
Jev-, OpenJev-, Laya-, Verdict-, Kev-, and System-One-shaped fixtures must parse
through the same adapter protocol. Candidate-only fields are retained in
extensions or rejected. A normalized value cannot change silently. Each adapter
declares its supported protocol versions and compatibility fixture digests.

Migration never edits an artifact. A deterministic migrator reads one supported
source version, emits a new artifact and `migration_receipt`, preserves the
source, and states whether the conversion was lossless. Unknown fields,
ambiguous confidence semantics, missing identity, lossy task meaning, or an
unsupported major yields a typed rejection. No database migration is required
for TD12-01; the repository stores immutable versioned blobs and references.

Pydantic's long-running discriminated-union and parse-versus-serialization
reports demonstrate that generated schemas can diverge from runtime behavior.
The committed JSON Schema is therefore normative and is validated independently
of Python model-schema generation. Round-trip tests cover Python model -> JSON ->
JSON Schema -> Python model, while golden fixtures catch generator drift.

## Exact ownership map

TD12-01 implementation has one writer on one development-derived branch. These
paths are reserved; no provider-specific package or second schema may be created.

```text
docs/schemas/typed-decision-artifact-v1.schema.json
docs/examples/typed-decision-v1/
docs/features/TYPED_DECISION_IMPLEMENTATION_CONTRACT.md

src/general_ludd/typed_decision/__init__.py
src/general_ludd/typed_decision/artifacts.py
src/general_ludd/typed_decision/canonical_json.py
src/general_ludd/typed_decision/contracts.py
src/general_ludd/typed_decision/domain_adapter_evaluation.py
src/general_ludd/typed_decision/domain_adapters.py
src/general_ludd/typed_decision/identity.py
src/general_ludd/typed_decision/ledger.py
src/general_ludd/typed_decision/protocols.py
src/general_ludd/typed_decision/task_profiles.py
src/general_ludd/typed_decision/validation.py

collections/ansible_collections/general_ludd/typed_decision/README.md
collections/ansible_collections/general_ludd/typed_decision/galaxy.yml
collections/ansible_collections/general_ludd/typed_decision/plugins/module_utils/artifact_contract.py
collections/ansible_collections/general_ludd/typed_decision/plugins/modules/typed_decision_artifact.py
collections/ansible_collections/general_ludd/typed_decision/roles/typed_decision_contract/
collections/ansible_collections/general_ludd/typed_decision/tests/unit/

tests/unit/test_typed_decision_artifacts.py
tests/unit/test_typed_decision_canonical_json.py
tests/unit/test_typed_decision_contracts.py
tests/unit/test_typed_decision_domain_adapter_evaluation.py
tests/unit/test_typed_decision_domain_adapters.py
tests/unit/test_typed_decision_identity.py
tests/unit/test_typed_decision_ledger.py
tests/unit/test_typed_decision_ledger_adversarial.py
tests/unit/test_typed_decision_protocols.py
tests/unit/test_typed_decision_task_profiles.py
tests/unit/test_typed_decision_validation.py
tests/integration/test_typed_decision_contract_boundary.py
tests/e2e/test_typed_decision_artifact_contract.py
tests/unit/coverage_typed_decision_ledger.ini
tests/unit/coverage_typed_decision_domain_adapter_evaluation.ini
tests/unit/coverage_typed_decision_domain_adapters.ini
molecule/playbooks/typed_decision_contract/
config/coverage_typed_decision_contract.ini
```

The Python package owns semantics, strict validation, JCS bytes, digests, and
fixture adapters. The collection is a thin controller-only projection: it
validates or inspects an artifact, supports check mode, always reports
`changed=false`, performs no transport, and must not duplicate the schema. The
Molecule scenario proves delegation, installed-collection behavior, check mode,
idempotence, and zero managed-host mutation. The coverage config is added with
the first executable tranche, not by this documentation freeze.

Later ownership is disjoint:

- TD12-02 owns corpus and evaluation code;
- TD12-03 owns bounded shadow transport;
- TD12-07 owns scheduling, not request semantics;
- TD12-08 owns residency policy, not model lifecycle execution; and
- TD12-09 owns sanitizer/observer wiring, not the canonical redactor.

## ZDD promotion and rollback

TD12-01 ships unwired. Adding or reverting strict models, schemas, examples, and
the read-only collection module changes no daemon, endpoint, decision, database,
port, model cache, or managed host. Rollback removes the new reader and module;
already written artifacts remain immutable evidence readable by the retained
versioned schema.

Later rollout is reader-first and always preserves the current path:

1. deploy v1 readers and validation telemetry with writers absent;
2. dual-write redacted shadow artifacts at 1%, then 5%, then 25%; keep the old
   evidence path hot;
3. enable shadow transport for one exact task profile and identity;
4. manually promote `shadow -> canary_1 -> canary_5 -> canary_25`; and
5. automatically demote on integrity, identity, security, quality, resource, or
   forbidden-impact triggers. Forward promotion is never automatic.

At each step, new work selects one exact generation atomically. In-flight work
retains its generation and deadline. Rollback first selects the prior healthy
artifact/profile/identity or current Gludd path, stops new candidate work, drains
accepted work, retires owned resources, and proves absence. Receipts are never
rewritten or deleted to make rollback appear clean. The local ledger records the
rollback as a new hash-chained event; the prior terminal event remains intact and
continues to replay to the same idempotency result.

## Acceptance matrix

TD12-01 code is not complete until every row passes on one exact combined SHA.
Tests are written failing first. Fixture/schema-only validation in this document
does not satisfy the executable rows.

| ID | Layer | Exact acceptance |
| --- | --- | --- |
| TD01-U01 | JSON Schema | Official draft-2020-12 metaschema accepts the committed schema; every example validates; missing/extra fields, duplicate keys, unknown kind/version, and wrong discriminators fail. |
| TD01-U02 | Canonical bytes | RFC 8785 vectors plus verified `-0`, Unicode, number, ordering, NaN/Infinity, and lone-surrogate cases; Python round trips retain the same digest. |
| TD01-U03 | Artifact kinds | All eleven kinds round trip; every strict bound and cross-field invariant has a positive, boundary, and one-past-boundary case. |
| TD01-U04 | Identity | Mutating each identity field changes the fingerprint; endpoint secrets and mutable aliases never enter it; missing digests fail. |
| TD01-U05 | Task profiles | Chemistry, code, hardware, and configuration fixtures route only by exact reviewed inputs; unknown/mixed/conflicting hints fall back in 100% of cases. |
| TD01-U06 | Adapters | Jev/OpenJev/Laya/Verdict/Kev/System-One fixtures normalize only through the generic protocol; semantic differences are retained or rejected, never coerced. |
| TD01-U07 | Answers | Choice/Score/Noul request binding, IDs, labels, finite numbers, probability sums/tolerance, score bounds, abstention, and stable reason codes are exhaustive. |
| TD01-U08 | Idempotency | Atomic reservation across independent adapters, duplicate terminal return, in-progress join/refusal, conflicting payload rejection, crash reconciliation, bounded retry, and event/read ceilings produce at most one accepted result. |
| TD01-U09 | Replay/migration | Offline replay makes zero external calls; partial writes, corruption, tampering, gaps, reordering, and illegal transitions are detected; every supported migration creates a new artifact and receipt; lossy/unknown versions fail. |
| TD01-U10 | Security | Seeded secrets and hidden reasoning never reach transport, disk, logs, metrics, errors, or extensions; cross-project/classification artifacts and failed sanitization send zero bytes. |
| TD01-U11 | Batch/residency shapes | Group keys include every isolation field; permutations preserve binding/order; cross-scope grouping and leased eviction fail; plans have no side effect. |
| TD02-U01 | Hermetic adapter evaluation | All 28 universal-domain cases run without live capabilities; five safety rates equal 1.0, deterministic p95 is within 20 ms, threshold regressions reject, and evidence binds corpus, adapter bundle, report, and evaluator digests while remaining shadow-only. |
| TD01-C01 | Collection unit | Module args, check mode, `changed=false`, strict result shape, missing file, symlink/path escape, oversized artifact, and controller-only import boundary pass. |
| TD01-M01 | Molecule | `prepare -> converge -> idempotence -> verify -> cleanup -> destroy` validates good/bad fixtures, runs twice with zero changes, performs no network/provider call, and leaves no process, port, lease, or temp artifact. |
| TD01-I01 | Integration | Python package and installed collection produce byte-identical normalized artifact/digest/reason codes for every domain and candidate fixture. |
| TD01-E01 | E2E | Create profile -> request -> fixture result -> evidence -> offline replay -> migration -> shadow promotion receipt -> rollback receipt; current authoritative classification is unchanged throughout. |
| TD01-Q01 | Coverage/gates | Typed-decision executable files are at least 85% aggregate and no file is below 75%; branch coverage enabled; lint, strict types, warnings-as-errors, secret/schema checks, collection, Molecule, E2E, and full gate pass. |

The focused command set for the implementation tranche is frozen as:

```console
make test-files TESTFILES='tests/unit/test_typed_decision_artifacts.py tests/unit/test_typed_decision_canonical_json.py tests/unit/test_typed_decision_contracts.py tests/unit/test_typed_decision_identity.py tests/unit/test_typed_decision_protocols.py tests/unit/test_typed_decision_task_profiles.py tests/unit/test_typed_decision_validation.py tests/integration/test_typed_decision_contract_boundary.py tests/e2e/test_typed_decision_artifact_contract.py' GLUDD_XDIST_WORKERS=1 PYTEST_ARGS=
make test-specific TESTFILE=tests/unit/test_typed_decision_domain_adapters.py GLUDD_XDIST_WORKERS=1 PYTEST_ARGS='-q -n 0 -W error'
make coverage-files COVERAGE_TESTFILES='tests/unit/test_typed_decision_domain_adapters.py' COVERAGE_CONFIG=tests/unit/coverage_typed_decision_domain_adapters.ini COVERAGE_REPORT=.gate-logs/typed-decision-domain-adapters-coverage.json COVERAGE_AGGREGATE_MIN=85 COVERAGE_PER_FILE_MIN=75 OBSERVED_ROOT=.gate-logs/observed OBSERVED_HEARTBEAT_SECS=30 OBSERVED_QUIET_SECS=900 OBSERVED_MAX_SECS=3600 OBSERVED_RETAIN_RUNS=20
make test-specific TESTFILE=tests/unit/test_typed_decision_domain_adapter_evaluation.py GLUDD_XDIST_WORKERS=1 PYTEST_ARGS='-q -n 0 -W error'
make coverage-files COVERAGE_TESTFILES='tests/unit/test_typed_decision_domain_adapter_evaluation.py' COVERAGE_CONFIG=tests/unit/coverage_typed_decision_domain_adapter_evaluation.ini COVERAGE_REPORT=.gate-logs/typed-decision-domain-adapter-evaluation-coverage.json COVERAGE_AGGREGATE_MIN=85 COVERAGE_PER_FILE_MIN=75 OBSERVED_ROOT=.gate-logs/observed OBSERVED_HEARTBEAT_SECS=30 OBSERVED_QUIET_SECS=900 OBSERVED_MAX_SECS=3600 OBSERVED_RETAIN_RUNS=20
make test-files TESTFILES='tests/unit/test_typed_decision_ledger.py tests/unit/test_typed_decision_ledger_adversarial.py' GLUDD_XDIST_WORKERS=1 PYTEST_ARGS='-q -n 0 -W error'
make coverage-files COVERAGE_TESTFILES='tests/unit/test_typed_decision_ledger.py tests/unit/test_typed_decision_ledger_adversarial.py' COVERAGE_CONFIG=tests/unit/coverage_typed_decision_ledger.ini COVERAGE_REPORT=.gate-logs/typed-decision-ledger-coverage.json COVERAGE_AGGREGATE_MIN=85 COVERAGE_PER_FILE_MIN=75 OBSERVED_ROOT=.gate-logs/observed OBSERVED_HEARTBEAT_SECS=30 OBSERVED_QUIET_SECS=900 OBSERVED_MAX_SECS=3600 OBSERVED_RETAIN_RUNS=20
make molecule-test SCENARIO=typed_decision_contract LIMA_INSTANCE=gludd-molecule LIMA_DOCKER_CONFIG=/tmp/gludd-molecule-docker-config LINUX_BINARY_IMAGE=python:3.12-slim LINUX_BINARY_OUTPUT=/tmp/gludd-molecule-binary
make coverage-files COVERAGE_TESTFILES='tests/unit/test_typed_decision_artifacts.py tests/unit/test_typed_decision_canonical_json.py tests/unit/test_typed_decision_contracts.py tests/unit/test_typed_decision_identity.py tests/unit/test_typed_decision_protocols.py tests/unit/test_typed_decision_task_profiles.py tests/unit/test_typed_decision_validation.py tests/integration/test_typed_decision_contract_boundary.py tests/e2e/test_typed_decision_artifact_contract.py' COVERAGE_CONFIG=config/coverage_typed_decision_contract.ini COVERAGE_REPORT=.gate-logs/typed-decision-contract-coverage.json COVERAGE_AGGREGATE_MIN=85 COVERAGE_PER_FILE_MIN=75 OBSERVED_ROOT=.gate-logs/observed OBSERVED_HEARTBEAT_SECS=30 OBSERVED_QUIET_SECS=900 OBSERVED_MAX_SECS=3600 OBSERVED_RETAIN_RUNS=20
make collect-check
make gate
```

The implementation owner must register any new public Make target and run its
target-contract example; this freeze adds none.

## Primary sources and practitioner findings

Current primary sources reviewed for this freeze:

- [JSON Schema draft 2020-12](https://json-schema.org/draft/2020-12) is the
  normative structural dialect.
- [RFC 8785](https://www.rfc-editor.org/rfc/rfc8785.html) defines deterministic
  JSON canonicalization; its
  [verified errata](https://www.rfc-editor.org/errata/rfc8785) motivates explicit
  negative-zero rejection.
- [W3C PROV](https://www.w3.org/TR/prov-overview/) supplies the
  entity/activity/agent provenance model.
- [in-toto Attestation Framework](https://github.com/in-toto/attestation) and
  [SLSA 1.2 provenance](https://slsa.dev/spec/v1.2/provenance) support binding
  claims to immutable subject digests while keeping verification separate from
  the claim.
- [MolSSI QCSchema](https://molssi.org/software/qcschema-2/),
  [OASIS SARIF 2.1 plus Errata 01](https://www.oasis-open.org/standard/sarifv2-1-os/),
  [DMTF Redfish](https://www.dmtf.org/standards/redfish), and
  [CUE documentation](https://cuelang.org/docs/) anchor the task-general domain
  fixtures.
- [Molecule workflow](https://github.com/ansible/molecule/blob/main/docs/workflow.md)
  defines converge/idempotence/verify lifecycle expectations.
- The current [Laya README](https://github.com/NandhaKishorM/laya#readme),
  [Laya router](https://github.com/NandhaKishorM/laya/blob/main/laya/router.py),
  and [OpenJev README](https://github.com/razorback16/openjev/blob/main/README.md)
  are candidate observations, never Gludd authority or maturity evidence.

Long-lived practitioner reports outside the young OpenJev/Laya ecosystem expose
the relevant failure classes:

| Report | Persisting lesson encoded here |
| --- | --- |
| [Pydantic discriminated unions #619 (2019)](https://github.com/pydantic/pydantic/pull/619), [list unions #3608 (2022)](https://github.com/pydantic/pydantic/issues/3608), and [nested OpenAPI unions #7491 (2023)](https://github.com/pydantic/pydantic/issues/7491) | Generated and runtime schemas can diverge for years. Commit an independent normative schema and golden round trips. |
| [Temporal nondeterministic replay discussion (2021)](https://community.temporal.io/t/why-cant-workflows-contain-non-deterministic-code-and-how-does-using-activity-solve-the-problem/3231) and [workflow determinism (2022)](https://community.temporal.io/t/workflow-determinism/4027) | Replay recorded results deterministically; treat an external inference rerun as a new activity/artifact. |
| [Molecule idempotence diagnostics #835 (2017)](https://github.com/ansible/molecule/issues/835) | A second converge alone is insufficient evidence when the first failed or hid context. Preserve phase/result reason codes and run the full sequence. |
| [Transformers partial save with `device_map=auto` #20072 (2022)](https://github.com/huggingface/transformers/issues/20072) and [load-time OOM #21376 (2023)](https://github.com/huggingface/transformers/issues/21376) | A declared placement is not a complete artifact or safe residency proof. Measure bytes, preserve exact identity, lease before use, and verify teardown. |
| [OpenTelemetry sanitization default #3104 (2023)](https://github.com/open-telemetry/opentelemetry-specification/issues/3104) | Data collection can expose content unexpectedly. Redact before persistence/observation and require explicit persistence state. |

The OpenJev/Laya-specific reports remain recent. They are retained in the
provider-admission decision and backlog as regression hypotheses, with the
dated caveat that they do not establish long-lived production maturity.

## Exit condition

This freeze is complete when the schema and examples validate, the three
v0.1.2 documents point to this contract without conflicting ownership or scope,
Markdown/URL checks pass, and the docs commit is clean. It is not TD12-01 runtime
implementation evidence. The next tranche starts with failing tests for the
exact paths and acceptance IDs above; no live client, model download, daemon,
route authority, or production wiring may be added in that tranche.
