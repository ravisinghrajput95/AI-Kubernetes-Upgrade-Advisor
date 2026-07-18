from k8s_upgrade_advisor.analysis import run_deterministic_analysis
from k8s_upgrade_advisor.models import KubeVersion
from k8s_upgrade_advisor.reporting import render_html, render_json, render_markdown

V = KubeVersion.parse


def make_report(eks_snapshot):
    return run_deterministic_analysis(eks_snapshot, V("1.26"), V("1.29"))


class TestMarkdown:
    def test_sections_present(self, eks_snapshot):
        md = render_markdown(make_report(eks_snapshot))
        for heading in (
            "# Kubernetes Upgrade Assessment",
            "## Cluster Profile",
            "## Findings",
            "## Compatibility Matrix",
            "## Upgrade Plan",
            "## Rollback Plan",
            "## Pre-Upgrade Checklist",
            "## Post-Upgrade Validation",
            "## Downtime",
            "## Unknown Risks",
            "## Evidence Appendix",
        ):
            assert heading in md, f"missing {heading}"

    def test_verdict_and_scores_rendered(self, eks_snapshot):
        report = make_report(eks_snapshot)
        md = render_markdown(report)
        assert f"Readiness **{report.readiness.score}/100**" in md
        assert "NOT READY" in md

    def test_checklists_are_checkboxes(self, eks_snapshot):
        assert "- [ ] " in render_markdown(make_report(eks_snapshot))


class TestHtml:
    def test_valid_skeleton_and_escaping(self, eks_snapshot):
        report = make_report(eks_snapshot)
        report.executive_summary = "<script>alert(1)</script> & summary"
        html = render_html(report)
        assert html.startswith("<!doctype html>")
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html

    def test_findings_and_matrix_rendered(self, eks_snapshot):
        html = render_html(make_report(eks_snapshot))
        assert "Compatibility Matrix" in html
        assert "cert-manager" in html
        assert "class='badge risk'" in html  # NOT READY badge


class TestJson:
    def test_roundtrip(self, eks_snapshot):
        from k8s_upgrade_advisor.models import AssessmentReport

        report = make_report(eks_snapshot)
        restored = AssessmentReport.model_validate_json(render_json(report))
        assert restored.id == report.id
        assert restored.readiness.score == report.readiness.score
        assert len(restored.findings) == len(report.findings)
