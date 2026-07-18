import pytest

from k8s_upgrade_advisor.errors import ConfigurationError
from k8s_upgrade_advisor.models.versions import KubeVersion, validate_upgrade_pair


class TestParse:
    @pytest.mark.parametrize(
        "raw,major,minor,patch",
        [
            ("1.29", 1, 29, None),
            ("v1.29", 1, 29, None),
            ("v1.28.9-eks-036c24b", 1, 28, 9),
            ("v1.29.5-gke.1091002", 1, 29, 5),
            ("1.28+", 1, 28, None),
            ("v1.30.1+rke2r1", 1, 30, 1),
        ],
    )
    def test_valid(self, raw, major, minor, patch):
        v = KubeVersion.parse(raw)
        assert (v.major, v.minor, v.patch) == (major, minor, patch)

    @pytest.mark.parametrize("raw", ["", "latest", "1", "one.two", "1.x"])
    def test_invalid(self, raw):
        with pytest.raises(ConfigurationError):
            KubeVersion.parse(raw)


class TestOrdering:
    def test_compare(self):
        assert KubeVersion.parse("1.28") < KubeVersion.parse("1.29")
        assert KubeVersion.parse("1.29.1") < KubeVersion.parse("1.29.2")
        assert KubeVersion.parse("v1.30") == KubeVersion.parse("1.30")

    def test_minors_until(self):
        path = KubeVersion.parse("1.27").minors_until(KubeVersion.parse("1.30"))
        assert [str(v) for v in path] == ["1.28", "1.29", "1.30"]

    def test_single_hop(self):
        path = KubeVersion.parse("1.28").minors_until(KubeVersion.parse("1.29"))
        assert [str(v) for v in path] == ["1.29"]


class TestValidatePair:
    def test_ok(self):
        src, tgt = validate_upgrade_pair("1.27", "v1.29")
        assert src.minor_str == "1.27" and tgt.minor_str == "1.29"

    def test_downgrade_rejected(self):
        with pytest.raises(ConfigurationError):
            validate_upgrade_pair("1.29", "1.27")

    def test_same_version_rejected(self):
        with pytest.raises(ConfigurationError):
            validate_upgrade_pair("1.29", "1.29.3")

    def test_cross_major_rejected(self):
        with pytest.raises(ConfigurationError):
            validate_upgrade_pair("1.29", "2.0")
