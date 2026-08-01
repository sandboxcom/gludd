# Azure billed-cost reconciliation design

Status: implementation design plus durable ledger foundation for AZL.2
(2026-08-01). This document defines the delayed half of Azure cost prediction.
It does not claim that Azure export ingestion or final calibrated accuracy is
implemented.

## Decision

Use Microsoft's supported `azure-mgmt-costmanagement` client with
`DefaultAzureCredential` for bounded Cost Management Query requests, and use a
scheduled Cost Management Export as the durable source of truth and backfill
path:

- `CostManagementClient.query.usage()` at API version `2025-03-01` is the
  low-volume probe for a subscription or resource-group scope. Queries use a
  custom UTC interval, paginate every `nextLink`, and group only on supported
  dimensions. A `204`, an empty page, or no matching row means **not available
  yet**, never zero cost ([Query Usage REST API][query-api],
  [Python SDK][python-sdk]).
- One daily, partitioned `ActualCost` export at API version `2025-03-01` is the
  canonical ingest for final invoice-basis reconciliation. Store its selected
  schema version with every observation. Overwrite mode is intentional because
  Azure republishes a more complete month-to-date file; ingestion is by blob
  ETag/export run, not by path alone ([Exports API][exports-api],
  [Exports tutorial][exports-tutorial]).
- A parallel FOCUS export may supply normalized allocation fields, and an
  amortized export may measure reservation or savings-plan economics. Neither
  may be silently substituted for `ActualCost`: each is a separate cost basis.
- The asynchronous Cost Details API is a forensic/backfill fallback for
  supported EA and MCA scopes. It is not the hot path. The deprecated
  Consumption Usage Details endpoint and a custom HTTP client are rejected;
  supported SDK/API pagination, retry headers, authentication, and schema
  versions are less error-prone ([automation guidance][automation]).

The Query API should be called no more than once per day per consolidated
scope. Azure refreshes received data during the day, but Microsoft explicitly
recommends daily rather than busy polling and exposes QPU retry headers. A
single scheduled worker batches due reconciliation IDs into the smallest
number of scope/time-window queries.

Runtime authentication is a managed identity through `DefaultAzureCredential`,
with Cost Management read access at the selected scope and Storage Blob Data
Reader on the export container. A separate deployment identity creates or
changes the export and its storage role assignment; the runtime does not need
those write privileges. Client secrets, access tokens, signed download URLs,
and raw billing exports never enter Gludd events or test logs.

## Cost bases and the prediction envelope

Every prediction is immutable and has a `prediction_version`. It records three
views so rate discounts are not mistaken for usage accuracy:

1. **Quantity:** predicted versus billed quantity for each exact meter and
   unit. This isolates resource shape and lifecycle timing.
2. **Retail-normalized:** billed quantity multiplied by the exact public USD
   Retail Prices snapshot saved before allocation. This validates Gludd's
   public-price arithmetic.
3. **Invoice basis:** the prediction made with an eligible, already calibrated
   effective rate versus `ActualCost`. This includes negotiated rates but
   excludes a discount model until that model has enough final samples.

The envelope must contain:

- reconciliation, work-item, Terraform run, deployment, and prediction IDs;
- tenant, billing scope, subscription, resource group, region, currency, and
  pricing model;
- lowercase canonical ARM IDs for the resource group, managed environment,
  Container App, workspace, storage, registry, network resources, temporary
  VMs, disks, and every other deployment-owned resource;
- Terraform addresses and the stable tags `gludd-reconciliation-id`,
  `gludd-work-item-id`, and `gludd-deployment-id`;
- allocation start, provider-accepted, resource-ready, first-work,
  last-work, drain, scale-zero/deallocate, and delete-confirmed UTC times;
- for every predicted component: Retail Prices `meterId`, `productId`,
  `skuId`, service, SKU, meter name, ARM SKU, price type, effective date,
  currency, unit, unit price, quantity, retrieval time, and source URL; and
- predicted subtotal, risk reserve, conservative ceiling, exclusions, and the
  estimator/cohort versions used.

Full ARM resource ID is the primary join key and exact `meterId` is the meter
key. Resource names are never keys. Tags are secondary evidence because they
can be absent, changed, or delayed. Charges without a resource ID are joined
only through an explicit deployment-owned resource group/tag or an allocation
rule; ambiguous rows are quarantined instead of guessed.

