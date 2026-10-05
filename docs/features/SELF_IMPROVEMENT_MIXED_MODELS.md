# Mixed-model self-improvement candidate boundary

Status: provider-neutral identity, deterministic task classification, live local,
Azure OpenAI, and Azure Container Apps discovery/inference, candidate-set
assembly, content-free calibrated routing, and managed-runner selection are
implemented behind explicit policy. Gludd now plans and owns the complete Azure
Container Apps resource-group/environment/app lifecycle through the Microsoft SDK
and OpenTofu. The 19-action least-privilege role and workload-identity path are
implemented. Paid canaries now prove concurrent local and Azure inference plus
verified app teardown, but an accepted code improvement and positive Azure Monitor
GPU-utilization evidence remain pending.

## Outcome

Gludd represents an acquired local GGUF, an explicitly named Azure OpenAI
deployment, and an explicitly named model served by Azure Container Apps as typed
candidates under one backend protocol. Candidate identity, execution policy,
project privacy, and provider credentials stay separate. The legacy local-only
path remains the default when live candidate wiring or the remote proposal codec
is omitted.

The opt-in Azure backends discover one exact deployment or Container App model,
revalidate its immutable identity before inference, and expose it through the
same bounded-session protocol as the local adapter. The managed runner classifies
the approved task, assembles the explicit local/cloud set, derives evidence-bound
predictions, deliberately challenges under-tested candidates, executes every
approved trial once, evaluates each result deterministically, and selects from the
observed results. It persists only eligible public quality evidence. Private work
and infrastructure failures remain observable but cannot train the selector.

## Azure infrastructure ownership and library reuse

The Microsoft resource SDK writes only the zero-compute, exact owner-tagged
resource-group boundary. OpenTofu/AzAPI is Gludd's only paid Azure infrastructure
writer. Its lifecycle plan and owner-bound state cover the managed environment,
right-sized workload profiles, model-serving apps, retention in the presence of
foreign apps, and verified teardown. Python orchestrates those reviewed phases
and uses supported SDK clients for the bounded group and observation contracts;
it does not reproduce raw ARM calls.

