# Local AMD/NVIDIA GPU smoke tests

`scripts/gpu_hardware_smoke.py` is the command-line smoke path for physical
devices. It does not call Azure, RunPod, or any other cloud API. CI uses the
default dry-run to validate the bounded workload; an operator runs `--live`
on the target host to exercise the actual accelerator.

## What it verifies

The live check loads the local PyTorch build, confirms that a CUDA or ROCm
device is visible, records the device name and total memory, and performs a
small sparse-linear inference workload. It reports mean latency,
iterations/second, allocated/reserved memory, and the host diagnostics. The
workload is bounded to 32-4096 dimensions, 1-20 iterations, and at least 50%
sparsity so a smoke run cannot accidentally allocate an entire unified-memory
machine.

Both Linux and Windows are supported. NVIDIA systems use a CUDA-enabled
PyTorch build. AMD systems, including ASUS systems with supported Radeon or
Instinct accelerators, use a ROCm-enabled PyTorch build. The harness identifies
the GPU through PyTorch and optionally captures `nvidia-smi` and `rocminfo`
output; it never treats a CPU fallback as a passing GPU test.

## Memory policy and model fit

`src/general_ludd/hardware_memory_policy.py` is shared by local hardware
smokes. It distinguishes discrete VRAM from integrated/unified memory, keeps a
20% runtime headroom reserve by default, estimates quantized model footprints,
and rejects a requested model when its estimated footprint exceeds the usable
budget. Use `--model-params-b` and `--quant-bits` to enforce this check:

```text
python scripts/gpu_hardware_smoke.py --live --model-params-b 7 --quant-bits 4
```

The JSON includes a conservative list of 3B/7B/13B/34B Q4 models that fit.
Unknown capacity is reported as `unknown`, never as a fit. Do not run a model
when the policy says `reject`, when the driver reports less capacity than the
model estimate, or when unified-memory pressure is already high; reduce the
model/quantization or close other workloads first. The estimate covers weights
plus a 20% runtime overhead, but long contexts and multiple concurrent models
need additional headroom.

## Linux

Dry-run (safe on a developer laptop):

```text
python scripts/gpu_hardware_smoke.py --backend auto
```

Actual NVIDIA CUDA device:

```text
python scripts/gpu_hardware_smoke.py --live --backend cuda --size 256 --iterations 3 --sparsity 0.90
```

Actual AMD ROCm device:

```text
python scripts/gpu_hardware_smoke.py --live --backend rocm --size 256 --iterations 3 --sparsity 0.90
```

Install a PyTorch wheel matching the driver before running live mode. Keep
the driver and ROCm/CUDA versions aligned; a common long-lived failure is an
installed GPU driver paired with a CPU-only or incompatible PyTorch wheel.
PyTorch's installation selector and the ROCm compatibility matrix should be
used for the exact host rather than copying a wheel from a different GPU
generation.

## Windows

Run from PowerShell in the repository root:

```powershell
py -3 scripts/gpu_hardware_smoke.py --backend auto
py -3 scripts/gpu_hardware_smoke.py --live --backend cuda --size 256 --iterations 3 --sparsity 0.90
```

ROCm support on Windows depends on the specific AMD GPU and current ROCm
release. When the vendor's Windows ROCm support does not expose a PyTorch
CUDA device, the harness exits with code 3 instead of silently using the CPU;
run the Linux ROCm example on a supported installation.

## Exit codes and safety

* `0`: dry-run or live GPU inference passed.
* `2`: invalid CLI bounds or backend selection.
* `3`: PyTorch is unavailable, no GPU is visible, the backend mismatches, or
  the requested model exceeds the memory budget.
* `4`: the device was visible but sparse inference failed.

The test does not download models, alter drivers, provision resources, or send
telemetry. Save the JSON output with the host's change ticket when diagnosing
driver regressions. Forum reports over the long term consistently point to
driver/runtime mismatch, unsupported ROCm GPU/OS combinations, and unified
memory pressure as the recurring causes; keeping this bounded local probe
separate from cloud-provider smoke tests makes those failures actionable.

Long-lived reports that informed this guardrail include the [PyTorch forum
thread on CPU-only wheels despite an installed GPU](https://discuss.pytorch.org/t/torch-being-installed-with-cpu-only-even-when-i-have-a-gpu/135060),
the [PyTorch forum explanation of `torch.cuda.is_available()` returning false
while `nvidia-smi` works](https://discuss.pytorch.org/t/torch-cuda-is-available-returns-false-nvidia-smi-is-working/20614),
and the [ROCm/TheRock report on native Windows PyTorch support for newer AMD
GPUs](https://github.com/ROCm/TheRock/issues/5113). These reports are why the
harness checks the runtime build and refuses CPU fallback, treats Windows ROCm
as device/release dependent, and reserves memory instead of assuming every
byte reported by a unified-memory system is available to a model.