Each actual-cost observation preserves its source scope and API version,
query interval, response columns, page count and continuation evidence, export
name/run ID, blob path/ETag, dataset/schema version, ingestion time, and data
watermark. Preserve at least resource ID, meter ID, service/product/meter,
charge type, pricing model, publisher type, quantity, unit, effective price,
cost in USD, billing-currency cost, usage date, and tags when Azure supplies
them. Multiple records for one resource/day are summed only after retaining the
original row identities; Azure documents that such rows are expected
([automation guidance][automation]).

## Ancillary meter ledger

The estimate and reconciliation use a ledger, not a compute-only scalar. Each
line is `direct`, `shared-allocated`, `excluded`, or `unresolved` with a reason.
The expected categories are:

- Container Apps GPU seconds plus additive active vCPU and GiB-seconds;
- requests, idle/cooldown time, minimum replicas, dedicated workload-profile
  management charges, and revision overlap where applicable;
- temporary VM/Spot compute, OS/data disks, snapshots, images, and retained
  storage during deallocation or eviction recovery;
- Log Analytics ingestion/retention, diagnostic settings, and application
  telemetry;
- registry storage/build/network transfer and model/game artifact storage;
- public IP, load balancer/NAT/private endpoint, DNS, intra-region or
  inter-region transfer, and internet egress;
- Key Vault and other per-operation platform services when they produce a
  charge; and
- failed attempts, retries, duplicate allocation windows, eviction recovery,
  and teardown lag.

Taxes, support, invoice rounding, credits, refunds, reservation purchases, and
savings-plan purchases do not belong to a work item's direct operational
forecast. They remain visible as excluded invoice lines. Amortized benefit and
unused commitment are reported separately. A shared line is allocated by
measured usage first, then a versioned declared rule; Gludd never assigns an
entire shared charge to the most convenient run.

## Delayed-data state machine

Reconciliation transitions are durable, monotonic except for an explicit
adjustment, and observable immediately:

| State | Entry and exit rule |
|---|---|
| `PREDICTED` | Immutable prediction persisted before provisioning. |
| `USAGE_PENDING` | Teardown/delete evidence exists; `not_before` is set. This emits `COST_RECONCILIATION_PENDING`. |
| `QUERY_DUE` | The account-specific delay passed and the batched daily query/export ingest is due. |
| `NO_DATA_RETRY` | API returned `204`, empty data, or no candidate rows. Preserve the observation and retry the next daily cycle; cost is not zero. |
| `PARTIAL` | Some rows match, but an expected category, resource, export partition, or watermark is incomplete. Publish the matched/unresolved totals and retry. |
| `PROVISIONAL` | All required direct categories are resolved, but the usage window or billing period can still be revised. Emit `COST_RECONCILIATION_PROVISIONAL`. |
| `STABLE` | Two complete snapshots at least 24 hours apart have identical row fingerprints and totals, and teardown is at least 72 hours old. |
| `FINAL` | `STABLE`, the billing period has closed, 72 additional hours passed, and the latest prior-month export agrees. Emit `COST_RECONCILIATION_FINAL` and update cohort metrics. |
| `ADJUSTED` | A later Azure correction differs from `FINAL`. Append the correction, retain the superseded final, recompute metrics, and emit `COST_RECONCILIATION_ADJUSTED`. |
| `NEEDS_REVIEW` | Identity is ambiguous, currency/basis differs, expected data remains absent after the finality deadline, or allocation cannot be justified. |
| `RETRYABLE_ERROR` | Throttle, timeout, Azure `5xx`, export failure, or transient storage failure. Honor `Retry-After`, add jitter, and return to the daily schedule. |
| `AUTH_BLOCKED` | Authentication, RBAC, invalid scope, or unsupported-account error. Alert once per fingerprint and require configuration repair; never retry tightly. |

For EA/MCA, the earliest query is eight hours after delete confirmation; for
pay-as-you-go it is 24 hours. Subsequent attempts are at most daily. The final
deadline is the later of teardown plus 72 hours or billing-period close plus 72
hours. These are lower bounds, not completeness claims: Azure says EA/MCA data
usually takes 8--24 hours, pay-as-you-go can take 72 hours, and services report
at different times ([data timing][data-timing]). Daily exports can take up to
24 hours, have load-dependent run times, and deliberately republish prior-month
data during the first five days to capture latent charges
([Exports tutorial][exports-tutorial]).

