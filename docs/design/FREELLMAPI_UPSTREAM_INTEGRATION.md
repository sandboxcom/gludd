# FreeLLMAPI Upstream Integration Decision

**Status:** Accepted architecture; compatibility proof is required before enablement
**Decision date:** 2026-09-15
**Upstream:** [`tashfeenahmed/freellmapi`][upstream]
**Evaluated release:** `v0.9.9`, release commit abbreviation `780a7d8`
**Scope:** upstream ownership, in-process execution, signed free-tier knowledge,
pure-function evaluation, security, updates, rollback, tests, and
self-improvement evidence

## Decision summary

Gludd already implements the majority of FreeLLMAPI's advertised runtime
features. It remains the sole owner of discovery, routing, calibration, health,
failover, envelopes, privacy, cost, scheduling, provider transport, compute, and
lifecycle. This is not a FreeLLMAPI reimplementation project.

The first and only pre-approved integration is FreeLLMAPI's maintained, signed
free-tier catalog: model availability, advertised quotas, and provider quirks.
Those records are advisory discovery inputs, never executable candidates or
routing decisions. They are fetched and authenticated by Gludd's existing Python
runtime and do not require a JavaScript engine.

In this design, "free-model routing" means that the authenticated feed expands
Gludd's native candidate set with currently free endpoints and quota metadata;
Gludd's existing calibrated router then selects among free, local, Azure, and
other admitted candidates under the task's privacy, quality, latency, and cost
constraints. It does not mean embedding FreeLLMAPI's second router.

The concrete Gludd gap is small but real: its provider presets currently mark
only a few hard-coded model-list endpoints and use them for smoke discovery; they
do not provide a signed, maintained, cross-provider free-tier inventory with
quota/reset and compatibility metadata. Filling that gap is the integration.
Everything after catalog admission reuses Gludd's existing candidate probing,
calibration, trial, routing, execution, trace, and lifecycle paths.

The executable scoring kernel follows the same ownership rule. Its canonical
Python boundary and attributed JavaScript artifact live under
`general_ludd.models`, where chemistry, firmware, self-improvement, and future
workloads can reuse them. `general_ludd.self_improve.freellmapi_scoring_kernel`
is an identity-preserving compatibility import only; the model layer never
imports self-improvement implementation.

No FreeLLMAPI TypeScript function is pre-approved. A named, pure upstream export
may be proposed only after a frozen shadow/ablation experiment identifies a
specific Gludd capability gap and demonstrates a positive quality benefit after
latency, memory, cost, and failure penalties. Only then may the minimal import
graph for that export be bundled and called through the in-process bridge. If no
export clears that gate, Gludd ships no JavaScript bridge or JavaScript runtime.

## Non-negotiable boundary

FreeLLMAPI code must execute **inside the existing Gludd Python worker**. Gludd
will not start a FreeLLMAPI server, subprocess, sidecar, container, or separately
supervised service. It will not call a loopback FreeLLMAPI HTTP gateway.

The selected integration is:

1. fetch an exact upstream source release in an isolated update job;
2. admit only signed, schema-locked free-tier catalog/quota/quirk data;
3. when a named export clears its delta gate, compile its deliberately small,
   pure TypeScript import graph into one pinned JavaScript artifact;
4. load that artifact into an in-process V8 isolate through PyMiniRacer; and
5. invoke only manifest-enumerated, pure, bounded functions with no host callback
   or I/O capability.

No upstream source file is copied into or hand-edited in the Gludd source tree.
The generated artifact is an attributable MIT-licensed redistribution, never a
fork. Its source commit, source digest, build recipe, dependency lock, license
notices, SBOM, provenance, and artifact digest travel together.

This decision is intentionally fail-closed. If a release cannot produce the
approved pure import graph, Gludd keeps a still-compatible previous artifact or
disables that optional capability. The independently locked data feed may remain
available. Gludd does not fall back to a process boundary, image, mutable tag, or
locally rewritten copy.

## Context and upstream facts

FreeLLMAPI is a fast-moving, self-hosted model router. Gludd already has discovery,
routing, calibration, health, failover, proposal envelopes, project privacy, cost
accounting, scheduling, and infrastructure lifecycle. This integration does not
replace or wrap any of those capabilities. Its narrow purpose is to ingest
FreeLLMAPI's free-tier catalog, advertised quota, and provider-quirk knowledge and,
only where an ablation proves incremental value, execute selected pure upstream
scoring, normalization, or fusion functions.

The following facts were checked against upstream on 2026-09-15:

- The source is under the [MIT license][license]. Individual provider terms still
  apply; the MIT license does not grant Google, NVIDIA, GitHub Models, or another
  provider's service rights.
- The root is a private npm workspace monorepo. The server workspace is also
  private. The published [`freellmapi` npm package][cli-package] is a setup CLI,
  not a reusable server or core library. Upstream requires Node
  `>=20.18.0 <25.0.0`.
- The server targets ES2022 ESM. Its [package manifest][server-package] depends on
  Express, Undici, and Sharp, with optional Better SQLite3. The
  [server entry point][server-entry] owns a Node HTTP listener, process signals,
  timers, SQLite lifecycle, background schedulers, and Node-specific error
  handling.
