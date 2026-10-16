FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
RUN pip install --prefer-binary --index-url "$TORCH_INDEX_URL" "torch>=2,<3" "torchvision>=0.17,<1"
COPY --from=shreckdoc / /tmp/shreckdoc
RUN pip install --no-deps /tmp/shreckdoc
COPY pyproject.toml ./
# Keep dependency layers cached when application code changes.
RUN python -c 'import subprocess, tomllib; subprocess.run(["pip", "install", "--prefer-binary", *tomllib.load(open("pyproject.toml", "rb"))["project"]["dependencies"]], check=True)'
COPY app ./app
RUN pip install --no-deps .

EXPOSE 8120
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8120"]
