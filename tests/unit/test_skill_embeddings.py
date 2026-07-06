"""Unit tests for embedding-based skill matching (RAG routing Tier 1).

Covers:
- Hash-based default embedder (no external deps)
- Cosine similarity
- Embedding cache (computed once per skill)
- SkillRegistry.match_trigger embedding fallback when substring misses
- Substring fast-path still works unchanged
- Similarity threshold filtering
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from general_ludd.skills.embeddings import (
    HashEmbedder,
    SkillEmbedder,
    cosine_similarity,
)
from general_ludd.skills.registry import SkillRegistry
from general_ludd.skills.skill import Skill


class TestHashEmbedder:
    def test_returns_vector_of_requested_dimension(self):
        emb = HashEmbedder(dim=64)
        vec = emb.embed("hello world foo bar")
        assert len(vec) == 64

    def test_same_text_produces_same_vector(self):
        emb = HashEmbedder(dim=32)
        assert emb.embed("race condition bug") == emb.embed("race condition bug")

    def test_different_text_produces_different_vectors(self):
        emb = HashEmbedder(dim=128)
        a = emb.embed("deploy to production kubernetes")
        b = emb.embed("refactor python type hints")
        assert a != b

    def test_zero_vector_for_empty_text(self):
        emb = HashEmbedder(dim=16)
        vec = emb.embed("")
        assert all(x == 0.0 for x in vec)

    def test_shared_tokens_increase_similarity(self):
        emb = HashEmbedder(dim=256)
        # Overlapping vocabulary should be more similar than disjoint
        similar_a = emb.embed("fix race condition in concurrency code")
        similar_b = emb.embed("debug concurrency race condition bug")
        disjoint = emb.embed("deploy helm chart to kubernetes cluster")
        sim_close = cosine_similarity(similar_a, similar_b)
        sim_far = cosine_similarity(similar_a, disjoint)
        assert sim_close > sim_far


class TestCosineSimilarity:
    def test_identical_vectors_are_one(self):
        v = [1.0, 2.0, 3.0]
        assert cosine_similarity(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors_are_zero(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert cosine_similarity(a, b) == pytest.approx(0.0)

    def test_zero_vector_returns_zero(self):
        assert cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0

    def test_symmetric(self):
        a = [1.0, 2.0, 3.0, 0.5]
        b = [0.5, 1.5, 2.0, 1.0]
        assert cosine_similarity(a, b) == pytest.approx(cosine_similarity(b, a))

    def test_different_length_vectors_raise(self):
        with pytest.raises(ValueError):
            cosine_similarity([1.0, 2.0], [1.0])


class TestSkillEmbedder:
    def test_embeds_skill_description(self):
        se = SkillEmbedder(embedder=HashEmbedder(dim=64))
        skill = Skill(name="x", description="review pull request code")
        vec = se.embed_skill(skill)
        assert len(vec) == 64

    def test_cache_returns_same_vector_object(self):
        se = SkillEmbedder(embedder=HashEmbedder(dim=32))
        skill = Skill(name="x", description="cached description")
        v1 = se.embed_skill(skill)
        v2 = se.embed_skill(skill)
        assert v1 is v2  # cached, not recomputed

    def test_embed_query_uses_embedder(self):
        se = SkillEmbedder(embedder=HashEmbedder(dim=16))
        vec = se.embed_query("some query text")
        assert len(vec) == 16

    def test_default_uses_hash_embedder(self):
        se = SkillEmbedder()
        # No API key → hash embedder is the backend
        assert isinstance(se._embedder, HashEmbedder)

    def test_skill_with_empty_description_embeds_to_zero(self):
        se = SkillEmbedder(embedder=HashEmbedder(dim=16))
        skill = Skill(name="x", description="")
        vec = se.embed_skill(skill)
        assert all(x == 0.0 for x in vec)

    def test_falls_back_to_hash_when_openai_key_missing(self):
        with patch.dict("os.environ", {}, clear=True):
            se = SkillEmbedder(use_openai_if_available=True)
            assert isinstance(se._embedder, HashEmbedder)


class TestRegistryEmbeddingFallback:
    def _make_registry_with_skills(self) -> SkillRegistry:
        reg = SkillRegistry()
        reg.register(Skill(
            name="concurrency_debugger",
            description="Investigate race conditions, deadlocks, and concurrency bugs in threaded code.",
            trigger_patterns=["race condition"],
        ))
        reg.register(Skill(
            name="deployer",
            description="Ship helm charts to kubernetes production clusters.",
            trigger_patterns=["deploy"],
        ))
        reg.register(Skill(
            name="doc_writer",
            description="Generate API documentation and README files from source.",
            trigger_patterns=["write docs"],
        ))
        return reg

    def test_substring_fast_path_still_matches(self):
        reg = self._make_registry_with_skills()
        # Exact trigger substring present → fast path wins
        matches = reg.match_trigger("please help with this race condition")
        names = [m.name for m in matches]
        assert "concurrency_debugger" in names

    def test_embedding_fallback_matches_synonym(self):
        reg = self._make_registry_with_skills()
        # "deadlock" overlaps lexically with the concurrency skill description
        # (which contains "deadlocks") but is NOT a substring trigger pattern.
        # The hash embedder needs a lower threshold than the 0.7 default (which
        # is calibrated for dense OpenAI embeddings); 0.15 is realistic for
        # bag-of-words cosine on short queries against longer descriptions.
        matches = reg.match_trigger(
            "I have a deadlock between two threads",
            similarity_threshold=0.15,
        )
        names = [m.name for m in matches]
        assert "concurrency_debugger" in names
        assert "doc_writer" not in names

    def test_embedding_fallback_below_threshold_excluded(self):
        reg = self._make_registry_with_skills()
        # A query totally unrelated to any skill should return [] via embeddings
        matches = reg.match_trigger("zzzz unrelated quack quack noise words")
        assert matches == []

    def test_embedding_threshold_configurable(self):
        reg = self._make_registry_with_skills()
        # Very high threshold → fewer/no embedding matches
        high = reg.match_trigger("deadlock thread blocking", similarity_threshold=0.99)
        # Very low threshold → at least one match from embeddings
        low = reg.match_trigger("deadlock thread blocking", similarity_threshold=0.01)
        assert len(low) >= len(high)

    def test_substring_match_takes_precedence_over_embedding(self):
        reg = self._make_registry_with_skills()
        # "deploy" is an exact trigger; ensure we get the deployer back even
        # though the rest of the query might also semantically match another skill.
        matches = reg.match_trigger("deploy the new helm chart")
        names = [m.name for m in matches]
        assert "deployer" in names

    def test_embedding_does_not_duplicate_substring_matches(self):
        reg = self._make_registry_with_skills()
        # If substring already matched, embedding fallback should not re-add it.
        matches = reg.match_trigger("race condition in my threads")
        names = [m.name for m in matches]
        assert names.count("concurrency_debugger") == 1

    def test_project_skills_included_in_embedding_fallback(self):
        reg = SkillRegistry()
        reg.register(Skill(
            name="proj_concurrency",
            description="Debug race conditions, deadlocks, and concurrency issues.",
            trigger_patterns=["proj race"],
        ), project_id="p1")
        # Lower threshold for hash-embedder bag-of-words (see note above).
        matches = reg.match_trigger(
            "deadlock between threads",
            project_id="p1",
            similarity_threshold=0.15,
        )
        names = [m.name for m in matches]
        assert "proj_concurrency" in names

    def test_no_skills_returns_empty(self):
        reg = SkillRegistry()
        assert reg.match_trigger("anything") == []


class TestOpenAIEmbedderOption:
    def test_openai_embedder_used_when_key_present(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "fake-key"}, clear=True):
            se = SkillEmbedder(use_openai_if_available=True)
            # When key is available, the embedder should NOT be the hash fallback.
            # (It may still be hash if the openai package is missing, so we only
            # assert non-hash-when-openai-importable.)
            try:
                import openai  # noqa: F401  import probe — checks if openai is installed
                assert not isinstance(se._embedder, HashEmbedder)
            except ImportError:
                assert isinstance(se._embedder, HashEmbedder)

    def test_skill_embedder_accepts_custom_embedder(self):
        custom = HashEmbedder(dim=8)
        se = SkillEmbedder(embedder=custom)
        assert se._embedder is custom
        vec = se.embed_query("foo")
        assert len(vec) == 8
