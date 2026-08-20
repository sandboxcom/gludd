"""Deep plugin/discovery system tests: loading, resolution, compatibility,
hot reload, sandboxed execution, and failure isolation across all registries.

Covers:
  - Skill parsing, discovery, multi-path precedence
  - SkillRegistry: register / get / match_trigger (substring + embedding)
  - HashEmbedder / SkillEmbedder / cosine_similarity
  - ConnectorRegistry: config-driven build, factory/class/module resolution,
    validation, import guard, close/teardown, failure isolation
  - AgentRegistry: register, seal, can_invoke, behavior rendering
  - ProcessRegistry: register, seal, identity check, signal allowlist, reap
  - Compute discovery: LocalProbe, discover_all, probe isolation
  - Version compatibility stubs
  - Hot reload / refresh patterns
"""

from __future__ import annotations

import math
import os
import signal
import tempfile
from typing import Any, cast

import pytest

from general_ludd.agents.behavior import AgentBehavior, BehaviorRenderer
from general_ludd.agents.registry import AgentRegistry
from general_ludd.agents.registry import default_registry as agent_default_registry
from general_ludd.agents.types import AgentConfig, AgentPermission, AgentType
from general_ludd.connectors.registry import (
    ConnectorRegistry,
    _check_module_allowlist,
    _import_dotted,
    _validate_class_name,
    _validate_source_class,
)
from general_ludd.infra.discovery import (
    DiscoveredResource,
    LocalProbe,
    _parse_k8s_cpu,
    _parse_k8s_memory_gb,
    discover_all,
)
from general_ludd.process.registry import (
    ManagedProcess,
    ProcessRegistry,
    ProcessRegistryError,
    default_registry,
    set_default_registry,
)
from general_ludd.skills.embeddings import (
    HashEmbedder,
    OpenAIEmbedder,
    SkillEmbedder,
    _stem,
    _tokenize,
    cosine_similarity,
)
from general_ludd.skills.loader import FRONTMATTER_RE, discover_skills, parse_skill_md
from general_ludd.skills.registry import SkillRegistry
from general_ludd.skills.skill import Skill


class TestSkillParsingDeep:
    def test_parse_frontmatter_with_all_fields(self):
        md = (
            "---\n"
            "name: full_skill\n"
            "description: Does everything\n"
            "model_profile: strong\n"
            "tools: [read, write, edit]\n"
            'trigger_patterns: ["do thing", "run task"]\n'
            "tags: [core, prod]\n"
            "category: deployment\n"
            "---\n"
            "\n# Full Skill\nBody content.\n"
        )
        s = parse_skill_md(md, source_path="/skills/full_skill.md")
        assert s.name == "full_skill"
        assert s.description == "Does everything"
        assert s.model_profile == "strong"
        assert s.tools == ["read", "write", "edit"]
        assert s.trigger_patterns == ["do thing", "run task"]
        assert s.tags == ["core", "prod"]
        assert s.category == "deployment"
        assert "# Full Skill" in s.body
        assert s.source_path == "/skills/full_skill.md"

    def test_parse_tools_as_comma_delimited_string(self):
        md = "---\nname: tool_string\ntools: read, write, edit\n---\nbody\n"
        s = parse_skill_md(md)
        assert s.tools == ["read", "write", "edit"]

    def test_parse_tags_as_string_expands_to_list(self):
        md = "---\nname: tagged\ntags: devops\n---\nbody\n"
        s = parse_skill_md(md)
        assert s.tags == ["devops"]

    def test_parse_trigger_patterns_as_string_expands(self):
        md = "---\nname: simple\ntrigger_patterns: single_pattern\n---\nbody\n"
        s = parse_skill_md(md)
        assert s.trigger_patterns == ["single_pattern"]

    def test_parse_empty_frontmatter_uses_defaults(self):
        md = "---\n---\nBody only.\n"
        s = parse_skill_md(md, source_path="/s/minimal.md")
        assert s.name == "minimal"
        assert s.description == ""
        assert s.model_profile is None
        assert s.tools == []
        assert s.trigger_patterns == []
        assert s.tags == []

    def test_parse_yaml_error_graceful_fallback(self):
        md = "---\nname: [unclosed\n---\nBody\n"
        s = parse_skill_md(md, source_path="/s/bad.md")
        assert s.name == "bad"
        assert s.description == ""

    def test_frontmatter_regex_extracts_multiline(self):
        md = "---\nkey: val\nkey2: val2\n---\nBody\n"
        m = FRONTMATTER_RE.match(md)
        assert m is not None
        assert "key: val" in m.group(1)
        assert "key2: val2" in m.group(1)

    def test_no_frontmatter_present(self):
        md = "# Just a heading\n\nBody.\n"
        s = parse_skill_md(md, source_path="/s/plain.md")
        assert s.name == "plain"
        assert s.body == md

    def test_name_strips_whitespace_and_rejects_empty(self):
        md = "---\nname: '  '\n---\nBody\n"
        with pytest.raises(ValueError, match="name must not be empty"):
            parse_skill_md(md)