- The public service API includes OpenAI-compatible chat, Responses,
  completions, embeddings, and model discovery; Anthropic Messages; native
  Gemini; optional Ollama emulation; media routes; OpenAPI; and MCP. The
  [API reference][api-reference] remains useful as behavioral documentation, but
  Gludd does not deploy that HTTP service.
- Declarative startup configuration can define keys, custom endpoints, model
  metadata, fallback chains, and routing strategy through
  `FREEAPI_CONFIG_PATH` or `FREEAPI_CONFIG_JSON`. This is an upstream deployment
  feature, not an integration surface: Gludd does not import its configuration,
  keys, fallback chains, or routing strategy.
- Upstream's [catalog synchronization implementation][catalog-sync] polls twice
  daily, authenticates the exact response bytes with a pinned Ed25519 public key,
  rejects missing or invalid signatures, enforces a bundled minimum catalog
  version, and transactionally applies accepted data. Free installations receive
  a monthly snapshot; the paid feed changes more quickly.
- Upstream's root test command covers bootstrap, hooks, server, CLI, and client.
  The [CI workflow][upstream-ci] exercises Node 20 and 22, migration round trips,
  tests, and workspace builds. These tests must remain part of every update even
  though Gludd embeds only a subset.
- Upstream also publishes multi-architecture OCI images. Their
  [Docker workflow][docker-workflow] creates release, commit, and branch tags.

### Upstream recheck on 2026-09-21

The latest signed release is now `v0.11.1` at full commit
`4191d8e7abef39fcd93fab009123467036f39750`; the admitted executable subset
remains pinned to `v0.9.9` until the update workflow can replay upstream tests,
purity, provenance, ABI, and frozen delta evidence. A newer tag is discovery
evidence, not automatic authorization to replace a working artifact.

