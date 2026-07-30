# Azure A100 and comparable accelerator live-proof specification

Status: PROPOSED

Scope: implementation specification only. Writing or validating this document
must not create, modify, or delete Azure resources.

Related design: [Compute deploy and saturate](COMPUTE_DEPLOY_AND_SATURATE.md).

## 1. Outcome

Gludd needs one bounded, repeatable proof that an exact Gludd commit can:

1. select an explicitly approved Azure region and accelerator SKU;
2. provision one disposable accelerator VM through Gludd's deployment path;
3. make the endpoint ready and register it through Gludd's normal model
   gateway;
4. send exactly one completion request that produces exactly one new token;
5. prove that the request executed CUDA kernels on the expected physical GPU;
6. deregister the endpoint, destroy every proof-owned resource, and verify
   deletion; and
7. emit a tamper-evident evidence bundle bound to the tested Git commit.

The proof is not a benchmark, training run, quota request, capacity
reservation, production deployment, or permission to spend without an
explicit operator acknowledgement. A successful dry run, credential check,
Terraform plan, mocked test, reachable HTTP endpoint, or `nvidia-smi` device
listing is not a live proof.

If a release claims Azure A100 support, its evidence must use an A100 SKU. A
different approved accelerator can prove only the named comparable-SKU claim;
an H100, L40S, A10, CPU fallback, or emulated CUDA device must never silently
satisfy an A100 claim.

## 2. Current repository state is not live evidence

The current `make azure-harness` implementation in
`scripts/provider_smoke_harness.py` explicitly performs only a read-only
credential and subscription check. It never creates or deletes resources.
That is useful preflight coverage, but it cannot establish accelerator
usability.

The present Azure vLLM stack also contains values that a live proof must refuse
to inherit:

- `infra/terraform/stacks/azure-vllm/variables.tf` defaults to
  `Standard_NC6s_v3`, permits inference and SSH from `0.0.0.0/0`, selects Spot,
  and floats the container tag `latest`;
- `infra/terraform/modules/vllm-server/outputs.tf` reports a
  `terraform_data` identifier as `instance_id` and a hard-coded
  `http://localhost:8000/v1` as `base_url`, rather than the Azure VM resource
  ID and its actual private address;
- the vLLM cloud-init fragment starts `docker run` in the background but does
  not itself prove that Docker, the NVIDIA driver, the container, or
  `/v1/models` became ready; and
- the Azure stack surfaces the cost-watchdog fragment but does not compose it
  into the VM's `custom_data`.

These are implementation prerequisites, not reasons to weaken the proof. The
new target must fail before chargeable provisioning until the converged Azure
stack uses actual VM/NIC outputs, a digest-pinned image, closed ingress,
platform-enforced shutdown, and a readiness-gated service.

## 3. Public contract

Implement:

- `src/general_ludd/infra/azure_accelerator_proof.py`: state machine,
  Azure adapters, evidence validation, and cleanup reconciliation;
- `scripts/azure_accelerator_live_proof.py`: a thin CLI around that module;
- `make azure-accelerator-live-proof`: the only agent-facing entry point; and
- a manually approved `workflow_dispatch` job that invokes the same target and
  uploads the evidence archive.

The target contract is:

| Variable | Contract |
|---|---|
| `LIVE` | Defaults to `0`. Only literal `1` permits mutation. |
| `ACK_COST` | Must equal `I_ACCEPT_AZURE_ACCELERATOR_CHARGES` in live mode. |
| `AZURE_SUBSCRIPTION_ID` | Required; must belong to the configured non-production proof allowlist. |
| `REGIONS` | Required ordered list of one to three operator-approved regions. No implicit default. |
| `SKU` | Required exact primary SKU, initially `Standard_NC24ads_A100_v4`. |
| `SKU_ALLOWLIST` | Required path to a committed capability-and-cost policy. |
| `MAX_COST_USD` | Required positive ceiling; never inferred upward. |
| `TIMEOUT_MINUTES` | Required; maximum 45, including provisioning and teardown. |
| `ALLOW_SPOT` | Defaults to `0`. Literal `1` is a separate, explicit acknowledgement. |
| `MODEL_BUNDLE` | Required immutable model artifact URI plus digest. |
| `GLUDD_ARTIFACT` | Required exact-commit Gludd artifact URI plus digest. |
| `EVIDENCE_DIR` | Required namespaced output directory. |

