# Universal model-worker attestation

Gludd admits a model worker only after the guest proves the exact accelerator
shape and runner revision selected by the controller. This is a
provider-neutral hardware/runtime boundary shared by local hosts, Slurm nodes,
Azure VMs and VM scale sets, and future providers. Self-improvement is one task
that can consume an admitted worker; chemistry, coding, materials, and other
collections use the same evidence path.

## Contract

The controller supplies an immutable expected topology digest, minimum device
count and memory, required interconnect, a tokenized runner version probe, and
the digest of its expected output. The guest returns only:

- readiness booleans and stable refusal codes;
- backend, device count, and interconnect class;
- topology, private-inventory, driver-version, and runner-version digests; and
- the already-public runner source revision.

GPU UUIDs, PCI addresses, model names, raw versions, command output, and vendor
errors remain on the guest. A driver probe failure prevents the runner command
from executing. The registry accepts additional `SnapshotProbe` implementations,
so TPU, Intel XPU, Apple Metal, or a future accelerator does not require an
Azure or self-improvement branch in the admission engine.

NVIDIA discovery uses NVIDIA's maintained `nvidia-ml-py` NVML bindings. NVML is
the underlying management API for `nvidia-smi`; the documented
`nvidia-smi topo -m` matrix supplies the guest's NVLink-versus-PCIe
classification. AMD discovery uses the maintained AMD SMI Python API rather
than parsing human CLI output; AMD documents AMD SMI as the replacement for
the deprecated `rocm_smi` CLI and exposes VRAM, driver, UUID/BDF, and XGMI
queries through the library.

The controller engine is vendored byte-for-byte into the agent collection's
`plugins/module_utils` runtime. Ansible transfers that collection-owned copy to
the managed host, so a newly provisioned worker does not need the Gludd control
plane package or repository checkout. One parametrized behavioral suite runs
against both artifacts and an exact-source parity assertion prevents either
copy from drifting independently.

Primary references:

- <https://docs.nvidia.com/deploy/nvml-api/latest/>
- <https://docs.nvidia.com/deploy/nvidia-smi/>
- <https://pypi.org/project/nvidia-ml-py/>
- <https://rocm.docs.amd.com/projects/amdsmi/en/latest/reference/amdsmi-py-api.html>
- <https://rocm.docs.amd.com/projects/amdsmi/en/develop/how-to/amdsmi-cli-tool.html>

## Why driver and runner evidence are separate

Long-lived operator reports show that a healthy host probe does not prove a
usable model runtime:

- NVIDIA container-toolkit issue
  <https://github.com/NVIDIA/nvidia-container-toolkit/issues/48> documents
  containers losing NVML access even though the node had been configured;
- vLLM issue <https://github.com/vllm-project/vllm/issues/33041> reports a
  multi-GPU process hanging after NCCL initialization; and
- Ollama issue <https://github.com/ollama/ollama/issues/7047> reports uneven
  memory placement across multiple GPUs.

Accordingly, host driver/device evidence, topology equality, the exact runner
version probe, and post-launch health are independent gates. A passing NVML or
AMD SMI call alone is never treated as application readiness.

## ZDD sequence

1. Probe and hash the candidate host without changing it.
2. Reject any device, memory, interconnect, topology, or runtime revision drift.
3. Start a release-addressed candidate through the universal `model_worker`
   role while the prior service remains available.
4. Wait for candidate health and publish its content-free attestation.
5. Shift work only after external health and dispatch evidence pass.
6. Retire the exact prior service and its environment/attestation artifacts.

No probe installs packages, downloads a driver, executes through a shell, or
contains credentials. Immutable worker images own the maintained vendor
libraries; the Ansible bootstrap layer verifies them before admission.
