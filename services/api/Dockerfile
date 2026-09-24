# API och bakgrundsarbetare (samma avbild, olika kommandon i docker-compose.yml).
FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
COPY pyproject.toml ./
COPY src ./src
RUN pip wheel --wheel-dir /wheels ".[ai,s3,worker]"

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 RAI_APP_ROOT=/app RAI_STORAGE_PATH=/data/storage
RUN useradd --system --uid 10001 --home /app app && mkdir -p /data/storage && chown app /data/storage
WORKDIR /app
COPY --from=build /wheels /wheels
RUN pip install /wheels/*.whl && rm -rf /wheels
COPY alembic.ini ./
COPY migrations ./migrations
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/health').status != 200)"
CMD ["uvicorn", "redovisningai.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
