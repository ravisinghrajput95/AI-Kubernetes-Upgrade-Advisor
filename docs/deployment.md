# Deployment Guide

## Modes

| Mode | When | How |
|---|---|---|
| **CLI on an operator machine** | Ad-hoc assessments, CI jobs | `pip install`, kubeconfig present |
| **Server in-cluster** | Team-shared UI/API, Prometheus scraping | Helm chart, snapshots uploaded |
| **Air-gapped** | Regulated environments | Snapshot where kubectl lives; assess where the KB lives |

## CLI

```bash
pip install "k8s-upgrade-advisor[api,rag] @ git+https://github.com/ravisinghrajput95/AI-Kubernetes-Upgrade-Advisor"
k8s-upgrade-advisor snapshot cluster.json --context prod --kubeconfig ~/.kube/config
k8s-upgrade-advisor collect -s 1.28 -t 1.31 && k8s-upgrade-advisor build-kb -s 1.28 -t 1.31
OPENAI_API_KEY=sk-... k8s-upgrade-advisor assess -s 1.28 -t 1.31 --snapshot cluster.json
```

## Container

Release images are published by CI on version tags:
`ghcr.io/ravisinghrajput95/k8s-upgrade-advisor:<tag>`. To build locally:

```bash
docker build -t k8s-upgrade-advisor .
docker run -p 8080:8080 -e OPENAI_API_KEY=sk-... \
  -v advisor-data:/data k8s-upgrade-advisor
```

The image is intentionally kubectl-free: the server assesses **uploaded snapshots**.
Live collection happens wherever the kubeconfig lives (operator laptop, CI runner),
which keeps cluster credentials out of the service.

## Helm

```bash
kubectl create secret generic advisor-openai --from-literal=OPENAI_API_KEY=sk-...
helm install advisor deploy/helm/k8s-upgrade-advisor \
  --set llm.existingSecret=advisor-openai \
  --set persistence.enabled=true \
  --set serviceMonitor.enabled=true
kubectl port-forward svc/advisor 8080:8080
```

Chart properties: non-root (uid 10001), read-only root filesystem, seccomp
RuntimeDefault, `/livez` + `/readyz` probes, `/data` volume for KB + reports,
optional PVC and Prometheus `ServiceMonitor`. Omit `llm.existingSecret` to run in
deterministic/dry-run mode.

## CI gating example (GitHub Actions)

```yaml
- name: Kubernetes upgrade readiness gate
  run: |
    pip install "k8s-upgrade-advisor @ git+https://…"
    k8s-upgrade-advisor assess -s 1.28 -t 1.31 \
      --snapshot cluster-snapshot.json --dry-run \
      --json --fail-on not-ready > readiness.json
```

Exit codes: `0` pass · `20` readiness gate failed · `69` dependency unavailable ·
`78` bad configuration.

## Configuration reference

Everything is a `K8S_ADVISOR_` env var with `__` for nesting; see `config.py`.
Common ones:

| Variable | Default | Meaning |
|---|---|---|
| `K8S_ADVISOR_LLM__MODEL` | `gpt-4o` | Chat model |
| `K8S_ADVISOR_LLM__PROVIDER` | `openai` | `none` disables the LLM stage |
| `K8S_ADVISOR_KNOWLEDGE__EMBEDDING_BACKEND` | `auto` | `sentence-transformers` / `hash` |
| `K8S_ADVISOR_RETRIEVAL__TOP_K` | `24` | Chunks handed to the LLM |
| `K8S_ADVISOR_PATHS__KB_DIR` | `./kb` | Knowledge base location |
| `K8S_ADVISOR_SERVER__MAX_CONCURRENT_ASSESSMENTS` | `4` | In-flight assessment limit (503 beyond it) |
| `K8S_ADVISOR_PATHS__REPORTS_KEEP` | `200` | Report retention (assessments kept on disk, 0 = unlimited) |
| `K8S_ADVISOR_OBSERVABILITY__LOG_JSON` | `false` | JSON logs |
| `K8S_ADVISOR_OBSERVABILITY__OTEL_ENABLED` | `false` | OTLP tracing (extra `[otel]`) |
| `OPENAI_API_KEY` | — | LLM credential |

## Monitoring

Prometheus metrics at `/metrics`:
`advisor_assessments_total{outcome}` · `advisor_assessment_duration_seconds` ·
`advisor_assessment_stage_seconds{stage}` · `advisor_assessments_in_flight` ·
`advisor_llm_requests_total{provider,status}` · `advisor_llm_request_seconds` ·
`advisor_retrieval_seconds` · `advisor_kb_chunks` ·
`advisor_kb_build_timestamp_seconds` (alert on staleness) ·
`advisor_doc_fetches_total{status}` · `advisor_http_*`.

Suggested alerts: KB older than 30d; LLM error-rate > 20% over 15m; readiness
endpoint flapping.
