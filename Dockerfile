FROM node:26-alpine@sha256:0b36e8c136b94cd4fcf02188228e76c31ad5872eef3fec8cbd2eee500cfd9e80 AS frontend-build
WORKDIR /frontend
RUN apk add --no-cache python3
COPY scripts/build_extension.py /scripts/build_extension.py
COPY extensions/application-companion /extensions/application-companion
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY frontend/ ./
COPY hiring_scraper/app/latex_template.json /hiring_scraper/app/latex_template.json
RUN npm run build

FROM ghcr.io/astral-sh/uv:0.13.4@sha256:9f130421e930bfbdc563c00740466603f8723158e90297d07957c2294640262b AS uv

# Keep Python within the wheel support of the locked onnxruntime dependency.
FROM python:3.14-slim-bookworm@sha256:48b13b003dda20b16f9442b8475aa05fe21bf6579a8c881db92ffb4d8fd20f83
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    SEMANTIC_MODEL_DIR=/opt/models/multilingual-e5-small \
    HF_HUB_DISABLE_TELEMETRY=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libreoffice-writer fonts-liberation texlive-luatex texlive-latex-recommended \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project
COPY alembic.ini ./
COPY hiring_scraper ./hiring_scraper
COPY fixtures ./fixtures
RUN /app/.venv/bin/python -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='intfloat/multilingual-e5-small', revision='ccc66d3bcd826f577e26b9a4072cc5fe3a7ad6a3', local_dir='/opt/models/multilingual-e5-small', allow_patterns=['onnx/model_O4.onnx', 'tokenizer.json'])"
COPY scripts/ci/browser_sandbox_smoke.py /usr/local/bin/hiring-browser-sandbox-smoke.py
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev
RUN /app/.venv/bin/python -m playwright install --with-deps --only-shell chromium
COPY --from=frontend-build /frontend/dist ./frontend/dist
COPY docker/entrypoint.py /usr/local/bin/hiring-entrypoint.py
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
ENTRYPOINT ["python", "/usr/local/bin/hiring-entrypoint.py"]
