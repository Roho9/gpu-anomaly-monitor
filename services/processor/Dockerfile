# Container image for the FastAPI dashboard/read service (ECS Fargate).
# The same image also carries the gpumon package used by the Lambda handlers.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ARGUS_MODE=aws

WORKDIR /app

# Install the gpumon package (copied from the repo root at build time) + AWS deps.
COPY pyproject.toml README.md ./
COPY gpumon ./gpumon
COPY runbooks ./runbooks
RUN pip install --upgrade pip && pip install ".[aws]"

# Lambda handlers live alongside the package.
COPY services/processor/handlers.py ./handlers.py

EXPOSE 8000
# Fargate runs the FastAPI app; Lambda overrides the entrypoint with its handler.
CMD ["uvicorn", "gpumon.api:app", "--host", "0.0.0.0", "--port", "8000"]
