# Kubernetes Upgrade Assessment — 1.32 → 1.36

**Verdict: ⛔ BLOCKED**  
Readiness **20/100** (capped at 95: unverified risks remain (see Unknown Risks)) · Confidence **94/100**

`assess-20260817-171804-fffd59` · 2026-08-17 17:18 UTC · cluster: **kind** · hops: 1.32→1.33 → 1.33→1.34 → 1.34→1.35 → 1.35→1.36

## Executive Summary

The upgrade from Kubernetes 1.32 to 1.36 is currently blocked due to several critical and high-severity issues. The most significant blockers are the cgroup v1 default disablement in 1.35, which prevents kubelet initialization, and the containerd 1.x support removal in 1.36, requiring an upgrade to containerd 2.x. Additionally, the kubelet and kube-proxy versions will violate the version skew policy at 1.36. The upgrade path requires sequential minor version upgrades, with validation gates at each step. The next steps involve addressing these blockers and preparing for a sequential upgrade path.

## Cluster Profile

- **Distribution:** kind (kind node naming/image detected)
- **Current version:** v1.32.2
- **Nodes:** 1 · **Workloads:** 2 deploy / 0 sts / 2 ds / 0 cron
- **Upgrade mechanism:** Recreate cluster with a newer node image (kind clusters are disposable)

| Component | Version | Evidence |
|---|---|---|
| CoreDNS | 1.11.3 | image |

## Findings (9)

### 🔴 cgroup v1 disabled by default — kubelet fails to start on cgroup v1 nodes **[BLOCKING]**

`critical` · `kep-impact` · origin: `deterministic` · effective in **1.35**

From 1.35 the kubelet's failCgroupV1 setting defaults to true: this is a hard failure, not a warning. A kubelet on a node still running the cgroup v1 hierarchy refuses to initialise, so the node never becomes Ready after the upgrade. The failCgroupV1=false escape hatch exists but defers the inevitable and blocks cgroup-v2-only features.

**Remediation:** Confirm every node OS image boots with the cgroup v2 unified hierarchy (stat -fc %T /sys/fs/cgroup == cgroup2fs) before upgrading past 1.34; rebuild/replace node pools still on cgroup v1.

<details><summary>Evidence</summary>

- _static-table_: cgroup v1 disabled by default — kubelet fails to start on cgroup v1 nodes — effective in Kubernetes 1.35 (KEP-5573)

</details>

### 🟠 containerd 1.x support removed — nodes need containerd 2.x

`high` · `kep-impact` · origin: `deterministic` · effective in **1.36**

1.35 is the last release supporting containerd 1.x; from 1.36 the kubelet requires containerd 2.x (or another CRI 2.x runtime). Nodes still on containerd 1.x must be upgraded, and containerd 2.0 drops the deprecated registry.configs / registry.auths config structures, so runtime config may need rewriting.

**Remediation:** Upgrade node containerd to 2.x before crossing 1.36 and migrate any registry.configs/registry.auths settings to the 2.0 config format.

<details><summary>Evidence</summary>

- _static-table_: containerd 1.x support removed — nodes need containerd 2.x — effective in Kubernetes 1.36 (KEP-4033)

</details>

### 🟠 kubelet 1.32 will violate skew policy at control plane 1.36 **[BLOCKING]**

`high` · `version-skew` · origin: `deterministic`

1 node(s) run kubelet 1.32 (k8s-advisor-test-control-plane). The skew policy allows kubelets at most 3 minors behind the apiserver, so these nodes will violate the policy when the control plane reaches 1.36.

**Affected:** k8s-advisor-test-control-plane

**Remediation:** Upgrade these node pools to within 3 minors before the control plane hop to 1.36.

<details><summary>Evidence</summary>

- _cluster-data_: kubelet versions from node inventory: 1 node(s) at 1.32.
- _static-table_: Skew policy: kubelet may trail apiserver by 3 minors at control plane 1.36.

</details>

### 🟠 kube-proxy 1.32 falls out of skew at control plane 1.36 **[BLOCKING]**

`high` · `version-skew` · origin: `deterministic`

kube-proxy follows the kubelet skew window (n-3 at 1.36); it must be upgraded with the addon phase before this hop.

**Remediation:** Upgrade the kube-proxy addon before the control plane reaches 1.36.

<details><summary>Evidence</summary>

- _cluster-data_: kube-proxy image tag v1.32 from the DaemonSet listing.

</details>

### 🟡 exec/attach/port-forward now require the 'create' verb

`medium` · `breaking-change` · origin: `deterministic` · effective in **1.35**

