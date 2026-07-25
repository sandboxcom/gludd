# Terraform Stacks — Gludd

Concrete deployment stacks: one per provider × inference engine combination. Each stack wires the reusable modules into a deployable unit.

## Stack index by provider

### AWS

| Stack | Engine | Description |
|---|---|---|
| `aws-llamacpp/` | llama.cpp | EC2 GPU instance running llama.cpp server with security group, EBS volume, and cost watchdog |
| `aws-vllm/` | vLLM | EC2 GPU instance running vLLM OpenAI-compatible server with security group, EBS volume, and cost watchdog |

### Azure

| Stack | Engine | Description |
|---|---|---|
| `azure-llamacpp/` | llama.cpp | Azure VM with NVIDIA GPU running llama.cpp server, NSG rules, and managed disk |
| `azure-vllm/` | vLLM | Azure VM with NVIDIA GPU running vLLM server, NSG rules, and managed disk |
| `azure-container-app-llamacpp/` | llama.cpp | Azure Container App running llama.cpp server — serverless GPU container, no VM management |
| `azure-container-app-vllm/` | vLLM | Azure Container App running vLLM server — serverless GPU container, no VM management |

### GCP

| Stack | Engine | Description |
|---|---|---|
| `gcp-llamacpp/` | llama.cpp | GCE instance with NVIDIA GPU running llama.cpp server, firewall rules, and persistent disk |
| `gcp-vllm/` | vLLM | GCE instance with NVIDIA GPU running vLLM server, firewall rules, and persistent disk |

### vSphere

| Stack | Engine | Description |
|---|---|---|
| `vsphere-llamacpp/` | llama.cpp | vSphere VM with GPU passthrough running llama.cpp server, dvSwitch port-group binding |
| `vsphere-vllm/` | vLLM | vSphere VM with GPU passthrough running vLLM server, dvSwitch port-group binding |

### RunPod

| Stack | Engine | Description |
|---|---|---|
| `runpod-llamacpp/` | llama.cpp | RunPod serverless GPU pod running llama.cpp server — managed GPU, no instance lifecycle |
| `runpod-vllm/` | vLLM | RunPod serverless GPU pod running vLLM server — managed GPU, no instance lifecycle |

### Vast

| Stack | Engine | Description |
|---|---|---|
| `vast-llamacpp/` | llama.cpp | Vast.ai GPU rental instance running llama.cpp server — spot-market GPU pricing |
| `vast-vllm/` | vLLM | Vast.ai GPU rental instance running vLLM server — spot-market GPU pricing |

### Kubernetes

| Stack | Engine | Description |
|---|---|---|
| `kubernetes-llamacpp/` | llama.cpp | Kubernetes Deployment + Service for llama.cpp server with GPU resource requests and health probes |
| `kubernetes-vllm/` | vLLM | Kubernetes Deployment + Service for vLLM server with GPU resource requests and health probes |

### QEMU

| Stack | Engine | Description |
|---|---|---|
| `qemu-llamacpp/` | llama.cpp | QEMU/KVM VM running llama.cpp server — libvirt domain with cloud-init, port forwarding |
| `qemu-vllm/` | vLLM | QEMU/KVM VM running vLLM server — libvirt domain with cloud-init, port forwarding |

## Example tfvars

`infra/terraform/examples/` contains 11 starter `.tfvars.example` files. Copy and customize for your deployment:

```bash
cp infra/terraform/examples/aws-vllm.tfvars.example infra/terraform/stacks/aws-vllm/terraform.tfvars
```

## Quick-start

```bash
# 1. Warm the shared provider cache (once)
make tf-cache-warm

# 2. Init and validate a stack
make tf-init STACK=stacks/aws-vllm
make tf-validate STACK=stacks/aws-vllm

# 3. Plan and apply (standard Terraform workflow)
cd infra/terraform/stacks/aws-vllm
terraform plan -var-file=terraform.tfvars
terraform apply -var-file=terraform.tfvars
```

## Provider-version consistency

All stacks must use the same provider versions declared in `infra/terraform/versions.tf`. Verify with:

```bash
make tf-versions-check
```
