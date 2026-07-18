# syntax=docker/dockerfile:1

# ── Build stage ──────────────────────────────────────────────────────────────
FROM python:3.14-slim AS build

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --prefix=/install ".[api]"

# ── Runtime stage ────────────────────────────────────────────────────────────
FROM python:3.14-slim

LABEL org.opencontainers.image.title="k8s-upgrade-advisor" \
      org.opencontainers.image.description="AI Kubernetes upgrade intelligence platform" \
      org.opencontainers.image.source="https://github.com/ravisinghrajput95/k8s-upgrade-advisor"

RUN useradd --create-home --uid 10001 advisor
# Frontend ships as package data inside the wheel — nothing else to copy.
COPY --from=build /install /usr/local

# The server assesses uploaded snapshots; kubectl is intentionally not in the
# image. Live collection happens where the kubeconfig lives:
#   k8s-upgrade-advisor snapshot cluster.json   (from an operator machine)
USER 10001
WORKDIR /app
ENV K8S_ADVISOR_PATHS__KB_DIR=/data/kb \
    K8S_ADVISOR_PATHS__REPORTS_DIR=/data/reports \
    K8S_ADVISOR_OBSERVABILITY__LOG_JSON=true

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/livez')"

ENTRYPOINT ["k8s-upgrade-advisor"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080"]
