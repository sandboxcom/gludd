# Azure Terraform event, elasticity, and live E2E evidence

Status: acceptance contract and research record

Evidence date: 2026-08-01

Repository baseline: development commit `639e5032`

This document turns recurring Azure, Terraform, Event Grid, and Gunicorn
operator failures into testable Gludd requirements. It complements the
[Azure accelerator live-proof specification][gludd-accelerator-proof] and the
[Postgres multi-worker architecture][gludd-postgres-workers]. It does not claim
that the requirements below are implemented merely because a Terraform apply,
mocked test, or endpoint poll succeeds.

## Outcome

Gludd must receive Azure resource lifecycle events from Terraform-created
infrastructure without treating a control-plane event as workload readiness. It
must durably consume duplicate and out-of-order events across multiple Gunicorn
workers, begin work immediately after resource-specific readiness passes, scale
compute without thrashing, and prove that every chargeable resource is
deallocated and deleted.

The required lifecycle is:

```text
Terraform/ARM operation
  -> authenticated Event Grid delivery
  -> durable inbox commit and HTTP acknowledgement
  -> one leased reconciler across all Gunicorn workers
  -> resource-specific runtime readiness
  -> workload admission and progress events
  -> drain, deallocate, delete, and verified absence
  -> delayed billed-cost reconciliation
```

An Activity Log `Succeeded` event is only evidence that an Azure Resource
Manager operation succeeded. Azure explicitly defines provisioning state as
control-plane metadata and notes that it is independent of resource
functionality ([Azure provisioning states][azure-provisioning-state]). Gludd
must therefore use the event as a prompt to reconcile, not as permission to
dispatch a model, browser, game, or test workload.

## Baseline evidence gap

At the recorded baseline, `tests/e2e/providers/test_azure_provision_e2e.py`
deploys, polls `/v1/models`, performs one inference, records one in-memory cost
history entry, calls destroy, and checks the locally reported spend ceiling. It
does not establish Event Grid delivery, validation, deduplication, cross-worker
leases, resource-specific scale-to-zero behavior, verified Azure absence, or
eventual invoice reconciliation.

The generic `/ingest/webhook` endpoint in
`src/general_ludd/receiver/router.py` accepts arbitrary JSON objects and arrays.
That is useful log ingestion, but it is not by itself an Azure lifecycle
receiver: Event Grid requires endpoint validation, HTTPS, and authenticated
delivery, while its events can be duplicated and arrive out of order
([endpoint validation][azure-endpoint-validation],
[delivery authentication][azure-event-authentication], and
[delivery and retry][azure-delivery-retry]).

These observations define missing evidence, not permission to weaken or skip a
live test.

## Long-lived operator reports

User reports are included because they reveal failure modes that nominal happy
paths miss. Primary documentation in the next sections remains authoritative.

