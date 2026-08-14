# Model Deployment Tuning — vLLM & llama.cpp (production)

> Companion to the **static** `MisconfigDetector` in
> `src/general_ludd/infra/model_deploy_check.py` and the spec in
> `docs/MODEL_DEPLOYMENT.md`. The detector flags *misconfigurations* against
> hardware facts; this guide explains *how to set the knobs in the first
> place*, why they matter, and how the detector's findings feed an
> automated remediation loop (Section 4) and observability stack (Section 5).
>
> Currency: best practices below reflect 2025/2026 vLLM (v0.8–v0.10+, V1
> engine) and current `llama.cpp` (`ggml-org/llama.cpp` master). Numbers are
> *starting points* — always confirm against your own benchmark and the live
> metrics in Section 5. Sources are listed at the end.

---

## 0. The single mental model: bandwidth is the ceiling

Autoregressive *decode* is **memory-bandwidth bound**, not compute bound. Each
generated token must stream the full set of active weights (plus the growing KV
cache) through the GPU's memory system once. So the first-order throughput
ceiling for a single decode stream is roughly:

```
tokens/s  ≈  memory_bandwidth (bytes/s)  /  bytes_read_per_token
bytes_read_per_token  ≈  model_weight_bytes + KV_bytes_touched
```

That is why a 70B model at FP8 (~70 GB read/token) lands near **~14 tok/s on a
4090 (~1.0 TB/s GDDR6X), ~48 tok/s on an H100 SXM (3.35 TB/s HBM3), and ~114
tok/s on a B200 (8.0 TB/s HBM3e)** for a single stream — the ratios track
bandwidth, not FLOPs. Two consequences drive every knob below:

1. **Quantize to cut bytes/token.** Halving weight bytes (BF16→FP8/INT8) nearly
   doubles the bandwidth-bound ceiling. This is the biggest single lever.
2. **Batch to amortize the weight read.** *Prefill* and large batches are
   compute bound; the weight bytes are read once and reused across many
   sequences/tokens in the batch. Continuous batching (vLLM) and parallel slots
   (llama.cpp) exist to push you from the latency regime (1 stream) toward the
   throughput regime (many streams sharing each weight read).

Everything else — KV-cache sizing, parallelism topology, CUDA graphs — is about
keeping the GPU fed so you actually hit that ceiling.

---

## 1. vLLM production tuning

vLLM serves with **PagedAttention + continuous batching**. The V1 engine
(default in recent releases) makes chunked prefill, prefix caching, and CUDA
graphs first-class. Tune in this order: memory → parallelism → batching →
quantization → graph/spec-decode.

### 1.1 `gpu_memory_utilization` (the master memory dial)

Fraction of *each* GPU's VRAM vLLM may claim for weights **+ KV-cache pool +
activations + CUDA-graph capture**. Default `0.90`.

- **Too high** (`> 0.95`): peak activation/graph allocations spill → CUDA OOM,
  often only under load. (Detector **rule a**, critical.)
- **Too low** (`< 0.50` on a dedicated GPU): the KV pool is starved, so
  `max_num_seqs`/`max_model_len` are silently capped and throughput collapses.
  (Detector **rule a**, warn.)
- **Practice:** start `0.90`; if the box is shared (other processes, a draft
  model, an embedding server) drop to `0.85`; only push `0.92–0.95` once you've
  watched `vllm:gpu_cache_usage_perc` stay under ~90% at peak (Section 5). Leave
  headroom — the figure is a *fraction of total*, and other processes' VRAM is
  not subtracted for you.

### 1.2 KV-cache budget: `max_model_len` × `max_num_seqs`

After weights are loaded, the remaining pool holds the KV cache. Per-token KV
bytes are:

```
KV_bytes ≈ 2 (K+V) · num_layers · num_kv_heads · head_dim · kv_dtype_bytes
```

…multiplied by `max_model_len × max_num_seqs` for the worst-case concurrent
footprint. The detector computes exactly this (`_kv_cache_bytes`) and fires
**rule b** (critical) when `need > pool`. Levers when you blow the budget:

- **`kv_cache_dtype: fp8`** — halves KV bytes (the `KV_DTYPE_BYTES=2` constant in
  the detector becomes effectively 1). On Hopper/Blackwell FP8 KV is near-free
  in quality and is the first reach. (Detector rule-b remediation sets this.)
