import json

import pytest
from fastapi.testclient import TestClient

from k8s_upgrade_advisor.api.app import create_app

pytestmark = pytest.mark.integration


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings))


@pytest.fixture
def assess_body(eks_snapshot):
    return {
        "source_version": "1.26",
        "target_version": "1.29",
        "snapshot": json.loads(eks_snapshot.model_dump_json()),
        "dry_run": True,
    }


class TestHealth:
    def test_healthz(self, client):
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_readyz_reports_missing_kb(self, client):
        body = client.get("/readyz").json()
        assert body["status"] == "ok" and body["kb_loaded"] is False

    def test_metrics_exposition(self, client):
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "advisor_assessments_total" in response.text


class TestAssessments:
    def test_create_and_fetch(self, client, assess_body):
        created = client.post("/api/v1/assessments", json=assess_body)
        assert created.status_code == 200, created.text
        report = created.json()
        assert report["readiness"]["verdict"] == "not-ready"
        assert report["llm"]["dry_run"] is True

        listed = client.get("/api/v1/assessments").json()
        assert listed[0]["id"] == report["id"]
        assert listed[0]["blocking"] == 0

        html = client.get(f"/api/v1/assessments/{report['id']}/html")
        assert html.status_code == 200 and html.text.startswith("<!doctype html>")

        markdown = client.get(f"/api/v1/assessments/{report['id']}/markdown")
        assert "# Kubernetes Upgrade Assessment" in markdown.text

    def test_bad_versions_rejected(self, client, assess_body):
        assess_body["target_version"] = "1.20"  # downgrade
        response = client.post("/api/v1/assessments", json=assess_body)
        assert response.status_code == 422

    def test_missing_snapshot_rejected(self, client):
        response = client.post(
            "/api/v1/assessments", json={"source_version": "1.26", "target_version": "1.29"}
        )
        assert response.status_code == 422

    def test_unknown_id_404(self, client):
        assert client.get("/api/v1/assessments/nope").status_code == 404

    def test_reports_persisted_to_disk(self, client, assess_body, settings):
        report = client.post("/api/v1/assessments", json=assess_body).json()
        written = list(settings.paths.reports_dir.glob(f"{report['id']}.*"))
        assert {p.suffix for p in written} == {".md", ".html", ".json"}