Cloud credentials are environment-only inputs and must never appear in the
command line, output, evidence, Terraform variables, or state. Hosted runs use
GitHub OIDC and `DefaultAzureCredential`; a client secret is a local fallback,
not the recommended path.

`LIVE=0` executes identity-shape, policy, price-query, SKU, quota-response,
Terraform-plan, state-machine, evidence-schema, and cleanup-plan validation
against fixtures. It performs no Azure write. `LIVE=1` without the exact
acknowledgement aborts before creating a resource group.

The command returns:

- exit `0` only for `PASS` with deletion verified;
- exit `2` for a preflight refusal;
- exit `3` for provisioning or readiness failure;
- exit `4` for routing, workload, or telemetry failure; and
- exit `5` whenever cleanup cannot be proved, regardless of earlier success.

Every exit emits a redacted JSON summary. There is no "warning-only" result.

## 4. SKU and region policy

The default policy contains one exact claim:

```yaml
Standard_NC24ads_A100_v4:
  claim: azure-a100-80gb
  expected_device_name: "^NVIDIA A100.*80GB"
  minimum_gpu_count: 1
  minimum_vram_mib: 79000
  minimum_cuda_compute_capability: "8.0"
  required_vcpus: 24
```

Microsoft documents `Standard_NC24ads_A100_v4` as one NVIDIA A100 PCIe GPU
with 80 GB of accelerator memory and 24 vCPUs in the
[NC A100 v4 size specification][azure-a100-size].

Comparable hardware is capability-based but never automatically substituted.
Each additional SKU must have a separately reviewed policy entry with its
expected device-name expression, GPU count, VRAM, CUDA capability, vCPU
requirement, maximum hourly rate, and claim name. At runtime the proof must
compare all of the following:

1. the requested exact SKU;
2. the SKU returned by the Azure VM resource and instance view;
3. Azure Resource SKU capabilities and subscription-specific restrictions;
4. the driver-reported GPU name, UUID, VRAM, and CUDA capability; and
5. the claim being attested.

Any mismatch destroys the run. The
[Resource SKUs API][azure-resource-skus] exposes locations, zones,
capabilities, and `QuotaId`/`NotAvailableForSubscription` restrictions; an
empty restriction list is necessary but does not prove capacity.

The runner may try the ordered regions only for Azure
`AllocationFailed`, `ZonalAllocationFailed`, or
`OverconstrainedAllocationRequest`. It gets one allocation attempt per region
and at most three attempts total. It must completely clean the failed attempt
before trying the next region. Authentication, authorization, price, quota,
policy, driver, workload, telemetry, and cleanup failures are never
region-retried.

Azure documents capacity as distinct from quota and recommends changing a
zone, region, or size when allocation is unavailable
([allocation failure guidance][azure-allocation-failure]). The target records
capacity failure as evidence, not as a false product failure and not as a
pass.

## 5. Cost, quota, and identity gates

Preflight is read-only and must complete before a resource group is created:

1. obtain an OIDC/managed-identity token and verify the tenant and subscription
   are the configured non-production proof scope;
2. query Resource SKUs for every candidate region and reject restrictions or
   missing required capabilities;
3. query both total regional vCPU quota and the exact VM-family quota; require
   `current_usage + required_vcpus <= limit` for both;
4. query the Azure Retail Prices API for one unambiguous Linux consumption
   meter matching the exact ARM SKU and region;
5. compute a conservative ceiling using the price's billing unit, the full
   timeout, disk/network allowance, and a configurable overhead factor of at
   least `1.25`;
6. require that ceiling to be no greater than both `MAX_COST_USD` and the
   policy's maximum hourly rate; and
7. verify that the durable cleanup reconciler is healthy and that no active
   lease exists for the same proof scope.

