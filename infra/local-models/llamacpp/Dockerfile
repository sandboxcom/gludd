# llama.cpp model-server image for gludd local inference.
#
# Compiles llama.cpp server with CUDA support and downloads the model at build
# time. Use this for lower-resource GPU or single-GPU inference where vLLM's
# overhead is not warranted.
#
# Build args:
#   LLAMACPP_VERSION   git tag / commit (e.g. b4000)
#   MODEL_URL          direct download URL for the .gguf model file

ARG CUDA_VERSION=12.4.1
FROM nvidia/cuda:${CUDA_VERSION}-devel-ubuntu22.04 AS builder

ARG LLAMACPP_VERSION=b4000
ARG MODEL_URL

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        git build-essential cmake curl ca-certificates \
        libcurl4-openssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Build llama.cpp with CUDA enabled (GGML_CUDA=ON).
RUN git clone --depth 1 --branch ${LLAMACPP_VERSION} https://github.com/ggerganov/llama.cpp /llama.cpp
WORKDIR /llama.cpp
RUN cmake -B build \
        -DGGML_CUDA=ON \
        -DLLAMA_CURL=ON \
        -DCMAKE_BUILD_TYPE=Release \
    && cmake --build build --config Release -j$(nproc)

# Download the model at build time.
RUN mkdir -p /models \
    && if [ -n "${MODEL_URL}" ]; then \
        curl -fL "${MODEL_URL}" -o /models/model.gguf; \
    fi

# ---- runtime stage ----
FROM nvidia/cuda:${CUDA_VERSION}-runtime-ubuntu22.04

RUN apt-get update && apt-get install -y --no-install-recommends \
        libcurl4 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /llama.cpp/build/bin/llama-server /usr/local/bin/llama-server
COPY --from=builder /models/model.gguf /models/model.gguf

EXPOSE 8080

ENTRYPOINT ["llama-server", "--host", "0.0.0.0", "--port", "8080", "--model", "/models/model.gguf"]
