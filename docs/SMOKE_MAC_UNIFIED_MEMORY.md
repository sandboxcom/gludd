# Local Unified-Memory Sparse-Model Smoke Test

`mac_unified_memory_smoke.py` is a bounded, credential-free command-line
harness for validating local model execution. It never contacts a provider and
does not download a model. The deterministic model is deliberately small and
has an explicit zero-weight ratio, so the command can be used before attempting
a large sparse model on a workstation.

## macOS Apple Silicon

Install a native arm64 PyTorch build in the environment used by Gludd, then
inspect the limits without allocating tensors:

```text
python scripts/mac_unified_memory_smoke.py --dry-run --backend mps
```

Run the bounded MPS test and capture JSON telemetry (latency, samples/sec,
sparsity, MPS allocated/driver bytes):

```text
python scripts/mac_unified_memory_smoke.py --live --backend mps \
  --allow-cpu  # only affects auto fallback; an explicit mps request still fails closed
```

An explicit `mps` request exits nonzero when Metal/MPS is not built or is not
available. This prevents a false green result caused by an accidental CPU
fallback. The model budget is checked before loading tensors and reserves 20%
of detected capacity by default (`GLUDD_SMOKE_HEADROOM`).

Apple Silicon uses **unified memory**: the MPS capacity is the system memory,
not a separate VRAM pool. The JSON `memory_policy.kind` is `unified` and its
capacity is obtained from the host. Because MPS and the operating system share
that pool, do not run a model when `model_fit.fits` is false, when swap pressure
is high, or when the model's real tokenizer/KV-cache footprint is larger than
the reported dense estimate. Sparsity reduces arithmetic but does not make a
dense PyTorch tensor consume compressed storage.

## Linux on a Mac or in a container

MPS is a macOS framework and is not exposed by a normal Linux VM or container.
Use the dry run to document the fallback and run a bounded CPU functional check:

```text
python scripts/mac_unified_memory_smoke.py --dry-run --backend cpu
python scripts/mac_unified_memory_smoke.py --live --backend cpu
```

For a Linux container with a passed-through NVIDIA device, install a CUDA
PyTorch build and use `--backend cuda`; the policy reports `discrete` memory
and queries device VRAM. ROCm-backed PyTorch is reported as `rocm` when its
HIP runtime is visible. A Linux `--backend mps` invocation fails closed rather
than pretending that the container can access Metal.

## Memory-fit policy

The harness reports fp32 and fp16 dense-storage estimates, the effective budget
after headroom, and a recommendation. Set `GLUDD_SMOKE_MODEL_PARAMS` when
screening a real model before loading it. Prefer a smaller or quantized model
when the recommendation says **do not run this model**; do not bypass the
budget by setting an unbounded value. The default hidden size, batch, and step
limits are intentionally bounded for shared development hosts.

Use the `memory_policy.model_guidance` field as the device rule: unified-memory
hosts prefer a single small/quantized model (normally 3B-7B Q4), while long
contexts, concurrent models, and dense 13B+ models are rejected when the shared
pool is pressured. A discrete-VRAM result may prefer larger throughput-oriented
7B-34B Q4/Q8 models, but only after the reserved-VRAM fit check passes. Unknown
capacity always means **do not run** until the backend and capacity are proven.

## Telemetry and long-lived issues

The output is local JSON and contains no credentials. MPS memory accounting has
known platform-version sensitivity: Apple Developer Forum thread
<https://developer.apple.com/forums/thread/824753> describes MPS reporting
large “other allocations” on unified-memory machines, and PyTorch Forum thread
<https://discuss.pytorch.org/t/current-state-of-mps/172212> documents operator
coverage and CPU-fallback surprises. The harness therefore records both MPS
allocated and driver memory, requires explicit backend selection for live GPU
checks, and fails closed when capability or fit checks cannot be proven.
