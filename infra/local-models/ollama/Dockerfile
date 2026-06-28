# Minimal ollama wrapper for gludd dev/CI inference.
#
# Pre-pulls the model at build time so `docker compose up` is immediately
# servable. Not a production substrate — use vLLM or llama.cpp for real load.

FROM ollama/ollama:latest

ARG OLLAMA_MODEL=llama3.2

# Start the ollama daemon in the background, pull the model, then stop it.
# The pulled model persists in /root/.ollama for runtime use.
RUN nohup ollama serve > /tmp/ollama.log 2>&1 & \
    sleep 5 && \
    ollama pull ${OLLAMA_MODEL} && \
    pkill ollama || true

ENV OLLAMA_MODEL=${OLLAMA_MODEL} \
    OLLAMA_HOST=0.0.0.0:11434

EXPOSE 11434

# Runtime: start the daemon (model is already cached from the build step).
ENTRYPOINT ["sh", "-c", "ollama serve & sleep 3 && ollama run ${OLLAMA_MODEL}"]
