# vLLM model-server image for gludd local inference.
#
# Build args:
#   MODEL_ID              HuggingFace model id (e.g. meta-llama/Llama-3.1-8B-Instruct)
#   VLLM_VERSION          vllm/vllm-openai tag (e.g. v0.6.3)
#   CUDA_VERSION          CUDA runtime tag fragment (e.g. 12.4.1)
#   TORCH_CUDA_ARCH_LIST  comma-separated compute capabilities (e.g. "8.0;8.6;9.0")
#
# The image pre-caches the model at build time so first-request latency on
# `docker compose up` is just vLLM cold-start, not network download.

ARG VLLM_VERSION="v0.6.3"
FROM vllm/vllm-openai:${VLLM_VERSION}

ARG MODEL_ID="meta-llama/Llama-3.1-8B-Instruct"
ARG CUDA_VERSION="12.4.1"
ARG TORCH_CUDA_ARCH_LIST="8.0;8.6;9.0"

ENV MODEL_ID=${MODEL_ID} \
    CUDA_VERSION=${CUDA_VERSION} \
    TORCH_CUDA_ARCH_LIST=${TORCH_CUDA_ARCH_LIST} \
    HF_HOME=/root/.cache/huggingface \
    HUGGINGFACE_HUB_CACHE=/root/.cache/huggingface/hub

# Model-cache layer: pre-download weights so runtime start is fast.
# This layer is cached by docker and only re-runs when MODEL_ID changes.
RUN --mount=type=cache,target=/root/.cache/huggingface \
    pip install --no-cache-dir huggingface_hub \
    && python3 -c "from huggingface_hub import snapshot_download; snapshot_download('${MODEL_ID}')"

EXPOSE 8000

# Default launch; overridden by docker-compose for tensor-parallel etc.
# ${MODEL_ID} is expanded from ENV at runtime.
ENTRYPOINT ["sh", "-c", "vllm serve \"${MODEL_ID}\" --host 0.0.0.0 --port 8000"]
