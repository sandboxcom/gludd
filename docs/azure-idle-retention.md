# Azure model-runner idle retention

Gludd separates a model deployment into independently priced layers instead of
treating the whole stack as either warm or destroyed. The default
`zero_cost_only` posture destroys the Container App and its GPU replicas after
work, but retains an eligible Consumption managed environment for a bounded
reuse window. A live Gludd run measured the environment phase at 964 seconds
when cold and 18 seconds when the existing environment could be reconciled.

This is a cost-and-latency policy, not a guess. Unknown or stale price or
provisioning-latency evidence makes a layer ineligible for retention. A
dependency can be retained only when every layer it depends on is retained.
Gludd refuses to describe a system as idle while a durable runnable todo exists,
and it never retains idle compute replicas in that state.

## Strategies

| Preset | Behavior | Intended use |
| --- | --- | --- |
| `always_destroy` | Retain nothing considered by the planner. | Existence minimization and one-off work. |
| `zero_cost_only` | Retain only layers with current, explicit zero-idle-cost evidence. | Safe default for repeated experiments. |
| `balanced` | Maximize measured p95 provisioning time saved while enforcing hourly, monthly, retention-horizon, and cost-per-saved-hour ceilings. | Regular bursts with a small explicit idle budget. |
| `latency_first` | Maximize measured p95 provisioning time saved under the same hard cost ceilings, without the balanced value-rate filter. | Latency-sensitive repeated work with a firm budget. |

## Deployment strategy ladder

Gludd uses the cheapest retained-state frontier that satisfies the requested
latency objective. In order, the practical choices are:

1. Retain only the resource group and verified Consumption managed environment.
   This removes the measured 11-16 minute environment creation phase while
   retaining no app or replica. It is the live runner's `zero_cost_only` mode.
2. Retain an inert app definition at `minReplicas: 0` only when an authenticated,
   internal activation boundary proves that arbitrary requests cannot wake it.
   The current public proof cannot establish that fact, so it destroys the app.
3. Keep image layers in a regional ACR or artifact cache when the measured saved
   pull time exceeds the registry's exact storage and service-tier price. Azure
   documents that the first pull still populates the cache and new tags are not
   fetched automatically, so Gludd must pin and measure image digests.
4. Persist model weights and compile/JIT caches separately. Azure Files can be
   shared across revisions and apps, while vLLM documents that its Hugging Face
   weights cache and `VLLM_CACHE_ROOT` compile cache are distinct. Cache identity
   must include every invalidation dimension before reuse is permitted.
5. Predictively create the app shortly before a due todo rather than keeping a
   GPU replica idle. Microsoft states that serverless GPU replicas are always
   billed at the active rate; Gludd therefore does not retain a replica when the
   durable runnable queue is empty.
6. Consider a warm dedicated fleet only for sustained utilization where measured
   throughput plus a firm budget beats serverless cold starts. Dedicated profiles
   and custom-container Dynamic Sessions have baseline management/allocated-pool
   costs, so neither can enter the zero-cost frontier.

Container Apps Jobs are useful for isolated batch executions, but are not an
automatic cold-start optimization. A practitioner report in issue #1800 measured
roughly 47 seconds end to end for about 0.4 seconds of work even with a 117 ms
image pull. Gludd consequently compares complete first-correct-token latency,
not only image-pull time, before changing deployment architecture.

The current autonomous Container Apps integration supplies only the documented
zero-cost Consumption shape to this planner. It destroys the externally
reachable app because scale-to-zero does not prevent a request from activating
it. It retains the managed environment only when there are no Dedicated
profiles, private endpoint, planned-maintenance charge, or paid logging feature.
Container Registry and model-cache layers are represented by the planner, but
must not be retained until their exact current Azure Retail Prices or Cost
Management evidence is supplied.

## Practical retained-state frontier

Gludd evaluates the layers below independently. It does not treat a convenient
monthly estimate as pricing evidence, and it does not assume that a generic VM
disk or public-IP rule applies to a serverless Container Apps deployment.

