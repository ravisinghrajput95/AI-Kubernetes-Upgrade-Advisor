# Kubernetes Upgrade Assessment — 1.26 → 1.29

**Verdict: 🔶 NOT READY**  
Readiness **47/100** (capped at 95: unverified risks remain (see Unknown Risks)) · Confidence **94/100**

`assess-20260718-094024-5244b4` · 2026-07-18 09:40 UTC · cluster: **eks** · hops: 1.26→1.27 → 1.27→1.28 → 1.28→1.29

## Executive Summary

[dry-run] Deterministic assessment only — no LLM narrative was generated. Verdict not-ready with readiness 47/100 from 7 findings.

## Cluster Profile

- **Distribution:** eks (apiserver gitVersion 'v1.26.12-eks-1234' has EKS suffix)
- **Current version:** v1.26.12-eks-1234
- **Nodes:** 3 · **Workloads:** 3 deploy / 0 sts / 0 ds / 0 cron
- **Upgrade mechanism:** Managed control plane via EKS API (eksctl/console/IaC); managed node groups or Karpenter node rotation
- **Provider-managed:** etcd, kube-apiserver, kube-controller-manager, kube-scheduler, coredns (addon), kube-proxy (addon), vpc-cni (addon)

| Component | Version | Evidence |
|---|---|---|
| AWS VPC CNI | 1.13.0 | image |
| Karpenter | 0.31.0 | image |
| ingress-nginx | 1.8.1 | image |
| cert-manager | 1.11.0 | helm |

## Findings (7)

### 🟠 Karpenter 0.31.0 does not support Kubernetes 1.29

`high` · `autoscaler-compatibility` · origin: `deterministic`

Installed 0.31.0 < required 0.34 for Kubernetes 1.29. Karpenter v1 API (NodePool/NodeClaim) required from 0.32+; v1alpha5 Provisioners must be migrated.

**Affected:** karpenter

**Remediation:** Upgrade Karpenter to >= 0.34 *before* the control plane reaches 1.29.

<details><summary>Evidence</summary>

- _cluster-data_: Version 0.31.0 resolved from image (image 'karpenter/controller' in workloads; CRD 'nodepools.karpenter.sh').
- _static-table_: Support matrix: Kubernetes 1.29 requires >= 0.34.

</details>

### 🟠 ingress-nginx 1.8.1 does not support Kubernetes 1.29

`high` · `operator-compatibility` · origin: `deterministic`

Installed 1.8.1 < required 1.10.0 for Kubernetes 1.29.

**Affected:** ingress-nginx

**Remediation:** Upgrade ingress-nginx to >= 1.10.0 *before* the control plane reaches 1.29.

<details><summary>Evidence</summary>

- _cluster-data_: Version 1.8.1 resolved from image (image 'ingress-nginx/controller' in workloads).
- _static-table_: Support matrix: Kubernetes 1.29 requires >= 1.10.0.

</details>

### 🟠 cert-manager 1.11.0 does not support Kubernetes 1.29

`high` · `operator-compatibility` · origin: `deterministic`

Installed 1.11.0 < required 1.14 for Kubernetes 1.29. Webhook-based; must be compatible *before* the control plane hop.

**Affected:** cert-manager

**Remediation:** Upgrade cert-manager to >= 1.14 *before* the control plane reaches 1.29.

<details><summary>Evidence</summary>

- _cluster-data_: Version 1.11.0 resolved from helm (helm release 'cert-manager' chart cert-manager-v1.11.0; image 'jetstack/cert-manager' in workloads).
- _static-table_: Support matrix: Kubernetes 1.29 requires >= 1.14.

</details>

### 🟠 FlowSchema, PriorityLevelConfiguration (flowcontrol.apiserver.k8s.io/v1beta2) removed in 1.29

`high` · `removed-api` · origin: `deterministic` · effective in **1.29**

The cluster still serves flowcontrol.apiserver.k8s.io/v1beta2, which is removed in Kubernetes 1.29 (inside this upgrade path). Any manifest, Helm chart, controller, or stored object using this version fails after the hop to 1.29.

**Remediation:** Migrate to flowcontrol.apiserver.k8s.io/v1. Audit usage before upgrading: kubectl get flowschema.flowcontrol.apiserver.k8s.io -A; kubectl get prioritylevelconfiguration.flowcontrol.apiserver.k8s.io -A. Tools like 'pluto detect-all-in-cluster' or 'kubent' find manifests pinned to removed versions.

