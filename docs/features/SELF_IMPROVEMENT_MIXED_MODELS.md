# Mixed-model self-improvement candidate boundary

Status: provider-neutral identity, live Azure OpenAI discovery and inference,
content-free calibrated ranking/trial-planning, and bounded execution of an
already-approved plan are implemented; automatic candidate-set assembly and
managed-runner selection remain disabled.

## Outcome

Gludd can represent an acquired local GGUF and a deployed Azure Foundry model as
different typed candidates under one backend protocol. Candidate identity,
execution policy, and provider credentials stay separate. This separation is the
prerequisite for testing local and Azure candidates without silently changing the
existing local-only managed runner.

The opt-in `AzureOpenAICandidateBackend` can now discover one exact deployment
through Azure Resource Manager and invoke that deployment through Azure's unified
OpenAI v1 endpoint. Provider-neutral routing primitives can rank immutable local
and Azure predictions, deliberately challenge a preferred model, measure pre-call
calibration, and execute a caller-approved local-only, Azure-only, or mixed plan
through explicitly supplied bounded sessions. The managed runner still selects
and acquires only local GGUF candidates. Its legacy proposal callback is
transported through `LocalProposalBackendAdapter` with the same four objects,
return object, exception identity, progress events, and lease lifecycle.
Therefore this tranche does **not** claim automatic end-to-end mixed-provider
self-improvement.

## Candidate identities

Both candidate types are frozen values with a canonical SHA-256
`identity_digest`. The digest uses sorted, compact JSON with a versioned protocol
tag. It is safe to put the digest in events; raw routing fields are not event
fields.

| Provider | Fields bound into identity | Fields deliberately excluded |
| --- | --- | --- |
| Local GGUF | model ID, repository, immutable commit, confined GGUF filename, acquired artifact SHA-256 | cache path, lease path, Hugging Face token |
| Azure Foundry | canonical endpoint, API family, deployment name, API version, deployed model version, deployment ETag | API key, bearer token, tenant credential, subscription credential |

A repository and commit are optional only for an operator-supplied local file;
the acquired artifact digest remains mandatory. Repository-managed GGUFs require
both fields together and require a 40-character commit SHA.

Azure has two non-interchangeable API families:

- `azure_ai_model_inference` requires a canonical
  `https://<resource>.services.ai.azure.com/models` endpoint and an explicit
  dated API version.
- `azure_openai` requires a canonical
  `https://<resource>.openai.azure.com` root and the `v1` API marker. The live
  adapter appends the documented `/openai/v1/` route and never accepts a complete
  target URL, query string, or deployment route as its configured root.

The Azure identity rejects HTTP, credentials embedded in URLs, ports, query
strings, fragments, wrong Azure DNS families, mismatched paths, mutable version
aliases, control characters, and incomplete deployment evidence. In particular,
an API key can never be supplied as an identity field.

Changing any routing or deployment field changes the digest. A backend whose
identity changes after session construction is rejected before it sees another
request. This prevents evidence learned for one deployment revision from being
attributed to a replacement behind the same friendly deployment name.

## Backend and budget contract

`CandidateBackend[Request, Response]` exposes exactly one candidate identity and
one `generate` operation. A backend implementation must call only that candidate;
it is not a router. `BoundedCandidateSession` snapshots the identity and binds one
backend for its entire lifetime, so it has no automatic fallback surface.

Before every call, the session reserves the worst-case amount rather than relying
on provider-reported usage after the fact:

- one of at most 16 explicitly approved calls;
- per-call input and output token ceilings;
- a total input-plus-requested-output token ceiling;
- a total estimated cost ceiling in micro-US-dollars; and
- one positive timeout no greater than one hour.

Calls rejected for identity drift, missing Azure opt-in, or budget exhaustion do
not reach the backend and do not consume budget. Once a provider call starts, its
reserved tokens, cost, and call count remain consumed even if infrastructure
fails. That conservative accounting prevents retry storms from escaping an
approval.

`BoundedCandidateSession.authorize` exposes the same identity, provider-opt-in,
and budget checks without consuming a reservation. Plan execution uses it to
preflight the complete call set before any backend can observe a request.
`generate` repeats those checks and reserves immediately before the real call,
closing the check-to-use boundary without weakening direct session callers.

