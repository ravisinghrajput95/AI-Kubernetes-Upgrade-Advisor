# Kubernetes Upgrade Assessment — 1.35 → 1.36

**Verdict: 🔶 NOT READY**  
Readiness **60/100** (capped at 60: critical inventory commands failed — the cluster was not fully observable) · Confidence **75/100**

`assess-20260817-131146-864673` · 2026-08-17 13:11 UTC · cluster: **gke** · hops: 1.35→1.36

## Executive Summary

[dry-run] Deterministic assessment only — no LLM narrative was generated. Verdict not-ready with readiness 60/100 from 6 findings.

## Cluster Profile

- **Distribution:** gke (apiserver gitVersion 'v1.35.6-gke.1641000' has GKE suffix)
- **Current version:** v1.35.6-gke.1641000
- **Nodes:** 1 · **Workloads:** 7 deploy / 0 sts / 30 ds / 0 cron
- **Upgrade mechanism:** Managed control plane via GKE API (gcloud container clusters upgrade); node pools upgrade with surge settings; release channels gate versions
- **Provider-managed:** etcd, control plane, coredns/kube-dns, konnectivity, gce-pd CSI

| Component | Version | Evidence |
|---|---|---|
| GCE PD CSI Driver | 1.23.1-gke.20 | image |
| metrics-server | — | presence |

## Findings (6)

### 🟠 containerd 1.x support removed — nodes need containerd 2.x

`high` · `kep-impact` · origin: `deterministic` · effective in **1.36**

1.35 is the last release supporting containerd 1.x; from 1.36 the kubelet requires containerd 2.x (or another CRI 2.x runtime). Nodes still on containerd 1.x must be upgraded, and containerd 2.0 drops the deprecated registry.configs / registry.auths config structures, so runtime config may need rewriting.

**Remediation:** Upgrade node containerd to 2.x before crossing 1.36 and migrate any registry.configs/registry.auths settings to the 2.0 config format.

<details><summary>Evidence</summary>

- _static-table_: containerd 1.x support removed — nodes need containerd 2.x — effective in Kubernetes 1.36 (KEP-4033)

</details>

### 🟠 2 admission webhook(s) with failurePolicy=Fail and no scoping selector

`high` · `webhook-compatibility` · origin: `deterministic`

These webhooks gate admissions cluster-wide and hard-fail when their backend is unreachable. During node rotation the webhook's own pods get drained; if no replica is available, every intercepted admission — including system pods rescheduling onto new nodes — is rejected, which can deadlock the upgrade. failurePolicy defaults to Fail when unset.

**Affected:** warden-validating.config.common-webhooks.networking.gke.io/warden-validating.common-webhooks.networking.gke.io (ValidatingWebhookConfiguration), warden-mutating.config.common-webhooks.networking.gke.io/warden-mutating.common-webhooks.networking.gke.io (MutatingWebhookConfiguration)

**Remediation:** Before the upgrade window: ensure each webhook backend has >=2 replicas with a PodDisruptionBudget and a namespaceSelector that at minimum excludes kube-system; consider failurePolicy: Ignore for non-security webhooks for the duration of the upgrade.

<details><summary>Evidence</summary>

- _cluster-data_: failurePolicy/selectors read from webhook configurations: warden-validating.config.common-webhooks.networking.gke.io/warden-validating.common-webhooks.networking.gke.io (ValidatingWebhookConfiguration), warden-mutating.config.common-webhooks.networking.gke.io/warden-mutating.common-webhooks.networking.gke.io (MutatingWebhookConfiguration)

</details>

### 🟡 k8s.gcr.io frozen — images must come from registry.k8s.io

`medium` · `breaking-change` · origin: `deterministic` · effective in **1.27**

The legacy k8s.gcr.io registry was frozen in April 2023 (calendar-based, not tied to any cluster version): images referencing it receive no new tags and may disappear. This applies to every upgrade window as long as workloads still pull from it.

**Remediation:** Repoint image references from k8s.gcr.io to registry.k8s.io.

<details><summary>Evidence</summary>

