# RAG Architecture

## Corpus

`knowledge/sources.py` is the registry — adding a source is a data change.
Per assessment window it fetches:

- **Kubernetes CHANGELOGs** for every minor in the hop path (raw GitHub markdown),
  stamped `k8s_version`
- **kubernetes.io** deprecation guide, skew policy, kubeadm upgrade guide
- **Provider docs** — EKS/GKE/AKS version & upgrade pages, OpenShift updating, RKE2
- **Component docs** — cert-manager, ingress-nginx, Istio, Linkerd, Cilium, Calico,
  Karpenter, Cluster Autoscaler, Argo CD, Flux, KEDA, Kyverno, Gatekeeper,
  External Secrets, Prometheus Operator, EBS CSI, metrics-server, CoreDNS, Helm,
  Velero — stamped `component`, fetched only for components detected in the cluster

Fetching is resilient (retry + jittered backoff), polite (ETag/If-None-Match,
0.2s spacing), and cached under `kb/raw/` with fetch timestamps.

## Indexing

- **Chunking** is structure-aware: markdown heading hierarchy → sections → paragraph
  packing to ~1400 chars with 200 overlap. Every chunk carries its heading path
  (`CHANGELOG-1.29 > Deprecation`) and source metadata.
- **Embedding backends** are pluggable behind one protocol:
  - `sentence-transformers/all-MiniLM-L6-v2` (install extra `[rag]`)
  - deterministic feature-hashed token+bigram vectors (always available; also the
    test backend)
- The store writes `manifest.json` (embedder, dims, chunk params, build time,
  version window). **Loading refuses an index built by a different embedder** —
  mixed vector spaces fail loudly, not silently.
- FAISS accelerates search when installed; numpy brute-force is the fallback
  (KB-scale corpora make this a non-issue).

## Retrieval pipeline

```
queries (version- and component-qualified)
  → dense arm (cosine) + lexical arm (BM25)     × every query
  → Reciprocal Rank Fusion (k=60)
  → HARD metadata filter: chunk.k8s_version ∈ upgrade window
  → soft component filter (0.3× penalty for docs of uninstalled components)
  → MMR diversification (λ=0.7)
  → context assembly under char budget with stable [DOC n] refs
```

Design notes:

- **Why hybrid**: dense embeddings blur exact tokens like
  `flowcontrol.apiserver.k8s.io/v1beta2`; BM25 nails them. BM25 misses paraphrase;
  dense covers it. RRF needs no score calibration between the two arms.
- **Why a hard version filter**: "1.24 release notes surfacing in a 1.29 assessment"
  is the classic RAG failure here. Query phrasing reduces it probabilistically; a
  metadata filter eliminates it. Version-stamped chunks outside the window are
  dropped regardless of similarity.
- **Why MMR**: CHANGELOG chunks are highly self-similar; without diversification
  they crowd out operator compat docs.
- **Citations**: `[DOC n]` numbering is assigned at assembly and returned alongside
  the context; the merge step discards any model citation not in that set.

## Grounding contract with the LLM

The prompt (see `llm/prompts.py`) states: compatibility claims come only from
deterministic findings or `[DOC n]`; scores are fixed inputs to explain; unknowns
must be called unknown. The *enforced* part lives in `llm/advisor.py` — the schema,
the demotion rules, and citation validation — because a contract you can't enforce
is a wish.
