"""Deep edge-case tests for POST/GET /api/dispatch router.
Covers: recent-buffer clamping, parse_tool_calls boundary conditions,
invalid-shape bodies, capability dispatch null/invalid args, mixed
valid/invalid tool_call lists, and per-call name/kind coercion.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.dispatch.capabilities import CapabilityRegistry, CollectionMeta
from general_ludd.dispatch.dynamic_dispatcher import UNRESTRICTED_ROLE


def _mini_registry() -> CapabilityRegistry:
    reg = CapabilityRegistry()
    reg.add_collection(
        CollectionMeta(
            name="echo",
            namespace="gludd",
            version="0.1.0",
            description="test",
            tags=frozenset({"echo"}),
            raw_tags=["echo"],
        )
    )
    return reg


def _echo_handler(name: str, args: dict[str, object]) -> dict[str, object]:
    return {"name": name, "args": args}


def _client(
    *, reg: CapabilityRegistry | None = None, role: str | object | None = UNRESTRICTED_ROLE, col: Callable | None = None
) -> TestClient:
    from general_ludd.routers.dispatch import register

    app = FastAPI()
    register(app, {}, capability_registry=reg, collection_handler=col or _echo_handler, role=role)
    return TestClient(app, raise_server_exceptions=False)


# ── Recent buffer limit clamping ─────────────────────────────────────


class TestRecentBufferEdgeCases:
    def test_recent_negative_limit_clamped_to_one(self):
        client = _client(reg=_mini_registry())
        client.post("/api/dispatch", json={"capability": "echo", "action": "ping", "args": {}})
        resp = client.get("/api/dispatch/recent", params={"limit": -5})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["recent"]) == 1

    def test_recent_zero_limit_clamped_to_one(self):
        client = _client(reg=_mini_registry())
        client.post("/api/dispatch", json={"capability": "echo", "action": "ping", "args": {}})
        resp = client.get("/api/dispatch/recent", params={"limit": 0})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["recent"]) == 1

    def test_recent_limit_exceeding_max_clamped(self):
        client = _client(reg=_mini_registry())
        for i in range(3):
            client.post("/api/dispatch", json={"capability": "echo", "action": f"a{i}", "args": {}})
        resp = client.get("/api/dispatch/recent", params={"limit": 9999})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["recent"]) <= 50

    def test_recent_buffer_wraps_at_50(self):
        client = _client(reg=_mini_registry())
        for i in range(55):
            client.post("/api/dispatch", json={"capability": "echo", "action": f"a{i}", "args": {}})
        resp = client.get("/api/dispatch/recent", params={"limit": 50})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 50
        assert len(data["recent"]) == 50

    def test_recent_empty_before_any_dispatch(self):
        client = _client(reg=_mini_registry())
        resp = client.get("/api/dispatch/recent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["recent"] == []

    def test_recent_limit_as_string_converted(self):
        client = _client(reg=_mini_registry())
        client.post("/api/dispatch", json={"capability": "echo", "action": "ping", "args": {}})
        resp = client.get("/api/dispatch/recent", params={"limit": "5"})
        assert resp.status_code == 200


# ── parse_tool_calls boundary conditions ───────────────────────────────


class TestParseToolCallsBoundary:
    def test_body_is_json_list_returns_422(self):
        client = _client()
        resp = client.post("/api/dispatch", json=[1, 2, 3])
        assert resp.status_code == 422

    def test_body_is_json_number_returns_422(self):
        client = _client()
        resp = client.post("/api/dispatch", json=42)
        assert resp.status_code == 422

    def test_body_is_json_null_returns_422(self):
        client = _client()
        resp = client.post("/api/dispatch", json=None)
        assert resp.status_code == 422

    def test_body_is_json_bool_returns_422(self):
        client = _client()
        resp = client.post("/api/dispatch", json=True)
        assert resp.status_code == 422

    def test_tool_calls_not_a_list_returns_422(self):
        client = _client()
        resp = client.post("/api/dispatch", json={"tool_calls": "not_a_list"})
        assert resp.status_code == 422

    def test_tool_calls_is_a_dict_returns_422(self):
        client = _client()
        resp = client.post("/api/dispatch", json={"tool_calls": {"kind": "mcp", "name": "x"}})
        assert resp.status_code == 422

    def test_tool_calls_item_is_not_a_dict_skipped(self):
        client = _client(role=UNRESTRICTED_ROLE, col=_echo_handler)
        resp = client.post(
            "/api/dispatch",
            json={
                "tool_calls": [
                    "not-a-dict",
                    {"kind": "collection", "name": "gludd.echo.a", "args": {}},
                ]
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["ok_count"] == 1

    def test_all_tool_calls_invalid_returns_422(self):
        client = _client()
        resp = client.post("/api/dispatch", json={"tool_calls": [None, 123, "hello"]})
        assert resp.status_code == 422

    def test_tool_call_with_only_kind_no_name_skipped(self):
        client = _client()
        resp = client.post("/api/dispatch", json={"tool_calls": [{"kind": "mcp"}]})
        assert resp.status_code == 422

    def test_tool_call_kind_is_none_still_parses(self):
        client = _client(col=_echo_handler, role=UNRESTRICTED_ROLE)
        resp = client.post("/api/dispatch", json={"kind": None, "name": "some_tool", "args": {}})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok_count"] == 0
        assert data["error_count"] == 1

    def test_tool_call_name_is_int_skipped(self):
        client = _client(col=_echo_handler, role=UNRESTRICTED_ROLE)
        resp = client.post("/api/dispatch", json={"kind": "collection", "name": 42, "args": {}})
        assert resp.status_code == 422

    def test_tool_call_name_is_none_skipped(self):
        client = _client()
        resp = client.post("/api/dispatch", json={"tool_calls": [{"kind": "collection", "name": None, "args": {}}]})
        assert resp.status_code == 422


# ── Name truncation and args coercion ──────────────────────────────────


class TestNameAndArgsEdgeCases:
    def test_name_exceeds_256_chars_truncated(self):
        long_name = "ns." + "a" * 300
        client = _client(col=_echo_handler, role=UNRESTRICTED_ROLE)
        resp = client.post("/api/dispatch", json={"kind": "collection", "name": long_name, "args": {}})
        data = resp.json()
        dispatched_name = data["results"][0]["name"]
        assert len(dispatched_name) == 256
        assert dispatched_name == long_name[:256]

    def test_name_exactly_256_chars_preserved(self):
        name = "ns." + "b" * 253
        assert len(name) == 256
        client = _client(col=_echo_handler, role=UNRESTRICTED_ROLE)
        resp = client.post("/api/dispatch", json={"kind": "collection", "name": name, "args": {}})
        data = resp.json()
        assert len(data["results"][0]["name"]) == 256

    def test_args_is_string_treated_as_empty(self):
        client = _client(col=_echo_handler, role=UNRESTRICTED_ROLE)
        resp = client.post("/api/dispatch", json={"kind": "collection", "name": "gludd.echo.a", "args": "not-a-dict"})
        data = resp.json()
        assert data["results"][0]["output"]["args"] == {}

    def test_args_is_int_treated_as_empty(self):
        client = _client(col=_echo_handler, role=UNRESTRICTED_ROLE)
        resp = client.post("/api/dispatch", json={"kind": "collection", "name": "gludd.echo.b", "args": 42})
        data = resp.json()
        assert data["results"][0]["output"]["args"] == {}

    def test_args_is_none_treated_as_empty(self):
        client = _client(col=_echo_handler, role=UNRESTRICTED_ROLE)
        resp = client.post("/api/dispatch", json={"kind": "collection", "name": "gludd.echo.c", "args": None})
        data = resp.json()
        assert data["results"][0]["output"]["args"] == {}

    def test_args_missing_defaults_to_empty(self):
        client = _client(col=_echo_handler, role=UNRESTRICTED_ROLE)
        resp = client.post("/api/dispatch", json={"kind": "collection", "name": "gludd.echo.d"})
        data = resp.json()
        assert data["results"][0]["output"]["args"] == {}

    def test_kind_string_exceeding_64_chars_truncated(self):
        long_kind = "x" * 100
        client = _client(col=_echo_handler, role=UNRESTRICTED_ROLE)
        resp = client.post("/api/dispatch", json={"kind": long_kind, "name": "gludd.echo.x", "args": {}})
        data = resp.json()
        assert len(data["results"][0]["kind"]) <= 64


# ── Mixed valid/invalid and kind-specific dispatch ─────────────────────


class TestMixedDispatch:
    def test_mixed_valid_invalid_items(self):
        client = _client(role=UNRESTRICTED_ROLE, col=_echo_handler)
        resp = client.post(
            "/api/dispatch",
            json={
                "tool_calls": [
                    {"kind": "collection", "name": "gludd.echo.a", "args": {"x": 1}},
                    {"kind": "bogus-zzz", "name": "fail.tool", "args": {}},
                ]
            },
        )
        data = resp.json()
        assert data["count"] == 2
        assert data["ok_count"] == 1
        assert data["error_count"] == 1

    def test_only_role_kind_with_handler(self):
        _client(role=UNRESTRICTED_ROLE, col=None)
        from general_ludd.routers.dispatch import register as _reg

        app = FastAPI()
        _reg(app, {}, role_handler=_echo_handler, role=UNRESTRICTED_ROLE)
        tc = TestClient(app, raise_server_exceptions=False)
        resp = tc.post("/api/dispatch", json={"kind": "role", "name": "planner", "args": {"task": "x"}})
        data = resp.json()
        assert data["ok_count"] == 1
        assert data["results"][0]["output"]["name"] == "planner"

    def test_only_skill_kind_with_handler(self):
        from general_ludd.routers.dispatch import register as _reg

        app = FastAPI()
        _reg(app, {}, skill_handler=_echo_handler, role=UNRESTRICTED_ROLE)
        tc = TestClient(app, raise_server_exceptions=False)
        resp = tc.post("/api/dispatch", json={"kind": "skill", "name": "guardrail-pattern", "args": {}})
        data = resp.json()
        assert data["ok_count"] == 1
        assert data["results"][0]["output"]["name"] == "guardrail-pattern"


# ── Capability dispatch edge cases ─────────────────────────────────────


class TestCapabilityDispatchEdges:
    def test_capability_payload_empty_defaults(self):
        client = _client(reg=_mini_registry())
        resp = client.post("/api/dispatch", json={"capability": "echo", "action": "ping"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok_count"] == 1

    def test_capability_action_cannot_be_empty(self):
        client = _client(reg=_mini_registry())
        resp = client.post("/api/dispatch", json={"capability": "echo", "action": ""})
        assert resp.status_code == 422

    def test_capability_action_not_a_string(self):
        client = _client(reg=_mini_registry())
        resp = client.post("/api/dispatch", json={"capability": "echo", "action": 123})
        assert resp.status_code == 422

    def test_capability_args_non_dict_coerced_to_empty(self):
        client = _client(reg=_mini_registry())
        resp = client.post("/api/dispatch", json={"capability": "echo", "action": "ping", "args": "not-dict"})
        data = resp.json()
        assert data["ok_count"] == 1
        assert data["results"][0]["output"]["args"] == {}

    def test_capability_action_with_spaces(self):
        client = _client(reg=_mini_registry())
        resp = client.post("/api/dispatch", json={"capability": "echo", "action": " ping ", "args": {}})
        data = resp.json()
        assert data["ok_count"] == 1


# ── Response shape invariants ──────────────────────────────────────────


class TestResponseShapeInvariants:
    def test_count_equals_ok_plus_error(self):
        client = _client(role=UNRESTRICTED_ROLE, col=_echo_handler)
        resp = client.post("/api/dispatch", json={"kind": "collection", "name": "gludd.echo.a", "args": {}})
        data = resp.json()
        assert data["count"] == data["ok_count"] + data["error_count"]

    def test_single_error_result_has_null_output(self):
        client = _client()
        resp = client.post("/api/dispatch", json={"tool_calls": [{"kind": "bogus-kind", "name": "tool", "args": {}}]})
        data = resp.json()
        assert data["error_count"] == 1
        assert data["results"][0]["output"] is None
        assert data["results"][0]["error"] is not None

    def test_mixed_ok_error_counts_correct(self):
        client = _client(role=UNRESTRICTED_ROLE, col=_echo_handler)
        resp = client.post(
            "/api/dispatch",
            json={
                "tool_calls": [
                    {"kind": "collection", "name": "gludd.echo.ok", "args": {}},
                    {"kind": "collection", "name": "gludd.echo.ok2", "args": {}},
                    {"kind": "bogus-kind-zz", "name": "fail.tool", "args": {}},
                ]
            },
        )
        data = resp.json()
        assert data["count"] == 3
        assert data["ok_count"] == 2
        assert data["error_count"] == 1

    def test_available_endpoint_no_handlers(self):
        from general_ludd.routers.dispatch import register as _reg

        app = FastAPI()
        _reg(app, {})
        tc = TestClient(app, raise_server_exceptions=False)
        resp = tc.get("/api/dispatch/available")
        data = resp.json()
        assert data["registered_kinds"] == []

    def test_capability_endpoint_without_registry_404(self):
        from general_ludd.routers.dispatch import register as _reg

        app = FastAPI()
        _reg(app, {})
        tc = TestClient(app, raise_server_exceptions=False)
        resp = tc.post("/api/dispatch/capability", json={"capability": "x"})
        assert resp.status_code == 404


# ── Concurrent and order-preserving dispatch ───────────────────────────


class TestDispatchOrdering:
    def test_results_order_matches_input_order(self):
        client = _client(role=UNRESTRICTED_ROLE, col=_echo_handler)
        names = ["gludd.echo.first", "gludd.echo.second", "gludd.echo.third"]
        resp = client.post(
            "/api/dispatch", json={"tool_calls": [{"kind": "collection", "name": n, "args": {}} for n in names]}
        )
        data = resp.json()
        result_names = [r["name"] for r in data["results"]]
        assert result_names == names

    def test_results_order_preserved_with_mixed_ok_error(self):
        client = _client(role=UNRESTRICTED_ROLE, col=_echo_handler)
        resp = client.post(
            "/api/dispatch",
            json={
                "tool_calls": [
                    {"kind": "collection", "name": "gludd.echo.a", "args": {"i": 1}},
                    {"kind": "bogus", "name": "fail", "args": {}},
                    {"kind": "collection", "name": "gludd.echo.c", "args": {"i": 3}},
                ]
            },
        )
        data = resp.json()
        names = [r["name"] for r in data["results"]]
        assert names == ["gludd.echo.a", "fail", "gludd.echo.c"]


# ── JSON string body variants ──────────────────────────────────────────


class TestJsonStringBodies:
    def test_json_string_with_bom_prefix(self):
        client = _client(col=_echo_handler, role=UNRESTRICTED_ROLE)
        payload = json.dumps({"kind": "collection", "name": "gludd.echo.bom", "args": {}})
        resp = client.post("/api/dispatch", content=payload, headers={"Content-Type": "application/json"})
        assert resp.status_code == 200

    def test_json_string_leading_whitespace_trimmed(self):
        client = _client(col=_echo_handler, role=UNRESTRICTED_ROLE)
        payload = "  \n  " + json.dumps({"kind": "collection", "name": "gludd.echo.ws", "args": {}})
        resp = client.post("/api/dispatch", content=payload, headers={"Content-Type": "application/json"})
        assert resp.status_code == 200
        assert resp.json()["ok_count"] == 1

    def test_non_json_string_body_returns_422(self):
        client = _client()
        resp = client.post("/api/dispatch", content="not json at all", headers={"Content-Type": "text/plain"})
        assert resp.status_code == 422

    def test_empty_string_body_returns_422(self):
        client = _client()
        resp = client.post("/api/dispatch", content="", headers={"Content-Type": "application/json"})
        assert resp.status_code == 422


# ── Extreme edge cases ─────────────────────────────────────────────────


class TestExtremeEdges:
    def test_deeply_nested_args_dict_survives(self):
        deep = {"a": {"b": {"c": {"d": {"e": {"f": {"g": 42}}}}}}}
        client = _client(col=_echo_handler, role=UNRESTRICTED_ROLE)
        resp = client.post("/api/dispatch", json={"kind": "collection", "name": "gludd.echo.deep", "args": deep})
        data = resp.json()
        assert data["results"][0]["output"]["args"] == deep

    def test_args_with_special_json_values(self):
        special = {"null": None, "bool_true": True, "bool_false": False, "float": 3.14}
        client = _client(col=_echo_handler, role=UNRESTRICTED_ROLE)
        resp = client.post("/api/dispatch", json={"kind": "collection", "name": "gludd.echo.special", "args": special})
        data = resp.json()
        assert data["results"][0]["output"]["args"] == special

    def test_empty_string_name_rejected(self):
        client = _client()
        resp = client.post("/api/dispatch", json={"tool_calls": [{"kind": "collection", "name": "", "args": {}}]})
        assert resp.status_code == 422

    def test_unicode_in_name_and_args(self):
        client = _client(col=_echo_handler, role=UNRESTRICTED_ROLE)
        resp = client.post(
            "/api/dispatch",
            json={
                "kind": "collection",
                "name": "gludd.echo.unicode",
                "args": {"key": "value😀", "emoji": "🚀"},
            },
        )
        data = resp.json()
        assert data["ok_count"] == 1
        assert data["results"][0]["output"]["args"]["emoji"] == "🚀"

    def test_capability_dispatch_with_extra_ignored_keys(self):
        client = _client(reg=_mini_registry())
        resp = client.post(
            "/api/dispatch",
            json={
                "capability": "echo",
                "action": "ping",
                "args": {},
                "unknown_field": "should-be-ignored",
                "another": 42,
            },
        )
        data = resp.json()
        assert data["ok_count"] == 1

    def test_recent_buffer_trimming_preserves_order(self):
        client = _client(reg=_mini_registry())
        for i in range(55):
            client.post("/api/dispatch", json={"capability": "echo", "action": f"mod{i:03d}", "args": {}})
        resp = client.get("/api/dispatch/recent", params={"limit": 5})
        data = resp.json()
        names = [r["name"] for r in data["recent"]]
        assert names == [
            "gludd.echo.mod050",
            "gludd.echo.mod051",
            "gludd.echo.mod052",
            "gludd.echo.mod053",
            "gludd.echo.mod054",
        ]

    def test_exactly_at_max_calls_with_role_kind(self):
        from general_ludd.routers.dispatch import register as _reg

        app = FastAPI()
        _reg(app, {}, role_handler=_echo_handler, role=UNRESTRICTED_ROLE)
        tc = TestClient(app, raise_server_exceptions=False)
        calls = [{"kind": "role", "name": f"role.{i}", "args": {}} for i in range(20)]
        resp = tc.post("/api/dispatch", json={"tool_calls": calls})
        assert resp.status_code == 200
        assert resp.json()["count"] == 20

    def test_capability_endpoint_route_by_collection(self):
        client = _client(reg=_mini_registry())
        resp = client.post("/api/dispatch/capability", json={"collection": "echo", "payload": {"x": 1}})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert len(data["matches"]) >= 1

    def test_capability_endpoint_collection_not_found(self):
        client = _client(reg=_mini_registry())
        resp = client.post("/api/dispatch/capability", json={"collection": "ghost.nope"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["matches"] == []
