"""Shared fixtures: synthetic cluster snapshots per distribution and a
hash-embedder knowledge base — no network, no kubectl, no LLM."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from k8s_upgrade_advisor.config import Settings
from k8s_upgrade_advisor.knowledge.chunker import chunk_documents
from k8s_upgrade_advisor.knowledge.embeddings import HashingEmbedder
from k8s_upgrade_advisor.knowledge.fetcher import RawDocument
from k8s_upgrade_advisor.knowledge.store import KnowledgeStore
from k8s_upgrade_advisor.models import ClusterSnapshot, CommandResult, HelmRelease

FIXTURES = Path(__file__).parent / "fixtures"


def _ok(stdout: str) -> CommandResult:
    return CommandResult(stdout=stdout, returncode=0)


def _fail(stderr: str = "error", code: int = 1) -> CommandResult:
    return CommandResult(stderr=stderr, returncode=code)


def make_nodes_json(
    count: int,
    kubelet: str,
    provider_id: str,
    pool_label: dict | None = None,
    runtime: str = "containerd://1.7.0",
    name_prefix: str = "node",
) -> str:
    return json.dumps(
        {
            "items": [
                {
                    "metadata": {"name": f"{name_prefix}-{i}", "labels": pool_label or {}},
                    "spec": {"providerID": provider_id},
                    "status": {
                        "nodeInfo": {
                            "kubeletVersion": kubelet,
                            "osImage": "Linux",
                            "containerRuntimeVersion": runtime,
                        },
                        "conditions": [{"type": "Ready", "status": "True"}],
                    },
                }
                for i in range(count)
            ]
        }
    )


def base_kubectl(
    git_version: str,
    nodes_json: str,
    api_versions: str = "apps/v1\npolicy/v1",
    deployments: str = "NS NAME IMAGES\n",
) -> dict[str, CommandResult]:
    return {
        "version": _ok(json.dumps({"serverVersion": {"gitVersion": git_version}})),
        "nodes": _ok("NAME STATUS\nnode-0 Ready"),
        "nodes_json": _ok(nodes_json),
        "api_versions": _ok(api_versions),
        "api_resources": _ok("deployments apps true Deployment"),
        "crds": _ok(""),
        "deployments": _ok(deployments),
        "statefulsets": _ok("NS NAME\n"),
        "daemonsets": _ok("NS NAME\n"),
        "jobs": _ok(""),
        "cronjobs": _ok(""),
        "validating_webhooks": _ok(""),
        "mutating_webhooks": _ok(""),
        "pdbs": _ok(""),
        "psp": _fail("the server doesn't have a resource type"),
        "top_nodes": _ok("node-0 5% 10%"),
    }


@pytest.fixture
def eks_snapshot() -> ClusterSnapshot:
    return ClusterSnapshot.model_validate_json((FIXTURES / "eks_1_26.json").read_text())


@pytest.fixture
def gke_snapshot() -> ClusterSnapshot:
    nodes = make_nodes_json(
        2,
        "v1.29.5-gke.1091002",
        "gce://proj/zone/instance-1",
        {"cloud.google.com/gke-nodepool": "default-pool"},
    )
    return ClusterSnapshot(kubectl=base_kubectl("v1.29.5-gke.1091002", nodes))


@pytest.fixture
def kind_snapshot() -> ClusterSnapshot:
    nodes = make_nodes_json(1, "v1.29.2", "", name_prefix="kind-control-plane")
    kubectl = base_kubectl("v1.29.2", nodes)
    kubectl["nodes"] = _ok("NAME STATUS\nkind-control-plane Ready")
    kubectl["top_nodes"] = _fail("Metrics API not available")
    return ClusterSnapshot(kubectl=kubectl)


@pytest.fixture
def openshift_snapshot() -> ClusterSnapshot:
    nodes = make_nodes_json(3, "v1.28.9", "")
    kubectl = base_kubectl("v1.28.9", nodes)
    kubectl["api_resources"] = _ok("routes route.openshift.io true Route")
    return ClusterSnapshot(kubectl=kubectl)


@pytest.fixture
def helm_snapshot() -> ClusterSnapshot:
    """Cluster where cert-manager version comes from Helm (preferred over image)."""
    nodes = make_nodes_json(1, "v1.28.0", "aws:///us-east-1a/i-1")
    kubectl = base_kubectl(
        "v1.28.0-eks-abc",
        nodes,
        deployments="NS NAME IMAGES\ncert-manager cm quay.io/jetstack/cert-manager-controller:v1.99.0",
    )
    return ClusterSnapshot(
        kubectl=kubectl,
        helm_available=True,
        helm_releases=[
            HelmRelease(
                name="cert-manager",
                namespace="cert-manager",
                chart="cert-manager-v1.14.4",
                chart_name="cert-manager",
                chart_version="v1.14.4",
                app_version="v1.14.4",
            )
        ],
    )


@pytest.fixture
def kb_store(tmp_path: Path) -> KnowledgeStore:
    docs = [
        RawDocument(
            "k8s-changelog-1-29",
            "Kubernetes 1.29 CHANGELOG",
            "https://k8s.io/1.29",
            "release-notes",
            None,
            "1.29",
            "## Deprecation\n\nThe flowcontrol.apiserver.k8s.io/v1beta2 API version of "
            "FlowSchema is no longer served in v1.29. Use flowcontrol.apiserver.k8s.io/v1 "
            "instead for all flow control configuration objects going forward.",
        ),
        RawDocument(
            "k8s-changelog-1-24",
            "Kubernetes 1.24 CHANGELOG",
            "https://k8s.io/1.24",
            "release-notes",
            None,
            "1.24",
            "## Urgent Upgrade Notes\n\nDockershim removed from kubelet in 1.24. Migrate "
            "container runtime from Docker Engine to containerd or CRI-O before upgrading.",
        ),
        RawDocument(
            "cert-manager-supported",
            "cert-manager supported releases",
            "https://cert-manager.io",
            "component-docs",
            "cert-manager",
            None,
            "## Supported releases\n\ncert-manager 1.14 supports Kubernetes 1.24 through "
            "1.29 inclusive. Upgrade cert-manager before the Kubernetes control plane.",
        ),
        RawDocument(
            "istio-support",
            "Istio supported releases",
            "https://istio.io",
            "component-docs",
            "istio",
            None,
            "## Support status\n\nIstio 1.20 supports Kubernetes versions 1.25 to 1.29 "
            "as tested platforms. Older Istio releases are unsupported on newer clusters.",
        ),
    ]
    chunks = chunk_documents(docs, 700, 100)
    return KnowledgeStore.build(
        chunks, HashingEmbedder(), tmp_path / "kb", "1.28", "1.29", 700, 100, len(docs)
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = Settings()
    s.paths.kb_dir = tmp_path / "kb"
    s.paths.reports_dir = tmp_path / "reports"
    s.knowledge.embedding_backend = "hash"
    s.llm.provider = "none"
    return s
