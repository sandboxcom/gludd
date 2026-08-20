"""Deep property-based tests for serialization, merge, composition, and retry.

Uses hypothesis to generate inputs and verify algebraic properties:
  - Roundtrip: to_dict/from_dict or serialize/deserialize cycles
  - Commutativity: merge(a, b) == merge(b, a) where applicable
  - Associativity: merge(merge(a, b), c) == merge(a, merge(b, c))
  - Idempotency: merge(x, x) == x; retry(n, f) == f where f never fails
"""

from __future__ import annotations

import json
from typing import Any

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from general_ludd.agents.behavior import AgentBehavior
from general_ludd.chat.contracts import ChatMessage
from general_ludd.config.project_dir import merge_config
from general_ludd.dispatch.dynamic_dispatcher import DispatchResult, parse_tool_calls
from general_ludd.entity.graph import EntityGraph, EntityNode
from general_ludd.infra.service_catalog import DiscoveredService, ServiceCatalog, merge_catalog
from general_ludd.memory.memory_bank import Disposition, MemoryBankConfig, MentalModel
from general_ludd.retrieval.agentic_context import AgenticContextInjector, AgenticResearchContext, ResearchContextItem

# ── strategies ──────────────────────────────────────────────────────────

_text = st.text(min_size=0, max_size=100, alphabet=st.characters(blacklist_categories=["Cs"]))
_short_text = st.text(min_size=0, max_size=30, alphabet=st.characters(blacklist_categories=["Cs"]))
_float01 = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)


def _simple_dict() -> st.SearchStrategy[dict[str, Any]]:
    return st.dictionaries(
        keys=st.text(min_size=1, max_size=20, alphabet=st.characters(blacklist_categories=["Cs"])),
        values=st.one_of(
            st.integers(-1000, 1000), st.floats(-1000.0, 1000.0, allow_nan=False), _text, st.booleans(), st.none()
        ),
        min_size=0,
        max_size=10,
    )


def _nested_dict(depth: int = 0) -> st.SearchStrategy[dict[str, Any]]:
    if depth >= 3:
        return _simple_dict()
    return st.dictionaries(
        keys=_short_text,
        values=st.one_of(st.integers(-100, 100), _text, st.booleans(), st.deferred(lambda: _nested_dict(depth + 1))),
        min_size=0,
        max_size=6,
    )


# =========================================================================
# 1. merge_config — algebraic properties
# =========================================================================


class TestMergeConfigAlgebraic:
    def test_merge_cfg_idempotent_result_is_detached_from_source(self) -> None:
        """An idempotent merge must not couple later result mutations to its input."""
        source: dict[str, Any] = {
            "pipeline": {"steps": [{"name": "build"}]},
            "rules": [{"enabled": True}],
        }

        merged = merge_config(source, source)
        assert merged == source

        merged["pipeline"]["steps"][0]["name"] = "deploy"
        merged["rules"][0]["enabled"] = False

        assert source == {
            "pipeline": {"steps": [{"name": "build"}]},
            "rules": [{"enabled": True}],
        }

    @given(items=st.lists(_simple_dict(), min_size=1, max_size=5))
    @settings(max_examples=100)
    def test_merge_cfg_detaches_mutable_values_for_every_merge_path(self, items: list[dict[str, Any]]) -> None:
        """User-only, project-only, and idempotent values are independent snapshots."""
        user_only = merge_config({"rules": items}, {})
        project_only = merge_config({}, {"rules": items})
        idempotent = merge_config({"rules": items}, {"rules": items})

        for merged in (user_only, project_only, idempotent):
            assert merged["rules"] is not items
            assert merged["rules"][0] is not items[0]

    @given(_nested_dict(), _nested_dict())
    @settings(max_examples=200)
    def test_merge_cfg_idempotent(self, a: dict[str, Any], b: dict[str, Any]) -> None:
        assert merge_config(a, a) == a
        ab = merge_config(a, b)
        assert merge_config(ab, ab) == ab

    @given(_nested_dict(), _nested_dict(), _nested_dict())
    @settings(max_examples=200)
    def test_merge_cfg_associative(self, a: dict[str, Any], b: dict[str, Any], c: dict[str, Any]) -> None:
        left = merge_config(merge_config(a, b), c)
        right = merge_config(a, merge_config(b, c))
        assert left == right

    @given(_nested_dict())
    @settings(max_examples=200)
    def test_merge_cfg_left_identity(self, a: dict[str, Any]) -> None:
        assert merge_config({}, a) == a

    @given(_nested_dict())
    @settings(max_examples=200)
    def test_merge_cfg_right_identity(self, a: dict[str, Any]) -> None:
        assert merge_config(a, {}) == a

    @given(_nested_dict(), _nested_dict())
    @settings(max_examples=200)
    def test_merge_cfg_commutative_disjoint(self, a: dict[str, Any], b: dict[str, Any]) -> None:
        assume(set(a.keys()).isdisjoint(set(b.keys())))
        assert merge_config(a, b) == merge_config(b, a)

    @given(_simple_dict(), _simple_dict())
    @settings(max_examples=200)
    def test_merge_cfg_project_wins(self, a: dict[str, Any], b: dict[str, Any]) -> None:
        result = merge_config(a, b)
        for key in b:
            if not isinstance(b[key], dict):
                assert key in result
                assert result[key] == b[key]


