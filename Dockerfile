FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY pyproject.toml README.md requirements.lock ./
COPY src ./src
RUN python -m pip wheel --wheel-dir /wheels --requirement requirements.lock \
    && python -m pip wheel --no-deps --wheel-dir /wheels .

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN useradd --create-home --uid 10001 app
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/* && rm -rf /wheels
USER app
WORKDIR /home/app
EXPOSE 8080
CMD ["uvicorn", "territory_api.api:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