| Report | User-visible symptom | Required Gludd consequence |
|---|---|---|
| [Gunicorn workers versus threads, opened 2015][forum-gunicorn-workers-threads] | Operators expected worker and thread settings to share state; maintainers clarified that workers are processes and threads only affect threaded workers. | Never keep event dedupe, leases, readiness, or cost state in Python globals. Prove cross-process behavior. |
| [Azure VM stuck deallocating, opened 2019][forum-vm-deallocating] | A VM remained in `Deallocating`; the accepted guidance allowed about 90 minutes for timeout before retry. | Treat deallocation and deletion as bounded asynchronous operations, preserve a cleanup lease, and verify final state rather than trusting the request. |
| [Gunicorn worker state, opened 2019][forum-gunicorn-state] | A user asked whether globals were safe; a maintainer explained that Gunicorn workers share nothing without an external store or message system. | Persist the inbox and work claims in Postgres or an equivalent shared durable store before returning success. |
| [Failed Azure VM missing from Terraform state, opened 2020][forum-terraform-orphan] | A failed VM existed in Azure but not in Terraform state, so a later apply collided with the orphan. | Cleanup must reconcile Terraform state with Azure-owned resource IDs and tags; `terraform destroy` alone is insufficient evidence. |
| [Event Grid publication delays, opened 2021][forum-event-delay] | Multiple publishers saw internal errors and events delayed by 30 to 60 minutes; request IDs were needed for investigation. | Capture `x-ms-request-id`, publish/delivery metrics, and timestamps; run an ARM reconciliation safety net when the stream is silent. |
| [Container Apps scale-from-zero cold start, opened 2022][forum-cold-start] | A small API took five to six seconds to wake, making scale from zero unsuitable for the reporter's latency target. | Measure cold and warm admission separately and retain a minimum replica or warm pool when the workload SLO cannot absorb cold start. |
| [Event Grid dead-letter and duplicate questions, opened 2022][forum-event-duplicates] | The accepted answer states that Event Grid has no duplicate detection and at-least-once delivery means duplicates should be expected. | Use a unique durable event identity and make every transition idempotent. Dead-letter replay must use the same path. |
| [Container Apps readiness regression, opened 2023][forum-revision-cold-start] | A new revision received traffic while still provisioning, briefly stalling a warm endpoint despite `minReplicas=1`. | Keep the old revision routable until the new revision passes readiness and prove uninterrupted admission during a ZDD rollout. |
| [Retail Prices results became empty and pagination changed, opened 2023][forum-retail-throttling] | A user observed empty responses, a page-size change from 100 to 1,000, and inconsistent continuation offsets from the public endpoint. | Bound and validate pagination, cache only fresh successful results, and fail preflight instead of silently reusing stale prices. |
| [Container Apps active-use cost rose 3x to 5x, opened 2023][forum-container-app-cost-spike] | Multiple operators reported replicas billed as active despite metrics below the documented threshold; some shut apps down while Microsoft investigated. | Treat public retail rates as a prediction input, never as billed truth; retain resource/meter identity for delayed Cost Management reconciliation and bias alarms. |
| [Retail API and pricing-page values disagreed, opened 2021][forum-retail-page-mismatch] | A user found the programmatic retail result differed from the Azure pricing page after currency conversion. | Pin USD, currency, effective date, region, primary-meter status, SKU, meter name, and price type; reject comparisons that mix currencies or contract discounts. |
| [Terraform-created Azure resources absent from state, opened 2025][forum-terraform-state-gap] | After a late apply failure, operational Azure resources did not appear in state and a rerun wanted to recreate them. | Persist the planned ownership manifest before apply and reconcile it after every partial failure before retrying or deleting. |
| [AzureRM GPU support request, opened 2024][forum-azurerm-gpu-support] | For about a year the provider rejected both serverless and dedicated Container Apps GPU profile types before the change reached AzureRM 4.55.0. | Pin and test the provider schema; a platform feature announcement is not evidence that the Terraform provider can express it. |
| [AzureRM GPU follow-up, reported 2025][forum-azurerm-gpu-counts] | After GPU enum support merged, users found that the provider still sent zero-valued environment `minimumCount` and `maximumCount` fields that the serverless GPU API rejected. | Keep environment profile capacity distinct from app replica bounds and use an omission-preserving ARM path until a live test proves the AzureRM encoder is fixed. |
| [Azure CLI GPU profile-name failure, opened 2025][forum-azure-cli-gpu-name] | A supported long GPU profile type was also used as the profile name and failed the name-length rule; the request body omitted `workloadProfileType`. | Use a short stable profile `name`, send the exact long string only as `workloadProfileType`, and inspect the emitted request in acceptance evidence. |
| [Stopped Azure VM disk charges, opened 2022][forum-vm-disk-cost] | The author deallocated a VM but continued receiving charges; the accepted Microsoft answer confirms the retained OS and data disks still consume billable storage. | Include disk and public-IP meters through verified deletion, not merely through compute deallocation, and reconcile unexpected retained-resource rows. |

Closed issues still matter as regression fixtures. They describe observable
contracts that can recur through provider, API, regional, or configuration
changes.

## Event ingestion and cross-worker ownership

Azure Activity Log Administrative records include both start and success or
failure records for write, delete, and action operations. Their `operationId`,
`correlationId`, `resourceId`, `status`, `eventTimestamp`, and
`submissionTimestamp` fields provide the control-plane join keys
([Activity Log schema][azure-activity-log-schema]). Terraform output should
also carry the run ID, stack ID, workspace, planned resource IDs, and exact
state serial. Neither source is sufficient alone after a partial apply.

The Azure receiver must implement all of the following:

1. Accept Event Grid schema or CloudEvents on a dedicated HTTPS endpoint and
   complete the correct validation handshake. Validate the Microsoft Entra
   token issuer, audience, signature, expiry, expected Event Grid identity,
   subscription, resource group, and ownership tags before accepting data.
2. Persist the original event and a normalized envelope in one transaction
   before returning a success response. A `202 Accepted` means only that the
   durable inbox owns the event; no expensive readiness probe runs in the HTTP
   request.
3. Uniquely identify an event by tenant, event-subscription resource ID, and
   Event Grid event ID. Preserve payload hashes so the same ID with different
   content fails closed. Duplicate delivery returns success without scheduling
   duplicate work.