Azure enforces both regional and VM-family vCPU quotas
([regional quota guidance][azure-regional-quota]); the
[Quota API][azure-quota-api] can read current limits and usage. The proof never
submits an increase request.

Retail pricing is an estimate, not a billing oracle. The
[Retail Prices API][azure-retail-prices] returns public USD rates and exact ARM
SKU/region fields. Missing, paginated-but-unread, ambiguous, stale, Spot-only,
or non-USD price data is a hard refusal. Azure Cost Management data can lag,
so budgets and alerts are secondary controls, never the runtime kill switch.

Only one VM and one GPU are allowed per run. Spot is off by default because
eviction can make a correct proof inconclusive. When `ALLOW_SPOT=1`, an
eviction still fails the proof and triggers cleanup; the harness does not
repeat the workload.

## 6. ZDD and bounded lifecycle

The proof must not restart, resize, reconfigure, route traffic away from, or
destroy any pre-existing Gludd or Azure resource. It provisions an isolated
parallel slice:

- a unique run ID and resource group named `gludd-proof-<run-id>`;
- a proof-only tenant, model profile, role, state directory, and telemetry
  namespace;
- a dedicated non-production Azure subscription;
- no public inference or profiler endpoint and no public SSH rule; and
- tags for `gludd-proof=true`, `proof-id`, `git-sha`, `owner`, `created-at`,
  `expires-at`, and `max-cost-usd`.

The endpoint becomes routable only after VM instance view, driver, container,
and `/v1/models` readiness gates pass. It is removed from routing before
teardown begins. Existing profiles remain untouched throughout, which makes
the operation ZDD for every production consumer.

Use Azure managed Run Command over the VM-agent control channel for the
proof-only control script and raw-trace upload. This avoids opening SSH or
vLLM to the Internet. The controller downloads the raw trace before teardown,
then adds the deletion evidence and builds the final archive outside Azure.
Microsoft documents that
[managed Run Command][azure-managed-run-command] executes scripts through the
VM agent; the implementation must set explicit command timeouts and use a
proof-owned output blob because console output is not an evidence store.

The state machine is monotonic:

```text
NEW -> PREFLIGHTED -> LEASED -> PROVISIONED -> READY
    -> PROFILE_REGISTERED -> WORKLOAD_PROVED -> PROFILE_REMOVED
    -> DEALLOCATED -> DESTROYED -> PASS
```

Any failure moves to `CLEANUP_REQUIRED`, never directly to `PASS` or a terminal
failure. `PASS` is legal only after the resource-group GET returns `404`, the
proof profile is absent, the lease is closed, and the local state records no
owned resource. Azure's documented resource-group cleanup sequence is delete
then wait for the group to be
[fully deleted][azure-group-cleanup].

Cleanup has three independent layers:

1. `try/finally` plus termination-signal handlers deregister, deallocate, and
   delete;
2. an Azure platform auto-shutdown schedule is created in the same apply and
   verified before model download begins; it deallocates the VM no later than
   `expires-at`; and
3. a durable reconciler, healthy before provisioning, scans only allowlisted
   subscriptions for expired, correctly signed `gludd-proof` leases,
   deallocates exact recorded VM IDs, deletes exact recorded resource-group
   IDs, and records the result.

Azure states that deallocation releases compute resources and stops compute
billing ([deallocate API][azure-deallocate]); auto-shutdown is therefore an
independent cost brake, not proof of full deletion. The reconciler must never
construct a delete target from a prefix, wildcard, environment-variable
default, or tag search alone. It compares the subscription, full resource ID,
run ID, lease signature, and ownership tags before acting.

## 7. Provisioning and readiness proof

Live implementation must route `DeploymentManager` to one converged
`azure-vllm` stack rather than generate a second, independent Azure topology.
The stack must:

- pin provider, OS image, NVIDIA driver, CUDA-compatible vLLM image, model, and
  Gludd artifacts by immutable versions and digests;
- launch vLLM with `--generation-config vllm` so a model repository cannot
  override the proof's fixed sampling contract;
- use `Standard_NC24ads_A100_v4` unless an exact policy-approved comparable SKU
  was requested;
