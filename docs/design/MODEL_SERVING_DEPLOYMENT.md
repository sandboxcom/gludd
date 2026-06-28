# Model Serving Deployment — Design Doc

**Status:** Proposed
**Author:** infra workstream
**Last updated:** 2026-06-28

## Purpose

gludd has three observable compute substrates that a model server (vLLM or
llama.cpp) can land on:

1. **A Slurm-managed HPC cluster** — operator already arbitrates GPU access via
   Slurm fairshare; the nodes have drivers, CUDA, and a shared filesystem.
2. **A bare-metal / long-lived VM** the operator owns outright (no scheduler).
3. **Cloud GPU instances** provisioned via the existing `TerraformGenerator`
   (`src/general_ludd/infra/terraform.py::_generate_*`).

Plus a dev/single-host substrate:

4. **Local docker / podman** on the operator's workstation.

This doc records the tradeoff between them and pins the decision: **when the
operator has a Slurm cluster, the model server is deployed as a Slurm batch
job.** Ansible-into-systemd and Terraform do NOT apply on a Slurm cluster —
the former competes with the scheduler for node control, the latter assumes a
cloud API that does not exist for on-prem HPC.

Grounds in:
- `src/general_ludd/infra/slurm.py` — `SlurmAdapter` (sbatch/sacct/scancel,
  fail-closed argv validation, local + REST paths).
- `src/general_ludd/infra/local_inference.py` — `LocalInferenceManager`
  (already spawns `vllm serve` / `llama_cpp.server` locally; has an
  `engine="slurm"` branch that today emits a one-line `--wrap`).
- `src/general_ludd/infra/terraform.py::_generate_aws` / `_generate_gcp` /
  `_generate_azure` / `_generate_runpod` / `_generate_vast` — the cloud path.
- `docs/design/TERRAFORM_INFRA_STRUCTURE.md` — the hybrid static-module +
  dynamic-composition plan for the cloud path.

---

## 1. The three production paths

| Path | When | Pros | Cons |
|---|---|---|---|
| **Slurm batch script** (`infra/slurm/{vllm,llamacpp}.sbatch`) | HPC cluster with Slurm; ephemeral GPU allocation | Uses the scheduler the operator already runs; fairshare + accounting + QoS for free; no cloud spend; nodes already have GPUs + drivers + a shared filesystem | Cold-start per job (no warm pool); no auto-scale; assumes a shared filesystem for the artifact directory; the server is bound to the job's walltime |
| **Ansible role deploying vLLM/llama.cpp as a systemd service** | Bare-metal or long-lived VMs the operator controls outright | Persistent service; fast subsequent requests (warm weights); simple ops (`systemctl restart`) | Does NOT fit Slurm's ephemeral-allocation model — the scheduler owns node state, a hand-installed systemd unit competes with it; no fairshare; no accounting |
| **Terraform** (existing `_generate_aws` / `_generate_gcp` / `_generate_azure` / `_generate_runpod` / `_generate_vast`) | Cloud GPU instances | Auto-scale; pay-per-use; isolated per-tenant; the only path when there is no on-prem GPU | Cloud spend; not applicable when Slurm is the backing scheduler — there is no cloud API to call |

Plus the dev/single-host path:

| **Local docker / podman** (`infra/local-models/{vllm,llamacpp,ollama}/`) | Dev workstation, single-host inference, CI smoke | Same ergonomics as every other gludd agent that runs locally; no scheduler; reproducible image | Not a production substrate — single host, no fairshare, no accounting |

---

## 2. Decision criteria

**When the operator has a Slurm cluster, slurm batch script submission is the
right answer** because Slurm already arbitrates GPU access on those hosts.
Two structural reasons:

1. **Node ownership.** A Slurm compute node is in one of three states at any
   moment: `ALLOCATED` to a job, `MIXED`, or `IDLE`. The scheduler controls
   transitions. A hand-installed systemd unit that pins a GPU is invisible to
   the scheduler — Slurm will happily allocate the same GPU to a second job
   and both workloads corrupt each other. Submitting the model server AS a
   Slurm job means the scheduler's `Gres=Gpu` accounting is the single source
   of truth for who owns the GPU.
