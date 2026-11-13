# syntax=docker/dockerfile:1.7
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --prefer-binary --index-url "$TORCH_INDEX_URL" 'torch>=2.6,<2.9' 'torchvision>=0.17,<1'
COPY --from=shreckconfig / /tmp/shreckconfig
RUN pip install --no-deps /tmp/shreckconfig

COPY --from=shreckdoc / /tmp/shreckdoc
RUN pip install --no-deps /tmp/shreckdoc
COPY pyproject.toml ./
# Keep dependency layers cached when application code changes.
RUN --mount=type=cache,target=/root/.cache/pip \
    python -c 'import re, subprocess, tomllib; project = tomllib.load(open("pyproject.toml", "rb"))["project"]; requirements = [item for group in (project["dependencies"], project["optional-dependencies"]["openvino"]) for item in group if re.match(r"^\s*([A-Za-z0-9_.-]+)", item).group(1).lower() not in {"torch", "torchvision"}]; subprocess.run(["pip", "install", "--prefer-binary", "--timeout", "120", "--retries", "10", *requirements], check=True)'
COPY app ./app
RUN pip install --no-deps .

EXPOSE 8120
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8120"]
