# Provider-neutral accelerator discovery

Gludd exposes one normalized, read-only inventory for local accelerators and
Slurm scheduler resources. Discovery never provisions compute, starts a model,
or turns an observed device name into a configuration key.

## Support matrix

| Runtime | Discovery source | Capacity source | Current behavior |
| --- | --- | --- | --- |
| Apple Metal GPU | macOS `system_profiler` through `HardwareSurvey` | Reported VRAM, or the existing bounded unified-memory estimate when macOS omits VRAM | Reports each device as `gpu`, backend `metal`, vendor `apple` |
| Intel GPU | PyTorch `torch.xpu` | `get_device_properties(index).total_memory` | Reports every available XPU device; an absent or CPU-only PyTorch build is unavailable, not an empty-memory guess |
| NVIDIA GPU | Existing `nvidia-smi` survey | Driver-reported device memory | Reports each device as backend `nvidia` |
| AMD GPU | Existing `rocm-smi` survey | Driver-reported VRAM | Reports each device as backend `rocm` |
| TPU | JAX `devices()` | `memory_stats().bytes_limit` when the runtime provides it | Reports devices whose platform or kind identifies TPU; missing capacity remains `null` |
| Slurm GPU or TPU | Existing `SlurmAdapter`, using `sinfo --json` locally or authenticated `slurmrestd` remotely | Scheduler-declared GRES count; memory only when encoded in the GRES name | Subtracts `gres_used`, rejects unhealthy nodes as available capacity, and preserves node and partition facts |

An Apple Neural Engine is not called a TPU. The current inventory covers the
Apple Metal GPU; it does not claim ANE/Core ML capacity. Intel discovery covers
the XPU GPU runtime; it does not claim Gaudi HPU or an Intel NPU unless a future
runtime adapter can report that accelerator with verified capacity.

## Interfaces

- `make local-accelerator-inventory` prints the exact local JSON inventory to
  standard output while streaming content-free discovery progress to standard
  error. It is inspection-only and accepts no provisioning option.
- `GET /admin/hardware/accelerators` returns the daemon's cached local
  inventory. The daemon reuses its startup GPU snapshot, so it does not run a
  second `system_profiler` or duplicate an already-observed XPU probe.
- `GET /admin/slurm/hardware` obtains live Slurm node inventory through the
  configured local client or REST controller.
- `general_ludd.agent.gludd_accelerator_facts` reads either endpoint, or both,
  and publishes `ansible_facts.gludd_accelerators`.
- The `general_ludd.agent.discover_accelerators` role exposes the same fact for
  playbook decisions without changing the managed host.

Every resource uses the same schema: kind, location, backend, observed model,
observed vendor, stable resource key, total and available counts, capacity,
source, node, and partitions. New device names remain values in `resources`;
there is no growing collection of T4, A100, Apple, or Intel configuration keys.

## Safety and observability

Local discovery emits bounded, content-free events for discovery start,
source completion, unavailable optional runtimes, source failure, and discovery
completion. The daemon logs only the event, source, and count. It never logs
device payloads, credentials, model text, or project content.

Optional local runtimes fail soft and remain observable: a missing XPU-enabled
PyTorch or TPU-enabled JAX installation cannot hide a Metal, NVIDIA, or AMD
device that was already found. A configured Slurm controller failure fails
closed and propagates; it is not converted to a misleading empty cluster.
Counts, strings, collection sizes, device memory, job state, and available
capacity are bounded before serialization.

Discovery is intentionally separate from allocation. An empty todo queue does
not cause either endpoint or the Ansible role to provision anything. Scheduling
may consume the facts later, but a resource with unknown memory must not be
selected for a memory-constrained model without a runtime smoke test or an
explicit scheduler constraint.

## Operational limits

Apple unified memory is not dedicated VRAM. Apple's
`recommendedMaxWorkingSetSize` is an approximate performance-safe working set,
and PyTorch exposes the same value as `torch.mps.recommended_max_memory()`.
The legacy survey fallback currently estimates a bounded share when macOS does
not print VRAM; model-fit evidence still requires a live allocation/inference
smoke. PyTorch users also report MPS caching and leak behavior that can make a
static capacity number optimistic.

An Intel GPU is discoverable only when the installed PyTorch build supports
XPU. A CPU-only build can see no XPU even on an Intel-GPU host. Similarly, JAX
may fall back to CPU when `jaxlib` or `libtpu` is incompatible; Gludd therefore
checks returned device platforms instead of treating an import as TPU proof.

Slurm GRES is the scheduler's declared inventory, not direct physical-device
attestation. `AutoDetect=nvml` mismatches can drain a node or expose fewer GPUs,
and MIG configurations make type and memory strings site-specific. Gludd uses
the controller's health and usage facts, keeps unknown capacity unknown, and
does not maintain a hardware-SKU table. A live model runner must still attest
the accelerator it actually received.

## Verification

The always-on unit and integration tests are hermetic. Fake PyTorch, JAX,
Slurm CLI, and Slurm REST boundaries exercise Apple, Intel, TPU, GRES usage,
unhealthy nodes, absent runtimes, malformed responses, API caching, daemon
wiring, and Ansible fact aggregation without requiring a GPU, TPU, Slurm
cluster, cloud credential, or paid resource in GitHub Actions. Live hardware
proof remains an explicit environment test and must report its runtime
attestation separately.

On the Apple M2 development host, the exact Make behavior found one Metal GPU
and emitted separate unavailable events for XPU and JAX TPU. It reported a
5.36 GiB planning budget from the host's 8 GiB unified-memory pool. The
separate backend smoke found that the installed PyTorch build has no MPS
support, demonstrating that physical discovery and runner attestation remain
distinct evidence.

## References and practitioner reports

Primary interfaces:

- [Apple `recommendedMaxWorkingSetSize`](https://developer.apple.com/documentation/metal/mtldevice/recommendedmaxworkingsetsize)
- [PyTorch `torch.mps.recommended_max_memory`](https://docs.pytorch.org/docs/stable/generated/torch.mps.recommended_max_memory.html)
- [PyTorch `torch.xpu.get_device_properties`](https://docs.pytorch.org/docs/stable/generated/torch.xpu.get_device_properties.html)
- [JAX device API](https://docs.jax.dev/en/latest/_autosummary/jax.Device.html)
- [Slurm generic resources](https://slurm.schedmd.com/gres.html)
- [Slurm REST API](https://slurm.schedmd.com/rest_api.html)

Long-lived operator reports that shape the fail-closed behavior:

- [CPU-only PyTorch build does not detect Intel XPU](https://discuss.pytorch.org/t/torch-cannot-detect-intel-xpu-device/224427)
- [JAX TPU falls back to CPU with incompatible runtime components](https://github.com/jax-ml/jax/issues/22070)
- [Slurm NVML discovery reports fewer GRES than configured](https://lists.schedmd.com/pipermail/slurm-users/2022-October/009280.html)
- [Slurm `AutoDetect=nvml` configuration mismatch](https://lists.schedmd.com/pipermail/slurm-users/2021-April/007127.html)
- [Slurm MIG accounting complexity](https://lists.schedmd.com/pipermail/slurm-users/2022-January/008328.html)
- [PyTorch MPS memory-cache pressure](https://github.com/pytorch/pytorch/issues/105839)
- [PyTorch MPS training memory leak](https://github.com/pytorch/pytorch/issues/121113)