2. **Walltime + fairshare.** A long-lived systemd service bypasses the
   fairshare/QoS policy the operator spent time tuning. A batch job respects
   partition time limits, QoS, and account charge-back — the model server
   costs the right project the right amount of SUs.

**When the operator does NOT have Slurm:**

- If they have **bare metal / long-lived VMs** they own outright (and the
  workload is long-lived), the **Ansible-as-systemd** path applies.
- If they want **cloud GPUs**, the existing **Terraform** path applies
  (`_generate_aws` etc.).
- For **dev / CI / single-host inference**, the **local docker/podman** path
  applies (`infra/local-models/`).

### Recommendation table

| Environment | Path | Entry point |
|---|---|---|
| Slurm HPC cluster | **Slurm batch script** | `SlurmAdapter.submit` → `infra/slurm/vllm.sbatch` |
| Bare-metal / long-lived VM | Ansible → systemd | future work (no scheduler conflict — operator owns the host) |
| Cloud GPU | Terraform (existing) | `TerraformGenerator._generate_<provider>` |
| Dev / CI / single host | Local docker / podman | `make local-model-vllm MODEL_ID=...` |

---

## 3. Sequence diagram — Slurm path

```
 gludd            SlurmAdapter        sbatch script          vllm serve         artifact file
   |                    |                    |                     |                  |
   | submit(model_id,   |                    |                     |                  |
   |  gpu_count, ...)   |                    |                     |                  |
   |------------------->|                    |                     |                  |
   |                    | render template    |                     |                  |
   |                    | (substitute ${VAR})|                     |                  |
   |                    |------------------->|                     |                  |
   |                    | sbatch <script>    |                     |                  |
   |                    |---------+---------->|                     |                  |
   |                    |         |  job_id   |                     |                  |
   |  job_id            |<--------+---------- |                     |                  |
   |<-------------------|                    |                     |                  |
   |                    |                    |                     |                  |
   |                    |           [PENDING → RUNNING on a GPU node]                  |
   |                    |                    |                     |                  |
   |                    |                    | nvidia-smi preflight|                  |
   |                    |                    |----------+--------->|                  |
   |                    |                    |          | OK / FAIL |                 |
   |                    |                    |<---------+---------- |                  |
   |                    |                    | launch vllm serve   |                  |
   |                    |                    |---------+---------->|                  |
   |                    |                    |         | bind :PORT|                  |
   |                    |                    |         |-----+     |                  |
   |                    |                    | curl /health loop   |                  |
   |                    |                    |---------+---------->|                  |
   |                    |                    |         | 200 OK    |                  |
   |                    |                    |<--------+---------- |                  |
   |                    |                    | write servable.json |                  |
   |                    |                    |--------------------+------------------>|
   |                    |                    |                     |    {servable_url}|
   |                    |                    |                     |                  |
   | poll_until_servable(job_id, timeout)    |                     |                  |
   |------------------->| squeue / sacct     |                     |                  |
   |                    | read artifact file |                     |                  |
   |                    |<-------------------+---------------------+------------------|
   | servable_url       |                    |                     |                  |
   |<-------------------|                    |                     |                  |
   |                    |                    |                     |                  |
   | route inference calls to servable_url   |                     |                  |
   |----------------------------------------------------------------------->|                  |
```

Key contract: the sbatch script's ONLY observable side effect is the
`servable.json` file it writes to `${ARTIFACT_DIR}` on the shared filesystem.
gludd never SSHes to the compute node; it reads the artifact file from a path
both sides agreed on before submit. This keeps the gludd daemon off the
compute fabric and lets the scheduler pick any free GPU node.

---

## 4. The artifact contract

The batch script writes a single JSON file on success:

