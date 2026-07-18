import pytest

from k8s_upgrade_advisor.errors import CircuitOpenError, KnowledgeBaseError
from k8s_upgrade_advisor.knowledge.embeddings import HashingEmbedder
from k8s_upgrade_advisor.knowledge.store import KnowledgeStore
from k8s_upgrade_advisor.resilience import CircuitBreaker, CircuitState, retry


class TestRetry:
    def test_succeeds_after_transient_failures(self):
        attempts = []

        @retry(attempts=3, base_delay=0.01, sleep=lambda _s: None)
        def flaky():
            attempts.append(1)
            if len(attempts) < 3:
                raise ConnectionError("transient")
            return "ok"

        assert flaky() == "ok" and len(attempts) == 3

    def test_exhaustion_raises_last_error(self):
        @retry(attempts=2, base_delay=0.01, sleep=lambda _s: None)
        def always_fails():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            always_fails()

    def test_non_matching_exception_not_retried(self):
        calls = []

        @retry(attempts=3, retry_on=(ConnectionError,), sleep=lambda _s: None)
        def fails_differently():
            calls.append(1)
            raise KeyError("no retry")

        with pytest.raises(KeyError):
            fails_differently()
        assert len(calls) == 1


class TestCircuitBreaker:
    def _tripped(self, clock) -> CircuitBreaker:
        breaker = CircuitBreaker("test", failure_threshold=2, reset_timeout=10, clock=clock)
        for _ in range(2):
            with pytest.raises(RuntimeError):
                breaker.call(self._boom)
        return breaker

    @staticmethod
    def _boom():
        raise RuntimeError("dependency down")

    def test_opens_after_threshold_and_fails_fast(self):
        now = [0.0]
        breaker = self._tripped(lambda: now[0])
        assert breaker.state is CircuitState.OPEN
        with pytest.raises(CircuitOpenError):
            breaker.call(lambda: "never runs")

    def test_half_open_probe_closes_on_success(self):
        now = [0.0]
        breaker = self._tripped(lambda: now[0])
        now[0] = 11.0  # past reset_timeout
        assert breaker.call(lambda: "recovered") == "recovered"
        assert breaker.state is CircuitState.CLOSED

    def test_half_open_probe_reopens_on_failure(self):
        now = [0.0]
        breaker = self._tripped(lambda: now[0])
        now[0] = 11.0
        with pytest.raises(RuntimeError):
            breaker.call(self._boom)
        assert breaker.state is CircuitState.OPEN


class TestStoreIntegrity:
    def test_load_refuses_wrong_embedder(self, kb_store, tmp_path):
        with pytest.raises(KnowledgeBaseError, match="rebuild"):
            KnowledgeStore.load(tmp_path / "kb", expected_embedder="sentence-transformers/other")

    def test_load_roundtrip(self, kb_store, tmp_path):
        loaded = KnowledgeStore.load(tmp_path / "kb", expected_embedder=HashingEmbedder().name)
        assert loaded.manifest.chunk_count == len(loaded.chunks)
        assert loaded.vectors.shape[0] == len(loaded.chunks)

    def test_missing_kb_raises(self, tmp_path):
        with pytest.raises(KnowledgeBaseError, match="collect"):
            KnowledgeStore.load(tmp_path / "nowhere")

    def test_dense_search_returns_ranked_hits(self, kb_store):
        embedder = HashingEmbedder()
        query = embedder.encode(["flowcontrol FlowSchema v1beta2 no longer served"])[0]
        hits = kb_store.dense_search(query, k=3)
        assert hits and hits[0][1] >= hits[-1][1]
