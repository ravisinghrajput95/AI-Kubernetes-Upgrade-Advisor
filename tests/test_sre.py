"""SRE-readiness regressions: idempotency, rate limiting, disk-backed
listing (multi-replica story), request-ID correlation."""

import json

import pytest
from fastapi.testclient import TestClient

from k8s_upgrade_advisor.api.app import _IdempotencyCache, _TokenBucket, create_app

pytestmark = pytest.mark.integration


def _body(eks_snapshot, dry_run=True):
    return {
        "source_version": "1.26",
        "target_version": "1.29",
        "dry_run": dry_run,
        "snapshot": json.loads(eks_snapshot.model_dump_json()),
    }


class TestIdempotency:
    def test_identical_submission_returns_cached_report(self, settings, eks_snapshot):
        client = TestClient(create_app(settings))
        first = client.post("/api/v1/assessments", json=_body(eks_snapshot)).json()
        second = client.post("/api/v1/assessments", json=_body(eks_snapshot)).json()
        assert second["id"] == first["id"]

    def test_different_target_is_not_cached(self, settings, eks_snapshot):
        client = TestClient(create_app(settings))
        first = client.post("/api/v1/assessments", json=_body(eks_snapshot)).json()
        other = _body(eks_snapshot)
        other["target_version"] = "1.28"
        second = client.post("/api/v1/assessments", json=other).json()
        assert second["id"] != first["id"]

    def test_ttl_zero_disables(self, settings, eks_snapshot):
        settings.server.idempotency_ttl_seconds = 0
        client = TestClient(create_app(settings))
        first = client.post("/api/v1/assessments", json=_body(eks_snapshot)).json()
        second = client.post("/api/v1/assessments", json=_body(eks_snapshot)).json()
        assert second["id"] != first["id"]

    def test_cache_expires(self):
        now = [0.0]
        cache = _IdempotencyCache(ttl_seconds=10, clock=lambda: now[0])
        cache.put("k", "assess-1")
        assert cache.get("k") == "assess-1"
        now[0] = 11.0
        assert cache.get("k") is None


class TestRateLimiting:
    def test_bucket_exhaustion_returns_429(self, settings, eks_snapshot):
        app = create_app(settings)
        client = TestClient(app)
        app.state.rate_bucket.tokens = 0.0  # drain deterministically
        response = client.post("/api/v1/assessments", json=_body(eks_snapshot))
        assert response.status_code == 429
        assert response.headers.get("retry-after") == "10"

    def test_bucket_refills_over_time(self):
        now = [0.0]
        bucket = _TokenBucket(per_minute=60, clock=lambda: now[0])  # 1 token/s
        bucket.tokens = 0.0
        assert not bucket.try_acquire()
        now[0] = 2.0
        assert bucket.try_acquire()

    def test_zero_rate_disables_limiter(self, settings, eks_snapshot):
        settings.server.rate_limit_per_minute = 0
        app = create_app(settings)
        assert app.state.rate_bucket is None
        client = TestClient(app)
        assert client.post("/api/v1/assessments", json=_body(eks_snapshot)).status_code == 200

    def test_cached_hits_bypass_rate_limit(self, settings, eks_snapshot):
        app = create_app(settings)
        client = TestClient(app)
        first = client.post("/api/v1/assessments", json=_body(eks_snapshot)).json()
        app.state.rate_bucket.tokens = 0.0
        # Identical resubmission is served from cache even while rate-limited.
        second = client.post("/api/v1/assessments", json=_body(eks_snapshot))
        assert second.status_code == 200 and second.json()["id"] == first["id"]


class TestMultiReplicaListing:
    def test_listing_reads_reports_written_by_another_replica(self, settings, eks_snapshot):
        writer = TestClient(create_app(settings))
        report = writer.post("/api/v1/assessments", json=_body(eks_snapshot)).json()
        # Second app instance = second replica sharing the reports volume.
        reader = TestClient(create_app(settings))
        listed = reader.get("/api/v1/assessments").json()
        assert [row["id"] for row in listed] == [report["id"]]
        assert listed[0]["verdict"] == report["readiness"]["verdict"]


class TestRequestCorrelation:
    def test_response_carries_request_id(self, settings):
        client = TestClient(create_app(settings))
        response = client.get("/readyz")
        assert len(response.headers.get("x-request-id", "")) >= 8

    def test_client_supplied_request_id_is_echoed(self, settings):
        client = TestClient(create_app(settings))
        response = client.get("/readyz", headers={"X-Request-ID": "trace-me-123"})
        assert response.headers["x-request-id"] == "trace-me-123"