class TestSkillDiscoveryDeep:
    def test_discover_nonexistent_path_skips(self):
        skills = discover_skills("/tmp/nonexistent_skills_dir_42")
        assert skills == []

    def test_discover_sorts_filenames_deterministic(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ["z_skill.md", "a_skill.md", "m_skill.md"]:
                with open(os.path.join(d, name), "w") as f:
                    f.write(f"---\nname: {name[:-3]}\n---\nbody\n")
            skills = discover_skills(d)
            names = [s.name for s in skills]
            assert names == sorted(names)

    def test_discover_subdirectory_recursive(self):
        with tempfile.TemporaryDirectory() as d:
            subdir = os.path.join(d, "subdir")
            os.makedirs(subdir)
            with open(os.path.join(subdir, "nested.md"), "w") as f:
                f.write("---\nname: nested\n---\nnested body\n")
            skills = discover_skills(d)
            assert any(s.name == "nested" for s in skills)

    def test_discover_multiple_paths_later_wins(self):
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            with open(os.path.join(d1, "same.md"), "w") as f:
                f.write("---\nname: same\ndescription: first\n---\nv1\n")
            with open(os.path.join(d2, "same.md"), "w") as f:
                f.write("---\nname: same\ndescription: second\n---\nv2\n")
            skills = discover_skills(d1, d2)
            assert len(skills) == 1
            assert skills[0].description == "second"

    def test_discover_ignores_non_md_files(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "config.yml"), "w") as f:
                f.write("key: val\n")
            with open(os.path.join(d, "README.md"), "w") as f:
                f.write("---\nname: readme\n---\nbody\n")
            skills = discover_skills(d)
            assert len(skills) == 1
            assert skills[0].name == "readme"


class TestSkillRegistryDeep:
    def test_register_preserves_all_attributes(self):
        reg = SkillRegistry()
        s = Skill(
            name="deep",
            description="deep test",
            tags=["a", "b"],
            trigger_patterns=["deep"],
            body="content",
            source_path="/s/deep.md",
            model_profile="x",
        )
        reg.register(s)
        got = reg.get("deep")
        assert got is not None
        assert got.description == "deep test"
        assert got.tags == ["a", "b"]
        assert got.model_profile == "x"
        assert got.source_path == "/s/deep.md"

    def test_register_replaces_same_name(self):
        reg = SkillRegistry()
        reg.register(Skill(name="dup", description="v1"))
        reg.register(Skill(name="dup", description="v2"))
        dup = reg.get("dup")
        assert dup is not None
        assert dup.description == "v2"

    def test_project_skills_isolated_from_global(self):
        reg = SkillRegistry()
        reg.register(Skill(name="shared"), project_id="p1")
        assert reg.get("shared") is None
        assert reg.get("shared", project_id="p1") is not None

    def test_project_skill_shadows_global(self):
        reg = SkillRegistry()
        reg.register(Skill(name="shadow", description="global"))
        reg.register(Skill(name="shadow", description="project"), project_id="p1")
        got = reg.get("shadow", project_id="p1")
        assert got is not None
        assert got.description == "project"

    def test_match_trigger_case_insensitive(self):
        reg = SkillRegistry()
        reg.register(Skill(name="s1", trigger_patterns=["DePlOy"]))
        matches = reg.match_trigger("please deploy to prod")
        assert len(matches) == 1
        assert matches[0].name == "s1"

    def test_match_trigger_substring_in_text(self):
        reg = SkillRegistry()
        reg.register(Skill(name="k8s", trigger_patterns=["kubernetes"]))
        matches = reg.match_trigger("manage kubernetes cluster")
        assert len(matches) == 1

    def test_match_trigger_embedding_fallback_basic_match(self):
        reg = SkillRegistry()
        reg.register(Skill(name="deploy", description="deploy to kubernetes cluster"))
        reg.register(Skill(name="unrelated", description="baking recipes for cookies"))
        matches = reg.match_trigger("kubernetes rollout deployment", similarity_threshold=0.1)
        assert any(m.name == "deploy" for m in matches)

    def test_match_trigger_embedding_no_match_below_threshold(self):
        reg = SkillRegistry()
        reg.register(Skill(name="baking", description="bake cookies and cakes"))
        matches = reg.match_trigger("quantum chromodynamics", similarity_threshold=0.99)
        assert matches == []

    def test_match_trigger_returns_all_when_substring_matches(self):
        reg = SkillRegistry()
        reg.register(Skill(name="a", trigger_patterns=["deploy"]))
        reg.register(Skill(name="b", trigger_patterns=["deploy"]))
        matches = reg.match_trigger("deploy now")
        assert len(matches) == 2

    def test_list_skills_tag_filter_nonexistent(self):
        reg = SkillRegistry()
        reg.register(Skill(name="x", tags=["a"]))
        assert reg.list_skills(tag="z") == []

    def test_list_skills_without_project_returns_only_global(self):
        reg = SkillRegistry()
        reg.register(Skill(name="g"))
        reg.register(Skill(name="p"), project_id="p1")
        all_skills = reg.list_skills()
        assert {s.name for s in all_skills} == {"g"}

    def test_refresh_with_paths_discovers_and_registers(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "r.md"), "w") as f:
                f.write("---\nname: refreshed\ndescription: R\n---\nbody\n")
            reg = SkillRegistry()
            reg.refresh(search_paths=[d])
            refreshed = reg.get("refreshed")
            assert refreshed is not None
            assert refreshed.description == "R"

    def test_refresh_with_project_id(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "px.md"), "w") as f:
                f.write("---\nname: px\ndescription: project x\n---\nbody\n")
            reg = SkillRegistry()
            reg.refresh(search_paths=[d], project_id="x")
            assert reg.get("px", project_id="x") is not None

    def test_refresh_empty_paths_returns_existing(self):
        reg = SkillRegistry()
        reg.register(Skill(name="persistent"))
        result = reg.refresh()
        assert "skills" in result
        assert "persistent" in result["skills"]


