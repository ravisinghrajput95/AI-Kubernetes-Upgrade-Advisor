from k8s_upgrade_advisor.analysis.profile import build_profile, detect_flavour
from k8s_upgrade_advisor.models import ClusterFlavour


class TestFlavourDetection:
    def test_eks_from_git_version(self, eks_snapshot):
        flavour, evidence = detect_flavour(eks_snapshot)
        assert flavour is ClusterFlavour.EKS
        assert any("eks" in e.lower() for e in evidence)

    def test_gke_from_git_version(self, gke_snapshot):
        assert detect_flavour(gke_snapshot)[0] is ClusterFlavour.GKE

    def test_kind_from_node_name(self, kind_snapshot):
        assert detect_flavour(kind_snapshot)[0] is ClusterFlavour.KIND

    def test_openshift_from_api_groups(self, openshift_snapshot):
        assert detect_flavour(openshift_snapshot)[0] is ClusterFlavour.OPENSHIFT


class TestProfile:
    def test_eks_profile(self, eks_snapshot):
        profile = build_profile(eks_snapshot)
        assert profile.node_count == 3
        assert profile.nodes[0].node_pool == "workers-a"
        assert profile.nodes[0].kubelet_version.startswith("v1.26")
        assert "etcd" in profile.provider_managed
        assert profile.workloads.deployments == 3

    def test_gke_pool_label(self, gke_snapshot):
        profile = build_profile(gke_snapshot)
        assert profile.nodes[0].node_pool == "default-pool"

    def test_flavour_properties(self):
        assert ClusterFlavour.EKS.is_managed
        assert not ClusterFlavour.KUBEADM.is_managed
        assert ClusterFlavour.KIND.is_local_dev