The session never catches `BaseException`, so cancellation and process shutdown
retain their normal control flow. A typed `BackendInfrastructureError` preserves
only its enumerated category. Any other backend exception is translated to
`internal` with exception chaining suppressed. Endpoints, SDK response bodies,
credentials, and exception messages therefore cannot escape through the shared
error contract.

Current infrastructure categories are authentication, authorization, not found,
rate limited, timeout, transport, unavailable, invalid response, and internal.
Concrete providers must translate SDK exceptions at their boundary and may not
copy SDK text into the typed exception.

## Live Azure OpenAI adapter

`general_ludd.self_improve.azure_backend` implements one synchronous live path:

1. Require an explicit `azure_enabled=True` config before constructing any Azure
   credential or client.
2. Use `CognitiveServicesManagementClient.deployments.get` with retries disabled,
   bounded connection/read timeouts, and SDK logging disabled.
3. Require the requested deployment name, `Succeeded` provisioning state,
   immutable model version, and server-provided ETag; absent, partial, mutable, or
   malformed snapshots fail as `invalid_response`.
4. Re-read the deployment before every inference and reject version/ETag drift
   before resolving the inference secret or revealing a prompt.
5. Accept only `AzureApprovedPrompt`, which is created and revalidated through
   `SelfImproveRuntimePolicyGuard`; a raw string or a private/drifted path scope
   cannot reach the SDK.
6. Lazily construct the maintained `openai.OpenAI` client with `max_retries=0`,
   then call `responses.create` exactly once with the deployment name,
   `store=False`, the approved output ceiling, and the bounded call timeout.

The adapter has no local-backend field, retry callback, alternate deployment, or
fallback branch. Azure/SDK exceptions are reduced to the existing typed categories
without copying response bodies or exception strings. Its trace objects contain
only phase, candidate digest, call ordinal, typed failure, and accepted token
counts. Prompt and response text are excluded from representations and traces.
Exception-chain context is also removed at credential, policy, discovery, client,
request, and trace boundaries, so provider or secret text cannot be recovered by
introspecting a censored exception. Hostile mapping and string subclasses fail
closed rather than running provider-controlled accessors during validation.

The cumulative accounting snapshot separately records provider requests started,
responses received, responses accepted, failed requests, and exact provider-
reported input/output/total tokens. A provider exception consumes one started
request; a returned malformed response also records one received response but no
accepted response. This makes retries a deliberate outer-policy decision instead
of invisible SDK behavior.

## Calibrated routing boundary

`general_ludd.self_improve.candidate_routing` now exposes a content-free decision
boundary. A `CandidatePrediction` binds one immutable candidate digest to a typed
task category, bounded categorical task kind, evaluator and sampling protocols,
privacy policy, and evaluation stratum. It records pre-call estimates for
acceptance probability, latency, input/output tokens, and cost without accepting
source text, paths, endpoints, deployment names, or credentials.

Each completed call becomes one `CandidateAttempt` with exactly one disposition:
accepted, deterministically rejected, or infrastructure failure. Both accepted and
rejected public evaluations are necessary binary labels and may update the exact-
stratum capability evidence. Private-scope attempts and infrastructure failures
emit a content-free `SELF_IMPROVE_MODEL_CALIBRATION_SKIPPED` trace and never become
model-quality evidence. Persisted records carry canonical evidence, prediction,
and attempt digests; loading rejects malformed, foreign-stratum, or tampered
records.

Ranking uses a conservative beta-posterior lower bound for acceptance, then cost,
latency, token estimate, and immutable identity as deterministic tie-breakers.
`plan_bounded_candidate_trials` authorizes at most 16 explicit calls, labels each
as preferred, challenge, or ranked, and makes concurrent versus serial execution a
required boolean. A plan contains no fallback: an infrastructure failure ends that
candidate attempt. Challengers are least-tested candidates selected within the
caller-provided bound, so predictions can be falsified rather than becoming a
self-confirming routing loop.

Calibration is reported prequentially with Brier skill against the causal empirical
base rate for one exact task stratum. Later evidence cannot rewrite an earlier
baseline. These facilities are implemented and tested, but automatic task-kind
classification, live candidate-set assembly, managed-runner construction of the
execution inputs, and promotion policy based on calibration remain pending.

