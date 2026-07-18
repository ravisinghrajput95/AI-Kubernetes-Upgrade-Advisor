# API Reference

Interactive OpenAPI docs are served at `/docs` when the server runs. Summary:

## Health & observability

| Endpoint | Purpose |
|---|---|
| `GET /healthz`, `GET /livez` | Liveness — process up |
| `GET /readyz` | Readiness — reports KB presence/size/age (KB absence is reported, not failing) |
| `GET /metrics` | Prometheus exposition |

## Assessments

### `POST /api/v1/assessments`

```json
{
  "source_version": "1.28",
  "target_version": "1.31",
  "dry_run": true,
  "snapshot": { "schema_version": 1, "kubectl": { "...": {} }, "helm_releases": [] }
}
```

Returns the full `AssessmentReport` (200), `422` for invalid versions/snapshot,
`503` when a required dependency is down. Reports are also persisted to
`reports/<id>.{md,html,json}`.

Snapshots are produced by `k8s-upgrade-advisor snapshot out.json` — the server
itself never contacts clusters (see deployment.md).

### `GET /api/v1/assessments`
Recent assessment summaries (id, versions, verdict, scores, finding counts).

### `GET /api/v1/assessments/{id}`
Full report JSON. `404` if the id has aged out of the in-memory window (disk
artifacts in `reports/` remain).

### `GET /api/v1/assessments/{id}/html` · `GET /api/v1/assessments/{id}/markdown`
Rendered artifacts.

## Report schema (stable machine interface)

Top-level fields of `AssessmentReport` (see `models/assessment.py`):
`id`, `source_version`, `target_version`, `version_path[]`, `profile{}`,
`readiness{score,cap,cap_reason,confidence,verdict}`, `executive_summary`,
`findings[]` (each: severity, category, origin, blocking, evidence[],
remediation), `compatibility_matrix[]`, `plan{steps[],rollback[],checklists}`,
`downtime{}`, `unknown_risks[]`, `citations[]`, `evidence_metrics{}`, `llm{}`.

`schema_version: 2` — breaking schema changes bump it.