- use Regular priority unless Spot was explicitly acknowledged;
- output the real Azure VM resource ID, NIC private IP, profile ID, and
  endpoint URL;
- install a driver/OS combination allowed by Microsoft's current matrix;
- keep Secure Boot disabled for the extension path unless the selected signed
  driver path explicitly supports it;
- compose and activate the cost/TTL watchdog;
- expose vLLM and its profiler endpoints only inside the disposable VM; and
- emit continuous, phase-marked progress without logging tokens or secrets.

Azure's [NVIDIA GPU Driver Extension guidance][azure-driver-extension] notes
that installation can reboot the VM, needs outbound Internet access, does not
automatically update, has OS/kernel compatibility constraints, and records
detailed status in `/var/log/azure/nvidia-vmext-status`. Therefore ARM
`ProvisioningState/succeeded` is only the start of readiness, not its end.

Readiness requires all of:

- Azure VM instance view shows `ProvisioningState/succeeded` and
  `PowerState/running` ([instance-view API][azure-instance-view]);
- the VM agent and managed Run Command are ready;
- the NVIDIA extension reports success and its full diagnostic log is
  captured;
- `nvidia-smi` reports exactly the policy GPU count, expected device name,
  UUID, driver, VRAM, and no Xid/ECC fatal error;
- CUDA runtime and framework compatibility checks pass;
- the digest-pinned vLLM container is running with exactly the requested GPU;
- `/health` and `/v1/models` return success and the exact model ID; and
- the registrar creates an isolated deploy profile using the authoritative
  VM record and the standard trusted-deploy SSRF path described by
  D-DEPLOY.3.

Readiness gets one 15-minute deadline with an observable heartbeat. There is
no blind sleep and no server-start retry that could create a second workload.

## 8. Exactly-one-token workload and accelerator attribution

The workload is one request through Gludd's normal
`ModelGateway.call_model_by_role` path, not a direct `curl` that bypasses
routing. The proof role has only the proof profile, so fallback cannot hide a
failure.

The committed workload fixture fixes:

- model artifact digest and tokenizer digest;
- prompt token IDs and prompt length;
- `temperature=0`, one candidate, `min_tokens=1`, and `max_tokens=1`;
- one request ID derived from the run ID; and
- a 120-second request deadline.

The prompt is long enough to span multiple 100 ms telemetry intervals but has
a fixed maximum input length and a precomputed hash. "One token" means exactly
one newly generated output token; prompt prefill is allowed. There is no warmup
completion and no automatic request retry. Model loading and health probes
must not invoke generation.

The response must contain the expected request ID, the exact model ID, a
finish reason compatible with the one-token limit, and exactly one new token
ID after tokenization. A non-empty string alone is insufficient because one
string can contain zero or multiple model tokens.

Accelerator attribution requires three independent evidence planes within the
same monotonic request interval:

1. **Gludd plane:** gateway dispatch and response events identify the proof
   profile, request ID, timestamps, model digest, input-token count, and
   exactly one output token.
2. **Framework plane:** the proof-only vLLM server enables its supported
   `/start_profile` and `/stop_profile` flow. The worker trace must contain at
   least one positive-duration CUDA kernel and positive CUDA memory activity
   after profile start and before the response. vLLM documents these
   [profiling endpoints][vllm-profiling], and PyTorch documents CUDA activity
   in [`torch.profiler`][pytorch-profiler].
3. **Device plane:** the host records the vLLM compute PID on the expected GPU
   UUID with positive device memory, plus a DCGM sample window containing
   non-zero graphics/compute-engine or SM activity. DCGM explicitly describes
   these counters as interval averages
   ([DCGM profiling][dcgm-profiling]), so the trace is the authoritative
   kernel proof and DCGM is independent corroboration.

Before profile start, the harness asserts that no unrelated GPU compute
process exists. All timestamps use the guest's monotonic clock, while UTC
timestamps are retained only for cross-system correlation. A device listing,
model load, positive memory use without a request, CPU event, stale DCGM
sample, or profiler trace without a CUDA kernel fails attribution.

Profiler endpoints are enabled only inside the disposable proof network and
removed with the VM. They must never be exposed on a production endpoint;
vLLM's [security guidance][vllm-security] classifies them as development
interfaces.