4. Claim pending rows with one atomic database lease. A lease contains owner,
   fencing token, attempt, expiry, heartbeat, and run ID. Worker death permits
   takeover only after expiry; a stale worker cannot commit after takeover.
5. Order state transitions by resource version or authoritative observation,
   not arrival order. A late `Started` or `Deleting` event cannot regress a
   resource already reconciled as ready or absent.
6. Correlate Event Grid delivery with Terraform operation output and an
   authoritative ARM GET. Unknown or unowned resources are recorded and
   ignored, never adopted or deleted.
7. Poll Activity Log or ARM on a bounded cadence as a reconciliation safety
   net. The event path remains separately tested: fallback discovery cannot
   convert a missing-event assertion into a pass.

Event Grid attempts immediate at-least-once delivery but does not guarantee
order. Unhealthy endpoints receive exponential retries, later deliveries may
be delayed for hours, and dead-lettering is not enabled by default
([delivery and retry][azure-delivery-retry]). The live receiver must therefore
configure dead-letter storage, alert on dropped or dead-lettered events, and
expose matched, delivered, failed, dropped, dead-lettered, and latency metrics
([Event Grid metrics][azure-event-metrics]).

Gunicorn uses a pre-fork master with independent worker processes. Its own
documentation warns that too many workers can decrease throughput through
resource thrashing ([Gunicorn design][gunicorn-design]). Gludd must keep HTTP
workers stateless, keep GPU/model processes outside Gunicorn, and bound total
database connections. Worker count is tuned under load; it is not inferred from
the number of events or jobs.

## Readiness is resource-specific

Every resource follows two separate state machines:

```text
CONTROL: requested -> accepted -> ARM succeeded|failed
RUNTIME: unknown -> probing -> ready|degraded|failed -> draining -> absent
```

Only `RUNTIME=ready` admits work. Required gates include:

| Resource | Minimum readiness gate |
|---|---|
| Azure VM or VM scale-set instance | Instance View reports `ProvisioningState/succeeded` and `PowerState/running`; VM agent and required extensions are healthy; network path and workload endpoint pass an identity-bearing probe. The Instance View API is the runtime-state source ([VM Instance View][azure-vm-instance-view]). |
| Azure Container App | The exact immutable revision is active, provisioning succeeded, health is healthy, at least one ready replica exists, and the endpoint returns the expected Gludd/model artifact identity. Traffic remains on the old ready revision until these pass. |
| GPU worker | The exact requested SKU and GPU identity are present; driver, CUDA, engine, model, memory headroom, and health probes pass. ARM success or an allocated VM is not a GPU-readiness signal. |
| FPS/game worker | GPU readiness passes, the browser/game process starts, the menu is interactive, a deterministic control trace changes game state, and frame/video capture produces the expected artifact set. |
| Teardown | Admission is closed and leases drain, then the instance deallocates, owned resources delete, Terraform and ARM inventory converge, and the resource group or exact resource GET returns `404`. |

Stopped and deallocated are materially different: a stopped VM can still incur
compute charges, while a deallocated VM does not, although attached storage and
network resources can continue to cost money
([VM states and billing][azure-vm-states]). A deallocation marker is therefore
an immediate cost brake, not proof of complete cleanup.

## Elasticity and scale-to-zero

Azure Container Apps uses KEDA. Current documented defaults include a 30-second
event-source polling interval and a 300-second final-replica cooldown. An app
with ingress disabled, no minimum replica, and no custom scale rule can scale to
zero with no mechanism to start again
([Container Apps scaling][azure-container-scaling]).

Gludd's scaler must use queue and lease state rather than HTTP-worker load:

- classify work before allocation by CPU, memory, GPU family/count, VRAM,
  expected duration, startup cost, deadline, and interruptibility;
- reserve capacity once with a fenced lease and never allow two Gunicorn
  workers to allocate for the same backlog transition;
- scale out from pending runnable work plus measured service time, capped by
  quota, spend, provider concurrency, and GPU memory headroom;
- keep hysteresis between scale-out and scale-in, drain admissions before
  shutdown, and never terminate an active non-checkpointable workload;
- choose `minReplicas=0` only when a durable external trigger can wake the
  service and measured cold-start latency fits the task deadline;
- maintain a small warm slice for latency-sensitive interactive controls or
  game streaming, while batch rendering and test generation may scale to zero;
- use immutable parallel revisions and readiness-gated traffic shifts so
  scaling and deployment remain ZDD; and
- react to Spot eviction notices as best-effort hints, checkpoint when
  possible, and never treat a deallocated Spot VM as guaranteed reallocatable.

