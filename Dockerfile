# Pokemon card collection: web app image. Built by GitHub Actions (.github/workflows/docker.yml) and published to ghcr.io.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    UPLOAD_DIR=/data/uploads HOST=0.0.0.0 PORT=8000

# libglib is needed by OpenCV; curl is for the health check. Everything else ships as wheels.
RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN chmod +x docker/entrypoint.sh

# 568 is the "apps" user on TrueNAS SCALE, so files on a host path it owns are writable without extra setup.
RUN groupadd -g 568 apps && useradd -u 568 -g apps -M -s /usr/sbin/nologin apps \
    && mkdir -p /data/uploads && chown -R apps:apps /data
USER 568:568

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1
ENTRYPOINT ["docker/entrypoint.sh"]