- _static-table_: k8s.gcr.io frozen — images must come from registry.k8s.io — effective in Kubernetes 1.27
- _cluster-data_: Workload images reference the frozen k8s.gcr.io registry.

</details>

### 🟡 metrics-server version unresolved — compatibility unverified

`medium` · `operator-compatibility` · origin: `deterministic`

Detected via image 'metrics-server:' in workloads but version could not be resolved; requires >= 0.8 for Kubernetes 1.36.

**Remediation:** Determine the installed version (helm list, image tags) and confirm >= 0.8 before upgrading.

<details><summary>Evidence</summary>

- _cluster-data_: Detected via image 'metrics-server:' in workloads, no version signal.

</details>

### 🔵 Service .spec.externalIPs deprecated

`low` · `breaking-change` · origin: `deterministic` · effective in **1.36**

From 1.36 Service .spec.externalIPs is deprecated: the apiserver warns on its use. The field is a long-standing man-in-the-middle risk (CVE-2020-8554) and is slated for removal in a future release.

**Remediation:** Move Services off .spec.externalIPs to LoadBalancer/NodePort or an ingress controller; where it must stay, gate it with the DenyServiceExternalIPs admission controller.

<details><summary>Evidence</summary>

- _static-table_: Service .spec.externalIPs deprecated — effective in Kubernetes 1.36

</details>

### 🔵 8 CRD version(s) served while marked deprecated

`low` · `crd-compatibility` · origin: `deterministic`

These custom resource versions are flagged deprecated by their own CRD spec; clients using them will break when the operator drops the version.

**Affected:** capacitybuffers.autoscaling.x-k8s.io/v1alpha1, managedcertificates.networking.gke.io/v1beta1, managedcertificates.networking.gke.io/v1beta2, provisioningrequests.autoscaling.x-k8s.io/v1beta1, serviceattachments.networking.gke.io/v1beta1, volumesnapshotclasses.snapshot.storage.k8s.io/v1beta1, volumesnapshotcontents.snapshot.storage.k8s.io/v1beta1, volumesnapshots.snapshot.storage.k8s.io/v1beta1

**Remediation:** Migrate clients/manifests to the storage version of each CRD.

<details><summary>Evidence</summary>

- _cluster-data_: spec.versions[].deprecated flags.

</details>

## Compatibility Matrix

Target: Kubernetes 1.36

| Component | Installed | Status | Min required | Notes |
|---|---|---|---|---|
| GCE PD CSI Driver | 1.23.1-gke.20 | unknown | — | No static support matrix tracked; verify against upstream docs. |
| metrics-server | unknown | unknown | 0.8 | Detected via image 'metrics-server:' in workloads but version could not be resolved; requires >= 0.8 for Kubernetes 1.36. |

## Upgrade Plan

**Strategy:** single-minor in-place upgrade

### Preparation

1. **Snapshot cluster state and back up** _(~15 min, disruption: none)_
   etcd snapshot (self-managed) or velero backup; export current manifests for diffing.
   - `velero backup create pre-upgrade-$(date +%Y%m%d)`
   - `kubectl get all -A -o yaml > pre-upgrade-state.yaml`

2. **Remediate removed-API usage flagged in findings** _(~60 min, disruption: none)_
   Every finding in the removed-api category must be resolved before the first hop.
   - `pluto detect-all-in-cluster`
   - `kubent`

3. **Upgrade webhook-backed operators to target-compatible versions** _(~30 min, disruption: none)_
   cert-manager, policy engines, and other admission webhooks must support the target version *before* the control plane moves — a failing webhook can block all admissions cluster-wide.

4. **Verify PodDisruptionBudgets allow node drains** _(~15 min, disruption: none)_
   PDBs with maxUnavailable: 0 or single-replica workloads without PDBs will stall or take downtime during node rotation.
   - `kubectl get pdb -A`

### Control Plane

5. **Upgrade control plane to 1.36** _(~10 min, disruption: none)_
   Distribution mechanism: Managed control plane via GKE API (gcloud container clusters upgrade); node pools upgrade with surge settings; release channels gate versions
   - `gcloud container clusters upgrade <cluster> --master --cluster-version 1.36`