# =========================================================================
# 2. Roundtrip serialization — to_dict / from_dict
# =========================================================================


class TestDiscoveredServiceRoundtrip:
    @given(
        name=st.text(min_size=1, max_size=80, alphabet=st.characters(blacklist_categories=["Cs"])),
        url=_text,
        api_docs_url=st.one_of(st.none(), _text),
        pricing_url=st.one_of(st.none(), _text),
        status=st.sampled_from(["active", "inactive", "unknown"]),
        description=st.one_of(st.none(), _text),
        source_engine=st.one_of(st.none(), _text),
    )
    @settings(max_examples=200)
    def test_discovered_service_roundtrip(
        self,
        name: str,
        url: str,
        api_docs_url: str | None,
        pricing_url: str | None,
        status: str,
        description: str | None,
        source_engine: str | None,
    ) -> None:
        svc = DiscoveredService(
            name=name,
            url=url,
            api_docs_url=api_docs_url,
            pricing_url=pricing_url,
            status=status,  # type: ignore[arg-type]
            description=description,
            source_engine=source_engine,
        )
        data = svc.to_dict()
        rt = DiscoveredService.from_dict(data)
        assert rt.name == svc.name
        assert rt.url == svc.url
        assert rt.status == svc.status

    @given(
        name=st.text(min_size=1, max_size=80, alphabet=st.characters(blacklist_categories=["Cs"])),
        url=_text,
    )
    @settings(max_examples=200)
    def test_discovered_service_roundtrip_minimal(self, name: str, url: str) -> None:
        svc = DiscoveredService(name=name, url=url)
        rt = DiscoveredService.from_dict(svc.to_dict())
        assert rt.name == name
        assert rt.url == url

    @given(name=_text, url=_text)
    @settings(max_examples=100)
    def test_discovered_service_to_dict_idempotent(self, name: str, url: str) -> None:
        svc = DiscoveredService(name=name, url=url)
        d1 = svc.to_dict()
        svc2 = DiscoveredService.from_dict(d1)
        d2 = svc2.to_dict()
        for k in d1:
            assert d2.get(k) == d1[k]


class TestDispatchResultRoundtrip:
    @given(
        ok=st.booleans(),
        kind=st.text(min_size=1, max_size=30, alphabet=st.characters(blacklist_categories=["Cs"])),
        name=st.text(min_size=1, max_size=30, alphabet=st.characters(blacklist_categories=["Cs"])),
        output=st.one_of(st.none(), st.integers(), _text, st.floats(allow_nan=False)),
    )
    @settings(max_examples=200)
    def test_dispatch_result_roundtrip(self, ok: bool, kind: str, name: str, output: Any) -> None:
        r = DispatchResult(ok=ok, kind=kind, name=name, output=output)
        d = r.to_dict()
        assert d["ok"] == ok
        assert d["kind"] == kind
        assert d["name"] == name
        assert d["output"] == output