An append-only observation table is unique on source, reconciliation ID,
snapshot/version, and row identity. A short database lease with fencing guards
claims; state mutation and its outbox event commit in one transaction. Any
Gunicorn worker may ingest or reconcile, a dead worker's lease is recoverable,
and a stale worker cannot publish a second final. This preserves ZDD and lets
live tests diagnose partial cost results while unrelated tests continue.

### Durable foundation implemented

Migration `038` and `AzureCostReconciliationRepository` implement the smallest
database-backed portion of this contract without querying Azure:

- `azure_cost_predictions` stores a canonical, fingerprinted prediction
  envelope keyed by prediction ID and version. Repeating the same write is
  idempotent; changing that identity under the same key fails closed.
- due claims use PostgreSQL row locks with `SKIP LOCKED`, a lease expiry, and a
  monotonically increasing fencing token. SQLite exercises deterministic
  repository semantics but is not used as evidence for multiworker safety.
- `azure_cost_observations` retains arbitrary immutable source rows by
  snapshot and row identity. Its payload is intentionally resource- and
  meter-agnostic: VM compute, OS/data disks, public IPs, Log Analytics, network,
  registry, storage, corrections, and future ancillary meters do not require a
  schema rewrite or a compute-only total.
- finality rank cannot move backward; `FINAL` requires `STABLE`, and only an
  explicit `ADJUSTED` transition may supersede it. Every state change inserts
  a deduplicated outbox event in the caller's same database transaction.
- the live PostgreSQL multiworker acceptance races separate processes for one
  due prediction, then proves expired-owner takeover and stale-token rejection.

This is persistence and concurrency infrastructure, not a final cost claim.
Export parsing, completeness checks, billing-period closure, cohort updates,
and outbox delivery remain separate rollout slices.

## Cohort math and acceptance

A cohort is homogeneous on provider, region, exact resource shape/SKU, workload
profile, purchase/priority model, currency and cost basis, ancillary allocation
version, prediction version, and workload class. T4 and A100, public retail and
contract-effective rates, and materially different warm-pool policies never
share a cohort.

For final run `i`, let `P_i` be predicted cost and `A_i` be reconciled actual
cost on the same basis, with `A_i > 0`:

```text
signed_error_i = A_i - P_i                 # positive means underprediction
APE_i          = abs(A_i - P_i) / A_i
MAPE           = mean(APE_i)
p95_APE        = nearest_rank_95_percentile(APE_i)
WAPE           = sum(abs(A_i - P_i)) / sum(A_i)
bias           = sum(A_i - P_i) / sum(A_i) # positive means underprediction
match_coverage = matched_candidate_cost / all_candidate_cost
```

Candidate cost is every actual-cost row in the deployment's exact subscription,
resource-group, and UTC ownership window that matches an owned ARM ID, stable
deployment tag, or declared shared-cost rule. The denominator includes
unresolved candidates; ambiguity can therefore lower coverage but can never be
hidden by dropping rows.

Zero-actual runs are excluded only from APE/MAPE and remain in absolute error,
WAPE inputs, ceiling coverage, and review. Negative actual totals, mixed
currency, unresolved allocation, or corrections are not silently clipped.

A cohort remains `UNCALIBRATED` until it has at least 20 `FINAL` independent
runs, 100% run-identity coverage, at least 99% billed-dollar match coverage,
and all required meter categories resolved. It is `ACCURATE` only when all of
these gates pass on a fixed evaluation window:

- MAPE is at most 10%, p95 APE is at most 20%, and WAPE is at most 10%;
- absolute aggregate bias is at most 5%;
- the one-sided 95% bootstrap upper confidence bound for positive
  (underprediction) bias is at most 5%; and
- the conservative ceiling covers at least 95% of runs, with no run exceeding
  its ceiling by more than 10% without a `NEEDS_REVIEW` root cause.

Use deterministic bootstrap seeds and publish sample count, window boundaries,
all formulas, exclusions, and confidence bounds. Failure of any gate changes
the cohort to `DEGRADED`, increases its reserve, and prohibits an “accurate”
claim. Meter-quantity error and retail-normalized error are published beside
invoice-basis error so contract-rate movement cannot hide lifecycle bugs.

## Test and rollout contract

Implementation is not complete until tests cover:

- SDK fixtures for `204`, empty, partial, paginated, duplicate, corrected,
  multi-record/day, mismatched-currency, throttled, and auth-blocked responses;
- export overwrite/backfill, ETag idempotence, schema pinning, missing
  partitions, and an adjustment after finalization;
