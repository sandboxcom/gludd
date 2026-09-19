# Provider-neutral accelerator topology contract

## Status and scope

This document describes Gludd's provider-neutral contract for planning a model
runner onto accelerators. The contract is implemented in
`general_ludd.hardware.accelerator_topology`; it is a pure planner and does not
provision infrastructure or start a model server.

The module depends on the structural discovery contract introduced by S83.159.
That prerequisite supplies discovered accelerator facts such as kind, vendor,
model, count, memory, backend, locality, node, and partitions. Adapters enrich
those facts with topology, cost, and runner capability attestations. No hardware
model name is a configuration key in the planner.

Azure's service is called Virtual Machines, not EC2. EC2 is an AWS product. Azure
currently documents GPU-backed Container Apps and GPU Virtual Machines; this
contract does not imply that Azure offers Google Cloud TPUs.

## Contract boundaries

The planner has four inputs and one desired-state output:

1. `AcceleratorTopology` wraps one discovered resource with provider, region,
   zone, host, interconnect, partition isolation, workload-placement, cost, and
   attestation facts.
2. `RunnerCapabilities` declares what a particular, observed runner deployment
   can do: device kinds, backends, partition modes, distribution mode, supported
   model-, tensor-, pipeline-, and data-parallel sizes, interconnect requirements,
   and host limits.
3. `ModelRunnerDemand` contains exact artifact-derived weight bytes and explicit
   runtime, KV-cache, context, concurrency, replication, and divisibility needs.
4. `TopologyConstraints` contains the caller's allowlists and ceilings for
   providers, regions, devices, hosts, hourly cost, and memory reserve.
5. `TopologyPlan` is immutable desired state. It identifies the selected topology
   and runner, device and host counts, parallelism, required and usable memory,
   indivisible billing units, and projected hourly cost.

Device kind is deliberately an opaque normalized string. GPU, TPU, FPGA, and a
future accelerator kind therefore use the same path. Vendor and model are facts,
not branches in the planner. Platform-specific adapters may discover hundreds of
models without expanding a central enumeration.

The canonical `AcceleratorResource` preserves compatibility constants for the
built-in GPU and TPU probes, but it also accepts any bounded, normalized
lowercase kind reported by a future adapter. Inventory serialization preserves
that value verbatim. This prevents the discovery layer from contradicting the
planner by requiring a core enum release before an FPGA, NPU, or other new
accelerator can be represented and routed.

The planner accepts the discovery object through a structural protocol. This
keeps discovery ownership in S83.159 rather than copying that type into a second
abstraction.

## Universal demand derivation

`general_ludd.hardware.model_service_rightsizing` supplies the upstream bridge
from an ordinary task graph to `ModelRunnerDemand`. It is not part of the
self-improvement subsystem. The bridge consumes three immutable records:

- `ModelVariantEvidence` identifies an exact model variant, architecture,
  quantization, memory requirements, context ceiling, legal device kinds,
  legal runner identities, quality evidence, and tensor-sharding divisor.
- `InferenceWorkloadDemand` states input and output budgets, concurrent
  sequences, minimum measured quality, maximum p95 latency, and whether an
  isolated shared accelerator is acceptable.
- `RunnerSizingEvidence` states an observed runner's per-replica batch,
  context, output, replica, and p95 latency limits.

The derivation computes full context as input plus output, chooses no larger a
per-replica batch than either demand or the attested runner limit, and computes
the minimum replica count with ceiling division. It then produces the exact
memory and parallelism demand consumed by the topology planner. Unknown,
unattested, over-context, over-output, under-quality, over-latency, unsupported,
or over-replica evidence yields a stable refusal instead of silently reducing
the requested task. This same path applies to chemistry, firmware, games,
self-improvement, and every other task collection.

## Right-sizing rules

Required replicated memory is calculated as:

```text
runtime_and_kv = runtime_overhead
               + kv_bytes_per_token * context_tokens * concurrent_sequences
per_device_required = ceil(weight_bytes / model_parallel_devices)
                    + runtime_and_kv
usable_device_memory = memory_bytes * (1000 - reserve_millis) / 1000
```

Every selected device must meet the per-device requirement after the requested
reserve. The model-parallel device count must divide the model's declared
parallelism divisor. This prevents a planner from silently selecting a shard
shape that the artifact cannot use.

Candidate plans are ordered by hourly cost, then accelerator count, then unused
memory, and finally a stable identity. Consequently, the smallest known-cost fit
wins without embedding assumptions about a particular GPU or TPU model.