class TestChatMessageRoundtrip:
    @given(
        role=st.sampled_from(["system", "user", "assistant", "tool"]),
        content=st.text(min_size=0, max_size=500, alphabet=st.characters(blacklist_categories=["Cs"])),
    )
    @settings(max_examples=200)
    def test_chat_message_roundtrip(self, role: str, content: str) -> None:
        msg = ChatMessage(role=role, content=content)  # type: ignore[arg-type]
        data = {"role": msg.role, "content": msg.content}
        rt = ChatMessage.from_dict(data)
        assert rt.role == role
        assert rt.content == content

    @given(
        role=st.sampled_from(["system", "user", "assistant", "tool"]),
        content=st.text(max_size=200, alphabet=st.characters(blacklist_categories=["Cs"])),
    )
    @settings(max_examples=100)
    def test_chat_message_to_dict_from_dict_idempotent(self, role: str, content: str) -> None:
        msg = ChatMessage(role=role, content=content)  # type: ignore[arg-type]
        data = {"role": msg.role, "content": msg.content}
        rt = ChatMessage.from_dict(data)
        assert rt.role == msg.role
        assert rt.content == msg.content


class TestMemoryRoundtrip:
    @given(
        skepticism=st.integers(min_value=1, max_value=5),
        literalism=st.integers(min_value=1, max_value=5),
        empathy=st.integers(min_value=1, max_value=5),
    )
    @settings(max_examples=200)
    def test_disposition_roundtrip(self, skepticism: int, literalism: int, empathy: int) -> None:
        disp = Disposition(skepticism=skepticism, literalism=literalism, empathy=empathy)
        rt = Disposition.from_dict(disp.to_dict())
        assert rt.skepticism == disp.skepticism
        assert rt.literalism == disp.literalism
        assert rt.empathy == disp.empathy

    @given(
        bank_id=st.text(min_size=1, max_size=40, alphabet=st.characters(blacklist_categories=["Cs"])),
        mission=_text,
        directives=st.lists(_short_text, max_size=5),
        skepticism=st.integers(min_value=1, max_value=5),
        literalism=st.integers(min_value=1, max_value=5),
        empathy=st.integers(min_value=1, max_value=5),
    )
    @settings(max_examples=200)
    def test_memory_bank_config_roundtrip(
        self,
        bank_id: str,
        mission: str,
        directives: list[str],
        skepticism: int,
        literalism: int,
        empathy: int,
    ) -> None:
        disp = Disposition(skepticism=skepticism, literalism=literalism, empathy=empathy)
        cfg = MemoryBankConfig(bank_id=bank_id, mission=mission, directives=directives, disposition=disp)
        rt = MemoryBankConfig.from_dict(cfg.to_dict())
        assert rt.bank_id == cfg.bank_id
        assert rt.mission == cfg.mission
        assert rt.directives == cfg.directives
        assert rt.disposition.skepticism == cfg.disposition.skepticism


class TestAgentBehaviorRoundtrip:
    @given(
        role=st.one_of(st.none(), _short_text),
        goal=st.one_of(st.none(), _text),
        backstory=st.one_of(st.none(), _text),
        tdd_enforced=st.booleans(),
        session_persistence=st.booleans(),
        self_directed_work=st.booleans(),
        max_retries=st.integers(min_value=0, max_value=10),
    )
    @settings(max_examples=200)
    def test_agent_behavior_roundtrip(
        self,
        role: str | None,
        goal: str | None,
        backstory: str | None,
        tdd_enforced: bool,
        session_persistence: bool,
        self_directed_work: bool,
        max_retries: int,
    ) -> None:
        ab = AgentBehavior(
            role=role,
            goal=goal,
            backstory=backstory,
            tdd_enforced=tdd_enforced,
            session_persistence=session_persistence,
            self_directed_work=self_directed_work,
            max_retries=max_retries,
        )
        rt = AgentBehavior.from_dict(ab.to_dict())
        assert rt.role == ab.role
        assert rt.goal == ab.goal
        assert rt.tdd_enforced == ab.tdd_enforced
        assert rt.session_persistence == ab.session_persistence
        assert rt.self_directed_work == ab.self_directed_work
        assert rt.max_retries == ab.max_retries


