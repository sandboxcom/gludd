# attest_model_worker

Runs the provider-neutral, read-only guest attestation boundary before a model
worker candidate is started. It consumes the same schema-v1 `RunnerLaunchPlan`
used by `model_worker`, combines it with controller-selected hardware and
runtime expectations, and publishes `gludd_model_worker_attestation` only after
the maintained vendor probe has run on the managed host.

This role is shared infrastructure. self-improvement, chemistry, coding,
materials, and other Gludd tasks all use the same admitted model services. The
role has no cloud or workload branches; local hosts, Slurm nodes, Azure workers,
and future providers pass the same inputs.

## Required immutable inputs

- `gludd_model_worker_plan`
- `gludd_model_worker_attestation_backend`
- `gludd_model_worker_attestation_minimum_memory_mib`
- `gludd_model_worker_attestation_required_interconnect`
- `gludd_model_worker_attestation_expected_topology_digest`
- `gludd_model_worker_attestation_runtime_probe`
- `gludd_model_worker_attestation_expected_runtime_version_digest`

The worker image must already contain Gludd plus the selected maintained vendor
library: NVIDIA `nvidia-ml-py`/NVML or AMD SMI. The role never downloads drivers
or packages. Other accelerator kinds register a `SnapshotProbe` in the same
engine; they do not add provider-specific logic here.

Credentials are not role variables. OpenBao delegates any later component
credential through a separate in-memory channel; the version probe and returned
fact remain credential-free.

## ZDD boundary

Attestation is side-effect free and runs before the candidate service. The
`model_worker` role then launches a release-addressed candidate, waits for
health, publishes candidate evidence, and leaves the prior service available.
Only a separate retirement step removes the exact prior unit and its artifacts.

The rationale, vendor references, and long-lived operator reports are recorded
in `docs/features/MODEL_WORKER_ATTESTATION.md`.