### Addons

6. **Upgrade cluster addons for 1.36** _(~20 min, disruption: none)_
   CoreDNS, kube-proxy, CNI, CSI drivers, metrics-server to versions matching 1.36; managed addons via provider APIs. Detected components required at this hop: metrics-server → >=0.8.

### Node Pools

7. **Roll node pools to 1.36** _(~20 min, disruption: rolling)_
   1 node pool(s) detected (default-pool). Upgrade one pool at a time; start with the least critical. Use surge settings to keep capacity during rotation.
   - `gcloud container clusters upgrade <cluster> --node-pool <pool> --cluster-version 1.36`

### Validation

8. **Validate hop to 1.36 before proceeding** _(~15 min, disruption: none)_
   Gate the next hop on: all nodes Ready at the new version, no CrashLoopBackOffs, webhook and DNS health, workload smoke tests.
   - `kubectl get nodes`
   - `kubectl get pods -A | grep -v Running | grep -v Completed`

### Workloads

9. **Run application-level verification** _(~30 min, disruption: none)_
   Synthetic checks / golden transactions against critical services; compare error rates and latency to the pre-upgrade baseline.

## Rollback Plan

1. **Control plane rollback reality** — Managed control planes (EKS/GKE/AKS) cannot be downgraded once upgraded. Self-managed: kubeadm downgrade is unsupported territory once etcd schema migrates — restore from the etcd snapshot instead. Rollback planning must therefore be *forward-fix for the control plane, backward-roll for nodes*.
2. **Roll nodes back to previous version** — Recreate node pools pinned to the previous version/AMI and drain new-version nodes.
3. **Revert workload/addon changes** — Helm rollback for addons upgraded during the window; redeploy prior manifests via GitOps history.
   - `helm rollback <release> <revision>`

## Pre-Upgrade Checklist

- [ ] All removed-API findings remediated and verified with pluto/kubent
- [ ] Webhook operators (cert-manager, policy engines) at target-compatible versions
- [ ] etcd snapshot / velero backup completed and restore-tested
- [ ] PDBs reviewed; no maxUnavailable: 0 deadlocks
- [ ] Maintenance window and rollback decision criteria agreed
- [ ] Monitoring dashboards and alerts green before starting
- [ ] Cloud provider release notes for the target version reviewed

## Post-Upgrade Validation

- [ ] All nodes Ready at target kubelet version (kubectl get nodes)
- [ ] No pods in CrashLoopBackOff/ImagePullBackOff introduced by the upgrade
- [ ] Admission webhooks answering (create a dry-run object through each)
- [ ] DNS resolution healthy (CoreDNS metrics/log check)
- [ ] Ingress traffic serving; certificate issuance verified if cert-manager present
- [ ] Autoscaling verified (scale a canary deployment; node provisioning if Karpenter/CA)
- [ ] Application golden-path checks pass; error budgets not consumed
- [ ] Deprecation warnings in audit logs reviewed for the *next* upgrade

## Downtime & Disruption Estimate

- **Control plane:** None expected — managed control plane upgrades are non-disruptive to the API.
- **Workloads:** No PDBs detected: every workload restarts un-coordinated during node rotation; single-replica services will take brief outages.
- **Estimated window:** ~70 minutes
- _Assumption: 1 nodes, 37 workloads, 1 hop(s)_
- _Assumption: Surge capacity available for node rotation_

## Unknown Risks (honest gaps)

- Version could not be resolved for: metrics-server — their target compatibility is unverified.
- No PodDisruptionBudget data — workload disruption during node drains is unmodelled.
- No load/canary testing performed — runtime behaviour under production traffic during the upgrade is unverified.
- Knowledge base unavailable — recommendations lack document grounding (run 'k8s-upgrade-advisor build-kb').

## Evidence Appendix

- kubectl commands: 27/29 succeeded (critical: 8/9)
- Component versions resolved: 1/2
- KB chunks retrieved: 0 from 0 documents
- LLM: none/none (dry run)

---
_Generated by k8s-upgrade-advisor. Deterministic findings are provable from cluster data and static lifecycle tables; LLM-origin content is labelled._