- exact ARM/meter matching, ambiguous tags, shared allocation, all ancillary
  categories, zero and negative actuals, and the cohort formulas above;
- two Gunicorn workers racing for one due record, worker death after claim, a
  fenced takeover, exactly one final event, and continued unrelated work; and
- a bounded live Azure run that persists prediction/resource IDs immediately,
  exits as `PENDING` without waiting days, then a scheduled delayed test consumes
  the real export/query data and publishes provisional/final evidence.

The live test must surface each state event as it commits. Waiting for final
billing inside `make test-e2e-azure-provision` would be both slow and false: the
provision test passes when it proves durable pending work and teardown, while a
separate delayed reconciliation job owns final accuracy evidence.

## Operator reports that shape the design

These long-lived reports are not treated as specifications. They are regression
evidence for failure modes that official happy-path examples do not emphasize.

| Report | Observed failure | Design consequence |
|---|---|---|
| [New-subscription Query delay, opened 2021][forum-new-subscription] | Subscription-scoped usage returned “not supported” for days and later resolved as propagation completed. | `AUTH_BLOCKED` and `NO_DATA_RETRY` are distinct; billing-scope fallback is explicit and an empty young subscription is never zero. |
| [Historical month disappeared, opened 2022][forum-missing-month] | A previously available month vanished without an API error and reappeared after a weekend. | Snapshots are append-only, missing data cannot overwrite a final, and later corrections use `ADJUSTED`. |
| [Query pagination missing from SDK specification, opened 2023][forum-pagination] | The service returned `nextLink`, but generated SDKs exposed only the first 1,000 rows. | Every selected SDK/API version gets a live pagination contract; page count and continuation evidence are persisted. |
| [Export failed with opaque diagnostics, opened 2021][forum-export-failure] | A working scheduled export began failing while run history exposed no useful cause. | Export health is its own observable state; Query provides a bounded fallback and opaque failures retain request/run IDs for escalation. |
| [Invoice ID arrived days after monthly export, opened 2023][forum-invoice-delay] | Invoice metadata was absent from the scheduled file and appeared on a later manual run. | Billing-period close is not immediate finality; wait 72 hours and compare the republished prior-month export. |
| [Stopped Spot VM still bills attached resources, opened 2023][forum-spot-deallocated-cost] | Operators confirmed that stopped/deallocated or evicted Spot VMs stop compute billing while OS/data disks and allocated public IPs can continue billing. | Warmup and eviction reconciliation retain VM, disk, and IP rows independently; deallocation never closes the ownership window for ancillary meters, and totals are never compute-only. |

## Sources

- [Cost Management Query Usage REST API, version 2025-03-01][query-api]
- [`azure-mgmt-costmanagement` Python SDK overview][python-sdk]
- [Cost Management Exports REST API, version 2025-03-01][exports-api]
- [Create and manage Cost Management exports][exports-tutorial]
- [Manage Azure costs with automation][automation]
- [Cost and usage data updates and retention][data-timing]

[automation]: https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/manage-automation
[data-timing]: https://learn.microsoft.com/en-ca/azure/cost-management-billing/costs/understand-cost-mgt-data#cost-and-usage-data-updates-and-retention
[exports-api]: https://learn.microsoft.com/en-us/rest/api/cost-management/exports/get?view=rest-cost-management-2025-03-01
[exports-tutorial]: https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/tutorial-improved-exports
[forum-export-failure]: https://learn.microsoft.com/en-us/answers/questions/556654/azure-cost-management-export-failing-to-run
[forum-invoice-delay]: https://learn.microsoft.com/en-us/answers/questions/1286641/invoice-id-in-monthly-cost-export-data
[forum-missing-month]: https://learn.microsoft.com/en-us/answers/questions/712665/azurecostmanagement-api-missing-data
[forum-new-subscription]: https://learn.microsoft.com/en-us/answers/questions/306268/the-azure-cost-management-api-usage-query-error-no
[forum-pagination]: https://github.com/Azure/azure-rest-api-specs/issues/23405
[forum-spot-deallocated-cost]: https://learn.microsoft.com/en-us/answers/questions/1358675/how-is-the-spot-price-calculated
[python-sdk]: https://learn.microsoft.com/en-us/python/api/overview/azure/mgmt-costmanagement-readme?view=azure-python
[query-api]: https://learn.microsoft.com/en-us/rest/api/cost-management/query/usage?view=rest-cost-management-2025-03-01
