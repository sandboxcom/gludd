#!/bin/bash
#SBATCH --job-name=gludd-vllm-serve
#SBATCH --partition=${PARTITION}
#SBATCH --gres=gpu:${GPU_COUNT}:${GPU_TYPE}
#SBATCH --time=${MAX_HOURS}:00:00
#SBATCH --mem=${MEM_GB}G
#SBATCH --nodes=1
#SBATCH --output=${ARTIFACT_DIR}/vllm-%j.out

# ---------------------------------------------------------------------------
# gludd vLLM model server — Slurm batch template.
# Variables (${VAR}) are substituted by VllmSlurmDeployment.render_script()
# at submit time. Every value is fail-closed validated before interpolation.
# ---------------------------------------------------------------------------

set -euo pipefail

mkdir -p "${ARTIFACT_DIR}"
LOG_FILE="${ARTIFACT_DIR}/vllm.log"

# --- module loads (operator-configurable) ---
${MODULE_LOADS}

# --- preflight: confirm a GPU is present on this node ---
echo "[$(date -Is)] preflight: nvidia-smi" | tee -a "${LOG_FILE}"
if ! command -v nvidia-smi >/dev/null 2>&1; then
    echo "[$(date -Is)] FATAL: nvidia-smi not found on \$(hostname)" | tee -a "${LOG_FILE}"
    cat > "${ARTIFACT_DIR}/servable.json" <<EOF
{"servable_url": null, "model_id": "${MODEL_ID}", "engine": "vllm",
 "slurm_job_id": "${SLURM_JOB_ID}", "port": ${PORT},
 "error": "nvidia-smi not found",
 "diagnostics": "$(scontrol show job ${SLURM_JOB_ID} 2>&1 | tr '\n' ' ')"}
EOF
    exit 1
fi
if ! nvidia-smi >/dev/null 2>&1; then
    echo "[$(date -Is)] FATAL: nvidia-smi reported no GPU" | tee -a "${LOG_FILE}"
    cat > "${ARTIFACT_DIR}/servable.json" <<EOF
{"servable_url": null, "model_id": "${MODEL_ID}", "engine": "vllm",
 "slurm_job_id": "${SLURM_JOB_ID}", "port": ${PORT},
 "error": "nvidia-smi failed: no GPU detected",
 "diagnostics": "$(scontrol show job ${SLURM_JOB_ID} 2>&1 | tr '\n' ' ')"}
EOF
    exit 1
fi
echo "[$(date -Is)] preflight OK: $(nvidia-smi -L)" | tee -a "${LOG_FILE}"

# --- launch vLLM ---
echo "[$(date -Is)] launching vllm serve ${MODEL_ID}" | tee -a "${LOG_FILE}"
vllm serve "${MODEL_ID}" \
    --host 0.0.0.0 \
    --port ${PORT} \
    --tensor-parallel-size ${GPU_COUNT} \
    --max-model-len ${MAX_CTX} \
    ${EXTRA_ARGS} \
    >> "${LOG_FILE}" 2>&1 &
VLLM_PID=$!

# --- post-start health probe loop ---
HOSTNAME=$(hostname)
SERVABLE_URL="http://${HOSTNAME}:${PORT}"
HEALTH_URL="${SERVABLE_URL}/health"
PROBE_DEADLINE=$(( $(date +%s) + ${MAX_HOURS} * 3600 / 2 ))
PROBE_DEADLINE=$(( PROBE_DEADLINE < $(date +%s) + 600 ? $(date +%s) + 600 : PROBE_DEADLINE ))

echo "[$(date -Is)] probing ${HEALTH_URL} until ready" | tee -a "${LOG_FILE}"
while [ $(date +%s) -lt ${PROBE_DEADLINE} ]; do
    if ! kill -0 "${VLLM_PID}" 2>/dev/null; then
        echo "[$(date -Is)] FATAL: vllm process exited prematurely" | tee -a "${LOG_FILE}"
        cat > "${ARTIFACT_DIR}/servable.json" <<EOF
{"servable_url": null, "model_id": "${MODEL_ID}", "engine": "vllm",
 "slurm_job_id": "${SLURM_JOB_ID}", "port": ${PORT},
 "error": "vllm process exited prematurely",
 "diagnostics": "$(scontrol show job ${SLURM_JOB_ID} 2>&1 | tr '\n' ' '); $(sacct -j ${SLURM_JOB_ID} --format=State,ExitCode --noheader --parsable2 2>&1 | tr '\n' ' ')"}
EOF
        exit 1
    fi
    if curl -sf -o /dev/null "${HEALTH_URL}"; then
        echo "[$(date -Is)] vllm healthy at ${SERVABLE_URL}" | tee -a "${LOG_FILE}"
        cat > "${ARTIFACT_DIR}/servable.json" <<EOF
{"servable_url": "${SERVABLE_URL}", "model_id": "${MODEL_ID}", "engine": "vllm",
 "slurm_job_id": "${SLURM_JOB_ID}", "port": ${PORT}, "started_at": $(date +%s)}
EOF
        # Wait on the vllm process for the remainder of the allocation.
        wait "${VLLM_PID}"
        exit 0
    fi
    sleep 5
done

# --- probe timed out ---
echo "[$(date -Is)] FATAL: health probe timed out" | tee -a "${LOG_FILE}"
kill "${VLLM_PID}" 2>/dev/null || true
cat > "${ARTIFACT_DIR}/servable.json" <<EOF
{"servable_url": null, "model_id": "${MODEL_ID}", "engine": "vllm",
 "slurm_job_id": "${SLURM_JOB_ID}", "port": ${PORT},
 "error": "health probe timed out",
 "diagnostics": "$(scontrol show job ${SLURM_JOB_ID} 2>&1 | tr '\n' ' ')"}
EOF
exit 1