Costs preserve the provider's billing boundary. For example, an eight-GPU VM is
one indivisible billable unit even if a plan needs only four GPUs. The conservative
plan does not assume that independent data-parallel replicas can be packed into
unused devices in an already billed unit; an adapter can expose a separately
attested packing topology when that guarantee exists.

## Placement and runner semantics

`max_devices_per_workload` is different from available device count. This makes
the following deployments representable without special hardware keys:

- An Azure Container Apps replica can advertise one device per workload even
  when multiple replicas are available. A model too large for one device is
  rejected, while independent data-parallel replicas can still scale out.
- An Azure GPU VM can advertise several devices on one host, an attested
  intra-host interconnect group, and a VM-level billing unit.
- A Slurm allocation can describe devices per node, multiple hosts, intra-host
  and cross-host links, and scheduler-managed partitions.
- A TPU adapter can declare legal mesh sizes and runner-managed distribution
  without pretending that TPU topology is tensor parallelism.
- Local GPU, accelerator partition, and future FPGA adapters use the same fields.

Explicit-parallel runners, such as an attested vLLM deployment, declare legal
tensor and pipeline sizes. Tensor parallelism must fit one attested interconnect
group. Pipeline parallelism may span hosts only when both runner and topology
attest the cross-host link. Runner-managed deployments, such as an observed
Ollama or JAX setup, declare legal aggregate sizes but do not cause the planner to
invent tensor or pipeline settings.

An adapter must describe capabilities it has verified for its exact version and
launch configuration. Documentation saying that a feature exists is not itself a
runtime attestation.

## Fail-closed behavior

Planning returns a typed refusal rather than guessing when any material fact is
unknown or incompatible. Refusals cover, among other cases:

- missing or stale attestation;
- zero or unknown memory, availability, or workload capacity;
- missing cost data;
- disallowed provider or region;
- unsupported accelerator kind, backend, partitioning, or shared isolation;
- an unsupported tensor, pipeline, data-parallel, or runner-managed size;
- a model-parallel shape that violates artifact divisibility;
- absent intra-host or cross-host interconnect evidence;
- device, host, or hourly-cost ceilings; and
- arithmetic overflow or malformed demand and capability records.

Shared or partitioned devices require explicit runner support. Shared placement
also requires the workload to opt in and the topology to attest isolation. An
unattested device is never promoted to a valid candidate because another device
in the same pool is healthy.

## Lifecycle, ZDD, and rollback

The contract produces desired state only. A lifecycle controller should apply a
zero-downtime deployment sequence:

1. Re-attest inventory, topology, quota, price, and runner capability immediately
   before provisioning.
2. Provision a replacement runner under a unique generation while the serving
   generation remains healthy.
3. Load the exact model artifact, run health, protocol, capacity, and quality
   probes, and record the resulting attestation.
4. Shift a bounded canary share, observe service and accelerator signals, then
   advance traffic only while the rollout policy remains satisfied.
5. Retain the prior generation until the rollback window closes; retire it only
   after durable success evidence exists.

Rollback routes traffic to the retained generation first. It must not depend on
destroying or mutating the failed generation. A rejected plan performs no
provisioning and therefore needs no cleanup. A failed replacement is quarantined
for diagnostics and reclaimed by a lease-aware lifecycle policy, never by a
broad or path-derived cleanup command.

## Security and privacy

Discovery adapters need only read hardware and scheduler facts. Planning is pure
and requires no cloud credential. Provisioning credentials remain outside this
contract and should be short lived and scoped to the exact resource owner and
actions needed by the lifecycle controller.

Model identity, resource identity, vendor, region, host, and node do not appear in
the planner's operational trace. Traces contain a phase, candidate counts, and a
bounded reason code. Detailed inventory belongs in access-controlled state, not
general telemetry. Demand inputs contain byte counts and execution requirements,
not model content or application prompts.

## Observability

Each planning attempt emits ordered `started`, `rejected`, `accepted`,
`completed`, or `failed` events through an injected trace sink. Candidate totals
and bounded reason codes make selection auditable without disclosing identities.
A sink failure does not change the placement decision and must be monitored by the
caller as a separate telemetry-health signal.

Lifecycle adapters should correlate the plan identity and generation with cloud
control-plane operations, runner logs, scheduler events, accelerator memory and
utilization, model-load progress, readiness probes, rollout state, cost samples,
and cleanup leases. Long operations must continue to emit progress or heartbeats.
Observed capability changes invalidate the earlier attestation and trigger a new
plan rather than mutating a running plan in place.

## Verification strategy

The contract suite is hermetic and runs locally and in GitHub Actions without
Azure or other cloud credentials. It exercises one-device Container Apps
placement, multi-GPU VM tensor parallelism, Slurm multi-host pipeline parallelism,
TPU and Ollama-style runner-managed distribution, an opaque future FPGA kind,
partitions, sharing, indivisible billing, deterministic choice, safe traces, and
all fail-closed branches.