## 9. Evidence bundle

Write `azure-accelerator-proof-<run-id>.tar.zst`, a detached SHA-256 file, and
an OIDC-signed `manifest.dsse.json`. Its canonical `manifest.json` contains:

- schema version, run ID, exact 40-character Git SHA, source-tree status, and
  Gludd artifact digest;
- identity issuer and subject, with tenant/subscription represented by
  approved non-secret fingerprints;
- selected region/SKU/claim, all attempted regions, Resource SKU response
  digest, and actual instance-view SKU;
- quota name/current/limit/required tuples for regional and family quotas;
- price meter ID, effective date, hourly price, computed ceiling, operator
  ceiling, timeout, and Spot policy;
- Azure resource-ID hashes and non-secret resource names, deployment
  operation/correlation IDs, timestamps, and ownership tags;
- driver, CUDA, vLLM, model, tokenizer, device-name, UUID, VRAM, and compute
  capability evidence;
- redacted Gludd request/response metadata and exact input/output token
  counts;
- hashes of the vLLM CUDA trace, DCGM samples, `nvidia-smi` samples, readiness
  logs, extension log, and activity-log slice; and
- deregistration, deallocation, delete-request, delete-wait, final `404`,
  lease closure, and cleanup-reconciler evidence.

Every phase has `status`, `started_monotonic_ns`, `ended_monotonic_ns`,
`evidence_sha256`, and a machine-readable failure code. The validator rejects
missing, duplicated, out-of-order, future, cross-run, cross-SHA, or
checksum-invalid evidence.

The private durable cleanup lease retains encrypted full resource IDs; those
IDs are not copied into the evidence archive. Secrets, bearer tokens, client
secrets, SAS query strings, prompt text, unredacted subscription IDs,
environment dumps, Terraform state, and cloud-init secret material are
forbidden. Run a fresh secret scan over the unpacked archive before declaring
it valid.

Raw VM traces must reach the controller before Azure deletion. The controller
adds final deletion evidence, validates the completed archive, and only then
uploads it as a hosted artifact. Sign the manifest as a DSSE/in-toto
attestation using the hosted workload's OIDC identity; a detached checksum
alone is not sufficient release evidence. The GitHub artifact name includes
the exact Git SHA, but the SHA inside the verified attestation is
authoritative.

## 10. Release evidence rule

A release gate may consume this proof only when:

- `result == "PASS"` and `cleanup_verified == true`;
- manifest Git SHA equals the release commit exactly;
- requested claim equals the release claim (`azure-a100-80gb` for A100);
- the evidence validator and secret scan pass on the downloaded archive;
- the proof completed within the prior 24 hours; and
- its hosted workflow conclusion is success.

Missing credentials, quota, capacity, price, or live-environment approval
means "not proven," never "skipped green." The ordinary PR pipeline remains
cloud-free; the chargeable job is manual, environment-protected, and
concurrency-limited to one.

## 11. Exact acceptance tests

### Unit tests

Create `tests/unit/test_azure_accelerator_proof.py` with these required cases:

1. `LIVE=0` performs zero Azure writes; `LIVE=1` without the exact cost
   acknowledgement performs zero writes.
2. Missing/ambiguous price, disallowed subscription/region/SKU, restriction,
   insufficient regional quota, insufficient family quota, timeout over 45
   minutes, floating image/model, and Spot without acknowledgement each fail
   before resource-group creation.
3. The selector never substitutes a comparable SKU for an A100 claim and
   never falls back to CPU.
4. Only the three named capacity codes can advance to the next approved
   region; every other error stops, and cleanup precedes a region transition.
5. State transitions reject skips, repeats, stale run IDs, and `PASS` before
   verified deletion.
6. Every exception and termination signal enters cleanup; a cleanup failure
   overrides workload success with exit `5`.
7. The deletion guard rejects prefix-only, tag-only, wrong-subscription,
   wrong-resource-ID, expired-signature, and unowned targets.
