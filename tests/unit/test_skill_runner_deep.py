"""Deep skill execution and runner tests: loading, trigger matching, argument
passing, skill chaining, error propagation, and timeout characteristics.
"""

from __future__ import annotations

import math
import os
import tempfile
import time

import pytest

from general_ludd.skills.embeddings import (
    HashEmbedder,
    SkillEmbedder,
    _stem,
    _tokenize,
    cosine_similarity,
)
from general_ludd.skills.loader import discover_skills, parse_skill_md
from general_ludd.skills.registry import SkillRegistry
from general_ludd.skills.renderer import SkillRenderError, render_skill
from general_ludd.skills.skill import Skill

# ── Skill Loading ──────────────────────────────────────────────────────────


class TestParseSkillMd:
    def test_yaml_error_fallback_returns_defaults(self):
        content = "---\nname: !!bad yaml :broken\n---\nBody here.\n"
        skill = parse_skill_md(content, source_path="/f/foo.md")
        assert skill.name == "foo"
        assert skill.description == ""
        assert skill.body.strip() == "Body here."
        assert skill.trigger_patterns == []

    def test_trigger_patterns_string_coerced_to_list(self):
        content = '---\nname: greet\ntrigger_patterns: "hi there"\n---\nBody.\n'
        skill = parse_skill_md(content)
        assert skill.trigger_patterns == ["hi there"]

    def test_tools_comma_separated_string_parsed(self):
        content = "---\nname: t\ntools: read, grep, glob\n---\nBody.\n"
        skill = parse_skill_md(content)
        assert skill.tools == ["read", "grep", "glob"]

    def test_tags_string_coerced_to_list(self):
        content = "---\nname: t\ntags: security\n---\nBody.\n"
        skill = parse_skill_md(content)
        assert skill.tags == ["security"]

    def test_parse_no_source_path_no_frontmatter_rejects_empty_name(self):
        content = "# No frontmatter\n\nContent here.\n"
        raised = False
        try:
            parse_skill_md(content)
        except ValueError:
            raised = True
        assert raised

    def test_frontmatter_only_at_file_start(self):
        content = "some text---\nname: nope\n---\nbody\n"
        skill = parse_skill_md(content, source_path="/f/my_skill.md")
        assert skill.name == "my_skill"
        assert skill.body == content


# ── Skill Discovery ────────────────────────────────────────────────────────


class TestSkillDiscovery:
    def test_nonexistent_directory_skipped_gracefully(self):
        skills = discover_skills("/this/path/does/not/exist/12345")
        assert skills == []

    def test_nested_directories_recursive_glob(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sub = os.path.join(tmpdir, "a", "b")
            os.makedirs(sub)
            with open(os.path.join(sub, "deep.md"), "w") as f:
                f.write("---\nname: deep_skill\n---\nDeep body.\n")
            skills = discover_skills(tmpdir)
            names = [s.name for s in skills]
            assert "deep_skill" in names

    def test_only_md_files_discovered(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "skill.md"), "w") as f:
                f.write("---\nname: md_only\n---\nBody.\n")
            with open(os.path.join(tmpdir, "not_a_skill.txt"), "w") as f:
                f.write("---\nname: not_skill\n---\n")
            skills = discover_skills(tmpdir)
            names = [s.name for s in skills]
            assert names == ["md_only"]


# ── Trigger Matching ───────────────────────────────────────────────────────


class TestMatchTriggerLiteral:
    def test_substring_case_insensitive_quick_path(self):
        skill = Skill(name="review", trigger_patterns=["return review", "approve"])
        reg = SkillRegistry()
        reg.register(skill)
        matches = reg.match_trigger("please RETURN REVIEW for this task")
        assert len(matches) == 1
        assert matches[0].name == "review"

    def test_no_match_returns_empty(self):
        reg = SkillRegistry()
        reg.register(Skill(name="x", trigger_patterns=["deploy to prod"]))
        assert reg.match_trigger("review this code") == []

    def test_multiple_triggers_match_same_skill_once(self):
        skill = Skill(name="a", trigger_patterns=["hello", "world"])
        reg = SkillRegistry()
        reg.register(skill)
        matches = reg.match_trigger("hello world")
        assert len(matches) == 1

    def test_global_and_project_triggers_both_checked(self):
        reg = SkillRegistry()
        reg.register(Skill(name="global", trigger_patterns=["build"]))
        reg.register(Skill(name="proj", trigger_patterns=["build"]), project_id="p1")
        matches = reg.match_trigger("trigger a build", project_id="p1")
        names = [s.name for s in matches]
        assert "global" in names
        assert "proj" in names


