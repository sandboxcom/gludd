# Azure GPU deployment diagnostics

## Purpose

Gludd needs reusable deployment evidence that helps the next Azure run without
copying secrets, raw prompts, or voluminous provider output into model context.
The `general_ludd.azure.gpu_deployment_diagnostics` role is the policy boundary
between Azure discovery and self-improvement routing.

Terraform still owns resource desired state. Microsoft SDK or
`azure.azcollection` adapters still own reads from Azure. The role accepts only
the resulting normalized facts, publishes them as `azure_gpu_diagnostics`, and
optionally writes a mode-`0600` JSON artifact. It never invokes Azure CLI or
duplicates lifecycle operations.

## Diagnostic sequence

1. Emit a visible marker for every bounded phase: bootstrap, preflight,
   environment apply, application apply, revision activation, exact replica
   inventory, inference probe, cleanup, and completion.
2. Check discovered region support, profile availability, and quota before
   attempting a cost-bearing workload.
3. Read both revision summary state and exact replica/container inventory.
   Summary state is advisory; a usable exact replica is required.
4. Aggregate only reason class, severity, scope, and count from application,
   revision, and environment system events.
5. Retry transient scheduling/capacity failures only within a supplied bound.
   After that bound, use a discovered, quota-sufficient, policy-approved
   fallback rather than a hard-coded GPU model or region.
6. Keep infrastructure failures out of model-quality calibration. Record model
   evidence only after an exact ready replica and successful inference probe.
7. Verify application absence during cleanup. Retain an environment only when
   policy explicitly attests zero idle cost; otherwise report cleanup required.

## Learned failure modes

Current Microsoft documentation says serverless GPU profiles are Consumption
profiles, quota-gated, allocated per replica, billed per second, and able to
scale to zero. It also documents separate Consumption and Dedicated cost
semantics. These are preflight inputs, not proof that a particular replica ran:

- [Serverless GPU overview](https://learn.microsoft.com/en-us/azure/container-apps/gpu-serverless-overview)
- [Workload profiles](https://learn.microsoft.com/en-us/azure/container-apps/workload-profiles-overview)
- [Application and system logs](https://learn.microsoft.com/en-us/azure/container-apps/logging)
- [ContainerAppSystemLogs schema](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/containerappsystemlogs)

Practitioner reports provide durable warnings that are easy to lose during an
interactive debugging session:

- [Intermittent GPU image-pull stall, issue 1511](https://github.com/microsoft/azure-container-apps/issues/1511):
  a pod can be created without reaching image pull, then recover later. Gludd
  therefore distinguishes platform startup from image and model behavior.
- [CUDA compatibility and silent CPU fallback, issue 1682](https://github.com/microsoft/azure-container-apps/issues/1682):
  container readiness alone does not attest accelerator use. An inference run
  needs a device canary and measured accelerator evidence.
- [Replica summary versus empty containers, issue 1705](https://github.com/microsoft/azure-container-apps/issues/1705):
  desired/control-plane state can mask capacity and materialization failures.
  Gludd records exact replica and container counts independently.
- [Non-root serverless GPU change, issue 1746](https://github.com/microsoft/azure-container-apps/issues/1746):
  image compatibility should be validated before deployment, with `/tmp` used
  for writable runtime state.

The reports are evidence for measurement and failover, not permanent device or
region deny-lists. Gludd should age operational observations, retry within cost
and time bounds, and continuously re-attest profiles as Azure evolves.

## Sanitized Gludd run evidence

A September 2026 Gludd diagnostic sequence produced the following bounded
facts. No prompt, credential, source patch, resource identifier, or provider
message is retained here:

- initial managed-environment creation took about 937 seconds;
- reusing that zero-idle-cost environment reduced its apply phase to about 19
  seconds;
- application apply and verified application removal each took about 18
  seconds;
- revision summary state reported one desired replica while repeated exact
  inventory reads found zero replicas and zero started or ready containers;
- bounded application and environment system-event reads succeeded, but no
  recognized reason class explained the missing replica; and
- application absence was independently verified after failure, while the
  explicitly zero-idle-cost environment was retained for another bounded run.

Those observations produce reusable policy, not a one-off workaround. Separate
cold environment time from application activation time. Query exact replica
inventory even after a terminal revision state. Treat a missing reason as
`unknown`, not as proof of image or model failure. Retry only within the policy
budget, then test another discovered profile or region. Verify cleanup after
every failure. Retain a reusable control-plane resource only when current cost
facts and explicit retention policy both say it is safe.

The original model candidate receives no negative quality observation when no
container started. Infrastructure operational evidence and model-quality
evidence remain separate so transient Azure capacity does not teach the router
that a capable model is bad at coding.

## Privacy contract

The accepted schema is intentionally lossy. It permits identifiers limited to
safe operation/profile/region labels, numeric counters and durations, boolean
preflight and cleanup facts, enumerated state, and enumerated reason classes.
It rejects raw provider messages, logs, prompts, credentials, secrets, and
tokens before fact publication. Nothing in the artifact can reconstruct the
original prompt, source patch, credential, or Azure error message.

## Test contract

The local/GitHub Actions path is credential-free. Its Molecule scenario uses
sanitized fixtures and proves healthy use, quota blocking, summary/exact replica
disagreement, bounded retry exhaustion and profile failover, cleanup precedence,
zero-cost retention, strict input rejection, artifact privacy, and idempotence.
Live Azure tests can feed the same schema, but live credentials and provisioning
remain opt-in and outside this role.