class TestEntityGraphRoundtrip:
    @given(
        node_id=st.text(min_size=1, max_size=40, alphabet=st.characters(blacklist_categories=["Cs"])),
        name=st.text(min_size=1, max_size=80, alphabet=st.characters(blacklist_categories=["Cs"])),
        entity_type=st.sampled_from(["organization", "person", "location", "asset"]),
    )
    @settings(max_examples=100)
    def test_entity_graph_roundtrip(self, node_id: str, name: str, entity_type: str) -> None:
        eg = EntityGraph()
        eg.add_node(EntityNode(id=node_id, name=name, entity_type=entity_type))
        d = eg.to_dict()
        rt = EntityGraph.from_dict(d)
        node = rt.get_node(node_id)
        assert node is not None
        assert node.id == node_id
        assert node.name == name
        assert node.entity_type == entity_type


class TestMentalModelRoundtrip:
    @given(
        subject=st.text(min_size=1, max_size=80, alphabet=st.characters(blacklist_categories=["Cs"])),
        content=_text,
        priority=st.integers(min_value=1, max_value=10),
        tags=st.lists(_short_text, max_size=5),
    )
    @settings(max_examples=200)
    def test_mental_model_roundtrip(self, subject: str, content: str, priority: int, tags: list[str]) -> None:
        mm = MentalModel(subject=subject, content=content, priority=priority, tags=tags)
        rt = MentalModel.from_dict(mm.to_dict())
        assert rt.subject == mm.subject
        assert rt.content == mm.content
        assert rt.priority == mm.priority
        assert rt.tags == mm.tags

    @given(
        subject=st.text(min_size=1, max_size=80, alphabet=st.characters(blacklist_categories=["Cs"])),
        content=_text,
    )
    @settings(max_examples=100)
    def test_mental_model_double_roundtrip_stable(self, subject: str, content: str) -> None:
        mm = MentalModel(subject=subject, content=content, priority=5, tags=[])
        d1 = mm.to_dict()
        mm2 = MentalModel.from_dict(d1)
        d2 = mm2.to_dict()
        for k in d1:
            assert d2.get(k) == d1[k]


# =========================================================================
# 3. Commutativity for merge_catalog
# =========================================================================


class TestMergeCatalogCommutativity:
    @given(
        name_a=st.text(min_size=1, max_size=40, alphabet=st.characters(blacklist_categories=["Cs"])),
        name_b=st.text(min_size=1, max_size=40, alphabet=st.characters(blacklist_categories=["Cs"])),
    )
    @settings(max_examples=100)
    def test_merge_catalog_commutative_disjoint(self, name_a: str, name_b: str) -> None:
        assume(name_a != name_b)
        cat_a = ServiceCatalog()
        cat_a.add(DiscoveredService(name=name_a, url=f"https://{name_a}.com"))
        cat_b = ServiceCatalog()
        cat_b.add(DiscoveredService(name=name_b, url=f"https://{name_b}.com"))
        target = ServiceCatalog()
        merge_catalog(target, cat_a)
        merge_catalog(target, cat_b)
        assert target.get(name_a) is not None
        assert target.get(name_b) is not None


# =========================================================================
# 4. AgenticResearchContext — roundtrip + merge associativity
# =========================================================================