```json
{
  "servable_url": "http://cn01.cluster.local:8000",
  "model_id": "meta-llama/Llama-3.1-8B-Instruct",
  "engine": "vllm",
  "slurm_job_id": "12345",
  "port": 8000,
  "started_at": 1719600000
}
```

Location: `${ARTIFACT_DIR}/servable.json`. `ARTIFACT_DIR` is a Slurm
environment variable exported by the submitter (it points at a path on the
shared filesystem; the scheduler copies it into the job's environment). On
failure the script writes `servable.json` with `servable_url: null` and a
`diagnostics` field containing the `scontrol show job` + `sacct` output, so
the poller can distinguish "still starting" from "permanently failed."

---

## 5. Failure modes and observability

The batch script captures, in order:

1. **Preflight `nvidia-smi` failure** → no GPU on the allocated node. Exit
   non-zero, write `servable.json` with `error: "nvidia-smi failed: ..."` +
   the `scontrol show job ${SLURM_JOB_ID}` output. The poller surfaces this
   as a terminal state within one poll interval.
2. **Health-probe timeout** → vLLM started but never answered `/health` within
   the post-start window. Capture the last 200 lines of the vLLM log
   (teed to `${ARTIFACT_DIR}/vllm.log`) into `diagnostics`, write
   `servable_url: null`, exit non-zero.
3. **Slurm preemption / timeout** → the scheduler kills the job. The
   `squeue`/`sacct` poller sees the transition to `CANCELLED` / `TIMEOUT`
   and returns that state to the caller — no silent hang.

Per the observability invariant (AGENTS.md §"No Unseen Events"), the batch
script tees all output to `${ARTIFACT_DIR}/vllm.log` so a 16-minute model
load never looks like a hung job.

---

## 6. Local docker / podman path

For dev, CI smoke, and single-host inference, gludd ships templated
docker-compose stacks under `infra/local-models/`:

- `vllm/` — `vllm/vllm-openai:${VLLM_VERSION}` base + model-cache layer +
  GPU device reservation. Build args: `MODEL_ID`, `VLLM_VERSION`,
  `CUDA_VERSION`, `TORCH_CUDA_ARCH_LIST`.
- `llamacpp/` — compiles `llama.cpp` server with CUDA support, downloads the
  model at build time. Build args: `LLAMACPP_VERSION`, `MODEL_URL`.
- `ollama/` — minimal wrapper around `ollama/ollama:latest` with a build-time
  model pre-pull. Dev-only.

Operator entry points: `make local-model-vllm MODEL_ID=...`,
`make local-model-llamacpp`, `make local-model-ollama`. Each runs
`docker compose build && docker compose up -d` with the right build args.

This path is the same shape as every other gludd agent that runs locally
(e.g. ollama) — it is NOT a production substrate and does not interact with
the Slurm or Terraform paths.

---

## 7. Future work

- **Warm pool on Slurm.** A sidecar job (one per partition) that holds a
  small pool of pre-loaded models in `SUSPENDED` state, resumed on demand.
  Cuts cold-start from minutes to seconds. Not in scope here; tracked as a
  follow-up because it requires Slurm `--suspend` + a notify socket back to
  gludd.
- **Multi-node tensor parallelism.** The current sbatch template assumes a
  single node (`--nodes=1`). For models that need tensor-parallel across
  nodes, extend with `--nodes=N` + a Ray head/worker launch inside the
  script. Future work — not blocking the single-node case.
- **Ansible-as-systemd role for bare-metal.** The non-Slurm, non-cloud
  production path (operator-owned VMs). Not implemented here; design only.
- **Round-robin across multiple servables.** When the poller returns a URL,
  register it in the model gateway's pool so subsequent calls fan out across
  multiple Slurm-served replicas. Hooks into the existing
  `src/general_ludd/gateway/` routing layer.
- **Health-check watchdog.** A separate Slurm job that pings each
  `servable_url` every N seconds and reaps dead ones. Today the poller only
  checks at submit time; a long-running model server can die mid-job.