| Layer | Reuse value | Idle-cost and safety posture |
| --- | --- | --- |
| Resource group | Preserves a stable authorization boundary; normally little provisioning time is saved. | No workload compute is retained. Keep unless the project explicitly requests complete scope removal. |
| Consumption-only managed environment | High. Observed cold creation has taken 711-964 seconds, versus about 18 seconds to reconcile an existing environment. | Eligible for the default only after proving there is no Dedicated profile, private endpoint, planned maintenance, or paid logging feature. |
| Container App configuration | Low in the current measurements: about 18 seconds. | Destroy by default. A public scale-to-zero endpoint can be activated by a request, so a nominally idle app is not an inert cache. |
| Serverless GPU replica | Avoids image, model, and engine cold start. | Never retained as idle compute. Active claimed work uses `minReplicas: 1`; teardown returns to no paid replica. |
| Azure Container Registry | Can avoid remote image availability and pull variability; artifact cache can help repeated pulls. | Paid storage/service layer. `balanced` or `latency_first` may select it only with a current exact meter and measured pull-time benefit. It is represented but not yet autonomously retained. |
| Model and compile cache | Potentially high for large weights and CUDA/JIT compilation. | Azure Files or another durable store has storage and transaction costs. It must be keyed by model revision, image digest, runtime version, GPU architecture, and cache type. It is represented but not yet autonomously retained. |
| Private endpoint and logging | Operational continuity rather than model-start speed. | Explicitly priced. A Container Apps private endpoint also causes a Dedicated Plan Management charge, so neither is eligible for `zero_cost_only`. |

For paid layers, the scheduler compares the projected retention cost over the
next-demand horizon with both the configured cost ceilings and measured p95
seconds saved. `balanced` additionally rejects a layer whose cost per saved hour
exceeds `max_cost_per_saved_hour_microusd`; `latency_first` omits that value-rate
filter but still enforces every hard hourly, monthly, and horizon budget. This
makes the choice reproducible instead of relying on a static list of resources
that are supposedly cheap.

The cache frontier should be measured as separate spans: environment creation,
image pull, model download, compile/JIT work, engine readiness, and first correct
token. The vLLM cold-start roadmap reports a 188.7-second median from clean
official-image pull to first correct token on one specific A10/Qwen3-8B setup,
and 108.3 seconds when the image was already local but the model was absent.
Those figures justify measuring retained state; they are not portable price or
latency constants. The official vLLM Docker guidance also distinguishes the
Hugging Face model cache from `VLLM_CACHE_ROOT`, because retaining weights alone
does not retain compile artifacts.

## Configuration

The `idle_retention` block is required when autonomous Azure Container Apps are
enabled. Values are integer micro-US-dollars so configuration and replay do not
depend on floating-point money calculations.

```yaml
self_improve:
  azure_containerapp:
    # The other exact Azure Container App fields remain required.
    idle_retention:
      schema_version: 1
      preset: zero_cost_only
      max_idle_hourly_cost_microusd: 0
      max_idle_monthly_cost_microusd: 0
      max_retention_cost_microusd: 0
      max_retention_seconds: 21600
      max_price_age_seconds: 31536000
      max_latency_age_seconds: 86400
      max_cost_per_saved_hour_microusd: 0
      expected_next_demand_seconds: null
```

`expected_next_demand_seconds` shortens the retention window when the scheduler
has a prediction. If that prediction is beyond `max_retention_seconds`, Gludd
destroys immediately. `null` means retain no longer than the configured maximum
before reevaluation. The plan and scope digests, chosen layers, deadline, p95
seconds saved, and micro-dollar costs are emitted as content-free traces and as
the `gludd_azure_containerapp.retention` Ansible fact.

The deadline is a reconciliation deadline, not an Azure-side deletion timer.
The next owned lifecycle invocation rechecks it and destroys an expired empty
environment. The integrated default can retain only a verified zero-cost
control-plane shape, so daemon downtime cannot leave a billable GPU replica
running through this mechanism. Use `always_destroy` when the resource must not
remain present between invocations. Paid-layer retention remains fail-closed
until Gludd's durable deployment registry owns its expiry cleanup.

The bounded live diagnostic exposes the same safe retry option. All Make
variables remain explicit; select
`AZURE_CONTAINERAPP_LIVE_PROOF_RETENTION_PRESET=zero_cost_only` and set
`AZURE_CONTAINERAPP_LIVE_PROOF_RETENTION_SECONDS` to the desired reevaluation
window. `always_destroy` remains the hermetic/GHA default. The app is still
destroyed and independently proven absent on success or failure. Planner events
are emitted as `AZURE_CONTAINERAPP_RETENTION_TRACE`; environment lifecycle events
carry the selected plan digest, remaining seconds, and hourly micro-dollar cost.
For a long live run, invoke the target through the existing `make run-watched`
target with an explicit `LOG` path so every heartbeat and fixed failure detail is
both streamed and retained for diagnosis.

