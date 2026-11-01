FROM python:3.11-slim AS builder

WORKDIR /build

ENV PIP_NO_CACHE_DIR=1

RUN python -m pip install --upgrade pip wheel

COPY backend/requirements-docker.txt .
RUN python -m pip wheel --wheel-dir /wheels -r requirements-docker.txt


FROM python:3.11-slim AS runtime

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV FASTEMBED_CACHE_PATH=/app/.cache/fastembed
ENV HF_HOME=/app/.cache/huggingface

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /wheels /wheels
COPY backend/requirements-docker.txt /requirements-docker.txt
RUN python -m pip install --no-index --find-links=/wheels -r /requirements-docker.txt \
    && rm -rf /wheels /requirements-docker.txt

RUN mkdir -p /app/data /app/data/reports /app/data/bank_policies /app/.cache/fastembed \
    && addgroup --system regulamind \
    && adduser --system --ingroup regulamind --home /app regulamind \
    && chown -R regulamind:regulamind /app

COPY --chown=regulamind:regulamind backend ./backend
COPY --chown=regulamind:regulamind retrieve.py embedding_service.py ./

RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5')"
RUN chown -R regulamind:regulamind /app/.cache

USER regulamind

EXPOSE 8000

CMD ["sh", "-c", "uvicorn backend.api:app --host 0.0.0.0 --port ${PORT:-8000}"]