From 1.35 WebSocket streaming requests (kubectl exec, attach, port-forward) are authorised against the 'create' verb on the pod subresource rather than 'get'. RBAC roles that granted only 'get' on pods/exec, pods/attach, or pods/portforward stop permitting those actions after the upgrade.

**Remediation:** Audit Roles/ClusterRoles granting pods/exec, pods/attach, or pods/portforward and ensure they include the 'create' verb.

<details><summary>Evidence</summary>

- _static-table_: exec/attach/port-forward now require the 'create' verb — effective in Kubernetes 1.35

</details>

### 🟡 4-hop upgrade path: control plane must move one minor at a time

`medium` · `upgrade-path` · origin: `deterministic`

Kubernetes does not support skipping minors for the control plane. 1.32 → 1.36 requires sequential hops: 1.33 → 1.34 → 1.35 → 1.36. Each hop needs its own validation gate; API removals apply per-hop, not only at the final version.

**Remediation:** Plan and validate each hop independently; do not batch hops in one window.

<details><summary>Evidence</summary>

- _static-table_: Kubernetes version skew policy: kube-apiserver upgrades are supported only from the previous minor.

</details>

### 🔵 Service .spec.externalIPs deprecated

`low` · `breaking-change` · origin: `deterministic` · effective in **1.36**

From 1.36 Service .spec.externalIPs is deprecated: the apiserver warns on its use. The field is a long-standing man-in-the-middle risk (CVE-2020-8554) and is slated for removal in a future release.

**Remediation:** Move Services off .spec.externalIPs to LoadBalancer/NodePort or an ingress controller; where it must stay, gate it with the DenyServiceExternalIPs admission controller.

<details><summary>Evidence</summary>

- _static-table_: Service .spec.externalIPs deprecated — effective in Kubernetes 1.36

</details>

### 🔵 kube-proxy IPVS mode deprecated

`low` · `kep-impact` · origin: `deterministic` · effective in **1.35**

From 1.35 the kube-proxy IPVS mode is formally deprecated, with nftables mode positioned as the long-term replacement. IPVS keeps working for now but removal is targeted for a later release (~1.38), so clusters pinning --proxy-mode=ipvs should plan a migration.

**Remediation:** Plan migration of kube-proxy from IPVS to the nftables (or iptables) mode ahead of its removal; validate Service/NetworkPolicy behaviour on nftables.

<details><summary>Evidence</summary>

- _static-table_: kube-proxy IPVS mode deprecated — effective in Kubernetes 1.35 (KEP-5495)

</details>

### ⚪ Endpoints API deprecated in favour of EndpointSlices

`info` · `breaking-change` · origin: `deterministic` · effective in **1.33**

From 1.33 the core v1 Endpoints API is formally deprecated: the apiserver emits deprecation warnings when clients read or write Endpoints. The type is not scheduled for removal, but scripts, controllers, and integrations reading Endpoints directly should move to EndpointSlices (stable since 1.21, and the only API with dual-stack and topology data).

**Remediation:** Migrate controllers/scripts that consume Endpoints to the discovery.k8s.io/v1 EndpointSlices API.

<details><summary>Evidence</summary>

- _static-table_: Endpoints API deprecated in favour of EndpointSlices — effective in Kubernetes 1.33

</details>

## Compatibility Matrix

Target: Kubernetes 1.36

| Component | Installed | Status | Min required | Notes |
|---|---|---|---|---|
| CoreDNS | 1.11.3 | unknown | — | No static support matrix tracked; verify against upstream docs. |
| kubelet | 1.32 | incompatible | 1.33 | Kubelet 1.32 will violate skew policy at control plane 1.36. |
| containerd | 1.x | incompatible | 2.x | Containerd 1.x support removed in Kubernetes 1.36. |

## Upgrade Plan

**Strategy:** Sequential minor version upgrades with validation gates at each step.

### Preparation

1. **Upgrade containerd to 2.x** _(~30 min, disruption: minimal)_
   Upgrade containerd to version 2.x to ensure compatibility with Kubernetes 1.36.
   - `apt-get update && apt-get install -y containerd=2.x`

### Control Plane

2. **Upgrade control plane to 1.33** _(~60 min, disruption: moderate)_
   Upgrade the control plane components to Kubernetes version 1.33.
   - `kind create cluster --image kindest/node:v1.33.0`

### Validation

3. **Validate cluster functionality at 1.33** _(~30 min, disruption: none)_
   Run tests to ensure the cluster is functioning correctly after the upgrade to 1.33.
   - `kubectl get nodes`
   - `kubectl get pods --all-namespaces`

### Control Plane

4. **Upgrade control plane to 1.34** _(~60 min, disruption: moderate)_
   Upgrade the control plane components to Kubernetes version 1.34.
   - `kind create cluster --image kindest/node:v1.34.0`