class TestAgenticContextProperties:
    @given(
        query=_text,
        items=st.lists(
            st.builds(
                ResearchContextItem,
                claim=_text,
                confidence=_float01,
                finding_id=_short_text,
                tags=st.lists(_short_text, max_size=5),
            ),
            min_size=0,
            max_size=10,
        ),
        overall_confidence=_float01,
        source_count=st.integers(min_value=0, max_value=100),
        freshness_score=_float01,
        caveats=st.lists(_text, max_size=5),
    )
    @settings(max_examples=200)
    def test_context_roundtrip_json(
        self,
        query: str,
        items: list[ResearchContextItem],
        overall_confidence: float,
        source_count: int,
        freshness_score: float,
        caveats: list[str],
    ) -> None:
        ctx = AgenticResearchContext(
            query=query,
            items=items,
            overall_confidence=overall_confidence,
            source_count=source_count,
            freshness_score=freshness_score,
            caveats=caveats,
        )
        raw = ctx.model_dump_json()
        rt = AgenticResearchContext.model_validate_json(raw)
        assert rt.query == ctx.query
        assert rt.overall_confidence == ctx.overall_confidence
        assert rt.source_count == ctx.source_count
        assert len(rt.items) == len(ctx.items)

    @given(
        items_a=st.lists(
            st.builds(ResearchContextItem, claim=_text, confidence=_float01, finding_id=_short_text),
            min_size=0,
            max_size=5,
        ),
        items_b=st.lists(
            st.builds(ResearchContextItem, claim=_text, confidence=_float01, finding_id=_short_text),
            min_size=0,
            max_size=5,
        ),
    )
    @settings(max_examples=100)
    def test_context_merge_idempotent(
        self, items_a: list[ResearchContextItem], items_b: list[ResearchContextItem]
    ) -> None:
        ctx_a = AgenticResearchContext(query="q", items=items_a)
        ctx_b = AgenticResearchContext(query="r", items=items_b)
        inj = AgenticContextInjector()
        m1 = inj.merge_contexts([ctx_a, ctx_b])
        m2 = inj.merge_contexts([ctx_a, ctx_b])
        assert m1.overall_confidence == m2.overall_confidence
        assert m1.source_count == m2.source_count
        assert len(m1.items) == len(m2.items)


# =========================================================================
# 5. parse_tool_calls — roundtrip-associative with JSON serialisation
# =========================================================================


class TestParseToolCallsProperties:
    @given(json_str=st.text(min_size=0, max_size=100, alphabet=st.characters(blacklist_categories=["Cs"])))
    @settings(max_examples=200)
    def test_parse_non_json_returns_empty(self, json_str: str) -> None:
        calls = parse_tool_calls(json_str)
        assert isinstance(calls, list)

    @given(
        kind=st.sampled_from(["role", "collection", "mcp", "skill"]),
        name=st.text(min_size=1, max_size=30, alphabet=st.characters(blacklist_categories=["Cs"])),
        value=st.integers(min_value=0, max_value=100),
    )
    @settings(max_examples=200)
    def test_parse_valid_dict_tool_call(self, kind: str, name: str, value: int) -> None:
        data: dict[str, object] = {"kind": kind, "name": name, "args": {"v": value}}
        calls = parse_tool_calls(data)
        assert len(calls) == 1
        assert calls[0].kind == kind
        assert calls[0].name == name

    @given(
        kind=st.sampled_from(["role", "collection", "mcp", "skill"]),
        name=st.text(min_size=1, max_size=30, alphabet=st.characters(blacklist_categories=["Cs"])),
    )
    @settings(max_examples=200)
    def test_parse_valid_json_string(self, kind: str, name: str) -> None:
        data = {"kind": kind, "name": name}
        raw = json.dumps(data)
        calls = parse_tool_calls(raw)
        assert len(calls) == 1
        assert calls[0].kind == kind
        assert calls[0].name == name


# =========================================================================
# 6. Idempotency for retry / repeated operations
# =========================================================================


class TestIdempotencyProperties:
    @given(name=_text, url=_text)
    @settings(max_examples=200)
    def test_to_dict_from_dict_idempotent_discovered_service(self, name: str, url: str) -> None:
        svc = DiscoveredService(name=name, url=url)
        d1 = svc.to_dict()
        svc2 = DiscoveredService.from_dict(d1)
        d2 = svc2.to_dict()
        svc3 = DiscoveredService.from_dict(d2)
        d3 = svc3.to_dict()
        for key in d1:
            assert d3.get(key) == d1[key]

    @given(_nested_dict(), _nested_dict())
    @settings(max_examples=100)
    def test_merge_config_double_apply_idempotent(self, a: dict[str, Any], b: dict[str, Any]) -> None:
        m1 = merge_config(a, b)
        m2 = merge_config(a, b)
        assert m1 == m2
        assert merge_config(m1, b) == m1

    @given(_nested_dict(), _nested_dict(), _nested_dict())
    @settings(max_examples=100)
    def test_merge_config_double_merge_stable(self, a: dict[str, Any], b: dict[str, Any], c: dict[str, Any]) -> None:
        m1 = merge_config(merge_config(a, b), c)
        m2 = merge_config(merge_config(a, b), c)
        assert m1 == m2