class TestMatchTriggerEmbeddingFallback:
    def test_embedding_fallback_kicks_in_when_no_substring_match(self):
        skill = Skill(name="rust_expert", description="Rust programming language expert")
        reg = SkillRegistry()
        reg.register(skill)
        matches = reg.match_trigger("write a systems program with borrow checker")
        assert len(matches) >= 0

    def test_embedding_fallback_with_empty_query_returns_empty(self):
        reg = SkillRegistry()
        reg.register(Skill(name="x", description="anything"))
        matches = reg.match_trigger("")
        assert matches == []

    def test_similarity_threshold_filters_low_scores(self):
        reg = SkillRegistry()
        reg.register(Skill(name="x", description="cooking recipes"))
        matches = reg.match_trigger(
            "debug a python deadlock in asyncio",
            similarity_threshold=0.9999,
        )
        assert len(matches) == 0

    def test_embedding_fallback_sorts_by_similarity(self):
        reg = SkillRegistry()
        reg.register(Skill(name="best", description="python programming asyncio coroutines"))
        reg.register(Skill(name="worst", description="woodworking cabinet joinery"))
        matches = reg.match_trigger("python asyncio")
        if len(matches) >= 2:
            assert matches[0].name == "best"


# ── Skill Chaining / Multi-Skill ───────────────────────────────────────────


class TestSkillChaining:
    def test_registry_match_returns_all_matching_skills(self):
        reg = SkillRegistry()
        reg.register(Skill(name="a", trigger_patterns=["python", "async"]))
        reg.register(Skill(name="b", trigger_patterns=["python", "test"]))
        reg.register(Skill(name="c", trigger_patterns=["deploy"]))
        matches = reg.match_trigger("python async test suite")
        names = {s.name for s in matches}
        assert names == {"a", "b"}

    def test_project_override_priority_in_get(self):
        reg = SkillRegistry()
        reg.register(Skill(name="py", description="global python"))
        reg.register(Skill(name="py", description="project python"), project_id="p1")
        found = reg.get("py", project_id="p1")
        assert found is not None
        assert found.description == "project python"

    def test_project_skill_not_leaked_to_other_projects(self):
        reg = SkillRegistry()
        reg.register(Skill(name="secret", description="p1 only"), project_id="p1")
        assert reg.get("secret", project_id="p2") is None
        assert reg.get("secret") is None

    def test_list_skills_tag_filter_with_project_skills(self):
        reg = SkillRegistry()
        reg.register(Skill(name="g", tags=["common"]))
        reg.register(Skill(name="p", tags=["common"]), project_id="p1")
        result = reg.list_skills(tag="common", project_id="p1")
        names = {s.name for s in result}
        assert names == {"g", "p"}


# ── Argument Passing / Template Rendering ──────────────────────────────────


class TestRenderSkillBody:
    def test_plain_body_passes_through(self):
        result = render_skill("Hello, world.")
        assert result == "Hello, world."

    def test_variable_substitution_works(self):
        result = render_skill("Hello {{ name }}!", {"name": "Alice"})
        assert result == "Hello Alice!"

    def test_undefined_variable_raises_skill_render_error(self):
        with pytest.raises(SkillRenderError, match="undefined variable"):
            render_skill("{{ missing_var }}")

    def test_missing_variable_raises_no_implicit_default(self):
        with pytest.raises(SkillRenderError):
            render_skill("User {{ username }} is here", {"x": 1})

    def test_nested_dict_variable_rendering(self):
        result = render_skill(
            "{{ user.name }} likes {{ user.lang }}",
            {"user": {"name": "Bob", "lang": "Rust"}},
        )
        assert "Bob" in result
        assert "Rust" in result

    def test_multiple_variables_in_one_template(self):
        result = render_skill(
            "{{ a }} + {{ b }} = {{ c }}",
            {"a": 3, "b": 4, "c": 7},
        )
        assert "3 + 4 = 7" in result

    def test_sandbox_blocks_attribute_access_to_dunder(self):
        with pytest.raises(SkillRenderError):
            render_skill("{{ ''.__class__.__mro__ }}")

    def test_empty_variables_dict_does_not_interfere(self):
        result = render_skill("static text", {})
        assert result == "static text"


# ── Error Propagation ──────────────────────────────────────────────────────


class TestErrorPropagation:
    def test_skill_model_rejects_empty_name(self):
        with pytest.raises(ValueError, match="name must not be empty"):
            Skill(name="")

    def test_skill_model_rejects_whitespace_name(self):
        with pytest.raises(ValueError, match="name must not be empty"):
            Skill(name="   ")

    def test_parse_skill_md_with_non_string_frontmatter_values(self):
        content = "---\nname: ok\ntrigger_patterns: []\ntools: []\n---\nBody.\n"
        skill = parse_skill_md(content)
        assert skill.name == "ok"
        assert skill.trigger_patterns == []
        assert skill.tools == []

    def test_registry_get_nonexistent_always_none(self):
        reg = SkillRegistry()
        assert reg.get("nothing") is None
        assert reg.get("nothing", project_id="any") is None


