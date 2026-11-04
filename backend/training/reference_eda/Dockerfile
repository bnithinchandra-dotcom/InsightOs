FROM nvidia/cuda:12.4.1-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    git python3 python3-venv python3-pip curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --upgrade pip setuptools wheel
RUN python3 -m pip install --index-url https://download.pytorch.org/whl/cu124 \
    torch==2.6.0

WORKDIR /workspace/backend
COPY backend/training/reference_eda/requirements-training.txt /tmp/requirements-training.txt
RUN python3 -m pip install -r /tmp/requirements-training.txt

COPY backend/training/reference_eda /workspace/backend/training/reference_eda

ENV HF_HOME=/workspace/cache/huggingface \
    TRANSFORMERS_CACHE=/workspace/cache/huggingface \
    INSIGHTOS_TRAINING_ARTIFACTS=/workspace/artifacts

ENTRYPOINT ["python3", "-m", "training.reference_eda.cli"]
CMD ["environment-check"]