## Approved plan execution

`general_ludd.self_improve.candidate_execution` accepts one immutable
`CandidateTrialPlan`, its externally recorded `plan_digest`, and exactly one
`CandidateTrialCall` for every plan ordinal. Before the first call it validates
the whole plan, rejects missing, duplicate, extra, or identity-mismatched
sessions, and non-destructively authorizes every session against the token, cost,
timeout, provider-opt-in, and call budgets already bound into the plan. The
executor never discovers another candidate, changes an output ceiling, retries a
call, or creates a fallback route.

Serial plans execute each preapproved ordinal once. Concurrent plans use a
namespaced thread pool capped by the plan's existing 16-trial hard limit, while
returning results in stable plan order. A failure in one explicitly planned call
becomes that candidate's censored infrastructure attempt; it does not synthesize
a replacement call. Local-only and Azure-only plans use the same path as mixed
plans, so provider composition does not create a second policy implementation.

`CandidateExecutionBoundary` combines the existing
`SelfImproveRuntimePolicyGuard` with a caller-supplied project-binding identity
probe. It rechecks both identities during complete-plan authorization and again
immediately before backend invocation, deterministic evaluation, and calibration
persistence. Drift before evaluation discards the opaque response and fails with
a fixed category. Drift at the learning boundary records a private-scope skip
instead of writing evidence. This preserves a useful content-free operational
trace without attributing behavior to the wrong project or privacy policy.

Execution traces contain only plan/candidate digests, ordinals, provider and
outcome enums, bounded counts, and fixed failure/skip categories. Requests,
responses, paths, credentials, evaluator exceptions, and provider text are not
trace fields. Responses remain available to the explicit caller but are excluded
from result representations. Both private-scope results and infrastructure
attempts go through the existing calibration contract and are excluded from
quality learning; accepted and deterministically rejected public attempts remain
eligible evidence.

## Explicit Azure opt-in and credentials

Azure is denied unless the caller constructs the bounded session with
`azure_enabled=True`. Supplying credentials alone is not opt-in. Selecting an
Azure identity alone is not opt-in. There is no local-to-Azure or Azure-to-local
fallback after a rejection or provider failure.

The live backend needs these explicit values from configuration plus an approved
secret store or environment indirection:

- an Entra credential available to the Azure management SDK for deployment
  discovery;
- either an `AZURE_INFERENCE_CREDENTIAL` environment pointer for key-based
  inference or the same supported `TokenCredential`/managed identity;
- the canonical endpoint for the selected API family;
- the exact deployment name, not the underlying catalog model name;
- the API family and API version marker;
- subscription ID, resource group, and account name for the exact management-plane
  lookup; and
- the deployed model version and current management-plane deployment ETag returned
  by that lookup.

The credential value must be resolved only inside the backend immediately before
client construction. It must never be copied into a candidate, plan, prompt,
event, exception, capability record, retry message, or test artifact. Credential
references and identity metadata must be revalidated at every effect boundary.
For Entra inference, the adapter uses the documented
`https://ai.azure.com/.default` token scope. An API key alone is insufficient for
this discovery-backed path because the ARM lookup still requires Entra
authorization.

## Discovery and prediction verification

Provider-neutral identities do not by themselves make a model discoverable. The
live Azure adapter now produces one immutable Azure OpenAI candidate snapshot, and
the existing local planner resolves immutable GGUF artifacts. Candidate-set
assembly in the managed runner remains future work:

1. Discover local catalog entries and resolve every Hugging Face revision to a
   commit, as the current planner already does.
2. When Azure has been explicitly enabled, get only the explicitly configured
   deployment from the approved Cognitive Services account; it does not enumerate
   or guess deployments.
3. Read deployment name, model version, provisioning state, and ETag from one
   management-plane snapshot; reject partial data or subsequent drift.
4. Build typed identities before any project source is sent to a provider.
5. Construct a pre-call prediction keyed by candidate digest and exact task stratum,
   not by a friendly model name.
6. Build an explicit bounded plan containing the preferred candidate and configured
   least-tested challengers; no candidate is inferred after execution starts.
7. Run deterministic evaluation and persist both accepted and rejected public
   labels only for the exact candidate, prompt protocol, project privacy policy,
   evaluator, sampling protocol, and stratum identities.
