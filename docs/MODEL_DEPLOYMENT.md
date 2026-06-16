# vLLM / llama.cpp Production Deployment + Misconfig Detection (spec for #76)

Grounds in gludd's existing `infra/local_inference.py` (`LocalServerConfig`: engine ∈
{vllm,llamacpp,slurm}, `gpu_layers`, `context_size`, `extra_args`) and `infra/deployment.py`
(`ComputeConfig.gpu_type/provider/model_name`). Gap today: **no misconfig detection** — this fills it.

## Engine choice
- **vLLM** when: GPU present, concurrency > ~4, throughput-bound, model fits fp16/fp8/AWQ, or needs TP across NVLinked GPUs. Continuous batching + PagedAttention.
- **llama.cpp** when: CPU/Apple-Metal/edge, single low-VRAM GPU, must aggressively 4-bit quantize to fit, low concurrency, or VRAM too small for vLLM's KV pre-alloc.
- Crossover: ≤1–2 users on one consumer GPU → llama.cpp Q4_K_M ≈ as fast, far less memory; above that vLLM wins.

## KV-cache budget (core formula)
`KV_bytes ≈ 2 · num_layers · max_model_len · max_num_seqs · num_kv_heads · head_dim · dtype_bytes`
- `2` = K+V; use **GQA** `num_kv_heads` (often 8), not attention heads; `head_dim = hidden/attn_heads`; `dtype_bytes` 2 (fp16/bf16) or 1 (fp8/int8 KV).
- `KV_pool ≈ gpu_mem_util·VRAM − weights − overhead`; cacheable tokens must cover `max_num_seqs·max_model_len` or requests preempt/queue.

## Key knobs
- **vLLM:** `--gpu-memory-utilization` (0.9 default; lower on OOM), `--max-model-len` (caps KV), `--max-num-seqs`, `--max-num-batched-tokens`, `--enable-chunked-prefill`, `--enable-prefix-caching` (big win for repeated ROLE prompts), `--tensor-parallel-size` (NVLink-bound all-reduce; must divide kv/attn heads), `--pipeline-parallel-size` (PCIe-tolerant), quant AWQ/GPTQ/FP8/INT8 (Marlin kernels), dtype bf16/fp16/fp8, `--kv-cache-dtype fp8`, avoid `--enforce-eager` on capable cards, `--cpu-offload-gb`/`--swap-space` = emergency only (thrash), speculative decoding for low-batch.
- **llama.cpp:** GGUF `Q4_K_M` default (`Q5_K_M`/`Q6_K`/`Q8_0` if VRAM allows), `-ngl`/`gpu_layers` (fit VRAM or thrash), `-fa` flash-attn, `--cache-type-k/v q8_0` (quantized KV), `--split-mode layer|row` + `--tensor-split`, `-c` ctx, `--mmap`, NUMA pinning, `-t` threads.

## GPU arch / bandwidth (decode is HBM-bandwidth-bound; TP is interconnect-bound)
| Arch | Cards | Interconnect | FP8 | Rule |
|---|---|---|---|---|
| Hopper | H100/H200 | NVSwitch | native | best for FP8 + large TP |
| Ampere DC | A100 | NVSwitch | no | TP via NVLink; use AWQ/GPTQ/INT8 |
| Ada | L4/L40S | **PCIe only** | yes | FP8 to fit; **TP=1**/PP (no NVLink) |
| Consumer | 3090/4090 (24GB) | **no NVLink, P2P off** | 4090 only | **avoid TP**; single-GPU/pipeline |
**Only TP across NVLinked GPUs** — TP over PCIe (≈32 GB/s, no P2P) can be slower than 1-GPU.

VRAM weights (add KV): 8B fp16 ~16/4bit ~5; 13B ~26/8; 70B ~140/40; Mixtral-8x7B ~94/26; DeepSeek-V3 671B → 8×H200 FP8 (native). MoE: VRAM holds ALL experts, throughput ∝ active params.

## Cloud instance → GPU → TP
AWS p5(8×H100 NVSwitch,TP8) · p4d(8×A100) · g6e(L40S PCIe→TP1/PP) · g5(A10G) · g6(L4). GCP a3(H100) · a2(A100) · g2(L4). Azure NDv5(H100) · NDv4(A100) · NCads(L40S/A10). RunPod/Lambda/CoreWeave: **verify NVLink per listing** before TP. Topology dictates TP size ≤ NVLinked GPUs/box.

## MisconfigDetector ruleset (detect signal → remediate)
| ID | Misconfig | Detect | Remediate |
|---|---|---|---|
| a | gpu-mem-util too high → peak OOM | CUDA OOM/EngineDeadError, preemption spike, mem ~100% | lower `--gpu-memory-utilization` 0.05 (≥0.80), cap `--max-num-seqs` |
| b | max-len > KV budget | "no available memory for KV cache"; GPU blocks < need | reduce `--max-model-len` (formula), or `--kv-cache-dtype fp8`, or lower max-num-seqs |
| c | TP ≠ GPU count or TP on non-NVLink | world-size/head-divisor error; low tok/s + high PCIe | valid divisor; if PCIe-only → TP=1 + pipeline/`tensor-split` |
| d | CPU offload/swap thrash | tok/s ≪ baseline, high host-RAM, PCIe traffic | drop offload; smaller quant to fit VRAM |
| e | quant/dtype unsupported on arch | `no kernel image`/`fp8 not supported` | map arch→supported quant (pre-Hopper→AWQ/GPTQ-Marlin/INT8) |
| f | llama.cpp `-ngl` > VRAM | VRAM ~100% + low tok/s + CPU busy | reduce `-ngl` to fit; `-fa`; quantized KV; smaller GGUF |
| g | flash-attn/CUDA-graphs off on capable card | `--enforce-eager` present / no `-fa`; low tok/s | enable CUDA graphs (drop enforce-eager); add `-fa` |

Signals come from the observability connectors: GPU mem util, tok/s, TTFT, OOM/CUDA-error log lines, host-RAM, PCIe/swap, vLLM `# GPU blocks`/preemption/throughput.

## Implementation shape (#76)
- `HardwareProfile(gpu_type,gpu_count,vram_gb,has_nvlink,supports_fp8,hbm_bw)`, `ModelProfile(name,num_layers,num_kv_heads,head_dim,params_b,is_moe,native_quant)`, `MisconfigRule(id,engine,triggers,predicate,severity,remediation)`, `Remediation(config_patch,requires_restart)`, `Finding(rule_id,severity,signal,evidence,proposed_patch)`.
- **Static validation** in `_build_command` (alongside the existing injection guards): KV-budget (b), TP divisor/NVLink (c), FP8-vs-arch (e), eager/flash (g), llama `-ngl` fit (f) — fail-closed on FATAL.
- **Runtime detection:** subscribe to the EventBus/log stream, match triggers → emit structured `misconfig_detected` CustomEvent → propose or auto-apply patch + restart.
- Map `ComputeConfig.gpu_type` → `HardwareProfile` via a static table from the arch/cloud sections.
- ROLES: `diagnose-deployment` (read-only Finding[]) and `remediate-deployment` (apply patch → restart → re-diagnose closed loop, audit-log before/after, escalate after N attempts).