Two newer operator reports reinforce that boundary. [Issue #1270][issue-1270]
showed the desktop updater advertising untagged commits for which no installer
existed; `v0.11.1` changed it to compare published releases. [Issue #1262][issue-1262]
showed a healthy but slow endpoint being cancelled by a fixed outer retry
budget; `v0.11.1` changed that budget to measured endpoint history.
Gludd therefore resolves full signed release identities, rejects mutable `main`,
and retains its own measured, task-bounded deadline rather than importing
FreeLLMAPI's process-wide retry policy.
  That is a supported upstream deployment form, but it is explicitly not Gludd's
  integration form.

The repository release page reports a GitHub-verified commit signature for the
evaluated release. That signature authenticates a commit; it is not a signature
over a Gludd-compatible compiled bridge. Gludd therefore produces and attests its
own deterministic artifact from the verified source.

## Concrete delta matrix

The default action for every upstream feature is **do not integrate**. A row moves
from data-only to executable only when a named pure export beats Gludd's existing
implementation on a preregistered metric without weakening correctness, privacy,
latency, or cost.

| Capability | Existing Gludd authority | Incremental FreeLLMAPI input | Integration action | Proof that ownership is not duplicated |
|---|---|---|---|---|
| Model discovery | Hardware, endpoint, provider, and live-capability discovery | Signed free-tier model/catalog rows | Admit rows only as unverified discovery seeds; Gludd probes and registers the real endpoint/model | Disable the upstream feed and prove all existing Gludd discovery still operates |
| Routing | Task-to-model selection and outer route | Optional pure upstream score | Run as a versioned advisory feature or shadow challenger; it cannot select a route | Router output is always a Gludd decision with its own calibrated explanation |
| Calibration | Benchmarks, priors, confidence, drift, and promotion | Catalog ranks and optional scorer output | Record as candidate priors, then calibrate against held-out Gludd outcomes | No upstream value can update quality without normal Gludd evaluation |
| Health | Active probes, availability, latency, error taxonomy, and circuit state | Advertised quota windows and provider-specific failure quirks | Annotate probe interpretation only; measured health wins | No upstream health checker, timer, cooldown table, or health state is embedded |
| Failover | Retry budget, fallback graph, deadline, and exactly-once lease | Provider retry/timeout quirks and suggested compatibility constraints | Convert data into bounded policy hints; Gludd validates and builds the graph | No upstream fallback planner or executor enters the artifact |
| Envelopes | Canonical request, response, proposal, tool, usage, and error envelopes | A pure normalizer may cover a provider edge case | Execute only as an inner transform, then validate against the Gludd envelope | The upstream function never defines or emits the authoritative envelope |
| Privacy | Project exclusions, egress admission, redaction, and retention | None | Import nothing | Privacy checks run before any bridge input and cannot be relaxed by catalog data |
| Cost and quota | Price accounting, budgets, reservations, observed usage, and idle-cost policy | Advertised free-tier limits, reset cadence, and quota quirks | Store as timestamped uncertain metadata; observed accounting and operator policy win | No upstream budget, ledger, or spend decision is embedded |
| Scheduling and work claims | Atomic todo leases, concurrency, deadlines, recovery, and ranking | None | Import nothing | The bridge never claims, queues, schedules, retries, or completes work |
| Provider transport | Auth, egress, HTTP/SSE, redirects, cancellation, and telemetry | Declarative provider protocol quirks | Compile data into Gludd transport fixtures/policy inputs | No upstream HTTP adapter, credential handler, socket, or callback performs I/O |
| Fusion | Model invocation, component identity, validation, and promotion | Optional pure fusion export over already collected bounded outputs | Evaluate as a deterministic challenger transform after Gludd runs the models | Gludd chooses participants, records each result, validates fusion, and owns promotion |
| Local/Azure serving | Hardware sizing, provisioning, runners, teardown, and metrics | None | Import nothing | FreeLLMAPI cannot create, configure, route, or tear down compute |
| Lifecycle and ZDD | Worker supervision, generation switch, rollback, and cleanup | Immutable bridge bytes only | Apply Gludd's existing artifact-generation lifecycle | No Express, SQLite, migration, dashboard, server, or upstream supervisor is loaded |

Every selected datum/export has a delta record containing the Gludd baseline, the
upstream candidate, fixture corpus, quality metric, latency and memory overhead,
decision threshold, observation window, owner, and removal condition. A
non-positive or statistically inconclusive delta means rejection. Rejection is a
successful update outcome and leaves no dead compatibility code.

## Feasibility boundary: JavaScript is not Node.js

A V8, SpiderMonkey, Duktape, or QuickJS engine does not by itself implement
Node.js. FreeLLMAPI's complete server cannot be evaluated unchanged in any
reviewed Python JavaScript bridge because it needs Node built-ins, libuv-backed
networking, process globals, and native addons such as Sharp and Better SQLite3.
Bundling does not turn those dependencies into portable JavaScript.

Gludd consequently considers only upstream logic that is pure and within the
delta matrix:

- free-tier catalog, advertised quota, and provider-quirk normalization;
- a scoring export whose shadow result measurably improves Gludd's router;
- a provider-result normalizer that closes a demonstrated conformance gap; and
- a fusion export that operates solely on outputs already selected and collected
  by Gludd.

Python retains discovery, routing, calibration, health, failover, envelopes,
privacy, cost, networking, credentials, persistence, scheduling, streaming,
timeouts, work leases, and lifecycle. The build rejects an import graph containing
`node:*`, filesystem or process access, sockets, child processes, dynamic
`require`, native `.node` addons, Sharp, Better SQLite3, Express, Undici, or
runtime package resolution.

If useful upstream behavior is entangled with the server, the preferred fix is an
upstream contribution that extracts a pure exported function. Until that lands
and passes the gate, the behavior is unavailable. Gludd does not reproduce the
implementation from memory.

## Runtime comparison and decision

| Bridge | Modern TS/ESM and Node compatibility | Async and cancellation | Limits and isolation | Packaging and license | Decision |
|---|---|---|---|---|---|
| [PyMiniRacer `mini-racer`][miniracer] | Current V8 and modern ECMAScript; no Node APIs or runtime module loader, so TypeScript and ESM must be bundled to one IIFE | Promises integrate with asyncio; cancelable evaluation and async Python callbacks are documented | Separate V8 isolates, synchronous timeout, cancelable async calls, hard/soft heap limits; callbacks deliberately pierce the capability boundary | ISC; 0.14.1 is classified stable, uses V8 14.4, and publishes attested wheels for Python 3.10-3.14 on macOS and Linux, x86-64 and arm64 | **Selected**, subject to the compatibility and crash-containment gates below |
| [jsrun][jsrun] | Current V8, native ESM, custom loaders; explicitly no Node or Web APIs | Async evaluation and host operations; timeout and same-thread termination | No I/O by default, heap limit, per-runtime isolate/thread | MIT; 0.1.0 is explicitly under development, and the observed wheel set lacks macOS x86-64 | Re-evaluate when its API and platform matrix mature |
| [PythonMonkey][pythonmonkey] | Modern SpiderMonkey; Node-like npm/CommonJS resolver, but not the Node runtime used by upstream ES2022 ESM | Python awaitables and JS promises share an event loop | No documented per-runtime hard heap limit, deadline, or termination API; its CommonJS environment exposes Python eval, exec, environment, and exit primitives | MIT; 1.3.2 has broad macOS/Linux wheel coverage | Rejected for this least-capability boundary |
| [DukPy][dukpy] | QuickJS-based 0.6 supports ESM/CommonJS classification and a TypeScript transpiler, but not Node built-ins | No documented asyncio cancellation contract | No documented Python API for hard heap/deadline enforcement | MIT; PyPI classifies it alpha | Rejected |
| [PetterS quickjs][quickjs-wrapper] | ES modules and QuickJS jobs, no Node runtime | Manual pending-job pumping rather than an asyncio bridge | Memory/time/stack limits | MIT; repository was archived in 2026 and published binaries target Python only through 3.10 | Rejected |
| Embedded libnode | Only option considered that supplies real Node APIs and native-addon semantics | Requires coordinating libuv, V8, Python, and Gunicorn lifecycle | Large same-process trusted computing base; Node's own [embedder API][node-embedder] may break on each semver-major | Would require a custom CPython extension and per-platform libnode build | Rejected unless a future ADR proves the pure-core boundary impossible |

PyMiniRacer is the best current fit because it combines a maintained V8, explicit
async cancellation, hard memory limits, broad wheels, and a small default host
surface. Its missing ESM loader is useful pressure: the accepted artifact is one
fully resolved IIFE, so no import can unexpectedly reach the host at runtime.
Although PyMiniRacer supports Python callbacks, this integration never registers
one; the callback API remains outside the allowed bridge surface.

Version `mini-racer==0.14.1` is the first candidate, not an accepted dependency
lock. The implementation must record hashes and PyPI provenance for every wheel
in Gludd's actual Python/platform matrix. A missing or unattested wheel fails that
platform instead of building native code opportunistically on an operator host.

## Embedded artifact contract

### Source and build identity

The lock records at least:

~~~text
upstream_repository
upstream_release
upstream_full_commit
upstream_source_archive_sha256
upstream_package_lock_sha256
selected_source_paths_and_export_names
eligible_upstream_data_schema_version
selected_capability_ids
delta_evidence_sha256_by_capability
bridge_entrypoint_sha256
typescript_and_bundler_versions
build_recipe_sha256
compiled_artifact_sha256
source_map_sha256
license_bundle_sha256
sbom_sha256
provenance_attestation_identity
miniracer_version
miniracer_wheel_sha256_by_platform
miniracer_v8_version
previous_accepted_artifact_sha256
abi_version
~~~

The short `780a7d8` reference is documentation only. A production lock requires
the full commit and exact archive digest. The generated artifact and source map
are build outputs, not reviewed source, and are never edited after generation.

The release bundle contains:

- one deterministic IIFE JavaScript file;
- a machine-readable export/import manifest and ABI version;
- the matching source map for private debugging;
- the complete upstream and transitive license notices;
- an SPDX or CycloneDX SBOM;
- build and test evidence; and
- a keyless CI provenance attestation bound to the artifact digest.

Gludd may package those immutable bytes inside its tested release artifact for
offline bootstrap. The repository lock identifies them; it never downloads code
at task execution time.

### Stable bridge ABI

A small Gludd-owned TypeScript entrypoint imports named upstream functions and
exports one frozen global object, `globalThis.__gluddFreellmapiCore`. The ABI
accepts and returns bounded JSON values or byte arrays only. There is no mandatory
FreeLLMAPI operation. A build may expose only an explicit subset of these
capability classes:

- `score_candidate_features`: return an advisory feature vector or score, never a
  route or provider selection;
- `normalize_provider_observation`: return an inner candidate representation for
  a demonstrated provider edge case, never Gludd's canonical envelope; and
- `fuse_candidate_outputs`: deterministically combine bounded outputs that Gludd
  has already selected, invoked, and recorded.

Each operation must call a named, pure upstream export and have a positive delta
record. The ABI manifest binds its public operation, exact upstream export,
input/output schema, limits, delta-record digest, and removal threshold. An
operation is absent when no export clears the gate; absence does not degrade
Gludd's existing behavior. If a shim would need to reproduce upstream logic,
perform I/O, choose a model, or own a policy decision, the build fails and an
upstream extraction is required.

The ABI has no generic eval operation. User input, prompts, model output, and
project code are data, never JavaScript source or module names.

### Zero-host-capability execution

The V8 isolate begins without filesystem, network, process, environment, console,
module-loader, secret, or Python-callback access. Gludd registers **zero** host
functions. Fetching, signature verification, provider I/O, event emission, work
leases, cancellation, and policy all stay in existing Python components. If an
approved pure export needs a time or seed value, Python supplies a bounded,
recorded value in its input; JavaScript cannot ask the host for another value.

Each call carries an operation enum, schema version, artifact identity, work-lease
identity, deadline, and bounded data. Python validates the returned value before
it can become advisory evidence. Exceptions cross the bridge as finite typed
errors, not tracebacks or environment dumps. Adding any callback or ambient
capability requires a new security review and ADR.

## Catalog, quota, quirks, and Gludd-owned discovery

Python fetches a catalog with a strict byte limit and no redirects. Before JSON
parsing, it verifies the upstream `x-catalog-signature` over the exact received
bytes with the pinned Ed25519 key. It rejects a missing/invalid signature, an
unknown signer, a version below the artifact's upstream minimum, rollback from a
previously accepted version, and a catalog whose identity conflicts with the
locked upstream data schema or any selected bridge artifact.

After authentication, Python schema-validates and stores the immutable upstream
snapshot as a knowledge feed. It extracts only free-tier model metadata,
advertised quota/reset information, and provider-quirk fields admitted by a
versioned allowlist. A selected pure upstream normalizer may additionally produce
an advisory representation when its delta record is positive. Raw or normalized
rows never become runnable candidates directly. Gludd maps relevant rows onto its
existing provider and endpoint identities, probes actual capability, and then
applies provider terms, project privacy, modality, context, cost, hardware, and
empirical-quality policy. Catalog rank is a prior, never proof.

Production forbids catalog URL and public-key overrides. A self-hosted catalog
requires a separate signer/key-rotation decision and a distinct trust namespace,
so it cannot masquerade as upstream.

The provenance attached to a native Gludd candidate or decision contains:

~~~text
upstream_knowledge_source=freellmapi
bridge_artifact_sha256
bridge_upstream_commit
bridge_abi_version
effective_signed_catalog_sha256
normalized_upstream_record_sha256
selected_capability_id_or_none
delta_record_sha256_or_none
native_gludd_candidate_id
~~~

FreeLLMAPI is never a backend, provider, route, candidate type, or evidence owner.
Its `auto` mode, named profiles, fallback chains, health system, and scheduler are
not registered in Gludd. A selected fusion export is an inner deterministic
challenger over already collected inputs; Gludd preserves every component model's
native identity and attributes the fused result separately. Bridge output cannot
update quality, cost, quota, or health until Gludd's normal validation and
calibration path accepts the evidence.

Provider availability, quota pressure, network health, model quality, and
infrastructure failure remain separate evidence axes. A bounded non-promoting
canary is required before a newly discovered candidate receives real
self-improvement work.

## Concurrency, cancellation, and lifecycle

Each Gunicorn worker owns a bounded pool of PyMiniRacer contexts. A context is
leased exclusively to one bridge invocation and never crosses a Python thread.
The compiled global is frozen, and per-request state is cleared or the context is
discarded before reuse.

Pool size is not a model-specific config key. It is computed from measured peak
context memory and the worker budget:

~~~text
pool_size = min(
  worker_concurrency,
  floor((worker_memory_budget - python_headroom) / measured_context_peak)
)
~~~

Zero capacity means the optional advisor/normalizer is not loaded. Native Gludd
discovery, routing, provider execution, and self-improvement continue unchanged.
Gludd never creates unbounded contexts merely because work is queued.

All bridge work uses cancelable evaluation under `asyncio.wait_for`. Cancellation
terminates or closes the owning V8 context and replaces it before new work.
PyMiniRacer's ordinary promise awaiting is not assumed to provide this behavior;
the cancellation E2E test is the authority. Provider I/O is never coupled to the
isolate and remains governed by Gludd's existing cancellation and work deadline.

A hard heap limit, soft collection threshold, wall-clock deadline, maximum ABI
invocations per work item, maximum input size, and maximum returned value are
mandatory.
Exceeding any bound invalidates and closes the context. V8 heap limits do not
cover every native allocation, so worker RSS is monitored; a leaking or crashing
worker loses its lease and is replaced by the existing Gunicorn supervisor.
There is no dedicated FreeLLMAPI process.

When the todo queue is empty and no discovery probe is due, Gludd makes no model
request and provisions no inference compute. The FreeLLMAPI knowledge feed and
bridge never create work or keep model compute alive. Warm isolate retention is an
explicit memory-cost policy; evicting an isolate never deletes the current or
rollback artifact.

## Zero-downtime update and rollback

An artifact generation is immutable. For an update, every worker creates a green
pool beside its serving blue pool, checks artifact digest and ABI, loads the
bundle, validates a signed fixture catalog, and runs a no-secret canary. Only
after all required workers report ready does the supervisor atomically assign new
leases to green. Blue finishes existing leases and remains available during the
rollback window.

Every phase emits a sanitized event: source verified, artifact verified, isolate
created, ABI checked, catalog checked, canary passed, generation switched, old
lease drained, and context closed. Long builds or platform tests emit phase
progress and heartbeats.

Rollback atomically routes new bridge leases to the retained blue generation,
cancels no valid in-flight work, drains green, and closes green contexts. Catalog
snapshots and artifact generations roll back as a compatible pair. Garbage
collection cannot delete:

- the generation serving new work;
- a generation with an active work lease;
- the immediately previous accepted generation;
- evidence referenced by an unresolved task; or
- the last known-good wheel/artifact for any supported platform.

Because Gludd does not run upstream SQLite or migrations, rollback has no
cross-version database hazard. Gludd-owned evidence schema migrations remain
subject to the repository's normal ZDD rules.

## Exact upstream update procedure

One bot-created update branch performs these steps serially and fails closed:

1. Discover a stable upstream release; never select `main` or a prerelease by
   default.
2. Resolve the tag to a full commit, verify repository signature policy, acquire
   the exact source archive, verify its digest, and confirm the MIT license.
3. Diff the npm lock, license set, security policy, provider terms, catalog
   signer/minimum version, eligible data schema, selected exports, and each
   capability's delta evidence. Explicitly record rejected additions.
4. In a credential-free isolated builder, reproduce upstream's Node 20 and 22
   locked install, migration round trip, root tests, lint, workspace build, and
   server coverage. Lifecycle scripts run only in this disposable no-secret
   environment.
5. Resolve the approved TypeScript import graph. Reject new Node built-ins,
   dynamic module loading/eval, native addons, undeclared network/filesystem
   access, or imports outside the allowlist.
6. Use exact locked TypeScript and bundler binaries to build the IIFE twice in
   clean builders. Reject non-identical bytes after deterministic source-map
   normalization. Generate the manifest, licenses, SBOM, and source map.
7. Run the same pure-function ABI conformance fixtures against the bundle in
   upstream-supported Node and in the locked PyMiniRacer/V8. Reject semantic
   differences, undeclared exports, or any attempted host access.
8. Scan source, npm dependencies, compiled bundle, PyMiniRacer wheel, and embedded
   V8. Verify PyPI provenance and hashes for every supported wheel.
9. Run the full credential-free Gludd bridge, ownership-regression, ablation,
   policy, privacy, cancellation, resource-limit, concurrency, self-improvement,
   and ZDD suites. An export without a positive measured delta is removed.
10. Run macOS arm64, macOS x86-64, Linux arm64, and Linux x86-64 smoke tests on
    each supported Gludd Python version. GitHub-hosted Linux is mandatory; absent
    platform capacity is a release blocker, not a skipped pass.
11. Publish the immutable artifact bundle with a CI identity-bound provenance
    attestation. An authorized review records its digest and promotes it through a
    green generation. Optional live tests use Gludd's existing provider transports
    and bounded leased credentials; the bridge only contributes admitted advisory
    data or pure-function output.
12. Exercise rollback, then commit current and previous artifact identities,
    catalog compatibility, evidence, and rationale as one reviewable lock update.

Discovery never mutates the running lock. Renovation is source replacement and
rebuild, not rebasing a fork.

## Security and privacy contract

1. **Admission before bridge.** Project-private or policy-excluded business logic
   is denied before scoring, normalization, or fusion. No source, diff, prompt,
   trace, or artifact reaches an external provider unless the project's egress
   policy permits that exact Gludd-owned provider route.
2. **No ambient authority.** The isolate receives no environment, filesystem,
   network, subprocess, cloud-management token, OpenBao token, provider secret, or
   generic Python callback.
3. **Artifact trust.** Verify the release source, compiled artifact, PyMiniRacer
   wheel, SBOM, and provenance before load. Runtime code download, CDN imports,
   mutable tags, and build-on-first-use are forbidden.
4. **Pure-output validation.** Treat every JavaScript-produced score,
   normalization, or fusion result as untrusted. Python revalidates schema,
   identity, bounds, and invariants. The result cannot weaken privacy, authorize
   egress, select a route, create a lease, or bypass Gludd's normal evidence gate.
5. **Memory safety.** The V8 isolate is a capability boundary, not an operating
   system sandbox. V8 and PyMiniRacer security updates are release-critical.
   Callback APIs weaken isolation, as the
   [PyMiniRacer security notes][miniracer-security] explicitly warn; callbacks are
   therefore prohibited rather than merely constrained.
6. **Records.** Prompts and responses are not cached by this bridge. Events,
   route evidence, and failure detail are bounded and redacted. Source maps remain
   private build evidence and are never returned through an API.
7. **Catalog trust.** A valid signature authenticates upstream bytes; it does not
   authorize provider terms, data egress, spend, or a model-quality promotion.
8. **Terms.** Provider use is deny-by-default. Evaluation-only,
   personal-use-only, ambiguous, or no-resale tiers need an explicit operator
   policy record for the actual workload.

[Security issue #35][security-35] documented authentication bypass,
unauthenticated administration, open CORS, missing rate limits, and
provider-detail leakage in an earlier server version. The embedded design removes
that HTTP/admin surface but does not treat upstream data or logic as trusted
policy.

## Test and acceptance matrix

| Layer | Required proof |
|---|---|
| Static source lock | Reject tags, short commits, source/hash mismatch, license change, missing previous artifact, unknown signer, unsupported ABI, or unpinned build tool. |
| Upstream source | At the exact commit on Node 20 and 22, pass install, migrations, root tests, lint, workspace build, and server coverage. |
| Import purity | Mechanically enumerate the transitive graph and reject Node built-ins, native addons, dynamic imports/eval, undeclared I/O, or files outside the approved upstream paths. |
| Reproducible artifact | Two clean builds produce identical bundle, map, manifest, licenses, and SBOM; attestation subject equals the locked digest. |
| Cross-engine ABI | Golden and property fixtures return identical normalized results in Node and locked PyMiniRacer, including Unicode, large integers, missing fields, typed errors, and malformed input. |
| Catalog security | Verify valid exact-byte signatures; reject modified bytes, wrong key, missing header, old version, rollback, oversized catalog, duplicate IDs, and hostile URLs. |
| Bridge unit | Bound every input/output and operation count; reject unknown operations and host access; never retain object proxies, keys, prompts, or task data between leases. |
| Provider transport | Run Gludd's existing fake-provider matrix unchanged with upstream quirks disabled and enabled; prove the artifact performs no HTTP, authentication, streaming, redirect, retry, or socket work. |
| Async cancellation | Cancel before invocation and during JS CPU/promise work; prove context invalidation, lease release, and no duplicate task. Separately rerun Gludd's unchanged provider-transport cancellation suite. |
| Resource limits | Infinite loops, promise storms, recursion, repeated ABI calls, large inputs/outputs, heap exhaustion, native RSS growth, and malformed source maps fail within limits without wedging other Gunicorn workers. |
| Platform | Run local and GHA smoke suites on macOS/Linux and x86-64/arm64 using only locked wheels; assert V8 and artifact identities. |
| Privacy E2E | Project-private tasks never enter the isolate or model provider; allowed bridge inputs contain no excluded paths/content; events and errors remain redacted. |
| Ownership regression | With the feed and every bridge export disabled, prove discovery, routing, calibration, health, failover, envelopes, privacy, cost, scheduling, lifecycle, and local/Azure execution still pass their existing suites. |
| Delta and ablation | Compare baseline, shadow, and admitted modes on a frozen task/fixture corpus; require preregistered quality gain after latency, memory, cost, and failure penalties. Reject inconclusive results and automatically exercise the removal path. |
| Mixed self-improvement | Run the same atomic task through native local and Azure models with the advisor disabled, shadowed, and admitted; preserve Gludd envelopes and identities, validate independently, and promote only through Gludd's normal gate. FreeLLMAPI is never a model backend. |
| ZDD and rollback | Blue continues serving while green loads/tests; assignment switches atomically; work drains; rollback preserves exactly-once leases; protected artifacts survive cleanup. |
| Live opt-in | With scoped, revocable credentials and existing Gludd transports, run a bounded task with upstream advice disabled and enabled, validate the patch, capture sanitized delta/quota/timing evidence, revoke leases, and prove no billed compute remains solely for an empty queue. |

Production Gludd files require at least 75% branch coverage each and the repository
must retain at least 85% aggregate coverage. Upstream tests are additional
evidence, not a replacement for Gludd bridge, privacy, lifecycle, and
self-improvement tests.

The mandatory GHA path is deterministic and credential-free: exact source
archive, locked build tools and wheels, signed catalog fixtures, fake providers,
bounded model fixtures, malicious bridge fixtures, and teardown assertions. Live
tests are separately labelled and report `not_run`, never a false pass, when
scoped secrets are absent.

## Eligible upstream inputs without copied code

- Signed free-tier catalog snapshots supply model identifiers, advertised
  capability metadata, and catalog rank as uncertain discovery priors.
- Advertised quotas and reset cadence supply timestamped hints; Gludd's observed
  quota and cost accounting remain authoritative.
- Provider quirks supply bounded compatibility, timeout, retry, and error-shape
  hints; Gludd's transport, health, and failover logic interpret them.
- A named pure scoring, normalization, or fusion export may execute only after its
  delta record proves incremental value and defines automatic removal criteria.

The pinned compiled module is the only executable upstream redistribution. Signed
snapshots are immutable attributed inputs, not copied source. Gludd does not
translate provider tables, router code, configuration, migrations, server code,
or upstream tests into locally owned implementations.

## Alternatives rejected

| Alternative | Reason rejected |
|---|---|
| FreeLLMAPI process, sidecar, container, or loopback service | Violates the in-process requirement, duplicates supervision and observability, and creates another credential/network/admin boundary. |
| Git submodule | Couples checkout and release state, encourages local upstream patches, and does not produce a runtime artifact or security boundary. |
| Vendored source or copied upstream tests | Creates a drifting fork and obscures ownership. Exact upstream tests run from an ephemeral verified archive. |
| Published npm package | The package is a configuration CLI, not a reusable server/core API, and would still require a JavaScript runtime boundary. |
| Upstream OCI image | Useful for upstream operators but would run another service. Mutable tag history also makes tags unsuitable as identity. |
| Protocol-only HTTP adapter | Requires a separately running gateway and duplicates Gludd's existing provider transport; eligible data and pure exports need no HTTP service. |
| Full server bundle in a bare JS engine | Express, Undici, Sharp, Better SQLite3, Node globals, and process lifecycle cannot be made available by bundling alone. |
| Handwritten Python port | Duplicates fast-moving upstream logic and makes behavioral drift inevitable. |
| Custom libnode CPython extension | Supplies Node compatibility but creates a large dual-event-loop/native-addon maintenance surface with semver-major embedder breakage. |
| Import upstream catalog directly as truth | A signature authenticates bytes, not provider terms, privacy, task fit, or measured quality. |
| Import upstream discovery, routing, health, failover, envelopes, scheduling, or lifecycle | Duplicates Gludd authority, splits evidence ownership, and creates behavior that cannot be independently disabled for regression and ablation. |

## Practitioner and maintenance evidence

The project is young, so no multi-year FreeLLMAPI issue history exists. The
oldest directly relevant operator threads were retained rather than claiming
long-term stability:

- In [discussion #533][discussion-533], an operator found confusing Docker
  update behavior. The maintainer explained that `latest` had tracked unreleased
  `main` and changed it to follow stable releases. Gludd does not use that image,
  but the incident supports immutable source and artifact identities.
- [Issue #666][issue-666] records a local Ollama custom endpoint being aborted by
  a router-wide retry budget before its provider timeout. This motivates one
  Gludd-owned outer deadline, with the upstream timeout detail consumed only as a
  quirk hint and tested with that hint enabled and disabled.
- [Issue #608][issue-608] demonstrates that a successful public `/v1/models`
  response can coexist with a revoked credential that fails an authenticated
  generation request. Gludd therefore treats catalog reachability, credential
  validity, quota state, and model readiness as separate evidence and never
  promotes a catalog seed without an exact, bounded generation probe.
- [Issue #880][issue-880] reports more than 100 returned model rows but zero
  models accepted by a consuming client because discovery metadata was not
  sufficient to establish compatibility. Gludd consequently retains its own
  capability/profile validation and records upstream rows only as trial seeds.
- [Issue #584][issue-584] records a long-context NVIDIA NIM stream ending after a
  fixed inactivity interval. Gludd's existing transport tests first-byte and
  mid-stream deadlines independently; the artifact performs no stream I/O.
- [Issue #35][security-35] records earlier server authentication/admin weaknesses.
  Removing the server surface is useful defense in depth, not permission to relax
  artifact or egress controls.
- The current [security policy][security-policy] still names `0.6.x` while the
  release page lists `0.9.9`. Update automation therefore reconciles source,
  release, security, and provider facts instead of trusting one page.

## Implementation slices

1. Add signed free-tier catalog/quota/quirk ingestion and an immutable data lock,
   then map authenticated rows onto existing native Gludd discovery candidates as
   uncertain advisory metadata. Prove that disabling the feed leaves every Gludd
   runtime capability unchanged.
2. Add credential-free catalog signature, rollback, schema, privacy, ownership,
   update, ZDD, GHA, and local/Azure self-improvement tests. This is the complete
   default integration when no executable upstream delta has been proven.
3. Add the delta-record and shadow/ablation harness. Evaluate named pure upstream
   exports only against a frozen corpus and the existing Gludd baseline. Do not
   add a JavaScript dependency for an inconclusive or losing export.
4. For each export that independently clears the delta gate, add the immutable
   upstream-source, compiled-artifact, PyMiniRacer-wheel, ABI, provenance, and
   rollback lock plus a mechanical validator. If purity requires source
   duplication, stop and submit an upstream core-export change.
5. Only when slice 4 has an admitted export, add the no-callback PyMiniRacer
   adapter, bounded context pool, typed pure ABI, cancellation, metrics,
   generation swap, and the full credential-free build/platform/security/E2E
   matrix.
6. Add serial data-update automation first. Add executable artifact building,
   attestation, blue/green promotion, rollback, and lease-aware cleanup only when
   an executable export exists. Opt-in local/Azure proofs compare the native
   baseline with each admitted input disabled, shadowed, and enabled.

No slice is complete until focused tests, coverage, lint, type checking, security
scans, lifecycle cleanup, full gate, and hosted CI evidence are green.

[api-reference]: https://github.com/tashfeenahmed/freellmapi/blob/main/docs/en/api/01-rest-api.md
[catalog-sync]: https://github.com/tashfeenahmed/freellmapi/blob/main/server/src/services/catalog-sync.ts
[cli-package]: https://github.com/tashfeenahmed/freellmapi/blob/main/cli/package.json
[discussion-533]: https://github.com/tashfeenahmed/freellmapi/discussions/533
[docker-workflow]: https://github.com/tashfeenahmed/freellmapi/blob/main/.github/workflows/docker.yml
[dukpy]: https://pypi.org/project/dukpy/
[issue-584]: https://github.com/tashfeenahmed/freellmapi/issues/584
[issue-608]: https://github.com/tashfeenahmed/freellmapi/issues/608
[issue-666]: https://github.com/tashfeenahmed/freellmapi/issues/666
[issue-880]: https://github.com/tashfeenahmed/freellmapi/issues/880
[issue-1262]: https://github.com/tashfeenahmed/freellmapi/issues/1262
[issue-1270]: https://github.com/tashfeenahmed/freellmapi/issues/1270
[jsrun]: https://imfing.github.io/jsrun/concepts/runtime/
[license]: https://github.com/tashfeenahmed/freellmapi/blob/main/LICENSE
[miniracer]: https://pypi.org/project/mini-racer/
[miniracer-security]: https://bpcreech.com/PyMiniRacer/architecture/
[node-embedder]: https://nodejs.org/download/release/v24.8.0/docs/api/all.html#c-embedder-api
[pythonmonkey]: https://docs.pythonmonkey.io/
[quickjs-wrapper]: https://github.com/PetterS/quickjs
[security-35]: https://github.com/tashfeenahmed/freellmapi/issues/35
[security-policy]: https://github.com/tashfeenahmed/freellmapi/blob/main/SECURITY.md
[server-entry]: https://github.com/tashfeenahmed/freellmapi/blob/main/server/src/index.ts
[server-package]: https://github.com/tashfeenahmed/freellmapi/blob/main/server/package.json
[upstream]: https://github.com/tashfeenahmed/freellmapi
[upstream-ci]: https://github.com/tashfeenahmed/freellmapi/blob/main/.github/workflows/ci.yml
