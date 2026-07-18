# Operations Runbook

How to run k8s-upgrade-advisor as a service — SLOs, capacity, scaling, DR,
and what to do when alerts fire. Assumes the Helm deployment with a shared
`/data` volume and Prometheus scraping `/metrics`.

## Service level objectives

| SLI | SLO | Measured by |
|---|---|---|
| API availability | 99.5% of requests non-5xx over 30d | `advisor_http_requests_total` |
| Assessment success | 99% of submissions produce a report (LLM-degraded still counts as success — the deterministic report ships) | `advisor_assessments_total{outcome}` |
| Assessment latency | p95 < 300s with LLM, p95 < 10s dry-run | `advisor_assessment_duration_seconds` |
| KB freshness | rebuilt within 30d of the newest fetch | `advisor_kb_build_timestamp_seconds` |
| Grounding quality | median grounding_ratio ≥ 0.5 | `advisor_llm_grounding_ratio` |

Error budget policy: LLM-provider failures consume the *dependency* budget
(`AdvisorLLMDegraded`), not the availability budget — the platform degrades
to deterministic-only by design, and that behaviour is the thing to protect.

## Capacity model (worked example: 10,000 clusters)

- Weekly assessment cadence → ~1,430/day, ~60/hour average; plan for 10×
  peak (fleet-wide upgrade seasons) → **~600/hour**.
- One replica: `max_concurrent_assessments=4`, LLM p50 ~45s → ~5 LLM
  assessments/min ≈ 300/hour. **Two replicas cover peak; HPA to 4 gives
  headroom** (`autoscaling.enabled=true`).
- Dry-run (deterministic) assessments are ~1s and effectively free — CI
  gates should use `--dry-run` and reserve LLM narrative for humans.
- Storage: reports ≈ 300KB/assessment × retention 200/replica-volume ≈ 60MB;
  snapshots are not stored server-side. KB ≈ tens of MB. A 2Gi PVC is ample.
- LLM spend: bound by rate limit (`rate_limit_per_minute`, default 120) and
  collapsed by the idempotency cache (identical resubmissions within
  `idempotency_ttl_seconds` return the cached report).

## Scaling & HA

- Replicas are **stateless over the shared `/data` volume**: report
  retrieval *and* listing read through to disk, so any replica serves any
  report. Use an RWX StorageClass for >1 replica; PDB keeps `minAvailable: 1`.
- Ordering of admission controls on POST: idempotency cache → rate limit
  (429) → concurrency slots (503). Clients should honor `Retry-After`.
- `terminationGracePeriodSeconds: 330` lets in-flight LLM assessments finish
  on rollout; do not shorten it below `llm.timeout_seconds` + 30.
- The KB is read-only at serve time. Rebuilds (`collect` + `build-kb`) should
  run as a Job/CronJob writing to the shared volume; replicas hot-reload via
  the manifest-mtime cache, no restart needed.

## Disaster recovery

State inventory — know what you can lose:

| State | Loss impact | Recovery |
|---|---|---|
| Knowledge base (`/data/kb`) | Assessments degrade to deterministic-only (still correct, lower confidence) | Rebuild from upstream docs: `collect` + `build-kb`, ~5 min. **RTO minutes, RPO irrelevant** |
| Reports (`/data/reports`) | Historical reports 404 | Restore volume snapshot if history matters; new assessments unaffected |
| Config/secrets | Service won't start / no LLM | Redeploy chart + re-create `OPENAI_API_KEY` secret |
| The service itself | Assessments pause; clusters unaffected | `helm install` from the chart + published image |

The platform holds **no cluster credentials and no irreplaceable state** —
worst-case recovery is a fresh install plus a KB rebuild (~15 minutes).
Back up `/data` with volume snapshots or Velero if report history has
compliance value; otherwise retention + regeneration is the DR strategy.

## Alert runbook

### Alert: AdvisorHighErrorRate
5xx ratio above SLO. Check `kubectl logs` for stack traces (filter by
`request_id` from a failing response's `X-Request-ID`). Common causes:
corrupt KB (`readyz` shows `kb_loaded: false` → rebuild), disk full on
`/data` (retention misconfigured), OOM (check restarts; raise memory limit).

### Alert: AdvisorAssessmentFailures
`outcome="failed"` means the *deterministic* stage failed — that's a bug or
malformed snapshots, not LLM weather. Grab a failing `request_id`, find the
snapshot source, reproduce with the CLI against the saved snapshot.

### Alert: AdvisorLLMDegraded
Provider errors burning the dependency budget. Reports still ship
(deterministic + `[degraded]` note). Check provider status page and
`advisor_llm_request_seconds` for timeout clustering; the circuit breaker
fails fast after 5 consecutive errors. No action needed beyond provider
escalation unless sustained >1h — then consider `llm.provider=none` to stop
burning retries.

### Alert: AdvisorAssessmentLatencyP95
Almost always LLM latency (`advisor_assessment_stage_seconds{stage="llm"}`
confirms). Check provider latency; consider a faster model via
`K8S_ADVISOR_LLM__MODEL`. If the `retrieval` stage spikes instead: KB grew
past expectations or the retriever cache is thrashing (KB rebuilt mid-scrape?).

### Alert: AdvisorSaturationSustained
Demand exceeds capacity for 15m+. Scale out (HPA should have done it —
check `kubectl get hpa`), or raise `max_concurrent_assessments` if CPU/memory
headroom exists. Sustained saturation with low CPU = LLM latency, not
compute — more replicas, not bigger ones.

### Alert: AdvisorKnowledgeBaseStale
The KB rebuild Job hasn't succeeded within the freshness SLO. Check the
CronJob's last run; common cause is an upstream doc URL gone stale (fetcher
logs `fetch_failed` with the URL). Grounding quality decays quietly — treat
as a real ticket, not noise.

## Logging & tracing

- Logs are structured (JSON in-cluster via `K8S_ADVISOR_OBSERVABILITY__LOG_JSON`);
  every request carries `request_id`, echoed to clients as `X-Request-ID` —
  ask users for it when triaging.
- Set `K8S_ADVISOR_OBSERVABILITY__OTEL_ENABLED=true` (+ `[otel]` extra in a
  custom image) for traces; assessment stages appear as
  `assess.deterministic` / `assess.retrieval` / `assess.llm` child spans.

## Dashboards worth building

Request rate + error ratio by path · assessments by outcome (incl. cached) ·
stage latency stack (deterministic/retrieval/llm) · in-flight vs capacity ·
LLM tokens + `advisor_llm_cost_usd_total` burn · grounding ratio histogram ·
KB age.
