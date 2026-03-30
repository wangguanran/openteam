ARG OPENTEAM_PYTHON_BASE_IMAGE=python:3.11-slim-bookworm
FROM ${OPENTEAM_PYTHON_BASE_IMAGE}

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG ALL_PROXY
ARG NO_PROXY
ARG http_proxy
ARG https_proxy
ARG all_proxy
ARG no_proxy
ARG OPENTEAM_APT_MIRROR_URL=https://mirrors.ustc.edu.cn/debian
ARG OPENTEAM_APT_SECURITY_MIRROR_URL=https://mirrors.ustc.edu.cn/debian-security
ARG OPENTEAM_CREWAI_GIT_URL=https://github.com/openteam-dev/crewAI.git
ARG OPENTEAM_CREWAI_GIT_REF=main
ARG OPENTEAM_CREWAI_GIT_SHA=
ARG OPENTEAM_NODE_VERSION=24.12.0
ARG OPENTEAM_NODE_DIST_BASE_URL=https://npmmirror.com/mirrors/node
ARG OPENTEAM_OPENCLAW_VERSION=2026.3.2
ARG OPENTEAM_NPM_REGISTRY=https://registry.npmmirror.com
ARG OPENTEAM_PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG OPENTEAM_PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OPENTEAM_REPO_PATH=/openteam \
    PIP_INDEX_URL=${OPENTEAM_PIP_INDEX_URL} \
    PIP_TRUSTED_HOST=${OPENTEAM_PIP_TRUSTED_HOST} \
    HTTP_PROXY=${HTTP_PROXY} \
    HTTPS_PROXY=${HTTPS_PROXY} \
    ALL_PROXY=${ALL_PROXY} \
    NO_PROXY=${NO_PROXY} \
    http_proxy=${http_proxy} \
    https_proxy=${https_proxy} \
    all_proxy=${all_proxy} \
    no_proxy=${no_proxy}

WORKDIR /app

RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
      sed -i "s|http://deb.debian.org/debian|${OPENTEAM_APT_MIRROR_URL}|g" /etc/apt/sources.list.d/debian.sources; \
      sed -i "s|http://deb.debian.org/debian-security|${OPENTEAM_APT_SECURITY_MIRROR_URL}|g" /etc/apt/sources.list.d/debian.sources; \
    fi

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git xz-utils \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "${arch}" in \
      amd64) node_arch='x64' ;; \
      arm64) node_arch='arm64' ;; \
      *) echo "unsupported architecture: ${arch}" >&2; exit 1 ;; \
    esac; \
    curl --http1.1 --retry 5 --retry-delay 3 --retry-all-errors -fsSL "${OPENTEAM_NODE_DIST_BASE_URL}/v${OPENTEAM_NODE_VERSION}/node-v${OPENTEAM_NODE_VERSION}-linux-${node_arch}.tar.xz" -o /tmp/node.tar.xz; \
    tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1; \
    rm -f /tmp/node.tar.xz; \
    node --version; \
    npm --version; \
    npm config set registry "${OPENTEAM_NPM_REGISTRY}"; \
    npm config set fetch-retries 5; \
    npm config set fetch-retry-mintimeout 20000; \
    npm config set fetch-retry-maxtimeout 120000; \
    npm install -g "openclaw@${OPENTEAM_OPENCLAW_VERSION}"; \
    openclaw --help >/dev/null

COPY scaffolds/runtime/orchestrator/requirements.txt /app/requirements.txt
RUN python - <<'PY' > /tmp/requirements-base.txt
from pathlib import Path

req = Path("/app/requirements.txt").read_text(encoding="utf-8").splitlines()
for line in req:
    if line.strip().startswith("crewai") or line.strip().startswith("coverage"):
        continue
    print(line)
PY
RUN pip install --no-cache-dir -r /tmp/requirements-base.txt \
    && success=0 \
    && for attempt in 1 2 3 4 5; do \
         rm -rf /tmp/crewai-src; \
         git -c http.version=HTTP/1.1 \
             -c http.lowSpeedLimit=1024 \
             -c http.lowSpeedTime=30 \
             clone --filter=blob:none --sparse --no-checkout "${OPENTEAM_CREWAI_GIT_URL}" /tmp/crewai-src \
         && git -C /tmp/crewai-src sparse-checkout init --cone \
         && git -C /tmp/crewai-src sparse-checkout set lib/crewai \
         && if [ -n "${OPENTEAM_CREWAI_GIT_SHA}" ]; then \
              git -C /tmp/crewai-src fetch --depth 1 origin "${OPENTEAM_CREWAI_GIT_SHA}" \
              && git -C /tmp/crewai-src checkout --detach FETCH_HEAD; \
            else \
              git -C /tmp/crewai-src fetch --depth 1 origin "${OPENTEAM_CREWAI_GIT_REF}" \
              && git -C /tmp/crewai-src checkout --detach FETCH_HEAD; \
            fi \
         && success=1 && break; \
         echo "CrewAI clone attempt ${attempt} failed; retrying in 3s"; \
         sleep 3; \
       done \
    && [ "${success}" = "1" ] \
    && echo "${OPENTEAM_CREWAI_GIT_SHA:-${OPENTEAM_CREWAI_GIT_REF}}" > /tmp/crewai-src/.openteam_crewai_revision \
    && pip install --no-cache-dir /tmp/crewai-src/lib/crewai \
    && rm -rf /tmp/crewai-src

COPY scaffolds/runtime/orchestrator/app /app/app
COPY . /openteam

EXPOSE 8787

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8787"]
