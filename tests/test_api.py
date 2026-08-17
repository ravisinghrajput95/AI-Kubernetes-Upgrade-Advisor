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


class TestApiKeyAuth:
    @pytest.fixture
    def authed_client(self, settings):
        settings.server.api_key = "s3cret-key"
        return TestClient(create_app(settings))

    def test_open_by_default(self, client, assess_body):
        # No api_key configured → /api/* reachable without credentials.
        assert client.post("/api/v1/assessments", json=assess_body).status_code == 200

    def test_missing_key_rejected(self, authed_client, assess_body):
        resp = authed_client.post("/api/v1/assessments", json=assess_body)
        assert resp.status_code == 401
        assert resp.headers.get("WWW-Authenticate") == "Bearer"

    def test_wrong_key_rejected(self, authed_client, assess_body):
        resp = authed_client.post(
            "/api/v1/assessments", json=assess_body, headers={"X-API-Key": "nope"}
        )
        assert resp.status_code == 401

    def test_x_api_key_accepted(self, authed_client, assess_body):
        resp = authed_client.post(
            "/api/v1/assessments", json=assess_body, headers={"X-API-Key": "s3cret-key"}
        )
        assert resp.status_code == 200, resp.text

    def test_bearer_token_accepted(self, authed_client, assess_body):
        resp = authed_client.post(
            "/api/v1/assessments",
            json=assess_body,
            headers={"Authorization": "Bearer s3cret-key"},
        )
        assert resp.status_code == 200, resp.text

    def test_health_and_metrics_stay_open(self, authed_client):
        # Probes and scrapers must not need the key.
        assert authed_client.get("/healthz").status_code == 200
        assert authed_client.get("/readyz").status_code == 200
        assert authed_client.get("/metrics").status_code == 200


class TestCors:
    def test_no_cors_headers_by_default(self, client):
        resp = client.get("/healthz", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in {k.lower() for k in resp.headers}

    def test_cors_allows_configured_origin(self, settings):
        settings.server.cors_allow_origins = ["https://ui.example"]
        c = TestClient(create_app(settings))
        resp = c.get("/healthz", headers={"Origin": "https://ui.example"})
        assert resp.headers.get("access-control-allow-origin") == "https://ui.example"