class TestTokenizeAndStem:
    def test_tokenize_returns_lowercase_alphanumeric(self):
        tokens = _tokenize("Hello World 42! Foo-Bar")
        assert tokens == ["hello", "world", "42", "foo", "bar"]

    def test_tokenize_filters_stopwords(self):
        tokens = _tokenize("the quick brown fox")
        assert "the" not in tokens

    def test_stem_plural_s(self):
        assert _stem("cats") == "cat"

    def test_stem_ing(self):
        assert _stem("running") == "runn"

    def test_stem_ies(self):
        assert _stem("parties") == "party"

    def test_stem_ied(self):
        assert _stem("carried") == "carry"

    def test_stem_short_word_unchanged(self):
        assert _stem("cat") == "cat"
        assert _stem("go") == "go"


class TestHashEmbedder:
    def test_embed_returns_correct_dimension(self):
        he = HashEmbedder(dim=128)
        vec = he.embed("test sentence")
        assert len(vec) == 128

    def test_embed_produces_unit_vector(self):
        he = HashEmbedder(dim=64)
        vec = he.embed("the quick brown fox jumps over the lazy dog")
        norm = math.sqrt(sum(v * v for v in vec))
        assert math.isclose(norm, 1.0, rel_tol=1e-9) or norm == 0.0

    def test_embed_empty_produces_zero_norm(self):
        he = HashEmbedder(dim=32)
        vec = he.embed("the and or but")
        assert all(v == 0.0 for v in vec)

    def test_dim_zero_raises(self):
        with pytest.raises(ValueError, match="dim must be positive"):
            HashEmbedder(dim=0)

    def test_embed_similar_texts_high_cosine(self):
        he = HashEmbedder(dim=256)
        v1 = he.embed("deploy to kubernetes production cluster")
        v2 = he.embed("kubernetes deployment to production")
        sim = cosine_similarity(v1, v2)
        assert sim > 0.7

    def test_embed_dissimilar_texts_low_cosine(self):
        he = HashEmbedder(dim=256)
        v1 = he.embed("kubernetes deploy helm chart")
        v2 = he.embed("bake chocolate chip cookies")
        sim = cosine_similarity(v1, v2)
        assert sim < 0.4


