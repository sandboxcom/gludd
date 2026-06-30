# Deployment Guides

Guides for deploying General Ludd in various environments.

## Contents

| Document | Description |
|----------|-------------|
| [Terraform](terraform.md) | Infrastructure as Code with Terraform |
| [Slurm](slurm.md) | HPC cluster deployment with Slurm |
| [Release Process](release-process.md) | Release cutting and publishing |

## Deployment Architectures

| Environment | Compute | Model Serving | Orchestration |
|-------------|---------|---------------|---------------|
| **Slurm HPC** | Slurm batch jobs | Slurm-served vLLM/llama.cpp | Native Slurm integration |
| **Bare Metal / VM** | Operator-owned hosts | Ansible → systemd | Ansible playbooks |
| **Cloud GPU** | Terraform-provisioned | Terraform + cloud-init | TerraformGenerator |
| **Dev / CI** | Local docker/podman | LocalInferenceManager | `make local-model-*` |

## Quick Reference

### Slurm Cluster
```bash
# Submit vLLM as Slurm batch job
gludd slurm submit --model meta-llama/Llama-3.1-8B-Instruct --gpus 1
```

### Cloud (Terraform)
```bash
# Deploy AWS GPU instance with vLLM
gludd compute launch --provider aws --engine vllm --model meta-llama/Llama-3.1-8B-Instruct
```

### Local Development
```bash
# Start vLLM locally via docker
make local-model-vllm MODEL_ID=meta-llama/Llama-3.1-8B-Instruct
```

### Model Profiles
Configure model endpoints in `~/.config/general-ludd/model_profiles/`:
- `zai_coder.yml` — Z.AI GLM (primary)
- `deepseek_coder.yml` — DeepSeek fallback
- `qwen_coder.yml` — Qwen fallback
- `openai_example.yml` — OpenAI GPT-4
- `anthropic_example.yml` — Anthropic Claude

## Related Docs

- [Model Serving Deployment Design](../design/MODEL_SERVING_DEPLOYMENT.md)
- [Terraform Infra Structure Design](../design/TERRAFORM_INFRA_STRUCTURE.md)
- [Architecture: Overview](../architecture/overview.md)

---

[Back to Documentation Index](../index.md)