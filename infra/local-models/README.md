# Local Model Serving — Docker / Podman Templates

Templated docker-compose stacks for running model servers on a single host
(dev workstation, CI runner, single-box inference). These are NOT production
substrates — for HPC clusters use the Slurm batch path
(`docs/design/MODEL_SERVING_DEPLOYMENT.md`), and for cloud GPUs use the
existing Terraform path (`docs/design/TERRAFORM_INFRA_STRUCTURE.md`).

## When to use which

| Stack | When | Why |
|---|---|---|
| `vllm/` | High-throughput GPU inference; multi-user load | vLLM has the highest throughput of the three, supports continuous batching, tensor parallelism, and the OpenAI-compatible API. Best when you have a real GPU and want max tokens/sec. |
| `llamacpp/` | Low-resource GPU or single-GPU inference; CPU-only fallback | llama.cpp is lighter than vLLM, runs on a wider hardware matrix (including CPU-only), and is the right choice when vLLM's overhead is not warranted. |
| `ollama/` | Dev / prototyping / smoke tests | Easiest ergonomics (`ollama run llama3.2`), smallest scope, slowest of the three. Use for local dev where you want a model running in 30 seconds, not for production load. |

## Build & start

Each stack is driven by `docker compose` (or `podman compose`) with build
args overridden from the environment. The Makefile wraps the common case:

```bash
# vLLM with a specific model
make local-model-vllm MODEL_ID=meta-llama/Llama-3.1-8B-Instruct

# vLLM with extra args (tensor parallel, max context)
make local-model-vllm MODEL_ID=... TENSOR_PARALLEL_SIZE=2 MAX_CTX=32768

# llama.cpp with a direct model URL
make local-model-llamacpp MODEL_URL=https://example.com/model.gguf

# ollama with a specific model
make local-model-ollama OLLAMA_MODEL=llama3.2
```

Each target runs `docker compose build && docker compose up -d` in the
relevant subdirectory. The service is then reachable at:

| Stack | URL |
|---|---|
| vLLM | `http://localhost:8000/v1` (OpenAI-compatible) |
| llama.cpp | `http://localhost:8080` |
| ollama | `http://localhost:11434` |

## Layout

```
infra/local-models/
  vllm/
    Dockerfile          build args: MODEL_ID, VLLM_VERSION, CUDA_VERSION, TORCH_CUDA_ARCH_LIST
    docker-compose.yml  GPU device reservation + 8000:8000 + HF cache volume
  llamacpp/
    Dockerfile          build args: LLAMACPP_VERSION, MODEL_URL; compiles with GGML_CUDA=ON
    docker-compose.yml  GPU device reservation + 8080:8080
  ollama/
    Dockerfile          wraps ollama/ollama:latest + pre-pulls OLLAMA_MODEL at build time
    docker-compose.yml  GPU device reservation + 11434:11434
  README.md            this file
```

## Prerequisites

- `docker` (or `podman`) with the NVIDIA Container Toolkit installed for GPU
  access. CPU-only runs work for llama.cpp and ollama but are not the design
  point — remove the `deploy.resources.reservations.devices` block to fall
  back to CPU.
- For vLLM: a CUDA-capable GPU with compute capability matching
  `TORCH_CUDA_ARCH_LIST`.

## Stopping

```bash
docker compose -f infra/local-models/vllm/docker-compose.yml down
```

Or remove all containers and volumes:

```bash
docker compose -f infra/local-models/vllm/docker-compose.yml down -v
```

## Relation to the other model-serving paths

This directory is the **dev / single-host** path. The two production paths:

1. **Slurm batch submission** — for HPC clusters where Slurm arbitrates GPU
   access. See `infra/slurm/{vllm,llamacpp}.sbatch` and
   `docs/design/MODEL_SERVING_DEPLOYMENT.md`.
2. **Terraform** — for cloud GPU instances. See
   `docs/design/TERRAFORM_INFRA_STRUCTURE.md` and the
   `TerraformGenerator._generate_<provider>` methods.

This local path is the same shape as every other gludd agent that runs
locally (e.g. ollama) — it does not interact with the Slurm or Terraform
paths.