class TestCosineSimilarity:
    def test_identical_vectors(self):
        v = [1.0, 2.0, 3.0]
        assert math.isclose(cosine_similarity(v, v), 1.0)

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert cosine_similarity(a, b) == 0.0

    def test_opposite_vectors(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert math.isclose(cosine_similarity(a, b), -1.0)

    def test_mismatched_lengths_raises(self):
        with pytest.raises(ValueError, match="vector length mismatch"):
            cosine_similarity([1.0], [1.0, 2.0])

    def test_zero_vector_returns_zero(self):
        assert cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0


class TestSkillEmbedder:
    def test_default_hash_embedder_used(self):
        se = SkillEmbedder()
        vec = se.embed_query("hello world")
        assert len(vec) == 256
        assert any(v != 0.0 for v in vec)

    def test_custom_embedder_respected(self):
        class _FakeEmb:
            def embed(self, text):
                return [0.5, 0.5]

        se = SkillEmbedder(embedder=_FakeEmb())
        vec = se.embed_query("anything")
        assert vec == [0.5, 0.5]

    def test_embed_skill_caches(self):
        se = SkillEmbedder()
        s1 = Skill(name="cache_test", description="test cache")
        v1 = se.embed_skill(s1)
        v2 = se.embed_skill(s1)
        assert v1 is v2

    def test_clear_cache_forces_recomputation(self):
        se = SkillEmbedder()
        s = Skill(name="clear_me", description="before clear")
        v1 = se.embed_skill(s)
        se.clear_cache()
        v2 = se.embed_skill(s)
        assert v1 == v2
        assert v1 is not v2

    def test_openai_fallback_when_no_key(self):
        se = SkillEmbedder(use_openai_if_available=True)
        assert isinstance(se._embedder, HashEmbedder)

    def test_openai_constructor_requires_key(self):
        with pytest.raises(RuntimeError):
            OpenAIEmbedder(api_key=None)


class _FakeSource:
    def __init__(self, config):
        self.config = dict(config)
        self.name = str(config.get("name") or "fake")
        self.KIND = str(config.get("kind") or "test")

    def health(self):
        return {"ok": True}

    def query(self, spec):
        return [{"result": "ok", "spec": spec}]


class _ClosableSource(_FakeSource):
    def __init__(self, config):
        super().__init__(config)
        self.closed = False

    def close(self):
        self.closed = True


class _DisconnectableSource(_FakeSource):
    def __init__(self, config):
        super().__init__(config)
        self.disc = False

    def disconnect(self):
        self.disc = True


class _BadSource:
    KIND = "bad"

    def __init__(self, config):
        raise RuntimeError("boom")


class _Uncallable:
    KIND = "bad"


class TestConnectorRegistryDeep:
    def test_build_from_empty_configs(self):
        reg = ConnectorRegistry.from_config([], factories={"f": _FakeSource})
        assert reg.list_sources() == []
        assert reg.errors() == []

    def test_build_from_none_configs(self):
        reg = ConnectorRegistry.from_config(None, factories={"f": _FakeSource})
        assert reg.list_sources() == []

    def test_factory_selector_resolves_class(self):
        reg = ConnectorRegistry.from_config(
            [{"name": "src1", "kind": "test", "factory": "f"}],
            factories={"f": _FakeSource},
        )
        src1 = reg.get("src1")
        assert src1 is not None
        assert src1.KIND == "test"

    def test_factory_not_in_map_records_error(self):
        reg = ConnectorRegistry.from_config(
            [{"name": "src1", "factory": "missing"}],
            factories={},
        )
        assert reg.get("src1") is None
        assert len(reg.errors()) == 1
        assert (
            "discovery" in str(reg.errors()[0]["error"]).lower() or "unknown" in str(reg.errors()[0]["error"]).lower()
        )

    def test_config_missing_name_records_error(self):
        reg = ConnectorRegistry.from_config(
            [{"factory": "f"}],
            factories={"f": _FakeSource},
        )
        assert reg.errors() or reg.list_sources() == []

    def test_config_not_a_dict_records_error(self):
        bad_configs: Any = [None, "string", 42]
        reg = ConnectorRegistry.from_config(
            bad_configs,
            factories={},
        )
        assert len(reg.errors()) > 0

    def test_bad_init_isolated_from_good_entries(self):
        reg = ConnectorRegistry.from_config(
            [
                {"name": "good", "factory": "f"},
                {"name": "bad", "factory": "boom"},
            ],
            factories={"f": _FakeSource, "boom": _BadSource},
        )
        assert reg.get("good") is not None
        assert reg.get("bad") is None
        assert any(e["name"] == "bad" for e in reg.errors())

    def test_post_init_source_validation_structural(self):
        class _MissingQuery:
            KIND = "bad"
            name = "bad"

            def health(self):
                return {}

            def __init__(self, config):
                pass

        reg = ConnectorRegistry.from_config(
            [{"name": "badq", "kind": "test", "factory": "badq"}],
            factories={"badq": _MissingQuery},
        )
        assert reg.get("badq") is None
        assert any("_SourceLike" in str(e.get("error", "")) for e in reg.errors())

    def test_list_sources_returns_metadata_not_secrets(self):
        reg = ConnectorRegistry.from_config(
            [{"name": "s", "kind": "logs", "factory": "f"}],
            factories={"f": _FakeSource},
        )
        sources = reg.list_sources()
        assert sources[0]["name"] == "s"
        assert sources[0]["kind"] == "logs"

    def test_names_returns_registration_order(self):
        reg = ConnectorRegistry.from_config(
            [{"name": "a", "factory": "f"}, {"name": "b", "factory": "f"}],
            factories={"f": _FakeSource},
        )
        assert reg.names() == ["a", "b"]

    def test_by_kind_groups_sources(self):
        reg = ConnectorRegistry.from_config(
            [
                {"name": "s1", "kind": "logs", "factory": "f"},
                {"name": "s2", "kind": "metrics", "factory": "f"},
                {"name": "s3", "kind": "logs", "factory": "f"},
            ],
            factories={"f": _FakeSource},
        )
        groups = reg.by_kind()
        assert "logs" in groups
        assert groups["logs"] == ["s1", "s3"]
        assert groups["metrics"] == ["s2"]

    def test_health_all_never_raises(self):
        class _FailingHealth(_FakeSource):
            def health(self):
                raise RuntimeError("health boom")

        reg = ConnectorRegistry.from_config(
            [{"name": "fail", "factory": "fh"}],
            factories={"fh": _FailingHealth, "f": _FakeSource},
        )
        result = reg.health_all()
        assert "fail" in result
        assert result["fail"].get("ok") is False

    def test_close_calls_disconnect_on_each_source(self):
        reg = ConnectorRegistry.from_config(
            [{"name": "ds", "factory": "disc"}],
            factories={"disc": _DisconnectableSource},
        )
        src: _DisconnectableSource = cast(_DisconnectableSource, reg.get("ds"))
        reg.close()
        assert src.disc is True

    def test_close_calls_close_on_each_source(self):
        reg = ConnectorRegistry.from_config(
            [{"name": "cs", "factory": "cl"}],
            factories={"cl": _ClosableSource},
        )
        src: _ClosableSource = cast(_ClosableSource, reg.get("cs"))
        reg.close()
        assert src.closed is True

    def test_close_handles_teardown_errors_gracefully(self):
        class _FailingClose(_FakeSource):
            def __init__(self, config):
                super().__init__(config)
                self.closed = False

            def close(self):
                self.closed = True
                raise RuntimeError("close boom")

        reg = ConnectorRegistry.from_config(
            [{"name": "fc", "factory": "fc"}],
            factories={"fc": _FailingClose},
        )
        reg.close()

    def test_query_unknown_name_raises_keyerror(self):
        reg = ConnectorRegistry.from_config([], factories={"f": _FakeSource})
        with pytest.raises(KeyError, match="no registered source"):
            reg.query("nonexistent", {})

    def test_query_connector_exception_becomes_error_record(self):
        class _BoomQuery(_FakeSource):
            def query(self, spec):
                raise RuntimeError("q boom")

        reg = ConnectorRegistry.from_config(
            [{"name": "bq", "factory": "bqm"}],
            factories={"bqm": _BoomQuery},
        )
        records = reg.query("bq", {})
        assert records[0].get("message") == "query failed"

    def test_query_none_spec_defaults_to_empty(self):
        reg = ConnectorRegistry.from_config(
            [{"name": "s", "factory": "f"}],
            factories={"f": _FakeSource},
        )
        records = reg.query("s", cast(dict[str, Any], None))
        assert records is not None

    def test_validate_source_class_rejects_non_callable(self):
        with pytest.raises(TypeError, match="not callable"):
            _validate_source_class("not_a_class")

    def test_validate_source_class_rejects_missing_methods(self):
        class _MissingHealth:
            def query(self, spec):
                return []

        with pytest.raises(TypeError, match="health"):
            _validate_source_class(_MissingHealth)

    def test_validate_class_name_rejects_dot_path(self):
        with pytest.raises(ValueError):
            _validate_class_name("os.system")

    def test_validate_class_name_rejects_underscore_prefix(self):
        with pytest.raises(ValueError, match="starts with '_'"):
            _validate_class_name("__builtins__")

    def test_validate_class_name_rejects_non_source_suffix(self):
        with pytest.raises(ValueError, match="must end with 'Source'"):
            _validate_class_name("MyClass")

    def test_validate_class_name_rejects_lowercase_start(self):
        with pytest.raises(ValueError, match="uppercase"):
            _validate_class_name("mySource")

    def test_validate_class_name_accepts_valid(self):
        _validate_class_name("MyConnectorSource")


class TestConnectorImportGuards:
    def test_module_allowlist_rejects_arbitrary_import(self):
        with pytest.raises(ValueError, match="module import denied"):
            _check_module_allowlist("os.system", selector="class")

    def test_module_allowlist_rejects_non_connector_package(self):
        with pytest.raises(ValueError, match="module import denied"):
            _check_module_allowlist("general_ludd.routers.compute", selector="class")

    def test_module_allowlist_accepts_connector_submodule(self):
        _check_module_allowlist("general_ludd.connectors.prometheus", selector="module")

    def test_import_dotted_rejects_arbitrary_module(self):
        with pytest.raises(ImportError):
            _import_dotted("os:system")

    def test_import_dotted_rejects_malformed(self):
        with pytest.raises(ValueError, match="malformed class path"):
            _import_dotted("no_dots_or_colons")


class TestAgentRegistryDeep:
    def test_default_registry_has_builtin_agents(self):
        reg = agent_default_registry()
        names = {a.name for a in reg.list_agents()}
        assert "build" in names
        assert "plan" in names
        assert "explore" in names
        assert "general" in names
        assert "research" in names

    def test_seal_prevents_new_registration(self):
        reg = AgentRegistry()
        reg.seal()
        with pytest.raises(RuntimeError, match="sealed"):
            reg.register(AgentConfig(name="new_agent", description="x", type=AgentType.SUBAGENT))

    def test_seal_allows_primary_offline_re_registration(self):
        reg = AgentRegistry()
        reg.register(AgentConfig(name="primary", description="p", type=AgentType.PRIMARY))
        reg.seal()
        reg.register(AgentConfig(name="primary", description="p2", type=AgentType.PRIMARY))

    def test_can_invoke_dispatch_allowed(self):
        reg = AgentRegistry()
        reg.register(
            AgentConfig(
                name="dispatcher",
                description="d",
                type=AgentType.PRIMARY,
                permissions=AgentPermission(
                    can_dispatch_subagents=True,
                    allowed_subagents=["explore"],
                ),
            )
        )
        reg.register(
            AgentConfig(
                name="explore",
                description="e",
                type=AgentType.SUBAGENT,
            )
        )
        assert reg.can_invoke("dispatcher", "explore") is True

    def test_can_invoke_dispatch_denied_without_permission(self):
        reg = AgentRegistry()
        reg.register(
            AgentConfig(
                name="reader",
                description="r",
                type=AgentType.SUBAGENT,
                permissions=AgentPermission(can_dispatch_subagents=False),
            )
        )
        reg.register(
            AgentConfig(
                name="explore",
                description="e",
                type=AgentType.SUBAGENT,
            )
        )
        assert reg.can_invoke("reader", "explore") is False

    def test_can_invoke_unknown_invoker(self):
        reg = AgentRegistry()
        reg.register(
            AgentConfig(
                name="explore",
                description="e",
                type=AgentType.SUBAGENT,
            )
        )
        assert reg.can_invoke("nonexistent", "explore") is False

    def test_can_invoke_unknown_target(self):
        reg = AgentRegistry()
        reg.register(
            AgentConfig(
                name="build",
                description="b",
                type=AgentType.PRIMARY,
                permissions=AgentPermission(
                    can_dispatch_subagents=True,
                    allowed_subagents=["*"],
                ),
            )
        )
        assert reg.can_invoke("build", "nonexistent") is False

    def test_can_invoke_glob_pattern_matching(self):
        reg = AgentRegistry()
        reg.register(
            AgentConfig(
                name="build",
                description="b",
                type=AgentType.PRIMARY,
                permissions=AgentPermission(
                    can_dispatch_subagents=True,
                    allowed_subagents=["explore", "agent-*"],
                ),
            )
        )
        reg.register(
            AgentConfig(
                name="agent-writer",
                description="w",
                type=AgentType.SUBAGENT,
            )
        )
        reg.register(
            AgentConfig(
                name="agent-reviewer",
                description="r",
                type=AgentType.SUBAGENT,
            )
        )
        assert reg.can_invoke("build", "agent-writer") is True
        assert reg.can_invoke("build", "agent-reviewer") is True
        assert reg.can_invoke("build", "agent-unknown") is False

    def test_get_behavior_returns_default_for_subagent(self):
        reg = AgentRegistry()
        reg.register(
            AgentConfig(
                name="worker",
                description="w",
                type=AgentType.SUBAGENT,
            )
        )
        behavior = reg.get_behavior("worker")
        assert behavior is not None
        assert behavior.tdd_enforced is True

    def test_render_behavior_prompt_for_known_agent(self):
        reg = AgentRegistry()
        reg.register(
            AgentConfig(
                name="builder",
                description="b",
                type=AgentType.PRIMARY,
            )
        )
        prompt = reg.render_behavior_prompt("builder", "build project X")
        assert prompt is not None
        assert "build project X" in prompt

    def test_render_behavior_prompt_for_unknown_agent(self):
        reg = AgentRegistry()
        assert reg.render_behavior_prompt("nonexistent", "task") is None

    def test_list_subagents_filters_type(self):
        reg = AgentRegistry()
        reg.register(
            AgentConfig(
                name="primary",
                description="p",
                type=AgentType.PRIMARY,
            )
        )
        reg.register(
            AgentConfig(
                name="worker",
                description="w",
                type=AgentType.SUBAGENT,
            )
        )
        subs = reg.list_subagents()
        assert all(a.type == AgentType.SUBAGENT for a in subs)
        assert len(subs) == 1


class TestProcessRegistryDeep:
    def test_seal_rejects_register(self):
        reg = ProcessRegistry()
        reg.seal()
        with pytest.raises(ProcessRegistryError, match="sealed"):
            reg.register(99999, ["fake"])

    def test_seal_rejects_deregister(self):
        reg = ProcessRegistry()
        reg.seal()
        with pytest.raises(ProcessRegistryError, match="sealed"):
            reg.deregister(99999)

    def test_seal_rejects_reap(self):
        reg = ProcessRegistry()
        reg.seal()
        with pytest.raises(ProcessRegistryError, match="sealed"):
            reg.reap()

    def test_seal_idempotent(self):
        reg = ProcessRegistry()
        reg.seal()
        reg.seal()
        assert reg.is_sealed

    def test_get_unknown_returns_none(self):
        reg = ProcessRegistry()
        assert reg.get(99999) is None

    def test_is_managed_returns_false_for_unknown(self):
        reg = ProcessRegistry()
        assert reg.is_managed(99999) is False

    def test_is_alive_returns_false_for_unknown(self):
        reg = ProcessRegistry()
        assert reg.is_alive(99999) is False

    def test_is_alive_stale_pid(self):
        reg = ProcessRegistry()
        reg.register(99999, ["fake", "--verbose"], origin="test_alive")
        assert reg.is_managed(99999)
        assert not reg.is_alive(99999)

    def test_resolve_signal_numeric(self):
        signum = ProcessRegistry.resolve_signal(signal.SIGTERM)
        assert signum == signal.SIGTERM

    def test_resolve_signal_name_with_sig_prefix(self):
        signum = ProcessRegistry.resolve_signal("SIGTERM")
        assert signum == signal.SIGTERM

    def test_resolve_signal_name_without_prefix(self):
        signum = ProcessRegistry.resolve_signal("TERM")
        assert signum == signal.SIGTERM

    def test_resolve_signal_disallowed(self):
        with pytest.raises(ProcessRegistryError, match="allow-list"):
            ProcessRegistry.resolve_signal(666)

    def test_allowed_signals_returns_copy(self):
        sigs = ProcessRegistry.allowed_signals()
        assert "SIGTERM" in sigs
        assert "SIGKILL" in sigs
        sigs["EXTRA"] = 999
        assert "EXTRA" not in ProcessRegistry.allowed_signals()

    def test_list_active_only_filters_dead(self):
        reg = ProcessRegistry()
        reg.register(99999, ["fake"], origin="test_list")
        assert len(reg.list(active_only=True)) == 0
        assert len(reg.list(active_only=False)) == 1

    def test_deregister_removes_entry(self):
        reg = ProcessRegistry()
        reg.register(99999, ["fake"], origin="test_dereg")
        assert reg.is_managed(99999)
        removed = reg.deregister(99999)
        assert removed is not None
        assert not reg.is_managed(99999)

    def test_deregister_missing_is_none(self):
        reg = ProcessRegistry()
        assert reg.deregister(99999) is None

    def test_managed_process_to_dict(self):
        mp = ManagedProcess(pid=123, command=["bash", "-c"], origin="test", job_id="J1", project_id="P1")
        d = mp.to_dict()
        assert d["pid"] == 123
        assert d["command"] == ["bash", "-c"]
        assert d["origin"] == "test"
        assert d["job_id"] == "J1"

    def test_register_string_command_converted_to_list(self):
        reg = ProcessRegistry()
        reg.register(99999, "echo hello", origin="test_str_cmd")
        mp = reg.get(99999)
        assert mp is not None
        assert isinstance(mp.command, list)
        assert mp.command == ["echo hello"]

    def test_set_default_registry_twice_raises(self):
        r1 = ProcessRegistry()
        import general_ludd.process.registry as pr_mod

        old = pr_mod._DEFAULT_REGISTRY
        pr_mod._DEFAULT_REGISTRY = None
        try:
            set_default_registry(r1)
            with pytest.raises(RuntimeError, match="already set"):
                set_default_registry(ProcessRegistry())
        finally:
            pr_mod._DEFAULT_REGISTRY = old

    def test_default_registry_is_singleton(self):
        a = default_registry()
        b = default_registry()
        assert a is b


class TestComputeDiscovery:
    def test_local_probe_returns_cpu(self):
        probe = LocalProbe()
        resources = probe.probe()
        assert len(resources) == 1
        assert resources[0].provider == "local"
        assert resources[0].kind == "process"
        assert resources[0].cpu > 0

    def test_discovered_resource_label_cpu_only(self):
        r = DiscoveredResource(provider="aws", kind="t2.micro", cpu=1, mem_gb=2)
        assert "cpu-only" in r.label()

    def test_discovered_resource_label_with_gpu(self):
        r = DiscoveredResource(provider="aws", kind="g4dn", cpu=4, mem_gb=16, gpu="T4", gpu_count=1)
        assert "T4" in r.label()

    def test_discover_all_isolates_probe_failures(self):
        class _FailingProbe:
            def probe(self):
                raise RuntimeError("probe down")

        resources = discover_all([_FailingProbe(), LocalProbe()])
        assert len(resources) == 1
        assert resources[0].provider == "local"

    def test_discover_all_empty_probes(self):
        assert discover_all([]) == []

    def test_parse_k8s_cpu_millis(self):
        assert _parse_k8s_cpu("500m") == 0.5

    def test_parse_k8s_cpu_nanos(self):
        assert _parse_k8s_cpu("1000000000n") == 1.0

    def test_parse_k8s_cpu_integer(self):
        assert _parse_k8s_cpu("2") == 2.0

    def test_parse_k8s_memory_ki(self):
        assert _parse_k8s_memory_gb("1048576Ki") == 1.0

    def test_parse_k8s_memory_mi(self):
        assert _parse_k8s_memory_gb("1024Mi") == 1.0

    def test_parse_k8s_memory_gi(self):
        assert _parse_k8s_memory_gb("4Gi") == 4.0

    def test_parse_k8s_memory_ti(self):
        assert _parse_k8s_memory_gb("2Ti") == pytest.approx(2048.0)

    def test_parse_k8s_memory_pi(self):
        assert _parse_k8s_memory_gb("1Pi") == pytest.approx(1048576.0)

    def test_parse_k8s_memory_raw_bytes(self):
        gb = _parse_k8s_memory_gb("1073741824")
        assert math.isclose(gb, 1.0, rel_tol=0.01)


# ---------------------------------------------------------------------------
# Version compatibility stubs
# ---------------------------------------------------------------------------
class TestPluginVersionCompatibility:
    """Structural tests for version resolution across registries."""

    def test_skill_model_strict_config(self):
        s = Skill(name="test")
        assert s.model_config.get("strict") is True

    def test_register_rejects_empty_name(self):
        with pytest.raises(ValueError, match="name must not be empty"):
            Skill(name="")

    def test_agent_config_default_values(self):
        ac = AgentConfig(name="test", description="desc", type=AgentType.SUBAGENT)
        assert ac.max_steps == 10
        assert ac.max_concurrent == 1
        assert ac.enabled is True
        assert ac.bind_tools_on_dispatch is True

    def test_agent_permission_defaults(self):
        ap = AgentPermission()
        assert ap.can_read is True
        assert ap.can_edit is False
        assert ap.can_bash is False
        assert ap.can_dispatch_subagents is False

    def test_managed_process_default_create_time_none(self):
        mp = ManagedProcess(pid=1, command=["cmd"])
        assert mp.create_time is None

    def test_connector_registry_errors_copy_independent(self):
        reg = ConnectorRegistry.from_config(
            [{"name": "e1", "factory": "missing"}],
            factories={},
        )
        errs = reg.errors()
        errs.clear()
        assert len(reg.errors()) == 1


# ---------------------------------------------------------------------------
# Hot reload / refresh patterns
# ---------------------------------------------------------------------------
class TestHotReloadPatterns:
    def test_skill_registry_retains_skill_on_reregister(self):
        reg = SkillRegistry()
        s = Skill(name="hot", description="original")
        reg.register(s)
        reg.register(Skill(name="hot", description="reloaded"))
        hot = reg.get("hot")
        assert hot is not None
        assert hot.description == "reloaded"

    def test_connector_registry_close_then_rebuild(self):
        reg1 = ConnectorRegistry.from_config(
            [{"name": "s", "kind": "test", "factory": "f"}],
            factories={"f": _ClosableSource},
        )
        src1: _ClosableSource = cast(_ClosableSource, reg1.get("s"))
        reg1.close()
        assert src1.closed is True

        reg2 = ConnectorRegistry.from_config(
            [{"name": "s", "kind": "test", "factory": "f"}],
            factories={"f": _ClosableSource},
        )
        assert reg2.get("s") is not None
        assert reg2.get("s") is not src1

    def test_skill_registry_refresh_replaces_skill(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "skill.md"), "w") as f:
                f.write("---\nname: skill_reload\ndescription: v1\n---\nbody\n")
            reg = SkillRegistry()
            reg.refresh(search_paths=[d])
            skill1 = reg.get("skill_reload")
            assert skill1 is not None
            assert skill1.description == "v1"

            with open(os.path.join(d, "skill.md"), "w") as f:
                f.write("---\nname: skill_reload\ndescription: v2\n---\nbody v2\n")
            reg.refresh(search_paths=[d])
            skill2 = reg.get("skill_reload")
            assert skill2 is not None
            assert skill2.description == "v2"

    def test_agent_registry_render_caching(self):
        renderer = BehaviorRenderer()
        b1 = AgentBehavior(role="test")
        r1 = renderer.render(b1)
        r2 = renderer.render(b1)
        assert r1 is r2


