"""
k8s_assess/retriever.py

Phase 3 — Retrieval Pipeline

Cluster Inventory + Target Version
           ↓
  EvidenceMetrics (computed before the LLM call)
           ↓
       Retriever  (multi-query against FAISS)
           ↓
  Relevant Upgrade Docs  (de-duplicated chunks, cited by [DOC N])
           ↓
        OpenAI  (receives hard evidence constraints)
           ↓
    Assessment Report  (citations + Unknown Risks + calibrated scores)
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .knowledge_base import KnowledgeBase, Chunk
if TYPE_CHECKING:
    from .cluster_profile import ClusterProfile

# ── Evidence metrics ──────────────────────────────────────────────────────────

# kubectl keys that indicate the command produced meaningful data
_CRITICAL_COMMANDS = {
    "version", "nodes", "api_resources", "crds",
    "validating_webhooks", "mutating_webhooks",
    "deployments", "statefulsets", "daemonsets",
}
_PRESSURE_COMMANDS = {"top_nodes", "top_pods"}

# Each operator: canonical name → list of signal strings that prove installation.
# ALL signals use lowercase matching against combined kubectl stdout.
# We require at least ONE match to classify as INSTALLED.
# Signals are ordered strongest → weakest so the first match wins.
_OPERATOR_DETECTION: dict[str, list[str]] = {
    "cert-manager": [
        "cert-manager",          # namespace, pod name, deployment name
        "certificates.cert-manager.io",  # CRD
        "clusterissuers.cert-manager.io",
    ],
    "ingress-nginx": [
        "ingress-nginx",
        "ingress.class=nginx",
        "nginx-ingress",
        "k8s.io/ingress-nginx",
    ],
    "istio": [
        "istio-system",          # canonical namespace
        "istio-pilot",
        "istiod",
        "istio-ingressgateway",
        "virtualservices.networking.istio.io",
    ],
    "cilium": [
        "cilium",                # daemonset / pod name
        "ciliumnetworkpolicies.cilium.io",
        "hubble",
    ],
    "karpenter": [
        "karpenter",
        "nodepools.karpenter.sh",
        "ec2nodeclasses.karpenter.k8s.aws",
    ],
    "argocd": [
        "argocd",
        "argo-cd",
        "applications.argoproj.io",
        "appprojects.argoproj.io",
    ],
    "metrics-server": [
        "metrics-server",
        "kube-system.*metrics",  # NOT used as regex — just substring
    ],
    "cluster-autoscaler": [
        "cluster-autoscaler",
        "clusterautoscaler",
    ],
    "velero": [
        "velero",
        "backups.velero.io",
    ],
    "crossplane": [
        "crossplane",
        "providers.pkg.crossplane.io",
    ],
    "flux": [
        "flux-system",
        "kustomizations.kustomize.toolkit.fluxcd.io",
        "helmreleases.helm.toolkit.fluxcd.io",
    ],
    "kube-prometheus": [
        "monitoring",            # namespace
        "prometheuses.monitoring.coreos.com",
        "alertmanagers.monitoring.coreos.com",
        "kube-prometheus",
        "prometheus-operator",
    ],
}

# Canonical display names for the report
_OPERATOR_DISPLAY: dict[str, str] = {
    "cert-manager":      "cert-manager",
    "ingress-nginx":     "ingress-nginx",
    "istio":             "Istio",
    "cilium":            "Cilium",
    "karpenter":         "Karpenter",
    "argocd":            "Argo CD",
    "metrics-server":    "metrics-server",
    "cluster-autoscaler":"Cluster Autoscaler",
    "velero":            "Velero",
    "crossplane":        "Crossplane",
    "flux":              "Flux",
    "kube-prometheus":   "kube-prometheus-stack",
}

# For backward compat with KB coverage check
_OPERATOR_SIGNALS = set(_OPERATOR_DETECTION.keys())


@dataclass
class OperatorStatus:
    """Tracks installation evidence for a single operator."""
    name:          str           # canonical key e.g. "cert-manager"
    display:       str           # human name e.g. "cert-manager"
    installed:     bool = False  # confirmed present in cluster stdout
    matched_signals: list[str] = field(default_factory=list)  # which signals fired
    kb_covered:    bool = False  # compatibility docs available in KB


@dataclass
class EvidenceMetrics:
    # Raw counts
    commands_ok:    int = 0
    commands_total: int = 0
    critical_ok:    int = 0
    critical_total: int = len(_CRITICAL_COMMANDS)
    kb_chunks:      int = 0
    kb_sources:     int = 0

    # Availability flags  (False = unknown risk)
    metrics_available:   bool = False
    nodes_available:     bool = False
    crds_available:      bool = False
    webhooks_available:  bool = False
    storage_available:   bool = False
    psp_available:       bool = False

    # Operator inventory — keyed by canonical name
    operators: dict[str, OperatorStatus] = field(default_factory=dict)

    # Cluster profile (detected type)
    cluster_profile: "ClusterProfile | None" = field(default=None)

    # Gaps (enumerate for Unknown Risks section)
    unknown_risks: list[str] = field(default_factory=list)

    # ── convenience views ──────────────────────────────────────────────────
    @property
    def detected_operators(self) -> list[str]:
        """Operators confirmed INSTALLED in the cluster."""
        return [k for k, v in self.operators.items() if v.installed]

    @property
    def covered_operators(self) -> list[str]:
        """Installed operators that also have KB docs."""
        return [k for k, v in self.operators.items() if v.installed and v.kb_covered]

    @property
    def doc_only_operators(self) -> list[str]:
        """Operators with KB docs but NOT detected in the cluster."""
        return [k for k, v in self.operators.items() if not v.installed and v.kb_covered]

    @property
    def command_success_rate(self) -> float:
        return self.commands_ok / max(self.commands_total, 1)

    @property
    def critical_coverage(self) -> float:
        return self.critical_ok / max(self.critical_total, 1)

    @property
    def inventory_completeness(self) -> float:
        """0-1: how much of the cluster state we could actually see."""
        flags = [
            self.metrics_available,
            self.nodes_available,
            self.crds_available,
            self.webhooks_available,
            self.storage_available,
        ]
        return sum(flags) / len(flags)

    @property
    def retrieval_coverage(self) -> float:
        """0-1: fraction of INSTALLED operators that are in the KB."""
        installed = self.detected_operators
        if not installed:
            return 1.0   # no operators found = nothing to cover
        covered = self.covered_operators
        return len(covered) / len(installed)

    @property
    def operator_verification(self) -> float:
        return self.retrieval_coverage

    def compute_confidence(self) -> int:
        """
        Confidence = how much evidence supports the conclusion.
        Missing data ONLY reduces confidence, never readiness.

        For local dev clusters (Kind, Docker Desktop, …) commands like
        'kubectl top' are expected to fail — we do not penalise for that.

        Pillars:
          inventory_completeness   35 %
          command_success_rate     25 %
          retrieval_coverage       25 %
          operator_verification    15 %
        """
        cp = self.cluster_profile

        # For local dev, recompute inventory_completeness ignoring expected-missing flags
        if cp and cp.is_local_dev:
            # Only count flags that are meaningful for this cluster type
            flags = []
            if "nodes"    not in cp.meta.missing_ok: flags.append(self.nodes_available)
            if "crds"     not in cp.meta.missing_ok: flags.append(self.crds_available)
            if "validating_webhooks" not in cp.meta.missing_ok: flags.append(self.webhooks_available)
            if "storage_classes"     not in cp.meta.missing_ok: flags.append(self.storage_available)
            if "top_nodes"           not in cp.meta.missing_ok: flags.append(self.metrics_available)
            inv = sum(flags) / len(flags) if flags else 1.0
        else:
            inv = self.inventory_completeness

        score = (
            inv                          * 35 +
            self.command_success_rate    * 25 +
            self.retrieval_coverage      * 25 +
            self.operator_verification   * 15
        )

        # Hard caps only for data gaps that matter on this cluster type
        if not self.nodes_available and (not cp or "nodes" not in cp.meta.missing_ok):
            score = min(score, 70)
        if self.command_success_rate < 0.5 and (not cp or not cp.is_local_dev):
            score = min(score, 75)
        if not self.metrics_available and (not cp or "top_nodes" not in cp.meta.missing_ok):
            score = min(score, 87)

        return round(score)

    def compute_readiness_cap(self) -> tuple[int, str]:
        """
        Readiness cap = highest score warranted by VERIFIED findings.

        Rules (cumulative — take the lowest applicable cap):
          100  Full inventory, no unknowns, no issues           → perfect
           95  Any genuine unknown risks remain                 → near-perfect
           85  Complex installed operators without KB coverage  → notable gap
           75  Cluster inventory largely opaque                 → high uncertainty

        Missing evidence does NOT lower readiness directly — it appears in
        Unknown Risks and reduces the cap here by at most one level.
        Only verified/probable incompatibilities cause the model to score
        below the cap.
        """
        cp   = self.cluster_profile
        cap  = 100
        reasons: list[str] = []

        # ── Genuine unknown risks (after cluster-profile filtering) ───────
        # If the profile says missing metrics is expected (local dev),
        # it was never added to unknown_risks, so len() is already correct.
        if self.unknown_risks:
            cap = min(cap, 95)
            reasons.append(
                f"{len(self.unknown_risks)} unknown risk(s) remain after "
                f"cluster-profile filtering"
            )

        # ── Cluster almost entirely opaque ────────────────────────────────
        if not self.nodes_available and not self.crds_available \
                and not self.webhooks_available:
            cap = min(cap, 75)
            reasons.append(
                "cluster inventory largely unavailable — upgrade safety unverifiable"
            )

        # ── Installed complex operators without KB docs ───────────────────
        _SIMPLE_OPERATORS = {"metrics-server", "cluster-autoscaler"}
        uncovered_installed = [
            k for k, v in self.operators.items()
            if v.installed and not v.kb_covered and k not in _SIMPLE_OPERATORS
        ]
        if uncovered_installed:
            cap = min(cap, 85)
            reasons.append(
                f"installed operators without KB coverage: "
                f"{', '.join(self.operators[k].display for k in uncovered_installed)}"
            )

        # ── Secondary cap: unknown risks present → can't be 100 ─────────
        secondary_cap, secondary_reason = self._readiness_cap_for_unknowns()
        if secondary_cap < cap:
            cap = secondary_cap
            reasons.append(secondary_reason)

        reason_str = "; ".join(reasons) if reasons else \
            "full inventory, no unknown risks, no verified issues"
        return cap, reason_str

    def _readiness_cap_for_unknowns(self) -> tuple[int, str]:
        """
        Secondary cap: reserve 100/100 for full-inventory, zero-gap runs.
        Any real unknown risk → cap at 95.
        Only boilerplate caveats (stress-test note) → cap at 98.
        Zero unknowns → no cap applied (100 allowed).
        """
        if not self.unknown_risks:
            return 100, "full inventory, zero unknown risks"
        real_unknowns = [r for r in self.unknown_risks
                         if "stress testing" not in r and "canary" not in r]
        if real_unknowns:
            return 95, (
                f"{len(real_unknowns)} unknown risk(s) present — "
                "100/100 reserved for full-inventory, zero-gap runs"
            )
        return 98, "only boilerplate unknown risks (stress-test caveat)"

    def as_prompt_block(self) -> str:
        """Return a structured block injected directly into the prompt."""
        lines = [
            "## Evidence Quality Report",
            "",
            "This block was computed BEFORE the LLM call from actual kubectl output.",
            "You MUST follow the operator classification rules below — do not infer",
            "installation from KB docs alone.",
            "",
        ]

        # ── Cluster profile (first — it frames everything else) ───────────
        if self.cluster_profile:
            lines.append(self.cluster_profile.prompt_context())
            lines.append("")

        lines += [
            "### Evidence Summary",
            f"kubectl commands succeeded : {self.commands_ok}/{self.commands_total}"
            f"  ({self.command_success_rate:.0%})",
            f"Critical commands available: {self.critical_ok}/{self.critical_total}"
            f"  ({self.critical_coverage:.0%})",
            f"KB chunks retrieved        : {self.kb_chunks}",
            f"KB sources covered         : {self.kb_sources}",
            "",
            "### Data Availability Flags",
        ]

        # Show availability flags with cluster-aware context
        cp = self.cluster_profile

        def _flag(label: str, available: bool, kubectl_key: str, note: str = "") -> str:
            if available:
                return f"  {label} : AVAILABLE"
            expected_missing = cp and kubectl_key in cp.meta.missing_ok
            suffix = " (EXPECTED on this cluster type — not a risk)" if expected_missing \
                     else " ← unknown risk"
            return f"  {label} : MISSING{suffix}{(' — ' + note) if note else ''}"

        lines += [
            _flag("Node data        ", self.nodes_available,    "nodes"),
            _flag("CRD data         ", self.crds_available,     "crds"),
            _flag("Webhook data     ", self.webhooks_available, "validating_webhooks"),
            _flag("Storage data     ", self.storage_available,  "storage_classes"),
            _flag("Resource metrics ", self.metrics_available,  "top_nodes"),
            _flag("PSP data         ", self.psp_available,      "pod_security",
                  "expected on k8s <1.25"),
            "",
        ]

        # ── Operator inventory table ──────────────────────────────────────
        installed   = self.detected_operators
        doc_only    = self.doc_only_operators
        unverified  = [k for k, v in self.operators.items()
                       if not v.installed and not v.kb_covered]

        lines += [
            "### Operator Inventory",
            "",
            "INSTALLED (confirmed in cluster stdout — apply full compatibility analysis):",
        ]
        if installed:
            for k in installed:
                op = self.operators[k]
                kb = " [KB docs available]" if op.kb_covered else " [NO KB docs]"
                signals = ", ".join(op.matched_signals[:2])
                lines.append(f"  ✅ {op.display}{kb}  — detected via: {signals}")
        else:
            lines.append("  (none detected)")

        lines += [
            "",
            "DOCUMENTATION AVAILABLE but NOT detected in cluster",
            "(DO NOT flag as risks — mention as 'compatibility info available'",
            " only if the user asks, or in a separate informational section):",
        ]
        if doc_only:
            for k in doc_only:
                op = self.operators[k]
                lines.append(f"  📄 {op.display} — KB docs present but operator not found in cluster")
        else:
            lines.append("  (none)")

        lines.append("")

        # ── Mandatory operator assessment rule ───────────────────────────
        lines += [
            "### MANDATORY OPERATOR ASSESSMENT RULE",
            "For every operator in the INSTALLED list:",
            "  → Perform full compatibility analysis and flag risks.",
            "For every operator in the DOCUMENTATION AVAILABLE list:",
            "  → Write EXACTLY: '<Operator> was not detected in this cluster.",
            "     Compatibility information is available if needed.'",
            "  → Do NOT raise warnings, risks, or action items for undetected operators.",
            "  → Do NOT assume the operator might be present.",
            "",
        ]

        if self.unknown_risks:
            lines += [
                "### Pre-computed Unknown Risks",
                "These MUST appear verbatim in the Unknown Risks section:",
            ]
            for r in self.unknown_risks:
                lines.append(f"  - {r}")
            lines.append("")

        confidence = self.compute_confidence()
        readiness_cap, readiness_reason = self.compute_readiness_cap()

        # Pre-compute the approval basis sentence the model must use verbatim
        if self.unknown_risks:
            unknown_count = len(self.unknown_risks)
            approval_basis = (
                f"No verified incompatibilities detected. "
                f"{unknown_count} unknown risk(s) remain due to incomplete inventory "
                f"and do not constitute verified upgrade blockers. "
                f"Approval is conditional on those gaps being acceptable for this environment."
            )
        else:
            approval_basis = (
                "No verified incompatibilities detected. "
                "Full inventory available with no evidence gaps. "
                "Upgrade may proceed subject to standard pre-upgrade validation."
            )

        lines += [
            "### Score Constraints",
            "",
            "CONFIDENCE SCORE (measures evidence quality, NOT upgrade risk):",
            f"  PRE-COMPUTED VALUE : {confidence}%",
            "  You MUST use this exact number. Do not adjust it.",
            "  Missing data reduces confidence. It does NOT affect readiness.",
            "",
            "READINESS SCORE (measures upgrade risk based on VERIFIED findings only):",
            f"  PRE-COMPUTED CAP   : {readiness_cap}/100",
            f"  Cap reason         : {readiness_reason}",
            "  You may score LOWER than the cap if you find verified CRITICAL/HIGH RISK issues.",
            "  You may NOT score higher than the cap.",
            "  Do NOT lower readiness for missing evidence — that belongs in Unknown Risks only.",
            "",
            "UPGRADE DECISION RULES:",
            "  APPROVED         — readiness >= 85, no CRITICAL issues",
            "  CONDITIONAL      — readiness 65-84, OR any HIGH RISK issues",
            "  NOT RECOMMENDED  — readiness < 65, OR any CRITICAL issues verified",
            "",
            "APPROVAL BASIS (include this verbatim in the Executive Summary):",
            f'  "{approval_basis}"',
            "  The Approval Basis MUST appear as its own paragraph immediately after",
            "  the decision line, before any Critical Issues or High Risks.",
            "  This makes explicit that unknown risks are informational, not blockers.",
            "",
            "CRITICAL: Missing evidence is NOT a verified risk.",
            "  Unknown Risks section  → missing evidence goes here.",
            "  Risk Matrix CRITICAL   → only for verified incompatibilities.",
            "  Risk Matrix HIGH RISK  → only for probable incompatibilities.",
            "  Risk Matrix UNKNOWN    → for gaps in evidence.",
            "  Never escalate an Unknown Risk to CRITICAL or HIGH RISK without verification.",
        ]
        return "\n".join(lines)


def compute_evidence(cluster_data: dict, chunks: list[Chunk]) -> EvidenceMetrics:
    """Analyse cluster_data and retrieved chunks to produce EvidenceMetrics."""
    from .cluster_profile import detect_cluster

    m = EvidenceMetrics()
    m.commands_total = len(cluster_data)
    m.commands_ok    = sum(1 for v in cluster_data.values() if v.get("ok"))

    # ── Detect cluster type first — it informs everything else ────────────
    m.cluster_profile = detect_cluster(cluster_data)
    cp = m.cluster_profile

    for key in _CRITICAL_COMMANDS:
        if cluster_data.get(key, {}).get("ok"):
            m.critical_ok += 1

    m.nodes_available     = bool(cluster_data.get("nodes", {}).get("ok"))
    m.crds_available      = bool(cluster_data.get("crds",  {}).get("ok"))
    m.webhooks_available  = (
        bool(cluster_data.get("validating_webhooks", {}).get("ok")) or
        bool(cluster_data.get("mutating_webhooks",   {}).get("ok"))
    )
    m.storage_available   = bool(cluster_data.get("storage_classes", {}).get("ok"))
    m.metrics_available   = (
        bool(cluster_data.get("top_nodes", {}).get("ok")) and
        bool(cluster_data.get("top_pods",  {}).get("ok"))
    )
    m.psp_available = bool(cluster_data.get("pod_security", {}).get("ok"))

    # ── Precise operator detection ────────────────────────────────────────
    all_stdout = " ".join(
        v.get("stdout", "") for v in cluster_data.values()
    ).lower()

    for key, signals in _OPERATOR_DETECTION.items():
        op = OperatorStatus(
            name=key,
            display=_OPERATOR_DISPLAY.get(key, key),
        )
        for sig in signals:
            if sig.lower() in all_stdout:
                op.installed = True
                op.matched_signals.append(sig)
        m.operators[key] = op

    # ── KB coverage ───────────────────────────────────────────────────────
    m.kb_chunks  = len(chunks)
    kb_sources   = {c.source for c in chunks}
    m.kb_sources = len(kb_sources)
    for key, op in m.operators.items():
        op_slug = key.replace("-", "_")
        op_nohyphen = key.replace("-", "")
        if any(op_slug in s or op_nohyphen in s or key in s for s in kb_sources):
            op.kb_covered = True

    # ── Profile-aware unknown risks ───────────────────────────────────────
    # Only flag missing data as an unknown risk if it's NOT expected to be
    # missing on this cluster type. Local dev clusters routinely lack metrics,
    # PSP, and certain storage commands — that's not a risk, it's normal.

    def _maybe_risk(kubectl_key: str, message: str) -> None:
        risk = cp.unknown_risk_for(kubectl_key, message)
        if risk:
            m.unknown_risks.append(risk)

    if not m.metrics_available:
        _maybe_risk(
            "top_nodes",
            "Resource pressure unverified — kubectl top nodes/pods unavailable "
            f"(Metrics API not running on {cp.display}); "
            "OOMKill or eviction risk during node drain is unknown",
        )
    if not m.nodes_available:
        _maybe_risk(
            "nodes",
            "Node details unavailable — OS version, container runtime, and kubelet "
            "version could not be confirmed against target Kubernetes requirements",
        )
    if not m.crds_available:
        _maybe_risk(
            "crds",
            "CRD inventory incomplete — custom resource API compatibility cannot be "
            "fully verified; CRD storage version conflicts may exist",
        )
    if not m.webhooks_available:
        _maybe_risk(
            "validating_webhooks",
            "Admission webhook inventory unavailable — cannot verify FailurePolicy "
            "or TLS compatibility; webhooks may silently block workloads post-upgrade",
        )
    if not m.storage_available:
        _maybe_risk(
            "storage_classes",
            "StorageClass / PV data unavailable — CSI driver and volume snapshot "
            "compatibility with target version cannot be confirmed",
        )

    # Installed operators without KB coverage (regardless of cluster type)
    for key in m.detected_operators:
        op = m.operators[key]
        if not op.kb_covered:
            m.unknown_risks.append(
                f"{op.display} detected in cluster but has no KB compatibility docs — "
                f"version support against the target Kubernetes version is unverified"
            )

    # Cloud-managed clusters: add provider-specific upgrade reminder
    if cp.is_managed:
        m.unknown_risks.append(
            f"{cp.display}: verify that provider-managed add-on versions "
            f"({', '.join(cp.meta.managed_components[:4])}, …) are compatible with "
            f"the target Kubernetes version in the provider's add-on compatibility matrix"
        )

    # Always include the stress-test caveat (unless local dev — no production workloads)
    if not cp.is_local_dev:
        m.unknown_risks.append(
            "No workload stress testing or canary upgrade performed — "
            "runtime behaviour under load during rolling node upgrades is unverified"
        )

    return m


# ── Retrieval queries ─────────────────────────────────────────────────────────

def _versions_between(source: str, target: str) -> list[str]:
    """Return all minor versions from source to target inclusive."""
    def _minor(v: str) -> int:
        return int(v.lstrip("v").split(".")[1])
    sm, tm = _minor(source), _minor(target)
    major = source.lstrip("v").split(".")[0]
    return [f"{major}.{m}" for m in range(sm, tm + 1)]


def build_retrieval_queries(source: str, target: str, cluster_summary: str) -> list[str]:
    """
    Generate targeted retrieval queries for the upgrade path.

    Every version-sensitive query is fully qualified (e.g. "Kubernetes 1.35
    release notes") to prevent FAISS from surfacing chunks about unrelated
    versions — the root cause of e.g. "Kubernetes 1.28 Release Notes"
    appearing in a 1.34→1.35 assessment.
    """
    versions = _versions_between(source, target)
    queries: list[str] = []

    # ── Per-version release note queries — fully qualified, no drift ──────
    for ver in versions:
        queries.append(f"Kubernetes {ver} release notes breaking changes removed APIs")
        queries.append(f"Kubernetes {ver} deprecations upgrade notes changelog")

    # ── Target-version specific ───────────────────────────────────────────
    queries += [
        f"Kubernetes {target} API removed deprecated migration",
        f"upgrade Kubernetes {source} to {target} steps",
        f"Kubernetes {target} new behavior changes security",
    ]

    # ── Operator compat — always target-version qualified ─────────────────
    for label in [
        "cert-manager", "ingress-nginx", "metrics-server",
        "Argo CD", "Istio", "Cilium", "Karpenter", "CSI driver EBS GCP Azure",
    ]:
        queries.append(f"{label} kubernetes {target} compatibility supported versions matrix")

    # ── Stable topic queries (no version numbers → no drift risk) ─────────
    queries += [
        "PodSecurityPolicy removal Pod Security Admission migration",
        "CRD custom resource storage version conversion webhook upgrade",
        "admission webhook validating mutating FailurePolicy upgrade",
        "kubelet node drain rolling upgrade",
        "container runtime CRI containerd kubernetes compatibility",
        "CoreDNS configuration upgrade",
        "etcd control plane upgrade",
        "StorageClass CSI volume snapshot upgrade",
        "RBAC admission controller breaking change",
        "NetworkPolicy CNI upgrade",
    ]

    # ── Cluster-specific injections ───────────────────────────────────────
    cs = cluster_summary.lower()
    for keyword, extra in [
        ("cilium",       f"Cilium {target} kubernetes CNI upgrade steps"),
        ("istio",        f"Istio {target} kubernetes service mesh upgrade"),
        ("karpenter",    f"Karpenter {target} kubernetes node provisioner"),
        ("cert-manager", f"cert-manager {target} webhook CRD upgrade order"),
        ("argocd",       f"Argo CD {target} kubernetes application controller"),
    ]:
        if keyword in cs:
            queries.insert(0, extra)

    return queries


def format_context(chunks: list[Chunk], max_chars: int = 40_000,
                   installed_operators: set[str] | None = None) -> str:
    """
    Format retrieved chunks into a cited context block.

    Each chunk is tagged [DOC N] with its title.  When installed_operators is
    provided, operator-specific chunks are annotated:
      [APPLIED]    — operator confirmed installed in cluster
      [REFERENCE]  — operator NOT detected; doc available but should not drive findings
    This annotation feeds the Evidence Used split: Applied Evidence vs Available Sources.
    """
    parts:       list[str] = []
    total:       int = 0
    index_lines: list[str] = []
    installed    = installed_operators or set()

    # Map source → operator key (loose match)
    def _source_tag(source: str) -> str:
        for op_key in installed:
            slug = op_key.replace("-", "_")
            if slug in source or op_key.replace("-", "") in source or op_key in source:
                return "[APPLIED]"
        # Check if this source belongs to any known operator (installed or not)
        _all_op_sources = {
            "cert_manager", "ingress_nginx", "metrics_server", "argocd",
            "istio", "cilium", "karpenter", "csi_ebs", "csi_gcp", "csi_azure",
            "csi_drivers",
        }
        if any(s in source for s in _all_op_sources):
            return "[REFERENCE]"
        return "[APPLIED]"   # kubernetes core docs always apply

    for i, chunk in enumerate(chunks, 1):
        tag = _source_tag(chunk.source)
        header = (
            f"[DOC {i}] {tag} {chunk.title}\n"
            f"Source: {chunk.source} | URL: {chunk.url}"
        )
        block = f"{header}\n{chunk.text}\n"
        if total + len(block) > max_chars:
            break
        parts.append(block)
        total += len(block)
        index_lines.append(f"  [DOC {i}] {tag} {chunk.title}")

    source_index = "\n".join(index_lines)
    header_block = (
        f"[RETRIEVED KNOWLEDGE BASE — {len(parts)} documents]\n\n"
        f"CITATION INDEX\n"
        f"  [APPLIED]   = document applies to a component confirmed in this cluster\n"
        f"  [REFERENCE] = component not detected; use only in 'Available Sources' section\n\n"
        f"{source_index}\n\n"
        f"{'─' * 60}\n\n"
    )
    return header_block + "\n\n---\n\n".join(parts)


# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a Senior Kubernetes Platform Engineer performing a
comprehensive upgrade readiness review.

You have been provided with:
1. A pre-computed Evidence Quality Report (DO NOT override its Confidence Score)
2. Live cluster inventory data (kubectl outputs, some may be unavailable)
3. Retrieved knowledge base documents tagged [DOC 1], [DOC 2], etc.

════════════════════════════════════════════════════
MANDATORY RULES — violations invalidate the report
════════════════════════════════════════════════════

CITATIONS
  Cite KB documents by their TITLE, not their [DOC N] number.
  Inline example:  "cert-manager 1.12 supports Kubernetes 1.27–1.29
                    (cert-manager Supported Releases)"
  If a claim has no KB citation, prefix it with "(model knowledge)" so the
  reader knows it is not evidence-backed.
  Do NOT use bare [DOC N] numbers as citations in the body text.
  [DOC N] tags may appear in the Evidence Used section for cross-reference.

CONFIDENCE SCORE
  The Evidence Quality Report contains a PRE-COMPUTED CONFIDENCE SCORE.
  You MUST use that exact number. Do not inflate it.
  Evidence gaps reduce confidence — they don't disappear because you assume things.

FINDING TIERS
  Every section MUST classify findings into exactly four tiers:
    ✅ Verified Issues    — confirmed by KB docs AND cluster data
    ⚠️  Probable Issues   — strong KB or cluster signal, not fully confirmed
    🔍 Possible Issues    — weak signal; worth investigating
    ❓ Unknown Risks      — data was unavailable; listed in Evidence Report
  If a tier is empty, write "None identified."
  Do NOT skip tiers or merge them.

UNKNOWN RISKS SECTION
  Include a dedicated ## Unknown Risks section AFTER the risk matrix.
  Copy every item from the Evidence Report's "Pre-computed Unknown Risks" list
  verbatim, then add any additional unknowns you identify during analysis.

SCORING — READ CAREFULLY
  These are two independent scores measuring different things.

  CONFIDENCE SCORE = how much evidence supports the assessment
    Use the PRE-COMPUTED value from the Evidence Quality Report exactly.
    Missing data, unavailable commands, and incomplete inventory reduce confidence.
    Missing data does NOT affect readiness.

  READINESS SCORE = how risky is this specific upgrade?
    Determined only by VERIFIED or PROBABLE issues found in cluster data or KB docs.
    Apply the PRE-COMPUTED READINESS CAP from the Evidence Quality Report.
    Further reduce only if you find CRITICAL or HIGH RISK verified incompatibilities:
      Unresolved verified CRITICAL issue  → reduce to <= 60
      Unresolved verified HIGH RISK issue → reduce to <= 75
    Do NOT reduce readiness for:
      - Missing kubectl output
      - Unavailable metrics
      - Operators not detected (absence = no risk, not a risk)
      - Webhooks not inventoried
      - Anything that appears in Unknown Risks
    Absence of evidence ≠ evidence of risk.

  UPGRADE DECISION (based on readiness only):
    APPROVED         — readiness >= 85, no verified CRITICAL issues
    CONDITIONAL      — readiness 65-84, OR any verified HIGH RISK issues
    NOT RECOMMENDED  — readiness < 65, OR any verified CRITICAL issues

════════════════════════════════════════════════════
OUTPUT STRUCTURE (follow exactly)
════════════════════════════════════════════════════

# Kubernetes Upgrade Assessment: {source} → {target}

## Executive Summary
UPGRADE DECISION: [APPROVED / CONDITIONAL / NOT RECOMMENDED]

**Approval Basis:** <copy the APPROVAL BASIS sentence from the Evidence Quality Report verbatim>

> This section must make explicit whether the decision is based on verified findings
> or on absence of findings.  Do NOT omit the Approval Basis line.
> If unknown risks exist, the Approval Basis sentence already explains they are
> informational — do not contradict it elsewhere in the report.

Readiness Score: XX/100
Confidence Score: XX%   ← must match pre-computed value

Critical Issues: [list, or "None verified"]
High Risks:      [list, or "None verified"]
Unknown Risks:   [count] items — see ## Unknown Risks section (informational only)

## Evidence Applied to This Assessment
List ONLY documents tagged [APPLIED] that materially influenced a finding.
These are docs for components confirmed installed in this cluster, plus Kubernetes
core docs (release notes, deprecation guides).
Format:
  - **<Title>** — <one sentence: what finding it supported>
Group under: Kubernetes Core | Installed Components | APIs & Deprecations
If no KB docs influenced a section, write "(model knowledge only)".

## Available Reference Sources
List documents tagged [REFERENCE] — retrieved but not applied because the
component was NOT detected in this cluster.
Format:
  - **<Title>** — available if <component> is present in your environment
This section informs the reader what compatibility docs exist without implying
those components were assessed.
Example:
  - **Karpenter Upgrade Guide** — available if Karpenter is present in your environment
  - **cert-manager Supported Releases** — available if cert-manager is present in your environment

## Steps 1-17
[Full analysis per step, with citations and four-tier findings]

## Risk Matrix
| Area | Status | Severity | Evidence | Explanation |
Add an "Evidence" column citing [DOC N] for each row.
Use status "UNKNOWN" (not WARNING or HIGH RISK) for rows where evidence was unavailable.

## Unknown Risks
> These items reflect evidence gaps, not verified problems.
> They do not change the upgrade decision unless resolved with verified findings.
> Each item should be actionable — state what the operator should check.

[Pre-computed list + any additional unknowns you identify]

## Upgrade Runbook
[Ordered numbered steps with: Action / Validation / Rollback]"""


# ── Prompt builder ────────────────────────────────────────────────────────────

def build_prompt(source: str, target: str,
                 cluster_data: dict,
                 context: str,
                 evidence: "EvidenceMetrics | None" = None) -> str:

    def section(title: str, key: str) -> str:
        d = cluster_data.get(key, {})
        out = d.get("stdout", "")
        err = d.get("stderr", "")
        content = out if out else f"(unavailable – {err or 'no output'})"
        return f"\n### {title}\n```\n{content[:6000]}\n```\n"

    cluster_dump = (
        section("kubectl version",          "version") +
        section("Nodes",                    "nodes") +
        section("Namespaces",               "namespaces") +
        section("API Resources",            "api_resources") +
        section("API Services",             "api_services") +
        section("All Resources (-A)",       "all_resources") +
        section("Deployments",              "deployments") +
        section("StatefulSets",             "statefulsets") +
        section("DaemonSets",               "daemonsets") +
        section("Jobs",                     "jobs") +
        section("CronJobs",                 "cronjobs") +
        section("CRDs",                     "crds") +
        section("Validating Webhooks",      "validating_webhooks") +
        section("Mutating Webhooks",        "mutating_webhooks") +
        section("StorageClasses",           "storage_classes") +
        section("PersistentVolumes",        "pvs") +
        section("PersistentVolumeClaims",   "pvcs") +
        section("Node Resource Usage",      "top_nodes") +
        section("Pod Resource Usage",       "top_pods") +
        section("PodSecurityPolicies",      "pod_security") +
        section("Cluster Info",             "cluster_info")
    )

    evidence_block = evidence.as_prompt_block() if evidence else (
        "## Evidence Quality Report\n"
        "Evidence metrics unavailable — apply maximum conservatism.\n"
        "PRE-COMPUTED CONFIDENCE SCORE: 40%\n"
    )

    # Fix placeholder in unknown risks (operator target version)
    evidence_block = evidence_block.replace("{target}", target)

    return f"""# Kubernetes Upgrade Assessment

## Upgrade Path
SOURCE_VERSION: {source}
TARGET_VERSION: {target}

---

{evidence_block}

---

## Retrieved Knowledge Base
{context}

---

## Live Cluster Inventory
{cluster_dump}

---

## Assessment Instructions

Perform the FULL 17-step assessment.
Follow ALL mandatory rules from the system prompt exactly.
Use [DOC N] citations for every KB-backed claim.
Honour the pre-computed Confidence Score.
Include all four finding tiers in every section.

Steps:
1. Cluster info & topology
2. Full resource inventory
3. CRD discovery & compatibility
4. Controller/operator compatibility
5. Kubernetes release note analysis (all intermediate versions)
6. API removal analysis
7. Deprecated API analysis
8. CRD schema & conversion compatibility
9. Controller upgrade requirements (before/after)
10. Admission webhook analysis
11. Networking compatibility
12. Storage compatibility
13. Security policy changes (PSP → PSA)
14. Container runtime compatibility
15. Resource pressure analysis
16. Upgrade simulation
17. Failure scenario modeling

Then: Risk Matrix (with Evidence column) → Unknown Risks → Upgrade Runbook
"""


# ── OpenAI streaming call ─────────────────────────────────────────────────────

def call_openai(prompt: str, system: str = SYSTEM_PROMPT,
                model: str = "gpt-4o",
                max_tokens: int = 8000,
                stream: bool = True) -> str:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")

    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    body = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": prompt},
        ],
        "stream": stream,
    }).encode()

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    collected: list[str] = []

    # The endpoint above is a hardcoded https:// constant; this guard makes
    # that invariant explicit so a future refactor can't introduce file://
    # or http:// requests (urllib would happily follow either).
    if not req.full_url.startswith("https://"):
        raise ValueError(f"Refusing non-HTTPS request: {req.full_url}")

    try:
        with urllib.request.urlopen(req, timeout=300) as resp:  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
            if stream:
                for raw_line in resp:
                    line = raw_line.decode("utf-8").strip()
                    if line.startswith("data: "):
                        payload = line[6:]
                        if payload == "[DONE]":
                            break
                        try:
                            event = json.loads(payload)
                            text = (
                                event.get("choices", [{}])[0]
                                     .get("delta", {})
                                     .get("content", "")
                            )
                            if text:
                                print(text, end="", flush=True)
                                collected.append(text)
                        except (json.JSONDecodeError, IndexError):
                            pass
            else:
                data = json.loads(resp.read())
                text = data["choices"][0]["message"]["content"]
                collected.append(text)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"OpenAI API error {e.code}: {e.read().decode()}")

    return "".join(collected)
