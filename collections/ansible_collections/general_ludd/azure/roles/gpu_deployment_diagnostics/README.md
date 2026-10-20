# Azure GPU deployment diagnostics

`general_ludd.azure.gpu_deployment_diagnostics` turns bounded, normalized Azure
accelerator observations into an Ansible fact, a visible phase stream, and an
optional private JSON artifact. It encodes deployment lessons without owning a
second cloud lifecycle implementation.

The role does not provision, update, or destroy Azure resources. Terraform
remains the desired-state lifecycle owner. A caller gathers facts with the
Microsoft SDK or supported `azure.azcollection` information modules, maps them
to the schema below, and invokes this role. The role contains no Azure CLI call,
credential loading, or provider-specific HTTP client.

## Safety and privacy boundary

The role is disabled by default and writes no artifact by default. It accepts a
closed schema of counts, states, durations, booleans, and reason classes. Raw
provider logs are never accepted. Raw provider messages, prompts, credentials,
secrets, and access tokens are rejected rather than redacted after ingestion.
Optional artifacts must use a `/tmp/gludd-*` namespace and are mode `0600`.

This boundary is deliberate: the SDK-facing adapter should classify Azure
responses while they are in memory, then pass only the normalized result. Never
register a task result containing authentication data and never feed provider
message text into self-improvement prompts.

## Input

```yaml
- name: Classify a bounded deployment observation
  ansible.builtin.include_role:
    name: general_ludd.azure.gpu_deployment_diagnostics
  vars:
    azure_gpu_diagnostics_enabled: true
    azure_gpu_diagnostics_observation:
      schema_version: 1
      operation_id: deployment-001
      region: westus3
      phases:
        - phase: preflight
          status: succeeded
          duration_seconds: 2
        - phase: revision_activation
          status: failed
          duration_seconds: 90
        - phase: cleanup
          status: succeeded
          duration_seconds: 18
      quota: {available: 2, requested: 1, sufficient: true}
      workload_profile:
        name: policy-selected-primary
        accelerator_kind: nvidia_gpu
        available: true
        quota_sufficient: true
      revision:
        summary_replica_count: 1
        exact_replicas: []
      system_events:
        - scope: environment
          severity: warning
          reason_class: capacity_unavailable
          count: 2
      retry: {attempt: 3, max_attempts: 3}
      fallback_profiles:
        - name: policy-selected-fallback
          accelerator_kind: nvidia_gpu
          available: true
          quota_sufficient: true
          estimated_hourly_cost_usd: 3.5
      cleanup:
        application_absent: true
        environment_retained: true
        estimated_idle_hourly_cost_usd: 0
        retention_mode: zero_cost_only
```

The profile names and accelerator kinds are data, not hard-coded device keys.
The same schema can represent a GPU, TPU, NPU, or a future accelerator after
the platform adapter has discovered it and policy has admitted it.

## Output fact

`azure_gpu_diagnostics` includes:

- visible `phase_markers` with bounded duration values;
- quota and workload-profile preflight facts;
- both the control-plane summary replica count and exact replica/container
  inventory totals;
- normalized application, revision, and environment event aggregates;
- a bounded next action such as `retry_same_profile`, `failover_profile`,
  `repair_deployment`, `request_quota`, or `use_deployment`;
- cleanup and zero-cost-retention evidence; and
- `quality_evidence_action`, which censors model-quality scoring when the
  infrastructure never produced a usable replica.

A summary count is never accepted as proof of a running container. If the
summary reports one replica but the exact inventory is empty, the fact records
`summary_matches_exact: false`. A transient failure retries only until the
caller-supplied bound. It then chooses the first policy-approved, discovered,
quota-sufficient fallback profile. The role never invents a device name.

Cleanup advice is equally bounded. A retained environment is considered safe
only when the caller attests zero idle hourly cost and uses `zero_cost_only`.
An application that remains present, or a retained cost-bearing environment,
produces `cleanup_required` and takes precedence over another retry.

## Evidence behind the policy

Microsoft documents that Container Apps serverless GPUs use Consumption GPU
workload profiles, require quota, allocate a whole GPU per replica, bill per
second, and can scale to zero. The workload-profile documentation distinguishes
per-replica Consumption capacity from cost-bearing Dedicated profile instances:

- [Using serverless GPUs in Azure Container Apps](https://learn.microsoft.com/en-us/azure/container-apps/gpu-serverless-overview)
- [Workload profiles in Azure Container Apps](https://learn.microsoft.com/en-us/azure/container-apps/workload-profiles-overview)
- [Container Apps system-log fields](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/containerappsystemlogs)

Long-lived practitioner reports show why the role keeps separate evidence
channels and never trusts a single control-plane summary:

- [Issue 1511](https://github.com/microsoft/azure-container-apps/issues/1511)
  reports intermittent GPU jobs that create a pod but never reach image pull,
  sometimes recovering minutes later. This supports bounded retries and
  profile/region failover rather than lowering a model-quality score.
- [Issue 1682](https://github.com/microsoft/azure-container-apps/issues/1682)
  reports a CUDA/runtime mismatch that can silently fall back to CPU. This
  supports an explicit accelerator canary and the `driver_incompatible` and
  `gpu_unavailable` reason classes before accepting inference evidence.
- [Issue 1705](https://github.com/microsoft/azure-container-apps/issues/1705)
  includes a case where summary metadata reports a replica while exact replica
  containers are empty and capacity masks another problem. This supports exact
  replica inventory and separate system-event aggregation.
- [Issue 1746](https://github.com/microsoft/azure-container-apps/issues/1746)
  announces removal of root access for serverless GPU workloads. This supports
  non-root image validation as a preflight concern rather than reactive shell
  debugging in a running deployment.

Forum reports are operational evidence, not an Azure service guarantee. Keep
the mapping updatable, retain the original links, and prefer measured facts
from the current deployment over a permanent region or hardware blacklist.

## Verification

The Molecule scenario is hermetic and uses sanitized fixtures; it requires no
Azure credential and creates no cloud resource:

```bash
make molecule-test SCENARIO=azure_gpu_deployment_diagnostics
```

It exercises healthy inference, quota failure, exact-replica mismatch,
transient profile failover, cleanup risk, zero-cost retention, raw-input
rejection, artifact privacy, and idempotence.