# ---------------------------------------------------------------------------
# Sandboxed plugin execution — failure isolation
# ---------------------------------------------------------------------------
class TestSandboxedExecution:
    """Plugins must isolate failures: one bad plugin must not crash the system."""

    def test_skill_parse_bad_yaml_does_not_raise(self):
        md = "---\n[\n---\nBody\n"
        s = parse_skill_md(md, source_path="/s/bad_yaml.md")
        assert s is not None
        assert s.name == "bad_yaml"

    def test_connector_bad_import_does_not_abort_build(self):
        reg = ConnectorRegistry.from_config(
            [
                {"name": "good", "factory": "f"},
                {"name": "bad_import", "class": "general_ludd.connectors:Nope"},
                {"name": "good2", "factory": "f"},
            ],
            factories={"f": _FakeSource},
        )
        assert reg.get("good") is not None
        assert reg.get("good2") is not None
        assert any(e["name"] == "bad_import" for e in reg.errors())

    def test_agent_registry_seal_does_not_block_re_registration(self):
        reg = AgentRegistry()
        reg.register(AgentConfig(name="primary", description="v1", type=AgentType.PRIMARY))
        reg.seal()
        reg.register(AgentConfig(name="primary", description="v2", type=AgentType.PRIMARY))
        assert reg.get("primary").description == "v2"

    def test_probe_network_failure_skips(self):
        class _NetworkDownProbe:
            def probe(self):
                raise ConnectionError("network gone")

        resources = discover_all([_NetworkDownProbe(), LocalProbe()])
        assert len(resources) == 1
        assert resources[0].provider == "local"

    def test_hash_embedder_with_infinite_values(self):
        he = HashEmbedder(dim=4)
        vec = he.embed("a")
        assert all(math.isfinite(v) for v in vec)

    def test_cosine_similarity_zero_dot_product(self):
        sim = cosine_similarity([1.0, 0.0, 0.0], [0.0, 1.0, 0.0])
        assert sim == 0.0

    def test_skill_registry_match_no_trigger_no_desc(self):
        reg = SkillRegistry()
        reg.register(Skill(name="bare"))
        matches = reg.match_trigger("anything")
        assert matches == []
