# Terraform Modules — Gludd

Reusable Terraform building blocks composed into deployment stacks.

## Module index

| Module | Purpose |
|---|---|
| `onboard-iam/` | AWS IAM onboarding: creates a least-privilege `gludd-compute-operator` role with EC2/EBS/EIP scoped policy, instance profile, and trust relationship |
| `onboard-iam-azure/` | Azure IAM onboarding: creates a user-assigned managed identity with Contributor role assignment at the resource-group scope |
| `onboard-iam-gcp/` | GCP IAM onboarding: creates a `gludd-compute-operator` service account with `compute.admin` role binding at the project level |
| `llamacpp-server/` | Generic llama.cpp inference server: deploys a GPU VM/container running `ghcr.io/ggerganov/llama.cpp:server` with configurable model, GPU count, quantization, KV cache, and cost watchdog |
| `vllm-server/` | Generic vLLM inference server: deploys a GPU VM/container running the vLLM OpenAI-compatible server with configurable model, tensor parallelism, GPU count, and cost watchdog |
| `network/` | Multi-cloud network plumbing: creates security groups (AWS), firewall rules (GCP), NSGs (Azure), or port-group bindings (vSphere) for the inference port (8000) and SSH (22) |
| `kubernetes-deploy/` | Kubernetes deployment manifest generator: creates Deployment + Service for an inference engine (vllm or llamacpp) with GPU resource requests, PVC model mounts, and health probes |
| `qemu-vm/` | QEMU/KVM virtual machine: provisions a libvirt domain from a cloud image with configurable vCPUs, RAM, disk, and cloud-init, plus port forwarding for the inference endpoint |
| `gpu-cost-watchdog/` | Cost-bound termination watchdog: deploys a background agent on GPU instances that self-terminates when cumulative spend reaches `max_cost_usd` or uptime exceeds `timeout_minutes` |

## Module contract

Every module follows the standard Terraform module contract:

- `main.tf` — resource declarations
- `variables.tf` — typed input variables with descriptions and validation
- `outputs.tf` — typed output values consumed by stacks

## Composing modules into stacks

Stacks wire modules together. A typical stack (`stacks/aws-vllm/main.tf`):

```hcl
module "network"   { source = "../../modules/network"; ... }
module "vllm"       { source = "../../modules/vllm-server"; ... }
module "watchdog"   { source = "../../modules/gpu-cost-watchdog"; ... }
```

The IAM modules (`onboard-iam*`) are run once per cloud account, not per stack. Their outputs (role ARN, identity ID, service account email) are referenced by instance stacks via `data.terraform_remote_state`.