8. Evidence validation rejects wrong SHA/SKU/GPU, zero CUDA kernels, stale
   DCGM windows, zero/multiple output tokens, hidden fallback, missing
   deregistration, missing final `404`, checksum drift, and secret-like data.

### Integration tests

Create `tests/integration/test_azure_accelerator_proof.py` using recorded,
secret-free ARM fixtures and real Gludd deployment/registration/router code:

1. a full fake-ARM lifecycle reaches `PASS` only after route removal and an
   asynchronous delete eventually returns `404`;
2. an allocation failure in region one cleans fully, provisions region two
   once, and records both attempts;
3. ARM success plus driver failure never becomes ready;
4. `/v1/models` success plus a CPU-only completion fails accelerator
   attribution;
5. a different gateway profile handling the request is detected as fallback;
6. a valid one-token response plus a CUDA trace and matching DCGM/NVML window
   passes workload proof; and
7. controller death leaves a lease that the reconciler deallocates and deletes
   without touching an adjacent untagged resource.

Normal validation is:

```text
make test-files TESTFILES='tests/unit/test_azure_accelerator_proof.py tests/integration/test_azure_accelerator_proof.py'
```

### Live acceptance

The protected hosted job runs exactly:

```text
make azure-accelerator-live-proof LIVE=1 ACK_COST=I_ACCEPT_AZURE_ACCELERATOR_CHARGES AZURE_SUBSCRIPTION_ID=<proof-subscription> REGIONS=<approved-regions> SKU=Standard_NC24ads_A100_v4 SKU_ALLOWLIST=config/azure_accelerator_proof.yml MAX_COST_USD=<ceiling> TIMEOUT_MINUTES=45 ALLOW_SPOT=0 MODEL_BUNDLE=<digest-uri> GLUDD_ARTIFACT=<exact-sha-digest-uri> EVIDENCE_DIR=.artifacts/azure-proof
```

Acceptance requires literal terminal markers:

```text
AZURE_ACCELERATOR_WORKLOAD_PROVED tokens_out=1
AZURE_ACCELERATOR_PROFILE_REMOVED
AZURE_ACCELERATOR_VM_DEALLOCATED
AZURE_ACCELERATOR_RESOURCE_GROUP_DELETED
AZURE_ACCELERATOR_PROOF_PASS cleanup_verified=true
```

Afterward, a separate read-only verifier downloads the workflow artifact,
checks the manifest against the hosted run SHA, rechecks that the resource
group is absent, and prints:

```text
AZURE_ACCELERATOR_EVIDENCE_PASS
```

No implementation may claim Azure accelerator readiness without both final
markers.

## 12. Long-lived operator reports and design consequences

These reports are not substitutes for primary documentation. They identify
failure modes that repeatedly surprise operators and therefore deserve
mechanical coverage:

| Report | Observed failure | Required design consequence |
|---|---|---|
| [Azure Q&A: use GPU instances from another region (2022)][forum-region] | GPU families are region-limited and may require per-region vCPU quota. | Region is explicit; quota is checked for every candidate region. |
| [Azure Q&A: A100 allocation failed despite requested quota (2025)][forum-a100-capacity] | `Standard_NC24ads_A100_v4` could not allocate because the requested size lacked capacity. | Treat quota, SKU restriction, and live capacity as three distinct gates. |
| [Azure Q&A: NCads A100 family shows 0 of 0 quota (2024)][forum-a100-zero-quota] | New subscriptions can expose zero family quota. | Abort read-only preflight; never attempt provisioning or request quota automatically. |
| [WALinuxAgent issue #1938 (opened 2020)][forum-cloud-init-race] | VM agent/extensions and cloud-init contended for package management during boot. | ARM success is not readiness; wait for driver, agent, container, and endpoint independently. |
| [DCGM exporter issue #328 (2024)][forum-dcgm-module] | A100/MIG profiling metrics were absent when the DCGM profiling module did not load. | Discover supported metric groups, preserve diagnostics, and require the independent CUDA trace rather than fabricating zero-valued telemetry. |
| [Azure Q&A: A100 driver install repeatedly fails (2025)][forum-a100-driver] | The driver extension can fail on an A100 VM even after allocation. | Pin the supported OS/driver pair, capture extension logs, and destroy on driver-readiness failure. |

Together these reports explain why a single "deployment succeeded" bit is
insufficient. The proof deliberately separates identity, SKU policy, two
quotas, capacity allocation, driver readiness, service readiness, gateway
routing, CUDA execution, and cleanup.

## 13. Primary sources

- [Azure NC A100 v4 sizes][azure-a100-size]
- [Azure Resource SKUs REST API][azure-resource-skus]
- [Azure regional and VM-family quota guidance][azure-regional-quota]
- [Azure Quota REST API][azure-quota-api]
- [Azure VM allocation-failure guidance][azure-allocation-failure]
- [Azure Retail Prices API][azure-retail-prices]
- [Azure NVIDIA GPU Driver Extension for Linux][azure-driver-extension]
- [Azure managed Run Command for Linux][azure-managed-run-command]
- [Azure VM instance-view API][azure-instance-view]
- [Azure VM auto-shutdown][azure-auto-shutdown]
- [Azure VM deallocate API][azure-deallocate]
- [Azure resource-group cleanup and delete wait][azure-group-cleanup]
- [vLLM online-serving profiling endpoints][vllm-profiling]
- [vLLM OpenAI-compatible server and generation configuration][vllm-openai]
- [vLLM server security guidance][vllm-security]
- [PyTorch profiler CUDA activity][pytorch-profiler]
- [NVIDIA DCGM profiling metrics][dcgm-profiling]
- [NVIDIA System Management Interface][nvidia-smi]

[azure-a100-size]: https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nca100v4-series
[azure-resource-skus]: https://learn.microsoft.com/en-us/rest/api/compute/resource-skus/list
[azure-regional-quota]: https://learn.microsoft.com/en-us/azure/quotas/regional-quota-requests
[azure-quota-api]: https://learn.microsoft.com/en-us/rest/api/quota/
[azure-allocation-failure]: https://learn.microsoft.com/en-us/troubleshoot/azure/virtual-machines/windows/allocation-failure
[azure-retail-prices]: https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices
[azure-driver-extension]: https://learn.microsoft.com/en-us/azure/virtual-machines/extensions/hpccompute-gpu-linux
[azure-managed-run-command]: https://learn.microsoft.com/en-us/azure/virtual-machines/linux/run-command-managed
[azure-instance-view]: https://learn.microsoft.com/en-us/rest/api/compute/virtual-machines/instance-view
[azure-auto-shutdown]: https://learn.microsoft.com/en-us/azure/virtual-machines/auto-shutdown-vm
[azure-deallocate]: https://learn.microsoft.com/en-us/rest/api/compute/virtual-machines/deallocate
[azure-group-cleanup]: https://learn.microsoft.com/en-us/cli/azure/azure-cli-vm-tutorial-6
[vllm-profiling]: https://docs.vllm.ai/en/latest/serving/online_serving/#profiling-apis
[vllm-openai]: https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/
[vllm-security]: https://docs.vllm.ai/en/latest/usage/security/
[pytorch-profiler]: https://docs.pytorch.org/docs/stable/profiler.html
[dcgm-profiling]: https://docs.nvidia.com/datacenter/dcgm/latest/learn/modules/profiling.html
[nvidia-smi]: https://docs.nvidia.com/deploy/nvidia-smi/index.html
[forum-region]: https://learn.microsoft.com/en-us/answers/questions/692301/use-gpu-instances-from-another-region
[forum-a100-capacity]: https://learn.microsoft.com/en-us/answers/questions/2143000/allocation-failed-for-standard-nc24ads-a100-v4-com
[forum-a100-zero-quota]: https://learn.microsoft.com/en-us/answers/questions/2133321/i-need-to-create-nc24ads-a100-series-gpu-but-when
[forum-cloud-init-race]: https://github.com/Azure/WALinuxAgent/issues/1938
[forum-dcgm-module]: https://github.com/NVIDIA/dcgm-exporter/issues/328
[forum-a100-driver]: https://learn.microsoft.com/en-nz/answers/questions/2337728/continuously-fail-to-install-nvidia-driver-to-my-v