Provider integration tests are a separate layer. They must use credentials from
the CI secret store or workload identity, create uniquely leased resources within
explicit cost and scope limits, attest the resulting live topology, exercise a
real runner request, and reclaim only resources bearing that lease. A missing
credential skips that live layer; it must not convert fabricated fixture data into
a claim that live provisioning passed.

## Current capability sources

The contract reflects capabilities as of 2026-09-15, but adapters must verify the
live provider and runner version because service limits change.

- [Azure Container Apps serverless GPU overview](https://learn.microsoft.com/en-us/azure/container-apps/gpu-serverless-overview)
  documents Consumption GPU profiles and states that a GPU-enabled app replica
  supports one container using the GPU, while multiple and fractional GPUs per
  replica are unsupported.
- [Azure VM size overview](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/overview)
  describes single-, multiple-, and fractional-GPU optimized VM sizes.
- [Azure ND GPU VM family](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-family)
  includes multi-accelerator shapes and topology-specific links, including
  MI300X configurations using Infinity Fabric and scale-out networking.
- [Azure NC GPU VM family](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nc-family)
  documents other single- and multi-GPU shapes.
- [vLLM parallelism and scaling](https://docs.vllm.ai/en/latest/serving/parallelism_scaling/)
  documents single-GPU, single-node tensor parallel, and multi-node tensor plus
  pipeline parallel deployments, including topology-dependent recommendations.
- [vLLM data-parallel deployment](https://docs.vllm.ai/en/stable/serving/data_parallel_deployment/)
  shows that data- and tensor-parallel dimensions multiply the required device
  count and describes per-rank concurrency.
- [Ollama FAQ](https://docs.ollama.com/faq)
  documents runner-managed spreading when a model does not fit on one GPU and
  memory-dependent concurrent model loading.
- [Google Cloud TPU system architecture](https://docs.cloud.google.com/tpu/docs/system-architecture-tpu-vm)
  describes version-specific chip slices, two- and three-dimensional topologies,
  and single- and multi-host TPU VM arrangements.
- [Google Cloud model scaling on TPUs](https://docs.cloud.google.com/tpu/docs/scaling-model-on-tpus)
  documents framework-managed scaling choices for TPU workloads.
- [Slurm generic resources](https://slurm.schedmd.com/gres.html)
  documents arbitrary GRES, GPU allocation controls, topology links, vendor
  autodetection, accounting, and current MIG discovery and accounting limits.

## Practitioner reports and operational consequences

Long-lived issue reports are design evidence, not proof that every deployment
will reproduce the same failure:

- [Azure Container Apps issue 1511](https://github.com/microsoft/azure-container-apps/issues/1511)
  reports intermittent T4 job replicas that are created but do not reach image
  pulling or container startup. Lifecycle control therefore needs progress
  deadlines, live events, retry, and safe rerouting.
- [Azure Container Apps issue 1705](https://github.com/microsoft/azure-container-apps/issues/1705)
  reports desired replica state diverging from the materialized revision and a
  workload-profile-capacity failure. Attestation must inspect the actual revision,
  containers, replicas, and capacity rather than trust only desired state.
- [Azure Container Apps issue 1746](https://github.com/microsoft/azure-container-apps/issues/1746)
  discusses changes to root access. Runner compatibility and required Linux
  capabilities must be attested rather than assumed from an older image.
- [vLLM issue 4431](https://github.com/vllm-project/vllm/issues/4431) and
  [vLLM issue 26598](https://github.com/vllm-project/vllm/issues/26598)
  report multi-GPU startup or model-load hangs for particular tensor/pipeline
  arrangements. Runner sizes in the contract are explicit observed capabilities,
  not every mathematically possible factorization.
- [Ollama issue 7047](https://github.com/ollama/ollama/issues/7047) reports uneven
  memory placement across two GPUs, while
  [issue 8430](https://github.com/ollama/ollama/issues/8430),
  [issue 10301](https://github.com/ollama/ollama/issues/10301), and
  [issue 11810](https://github.com/ollama/ollama/issues/11810) describe pinning,
  utilization-aware scheduling, and forced-spread gaps. Gludd therefore treats
  Ollama placement as runner-managed and verifies actual placement and headroom
  after load instead of predicting an undocumented split.

These reports motivate the same rule across providers: discovery describes what
exists, a versioned runner attestation describes what has worked, the pure planner
selects only supported combinations, and the lifecycle controller proves the
combination again before traffic reaches it.
