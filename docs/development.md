# Developer Guide

## Setup

```bash
python3.11+ -m venv .venv && source .venv/bin/activate
pip install -e ".[api,dev]"        # add [rag] for real embeddings, [otel] for tracing
pytest                              # full suite, <1s, zero network
ruff check src tests && ruff format src tests
```

## Repository layout

```
src/k8s_upgrade_advisor/
├── config.py            # pydantic-settings tree (env: K8S_ADVISOR_*)
├── service.py           # assess() orchestrator — CLI/API/tests all call this
├── cli.py               # argparse CLI, exit-code contract
├── models/              # versions, ClusterSnapshot, AssessmentReport, LLMAnalysis
├── collectors/          # kubectl + helm → snapshot; save/load
├── analysis/            # deterministic engines (the source of truth)
│   ├── api_lifecycle.py #   removed-API + behaviour-change tables & detectors
│   ├── profile.py       #   distribution detection, node/workload inventory
│   ├── components.py    #   addon detection: helm > image tag > presence
│   ├── compatibility.py #   component↔k8s support matrices
│   ├── skew.py          #   version skew policy engine
│   ├── planner.py       #   per-distribution plan skeleton + downtime model
│   ├── risk.py          #   evidence metrics, readiness/confidence
│   └── pipeline.py      #   orchestration
├── knowledge/           # sources registry, fetcher, chunker, embedders, store
├── retrieval/           # BM25, hybrid retriever (RRF, version filter, MMR)
├── llm/                 # provider (retry/breaker), prompts, trust-boundary merge
├── reporting/           # markdown/html/json renderers
├── api/                 # FastAPI app
├── frontend/            # single-page UI (package data, served at /)
└── observability/       # structlog + Prometheus metrics
```

## Testing philosophy

- Deterministic engines are pure functions of `ClusterSnapshot` → heavy unit
  coverage with synthetic snapshots per distribution (see `tests/conftest.py`).
- Retrieval tests run on the hash embedder — deterministic across machines,
  no model downloads.
- LLM tests use a `FakeProvider`; the trust-boundary tests
  (`test_llm_merge.py`) are the most important in the repo: they prove a
  hostile/hallucinating model cannot change verdicts, block, or fake citations.
- Integration tests (`-m integration`) exercise the CLI exit-code contract and
  the API surface via `TestClient`.

## Extending

**Add a tracked component** — one entry in `analysis/components.py:REGISTRY`
(detection signals) and optionally `analysis/compatibility.py:MATRICES`
(support matrix) + `knowledge/sources.py` (docs). Tests: a detection case and a
matrix case.

**Add an API removal** (new Kubernetes release) — extend
`analysis/api_lifecycle.py:API_REMOVALS` / `BEHAVIOR_CHANGES`; the detector and
renderer pick it up automatically. Update `compatibility.py` matrices for the new
minor.

**Add an LLM provider** — implement `complete_json(system, user) -> str` and wire
it in `llm/provider.py:make_provider`. The advisor/merge layers are
provider-agnostic.

**Release checklist for a new k8s minor**
1. `API_REMOVALS` / `BEHAVIOR_CHANGES` from the deprecation guide + CHANGELOG
2. `MATRICES` rows for the new minor — mark rows `confidence="inferred"` unless an
   upstream support matrix exists (cite it in `source_url`)
3. Bump `KNOWLEDGE_HORIZON` and `TABLES_LAST_REVIEWED` in `api_lifecycle.py` —
   assessments beyond the horizon are capped until this is done
4. `sources.py` — nothing (CHANGELOG URLs are templated)
5. Fixture + tests for at least one new-removal path