Spot price and capacity are variable, deallocated Spot VMs still retain
chargeable storage, and later allocation is not guaranteed
([Azure Spot VMs][azure-spot-vms]). The scaler must include eviction risk and
restart work in its decision rather than selecting Spot from hourly price alone.

## Cost-prediction evidence

Before provisioning, Gludd must resolve one unambiguous meter matching cloud,
region, exact ARM SKU, operating system, priority, price type, and effective
date. The Azure Retail Prices API returns public retail prices without
discounts, uses case-sensitive filters in current versions, and paginates at
1,000 records ([Retail Prices API][azure-retail-prices]). Ambiguous, stale,
incomplete, or Spot-only matches fail preflight.

The prediction record must expose, rather than hide, its components:

```text
provisioning + ready-wait + active work + idle/cooldown + teardown
  x exact compute meter
+ disks + snapshots + public IP + network egress + logs/storage
+ retry/eviction risk reserve
= predicted all-in cost and conservative ceiling
```

The immediate live test can prove meter selection and arithmetic. It cannot
prove final billed accuracy: Azure states that cost and usage data is usually
available after 8 to 24 hours for EA/MCA subscriptions and may take up to 72
hours for pay-as-you-go subscriptions
([Cost Management data latency][azure-cost-latency]). The live run must emit
`COST_RECONCILIATION_PENDING`, preserve resource IDs/tags and meter inputs, and
schedule a later actual-cost or amortized-cost report. A budget alert is not a
runtime kill switch.

For each homogeneous provider/region/SKU/workload cohort, delayed reconciliation
must calculate signed error, absolute percentage error, mean absolute
percentage error, p95 absolute percentage error, and bias. The acceptance
starting point is MAPE at or below 10%, p95 at or below 20%, and no systematic
underprediction after at least 20 reconciled runs. Until a cohort has enough
samples it reports `UNCALIBRATED`, applies a conservative ceiling, and cannot be
advertised as accurate. Arithmetic for metered compute time must match the exact
meter within 1%; all-in variance is calibrated separately.

### Implemented exact pre-deploy slice

`AzureContainerAppsRetailPricing` now queries the unauthenticated public API
with case-sensitive `armRegionName`, `skuName`, `meterName`, and `priceType`
filters, then revalidates service, USD currency, unit, primary-meter status,
and effective date client-side. The cache is keyed by all selector fields and
never returns an expired entry after a failed refresh. Duplicate meters at the
latest effective date, unsafe continuation hosts, malformed prices, unsupported
GPUs, and missing current meters all stop planning before allocation.

A credential-free live East US query on 2026-08-01 established an important
schema detail: Container Apps GPU records use `skuName=Standard` and an empty
`armSkuName`; the accelerator identity is the exact meter name. The current
Consumption meters were `Standard NC T4 v3 GPU Usage` at USD 0.000073 per
second, `Standard NC A100 v4 GPU Usage` at USD 0.000529 per second,
`Standard vCPU Active Usage` at USD 0.000024 per vCPU-second, and
`Standard Memory Active Usage` at USD 0.000003 per GiB-second. Gludd therefore
matches `skuName=Standard` plus the exact meter name instead of inventing an ARM
SKU from the workload-profile string. The opt-in, non-provisioning E2E contract
re-queries those identities with `AZURE_RETAIL_PRICES_LIVE=1`.

`DeployStrategist` uses the three exact meters for the full active resource
shape materialized by the Terraform GPU profile (T4: 8 vCPU/56 GiB; A100:
24 vCPU/220 GiB). Normal and background Container Apps plans are exact public
retail estimates. Immediate and queue-driven warmup plans no longer add a
static VM hourly rate. They resolve an exact Linux Consumption VM meter plus
the selected Standard SSD disk tier and the Global Standard static IPv4 meter,
then expose every component, meter ID, and elapsed phase through phased
execution.

The VM arithmetic bills compute across startup, workload handoff, and bounded
shutdown until deallocation. Disk and public-IP forecasts cover the same full
lifecycle through deletion. Disk sizes round up to Azure's offered E tier; for
example 100 GiB maps to E10/128 GiB. Azure documents both the size rounding and
hourly prorating of a monthly disk price ([managed disk billing][azure-disk-billing]).
The estimator uses the Pricing Calculator's documented 730-hour month
([pricing calculator][azure-pricing-calculator]). It does not fabricate I/O,
egress, snapshot, or logging quantities; those remain separately identified
ancillary observations during delayed reconciliation.