8. Measure prequential Brier skill; censor infrastructure and private-scope outcomes
   from model-quality learning.

An Azure deployment that is updated in place gets a new ETag or model version and
therefore a new candidate digest. Its old behavior evidence must not be treated as
proof for the new deployment. Infrastructure failures are censored operational
signals, not negative model-quality evidence.

## Zero-downtime rollout

The integration sequence preserves the current local service throughout:

1. **Identity-only:** ship the frozen types, fake backends, and local adapter. No
   live configuration is read and no network path exists.
2. **Single-candidate live adapter:** discover an explicitly configured Azure
   deployment and expose an opt-in, policy-gated backend without wiring it into
   local selection.
3. **Fake mixed execution (implemented):** exercise local and Azure-shaped
   deterministic fakes in standard CI with the same budget, no-fallback, project
   binding, privacy, and failure-censoring assertions.
4. **Live canary:** require a protected environment, explicit opt-in, least
   privilege, cost ceiling, and one non-production deployment. Keep the local
   production path active.
5. **Shadow comparison:** run an approved small task against both providers,
   evaluate independently, and record digest-bound prediction accuracy. Do not
   promote automatically.
6. **Bounded selection:** allow Azure into a specific approved candidate plan only
   after canaries and rollback tests pass. A provider failure ends that attempt;
   it never causes an implicit cross-provider call.

Rollback is configuration-only until phase six: disable Azure opt-in and the
existing local runner continues through the compatibility adapter. Deployment
identity changes invalidate Azure evidence without interrupting local work.

## Test strategy

Standard local and GitHub Actions tests use deterministic in-process fakes. The
live-adapter suite adds 120 warning-strict cases, the routing/calibration suite
adds 40 cases, and the focused execution suite covers local-only, Azure-only,
mixed serial, and mixed concurrent plans; none requires an Azure subscription,
network, or secret. The
canonical integrated self-improvement run passes 6,641 tests with six intentional
skips and one expected failure at 90% aggregate branch-aware coverage; all 33
measured files exceed 75%, including the Azure adapter at 95% and every routing
module at 88% or higher. Together the tests cover both identity types, every
identity field, invalid endpoint families,
immutable versions, URL credential injection, opt-in denial before input reaches
the backend, per-call and aggregate budgets, call consumption on failure,
identity drift, typed and untyped infrastructure failures, absence of fallback,
exact local callback compatibility, malformed live configuration, lazy API-key and
Entra authentication, private-policy denial/drift, ARM discovery failures,
auth/quota/timeout/transport classification, exact OpenAI request parameters,
response validation, redacted traces, cumulative token accounting, and SDK resource
cleanup. Routing cases additionally cover exact-stratum persistence, tamper
rejection, accepted and rejected labels, private/infrastructure censoring,
prequential calibration, deterministic resource tie-breakers, least-tested
challenges, serial/concurrent plans, and hard trial bounds.

The focused execution/routing/backend replay passes 127 tests. Its three measured
files reach 97.2% aggregate line coverage and 95.4% aggregate branch coverage;
`candidate_execution.py` reaches 95.7% line and 92.6% branch coverage, with no
measured file below the 75% per-file floor. It additionally proves complete-set
preflight before the first effect, non-consuming authorization, stable result
order, bounded concurrency, policy and project drift checks at later boundaries,
fixed-message error censorship, and calibration-store failure handling.

No standard CI job needs an Azure subscription or secret. A later live job must
be opt-in, protected, serialized, cost capped, and skipped when its explicit
credential pointers are absent. It must use a disposable non-production
deployment and always emit visible cleanup progress.

## Field evidence and design implications

Research checked on 2026-09-04:

