FROM node:20-bookworm-slim AS codex

# OAuth-first LLM backend: Codex CLI (installed in-container; auth state mounted from host).
ARG CODEX_CLI_VERSION=0.101.0
RUN set -eux; \
    npm config set fund false; \
    npm config set audit false; \
    npm config set update-notifier false; \
    npm config set fetch-retries 5; \
    npm config set fetch-retry-factor 2; \
    npm config set fetch-retry-mintimeout 20000; \
    npm config set fetch-retry-maxtimeout 120000; \
    npm install -g @openai/codex@${CODEX_CLI_VERSION}

FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NODE_PATH=/usr/local/lib/node_modules

WORKDIR /app

COPY --from=codex /usr/local/bin/ /usr/local/bin/
COPY --from=codex /usr/local/lib/node_modules/ /usr/local/lib/node_modules/

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app

EXPOSE 8787

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8787"]