- **Lower `max_model_len`** to the longest prompt+output you actually serve —
  don't pay KV budget for 128k if your p99 request is 8k.
- **Lower `max_num_seqs`** (see 1.4).
- **Enable prefix caching** (1.6) so shared prefixes don't re-allocate KV.

Sanity check `max_num_seqs` against VRAM: the detector's **rule l** flags
implausibly high `max_num_seqs` (> ~64 seqs/GiB) that can never fit KV — those
requests just queue and preempt, hurting tail latency.

### 1.3 Tensor / pipeline parallelism vs topology

- **Tensor parallel (TP)** shards every layer's matmuls across GPUs and does an
  **all-reduce every layer** → extremely bandwidth-hungry on the interconnect.
  TP is the right choice **only when GPUs share NVLink** (e.g. HGX/DGX H100,
  NVLink 4.0 ≈ 900 GB/s/GPU). On a PCIe-only box, per-layer all-reduce over
  ~32–64 GB/s PCIe can make TP=2 *slower than a single GPU*. (Detector **rule
  c**, warn, when `tp>1` and `has_nvlink` is false → remediation falls back to
  PP.)
- **Pipeline parallel (PP)** splits the model by layer ranges; GPUs exchange
  only activations at stage boundaries → **PCIe-tolerant**. Use PP across PCIe
  links, across NUMA nodes, and across hosts.
- **Sizing rules** (enforced by detector **rule c** / **rule k**):
  - `tensor_parallel_size` must be ≤ `gpu_count` **and divide it** (1, 2, 4, 8).
    Non-divisors are a hard config error (rule c, critical).
  - On a multi-GPU box with `tp==1 and pp==1`, extra GPUs sit idle (rule k,
    warn) — you must engage one of them.
  - Prefer **TP within a node** (NVLink), **PP across nodes** (network). For an
    8×H100 node serving one large model: `TP=8`. For 2 nodes: `TP=8, PP=2`.

### 1.4 Continuous batching: `max_num_seqs` & chunked prefill