# =========================================================================
# 7. merge_catalog associativity-like property
# =========================================================================


class TestMergeCatalogAssociativity:
    @given(
        items_a=st.lists(
            st.tuples(
                st.text(min_size=1, max_size=30, alphabet=st.characters(blacklist_categories=["Cs"])),
                _text,
            ),
            min_size=0,
            max_size=5,
            unique_by=lambda t: t[0],
        ).map(lambda pairs: [DiscoveredService(name=n, url=u) for n, u in pairs]),
    )
    @settings(max_examples=100)
    def test_merge_catalog_idempotent(self, items_a: list[DiscoveredService]) -> None:
        src = ServiceCatalog()
        for s in items_a:
            src.add(s)
        t1 = ServiceCatalog()
        merge_catalog(t1, src)
        merge_catalog(t1, src)
        assert len(t1.services) == len(src.services) == len(items_a)
        for s in items_a:
            assert s.name in t1.services


# =========================================================================
# 8. ServiceCatalog load/save roundtrip
# =========================================================================


class TestServiceCatalogSaveLoadRoundtrip:
    @given(
        items=st.lists(
            st.tuples(
                st.text(min_size=1, max_size=30, alphabet=st.characters(blacklist_categories=["Cs"])),
                _text,
            ),
            min_size=0,
            max_size=10,
        ).map(lambda pairs: [DiscoveredService(name=n, url=u) for n, u in pairs]),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_catalog_save_load_roundtrip(self, items: list[DiscoveredService], tmp_path: Any) -> None:
        cat_path = tmp_path / "catalog.json"
        cat_path.unlink(missing_ok=True)
        cat = ServiceCatalog(path=str(cat_path))
        for s in items:
            cat.add(s)
        cat.save()
        reloaded = ServiceCatalog(path=str(cat_path))
        assert len(reloaded.services) == len({service.name for service in items})
        for s in items:
            assert s.name in reloaded.services

    @given(
        items=st.lists(
            st.tuples(
                st.text(min_size=1, max_size=30, alphabet=st.characters(blacklist_categories=["Cs"])),
                _text,
            ),
            min_size=0,
            max_size=10,
        ).map(lambda pairs: [DiscoveredService(name=n, url=u) for n, u in pairs]),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_catalog_yaml_roundtrip(self, items: list[DiscoveredService], tmp_path: Any) -> None:
        cat_path = tmp_path / "catalog.yml"
        cat_path.unlink(missing_ok=True)
        cat = ServiceCatalog(path=str(cat_path))
        for s in items:
            cat.add(s)
        cat.save()
        reloaded = ServiceCatalog(path=str(cat_path))
        for s in items:
            assert s.name in reloaded.services


# =========================================================================
# 9. merge_config project bias (project always wins)
# =========================================================================


class TestMergeConfigProjectBias:
    @given(
        shared=st.dictionaries(
            keys=st.text(min_size=1, max_size=10, alphabet=st.characters(blacklist_categories=["Cs"])),
            values=st.integers(min_value=0, max_value=100),
            min_size=0,
            max_size=5,
        ),
    )
    @settings(max_examples=100)
    def test_merge_config_overwrite_scalar(self, shared: dict[str, int]) -> None:
        user: dict[str, Any] = {k: v for k, v in shared.items()}
        project: dict[str, Any] = {k: v + 1000 for k, v in shared.items()}
        result = merge_config(user, project)
        for k in project:
            assert result[k] == project[k]
            assert result[k] != user[k]
