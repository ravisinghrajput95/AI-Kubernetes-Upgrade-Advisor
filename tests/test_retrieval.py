from k8s_upgrade_advisor.config import RetrievalSettings
from k8s_upgrade_advisor.knowledge.chunker import chunk_document
from k8s_upgrade_advisor.knowledge.embeddings import HashingEmbedder
from k8s_upgrade_advisor.knowledge.fetcher import RawDocument
from k8s_upgrade_advisor.retrieval import BM25, HybridRetriever


class TestChunker:
    def test_heading_paths_and_min_size(self):
        doc = RawDocument(
            "d",
            "Doc",
            "https://x",
            "release-notes",
            None,
            "1.29",
            "# Top\n\nshort\n\n## Deprecations\n\n" + "API removal detail. " * 20,
        )
        chunks = chunk_document(doc, 400, 50)
        assert all(len(c.text) >= 80 for c in chunks)
        assert any("Deprecations" in c.section for c in chunks)

    def test_oversize_section_is_split_with_overlap(self):
        doc = RawDocument(
            "d", "Doc", "https://x", "release-notes", None, None, "## Big\n\n" + ("word " * 600)
        )
        chunks = chunk_document(doc, 500, 100)
        assert len(chunks) > 1
        assert all(len(c.text) <= 500 for c in chunks)

    def test_chunk_ids_stable(self):
        doc = RawDocument(
            "d", "Doc", "https://x", "release-notes", None, None, "## S\n\n" + "content " * 40
        )
        ids1 = [c.chunk_id for c in chunk_document(doc)]
        ids2 = [c.chunk_id for c in chunk_document(doc)]
        assert ids1 == ids2


class TestBM25:
    def test_exact_token_ranking(self):
        docs = [
            "the flowcontrol.apiserver.k8s.io/v1beta2 API is no longer served",
            "general notes about scheduling and nodes",
            "cert-manager webhook configuration guidance",
        ]
        bm25 = BM25(docs)
        results = bm25.search("flowcontrol.apiserver.k8s.io/v1beta2 removed", k=3)
        assert results and results[0][0] == 0

    def test_no_match_returns_empty(self):
        assert BM25(["alpha beta", "gamma delta"]).search("zzzz", k=5) == []


class TestHybridRetriever:
    def _retriever(self, kb_store):
        return HybridRetriever(kb_store, HashingEmbedder(), RetrievalSettings(top_k=4))

    def test_version_filter_is_hard(self, kb_store):
        retriever = self._retriever(kb_store)
        bundle = retriever.retrieve(
            ["dockershim docker runtime removed kubelet"],  # aims straight at the 1.24 doc
            allowed_versions={"1.28", "1.29"},
            installed_components=set(),
        )
        assert all(e.chunk.k8s_version != "1.24" for e in bundle.entries)

    def test_relevant_docs_retrieved_with_citations(self, kb_store):
        retriever = self._retriever(kb_store)
        bundle = retriever.retrieve(
            [
                "flowcontrol v1beta2 FlowSchema removed 1.29",
                "cert-manager kubernetes 1.29 compatibility",
            ],
            allowed_versions={"1.28", "1.29"},
            installed_components={"cert-manager"},
        )
        titles = [e.chunk.title for e in bundle.entries]
        assert any("1.29 CHANGELOG" in t for t in titles)
        assert any("cert-manager" in t for t in titles)
        refs = [c.ref for c in bundle.citations]
        assert refs == sorted(refs) and refs[0] == 1
        assert "[DOC 1]" in bundle.context_text

    def test_uninstalled_component_docs_penalised(self, kb_store):
        retriever = self._retriever(kb_store)
        bundle = retriever.retrieve(
            ["istio kubernetes compatibility supported releases"],
            allowed_versions={"1.28", "1.29"},
            installed_components={"cert-manager"},  # istio NOT installed
        )
        if bundle.entries:  # penalty, not exclusion
            istio_rank = next((e.ref for e in bundle.entries if e.chunk.component == "istio"), None)
            cm_rank = next(
                (e.ref for e in bundle.entries if e.chunk.component == "cert-manager"), None
            )
            if istio_rank and cm_rank:
                assert cm_rank < istio_rank

    def test_context_respects_budget(self, kb_store):
        settings = RetrievalSettings(top_k=10, max_context_chars=4000)
        retriever = HybridRetriever(kb_store, HashingEmbedder(), settings)
        bundle = retriever.retrieve(
            ["kubernetes upgrade"], allowed_versions={"1.28", "1.29"}, installed_components=set()
        )
        assert len(bundle.context_text) <= 4000
