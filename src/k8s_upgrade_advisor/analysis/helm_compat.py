"""Helm release compatibility analyzer.

The classic post-upgrade Helm failure: a release's *stored* manifests
reference an API version the new cluster no longer serves. ``helm upgrade``
then fails ("unable to build kubernetes objects from current release
manifest") until the release storage is rewritten — the problem the
``mapkubeapis`` plugin exists for.

We scan the rendered manifests collected per release for apiVersions that
are removed inside the upgrade window, using the same removal table as the
API lifecycle engine — one source of truth.
"""

from __future__ import annotations

import re

from ..models import (
    ClusterSnapshot,
    Evidence,
    Finding,
    FindingCategory,
    FindingOrigin,
    KubeVersion,
    Severity,
)
from .api_lifecycle import removals_in_range

_API_VERSION_RE = re.compile(r"^apiVersion:\s*([\w./\-]+)\s*$", re.MULTILINE)


def helm_release_findings(
    snapshot: ClusterSnapshot, source: KubeVersion, target: KubeVersion
) -> list[Finding]:
    if not snapshot.helm_manifests:
        return []

    removed_gvs = {r.group_version: r for r in removals_in_range(source, target)}
    if not removed_gvs:
        return []

    findings: list[Finding] = []
    for release_key, manifest in snapshot.helm_manifests.items():
        used = set(_API_VERSION_RE.findall(manifest))
        hits = sorted(used & set(removed_gvs))
        if not hits:
            continue
        first = removed_gvs[hits[0]]
        findings.append(
            Finding(
                id=f"helm-removed-api-{release_key.replace('/', '-')}",
                title=f"Helm release {release_key} renders API versions removed in this window",
                category=FindingCategory.HELM_COMPAT,
                severity=Severity.HIGH,
                origin=FindingOrigin.DETERMINISTIC,
                description=(
                    f"The stored manifest of release '{release_key}' contains "
                    f"{', '.join(hits)} — removed at or before Kubernetes "
                    f"{target.minor_str}. After the cluster upgrade, 'helm upgrade' on this "
                    "release fails against its own stored state, and the rendered objects "
                    "cannot be re-applied."
                ),
                remediation=(
                    "Upgrade the chart to a version emitting current APIs before the cluster "
                    f"upgrade (replacement for {hits[0]}: {first.replacement}). If the release "
                    "storage itself is stale, rewrite it with the 'helm mapkubeapis' plugin."
                ),
                affected_objects=[release_key],
                blocking=False,
                evidence=[
                    Evidence(
                        kind="cluster-data",
                        detail=f"'helm get manifest' for {release_key} contains: {', '.join(hits)}.",
                    ),
                    Evidence(
                        kind="static-table",
                        detail=f"{hits[0]} removed in {first.removed_in} "
                        f"(deprecated {first.deprecated_in}).",
                    ),
                ],
            )
        )
    return findings