<details><summary>Evidence</summary>

- _cluster-data_: API group version 'flowcontrol.apiserver.k8s.io/v1beta2' is served by this cluster (kubectl api-versions) and is removed in Kubernetes 1.29.
- _static-table_: Deprecated 1.26, removed 1.29. Replacement: flowcontrol.apiserver.k8s.io/v1.

</details>

### 🟡 3-hop upgrade path: control plane must move one minor at a time

`medium` · `upgrade-path` · origin: `deterministic`

Kubernetes does not support skipping minors for the control plane. 1.26 → 1.29 requires sequential hops: 1.27 → 1.28 → 1.29. Each hop needs its own validation gate; API removals apply per-hop, not only at the final version.

**Remediation:** Plan and validate each hop independently; do not batch hops in one window.

<details><summary>Evidence</summary>

- _static-table_: Kubernetes version skew policy: kube-apiserver upgrades are supported only from the previous minor.

</details>

### ⚪ Unused auto-generated ServiceAccount tokens are invalidated

`info` · `kep-impact` · origin: `deterministic` · effective in **1.29**

From 1.29 (LegacyServiceAccountTokenCleanUp beta, on by default) auto-generated legacy token Secrets unused for a year are labelled invalid and later deleted. Long-forgotten external credentials stop working some time after the upgrade, not during it.

**Remediation:** Audit kubernetes.io/service-account-token Secrets and migrate consumers to TokenRequest before relying on them post-upgrade.

<details><summary>Evidence</summary>

- _static-table_: Unused auto-generated ServiceAccount tokens are invalidated — effective in Kubernetes 1.29 (KEP-2799)

</details>

### ⚪ kubelet version skew widened to n-3

`info` · `kep-impact` · origin: `deterministic` · effective in **1.28**

From 1.28 the control plane supports kubelets up to three minors older, enabling fewer node pool upgrade waves on long paths.

<details><summary>Evidence</summary>

- _static-table_: kubelet version skew widened to n-3 — effective in Kubernetes 1.28 (KEP-3935)

</details>

## Compatibility Matrix

Target: Kubernetes 1.29

| Component | Installed | Status | Min required | Notes |
|---|---|---|---|---|
| AWS VPC CNI | 1.13.0 | unknown | — | No static support matrix tracked; verify against upstream docs. |
| Karpenter | 0.31.0 | upgrade-required | 0.34 | Installed 0.31.0 < required 0.34 for Kubernetes 1.29. Karpenter v1 API (NodePool/NodeClaim) required from 0.32+; v1alpha5 Provisioners must be migrated. |
| ingress-nginx | 1.8.1 | upgrade-required | 1.10.0 | Installed 1.8.1 < required 1.10.0 for Kubernetes 1.29. |
| cert-manager | 1.11.0 | upgrade-required | 1.14 | Installed 1.11.0 < required 1.14 for Kubernetes 1.29. Webhook-based; must be compatible *before* the control plane hop. |

## Upgrade Plan

**Strategy:** sequential in-place upgrade, one minor per maintenance window

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

5. **Upgrade control plane to 1.27** _(~10 min, disruption: none)_
   Distribution mechanism: Managed control plane via EKS API (eksctl/console/IaC); managed node groups or Karpenter node rotation
   - `eksctl upgrade cluster --name <cluster> --version 1.27 --approve`
   - `aws eks update-cluster-version --name <cluster> --kubernetes-version 1.27`

### Addons

6. **Upgrade cluster addons for 1.27** _(~20 min, disruption: none)_
   CoreDNS, kube-proxy, CNI, CSI drivers, metrics-server to versions matching 1.27; managed addons via provider APIs. Detected components required at this hop: Karpenter → >=0.29, cert-manager → >=1.12, ingress-nginx → >=1.8.0.

### Node Pools

7. **Roll node pools to 1.27** _(~24 min, disruption: rolling)_
   1 node pool(s) detected (workers-a). Upgrade one pool at a time; start with the least critical. Use surge settings to keep capacity during rotation.
   - `eksctl upgrade nodegroup --cluster <cluster> --name <ng> --kubernetes-version 1.27`
   - `# Karpenter nodes: drift replacement picks up the new AMI automatically`

### Validation

