# syntax=docker/dockerfile:1.7
#
# FinSecure Pay - multi-stage, distroless, non-root image.
#
# Stage 1 installs locked, hash-verified wheels into /install.
# Stage 2 copies only those packages and the app source onto a distroless base
# that has no shell, no package manager and runs as UID 65532 (nonroot).
#
# The builder's Python minor version MUST match the runtime's (3.13 on
# distroless debian13) because compiled wheels (pydantic-core) are ABI-specific.
# Debian 12 distroless images are no longer updated, so they are not used.
#
# Reproducibility: dependencies are pinned with hashes; pin the base images
# by digest with scripts/pin-base-images.sh before a release, and build with
#   --build-arg SOURCE_DATE_EPOCH=$(git log -1 --format=%ct)

ARG BUILDER_IMAGE=python:3.13-slim-trixie
ARG RUNTIME_IMAGE=gcr.io/distroless/python3-debian13:nonroot

# ---------------------------------------------------------------- builder
FROM ${BUILDER_IMAGE} AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build
COPY requirements.txt .
# --require-hashes: refuse anything not in the lock file (supply-chain safety)
# --only-binary:   wheels only, no compilers or build tools needed
RUN pip install --require-hashes --no-deps --only-binary=:all: \
        --target /install -r requirements.txt \
 && find /install -type d -name '__pycache__' -prune -exec rm -rf {} +

# ---------------------------------------------------------------- runtime
FROM ${RUNTIME_IMAGE} AS runtime

ARG VERSION=dev
ARG GIT_COMMIT=unknown
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.title="finsecure-pay" \
      org.opencontainers.image.description="FinSecure Pay transaction validation API" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${GIT_COMMIT}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.source="https://github.com/htetthanthar/finsecure-pay" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONPATH=/app/packages:/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app
# Files stay root-owned and read-only to the app user: it cannot modify its own code.
COPY --from=builder /install /app/packages
COPY app/ /app/app/

USER 65532:65532
EXPOSE 8080

# Used by `docker run` locally; Kubernetes uses its own probes instead.
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD ["/usr/bin/python3.13", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"]

ENTRYPOINT ["/usr/bin/python3.13", "-m", "app"]