- [Azure SDK for Python issue #39835](https://github.com/Azure/azure-sdk-for-python/issues/39835)
  was opened on 2025-02-23 after code copied from the Foundry **Consume** tab
  returned a 404 from `ChatCompletionsClient.complete`. The issue was closed with
  its title changed to identify the missing `/models` path in code samples.
  Design implication: API family and canonical endpoint shape are identity data,
  not interchangeable configuration decoration.
- The official
  [Foundry Models classic quickstart](https://learn.microsoft.com/en-us/azure/ai-foundry/foundry-models/how-to/quickstart-ai-project?view=foundry-classic)
  shows the model-inference endpoint with `/models` and key authentication through
  `AZURE_INFERENCE_CREDENTIAL`. Design implication: the credential is runtime
  authorization, while `/models` belongs to validated routing identity.
- The official
  [Foundry endpoint reference](https://learn.microsoft.com/en-us/azure/ai-studio/ai-services/concepts/endpoints)
  distinguishes Azure OpenAI from model-inference endpoints, requires the
  deployment name in requests, and documents implicit versioning for
  `/openai/v1/`. Design implication: an explicit API-family enum prevents an SDK
  or route migration from silently reinterpreting one stored endpoint.
- A long-lived Microsoft Q&A thread,
  ["Open AI error: Resource not found"](https://learn.microsoft.com/en-us/answers/questions/1187169/open-ai-error-invalidrequesterror-resource-not-fou),
  records 404s caused by a trailing slash, copied whitespace, and API-version
  mismatch beginning in 2023. Another community report,
  ["Batch API calls ... OperationNotSupported"](https://learn.microsoft.com/en-us/answers/questions/2108663/batch-api-calls-in-azure-openai-yield-operationnot),
  describes a misleading failure after using the portal's complete target URI as
  the SDK base endpoint. Design implication: fail on noncanonical values instead
  of trimming or guessing, because silent repair can target a different route.

The forum reports are operational anecdotes rather than normative API contracts.
They support the strictness decision; Microsoft documentation defines the actual
endpoint and credential requirements.

The bounded executor carries those operational lessons forward: because endpoint
and deployment mistakes have historically surfaced as ambiguous provider errors,
an execution-time failure is retained only as a typed infrastructure observation.
It never becomes a negative quality label or a reason to try a different provider.

### S83.150 live-adapter research

Research checked on 2026-09-04 before the adapter was implemented:

- Microsoft's current
  [Foundry SDK and endpoint overview](https://learn.microsoft.com/en-us/azure/foundry/how-to/develop/sdk-overview)
  identifies `azure-ai-projects` 2.x as the stable project SDK and the standard
  `openai` client as the lowest-latency, maximum-compatibility inference path. The
  [classic-to-current migration guide](https://learn.microsoft.com/en-us/azure/foundry/how-to/navigate-from-classic)
  says `azure-ai-inference` retired on 2026-08-26 and maps model inference to
  `OpenAI()` with `base_url`. This ruled out adding the retired inference package.
- The official
  [Azure OpenAI endpoint reference](https://learn.microsoft.com/en-us/azure/ai-studio/ai-services/concepts/endpoints)
  documents `/openai/v1/`, deployment name in the `model` field, implicit API
  versioning, `store=False`-compatible Responses calls, API-key authentication,
  and Entra token providers. The official
  [Cognitive Services management SDK](https://learn.microsoft.com/en-us/python/api/overview/azure/mgmt-cognitiveservices-readme?view=azure-python)
  and [Deployment model reference](https://learn.microsoft.com/en-us/python/api/azure-mgmt-cognitiveservices/azure.mgmt.cognitiveservices.models.deployment?view=azure-python)
  expose the ARM client and server-populated ETag; `DeploymentModel.version`
  supplies the immutable model version. These maintained clients are preferred to
  custom HTTP because they own Azure authentication, token refresh, service API
  shape, and deployment-model decoding while Gludd keeps retry, privacy, identity,
  and accounting policy explicit.
- The official OpenAI Python
  [v1.66.0 release](https://github.com/openai/openai-python/releases/tag/v1.66.0)
  introduced `/v1/responses`. The project dependency therefore requires
  `openai>=1.66.0`; the prior `>=1.0.0` floor did not guarantee the documented
  `client.responses.create` boundary used by this adapter.
- Exactly one long-lived user report was used for live-path operational evidence:
  [Azure SDK for Python issue #42361](https://github.com/Azure/azure-sdk-for-python/issues/42361),
  opened 2025-08-05 and still inactive without a product resolution at research
  time, describes high-rate evaluation producing connection-labelled failures and
  repeated 60-second quota retries. It is anecdotal rather than normative. The
  design implication is to disable SDK retries, classify 429 separately from
  transport failures, count each started request once, and leave any retry to the
  already bounded outer session.