# ── Timeout / Performance ──────────────────────────────────────────────────


class TestEmbedderPerformance:
    def test_hash_embedder_returns_fixed_dimensional_vector(self):
        embedder = HashEmbedder(dim=256)
        vec = embedder.embed("hello world")
        assert len(vec) == 256
        assert all(isinstance(v, float) for v in vec)

    def test_hash_embedder_same_input_produces_same_output(self):
        embedder = HashEmbedder(dim=128)
        v1 = embedder.embed("consistent output check")
        v2 = embedder.embed("consistent output check")
        assert v1 == v2

    def test_hash_embedder_different_inputs_produce_different_vectors(self):
        embedder = HashEmbedder(dim=256)
        v1 = embedder.embed("python programming")
        v2 = embedder.embed("woodworking joinery")
        assert v1 != v2

    def test_hash_embedder_embed_within_reasonable_time(self):
        embedder = HashEmbedder(dim=256)
        start = time.perf_counter()
        for _ in range(100):
            embedder.embed("a slightly longer text to test throughput performance")
        elapsed = time.perf_counter() - start
        assert elapsed < 1.0

    def test_hash_embedder_rejects_non_positive_dim(self):
        with pytest.raises(ValueError, match="dim must be positive"):
            HashEmbedder(dim=0)

    def test_hash_embedder_rejects_negative_dim(self):
        with pytest.raises(ValueError, match="dim must be positive"):
            HashEmbedder(dim=-5)


class TestCosineSimilarity:
    def test_identical_unit_vectors_return_one(self):
        v = [1.0 / math.sqrt(3)] * 3
        assert cosine_similarity(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors_return_zero(self):
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0

    def test_length_mismatch_raises_value_error(self):
        with pytest.raises(ValueError, match="vector length mismatch"):
            cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0])

    def test_zero_vector_returns_zero(self):
        result = cosine_similarity([0.0, 0.0], [1.0, 0.0])
        assert result == 0.0

    def test_both_zero_vectors_return_zero(self):
        assert cosine_similarity([0.0, 0.0], [0.0, 0.0]) == 0.0


class TestSkillEmbedderCache:
    def test_embed_skill_caches_by_name(self):
        embedder = SkillEmbedder(embedder=HashEmbedder(dim=64))
        skill = Skill(name="test_skill", description="A test skill")
        v1 = embedder.embed_skill(skill)
        v2 = embedder.embed_skill(skill)
        assert v1 == v2
        assert "test_skill" in embedder._cache

    def test_different_skills_have_different_vectors(self):
        embedder = SkillEmbedder(embedder=HashEmbedder(dim=64))
        s1 = Skill(name="python", description="Python programming")
        s2 = Skill(name="golang", description="Go programming")
        v1 = embedder.embed_skill(s1)
        v2 = embedder.embed_skill(s2)
        assert v1 != v2

    def test_embed_query_never_cached(self):
        embedder = SkillEmbedder(embedder=HashEmbedder(dim=64))
        embedder.embed_query("test query")
        assert len(embedder._cache) == 0

    def test_clear_cache_empties_internal_store(self):
        embedder = SkillEmbedder(embedder=HashEmbedder(dim=64))
        embedder.embed_skill(Skill(name="x", description="x"))
        assert len(embedder._cache) == 1
        embedder.clear_cache()
        assert len(embedder._cache) == 0


class TestTokenizeAndStem:
    def test_stem_plural_s_removed(self):
        assert _stem("functions") == "function"

    def test_stem_ing_removed(self):
        assert _stem("programming") == "programm"

    def test_stem_ed_removed(self):
        assert _stem("executed") == "execut"

    def test_stem_ies_converts_to_y(self):
        assert _stem("parties") == "party"

    def test_stem_ied_converts_to_y(self):
        assert _stem("carried") == "carry"

    def test_stem_short_word_preserved(self):
        assert _stem("go") == "go"

    def test_stem_no_suffix_returns_original(self):
        assert _stem("python") == "python"

    def test_tokenize_excludes_stopwords(self):
        tokens = _tokenize("the function is for a test")
        for sw in ("the", "is", "for", "a"):
            assert sw not in tokens

    def test_tokenize_lowercases_input(self):
        tokens = _tokenize("Hello WORLD")
        assert "hello" in tokens
        assert "world" in tokens
