"""Assessment domain models — the output side of the platform.

Design rule: the report is *data first*. Deterministic engines emit
:class:`Finding` and :class:`CompatibilityEntry` objects; the LLM fills a
narrowly-scoped :class:`LLMAnalysis` (narrative, sequencing, plans) that is
schema-validated on arrival. Renderers (JSON/Markdown/HTML) only ever read
from :class:`AssessmentReport` — nobody parses LLM prose with regexes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field

from .cluster import ClusterFlavour, DetectedComponent, NodeInfo, WorkloadCounts


class Severity(str, Enum):
    CRITICAL = "critical"  # upgrade will break something — blocking
    HIGH = "high"  # very likely impact, must be remediated first
    MEDIUM = "medium"  # needs review / staged validation
    LOW = "low"  # informational risk
    INFO = "info"  # observation, no action needed

    @property
    def rank(self) -> int:
        return {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}[self.value]


class FindingCategory(str, Enum):
    REMOVED_API = "removed-api"
    DEPRECATED_API = "deprecated-api"
    BREAKING_CHANGE = "breaking-change"
    KEP_IMPACT = "kep-impact"
    VERSION_SKEW = "version-skew"
    CRD_COMPAT = "crd-compatibility"
    WEBHOOK_COMPAT = "webhook-compatibility"
    OPERATOR_COMPAT = "operator-compatibility"
    HELM_COMPAT = "helm-compatibility"
    CNI_COMPAT = "cni-compatibility"
    CSI_COMPAT = "csi-compatibility"
    SERVICE_MESH = "service-mesh-compatibility"
    GITOPS = "gitops-compatibility"
    AUTOSCALER = "autoscaler-compatibility"
    RUNTIME = "runtime-compatibility"
    NODE_POOL = "node-pool-planning"
    CONTROL_PLANE = "control-plane"
    WORKLOAD_DISRUPTION = "workload-disruption"
    STORAGE = "storage"
    NETWORKING = "networking"
    UPGRADE_PATH = "upgrade-path"
    OBSERVATION = "observation"


class FindingOrigin(str, Enum):
    DETERMINISTIC = "deterministic"  # provable from cluster data + static tables
    RETRIEVED = "retrieved"  # grounded in a KB document
    LLM = "llm"  # model reasoning (lowest trust tier)


class Citation(BaseModel):
    """A reference into the knowledge base. ``ref`` is the [DOC n] number the
    LLM used; renderers resolve it to title/url."""

    ref: int
    title: str = ""
    url: str = ""
    source: str = ""
    k8s_version: str | None = None


class Evidence(BaseModel):
    """Why we believe a finding. Deterministic findings cite cluster data;
    retrieved findings cite [DOC n]; a finding with no evidence is invalid
    by construction in the deterministic pipeline."""

    kind: str  # cluster-data | static-table | kb-document | llm-reasoning
    detail: str
    citation_refs: list[int] = Field(default_factory=list)


class Finding(BaseModel):
    id: str  # stable slug, e.g. "removed-api-policy-v1beta1-psp"
    title: str
    category: FindingCategory
    severity: Severity
    origin: FindingOrigin
    description: str
    affected_objects: list[str] = Field(default_factory=list)
    remediation: str = ""
    blocking: bool = False  # must be resolved before upgrade proceeds
    evidence: list[Evidence] = Field(default_factory=list)
    introduced_in: str | None = None  # k8s version where behaviour changes
    effective_in: str | None = None  # k8s version where it bites (removal)


class CompatibilityStatus(str, Enum):
    COMPATIBLE = "compatible"
    UPGRADE_REQUIRED = "upgrade-required"
    INCOMPATIBLE = "incompatible"
    UNKNOWN = "unknown"


class CompatibilityEntry(BaseModel):
    component: str
    kind: str = ""  # cni | csi | mesh | gitops | autoscaler | operator | runtime
    current_version: str | None = None
    status: CompatibilityStatus = CompatibilityStatus.UNKNOWN
    minimum_version: str | None = None  # min component version for the target k8s
    recommended_version: str | None = None
    notes: str = ""
    citation_refs: list[int] = Field(default_factory=list)


class Verdict(str, Enum):
    READY = "ready"
    READY_WITH_CAUTIONS = "ready-with-cautions"
    NOT_READY = "not-ready"
    BLOCKED = "blocked"


class ReadinessScore(BaseModel):
    """Score is computed deterministically and *capped* by evidence quality;
    the LLM cannot raise it. ``cap_reason`` makes the ceiling auditable."""

    score: int = Field(ge=0, le=100)
    cap: int = Field(100, ge=0, le=100)
    cap_reason: str = ""
    confidence: int = Field(ge=0, le=100)
    verdict: Verdict

    @classmethod
    def verdict_for(cls, score: int, has_blockers: bool) -> Verdict:
        if has_blockers:
            return Verdict.BLOCKED
        if score >= 85:
            return Verdict.READY
        if score >= 65:
            return Verdict.READY_WITH_CAUTIONS
        return Verdict.NOT_READY


class UpgradePhase(str, Enum):
    PREPARATION = "preparation"
    CONTROL_PLANE = "control-plane"
    ADDONS = "addons"
    NODE_POOLS = "node-pools"
    WORKLOADS = "workloads"
    VALIDATION = "validation"


class UpgradeStep(BaseModel):
    order: int
    phase: UpgradePhase
    title: str
    description: str = ""
    commands: list[str] = Field(default_factory=list)
    estimated_minutes: int | None = None
    disruption: str = "none"  # none | control-plane-api | rolling | outage


class UpgradePlan(BaseModel):
    strategy: str = ""  # e.g. "sequential in-place, one minor at a time"
    hop_sequence: list[str] = Field(default_factory=list)  # ["1.27→1.28", …]
    steps: list[UpgradeStep] = Field(default_factory=list)
    rollback: list[UpgradeStep] = Field(default_factory=list)
    pre_upgrade_checklist: list[str] = Field(default_factory=list)
    post_upgrade_validation: list[str] = Field(default_factory=list)


class DowntimeEstimate(BaseModel):
    control_plane_impact: str = ""
    workload_impact: str = ""
    estimated_window_minutes: int | None = None
    assumptions: list[str] = Field(default_factory=list)


class ClusterProfileSummary(BaseModel):
    flavour: ClusterFlavour = ClusterFlavour.UNKNOWN
    flavour_evidence: list[str] = Field(default_factory=list)
    current_version: str = ""
    node_count: int = 0
    nodes: list[NodeInfo] = Field(default_factory=list)
    workloads: WorkloadCounts = Field(default_factory=WorkloadCounts)
    components: list[DetectedComponent] = Field(default_factory=list)
    upgrade_mechanism: str = ""  # e.g. "eksctl / EKS console, managed control plane"
    provider_managed: list[str] = Field(default_factory=list)


class EvidenceMetrics(BaseModel):
    """Quantifies how much we could actually see — feeds confidence."""

    commands_ok: int = 0
    commands_total: int = 0
    critical_ok: int = 0
    critical_total: int = 0
    kb_chunks_retrieved: int = 0
    kb_sources: int = 0
    components_with_versions: int = 0
    components_detected: int = 0
    unknown_risks: list[str] = Field(default_factory=list)

    @property
    def command_success_rate(self) -> float:
        return self.commands_ok / max(self.commands_total, 1)

    @property
    def critical_coverage(self) -> float:
        return self.critical_ok / max(self.critical_total, 1)

    @property
    def version_resolution_rate(self) -> float:
        return self.components_with_versions / max(self.components_detected, 1)


class LLMAnalysis(BaseModel):
    """The *only* thing the LLM is allowed to produce. Anything outside this
    schema is rejected. Findings it adds are tagged origin=llm and can never
    be blocking on their own."""

    executive_summary: str
    risk_narrative: str = ""
    upgrade_strategy: str = ""
    plan: UpgradePlan = Field(default_factory=UpgradePlan)
    downtime: DowntimeEstimate = Field(default_factory=DowntimeEstimate)
    additional_findings: list[Finding] = Field(default_factory=list)
    compatibility_notes: list[CompatibilityEntry] = Field(default_factory=list)
    citations_used: list[int] = Field(default_factory=list)


class LLMMetadata(BaseModel):
    provider: str = "none"
    model: str = ""
    prompt_chars: int = 0
    completion_chars: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_usd: float = 0.0
    # Fraction of substantive narrative sentences carrying a [DOC n] citation
    # — measured post-merge, not promised by the prompt.
    grounding_ratio: float = 0.0
    duration_ms: int = 0
    dry_run: bool = False


class AssessmentReport(BaseModel):
    """The single artifact every renderer and API response is built from."""

    schema_version: int = 2
    id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source_version: str
    target_version: str
    version_path: list[str] = Field(default_factory=list)

    profile: ClusterProfileSummary = Field(default_factory=ClusterProfileSummary)
    readiness: ReadinessScore
    executive_summary: str = ""
    risk_narrative: str = ""
    findings: list[Finding] = Field(default_factory=list)
    compatibility_matrix: list[CompatibilityEntry] = Field(default_factory=list)
    plan: UpgradePlan = Field(default_factory=UpgradePlan)
    downtime: DowntimeEstimate = Field(default_factory=DowntimeEstimate)
    unknown_risks: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    evidence_metrics: EvidenceMetrics = Field(default_factory=EvidenceMetrics)
    llm: LLMMetadata = Field(default_factory=LLMMetadata)

    @property
    def blocking_findings(self) -> list[Finding]:
        return [f for f in self.findings if f.blocking]

    def findings_by_severity(self) -> list[Finding]:
        return sorted(self.findings, key=lambda f: (f.severity.rank, f.category.value))
