# Infrastructure as Code — Gludd

> **Quick ref:** `make help | grep tf-` for Terraform targets; `make help | grep infra` for local-model targets.

## Directory map

| Directory | Purpose | Key files |
|---|---|---|
| `terraform/` | Declarative cloud infrastructure (Terraform IaC) | `versions.tf` (canonical provider contract), `modules/` (reusable blocks), `stacks/` (per-provider deployments) |
| `kubernetes/` | Raw Kubernetes manifests for GPU inference servers | `deployment-llamacpp.yaml`, `deployment-vllm.yaml` |
| `local-models/` | Docker/Podman Compose templates for local dev GPU inference | Dockerfiles + Compose files for vLLM, llama.cpp, ollama |
| `slurm/` | Slurm HPC batch scripts for GPU inference jobs | `llamacpp.sbatch`, `vllm.sbatch` |
| `searxng/` | SearXNG meta-search engine config for agent web retrieval | `settings.yml`, `docker-compose.yml` |

## How the model-serving paths relate

Three disjoint deployment paths, graduated by environment:

| Path | Location | When to use |
|---|---|---|
| **Local dev** | `local-models/` | Single-host dev workstation, CI runner, prototyping. Docker Compose on a local GPU. |
| **HPC batch** | `slurm/` | Slurm-managed GPU cluster. Jobs submitted via `sbatch`. |
| **Cloud IaC** | `terraform/` | Production cloud GPU instances across AWS, Azure, GCP, vSphere, RunPod, Vast, Kubernetes, QEMU. |

Local models and Terraform stacks share the same inference-engine surface area (llamacpp-server + vllm-server modules, identical variables), so a model serving config that works locally is valid for the Terraform generator with zero translation.

## Common operations

```bash
# Warm the Terraform provider cache (run once after clone)
make tf-cache-warm

# Validate all stacks match the canonical provider versions
make tf-versions-check

# Start a local vLLM instance
make local-model-vllm MODEL_ID=meta-llama/Llama-3.1-8B-Instruct

# Submit a Slurm llama.cpp job
sbatch infra/slurm/llamacpp.sbatch
```

## Design docs

- `docs/design/TERRAFORM_INFRA_STRUCTURE.md` — Terraform layout and generator integration
- `docs/design/MODEL_SERVING_DEPLOYMENT.md` — model serving deployment matrix
- `docs/design/NEXT_RELEASE_BETA2_SPEC.md` — upcoming work on provider-scoped config