A credential-free live East US check on 2026-08-01 proved the API's inconsistent
but exact identities without provisioning resources. T4 uses ARM SKU
`Standard_NC8as_T4_v3`, Linux product `Virtual Machines NCasT4 v3 Series`, and
meters `NC8as T4 v3` / `NC8as T4 v3 Spot`. A100 uses ARM SKU
`Standard_NC24ads_A100_v4`, Linux product `NCads A100 v4 Series Linux`, SKU
`Standard_NC24ads_A100_v4`, and meter `NC24ads_A100_v4`. Standard SSD E10 is
`E10 LRS Disk` at `1/Month`; the static Standard IPv4 meter is in the
case-sensitive `Global` pricing region with `skuName=Standard`. Gludd queries
broadly only where Azure omits or varies ARM identities, revalidates every
service/product/SKU/meter/unit/currency/primary/effective-date field client-side,
and fails closed with bounded observed identities when no unique match exists.

The stateful tier controller bases scale-up on queued and active work, runtime,
latency budget, urgency, and Spot eligibility. Lower scale-down thresholds keep
the current tier through moderate demand, preventing oscillation. Every choice
emits `scale_up`, `hold`, or `scale_down` plus its demand score and reason. A
completed estimate can materialize the immutable `AzureCostPrediction` used by
durable reconciliation, including all compute/disk/IP resource and meter IDs.

Contract-specific discounts, free-grant balances, transaction/egress/log
forecasts, and real 20-run calibrated cohort evidence remain open under AZL.2;
the public Retail Prices API is not evidence of final invoiced cost. Azure's
pricing page likewise states that serverless GPU charges are additive to active
vCPU and RAM charges ([Container Apps pricing][azure-container-apps-pricing]),
while the GPU overview confirms per-second billing and scale to zero
([serverless GPU overview][azure-serverless-gpu]).

The API choice, delayed-data states, resource identity, ancillary ledger, and
cohort acceptance formulas are specified in the
[Azure billed-cost reconciliation design](AZURE_BILLED_COST_RECONCILIATION_DESIGN.md).

## Live E2E acceptance matrix

The Azure environment pointer may be supplied to the existing make target, but
the path and its secret values must never appear in logs or evidence. The
chargeable target remains explicit and cost-gated.

`make test-e2e-azure-provision` and its game/GPU companion tests must eventually
prove every row below on live Azure. A skipped row is not a pass for the
corresponding feature claim.

| Slice | Required live evidence |
|---|---|
| Event subscription | Dedicated authenticated endpoint completes validation; the exact subscription reaches `Succeeded`; dead-lettering and diagnostic metrics are enabled. |
| Terraform correlation | One tagged resource is applied; start and terminal operations stream with run, operation, correlation, and resource IDs; an ARM GET agrees with the terminal observation. |
| Event latency | With a healthy receiver, record publish, submit, ingest, durable-commit, claim, and reconcile times. The event-path assertion has an explicit bounded deadline; ARM fallback is reported separately and cannot satisfy it. |
| Duplicate and order | Replay the same event and deliver a terminal event before its start event; exactly one readiness workflow and one workload lease result. |
| Gunicorn multitasking | Run at least two workers against shared Postgres, kill one after claim, and prove fenced takeover. Concurrent unrelated events continue, no global state diverges, and no duplicate compute is created. |
| Retry behavior | Return a retryable failure once, then recover. Prove durable eventual receipt, one side effect, request-ID capture, and zero dead-letter loss. |
| Readiness | ARM success precedes runtime readiness. Work starts only after exact VM/container/GPU/service identity gates pass, with both timestamps preserved. |
| Scale from zero | Queue work at zero replicas, measure detection, allocation, boot, readiness, and first-work latency, then prove safe drain and return to zero. |
| ZDD scale/rollout | Keep a warm revision serving while a new revision provisions; no accepted request is lost or routed to an unready revision. |
| Partial Terraform failure | Force a resource-level apply failure after at least one Azure object exists; reconcile state and ARM inventory, then remove every owned orphan without touching adjacent resources. |
| Teardown | Close admission, drain or checkpoint work, deallocate, delete, and poll exact IDs to absence. Controller death leaves a durable cleanup lease that another process completes. |
| Cost | Persist exact meter and prediction before allocation, enforce the ceiling live, and emit pending reconciliation. A delayed job later attaches billed cost and updates cohort error metrics. |
| Event observability | Every phase emits one flushed structured event immediately. A failure consumer can begin diagnosis while cleanup and remaining independent tests continue. |

### Fail-closed E2E cleanup command