The Azure CLI argument renderer is intentionally not an Azure client. It validates
identifiers and the checked-in exact role, then writes one NUL-delimited argv for
the real `az` executable. The existing custom read-only ARM transport is replaced by
Microsoft's stable
[`azure-mgmt-appcontainers`](https://pypi.org/project/azure-mgmt-appcontainers/)
client, while GPU evidence uses the stable
[`azure-mgmt-monitor`](https://pypi.org/project/azure-mgmt-monitor/) single-resource
metrics operation. `azure-monitor-querymetrics` is not suitable here because its
batch API documents subscription-level authorization, broader than Gludd's exact
resource-group assignment. Gludd keeps only conversion into its bounded types,
ownership/privacy validation, retry policy, and content-free trace emission.

This boundary also addresses current practitioner evidence. Container Apps issue
[#1646](https://github.com/microsoft/azure-container-apps/issues/1646) makes the v2
built-in Consumption profile part of the observed environment rather than owned
desired state. Issue
[#1682](https://github.com/microsoft/azure-container-apps/issues/1682) reports a
CUDA 12.8 T4 container silently falling back to CPU, so readiness must be followed
by positive `GpuUtilizationPercentage` evidence for the exact revision. Issues
[#1511](https://github.com/microsoft/azure-container-apps/issues/1511) and
[#1763](https://github.com/microsoft/azure-container-apps/issues/1763) justify
bounded visible startup supervision. AzAPI issues
[#856](https://github.com/Azure/terraform-provider-azapi/issues/856) and
[#875](https://github.com/Azure/terraform-provider-azapi/issues/875) justify pinned
v2 export syntax and rejecting sensitive or broad response material in plans.

Terminal readiness observation now uses the maintained Microsoft SDK for both
system-event scopes. The app token reads only that app's stream; the managed
environment token reads the environment stream and then accepts only records whose
`ContainerAppName` matches the owned ephemeral app. The parser caps lines and nesting,
retains no raw provider text, and emits only counts plus fixed reason classes derived
from the documented `Log`, `Reason`, and related status fields. The two additional
role actions are exactly `Microsoft.App/containerApps/getAuthToken/action` and
`Microsoft.App/managedEnvironments/getAuthToken/action`; they do not grant log-store,
secret, registry, network, VM, IAM, provider-registration, or resource-group-delete
access. Microsoft's current
[managed-environment token contract](https://learn.microsoft.com/en-us/rest/api/resource-manager/containerapps/managed-environments/get-auth-token?view=rest-resource-manager-containerapps-2026-01-01),
[system-log schema](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/containerappsystemlogs),
and [Azure CLI implementation](https://github.com/Azure/azure-cli/blob/dev/src/azure-cli/azure/cli/command_modules/containerapp/custom.py#L3243-L3283)
define those boundaries.

This diagnostic path also incorporates long-lived operator reports without treating
them as normative service contracts. Container Apps issue
[#1679](https://github.com/microsoft/azure-container-apps/issues/1679) records a
platform `identity-service` sidecar image-pull failure that stalled activation;
issue [#1780](https://github.com/microsoft/azure-container-apps/issues/1780) records
a platform-sidecar overlay mount failure on an unhealthy node without automatic
rescheduling; and issue
[#783](https://github.com/microsoft/azure-container-apps/issues/783) records an
environment-wide provisioning stall that environment recreation resolved for some
operators. Consequently Gludd observes both scopes, distinguishes platform/container/
capacity classes, and never recycles a retained environment from one app symptom
without matching environment evidence.

The first live run with both token actions dynamically selected the T4-fit candidate.
It read 79 app events and 101 environment events, scoped 77 environment records to
the reusable app name, and leaked none of their provider content. No fixed reason was
present in that immediate sample; Azure then reported the revision
`Failed`/`Unhealthy` with one desired replica and zero concrete replicas. Gludd
withheld the Azure candidate, destroyed the paid app in 18 seconds, verified absence,
and retained only the measured-zero-cost environment. In parallel, local
Qwen2.5-Coder 1.5B generated a two-file proposal from the common envelope, but syntax
preflight rejected it before tests or commit.

The reader no longer treats those app-name counts as exact failure evidence. It binds
every accepted record to the immutable terminal revision, emits fixed error/warning/
unclassified-error counts, and performs at most four visible reads separated by
five seconds to accommodate terminal-event publication lag. This follows Microsoft's
[documented revision-provisioning messages](https://learn.microsoft.com/en-us/azure/container-apps/troubleshoot-container-start-failures)
and the long-lived
[#670 operator report](https://github.com/microsoft/azure-container-apps/issues/670),
where the revision failed only after minutes with an operation-expired diagnosis.
The 15-second diagnostic bound cannot delay mandatory teardown indefinitely, and a
fresh paid canary remains necessary to prove the post-fix behavior.

A 2026-09-10 live canary exposed version skew before the paid app mutation: the
preflight caller used an unverified `2026-01-01` path while the official SDK
adapter admitted `2025-07-01`. Microsoft's current
[managed-environment get](https://learn.microsoft.com/en-us/rest/api/resource-manager/containerapps/managed-environments/get?view=rest-resource-manager-containerapps-2025-07-01)
and
[environment-usage list](https://learn.microsoft.com/en-us/rest/api/resource-manager/containerapps/managed-environment-usages/list?view=rest-resource-manager-containerapps-2025-07-01)
contracts both document `2025-07-01`. Gludd now imports one shared version into
the fixed-path SDK boundary and exercises the real caller/adapter pair in a
credential-free integration test. The long-lived user report in Azure SDK issue
[#34445](https://github.com/Azure/azure-sdk-for-python/issues/34445) records a
similar service-Swagger/SDK release lag; it reinforces testing the composed SDK
boundary instead of assuming independently valid version constants interoperate.
Typed, allowlisted preflight reasons are emitted without provider bodies and are
mapped to the model-neutral authentication, authorization, not-found, quota,
timeout, transport, invalid-response, or unavailable categories.

## Local model cache safety

Managed local candidates are downloaded at immutable revisions into Gludd's
dedicated cache and receive ownership manifests. Reclamation considers only
manifested artifacts, then excludes active leases and every candidate protected by
a live plan reservation. The deletion adapter uses Hugging Face's public
[`scan_cache_dir`](https://huggingface.co/docs/huggingface_hub/en/package_reference/cache#huggingface_hub.scan_cache_dir)
and `delete_revisions` APIs; it independently matches repository, revision,
filename, canonical snapshot, resolved blob, and every path in the returned
strategy before execution, rescans immediately before deletion, and proves the
exact revision absent afterward. It cannot select an unowned path or reach outside
the selected repository.

The supported scanner records a `CorruptedCacheException` for a repository it
skips and returns only independently valid repositories. Gludd therefore allows
that official warning type only when the requested revision is separately present
in the valid typed inventory; a warned target is absent and cannot produce a
deletion plan. Unknown warning types still stop reclamation without exposing paths
or warning text. This distinction matters in practice: Hugging Face issue
[#4420](https://github.com/huggingface/huggingface_hub/issues/4420) reports cache
listings that omitted an on-disk repository, while issue
[#4412](https://github.com/huggingface/huggingface_hub/issues/4412) records orphaned
partial downloads missing from older scan/prune inventories. Gludd neither treats
scanner totals as ownership evidence nor deletes partial or omitted content through
this path.

The same canary then created the environment in 722 seconds and the app ARM
resource in 18 seconds, but no revision became ready before the bounded 900-second
deadline; the owned lifecycle deleted the GPU app in 20 seconds and independently
verified absence. Microsoft's supported
[revision-list operation](https://learn.microsoft.com/en-us/rest/api/resource-manager/containerapps/container-apps-revisions/list-revisions?view=rest-resource-manager-containerapps-2025-07-01)
is therefore part of startup observation even before Azure publishes a latest
ready revision. Gludd selects only an unambiguous active or sole app-owned revision
and emits bounded provisioning, health, running, and replica-count facts. Terminal
states stop the supervisor immediately. Practitioner reports
[#477](https://github.com/microsoft/azure-container-apps/issues/477) and
[#646](https://github.com/microsoft/azure-container-apps/issues/646) show that
startup-probe and image-pull failures can otherwise look like long provisioning
stalls, which is why a generic polling heartbeat is not sufficient evidence.

## Candidate identities

All candidate types are frozen values with a canonical SHA-256
`identity_digest`. The digest uses sorted, compact JSON with a versioned protocol
tag. It is safe to put the digest in events; raw routing fields are not event
fields.

| Provider | Fields bound into identity | Fields deliberately excluded |
| --- | --- | --- |
| Local GGUF | model ID, repository, immutable commit, confined GGUF filename, acquired artifact SHA-256 | cache path, lease path, Hugging Face token |
| Azure Foundry | canonical endpoint, API family, deployment name, API version, deployed model version, deployment ETag | API key, bearer token, tenant credential, subscription credential |
| Azure Container App | canonical endpoint/resource ID, exact app revision, image SHA-256, model repository/commit, GPU profile type | client secret, bearer token, Terraform directory, response content |

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

For a managed local/cloud route, the approved plan's output-token ceiling is also
serialized into the canonical proposal contract consumed by every worker. The
local adapter forwards that exact value through the repository-bound generator,
and llama.cpp uses it for both compact and shared-envelope completions instead of
substituting its former fixed 4,096-token request. The parent rejects any mismatch
before starting the owned worker. The ceiling is included in the envelope digest,
the routing protocol stratum, and repair-seed context, so quality evidence gathered
under one limit cannot be attributed to another. Contracts without the field keep
their historical per-protocol ceiling only for backward-compatible local replay;
new managed mixed-provider routes always bind the explicit approved value.

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

The live Azure compiler now requires one absolute capability-evidence path and
passes that same path to both discovery/selection and the managed runtime. The
selector and the eventual Container App identity share one digest over the exact
model commit, container image digest, and workload profile, so a redeployed app
learns from the intended model/runtime/GPU stratum without inheriting evidence
from another shape. The store fails closed on malformed data, symlinks, non-regular
files, and oversized input. It preserves corrupt bytes for operator recovery,
writes through an unpredictable owner-private temporary file, fsyncs before an
atomic replacement, and never follows a planted predictable temporary-file
symlink. New and repaired evidence files are mode `0600`; Azure credentials remain
outside this store and are never read by selection.

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
baseline. Deterministic task-kind classification, live candidate-set assembly,
managed-runner construction of execution inputs, exact-once evaluation, and
evidence-based selection are implemented. Automatic promotion of a model into an
unbounded or default production policy is deliberately not implemented; every run
still requires the caller's bounded policy and explicit provider opt-in.

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

## Managed-runner selection

When live wiring and a `ManagedCandidateProposalCodec` are present, the managed
runner no longer treats assembly as shadow-only. It builds a complete
`ManagedCandidateTrialSpec` for the local session and each explicitly configured
remote session, authorizes the complete set before the first provider effect, and
passes it to `route_managed_candidate_proposals()`.

Every candidate is invoked exactly once in the approved plan. The same evaluator
that guards ordinary self-improvement assesses decoded local and remote proposals,
and its completed `AttemptResult` is rebound to the selected proposal rather than
being recomputed. Selection favors the first accepted calibrated result and falls
back only to an already-evaluated result when none passed; a provider failure never
creates an unplanned cross-provider call. The returned proposal records the
selected provider, immutable candidate digest, plan digest, and the local trial's
acceptance state without exposing source or response content in traces.

The runner rechecks project identity and privacy before acquisition, discovery,
provider invocation, evaluation, and evidence persistence. A project-private path
therefore stops before either local or Azure proposal generation and cannot enter
the calibration store. The inverse policies of two projects remain isolated even
when their relative source paths are identical.

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

The Container Apps path uses one resource-group-scoped accelerator identity. Its
private Azure CLI JSON is parsed directly by the credential loader; it is never
sourced into a shell. A protected GitHub Actions job can instead pass a short-lived
OIDC assertion to Microsoft's explicit workload-identity credential. The identity
may create or read the exact owner-tagged resource group, manage only Container
Apps and managed environments inside it, read their documented operation status
and single-resource metrics, and perform no resource-group deletion. It has no
Cognitive Services, registry, network, secret, provider-registration, unrelated
Compute, logging, billing, or IAM authority. The exact role and operator bootstrap
are documented in `docs/azure-iam-setup.md`.

### Azure serverless-GPU operational evidence

The runtime treats Azure's advertised profile and quota as admission evidence, not
as proof that a replica actually reached a GPU. Microsoft's current
[serverless-GPU documentation](https://learn.microsoft.com/en-us/azure/container-apps/gpu-serverless-overview)
requires a workload-profile environment and GPU quota, lists West US 3 for both T4
and A100, documents one whole GPU per replica, and currently reports driver 570
with a CUDA 12.x platform runtime. Gludd therefore requires all of those control
plane facts and still withholds model output until its CUDA startup canary,
OpenAI-compatible response validation, exact-revision runtime counters, and cleanup
proof all succeed.

Long-lived practitioner reports explain why the runtime cannot equate an accepted
deployment with working compute:

- [ACA issue #1511](https://github.com/microsoft/azure-container-apps/issues/1511)
  records intermittent T4 executions that create a pod but never reach image pull,
  then recover minutes later; the reporter could not reproduce the same symptom on
  A100.
- [ACA issue #1682](https://github.com/microsoft/azure-container-apps/issues/1682)
  records West US 3 T4 nodes rejecting CUDA 12.8 while an older CUDA/PyTorch image
  works. This conflicts with the newer platform-version documentation, so Gludd
  must measure the deployed revision instead of assuming either account is
  universally true.
- [ACA issue #1705](https://github.com/microsoft/azure-container-apps/issues/1705)
  shows a revision reporting one replica while replica details contain no
  containers and the independent condition is `WorkLoad Profile Full`.
- [ACA issue #1797](https://github.com/microsoft/azure-container-apps/issues/1797)
  records unresolved operator concern about future A100 capacity. An A100 retry is
  therefore an empirical alternative, never a hard-coded availability assumption.

The September 15, 2026 bounded live sequence authenticated with the protected
credential, discovered and selected an immutable public model, created a West US 3
environment in 937 seconds, passed exact-profile quota preflight, and created the
app in 18 seconds. Two subsequent trials reused the retained zero-cost environment
and independently observed `Succeeded` app state plus a desired revision count of
one, but no ready model response; every trial destroyed the app in about 19 seconds
and independently verified absence. The later diagnostic retry additionally read
the exact revision's replica inventory on every readiness poll. These are negative
operational observations, not model-quality labels, and must never lower a model's
learned coding score.

The 937-second cold-environment measurement is why the `zero_cost_only` policy may
retain a compatible managed environment while deleting every app and GPU replica.
Retention remains conditional on zero active apps, zero minimum replicas, no paid
logging/private endpoint/dedicated profile, an ownership match, and a bounded
reconciliation deadline. A changed model, region, profile, owner, cost fact, or
todo demand triggers a fresh plan; inactivity never authorizes paid compute.

### Runtime configuration file

The benchmark CLI accepts an optional `--self-improve-config-file`; the Make
contract exposes it as `SELF_IMPROVE_CONFIG_FILE`. An empty value preserves the
legacy local-only path. A nonempty value must name a regular, non-symlink JSON
object no larger than 65,536 bytes. The Container Apps object uses exactly one of
two credential forms:

- `auth_file` for the private Azure CLI JSON used by a local operator; or
- `client_id`, `tenant_id`, and `federated_token_file` for a short-lived GitHub
  OIDC assertion.

Ambiguous, incomplete, or additional authentication fields fail before any Azure
client is created. Parsing only connects the existing managed runner to the
existing topology, SDK resource-group bootstrap, OpenTofu/AzAPI lifecycle, bounded
candidate session, evaluator, and cleanup contracts; it does not introduce a
second infrastructure implementation. Every live run still requires the explicit
deployment acknowledgement, one-call token/cost/deadline ceilings, an immutable
image/model revision, and an idle-retention policy.

## Discovery and prediction verification

Provider-neutral identities do not by themselves make a model discoverable. The
managed path now performs this bounded sequence:

1. Discover local catalog entries and resolve every Hugging Face revision to a
   commit, as the current planner already does.
2. When Azure OpenAI has been explicitly enabled, get only the configured
   deployment from the approved account; it does not enumerate or guess
   deployments.
3. When Azure Container Apps has been explicitly enabled, read only the configured
   app and exact `/v1/models` inventory; require one expected model and reject app,
   revision, endpoint, or inventory drift.
4. Read deployment name, model version, provisioning state, and ETag from one
   Azure OpenAI management-plane snapshot; reject partial data or subsequent drift.
5. Build typed identities before any project source is sent to a provider.
6. Construct a pre-call prediction keyed by candidate digest and exact task stratum,
   not by a friendly model name.
7. Build an explicit bounded plan containing the preferred candidate and configured
   least-tested challengers; no candidate is inferred after execution starts.
8. Run deterministic evaluation and persist both accepted and rejected public
   labels only for the exact candidate, prompt protocol, project privacy policy,
   evaluator, sampling protocol, and stratum identities.
9. Measure prequential Brier skill; censor infrastructure and private-scope outcomes
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
3. **Hermetic mixed execution (implemented):** exercise local and Azure-shaped
   deterministic fakes in standard CI with the same budget, no-fallback, project
   binding, privacy, and failure-censoring assertions.
4. **Managed routing (implemented, default-off):** classify work, assemble the
   configured set, execute bounded trials, and select from exact evaluation and
   calibration evidence while retaining the unchanged local default.
5. **Live canary:** require a protected environment, explicit opt-in, least
   privilege, cost ceiling, and one non-production deployment. Keep the local
   production path active.
6. **Shadow comparison:** run an approved small task against both providers,
   evaluate independently, and record digest-bound prediction accuracy. Do not
   promote automatically.
7. **Bounded live selection:** admit the live Azure candidate only after the canary
   and cleanup evidence pass. A provider failure ends that attempt; it never causes
   an implicit cross-provider call.

Rollback remains configuration-only: disable Azure opt-in and the
existing local runner continues through the compatibility adapter. Deployment
identity changes invalidate Azure evidence without interrupting local work.

## Test strategy

Standard local and GitHub Actions tests use deterministic in-process fakes. The
live-adapter suite adds 120 warning-strict cases, the routing/calibration suite
adds 40 cases, and the focused execution suite covers local-only, Azure-only,
mixed serial, and mixed concurrent plans; none requires an Azure subscription,
network, or secret. The
canonical integrated self-improvement run passes 6,660 tests with six intentional
skips and one expected failure at 91% aggregate branch-aware coverage; all 37
measured files exceed 75%, including the Azure adapter at 95% and every routing
or execution module at 88% or higher. Together the tests cover both identity types, every
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

The focused execution/routing/backend replay passes 247 warning-strict tests. In
the canonical branch report, `candidate_execution.py` reaches 97%, its three
internal execution modules reach 90% to 99%, candidate routing reaches 100%,
model candidates reach 99%, and the Azure adapter reaches 95%. It additionally proves complete-set
preflight before the first effect, non-consuming authorization, stable result
order, bounded concurrency, policy and project drift checks at later boundaries,
fixed-message error censorship, and calibration-store failure handling.
Two filename-matched internal boundary suites add 11 direct validation, timing,
trace-censorship, invocation, typed-failure, and evidence-failure contracts; the
repository-wide coverage-gap audit consequently reports 1,085 covered modules
and zero untested modules.

No standard CI job needs an Azure subscription or secret. A later live job must
be opt-in, protected, serialized, cost capped, and skipped when its explicit
credential pointers are absent. It must use a disposable non-production
deployment and always emit visible cleanup progress.

The same `make test-azure-containerapp-coverage` command now runs locally and on
the Python 3.11 GitHub Actions gate leg. It executes 391 credential-free unit/E2E
cases and enforces branch-aware coverage across the three orchestration scripts
and 16 source modules. The deployment telemetry and provider-auth cases use
canonical fake Azure bindings, so the strict pre-Terraform provenance checks are
exercised without weakening or bypassing them. The observed 2026-09-06 run
reached 93% aggregate coverage; all 19 files were at or above 75%. The
private-policy target separately executes 28 fake-local/fake-Azure E2E cases and
is structurally pinned to the hosted `other` shard with warnings treated as
errors.

The clean full gate also exercised the shared local/hosted shard plan. After all
earlier Azure regressions passed, the strict 15-second `make -n help` assertion
timed out in `unit-2:batch-030` after 29 accumulated coverage batches. The exact
test passed alone in 2.94 seconds and the unchanged 16-file batch passed 136/136
in 18.78 seconds. Rather than extending the timeout or retrying a failed test,
the canonical registry now gives the subprocess-heavy Make audit one
fresh-process execution lane: local serial gates and the GitHub Actions
`unit-1a1` job consume the same isolated tuple, while `unit-2` excludes it.

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

### S83.157 live model-registry compatibility research

Research checked on 2026-09-10 after the first authenticated live proof reached
Hugging Face discovery:

- The current official
  [`HfApi.list_models` reference](https://huggingface.co/docs/huggingface_hub/package_reference/hf_api)
  supports `sort` and `limit` but no longer accepts the historical `direction`
  argument. Sorting by downloads already yields the highest-ranked models first.
  Gludd therefore uses the current public signature and has a unit contract that
  rejects reintroducing `direction`.
- A long-lived practitioner request,
  [huggingface_hub issue #2741](https://github.com/huggingface/huggingface_hub/issues/2741),
  has tracked the lack of independently addressable `list_models` pages since
  January 2025. A May 2026 report was closed as a duplicate after confirming the
  same limitation in 1.x. Gludd intentionally performs a bounded top-N query and
  must not present that result as an exhaustive Hub inventory.
- Older Hugging Face forum guidance, including
  [“List all tasks from hfapi”](https://discuss.huggingface.co/t/list-all-tasks-from-hfapi/68661),
  still shows `direction=-1`. That user-facing example explains why working 0.x
  integrations can fail immediately after an SDK upgrade. Forum snippets are
  treated as historical operational evidence; the current package reference is
  the normative call contract.

### Live vLLM rejection-envelope research

Research checked on 2026-09-10 after an authenticated Container Apps proof
reached `/v1/chat/completions` and received HTTP 400:

- Practitioner reports [#42474](https://github.com/vllm-project/vllm/issues/42474),
  [#33418](https://github.com/vllm-project/vllm/issues/33418), and
  [#34340](https://github.com/vllm-project/vllm/issues/34340) show that context
  overflow is returned as `BadRequestError` with both root and nested `error`
  envelopes. Across versions, `param` may be `max_tokens`, `input_tokens`, or
  null, and the message varies between "requested", "request has", and prompt
  token wording. Gludd recognizes only the bounded envelope schema plus these
  invariant context markers and emits the fixed
  `context_window_exceeded` category.
- Practitioner report
  [#17977](https://github.com/vllm-project/vllm/issues/17977) records the same
  root `BadRequestError` envelope when a tokenizer has no chat template. That
  case is kept distinct as `chat_template_unavailable`; model discovery alone
  is therefore not treated as proof that chat generation is usable.
- Practitioner report
  [#6890](https://github.com/vllm-project/vllm/issues/6890) shows an otherwise
  valid-looking request rejected with a provider-controlled validation message.
  Unknown recognized vLLM rejections remain the fixed
  `provider_bad_request` category rather than being guessed or relabelled.

These reports are compatibility evidence, not normative API contracts. Provider
messages can contain request data, so Gludd never stores or emits them. It parses
at most 16 KiB, rejects duplicate JSON fields and unexpected envelope keys, and
retains only the HTTP status and one fixed category. Infrastructure rejections
remain censored from model-quality calibration.

### Canonical local/Azure proposal-envelope research

Research checked on 2026-09-10 after a live Azure response passed the HTTP
contract but failed Gludd's proposal decoder:

- The official
  [vLLM structured-output guide](https://docs.vllm.ai/en/latest/features/structured_outputs/)
  documents JSON Schema through the OpenAI-compatible `response_format` field
  and notes that the older guided-decoding parameters are deprecated. Gludd
  therefore uses the maintained structured-output surface rather than inventing
  a vLLM-only response parser.
- A practitioner answer in
  [“How to get structured outputs in vLLM?”](https://discuss.vllm.ai/t/how-to-get-structured-outputs-in-vllm/2142/10)
  reports that behavior can differ by serving API and model configuration. A
  longer-lived user discussion,
  [“I got tired of digging through structured outputs”](https://www.reddit.com/r/LLMDevs/comments/1tarfl4/i_got_tired_of_digging_through_structured_outputs/),
  likewise cautions that OpenAI-compatible endpoints do not imply identical
  schema behavior. These are operational reports, not normative contracts; they
  justify retaining Gludd's strict parent-side decoder after constrained
  generation.
- The pinned image digest resolves to
  [`vllm/vllm-openai:v0.10.2`](https://hub.docker.com/layers/vllm/vllm-openai/v0.10.2/images/sha256-df2607b26bdda2875de4832f4d08da0055b4b6e3570347f3a849bcc652771dd6).
  Current vLLM source documents `uniqueItems` and `contains` as unsupported by
  XGrammar, while the
  [llama.cpp JSON-Schema guide](https://github.com/ggml-org/llama.cpp/blob/master/grammars/README.md#json-schemas--gbnf)
  reports that `prefixItems` is broken and `uniqueItems` is unsupported. A
  shared worker contract therefore cannot depend on either strategy to make a
  proposal array unique. Gludd uses fixed object properties keyed by shard
  ordinal, with each property bound to one constant focus path; both grammar
  engines can enforce that shape using their established object/property
  subset.
- Long-lived vLLM reports
  [#8350](https://github.com/vllm-project/vllm/issues/8350) and
  [#12692](https://github.com/vllm-project/vllm/issues/12692) document incomplete
  or ignored guided output in older engine paths. A newer report,
  [#53975](https://github.com/vllm-project/vllm/issues/53975), demonstrates that
  legacy `guided_json` can be silently ignored while the maintained
  `response_format.json_schema` path works. These practitioner reports reinforce
  two existing decisions: use `response_format`, and still apply Gludd's strict
  parent decoder to every response.

Local and Azure workers now receive one immutable provider-neutral proposal
codec rather than independently constructing merely compatible payloads. That
single envelope owns the exact canonical `GLUDD_SELF_IMPROVE_PROMPT_BATCH_V1`
request bytes, trusted request contract, response instruction, canonical JSON
Schema, protocol digest, sampling digest, and shared decoder. The local worker
reads the canonical serialized artifact directly. Azure approval carries that
same complete serialization as an opaque privacy capability, reparses it and
recomputes its digest immediately before inference, then projects its exact
model-visible fields into the provider API. Neither worker may synthesize or
rewrite the envelope. One `envelope_digest` commits to every artifact and is
emitted content-free by both request boundaries, alongside bounded provider
token accounting, so cross-worker parity is mechanically auditable. The schema
binds response protocol, proposal count, each shard ordinal to one constant
focus path, and compact edit fields; every provider response is passed through
the envelope's shared decoder. Legacy raw-string prompts fail closed instead of
creating a managed remote worker without those artifacts, and the serialized
wire bound accounts for worst-case JSON escaping of every accepted artifact.
Invalid JSON, root shape, protocol identity, proposal count, proposal shape,
scope, line budget, content budget, and semantic validation each produce a fixed
content-free category in routing traces; request, response, contract, provider
text, and schema contents are never emitted.

#### Live canonical-envelope proof (2026-09-10)

An opt-in mixed run executed a local SmolLM2 GGUF worker and a self-provisioned
Azure Container App worker concurrently. Both request boundaries emitted the
same envelope digest,
`d93971ac138f3bcdd4a555ce97c6059cebcb085dfe9a4047c59a457fea09e516`.
Azure returned a structured response with 4,894 input tokens and 480 output
tokens; the shared decoder rejected it as `proposal_scope`. The local response
was independently rejected as `proposal_validation`, and both observations were
persisted as negative calibration evidence. This proves transport, concurrency,
accounting, decoder, and lifecycle parity; it does not claim a successful code
improvement. The paid app was destroyed in 20 seconds and its absence verified;
the empty Consumption environment was retained for 21,600 seconds only because
its measured retained hourly cost was zero.

#### Azure preflight API-version pin (2026-09-11)

The read-only managed-environment preflight now pins `2026-01-01` for the exact
environment, usages, and workload-profile-state paths. Microsoft's current
[Managed Environment Usages reference](https://learn.microsoft.com/en-us/rest/api/resource-manager/containerapps/managed-environment-usages/list?view=rest-resource-manager-containerapps-2026-01-01)
publishes that stable version and exact usages URI. Lifecycle writes remain on
their separately reviewed version; a read-version refresh does not silently
change Terraform-owned mutation semantics.

This version is independently pinned by the usages-path test, while all other
preflight tests consume the production constant so they cannot contradict it.
That separation responds to the practitioner failure in
[Azure CLI issue #32181](https://github.com/Azure/azure-cli/issues/32181), where
a hard-coded Container Apps version was rejected by ARM even though it looked
temporally current. Gludd therefore treats the published operation catalog—not
the date alone—as authoritative and keeps the version under exact-path tests.

#### Live A100 scheduling failure and local continuity (2026-09-12)

A subsequent live run discovered and selected an immutable model that required
`Consumption-GPU-NC24-A100`, reused the owned environment, and created the paid
app in 18 seconds. Azure reported the app and revision as provisioned and healthy,
but the replica API continued to report zero running containers for the complete
900-second readiness bound. Gludd refused to treat those control-plane labels as
model readiness, destroyed the paid app in 20 seconds, independently verified its
absence, and retained only the measured-zero-cost environment under the configured
retention policy.

This is consistent with the long-lived operator report
[Microsoft Q&A 5572527](https://learn.microsoft.com/en-us/answers/questions/5572527/container-app-using-serverless-gpu-stuck-assigning),
which covers both T4 and A100 serverless replicas remaining in
`AssigningReplica`, and with the official
[Container Apps quota documentation](https://learn.microsoft.com/en-us/azure/container-apps/quotas),
which warns that exhausted environment GPU quota can restrict scaling or time out
provisioning. The live path therefore continues to require replica/container
evidence when Azure supplies it and must not infer successful model execution from
a successful ARM write.

A 2026-09-13 retained-environment retry exposed a narrower control-plane
contradiction: for the same exact revision, the revision operation reported
`active=true`, one replica, `Healthy`, and `Provisioned`, while the supplementary
replica-list operation repeatedly returned an empty inventory with no terminal
reason. Microsoft's current
[revision schema](https://learn.microsoft.com/en-us/rest/api/resource-manager/containerapps/container-apps-revisions/list-revisions?view=rest-resource-manager-containerapps-2026-01-01)
defines `healthState` as the revision's current health and `replicas` as its number
of currently running pods. Treating the contradictory optional empty list as an
authoritative zero kept a healthy revision in the readiness loop for the full
bound and delayed cleanup. Gludd now
classifies an empty replica inventory as `supplementary_unavailable` only when the
exact revision independently satisfies every required readiness field. A nonempty
inventory still must prove its containers ready, and any typed terminal replica or
revision state still stops immediately. That distinction is important in the
practitioner evidence from Container Apps issue
[#1705](https://github.com/microsoft/azure-container-apps/issues/1705): its
nonempty replica record reported `WorkLoad Profile Full`, no ready container, and
an HTTP 504, which Gludd continues to classify as terminal capacity exhaustion.
The next boundaries remain direct endpoint health, canonical proposal decoding,
and positive Azure Monitor GPU utilization, so this compatibility fallback cannot
turn an ARM label alone into an accepted code improvement.

The run also exposed an orchestration coupling: the typed Azure timeout occurred
while assembling candidates and prevented the already-approved local candidate
from running. Config-derived mixed execution now explicitly opts into local
continuation for a typed remote infrastructure failure. That option does not
relax configuration, identity, budget, or privacy checks; those failures remain
terminal, as does any remote failure when no validated local session exists. The
continuation event contains only the fixed infrastructure category. Default and
operator-composed policies remain fail closed unless they select this behavior.

#### Retained environment state adoption (2026-09-13)

A later bounded retry independently verified the retained owner-tagged A100
environment, but its isolated worktree had no matching local OpenTofu state.
OpenTofu consequently planned a duplicate create, which the plan auditor correctly
rejected before mutation. Gludd now adopts that exact environment only after the
independent ARM reader has verified its subscription, group, name, ownership tags,
and workload profiles. The importer accepts one exact AzAPI address and resource
ID, rejects foreign or ambiguous state, emits content-free import transitions, and
runs before the audited reconciliation plan. It never imports an unowned resource.

This handles the operational problem described by users in
[OpenTofu issue #1571](https://github.com/opentofu/opentofu/issues/1571): an existing
remote object that is absent from state otherwise requires an error-prone manual
import. Microsoft's
[AzAPI provider guidance](https://github.com/MicrosoftDocs/azure-dev-docs/blob/main/articles/terraform/overview-azapi-provider.md)
requires the import resource ID to carry an API-version query parameter. Gludd
therefore pins `2025-07-01` at the import boundary and accepts only the corresponding
canonical stored ID, with or without that exact query after provider normalization.
The provider's own current
[import changelog](https://github.com/Azure/terraform-provider-azapi/blob/main/CHANGELOG.md)
confirms support for ID-only and ID-plus-API-version forms.

The first adoption proof also showed that AzAPI normalizes the imported plan's
`before_identity.type` and `after_identity.type` to the configured resource type,
rather than the `null` emitted by a create plan. The auditor accepts only `null` or
the exact pinned managed-environment type and API version; every other type, version,
ID, missing field, resource address, action, or mutation channel still fails closed.

#### Concurrent A100/local retry and candidate deadline (2026-09-13)

The next bounded retry reconciled the retained environment, created the paid A100
app in 18 seconds, accepted the exact healthy revision despite Azure's contradictory
empty supplementary replica inventory, and reached direct inference. Azure consumed
4,894 input and 480 output tokens; the common parent decoder safely rejected the
structured response as `proposal_scope`. At the same time, evidence-based routing
selected a local CodeLlama 7B candidate instead of the earlier Qwen 3B candidate.
That worker ran for 301 seconds before the parent killed it, even though its approved
candidate-call budget was 30 seconds. The Azure app was then destroyed in 29 seconds,
absence was independently verified, and only the measured-zero-cost empty environment
was retained. This proves simultaneous provider execution and safe lifecycle cleanup,
not an accepted improvement or positive GPU metric.

The live evidence exposed two separate contracts rather than a model-quality failure.
The local adapter had discarded its approved timeout and the process wrapper used a
fixed 300-second deadline; the resulting process exit was also reduced to `internal`.
The adapter now forwards the exact finite candidate timeout through the runtime builder
to the owned Make worker. Exit 124 becomes a censored `timeout` infrastructure failure,
with provider text and exception context excluded, so it cannot become negative model
calibration. Legacy injected callbacks that do not declare the optional timeout or
proposal-codec keyword retain their existing call shape. The canonical self-improvement
gate passes 7,131 tests with 3 skips and 1 expected failure at 90% aggregate coverage;
every one of its 48 measured files clears the 75% individual threshold. Parent-owned
scope binding and production GPU attestation were the next fail-closed gates; the two
sections below close both locally before another paid retry.

#### Production exact-revision GPU attestation (2026-09-13)

The production runtime now withholds every Azure model response until the stable
Monitor client observes a positive `GpuUtilizationPercentage` maximum for the exact
owner-bound app revision that served it. Monitor construction is lazy and occurs only
for the exact Azure backend. Resource identity, metric name, revision dimension,
finite percentage bounds, and positive sample count all fail closed. Authentication,
authorization, throttling, timeout, not-found, malformed-response, transport, and
unexpected failures become censored typed infrastructure failures; they cannot be
mistaken for model quality or release unverified output. The wrapper closes its
backend, attestor, and partially constructed SDK clients on every exit path.

This closes the CPU-fallback class reported in Container Apps issue
[#1682](https://github.com/microsoft/azure-container-apps/issues/1682) at the actual
response boundary rather than treating a healthy replica as GPU proof. The identical
local/GitHub-Actions Azure profile passes 1,018 tests at 92% aggregate branch coverage,
all 54 measured files clear 75%, and the runtime resource owner reaches 93%. A fresh
bounded paid canary must still emit positive exact-revision GPU evidence and produce
one accepted code improvement before the live capability is complete.

The first post-wiring paid canary provisioned the selected A100 app, completed one
4,894-input-token Azure inference, and then correctly withheld that response when GPU
evidence was not yet available. The new attestor exposed an eventual-consistency bug:
an empty metric collection was classified as malformed immediately instead of being
polled within the existing bounded deadline. The app was still destroyed and its
absence independently verified; only the measured-zero-cost empty environment was
retained. Microsoft's
[Azure Monitor walkthrough](https://learn.microsoft.com/en-us/azure/azure-monitor/platform/rest-api-walkthrough)
documents an empty `timeseries` as a normal no-data response, and a long-lived
[Microsoft Q&A report](https://learn.microsoft.com/en-au/answers/questions/460863/azure-monitor-rest-api-empty-timeseries-data-point)
describes missing minute-granularity data points in successful metric responses. The
parser now treats only a structurally valid empty metric collection or empty time
series as pending and emits its normal content-free heartbeat before polling again.
Missing, null, multiple, foreign-revision, nonfinite, and out-of-range evidence still
fails closed. A paid retry remains necessary because this canary provided Azure
inference and safe lifecycle evidence, not positive GPU proof or an accepted change.

A later 4,037-input-token/246-output-token diagnostic canary proved that the next
failure occurred before metric parsing: no response-invariant rejection event was
emitted. Backend traces now preserve a validated Monitor HTTP status and one
allowlisted attestation reason while continuing to discard provider-controlled text.
The official
[Container Apps metric table](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/supported-metrics/microsoft-app-containerapps-metrics)
defines both `revisionName` and `podName` dimensions for
`GpuUtilizationPercentage`, and the official
[MetricsOperations filter contract](https://learn.microsoft.com/en-us/python/api/azure-mgmt-monitor/azure.mgmt.monitor.operations.metricsoperations?view=azure-python)
requires dimensions not narrowed to a single value to be explicitly selected or
rolled up. The exact query therefore initially bound the owner-verified revision and
explicitly selected all pods instead of sending an incomplete one-dimension filter.

The next paid canary again completed real A100 inference with 4,037 input and 246
output tokens, but the Monitor service rejected that concrete-revision filter with
HTTP 400 before returning metric data. This is consistent with the same SDK contract:
`validate_dimensions=true` rejects an unrecognized filter value, while a freshly
created revision may not yet exist in Monitor's dimension-value index. Gludd therefore
asked Monitor to split both dimensions with `revisionName eq '*' and podName eq '*'`,
then independently required every returned data-bearing series to carry the exact
owner-verified revision. Foreign, absent, or ambiguous revision metadata still fails
closed, so the ingestion workaround cannot attest an earlier deployment.

A subsequent paid retry disproved that wildcard query as a complete remedy: Azure
Monitor still returned HTTP 400 immediately after successful A100 inference. A
[Microsoft Q&A report about delayed metric dimensions](https://learn.microsoft.com/en-us/answers/questions/5811384/not-able-to-select-the-failure-type-dimension-valu)
notes that dimension values populate only after metric data arrives and that new
metrics can take 10--15 minutes to appear. Gludd now treats only HTTP 400 from this
owner-scoped query as a potentially transient registration state, emits a content-free
`query_pending` heartbeat, and retries within the existing bounded attestation
deadline. If registration never becomes ready, it fails closed with the typed
`metric_query_rejected` reason and validated status 400; every other Monitor failure
remains immediate. The app was destroyed in 19 seconds and independently verified
absent, while only the measured-zero-cost environment was retained. A live retry is
still required to establish positive GPU telemetry.

Two further bounded canaries narrowed that failure without accepting unverified
evidence. Microsoft's Monitor walkthrough states that only one dimension may use the
wildcard filter in a request, so Gludd changed the query to split only
`revisionName`; the service still returned HTTP 400. A second canary disabled the
SDK's local dimension-value validation while retaining Gludd's exact-revision parser;
the service again returned HTTP 400 for the full 300-second deadline. This rules out
both the two-wildcard filter and client-side dimension-index validation as the cause.
Each app was destroyed in 19 seconds and independently verified absent.

Those canaries did prove real mixed execution. Azure and local Qwen2.5-Coder 3B
consumed the same parent-built envelope digest concurrently. Azure completed 4,037
input plus 246 output tokens. The local worker completed the same input plus 202
output tokens, produced a 630-byte two-file proposal, passed syntax, all 27 targeted
tests, and full repository collection, then was rejected by the commit guard. The
guard formerly reduced that failure to `category=none`; it now maps only exact
Make-owned terminal lines to `commit_lint_guard`, `commit_docstring_guard`, or
`commit_lock`. Arbitrary model or provider output remains unclassified and is never
forwarded.

An earlier implementation discovered the resource's metric definition through
Microsoft's official MetricDefinitions API before querying values. The runtime role
therefore temporarily added `Microsoft.Insights/metricDefinitions/read`. Microsoft's
[Monitor RBAC operation list](https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/monitor)
distinguishes that catalog permission from metric-value reads. The long-lived
[Microsoft Q&A dimension-delay report](https://learn.microsoft.com/en-us/answers/questions/5811384/not-able-to-select-the-failure-type-dimension-valu)
remains relevant for the bounded pending state, but repeated service-side HTTP 400
responses show that waiting alone is not a sufficient query strategy. Positive GPU
telemetry and an accepted mixed-provider improvement remain required before this
capability is described as complete.

The next bounded mixed canary autonomously added the required A100 workload profile
to the retained environment, created one paid app, and admitted the Azure and local
candidates concurrently. Both consumed canonical envelope digest
`9cd0ce0a39abb298d1d31425bde74241a77a352da2ee8da4b81cf3d6815c74b7`.
Azure completed 4,894 input and 478 output tokens; local Qwen completed the same
4,894 input and 778 output tokens. The local patch reached syntax evaluation and was
rejected. Azure's response remained withheld because the app-scoped metric-definition
catalog returned no GPU record for the entire bound, so the value query was never
attempted. The paid app was destroyed in 19 seconds and independently verified absent;
only the measured-zero-cost empty environment was retained for six hours.

That result identifies catalog discovery as neither a safe prerequisite nor a
necessary runtime permission for a metric Microsoft already publishes. The official
[Container Apps supported-metrics table](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/supported-metrics/microsoft-app-containerapps-metrics)
fixes `GpuUtilizationPercentage` to the `Microsoft.App/containerapps` namespace with
Percent values, Maximum aggregation, `revisionName`/`podName` dimensions, and PT1M
samples. Microsoft's
[MetricsOperations contract](https://learn.microsoft.com/en-us/python/api/azure-mgmt-monitor/azure.mgmt.monitor.operations.metricsoperations?view=azure-python)
accepts that metric name and namespace directly. Gludd therefore skips the catalog,
queries the official constant contract, and has removed
`Microsoft.Insights/metricDefinitions/read` from its exact resource-group role. The
17-action role retains only `Microsoft.Insights/metrics/read` for Monitor. The value
response must independently match the documented metric name and Percent unit and
contain a positive, finite, in-range sample for the exact owner-verified revision;
HTTP 400, no data, foreign dimensions, and malformed responses continue to fail
closed.

A 2026-09-14 paid retry exercised that direct query against a real A100 deployment.
Azure and local Qwen2.5-Coder 3B consumed the same canonical envelope digest
`9cd0ce0a39abb298d1d31425bde74241a77a352da2ee8da4b81cf3d6815c74b7`
concurrently. Azure returned 4,894 input and 478 output tokens; local returned the
same 4,894 input and 534 output tokens before its syntax-invalid proposal was
rejected. The direct Monitor query continued to return HTTP 400 for the complete
five-minute bound, so Azure's response was correctly classified as an infrastructure
failure and never entered model-quality calibration. OpenTofu destroyed the paid app
in 19 seconds, the SDK independently verified absence, and the measured-zero-cost
empty environment was retained.

A second canary tested the documented eventual-consistency hypothesis for the full
15-minute maximum reported by the long-lived
[Microsoft Q&A dimension-delay report](https://learn.microsoft.com/en-us/answers/questions/5811384/not-able-to-select-the-failure-type-dimension-valu).
Azure again completed 4,894 input and 478 output tokens, but every filtered Monitor
request returned HTTP 400 through the 900-second deadline. The run withheld the
response, excluded the infrastructure failure from quality calibration, destroyed
the app in 19 seconds, independently verified absence, and retained only the
zero-hourly-cost environment. The delay hypothesis is therefore falsified for this
request shape; Gludd restores the shorter five-minute bound rather than spending an
extra USD 0.583 per failed diagnosis.

The official [Metrics List REST contract](https://learn.microsoft.com/en-us/rest/api/monitor/metrics/list?view=rest-monitor-2023-10-01)
makes `$filter` optional. The next query is consequently scoped only by the exact,
newly created Container App resource URI and the published metric name/namespace,
avoiding dependence on Azure's dimension-value index. The owner verifies absence
before creation and binds the one active revision. Returned revision metadata, when
present, must still match; a foreign revision still fails closed. An unsplit series
may omit dimension metadata because its Azure scope is already the exact ephemeral
app. Metric name, Percent unit, finite range, positive sample, deadline, heartbeats,
cost cap, and mandatory teardown remain unchanged.

The same run exposed an independent local admission defect: the planner measured only
the largest shard although both workers consume the encoded batch of every shard.
That let Phi-3 Mini's 4,096-token context pass planning before the worker rejected the
larger request. Planning now measures the canonical encoded batch, so local and Azure
capacity decisions use the same envelope size. Container Apps issues
[#1511](https://github.com/microsoft/azure-container-apps/issues/1511) and
[#1763](https://github.com/microsoft/azure-container-apps/issues/1763) remain recorded
as operational delay evidence, but neither is treated as proof of GPU execution.

The same run also confirmed a local failure-classification defect. A fatal native
decode error used the validation-retry marker and was therefore eligible to poison
model-quality calibration. The owned child now reserves exit 2 for proposal validation
and emits a content-free typed infrastructure marker with exit 3 for `OSError` or
`RuntimeError`; the parent converts only that stable marker to `unavailable` and never
persists it as model-quality evidence. Both local and Azure trials continue to consume
the same parent-built request/contract envelope and common evaluator.

After that correction, the managed local Qwen2.5-Coder 1.5B candidate completed the
same 4,037-token request concurrently with Azure, generated 403 completion tokens,
produced a 1,296-byte proposal artifact, applied its patch, passed syntax validation,
passed all 27 task-targeted tests, and passed full isolated collection. Its proposal
was still rejected by the commit-quality guard, so the model received negative quality
evidence rather than an accepted improvement. This proves real local work and common
evaluation, but not yet a successful self-improvement outcome.

#### Exact-profile operational evidence and failover (2026-09-15)

A later retained-environment retry authenticated from the protected credential
generation, reused the exact owned environment, and completed the Container App
control-plane write. The exact active revision then became terminal
`Failed`/`Unhealthy`: its summary requested one replica while the exact revision's
replica inventory contained zero replicas. No model request or GPU attestation began.
Gludd destroyed the paid app, independently verified absence, and retained only the
measured-zero-cost empty environment. This is placement evidence, not a model-quality
observation, and it matches the capacity-exhaustion class reported in the long-lived
Container Apps issue [#1705](https://github.com/microsoft/azure-container-apps/issues/1705).

#### Fresh-environment T4 placement and exact absence (2026-09-21)

The next bounded standalone proof used protected file authentication with in-process
subscription discovery, so neither the identifier nor credential material entered
command output. East US profile and quota discovery admitted T4 and A100. Gludd
updated the exact owned environment in 490 seconds and OpenTofu audited then created
exactly one T4 Container App in 710 seconds. The revision requested one replica but
its exact replica inventory remained empty before the revision became terminal
`Failed`/`Unhealthy`. No inference call or GPU attestation began, and the outcome was
recorded only as infrastructure placement evidence.

The app destroy completed in 19 seconds and the official SDK independently verified
absence. The mandatory environment destroy exceeded OpenTofu's 900-second polling
window, but ownership remained with the harness: it continued the independent ARM
absence check until Azure reported the environment gone. The content-free operation
digests were `2badefff303e198f4f4d1f156918a1c3a9f9f96abf1041fac12b95a3193f40d4`
for the app and
`be15cab31fe8066e6e938d5ac01127925941a9abde7f8d6013118d37452cb919`
for the environment. Thus the run proves paid-resource acquisition and exact cleanup,
not usable GPU capacity, model quality, or completion of S83.157.

This fresh failure is consistent with practitioner reports of intermittent T4
placement failure before image pull in Container Apps issue
[#1511](https://github.com/microsoft/azure-container-apps/issues/1511) and constrained
long-term A100/H100 availability in issue
[#1797](https://github.com/microsoft/azure-container-apps/issues/1797). Those reports
support bounded empirical failover; they are not capacity guarantees. Repeating the
same paid East US T4 placement without newer availability evidence is therefore not
an admissible next step.

Bootstrap now records only an exact, digest-bound operational tuple after resource
cleanup: region, workload-profile type, immutable container-image digest, deployment
identity digest, lifecycle phase, fixed failure class, and timestamp. Prompts, model
names, image repositories, provider messages, credentials, and endpoints are excluded.
Authentication, authorization, invalid responses, request-phase failures, stale or
future observations, foreign images or regions, and tampered records cannot suppress a
profile. Only repeated recent `rate_limited`, `timeout`, or `unavailable` startup or
preflight evidence reaches the selector.

The selector routes around such a profile only to another discovered profile that is
still sufficient for the selected immutable model and remains within the same explicit
hourly-cost policy. It does not hard-code T4, A100, a model name, or a region, and the
operational record never enters empirical model calibration. The location is passed
through the single `azure-self-improve-live-proof` workflow so the selection and
bootstrap phases share one evidence scope. The identical credential-free local/GitHub
Actions profile exercises these branches: 1,171 tests pass, aggregate coverage is 91%,
and every one of 61 measured files exceeds the 75% individual floor.

#### Versioned operational-availability learning and rollback

The placement channel now records both successful and failed terminal startup
outcomes in a second, strict schema. Each record binds a lowercase Azure region,
provider-discovered resource SKU, immutable runtime-image digest, and a generic
serving-topology digest derived from required VRAM. The scope and record protocols
are independently versioned and digest-bound. Model names, revisions, prompts,
endpoints, credentials, image repositories, and Azure response text are not stored.
An unknown field, unsupported schema or scope version, invalid coordinate, future
timestamp, or digest mismatch makes the owned collection unusable rather than
silently relaxing placement policy.

Only recent `available`, `rate_limited`, `timeout`, and `unavailable` startup or
preflight terminals affect placement. Authentication, authorization, transport,
invalid-response, internal, and request-phase outcomes remain excluded and can never
become model-quality evidence. Consecutive failures can make only their exact scope
infeasible; a later successful terminal resets that failure streak. A beta prior ranks
the remaining exact scopes, while publisher, licence, required and blocked tags,
context, immutable identity, and hourly cost are still hard gates evaluated outside
that score. With no recent evidence, selection remains the smallest sufficient
profile. The inventory is data-driven, so neither accelerator nor model names are
configuration keys.

The older failure-only profile records remain readable during migration. Once a
versioned terminal record exists, the selector uses the strict channel as the source
of placement truth so successful recovery can supersede a former failure streak.
Rollback is zero-downtime: stop passing the optional evidence store or let its bounded
TTL expire, and the neutral prior restores the existing smallest-sufficient plan on
the next selection. Evidence loading performs no Azure mutation, selection creates a
new mode-0600 artifact instead of replacing one, and a failed evidence write closes
the owned runtime through the existing lifecycle exception path. The already cited
long-lived Container Apps capacity report
[#1705](https://github.com/microsoft/azure-container-apps/issues/1705) remains the
practitioner basis for treating terminal zero-placement as infrastructure evidence,
not as a service guarantee or model-quality result.

#### Parent-owned per-ordinal proposal scope (2026-09-13)

The live rejection exposed a decoder/schema mismatch. The managed schema previously
built one compact item from the union of every shard's editable coordinates and copied
it to every ordinal. A response could therefore satisfy the advertised schema while
selecting a line legal only in another file. The decoder also used the model's redundant
`focus_path` field to reorder the batch, allowing two approved paths to exchange edits.

Each ordinal now receives a schema generated from only its exact path and editable
ranges. After checking that the transport field is a string, the parent discards its
value and binds the edit to the already-approved ordinal. A model can neither add a
path nor swap two approved files; the trusted baseline, editable ranges, expansion,
assessment, and gate remain unchanged. This mirrors the local decoder, which has
always received its focus path from parent state rather than model output.

Parent-side validation remains mandatory even when vLLM advertises structured output.
Practitioner report
[#15236](https://github.com/vllm-project/vllm/issues/15236) documents release-dependent
xgrammar failures for enums and numeric ranges, while report
[#17481](https://github.com/vllm-project/vllm/issues/17481) describes schema-shaped
Qwen output with unconstrained integers and truncation. Gludd therefore treats the
server schema as a generation aid, never as its authorization boundary. The managed
routing profile passes 48 tests at 91% aggregate branch coverage, with all five files
above 75%; the codec reaches 94% and the schema owner reaches 89%.

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

### S83.150 hosted-CI push reliability research

Research checked on 2026-09-04 after the first hosted-CI handoff exposed a local
push-orchestration regression:

- GitHub's long-lived community report
  [#26566](https://github.com/orgs/community/discussions/26566), opened in 2021,
  records workflow-level `cancel-in-progress` behaving differently from operator
  expectations. Report [#33948](https://github.com/orgs/community/discussions/33948),
  opened in 2022, records an in-progress run receiving `SIGTERM` despite
  `cancel-in-progress` not being enabled, with another user reproducing it while
  explicitly setting the option false. These are practitioner reports, not API
  guarantees, but they show why an orchestrator must not infer durable CI progress
  merely because one push command returned.
- Discussion [#55027](https://github.com/orgs/community/discussions/55027) has
  continued since 2023 and documents the less obvious pending-run behavior: under
  the default single concurrency queue, newer arrivals can replace older pending
  runs even when the active run is retained. The normative
  [GitHub concurrency documentation](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)
  now states that replacement behavior explicitly.

The resulting boundary is intentionally conservative. `deploy-and-forget` selects
exactly one guarded push target, never retries through a second target, propagates
every guard or push failure, and records cooldown/restart state only after the push
lands. Its hermetic validation mode exercises branch routing in local and hosted CI
without contacting GitHub, resolving credentials, mutating Git state, or touching
operator state. Exact-SHA hosted evidence remains a separate acceptance condition;
a local push attempt is never presented as proof that a workflow started.