### Validation

5. **Validate cluster functionality at 1.34** _(~30 min, disruption: none)_
   Run tests to ensure the cluster is functioning correctly after the upgrade to 1.34.
   - `kubectl get nodes`
   - `kubectl get pods --all-namespaces`

### Control Plane

6. **Upgrade control plane to 1.35** _(~60 min, disruption: moderate)_
   Upgrade the control plane components to Kubernetes version 1.35.
   - `kind create cluster --image kindest/node:v1.35.0`

### Validation

7. **Validate cluster functionality at 1.35** _(~30 min, disruption: none)_
   Run tests to ensure the cluster is functioning correctly after the upgrade to 1.35.
   - `kubectl get nodes`
   - `kubectl get pods --all-namespaces`

### Control Plane

8. **Upgrade control plane to 1.36** _(~60 min, disruption: moderate)_
   Upgrade the control plane components to Kubernetes version 1.36.
   - `kind create cluster --image kindest/node:v1.36.0`

### Validation

9. **Final validation at 1.36** _(~30 min, disruption: none)_
   Run final tests to ensure the cluster is functioning correctly after the upgrade to 1.36.
   - `kubectl get nodes`
   - `kubectl get pods --all-namespaces`

## Rollback Plan

1. **Rollback to previous version** — If validation fails, rollback to the previous stable version.
   - `kind create cluster --image kindest/node:v1.35.0`

## Pre-Upgrade Checklist

- [ ] All removed-API findings remediated and verified with pluto/kubent
- [ ] Webhook operators (cert-manager, policy engines) at target-compatible versions
- [ ] etcd snapshot / velero backup completed and restore-tested
- [ ] PDBs reviewed; no maxUnavailable: 0 deadlocks
- [ ] Maintenance window and rollback decision criteria agreed
- [ ] Monitoring dashboards and alerts green before starting
- [ ] OS packages for target kubeadm/kubelet staged on all nodes
- [ ] Ensure containerd is upgraded to 2.x
- [ ] Backup all critical data and configurations

## Post-Upgrade Validation

- [ ] All nodes Ready at target kubelet version (kubectl get nodes)
- [ ] No pods in CrashLoopBackOff/ImagePullBackOff introduced by the upgrade
- [ ] Admission webhooks answering (create a dry-run object through each)
- [ ] DNS resolution healthy (CoreDNS metrics/log check)
- [ ] Ingress traffic serving; certificate issuance verified if cert-manager present
- [ ] Autoscaling verified (scale a canary deployment; node provisioning if Karpenter/CA)
- [ ] Application golden-path checks pass; error budgets not consumed
- [ ] Deprecation warnings in audit logs reviewed for the *next* upgrade
- [ ] Verify all nodes are Ready
- [ ] Check all workloads are running as expected

## Downtime & Disruption Estimate

- **Control plane:** Control plane will be unavailable during each upgrade step.
- **Workloads:** Workloads may experience temporary disruption during node upgrades.
- **Estimated window:** ~240 minutes
- _Assumption: Each upgrade step completes within the estimated time._
- _Assumption: No unexpected issues arise during the upgrade process._

## Risk Narrative

The primary risk involves the kubelet failing to start on nodes using cgroup v1, as it is disabled by default from version 1.35 [DOC 7]. This could lead to nodes not becoming Ready post-upgrade, causing potential downtime. Another significant risk is the removal of containerd 1.x support in 1.36, necessitating an upgrade to containerd 2.x, which may involve configuration changes due to deprecated structures [DOC 7]. Additionally, the version skew policy violation for kubelet and kube-proxy at 1.36 could lead to unsupported configurations [DOC 1].

## Unknown Risks (honest gaps)

- No PodDisruptionBudget data — workload disruption during node drains is unmodelled.
- No load/canary testing performed — runtime behaviour under production traffic during the upgrade is unverified.

## Sources

- **[DOC 1]** [Kubernetes Version Skew Policy](https://kubernetes.io/releases/version-skew-policy/)
- **[DOC 7]** [Kubernetes 1.36 CHANGELOG](https://raw.githubusercontent.com/kubernetes/kubernetes/master/CHANGELOG/CHANGELOG-1.36.md) (Kubernetes 1.36)

## Evidence Appendix

- kubectl commands: 27/27 succeeded (critical: 9/9)
- Component versions resolved: 1/1
- KB chunks retrieved: 24 from 9 documents
- LLM: openai/gpt-4o · 11763+1633 tokens
- Narrative grounding: 33% of substantive sentences carry a [DOC n] citation

---
_Generated by k8s-upgrade-advisor. Deterministic findings are provable from cluster data and static lifecycle tables; LLM-origin content is labelled._