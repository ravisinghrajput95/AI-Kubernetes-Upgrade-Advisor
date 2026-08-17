# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.4.2] - 2026-08-17

### Fixed
- Repointed the dead `prometheus-operator` compatibility doc URL (moved upstream
  to `Documentation/getting-started/compatibility.md`; the old path 404s). The
  full source allow-list was swept — this was the only genuinely dead URL.

### Docs
- Replaced the older `eks-1.26-to-1.29` example with two current,
  cluster-validated reports: a grounded gpt-4o run on **kind 1.32→1.36** (AI
  narrative + `[DOC n]` citations, 33% grounding) and a real **GKE 1.35→1.36**
  managed-cluster report.

## [2.4.1] - 2026-08-17

### Fixed
- **Reported version was stale.** `__version__` was a hardcoded string that had
  not been bumped since 2.2.0, so the API (`/healthz`, `/metrics`
  `advisor_build_info`) and HTML report footer under-reported the running
  version (2.3.0 and 2.4.0 both announced themselves as 2.2.0). It now derives
  from the installed package metadata, making `pyproject.toml` the single source
  of truth so it can never drift again.

### Docs
- README: added an animated web-UI demo (`docs/images/ui-demo.gif`) and
  corrected the test count (203) and Helm image tag.

## [2.4.0] - 2026-08-17

Security-hardening pass across the API surface, the supply chain, and CI.

### Added
- **Optional API authentication** — set `K8S_ADVISOR_SERVER__API_KEY` to require
  `X-API-Key: <key>` or `Authorization: Bearer <key>` on every `/api/*` request
  (constant-time comparison). Health, `/metrics`, and the UI stay open for
  probes/scrapers. Unset by default, so trusted-network deployments are
  unchanged.
- **Optional CORS allow-list** — `K8S_ADVISOR_SERVER__CORS_ALLOW_ORIGINS` attaches
  `CORSMiddleware` only for explicitly named origins (never a wildcard); empty by
  default, so the API stays same-origin.
- **CI security scanning** — `pip-audit` dependency CVE job (warn-only), Trivy
  image scan with SARIF upload to the Security tab (reports HIGH/CRITICAL,
  `ignore-unfixed`, non-gating), and a weekly CodeQL `security-and-quality`
  workflow.
- **Coverage gate** — `pytest --cov` in CI with an 80% floor
  (`[tool.coverage.report] fail_under`); current coverage ~86%.

### Changed
- The document fetcher now rejects any non-`https://` source URL before making a
  request (defence in depth on top of the curated HTTPS allow-list), since
  fetched content feeds the RAG corpus.

## [2.3.0] - 2026-08-17

Knowledge-horizon refresh to the current Kubernetes support line (1.34–1.36) plus
the previously untagged deprecated-API usage evidence.

### Added
- Deprecated-API **usage** evidence via `apiserver_requested_deprecated_apis`:
  a served-but-unused deprecated API is a warning, confirmed *usage* escalates to
  blocking (served ≠ used).
- Behaviour changes for 1.33–1.36, each sourced from upstream:
  - **1.33** — Endpoints API deprecated in favour of EndpointSlices.
  - **1.35** — cgroup v1 disabled by default (`failCgroupV1=true`); kubelet fails
    to start on cgroup v1 nodes (KEP-5573, blocking). IPVS kube-proxy mode
    deprecated (KEP-5495). `exec`/`attach`/`port-forward` now require the `create`
    verb, breaking `get`-only RBAC.
  - **1.36** — containerd 1.x support removed; nodes need containerd 2.x
    (KEP-4033). Service `.spec.externalIPs` deprecated (CVE-2020-8554).
- CI: Dependabot and a weekly scheduled bit-rot canary run.

### Changed
- Knowledge horizon advanced from **1.33 to 1.36**; static tables reviewed
  against the upstream deprecation guide on 2026-08-17 (no GA/beta API
  group-versions were removed in 1.33–1.36; last removal was flowcontrol
  `v1beta3` in 1.32).
- Component support matrices extended through 1.36 (cert-manager, ingress-nginx,
  istio, cilium, calico, karpenter, cluster-autoscaler, argocd, flux, keda,
  ebs-csi, metrics-server).
- ingress-nginx matrix marked **retired** (project archived 2026-03-24; final
  v1.15.1 supports at most k8s 1.35) with migration guidance.
- Test warning filter narrowed: deprecations from within the package are now
  promoted to errors so regressions surface in CI instead of being swallowed.

## [2.2.0] - 2026-07-18

### Added
- SRE production-readiness pass: HA replica listing, admission controls
  (idempotency cache, token-bucket rate limiting, concurrency load shedding),
  shipped SLO alert rules, and a DR runbook.

## [2.1.0] - 2026-07-18

### Added
- AI-stack quality: retrieval reranking, per-assessment grounding measurement,
  and prompt/cost efficiency work.

### Fixed
- Kubernetes domain-accuracy remediations from a SIG-level review.
- Production-readiness review remediations.

### Changed
- Production hardening: load shedding, per-stage observability, retention.

## [2.0.0] - 2026-07-18

### Added
- AI Kubernetes Upgrade Intelligence Platform: deterministic-first compatibility
  analysis with RAG-grounded, schema-validated LLM reasoning.

### Fixed
- Replaced pickle KB metadata with JSON; enforced HTTPS on outbound API calls.

## [1.0.0] - 2026-06-06

### Added
- Initial release of the AI Kubernetes Upgrade Advisor.

[Unreleased]: https://github.com/ravisinghrajput95/k8s-upgrade-advisor/compare/v2.4.2...HEAD
[2.4.2]: https://github.com/ravisinghrajput95/k8s-upgrade-advisor/compare/v2.4.1...v2.4.2
[2.4.1]: https://github.com/ravisinghrajput95/k8s-upgrade-advisor/compare/v2.4.0...v2.4.1
[2.4.0]: https://github.com/ravisinghrajput95/k8s-upgrade-advisor/compare/v2.3.0...v2.4.0
[2.3.0]: https://github.com/ravisinghrajput95/k8s-upgrade-advisor/compare/v2.2.0...v2.3.0
[2.2.0]: https://github.com/ravisinghrajput95/k8s-upgrade-advisor/compare/v2.1.0...v2.2.0
[2.1.0]: https://github.com/ravisinghrajput95/k8s-upgrade-advisor/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/ravisinghrajput95/k8s-upgrade-advisor/compare/v1.0.0...v2.0.0
[1.0.0]: https://github.com/ravisinghrajput95/k8s-upgrade-advisor/releases/tag/v1.0.0
