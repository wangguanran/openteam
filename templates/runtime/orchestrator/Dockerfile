ARG TEAMOS_PYTHON_BASE_IMAGE=python:3.11-slim-bookworm
FROM ${TEAMOS_PYTHON_BASE_IMAGE}

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG ALL_PROXY
ARG NO_PROXY
ARG http_proxy
ARG https_proxy
ARG all_proxy
ARG no_proxy
ARG TEAMOS_APT_MIRROR_URL=http://deb.debian.org/debian
ARG TEAMOS_APT_SECURITY_MIRROR_URL=http://deb.debian.org/debian-security
ARG TEAMOS_CREWAI_GIT_URL=https://github.com/wangguanran/crewAI.git
ARG TEAMOS_CREWAI_GIT_REF=main
ARG TEAMOS_PIP_INDEX_URL=https://pypi.org/simple
ARG TEAMOS_PIP_TRUSTED_HOST=
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_INDEX_URL=${TEAMOS_PIP_INDEX_URL} \
    PIP_TRUSTED_HOST=${TEAMOS_PIP_TRUSTED_HOST} \
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
      sed -i "s|http://deb.debian.org/debian|${TEAMOS_APT_MIRROR_URL}|g" /etc/apt/sources.list.d/debian.sources; \
      sed -i "s|http://deb.debian.org/debian-security|${TEAMOS_APT_SECURITY_MIRROR_URL}|g" /etc/apt/sources.list.d/debian.sources; \
    fi

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN python - <<'PY' > /tmp/requirements-base.txt
from pathlib import Path

req = Path("/app/requirements.txt").read_text(encoding="utf-8").splitlines()
for line in req:
    if line.strip().startswith("crewai"):
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
             clone --filter=blob:none --sparse --depth 1 --single-branch --branch "${TEAMOS_CREWAI_GIT_REF}" "${TEAMOS_CREWAI_GIT_URL}" /tmp/crewai-src \
         && git -C /tmp/crewai-src sparse-checkout set lib/crewai \
         && success=1 && break; \
         echo "CrewAI clone attempt ${attempt} failed; retrying in 3s"; \
         sleep 3; \
       done \
    && [ "${success}" = "1" ] \
    && pip install --no-cache-dir /tmp/crewai-src/lib/crewai \
    && rm -rf /tmp/crewai-src

COPY app /app/app

EXPOSE 8787

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8787"]
