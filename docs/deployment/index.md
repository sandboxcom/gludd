# Deployment Guides

Guides for deploying General Ludd in various environments.

## Contents

This directory does not yet hold split-out deployment pages. Terraform and
Slurm deployment design live in [Model Serving Deployment](../design/MODEL_SERVING_DEPLOYMENT.md)
and [Terraform Infra Structure](../design/TERRAFORM_INFRA_STRUCTURE.md).

**Release process:** [RELEASE_RUNBOOK.md](../RELEASE_RUNBOOK.md) is the authoritative
guide — read it before touching any release target. [RELEASE_CHECKLIST.md](../RELEASE_CHECKLIST.md)
is the step-by-step tick list. In short: `make release-cut TAG=... MSG='...'` is the only
sanctioned path, and `make verify-release-completeness TAG=...` (not
`verify-release-artifact`) is the gate that decides whether a release actually shipped.

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

Config discovery is `$GLUDD_CONFIG_DIR` → `~/.config/general-ludd` → `/etc/general-ludd`.
**The repo's own `config/` directory is NOT on that path.** If the daemon finds no
profiles, it silently dispatches to a no-op executor — agents return `completed` with
empty output while `/healthz` still reports 200. See
[CONFIG_REFERENCE.md §2.0](../CONFIG_REFERENCE.md).

Configure model endpoints in `~/.config/general-ludd/model_profiles/`:
- `zai_coder.yml` — Z.AI GLM (primary)
- `deepseek_coder.yml` — DeepSeek fallback
- `qwen_coder.yml` — Qwen fallback
- `openai_example.yml` — OpenAI GPT-4
- `anthropic_example.yml` — Anthropic Claude

## Related Docs

- [Model Serving Deployment Design](../design/MODEL_SERVING_DEPLOYMENT.md)
- [Terraform Infra Structure Design](../design/TERRAFORM_INFRA_STRUCTURE.md)
- [Architecture](../architecture.md)

---

[Back to Documentation Index](../index.md)
