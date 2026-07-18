"""Admission webhook mechanics analyzer.

The single most dangerous upgrade configuration in Kubernetes is a
``failurePolicy: Fail`` webhook with cluster-wide scope: if its backend is
unavailable while nodes roll (its own pods are being drained), *every*
admission it intercepts fails — including the system pods trying to
reschedule, which can deadlock the cluster. This analyzer reads the webhook
configurations the collector already fetches and reports:

  - Fail-policy webhooks with no namespaceSelector/objectSelector scoping
    (v1 defaults failurePolicy to Fail when unset — the default is the trap)
  - long timeoutSeconds that amplify the blast radius during instability
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


def _iter_webhooks(snapshot: ClusterSnapshot) -> list[tuple[str, str, dict]]:
    """Yield (config_kind, config_name, webhook_dict) from both webhook types."""
    out: list[tuple[str, str, dict]] = []
    for key, kind in (
        ("validating_webhooks_json", "ValidatingWebhookConfiguration"),
        ("mutating_webhooks_json", "MutatingWebhookConfiguration"),
    ):
        raw = snapshot.stdout(key)
        if not raw:
            continue
        try:
            items = json.loads(raw).get("items", [])
        except json.JSONDecodeError:
            continue
        for config in items:
            name = config.get("metadata", {}).get("name", "unknown")
            for webhook in config.get("webhooks", []):
                out.append((kind, name, webhook))
    return out


def webhook_findings(snapshot: ClusterSnapshot) -> list[Finding]:
    findings: list[Finding] = []
    unscoped_fail: list[str] = []
    slow: list[str] = []

    for kind, config_name, webhook in _iter_webhooks(snapshot):
        webhook_name = webhook.get("name", "unnamed")
        label = f"{config_name}/{webhook_name}"
        # v1 default when unset is Fail — the unset case is the dangerous one.
        policy = webhook.get("failurePolicy", "Fail")
        scoped = bool(webhook.get("namespaceSelector")) or bool(webhook.get("objectSelector"))
        if policy == "Fail" and not scoped:
            unscoped_fail.append(f"{label} ({kind})")
        timeout = webhook.get("timeoutSeconds", 10)
        if isinstance(timeout, int) and timeout > 15:
            slow.append(f"{label} ({timeout}s)")

    if unscoped_fail:
        shown = unscoped_fail[:10]
        findings.append(
            Finding(
                id="webhook-fail-policy-unscoped",
                title=f"{len(unscoped_fail)} admission webhook(s) with failurePolicy=Fail "
                "and no scoping selector",
                category=FindingCategory.WEBHOOK_COMPAT,
                severity=Severity.HIGH,
                origin=FindingOrigin.DETERMINISTIC,
                description=(
                    "These webhooks gate admissions cluster-wide and hard-fail when their "
                    "backend is unreachable. During node rotation the webhook's own pods get "
                    "drained; if no replica is available, every intercepted admission — "
                    "including system pods rescheduling onto new nodes — is rejected, which "
                    "can deadlock the upgrade. failurePolicy defaults to Fail when unset."
                ),
                remediation=(
                    "Before the upgrade window: ensure each webhook backend has >=2 replicas "
                    "with a PodDisruptionBudget and a namespaceSelector that at minimum "
                    "excludes kube-system; consider failurePolicy: Ignore for non-security "
                    "webhooks for the duration of the upgrade."
                ),
                affected_objects=shown,
                evidence=[
                    Evidence(
                        kind="cluster-data",
                        detail="failurePolicy/selectors read from webhook configurations: "
                        + ", ".join(shown)
                        + (" …" if len(unscoped_fail) > 10 else ""),
                    )
                ],
            )
        )

    if slow:
        findings.append(
            Finding(
                id="webhook-long-timeouts",
                title=f"{len(slow)} admission webhook(s) with timeoutSeconds > 15",
                category=FindingCategory.WEBHOOK_COMPAT,
                severity=Severity.LOW,
                origin=FindingOrigin.DETERMINISTIC,
                description=(
                    "Long webhook timeouts multiply API latency during upgrade instability: "
                    "every intercepted request waits out the full timeout when the backend "
                    "is degraded."
                ),
                remediation="Reduce timeoutSeconds (default 10) unless the webhook truly needs longer.",
                affected_objects=slow[:10],
                evidence=[
                    Evidence(kind="cluster-data", detail="timeoutSeconds from webhook configs.")
                ],
            )
        )
    return findings