## Azure billing boundaries

Microsoft documents that Consumption workloads can scale to zero and incur no
resource-consumption charge while idle, while serverless GPU replicas are billed
while running. Dedicated workload profiles have a plan-management charge, and
private endpoints and planned maintenance can cause Dedicated Plan Management
billing even in an otherwise Consumption environment. These distinctions are
why Gludd prices each layer separately rather than assuming the environment is
free:

- [Container Apps billing](https://learn.microsoft.com/en-us/azure/container-apps/billing)
- [Workload profiles and plan types](https://learn.microsoft.com/en-us/azure/container-apps/plans)
- [Environment structure](https://learn.microsoft.com/en-us/azure/container-apps/environment)
- [Private endpoints](https://learn.microsoft.com/en-us/azure/container-apps/private-endpoints-with-dns)
- [Container Registry storage](https://learn.microsoft.com/en-us/azure/container-registry/container-registry-storage)
- [Container Registry retention policy](https://learn.microsoft.com/en-us/azure/container-registry/container-registry-retention-policy)
- [Container Registry artifact cache](https://learn.microsoft.com/en-us/azure/container-registry/artifact-cache-overview)
- [Azure Files storage mounts for Container Apps](https://learn.microsoft.com/en-us/azure/container-apps/storage-mounts)
- [Container Apps Dynamic Sessions](https://learn.microsoft.com/en-us/azure/container-apps/sessions)
- [Container Apps revision state API](https://learn.microsoft.com/en-us/rest/api/resource-manager/containerapps/container-apps-revisions/get-revision?view=rest-resource-manager-containerapps-2026-01-01)
- [vLLM persistent model and compile caches](https://github.com/vllm-project/vllm/blob/main/docs/deployment/docker.md)

## Practitioner reports considered

Azure users have reported the same tradeoff between `minReplicas: 0`, cold
starts, and keeping a replica warm in [azure-container-apps issue
388](https://github.com/microsoft/azure-container-apps/issues/388). Reports also
show why retention must remain observable and fail closed: GPU image/startup
failures in [issue #1511](https://github.com/microsoft/azure-container-apps/issues/1511),
an unexpected serverless-GPU billing report in [issue
1628](https://github.com/microsoft/azure-container-apps/issues/1628), and GPU
profile/API-version behavior in [issue
1646](https://github.com/microsoft/azure-container-apps/issues/1646). Long
managed-environment deletion or stuck states have also been reported in [issue
433](https://github.com/microsoft/azure-container-apps/issues/433), [issue
1523](https://github.com/microsoft/azure-container-apps/issues/1523), and
[issue #1778](https://github.com/microsoft/azure-container-apps/issues/1778).
GPU startup variance is not limited to image pulls: a report in
[issue #1763](https://github.com/microsoft/azure-container-apps/issues/1763) describes
an intermittent 25-minute delay after an image had already been pulled. The
vLLM project separately records a reproducible cold-start baseline in
[roadmap #48193](https://github.com/vllm-project/vllm/issues/48193) and a report that a
600-second engine-ready timeout can be insufficient for a large cold model and
empty JIT cache in
[issue #48031](https://github.com/vllm-project/vllm/issues/48031).
Container Apps users also report that per-execution platform startup and cleanup
can dominate short jobs in [issue #1800](https://github.com/microsoft/azure-container-apps/issues/1800),
reinforcing
the need to compare a retained regular app, an isolated job, and predictive
prewarming using the same complete latency boundary.

These reports also shape readiness telemetry. Gludd now waits on the exact
revision through Microsoft's SDK and emits only bounded `active`, replica-count,
health, provisioning, and running-state facts. It never forwards the provider's
free-form `provisioningError`. This distinguishes image/model warm-up from an
ARM resource merely existing and keeps long deployments observable without
leaking provider or project content.

These reports are operational evidence, not pricing authority. Gludd uses
Microsoft billing documentation and exact current meter evidence for decisions;
forum reports inform conservative timeouts, heartbeats, independent ARM reads,
and absence verification.