`make azure-cleanup-e2e` is the operator safety net for an interrupted live
test. It sources the same `AZURE_E2E_ENV_FILE` as the provision harness without
printing credential values, selects only resource groups prefixed
`gludd-gpu`, submits asynchronous deletions, and visibly polls the authoritative
Azure group inventory. `CLEANUP_VERIFIED leaked_resources=0` is the sole success
marker; query failures, delete failures, and the explicit timeout all fail
nonzero.

This directly guards the failure shape in the long-lived VM deallocation and
Terraform-orphan reports above. It is intentionally narrower than the full
teardown acceptance row: an empty resource-group query proves those test groups
are absent, but does not prove that unrelated Terraform state, billing records,
or resources outside the prefix are reconciled.

### Real GPU artifact boundary

The runtime generator must never substitute a CPU echo server for a GPU or
model-readiness assertion. The Azure Container Apps path now materializes a
deploy-local root module, exact `terraform.tfvars`, and a provider module that
creates a Consumption GPU workload profile, runs the requested vLLM image and
model, applies explicit ingress CIDRs, and bounds the app at zero-to-one
replicas. T4 maps to `Consumption-GPU-NC8as-T4`; A100 maps to
`Consumption-GPU-NC24-A100`. Unsupported GPU families, non-vLLM engines, and
multi-GPU-per-replica requests fail before Terraform can create billable
resources.

This artifact has a non-skipping Terraform `init` plus `validate` test against
the current AzureRM provider schema. That test proves provider syntax and local
module completeness, not quota, image startup, GPU identity, inference, or
teardown. Those remain live-provision acceptance requirements and cannot be
closed by the validate-only result.

### AzureRM provider and serverless-GPU payload contract

The provider boundary is now versioned explicitly. AzureRM 3.117.1 recognizes
only `Consumption`, D-series, and E-series workload-profile types in its
Container Apps helper ([3.117.1 helper source][azurerm-3117-helper]). The GPU
types first entered the provider through PR 30738, merged in November 2025 and
released as AzureRM 4.55.0 on December 4, 2025
([provider change][azurerm-gpu-pr] and [4.x changelog][azurerm-v4-changelog]).
The current resource documentation includes both
`Consumption-GPU-NC8as-T4` and `Consumption-GPU-NC24-A100`
([AzureRM resource schema][azurerm-container-app-environment]). Therefore a
3.117.1 validation failure is expected; it is not a regional quota failure.

Gludd's supported AzureRM constraint is `>= 4.55.0, < 5.0.0`, with the lock
file pinning the exact 4.x binary and checksums proven by the live E2E run. The
initial verified lock candidate is 4.81.0. Version 5 is a separate major-version
upgrade and must not enter through an unconstrained init. Both the canonical
provider file and every generated root module must use the same constraint, and
`make tf-versions-check` must reject drift.

Enum acceptance does not yet make the AzureRM environment payload safe. In
4.81.0, `ExpandWorkloadProfiles` still attaches `MaximumCount` and
`MinimumCount` to every profile whose *name* is not exactly `Consumption`
([4.81.0 helper source][azurerm-481-helper]). That includes a correctly named
serverless profile such as `gludd-gpu`. The long-running provider discussion
records Azure rejecting those fields for Consumption GPU profiles after the
enum fix ([GPU follow-up][forum-azurerm-gpu-counts]). Gludd's module currently
sets environment counts to zero and one, so schema validation alone is a false
positive.

Until an upstream encoder change is both released and live-proven, the
environment is owned by one `azapi_resource` using
`Microsoft.App/managedEnvironments@2025-07-01`, with a short profile `name`, an
exact GPU `workloadProfileType`, and both environment `minimumCount` and
`maximumCount` omitted ([managed-environment ARM schema][arm-managed-environment]).
The temporary AzAPI constraint is
`>= 2.11.0, < 3.0.0`, locked initially to 2.11.0. AzAPI is the official
full-lifecycle escape hatch for ARM features that AzureRM cannot safely encode
([AzAPI resource][azapi-resource]); AzureRM must not also own the same managed
environment. App-level `min_replicas=0` and `max_replicas=1` remain valid and
are deliberately separate from environment node counts.

AzAPI can be removed only when all of these are true in one change: the pinned
AzureRM source omits both count fields for Consumption GPU profiles; a request
payload regression test proves omission; `terraform plan` has no ownership or
replacement drift during the state migration; and a live T4 plus A100-capable
regional test creates the environment, reaches runtime readiness, scales the
app to zero, and destroys it. The official workload-profile table confirms the
two exact profile types and per-replica serverless allocation
([workload profiles][azure-workload-profiles]); regional availability and quota
remain independent preflight gates ([serverless GPU overview][azure-serverless-gpu]).