- **`max_num_seqs`** caps concurrent sequences in a batch. Higher = more
  throughput until the KV pool is exhausted (then preemption). Typical
  production: 128–256 on an 80 GB card for ~7–13B; lower for long contexts.
  Tune *down* if `vllm:num_requests_waiting` climbs while KV usage is already
  ~90% (you're oversubscribed, not under-batched).
- **Chunked prefill** (`--enable-chunked-prefill`, default-on in V1): splits a
  long prompt's prefill into chunks and **interleaves it with ongoing decode**,
  so one 100k-token prompt can't monopolize an engine step and stall every
  short request's TPOT. Keep it on for any workload with mixed/long prompts.
  Tune `max_num_batched_tokens` (chunk budget): larger favors prefill
  throughput, smaller favors decode latency (TTFT/TPOT smoothness).

### 1.5 Quantization vs GPU architecture

Pick the weight format the silicon has kernels for — the detector's **rule e**
fires (critical) when you request `fp8` on hardware without native FP8 tensor
cores (`_FP8_ARCHES = {hopper, ada, blackwell}`), and **rule j** fires when you
request `bf16` on pre-Ampere (`compute_capability < 8.0`).

| Arch (examples) | Native FP8? | Recommended weight quant | Notes |
|---|---|---|---|
| **Hopper** (H100/H200) | yes (W8A8) | **FP8** (W8A8) | ~2× memory cut, up to ~1.6× throughput, minimal accuracy loss; FP8 KV-cache also cheap. |
| **Blackwell** (B200/GB200) | yes | **FP8** (and emerging FP4) | 2× FP8 FLOPs vs BF16; uses default FlashInfer kernel (no Hopper-style 2-stage accumulation hack). |
| **Ada** (L40S, RTX 4090/Ada) | yes (W8A8) | **FP8** | Ada has FP8 tensor cores; good fit for L40S inference nodes. |
| **Ampere** (A100, A10, 3090) | **no** (W8A16 only) | **AWQ / GPTQ-Marlin / INT8** | FP8 is *weight-only* via Marlin at best; for W8A8 throughput use AWQ/GPTQ. (rule-e remediation defaults to `awq`.) |
| **Turing / pre-Ampere** | no | INT8/AWQ; **dtype fp16 not bf16** | bf16 needs sm_80+ (rule j → remediation `dtype: fp16`). |

Quant-method guidance (current kernels):
- **AWQ** preserves quality better than naive RTN; **GPTQ** is fast. **Marlin**
  kernels give large speedups (≈2.6× GPTQ, ≈10.9× AWQ over reference) — prefer
  **Marlin-AWQ** as the "best of both" on Ampere where FP8 isn't native.
- **FP8** is the default reach on Hopper/Ada/Blackwell for both weights *and*
  KV-cache. Quantize the KV cache (`kv_cache_dtype: fp8`) to relieve rule-b
  pressure independently of weight quant.

#### Quantization CLI contract and safe operations

`gludd quantization list`, `detect --model-id MODEL`, and `drift-check` are the
operator surface for the daemon's quantization tracker. The canonical API
contract is intentionally explicit: list returns a `models` mapping keyed by
model ID, detect returns the selected profile under `best`, and drift-check
returns a `changes` list. During rolling upgrades the CLI also accepts the
older list-shaped model/profile response, top-level detect fields, and
`drifted_models`; this permits either the CLI or daemon to deploy first without
downtime. Keep those aliases for the supported mixed-version window, then
remove them only with migration telemetry and a release note.

Operational guardrails:

- **ZDD:** all three commands remain available throughout a rollout. `list` is
  read-only; detection updates tracker observations but never edits the active
  serving configuration, and a drift result is advisory until an operator
  promotes a reviewed profile.
- **Resources:** detect and drift fan out to provider metadata probes with a
  bounded 30-second client deadline. Run fleet-wide drift checks off the
  request hot path, cap the tracked model inventory, and avoid concurrent
  checks for the same model so provider rate limits and daemon sockets remain
  bounded.
- **Security:** the admin URL defaults to loopback. For non-loopback access,
  require authenticated TLS and least-privilege access to the quantization
  endpoints; never print provider credentials or raw detector payloads in CLI
  output.
- **Rollback:** snapshot `quantization list` and the current serving profile
  before promotion. If accuracy, latency, or memory regresses, restore the
  last-known-good precision/configuration and restart or roll the serving
  replica while the tracker retains the new observation for investigation.

Long-lived practitioner evidence supports treating a quantization label as an
observation, not a permanent promise. In the llama.cpp community's 2024
[Llama 3 quantization discussion](https://github.com/ggml-org/llama.cpp/discussions/6901),
operators reported materially different full-context quality loss for the same
low-bit families across model architectures, and follow-up measurements tied
results to calibration and quantizer changes. Gludd therefore re-detects per
model/revision/backend and makes drift visible before any serving-profile
change.

### 1.6 Prefix caching, CUDA graphs, speculative decoding

- **Prefix caching** (`--enable-prefix-caching`): reuses KV blocks for shared
  prompt prefixes (system prompts, few-shot preambles, multi-turn history).
  Near-free TTFT win for RAG/agent/chat workloads with repeated prefixes; turn
  it on by default there.
- **CUDA graphs** (default; *disabled* by `enforce_eager: true`): vLLM captures
  and replays the decode graph, cutting per-step Python/launch overhead for a
  meaningful tok/s gain. The detector's **rule g** (warn) flags
  `enforce_eager: true` on a capable card (`cc >= 8.0`) — only keep eager for
  debugging or unsupported custom ops; remediation removes it. V1 supports
  piecewise/full CUDA-graph modes (`FULL_AND_PIECEWISE`).
- **Speculative decoding**: a cheap drafter proposes tokens the target verifies
  in one pass — pure **latency** win (lower TPOT) at low-to-mid batch; gains
  shrink as batch saturates compute.
  - **n-gram / prompt-lookup**: zero extra model, modest speedup, safe under
    peak load. `--speculative-config '{"method":"ngram","num_speculative_tokens":4,"prompt_lookup_min":2,"prompt_lookup_max":5}'`.
  - **EAGLE / MTP / draft-model**: best speedups (~2.5–2.8× at ~0.8 draft
    acceptance). EAGLE draft runs at `draft_tensor_parallel_size=1` even when the
    target uses TP.

### 1.7 Concrete starting configs

> Replace `MODEL` and confirm against live metrics. `gpu_memory_utilization`
> assumes a *dedicated* GPU; drop ~0.05 if shared.

**A100 80 GB (Ampere, single GPU, ~13B model)** — no native FP8:
```yaml
engine: vllm
model: { name: MODEL }
dtype: bf16                 # sm_80, bf16 OK
quantization: awq           # FP8 not native on Ampere → AWQ/GPTQ-Marlin
gpu_memory_utilization: 0.90
max_model_len: 8192
max_num_seqs: 256
enable_chunked_prefill: true
enable_prefix_caching: true
# enforce_eager omitted → CUDA graphs ON
```

**H100 80 GB SXM (Hopper, NVLink node, large model on 8 GPUs):**
```yaml
engine: vllm
model: { name: MODEL-70B }
quantization: fp8           # native FP8 W8A8
kv_cache_dtype: fp8         # halve KV bytes
gpu_memory_utilization: 0.92
tensor_parallel_size: 8     # divides gpu_count=8, NVLink present
pipeline_parallel_size: 1   # 2 if spanning 2 nodes (→ TP=8, PP=2)
max_model_len: 32768
max_num_seqs: 256
enable_chunked_prefill: true
enable_prefix_caching: true
```

**L40S 48 GB (Ada, single GPU, ~13B):**
```yaml
engine: vllm
quantization: fp8           # Ada has FP8 tensor cores
gpu_memory_utilization: 0.90
max_model_len: 16384
max_num_seqs: 128
enable_chunked_prefill: true
enable_prefix_caching: true
```

**RTX 4090 24 GB (Ada consumer, ~7–8B):** VRAM-tight, single GPU:
```yaml
engine: vllm
quantization: fp8           # or awq if the checkpoint is AWQ
gpu_memory_utilization: 0.88 # consumer card: leave headroom
max_model_len: 8192
max_num_seqs: 64            # small KV pool on 24 GB
enable_chunked_prefill: true
enable_prefix_caching: true
```

**2-GPU PCIe box (no NVLink), ~13B:** TP over PCIe is a trap (rule c) — use PP:
```yaml
engine: vllm
gpu_memory_utilization: 0.90
tensor_parallel_size: 1
pipeline_parallel_size: 2   # PCIe-tolerant; uses both GPUs
max_model_len: 8192
max_num_seqs: 128
```

---

## 2. llama.cpp production tuning (`llama-server`)

llama.cpp serves GGUF models with optional GPU offload. Levers: how much to put
on the GPU, the context/KV budget, batching, KV-cache precision, and quant
selection.

### 2.1 `n_gpu_layers` (`-ngl`) — VRAM is the constraint

Number of transformer layers offloaded to GPU; the rest run on CPU.

- **`-ngl 999` / `-ngl -1`** = offload *everything*. **Whenever the model + KV
  fit in VRAM, do this** — full offload is by far the fastest path.
- **Partial offload** (some layers on CPU) collapses to CPU/PCIe bandwidth for
  the offloaded boundary and is much slower; only use it when the model can't
  fit. Reserve **15–20% VRAM headroom** beyond the weight estimate because the
  **KV cache also lives on the GPU and grows with `n_ctx`** (double the context
  ≈ double the cache).
- **`-ngl 0` on a GPU box** runs fully on CPU and leaves the GPU idle — the
  detector's **rule h** (warn) flags exactly this; remediation sets `-1`.
- Setting `n_gpu_layers` **above the model's layer count** wastes config and may
  thrash — detector **rule f** (warn) clamps it to `num_layers + 1` (all layers
  + output).

### 2.2 `n_ctx`, `--parallel` slots, and the KV budget

- **`n_ctx`** is the per-context token budget. With `--parallel N` (slot count),
  the KV cache scales as **`n_ctx × N`** — each slot needs its own KV region.
  The detector's **rule i** (critical) computes `_kv_cache_bytes(n_ctx,
  n_parallel)` vs free VRAM and fires when it won't fit; remediation lowers
  `n_ctx`, enables flash attention, and quantizes KV (below).
- **Slots buy continuous batching**: decode steps for different requests merge
  into one forward pass — that shared weight read is where the throughput comes
  from. But each slot costs linear KV memory, so size `--parallel` to
  `VRAM_for_KV / (per-slot KV at n_ctx)`, not arbitrarily high.
- With `--kv-unified`, a single shared KV buffer backs all slots (better packing
  when per-request contexts vary).

### 2.3 Batch / micro-batch (`--batch-size`, `--ubatch-size`)

- **`--batch-size` (logical) / `--ubatch-size` (physical micro-batch)** shape
  **prefill** throughput. Larger ubatch → faster prompt ingestion (higher
  prefill tok/s, better TTFT on long prompts) at the cost of a VRAM spike during
  prefill. Defaults (2048/512) are reasonable; raise ubatch on big-VRAM cards
  feeding long prompts, lower it if prefill causes OOM.
- `--cache-reuse` collapses repeated prefill on a stable system prompt (the
  llama.cpp analogue of vLLM prefix caching).

### 2.4 KV-cache type + flash attention (pair them)

- **`--flash-attn`** fuses the attention kernel; it is the precondition for cheap
  quantized KV. **Always enable flash attention when quantizing the KV cache** —
  without it the dequant path isn't fused and decode with quantized KV is
  *slower* than f16; with it, speed parity is within ~5%.
- **`--cache-type-k` / `--cache-type-v`** (f16, bf16, q8_0, q5_0, q5_1, q4_0,
  iq4_nl): **`q8_0` halves KV VRAM with < 0.1% quality loss** — the standard
  production choice. Going lower: **Q4 on the K cache is tolerable, but Q4 on the
  V cache hurts quality** — keep V at q8_0 (or f16) if you push K to q4.
  - Caveat: **asymmetric K/V quant types may not offload to GPU**, and some Metal
    builds reject mixed quantized KV without flash attention — keep K and V the
    same type unless you've verified your build.
  - Caveat: with a **draft model**, q8_0 KV can *reduce* performance — benchmark
    spec-decode + quantized KV together before shipping.

### 2.5 GGUF quant selection (weights)

GGUF ships pre-quantized weights. Rough memory & quality ladder for a 7B-class
model (scales ~linearly with params):

| Quant | ~VRAM (7B) | Quality | Use when |
|---|---|---|---|
| BF16/F16 | ~14 GB | reference | abundant VRAM, max fidelity |
| **Q8_0** | ~7–8 GB | ~lossless | plenty of VRAM, want safety |
| **Q5_K_M** | ~5 GB | very good | balanced default |
| **Q4_K_M** | ~4 GB | good (sweet spot) | **most production**; best size/quality |
| Q3/Q2_K | <3.5 GB | degraded | last resort on tiny VRAM |

`Q4_K_M` is the standard production starting point; step up to `Q5_K_M`/`Q8_0`
if you have VRAM headroom and need fidelity.

### 2.6 Multi-GPU & CPU/GPU hybrid

- **`--tensor-split a,b,...`** distributes layers across GPUs by ratio. Set it to
  each GPU's *free* VRAM ratio (not equal split) when cards are uneven.
  llama.cpp's multi-GPU is layer/row split — far lighter interconnect traffic
  than vLLM TP, so PCIe boxes are workable, but it won't match NVLink TP for the
  largest models.
- **CPU/GPU hybrid** (partial `-ngl`) is a *fallback*, not a throughput strategy:
  the CPU-resident layers run at LPDDR/DDR bandwidth (tens of GB/s vs TB/s HBM),
  so every token pays for the slowest tier. Prefer a smaller quant that *fully*
  fits over a larger quant that spills to CPU.

### 2.7 Concrete starting configs

**Single 24 GB GPU (4090), 7–8B GGUF, fully offloaded:**
```yaml
engine: llamacpp
model: { name: MODEL-Q4_K_M.gguf, num_layers: 32 }
n_gpu_layers: -1
n_ctx: 8192
n_parallel: 4
flash_attn: true
cache_type_k: q8_0
cache_type_v: q8_0
batch_size: 2048
ubatch_size: 512
```

**Single 80 GB GPU (A100), 70B Q4_K_M, long context:**
```yaml
engine: llamacpp
model: { name: MODEL-70B-Q4_K_M.gguf, num_layers: 80 }
n_gpu_layers: -1
n_ctx: 16384
n_parallel: 2
flash_attn: true
cache_type_k: q8_0
cache_type_v: q8_0
```

**2× uneven GPUs (24 GB + 16 GB), tensor split by free VRAM:**
```yaml
engine: llamacpp
n_gpu_layers: -1
tensor_split: "24,16"
n_ctx: 8192
flash_attn: true
cache_type_k: q8_0
cache_type_v: q8_0
```

---

## 3. GPU architecture & bandwidth (why the knobs matter)

### 3.1 HBM vs GDDR, NVLink vs PCIe

| Link / memory | Bandwidth (order) | Where |
|---|---|---|
| **HBM3 / HBM3e** | 3.35 TB/s (H100) · 4.8 TB/s (H200) · 8.0 TB/s (B200) | datacenter GPU VRAM |
| **GDDR6X** | ~1.0 TB/s | consumer (4090) / L40S-class |
| **NVLink 4.0** | ~900 GB/s/GPU bidir (HGX/DGX) | GPU↔GPU inside a node |
| **PCIe 5.0 x16** | ~64 GB/s | GPU↔GPU (no NVLink), GPU↔host |
| **DDR5 / LPDDR5X** | tens–~100s GB/s | CPU RAM (hybrid offload tier) |

Two practical orders of magnitude: **HBM ≈ 50× PCIe**, and **NVLink ≈ 14× PCIe**.
This is why (a) decode tok/s tracks VRAM bandwidth (Section 0), (b) TP only pays
off over NVLink (Section 1.3 / rule c), and (c) CPU/PCIe hybrid offload is a
last resort (Sections 2.1, 2.6).

### 3.2 Batch-size vs latency/throughput

- **Batch = 1**: minimum latency, **bandwidth-bound** — you read all weights to
  emit one token. Worst tok/s-per-GPU.
- **Growing batch**: weight read is amortized across the batch → aggregate
  throughput climbs steeply at first (still bandwidth-bound), then the curve
  bends as you approach the **compute roofline** and KV pressure causes
  preemption. Per-request latency rises gradually, then sharply once you
  oversubscribe KV.
- **Operating point**: pick the largest batch (`max_num_seqs` / `--parallel`)
  whose **p99 TPOT** still meets SLO *and* whose KV usage stays < ~90%. The live
  metrics in Section 5 (`kv_cache_usage`, `num_requests_waiting`, TPOT
  histogram) tell you exactly where the knee is.

### 3.3 How VRAM splits

```
VRAM = weights + KV-cache pool + activations/workspace + (CUDA-graph capture)
```

- **Weights**: `params × bytes_per_param` (FP16 2.0, FP8/INT8/AWQ/GPTQ ~1.0, Q4
  ~0.5 — the `_weights_bytes` table in the detector).
- **KV-cache**: grows with `context × concurrency × kv_dtype` (Section 1.2 /
  2.2). The lever you most often fight.
- **Activations + workspace + CUDA graphs**: the "everything else" that
  `gpu_memory_utilization` headroom (vLLM) or your 15–20% reserve (llama.cpp)
  must cover. Skimping here is what turns a healthy config into intermittent OOM.

Quantizing weights frees room that the **KV pool absorbs**, directly raising the
concurrency or context you can serve — quantization is a throughput lever twice
over.

---

## 4. Misconfiguration detection → remediation loop

The detector (`MisconfigDetector.check`) is **static and side-effect-free**: it
takes a `deployment` config dict + `gpu_info` and returns `Finding`s; it never
touches hardware or the network. `MisconfigDetector.remediate(finding)` returns a
**yaml-first config patch**: `{rule_id, format:"yaml", config_patch:{...},
requires_restart:bool, notes}`. The serving control plane is responsible for the
*loop* around it: **detect → log → patch → restart (or escalate)**.

### 4.1 Rule → tunable → remediation action

Every `rule_id` below is real (`_REMEDIATIONS` map / `_check_vllm` /
`_check_llamacpp` in `model_deploy_check.py`).

| rule_id | Engine | Severity | Tunable (this guide) | Remediation patch (auto) | Loop action |
|---|---|---|---|---|---|
| **a** | vllm | critical / warn | `gpu_memory_utilization` (1.1) | high→ `-0.05` (floor 0.80); low→ `0.90` | **auto-patch + restart** |
| **b** | vllm | critical | KV budget `max_model_len`×`max_num_seqs` (1.2) | `kv_cache_dtype: fp8`, `max_model_len: 8192` | **auto-patch + restart** |
| **c** | vllm | critical / warn | TP sizing vs topology (1.3) | non-NVLink→ `tp:1, pp:gpu_count`; else `tp:1` | critical→ **block & escalate**; non-NVLink warn→ auto-patch |
| **e** | vllm | critical | FP8 vs arch (1.5) | `quantization: awq` | **auto-patch + restart** |
| **g** | vllm | warn | CUDA graphs / `enforce_eager` (1.6) | `enforce_eager: false` | **auto-patch + restart** |
| **j** | vllm | critical | `dtype` vs compute capability (1.5) | `dtype: fp16` | **auto-patch + restart** |
| **k** | vllm | warn | idle GPUs / parallelism (1.3) | `tensor_parallel_size: gpu_count` | propose; **gate on NVLink** before auto-apply |
| **l** | vllm | warn | `max_num_seqs` vs VRAM (1.2/1.4) | `max_num_seqs: 64` | auto-patch (throughput, non-fatal) |
| **f** | llamacpp | warn | `n_gpu_layers` > layers (2.1) | `n_gpu_layers: num_layers+1` | **auto-patch + restart** |
| **h** | llamacpp | warn | `n_gpu_layers==0` on GPU (2.1) | `n_gpu_layers: -1` | **auto-patch + restart** |
| **i** | llamacpp | critical | `n_ctx`×`n_parallel` vs VRAM (2.2/2.4) | `n_ctx:4096, flash_attn:true, cache_type_{k,v}:q8_0` | **auto-patch + restart** |
| `malformed-*`, `unknown-engine`, `internal-error` | any | critical | schema/guard | empty patch, `notes` only | **block & escalate** (never auto-apply) |

### 4.2 Recommended loop semantics

1. **Detect** on every deploy/config-change and on a schedule (re-resolve
   `gpu_info` live so topology facts — `arch`, `compute_capability`,
   `has_nvlink`, `gpu_count`, `vram_gb` — are current).
2. **Log** every `Finding` structured: `rule_id, severity, message, evidence`.
   The `evidence` dict (e.g. `kv_need_gib`, `kv_pool_gib`, `gpu_count`) is the
   audit trail — log it verbatim so a human can reconstruct the decision.
3. **Decide** by severity × confidence:
   - **`warn` + deterministic patch** (a-low, g, h, l) → **auto-apply** the
     `config_patch`, then restart only if `requires_restart` (all current
     patches set it `true`).
   - **`critical` with a safe single-knob fix** (a-high, b, e, i, j, f) →
     auto-apply behind a guard: re-run `check` on the *patched* config and only
     ship if the finding clears and no new critical appears (closed-loop
     verification; prevents patch oscillation).
   - **`critical` topology/architecture** (rule c hard error, rule k) →
     **escalate, don't auto-apply**: changing TP/PP layout has real performance
     cliffs (PCIe all-reduce) and the safe choice depends on intent. Surface the
     proposed patch for human approval.
   - **`malformed-*` / `unknown-engine` / `internal-error`** → **fail closed**:
     refuse the deploy, page the operator. Never patch around a malformed config.
4. **Patch → restart**: apply `config_patch` (yaml merge), restart the serving
   process, and **re-run `check`** post-restart to confirm the finding is gone.
   Record before/after in the deploy log.
5. **Guardrails**: cap auto-remediation attempts (e.g. ≤2 per rule per deploy) to
   avoid restart loops; if a critical finding survives two patch cycles,
   escalate. Always escalate rather than apply an *empty* patch (unknown
   `rule_id` → `notes`-only), which signals "no automated fix known."

---

## 5. Observability — what to scrape and which connectors surface it

Tuning is closed-loop only if you measure. Scrape **server metrics** (request +
KV + queue, from the engine) **and hardware metrics** (GPU util/VRAM/power, from
the driver) and correlate them.

### 5.1 Server-side (engine) metrics

**vLLM** exposes a Prometheus-compatible `/metrics` endpoint (V1 engine, `vllm:`
prefix). Scrape and alert on:

| Metric | What it tells you | Tune / alert |
|---|---|---|
| `vllm:gpu_cache_usage_perc` (a.k.a. KV-cache usage) | KV pool pressure | **alert > ~90%** → imminent preemption/throughput drop; lower `max_num_seqs`/`max_model_len` or raise `gpu_memory_utilization` (rule a/b/l territory) |
| `vllm:num_requests_running` / `vllm:num_requests_waiting` | live batch vs **queue depth** | waiting rising faster than running ⇒ can't absorb traffic → scale out or shed |
| `vllm:time_to_first_token_seconds` (**TTFT** histogram) | prefill latency | regressions ⇒ tune chunked-prefill `max_num_batched_tokens`, prefix caching, ubatch |
| `vllm:time_per_output_token_seconds` (**TPOT** histogram) | decode latency / SLO | the SLO knob for batch sizing (3.2); spec-decode lowers it |
| token throughput counters (prompt/generation tokens) | **tokens/s** | the bandwidth-ceiling check (Section 0) |
| preemption / swap counters | KV oversubscription events | nonzero ⇒ you're over the KV budget |

vLLM also supports **OpenTelemetry** tracing via `--otlp-traces-endpoint` for
per-request spans. **llama.cpp** `llama-server` exposes a Prometheus `/metrics`
endpoint (tokens/s, slot occupancy, prompt/eval timings) — scrape it the same
way; per-request timings come back in the response JSON `timings` block.

### 5.2 Hardware metrics (gludd connectors)

The engine can't see the silicon's own counters — scrape those from the driver:

- **DCGM / `dcgm-exporter`** (NVIDIA Data Center GPU Manager) → Prometheus:
  **GPU utilization (SM %)**, **VRAM used/free**, memory-controller %
  (bandwidth proxy), power, temperature, NVLink throughput, ECC. This is the
  authoritative source for the `gpu_info` facts the detector consumes
  (`vram_gb`, `gpu_count`, and — for capacity planning — how full VRAM is vs the
  `gpu_memory_utilization` you set).
- **Prometheus connector** scrapes both the engine `/metrics` and
  `dcgm-exporter`; Grafana correlates them. For non-DCGM hosts, `nvidia-smi`
  exporters give a coarser util/VRAM signal.

**The two halves must be read together.** Example: low `DCGM SM-util` *with* high
`vllm:gpu_cache_usage_perc` and rising `num_requests_waiting` = **KV-bound, not
compute-bound** → quantize KV / raise `gpu_memory_utilization`, *don't* add
GPUs. High SM-util at the batch knee with flat throughput = **compute-bound** →
you're at the roofline; only a smaller model/quant or more GPUs helps. These
diagnoses map straight back to the detector rules and the levers in Sections
1–3.

---

## Sources

- vLLM — Optimization and Tuning (stable): https://docs.vllm.ai/en/stable/configuration/optimization/
- vLLM — Inside vLLM: Anatomy of a High-Throughput Inference System (2025): https://vllm.ai/blog/2025-09-05-anatomy-of-vllm
- vLLM — FP8 W8A8 quantization: https://docs.vllm.ai/en/latest/features/quantization/fp8/
- vLLM — The State of FP8 KV-Cache and Attention Quantization (2026): https://vllm.ai/blog/2026-04-22-fp8-kvcache
- vLLM — Quantization overview: https://docs.vllm.ai/en/latest/features/quantization/
- vLLM — Speculative Decoding: https://docs.vllm.ai/en/latest/features/speculative_decoding/
- vLLM — Metrics design (V1): https://docs.vllm.ai/en/stable/design/metrics/
- vLLM Production Deployment 2026 (Spheron): https://www.spheron.network/blog/vllm-production-deployment-2026/
- The Complete Guide to LLM Quantization with vLLM (JarvisLabs): https://jarvislabs.ai/blog/vllm-quantization-complete-guide-benchmarks
- Speculative Decoding LLM Inference Speedup Guide 2025 (Introl): https://introl.com/blog/speculative-decoding-llm-inference-speedup-guide-2025
- llama.cpp — server README (master): https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
- llama.cpp — Optimal parameters for parallel inference (discussion #18308): https://github.com/ggml-org/llama.cpp/discussions/18308
- How to Tune llama.cpp --n-gpu-layers: A Practical VRAM Guide (2026): https://dev.to/pat9000/how-to-tune-llamacpp-n-gpu-layers-a-practical-vram-guide-2026-m8i
- KV Cache Quantization: Q8 vs FP16 (TechPlained): https://www.techplained.com/kv-cache-quantization
- Optimize Your GPU KV-Cache for llama.cpp (Rigel/Medium): https://medium.com/rigel-computer-com/optimize-your-gpu-kv-cache-for-llama-cpp-opencode-co-13b6bc74f5ec
- Top GPUs for LLM Inference Workloads in 2025 (GMI Cloud): https://www.gmicloud.ai/en/blog/choosing-graphics-cards-llm-workloads
- Best NVIDIA GPUs for LLMs (Spheron): https://www.spheron.network/blog/best-nvidia-gpus-for-llms/
- Monitor LLM Inference in Production 2026: Prometheus & Grafana for vLLM/TGI/llama.cpp (Glukhov): https://www.glukhov.org/observability/monitoring-llm-inference-prometheus-grafana/
- 5 steps to triage vLLM performance (Red Hat Developer, 2026): https://developers.redhat.com/articles/2026/03/09/5-steps-triage-vllm-performance
