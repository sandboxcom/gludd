# deploy_model_server_slurm

Submits a vLLM or llama.cpp model-server job to a Slurm cluster and polls
until the server is servable.

## When to use this role

Use this role when the operator has a **Slurm-managed HPC cluster** and Slurm
should arbitrate GPU access (fairshare + accounting + QoS). When the operator
does NOT have Slurm:

- Cloud GPU → use the existing Terraform path
  (`docs/design/TERRAFORM_INFRA_STRUCTURE.md`).
- Dev / single host → use `make local-model-vllm` (see
  `infra/local-models/README.md`).

## Variables

See [`defaults/main.yml`](defaults/main.yml). The required ones:

| Variable | Description |
|---|---|
| `model_id` | HuggingFace id (vllm) or path to `.gguf` (llamacpp). |
| `artifact_dir` | Shared-filesystem path the batch script writes `servable.json` to. Must be readable from the controller. |

## Example playbook

```yaml
- hosts: localhost
  roles:
    - role: general_ludd.agent.deploy_model_server_slurm
      vars:
        engine: vllm
        model_id: meta-llama/Llama-3.1-8B-Instruct
        gpu_count: 2
        gpu_type: a100
        port: 8000
        max_hours: 4
        partition: gpu
        artifact_dir: /scratch/gludd/deploy-{{ ansible_date_time.iso8601 }}
        module_loads:
          - cuda/12.3
          - python/3.11
```

## Output

On success, the role registers these facts:

- `slurm_model_server_job_id` — the Slurm job id.
- `slurm_model_server_servable_url` — the URL the server is reachable at.
- `slurm_model_server_error` — empty on success, diagnostics on failure.

The same data is written to `{{ artifact_dir }}/deploy_summary.json`.

## Design

See `docs/design/MODEL_SERVING_DEPLOYMENT.md` for the full tradeoff analysis
of Slurm batch vs. Ansible-as-systemd vs. Terraform vs. local docker.