For FPS claims, every declared game fixture must exercise a deterministic menu
path, key/mouse/controller trace, gameplay-state transition, and captured Azure
GPU video. Comparisons use version-pinned, license-compatible reference clips
with provenance and content hashes; tests must not scrape mutable online video
at runtime. Menu, control response, HUD/gameplay frames, timing, and video
decode/render integrity are separate assertions. One representative game cannot
stand in for all FPS implementations.

## Streaming failures before suite completion

Live tests must write newline-delimited events to a run-scoped stream and flush
each record. Every record contains at least:

```text
schema_version, run_id, test_id, sequence, source, event_id,
resource_id_hash, phase, status, observed_at_utc, monotonic_ns,
latency_ms, worker_id, attempt, evidence_sha256, error_code
```

Phase events are monotonic and include `TERRAFORM_STARTED`, `ARM_SUCCEEDED`,
`EVENT_DURABLE`, `READINESS_STARTED`, `RESOURCE_READY`, `WORK_STARTED`,
`WORK_FAILED|WORK_PASSED`, `ADMISSION_CLOSED`, `DEALLOCATED`, `DELETED`, and
`COST_RECONCILIATION_PENDING|RECONCILED` as applicable.

A supervisor tails the stream, groups failures by root cause, and publishes a
bounded diagnostic bundle as soon as a terminal phase fails. Cleanup continues
independently and keeps emitting progress. Independent tests may continue when
their resource and spend leases allow it; dependent tests are cancelled early
instead of waiting for the whole suite. The first error is never hidden by a
later cleanup error, and an incomplete cleanup always changes the overall run
to failure.

## Failure injections that remain mandatory

- duplicated and out-of-order Event Grid deliveries;
- a valid event ID reused with a different payload;
- webhook timeout, `503`, retry, and dead-letter replay;
- Event Grid published count increasing while matched/delivered counts stall;
- Gunicorn worker death before and after durable claim commit;
- stale lease owner attempting a late state transition;
- Terraform partial apply with an Azure object missing from state;
- ARM success while network, extension, model, or game readiness fails;
- GPU capacity/quota failure and Spot eviction without duplicate admission;
- Container App cold start and a new revision that is not yet healthy;
- deallocation delay, delete delay, controller death, and cleanup retry; and
- price pagination, ambiguous meter, price change, billing-data lag, and
  prediction underestimation.

## Authoritative sources

- [Azure Activity Log event schema][azure-activity-log-schema]
- [Azure Event Grid endpoint validation][azure-endpoint-validation]
- [Azure Event Grid delivery authentication][azure-event-authentication]
- [Azure Event Grid delivery, retry, duplicate, order, and dead-letter rules][azure-delivery-retry]
- [Azure Event Grid delivery metrics and diagnostics][azure-event-metrics]
- [Azure VM Instance View][azure-vm-instance-view]
- [Azure VM states and billing][azure-vm-states]
- [Azure Container Apps scaling behavior][azure-container-scaling]
- [Azure Spot VM behavior, pricing, and eviction history][azure-spot-vms]
- [Azure Retail Prices API][azure-retail-prices]
- [Azure managed-disk billing][azure-disk-billing]
- [Azure Pricing Calculator estimation rules][azure-pricing-calculator]
- [Azure Container Apps pricing][azure-container-apps-pricing]
- [Azure Container Apps serverless GPU overview][azure-serverless-gpu]
- [Azure Container Apps workload-profile types][azure-workload-profiles]
- [Azure Cost Management data latency][azure-cost-latency]
- [AzureRM Container App Environment schema][azurerm-container-app-environment]
- [AzureRM GPU workload-profile change and release history][azurerm-gpu-pr]
- [Managed Environment stable ARM schema][arm-managed-environment]
- [AzAPI full-lifecycle resource contract][azapi-resource]
- [Gunicorn pre-fork worker design][gunicorn-design]
- [Gunicorn graceful timeout and worker settings][gunicorn-settings]

