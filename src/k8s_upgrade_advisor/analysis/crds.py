"""CRD storage-version analyzer.

Serving a new API version is the easy half of CRD lifecycle; the operational
debt lives in ``status.storedVersions``: every version objects have ever
been persisted at and not yet migrated off. A version cannot be dropped from
the CRD spec until it is removed from storedVersions — which requires
rewriting the stored objects (kube-storage-version-migrator, or a
touch-write of every object, then patching status).

This analyzer flags:
  - CRDs whose storedVersions contain versions other than the current
    storage version → migration needed before the old version can go away
  - CRDs still *serving* versions marked deprecated in their own spec
"""

from __future__ import annotations

import json

from ..models import (
    ClusterSnapshot,
    Evidence,
    Finding,
    FindingCategory,
    FindingOrigin,
    Severity,
)


def crd_findings(snapshot: ClusterSnapshot) -> list[Finding]:
    raw = snapshot.stdout("crds_json")
    if not raw:
        return []
    try:
        items = json.loads(raw).get("items", [])
    except json.JSONDecodeError:
        return []

    migration_needed: list[str] = []
    deprecated_served: list[str] = []

    for crd in items:
        name = crd.get("metadata", {}).get("name", "unknown")
        spec = crd.get("spec", {})
        versions = spec.get("versions", [])
        storage_version = next((v.get("name") for v in versions if v.get("storage")), None)
        stored = crd.get("status", {}).get("storedVersions", []) or []

        legacy_stored = [v for v in stored if v != storage_version]
        if legacy_stored:
            migration_needed.append(f"{name} (stored: {', '.join(stored)})")

        for version in versions:
            if version.get("served") and version.get("deprecated"):
                deprecated_served.append(f"{name}/{version.get('name')}")

    findings: list[Finding] = []
    if migration_needed:
        shown = migration_needed[:10]
        findings.append(
            Finding(
                id="crd-storage-version-migration",
                title=f"{len(migration_needed)} CRD(s) carry legacy storage versions "
                "needing migration",
                category=FindingCategory.CRD_COMPAT,
                severity=Severity.MEDIUM,
                origin=FindingOrigin.DETERMINISTIC,
                description=(
                    "status.storedVersions lists API versions with objects still persisted "
                    "in etcd. Operators cannot drop these versions from their CRDs (a common "
                    "step in operator upgrades that accompany cluster upgrades) until the "
                    "stored objects are rewritten at the current storage version — attempting "
                    "it fails or, worse, strands unreadable objects."
                ),
                remediation=(
                    "Run kube-storage-version-migrator, or touch-write every object of the "
                    "affected CRDs (kubectl get -o json | kubectl replace -f -) and then "
                    "patch status.storedVersions, before operator upgrades that drop old "
                    "versions."
                ),
                affected_objects=shown,
                evidence=[
                    Evidence(
                        kind="cluster-data",
                        detail="status.storedVersions vs spec storage version: "
                        + "; ".join(shown)
                        + (" …" if len(migration_needed) > 10 else ""),
                    )
                ],
            )
        )
    if deprecated_served:
        findings.append(
            Finding(
                id="crd-deprecated-versions-served",
                title=f"{len(deprecated_served)} CRD version(s) served while marked deprecated",
                category=FindingCategory.CRD_COMPAT,
                severity=Severity.LOW,
                origin=FindingOrigin.DETERMINISTIC,
                description=(
                    "These custom resource versions are flagged deprecated by their own CRD "
                    "spec; clients using them will break when the operator drops the version."
                ),
                remediation="Migrate clients/manifests to the storage version of each CRD.",
                affected_objects=deprecated_served[:10],
                evidence=[
                    Evidence(kind="cluster-data", detail="spec.versions[].deprecated flags.")
                ],
            )
        )
    return findings
