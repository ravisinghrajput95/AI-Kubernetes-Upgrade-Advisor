"""Regressions for the AI-stack improvements: reranking, context ordering,
grounding measurement, prompt efficiency, cost accounting."""

import json

from k8s_upgrade_advisor.analysis import run_deterministic_analysis
from k8s_upgrade_advisor.config import RetrievalSettings
from k8s_upgrade_advisor.knowledge.embeddings import HashingEmbedder
from k8s_upgrade_advisor.llm.advisor import _grounding_ratio, run_llm_analysis
from k8s_upgrade_advisor.llm.prompts import _compact, build_user_prompt
from k8s_upgrade_advisor.models import Citation, KubeVersion, LLMAnalysis
from k8s_upgrade_advisor.retrieval import HybridRetriever
from k8s_upgrade_advisor.retrieval.rerank import NullReranker, select_reranker

V = KubeVersion.parse


class BoostReranker:
    """Fake cross-encoder: boosts chunks containing a marker phrase."""

    name = "fake-boost"

    def __init__(self, marker: str) -> None:
        self.marker = marker
        self.calls = 0

    def scores(self, query, texts):
        self.calls += 1
        return [10.0 if self.marker in t else 1.0 for t in texts]


class TestReranking:
    def test_reranker_reorders_fused_candidates(self, kb_store):
        # Boost the istio doc for a query where fusion favours others.
        reranker = BoostReranker("Istio 1.20")
        retriever = HybridRetriever(
            kb_store, HashingEmbedder(), RetrievalSettings(top_k=2), reranker=reranker
        )
        bundle = retriever.retrieve(
            ["kubernetes compatibility supported releases upgrade"],
            allowed_versions={"1.28", "1.29"},
            installed_components={"istio", "cert-manager"},
        )
        assert reranker.calls == 1
        assert any(e.chunk.component == "istio" for e in bundle.entries)
        # Reranked winner is presented first.
        assert bundle.entries[0].chunk.component == "istio"

    def test_null_reranker_is_noop(self, kb_store):
        settings = RetrievalSettings(top_k=3)
        base = HybridRetriever(kb_store, HashingEmbedder(), settings)
        nulled = HybridRetriever(kb_store, HashingEmbedder(), settings, reranker=NullReranker())
        args = (
            ["cert-manager kubernetes 1.29 compatibility"],
            {"1.28", "1.29"},
            {"cert-manager"},
        )
        assert [e.chunk.chunk_id for e in base.retrieve(*args).entries] == [
            e.chunk.chunk_id for e in nulled.retrieve(*args).entries
        ]

    def test_select_reranker_none(self):
        assert select_reranker("none").name == "none"

    def test_select_reranker_auto_degrades_without_dependency(self):
        # sentence-transformers is absent in the test environment.
        assert select_reranker("auto").name == "none"


class TestContextOrdering:
    def test_strongest_chunks_at_both_edges(self, kb_store):
        retriever = HybridRetriever(kb_store, HashingEmbedder(), RetrievalSettings(top_k=4))
        bundle = retriever.retrieve(
            [
                "flowcontrol FlowSchema removed",
                "cert-manager compatibility",
                "istio supported releases",
                "dockershim runtime",
            ],
            allowed_versions={"1.28", "1.29"},
            installed_components=set(),
        )
        if len(bundle.entries) >= 3:
            scores = [e.score for e in bundle.entries]
            # Lost-in-the-middle arrangement: the weakest selected chunk must
            # not sit at either edge.
            assert min(scores) not in (scores[0], scores[-1])


class TestGroundingRatio:
    def test_fully_cited_narrative(self):
        text = (
            "The flowcontrol API is removed in this window [DOC 1]. "
            "cert-manager requires an upgrade before the control plane hop [DOC 2]."
        )
        assert _grounding_ratio(text) == 1.0

    def test_uncited_narrative_is_zero(self):
        text = (
            "The cluster requires several component upgrades before proceeding. "
            "Node pools should be rolled after the control plane completes each hop."
        )
        assert _grounding_ratio(text) == 0.0

    def test_short_fragments_ignored(self):
        assert _grounding_ratio("Ok. Fine. Yes.") == 0.0

    def test_ratio_recorded_on_report(self, eks_snapshot):
        class Provider:
            name, model = "fake", "fake-1"

            def complete_json(self, system, user):
                return json.dumps(
                    {
                        "executive_summary": (
                            "The cert-manager release lags the target requirement [DOC 1]. "
                            "A staged upgrade path is mandatory because minors cannot be skipped."
                        ),
                        "citations_used": [1],
                    }
                )

        report = run_deterministic_analysis(eks_snapshot, V("1.26"), V("1.29"))
        merged = run_llm_analysis(
            report, "ctx", [Citation(ref=1, title="d", url="https://x")], Provider()
        )
        assert merged.llm.grounding_ratio == 0.5


class TestPromptEfficiency:
    def test_compact_strips_metadata_titles_keeps_title_property(self):
        compact = _compact(LLMAnalysis.model_json_schema())
        # Metadata titles are string-valued ("title": "Finding") — all gone.
        assert '"title": "' not in json.dumps(compact)
        # But Finding's *field* named title survives as a property.
        assert "title" in compact["$defs"]["Finding"]["properties"]
        assert "$defs" in compact and "required" in compact

    def test_compact_schema_saves_tokens(self):
        full = len(json.dumps(LLMAnalysis.model_json_schema()))
        compact = len(json.dumps(_compact(LLMAnalysis.model_json_schema())))
        assert compact < full * 0.9  # at least 10% smaller

    def test_prompt_uses_compact_schema(self, eks_snapshot):
        report = run_deterministic_analysis(eks_snapshot, V("1.26"), V("1.29"))
        prompt = build_user_prompt(report, "")
        assert '"title": "' not in prompt.split("Schema:")[1]


class TestCostAccounting:
    def test_cost_flows_from_provider_usage(self, eks_snapshot):
        class Provider:
            name, model = "fake", "fake-1"
            last_usage = {"prompt_tokens": 10_000, "completion_tokens": 2_000, "cost_usd": 0.055}

            def complete_json(self, system, user):
                return json.dumps({"executive_summary": "ok", "citations_used": []})

        report = run_deterministic_analysis(eks_snapshot, V("1.26"), V("1.29"))
        merged = run_llm_analysis(report, "", [], Provider())
        assert merged.llm.estimated_cost_usd == 0.055

    def test_zero_prices_mean_zero_cost(self, eks_snapshot):
        class Provider:
            name, model = "fake", "fake-1"
            last_usage = {"prompt_tokens": 10_000, "completion_tokens": 2_000}

            def complete_json(self, system, user):
                return json.dumps({"executive_summary": "ok", "citations_used": []})

        report = run_deterministic_analysis(eks_snapshot, V("1.26"), V("1.29"))
        merged = run_llm_analysis(report, "", [], Provider())
        assert merged.llm.estimated_cost_usd == 0.0