[azure-activity-log-schema]: https://learn.microsoft.com/en-us/azure/azure-monitor/platform/activity-log-schema
[azure-container-apps-pricing]: https://azure.microsoft.com/en-us/pricing/details/container-apps/
[azure-container-scaling]: https://learn.microsoft.com/en-us/azure/container-apps/scale-app
[azure-cost-latency]: https://learn.microsoft.com/en-ca/azure/cost-management-billing/costs/understand-cost-mgt-data#cost-and-usage-data-updates-and-retention
[azure-delivery-retry]: https://learn.microsoft.com/en-us/azure/event-grid/delivery-and-retry
[azure-disk-billing]: https://learn.microsoft.com/en-us/azure/virtual-machines/disks-types#billing
[azure-endpoint-validation]: https://learn.microsoft.com/en-us/azure/event-grid/end-point-validation-event-grid-events-schema
[azure-event-authentication]: https://learn.microsoft.com/en-us/azure/event-grid/security-authentication
[azure-event-metrics]: https://learn.microsoft.com/en-us/azure/event-grid/monitor-push-reference
[azure-provisioning-state]: https://learn.microsoft.com/en-us/azure/networking/troubleshoot-failed-state#provisioning-states
[azure-pricing-calculator]: https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/pricing-calculator
[azure-retail-prices]: https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices
[azure-serverless-gpu]: https://learn.microsoft.com/en-us/azure/container-apps/gpu-serverless-overview
[azure-workload-profiles]: https://learn.microsoft.com/en-us/azure/container-apps/workload-profiles-overview
[azure-spot-vms]: https://learn.microsoft.com/en-us/azure/virtual-machines/spot-vms
[azure-vm-instance-view]: https://learn.microsoft.com/en-us/rest/api/compute/virtual-machines/instance-view
[azure-vm-states]: https://learn.microsoft.com/en-us/azure/virtual-machines/states-billing
[forum-cold-start]: https://github.com/microsoft/azure-container-apps/issues/199
[forum-container-app-cost-spike]: https://github.com/microsoft/azure-container-apps/issues/799
[forum-event-delay]: https://learn.microsoft.com/en-us/answers/questions/436552/azure-eventgrid-throws-internal-server-error-to-pu
[forum-event-duplicates]: https://learn.microsoft.com/en-us/answers/questions/1121532/how-to-check-the-reason-why-event-was-dead-lettere
[forum-gunicorn-state]: https://github.com/benoitc/gunicorn/issues/2082
[forum-gunicorn-workers-threads]: https://github.com/benoitc/gunicorn/issues/1045
[forum-revision-cold-start]: https://github.com/microsoft/azure-container-apps/issues/598
[forum-retail-page-mismatch]: https://learn.microsoft.com/en-us/answers/questions/405787/azure-retail-pricing-api-is-not-consistent-with-az
[forum-retail-throttling]: https://learn.microsoft.com/en-us/answers/questions/1353965/undesirable-throttled-results-from-azure-retail-pr
[forum-terraform-orphan]: https://github.com/hashicorp/terraform-provider-azurerm/issues/7236
[forum-terraform-state-gap]: https://discuss.hashicorp.com/t/terraform-resources-not-tracked-after-failure-even-when-successfully-deployed/73976
[forum-vm-deallocating]: https://learn.microsoft.com/en-us/answers/questions/261/trending-on-msdn-virtual-machine-stuck-in-dealloca
[forum-vm-disk-cost]: https://learn.microsoft.com/en-us/answers/questions/885339/azure-stopped-vm-disk-cost
[forum-azure-cli-gpu-name]: https://github.com/Azure/azure-cli/issues/31239
[forum-azurerm-gpu-counts]: https://github.com/hashicorp/terraform-provider-azurerm/pull/30738
[forum-azurerm-gpu-support]: https://github.com/hashicorp/terraform-provider-azurerm/issues/28117
[gludd-accelerator-proof]: ../design/AZURE_ACCELERATOR_LIVE_PROOF.md
[gludd-postgres-workers]: ../POSTGRES_MULTI_WORKER.md
[gunicorn-design]: https://docs.gunicorn.org/en/stable/design.html
[gunicorn-settings]: https://docs.gunicorn.org/en/stable/settings.html
[azapi-resource]: https://registry.terraform.io/providers/Azure/azapi/latest/docs/resources/resource
[azurerm-3117-helper]: https://github.com/hashicorp/terraform-provider-azurerm/blob/v3.117.1/internal/services/containerapps/helpers/container_app_environment.go
[azurerm-481-helper]: https://github.com/hashicorp/terraform-provider-azurerm/blob/v4.81.0/internal/services/containerapps/helpers/container_app_environment.go
[azurerm-container-app-environment]: https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/container_app_environment
[azurerm-gpu-pr]: https://github.com/hashicorp/terraform-provider-azurerm/pull/30738
[azurerm-v4-changelog]: https://github.com/hashicorp/terraform-provider-azurerm/blob/main/CHANGELOG-v4.md#4550-december-04-2025
[arm-managed-environment]: https://learn.microsoft.com/en-us/azure/templates/microsoft.app/2025-07-01/managedenvironments
