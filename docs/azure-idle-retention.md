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

The current autonomous Container Apps integration supplies only the documented
zero-cost Consumption shape to this planner. It destroys the externally
reachable app because scale-to-zero does not prevent a request from activating
it. It retains the managed environment only when there are no Dedicated
profiles, private endpoint, planned-maintenance charge, or paid logging feature.
Container Registry and model-cache layers are represented by the planner, but
must not be retained until their exact current Azure Retail Prices or Cost
Management evidence is supplied.

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

These reports are operational evidence, not pricing authority. Gludd uses
Microsoft billing documentation and exact current meter evidence for decisions;
forum reports inform conservative timeouts, heartbeats, independent ARM reads,
and absence verification.
