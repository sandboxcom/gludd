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

Screen a candidate model before loading it by supplying its parameter count,
budget, and reserved headroom directly on the CLI. A non-fitting selection
returns exit code 3 and does not allocate model tensors:

```text
python scripts/mac_unified_memory_smoke.py --dry-run --backend mps \
  --model-parameters 7000000000 --max-memory-gb 24 --headroom 0.2
```

The same selection command works in a Linux VM/container on the Mac. Use
`--backend cpu` for a deterministic fallback, or `--backend cuda` only when a
passed-through discrete GPU is present:

```text
python scripts/mac_unified_memory_smoke.py --dry-run --backend cpu \
  --model-parameters 100000000 --max-memory-gb 8
python scripts/mac_unified_memory_smoke.py --dry-run --backend cuda \
  --model-parameters 1000000000 --max-memory-gb 16
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

## Telemetry and long-lived issues

The output is local JSON and contains no credentials. MPS memory accounting has
known platform-version sensitivity: Apple Developer Forum thread
<https://developer.apple.com/forums/thread/824753> describes MPS reporting
large “other allocations” on unified-memory machines, and PyTorch Forum thread
<https://discuss.pytorch.org/t/current-state-of-mps/172212> documents operator
coverage and CPU-fallback surprises. The harness therefore records both MPS
allocated and driver memory, requires explicit backend selection for live GPU
checks, and fails closed when capability or fit checks cannot be proven.