8. **Validate hop to 1.27 before proceeding** _(~15 min, disruption: none)_
   Gate the next hop on: all nodes Ready at the new version, no CrashLoopBackOffs, webhook and DNS health, workload smoke tests.
   - `kubectl get nodes`
   - `kubectl get pods -A | grep -v Running | grep -v Completed`

### Control Plane

9. **Upgrade control plane to 1.28** _(~10 min, disruption: none)_
   Distribution mechanism: Managed control plane via EKS API (eksctl/console/IaC); managed node groups or Karpenter node rotation
   - `eksctl upgrade cluster --name <cluster> --version 1.28 --approve`
   - `aws eks update-cluster-version --name <cluster> --kubernetes-version 1.28`

### Addons

10. **Upgrade cluster addons for 1.28** _(~20 min, disruption: none)_
   CoreDNS, kube-proxy, CNI, CSI drivers, metrics-server to versions matching 1.28; managed addons via provider APIs. Detected components required at this hop: Karpenter → >=0.31, cert-manager → >=1.13, ingress-nginx → >=1.9.0.

### Node Pools

11. **Roll node pools to 1.28** _(~24 min, disruption: rolling)_
   1 node pool(s) detected (workers-a). Upgrade one pool at a time; start with the least critical. Use surge settings to keep capacity during rotation.
   - `eksctl upgrade nodegroup --cluster <cluster> --name <ng> --kubernetes-version 1.28`
   - `# Karpenter nodes: drift replacement picks up the new AMI automatically`

### Validation

12. **Validate hop to 1.28 before proceeding** _(~15 min, disruption: none)_
   Gate the next hop on: all nodes Ready at the new version, no CrashLoopBackOffs, webhook and DNS health, workload smoke tests.
   - `kubectl get nodes`
   - `kubectl get pods -A | grep -v Running | grep -v Completed`

### Control Plane

13. **Upgrade control plane to 1.29** _(~10 min, disruption: none)_
   Distribution mechanism: Managed control plane via EKS API (eksctl/console/IaC); managed node groups or Karpenter node rotation
   - `eksctl upgrade cluster --name <cluster> --version 1.29 --approve`
   - `aws eks update-cluster-version --name <cluster> --kubernetes-version 1.29`

### Addons

14. **Upgrade cluster addons for 1.29** _(~20 min, disruption: none)_
   CoreDNS, kube-proxy, CNI, CSI drivers, metrics-server to versions matching 1.29; managed addons via provider APIs. Detected components required at this hop: Karpenter → >=0.34, cert-manager → >=1.14, ingress-nginx → >=1.10.0.

### Node Pools

15. **Roll node pools to 1.29** _(~24 min, disruption: rolling)_
   1 node pool(s) detected (workers-a). Upgrade one pool at a time; start with the least critical. Use surge settings to keep capacity during rotation.
   - `eksctl upgrade nodegroup --cluster <cluster> --name <ng> --kubernetes-version 1.29`
   - `# Karpenter nodes: drift replacement picks up the new AMI automatically`

### Validation

16. **Validate hop to 1.29 before proceeding** _(~15 min, disruption: none)_
   Gate the next hop on: all nodes Ready at the new version, no CrashLoopBackOffs, webhook and DNS health, workload smoke tests.
   - `kubectl get nodes`
   - `kubectl get pods -A | grep -v Running | grep -v Completed`

### Workloads

17. **Run application-level verification** _(~30 min, disruption: none)_
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
- **Workloads:** Zero expected for PDB-protected, multi-replica workloads; single-replica pods restart once per node rotation.
- **Estimated window:** ~222 minutes
- _Assumption: 3 nodes, 3 workloads, 3 hop(s)_
- _Assumption: Surge capacity available for node rotation_
- _Assumption: 1 PodDisruptionBudget(s) present_

## Unknown Risks (honest gaps)

- No load/canary testing performed — runtime behaviour under production traffic during the upgrade is unverified.
- Knowledge base unavailable — recommendations lack document grounding (run 'k8s-upgrade-advisor build-kb').

## Evidence Appendix

- kubectl commands: 15/15 succeeded (critical: 9/9)
- Component versions resolved: 4/4
- KB chunks retrieved: 0 from 0 documents
- LLM: none/none (dry run)

---
_Generated by k8s-upgrade-advisor. Deterministic findings are provable from cluster data and static lifecycle tables; LLM-origin content is labelled._