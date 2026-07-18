"""Live cluster collection via kubectl and helm.

Principles:
  - Success is judged by process return code, never by stderr content —
    kubectl emits deprecation and throttling *warnings* on stderr while
    succeeding.
  - Commands run concurrently (they are independent reads) with a bounded
    pool so large clusters don't serialize into a multi-minute collection.
  - The collector only fills a :class:`ClusterSnapshot`; no analysis here.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

from ..models.cluster import ClusterSnapshot, CommandResult, HelmRelease
from ..observability import get_logger

log = get_logger(__name__)

# Read-only inventory commands. Keys are stable identifiers used by analyzers.
KUBECTL_COMMANDS: dict[str, list[str]] = {
    "version": ["version", "--output=json"],
    "nodes": ["get", "nodes", "-o", "wide"],
    "nodes_json": ["get", "nodes", "-o", "json"],
    # --show-labels so analyzers can verify Pod Security Admission labels
    # actually exist after a PSP migration.
    "namespaces": ["get", "ns", "--show-labels"],
    "api_resources": ["api-resources", "--verbs=list", "-o", "wide"],
    "api_versions": ["api-versions"],
    "api_services": ["get", "apiservices"],
    "deployments": ["get", "deploy", "-A", "-o", "wide"],
    "statefulsets": ["get", "sts", "-A", "-o", "wide"],
    "daemonsets": ["get", "ds", "-A", "-o", "wide"],
    "jobs": ["get", "jobs", "-A"],
    "cronjobs": ["get", "cronjobs", "-A"],
    "crds": ["get", "crd"],
    # Full objects: analyzers inspect status.storedVersions (storage-version
    # migration debt) and webhook failurePolicy/timeout/selectors.
    "crds_json": ["get", "crd", "-o", "json"],
    "validating_webhooks": ["get", "validatingwebhookconfigurations"],
    "mutating_webhooks": ["get", "mutatingwebhookconfigurations"],
    "validating_webhooks_json": ["get", "validatingwebhookconfigurations", "-o", "json"],
    "mutating_webhooks_json": ["get", "mutatingwebhookconfigurations", "-o", "json"],
    "storage_classes": ["get", "sc"],
    "csi_drivers": ["get", "csidrivers"],
    "pvs": ["get", "pv"],
    "pvcs": ["get", "pvc", "-A"],
    "pdbs": ["get", "pdb", "-A"],
    "hpas": ["get", "hpa", "-A"],
    "priority_classes": ["get", "priorityclasses"],
    "top_nodes": ["top", "nodes"],
    "cluster_info": ["cluster-info"],
    "flowschemas": ["get", "flowschemas"],
    # Expected to fail on >=1.25 clusters; when it succeeds it is direct
    # usage evidence for the PodSecurityPolicy removal finding.
    "psp": ["get", "psp"],
}

# Commands whose failure means we genuinely can't assess the cluster.
CRITICAL_COMMANDS = {
    "version",
    "nodes",
    "api_resources",
    "crds",
    "validating_webhooks",
    "mutating_webhooks",
    "deployments",
    "statefulsets",
    "daemonsets",
}


def _run(argv: list[str], timeout: float = 45.0) -> CommandResult:
    start = time.monotonic()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return CommandResult(
            args=argv,
            stdout=proc.stdout.strip(),
            stderr=proc.stderr.strip(),
            returncode=proc.returncode,
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    except FileNotFoundError:
        return CommandResult(args=argv, error=f"{argv[0]} not found in PATH")
    except subprocess.TimeoutExpired:
        return CommandResult(
            args=argv,
            error=f"timed out after {timeout:.0f}s",
            duration_ms=int((time.monotonic() - start) * 1000),
        )


def _global_args(context: str | None, kubeconfig: str | None) -> list[str]:
    extra: list[str] = []
    if context:
        extra += ["--context", context]
    if kubeconfig:
        extra += ["--kubeconfig", kubeconfig]
    return extra


def _collect_helm(context: str | None, kubeconfig: str | None) -> tuple[list[HelmRelease], bool]:
    """Helm gives the highest-quality component version evidence (chart +
    app version per release). Absence of helm is fine — analyzers fall back
    to image-tag parsing."""
    if shutil.which("helm") is None:
        return [], False
    argv = ["helm", "list", "-A", "-o", "json", "--max", "500"]
    if kubeconfig:
        argv += ["--kubeconfig", kubeconfig]
    if context:
        argv += ["--kube-context", context]
    result = _run(argv, timeout=30)
    if not result.ok:
        log.warning("helm_list_failed", error=result.error or result.stderr[:200])
        return [], True
    releases: list[HelmRelease] = []
    try:
        for item in json.loads(result.stdout or "[]"):
            chart = item.get("chart", "")
            # chart is "<name>-<version>", version may itself contain dashes-free semver
            name, _, version = chart.rpartition("-")
            releases.append(
                HelmRelease(
                    name=item.get("name", ""),
                    namespace=item.get("namespace", ""),
                    chart=chart,
                    chart_name=name or chart,
                    chart_version=version,
                    app_version=item.get("app_version", ""),
                    status=item.get("status", ""),
                    revision=int(item["revision"])
                    if str(item.get("revision", "")).isdigit()
                    else None,
                )
            )
    except (json.JSONDecodeError, TypeError) as exc:
        log.warning("helm_parse_failed", error=str(exc))
    return releases, True


_MAX_MANIFEST_RELEASES = 30
_MAX_MANIFEST_CHARS = 400_000


def _collect_helm_manifests(
    releases: list[HelmRelease],
    context: str | None,
    kubeconfig: str | None,
    max_workers: int = 6,
) -> dict[str, str]:
    """Rendered manifests per release, so analyzers can detect templates
    still emitting API versions the target Kubernetes removes ('helm upgrade'
    breaks on those after the cluster upgrade — the mapkubeapis problem).
    Bounded: first N releases, capped size per manifest."""
    if not releases:
        return {}

    def _get_manifest(release: HelmRelease) -> tuple[str, str]:
        argv = ["helm", "get", "manifest", release.name, "-n", release.namespace]
        if kubeconfig:
            argv += ["--kubeconfig", kubeconfig]
        if context:
            argv += ["--kube-context", context]
        result = _run(argv, timeout=20)
        key = f"{release.namespace}/{release.name}"
        return key, result.stdout[:_MAX_MANIFEST_CHARS] if result.ok else ""

    manifests: dict[str, str] = {}
    subset = releases[:_MAX_MANIFEST_RELEASES]
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for key, text in pool.map(_get_manifest, subset):
            if text:
                manifests[key] = text
    log.info("helm_manifests_collected", releases=len(subset), fetched=len(manifests))
    return manifests


def _collect_deprecated_api_metrics(extra: list[str], timeout: float = 45.0) -> CommandResult:
    """The canonical usage signal for deprecated APIs: the apiserver's
    ``apiserver_requested_deprecated_apis`` metric records which deprecated
    group/versions have actually been *requested* since the apiserver
    started — the difference between "served" (available) and "used"
    (breaking on removal).

    The raw /metrics payload is megabytes; only the relevant lines are kept
    so snapshots stay small. Requires GET on the /metrics nonResourceURL
    (cluster-admin has it); failure just means analyzers fall back to
    served-only evidence."""
    result = _run(["kubectl", *extra, "get", "--raw", "/metrics"], timeout)
    if result.ok:
        kept = [
            line
            for line in result.stdout.splitlines()
            if line.startswith("apiserver_requested_deprecated_apis")
        ]
        result = CommandResult(
            args=[*result.args[:-2], "--raw", "/metrics (filtered)"],
            stdout="\n".join(kept),
            stderr=result.stderr,
            returncode=result.returncode,
            duration_ms=result.duration_ms,
        )
    return result


def collect_cluster_snapshot(
    context: str | None = None,
    kubeconfig: str | None = None,
    timeout: float = 45.0,
    max_workers: int = 8,
) -> ClusterSnapshot:
    """Collect a full snapshot from the live cluster the kubeconfig points at."""
    extra = _global_args(context, kubeconfig)
    log.info("cluster_collection_started", commands=len(KUBECTL_COMMANDS), context=context)

    results: dict[str, CommandResult] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            key: pool.submit(_run, ["kubectl", *extra, *args], timeout)
            for key, args in KUBECTL_COMMANDS.items()
        }
        for key, future in futures.items():
            results[key] = future.result()

    results["deprecated_api_requests"] = _collect_deprecated_api_metrics(extra, timeout)

    helm_releases, helm_available = _collect_helm(context, kubeconfig)
    helm_manifests = _collect_helm_manifests(helm_releases, context, kubeconfig)

    snapshot = ClusterSnapshot(
        source="live",
        context=context,
        kubectl=results,
        helm_releases=helm_releases,
        helm_available=helm_available,
        helm_manifests=helm_manifests,
    )
    log.info(
        "cluster_collection_finished",
        ok=snapshot.commands_ok,
        total=snapshot.commands_total,
        helm_releases=len(helm_releases),
    )
    return snapshot
