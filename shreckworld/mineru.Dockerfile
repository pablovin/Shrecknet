# NVIDIA GPU inference service. Keep the package and model tier pinned together.
FROM vllm/vllm-openai:v0.21.0

ARG MINERU_VERSION=4.1.7
ARG MINERU_MODEL_SOURCE=huggingface
ENV MINERU_MODEL_SMALL_BACKEND=torch \
    MINERU_MODEL_VLM_ENGINE=vllm \
    MINERU_TELEMETRY_DISABLED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends libgl1 curl \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m pip install --break-system-packages "mineru[torch]==${MINERU_VERSION}"
RUN mineru-kit models download --tier standard --small-backend torch --vlm-engine vllm -s "${MINERU_MODEL_SOURCE}"

EXPOSE 8000
ENTRYPOINT ["/bin/bash", "-c", "export MINERU_MODEL_SOURCE=local && exec \"$@\"", "--"]
