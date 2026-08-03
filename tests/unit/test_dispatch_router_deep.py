"""Deep tests for the capability dispatch router (POST /api/dispatch).

Covers: unknown capability rejection, permission bypass paths, call-count
capping, concurrent dispatch, invalid payload rejection, per-kind dispatch,
response format validation, and handler timeout behaviour.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.dispatch.capabilities import CapabilityRegistry, CollectionMeta
from general_ludd.dispatch.dynamic_dispatcher import UNRESTRICTED_ROLE

# ── helpers ──────────────────────────────────────────────────────────────


def _build_registry() -> CapabilityRegistry:
    reg = CapabilityRegistry()
    for name, tags in [
        ("agent", frozenset({"deploy", "plan", "build"})),
        ("infra", frozenset({"deploy", "monitor"})),
        ("travel", frozenset({"travel", "flights", "hotels"})),
        ("chemistry", frozenset({"chemistry", "molecule", "reaction"})),
    ]:
        reg.add_collection(
            CollectionMeta(
                name=name,
                namespace="general_ludd",
                version="0.1.0",
                description=f"{name} collection",
                tags=tags,
                raw_tags=sorted(tags),
            )
        )
    return reg


def _mock_handler(name: str, args: dict[str, object]) -> dict[str, object]:
    return {"invoked": name, "args": args, "status": "ok"}


def _slow_handler(name: str, args: dict[str, object]) -> dict[str, object]:
    import time

    time.sleep(0.5)
    return {"invoked": name, "args": args, "status": "ok"}


async def _async_handler(name: str, args: dict[str, object]) -> dict[str, object]:
    await asyncio.sleep(0.01)
    return {"invoked": name, "args": args, "status": "ok"}


def _failing_handler(_name: str, _args: dict[str, object]) -> dict[str, object]:
    raise RuntimeError("handler exploded")


def _make_client(
    registry: CapabilityRegistry | None = None,
    role_handler: Callable[[str, dict[str, object]], object] | None = None,
    mcp_handler: Callable[[str, dict[str, object]], object] | None = None,
    skill_handler: Callable[[str, dict[str, object]], object] | None = None,
    collection_handler: Callable[[str, dict[str, object]], object] | None = None,
    role: str | object | None = None,
) -> TestClient:
    from general_ludd.routers.dispatch import register

    app = FastAPI()
    register(
        app,
        {},
        capability_registry=registry,
        role_handler=role_handler,
        mcp_handler=mcp_handler,
        skill_handler=skill_handler,
        collection_handler=collection_handler,
        role=role,
    )
    return TestClient(app, raise_server_exceptions=False)


def _make_cap_client(
    registry: CapabilityRegistry | None = None,
    collection_handler: Callable[[str, dict[str, object]], object] | None = None,
    role: str | object | None = UNRESTRICTED_ROLE,
) -> TestClient:
    return _make_client(registry=registry, collection_handler=collection_handler, role=role)


# ── Unknown capability rejection ───────────────────────────────────────


class TestUnknownCapabilityRejection:
    def test_capability_dispatch_unknown_tag_404(self):
        client = _make_cap_client(_build_registry(), _mock_handler)
        resp = client.post(
            "/api/dispatch",
            json={"capability": "nonexistent", "action": "do_something", "args": {}},
        )
        assert resp.status_code == 404
        assert "no collection found" in resp.json()["detail"]

    def test_capability_endpoint_unknown_tag_ok_false(self):
        client = _make_cap_client(_build_registry())
        resp = client.post("/api/dispatch/capability", json={"capability": "nonexistent"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["error"] is not None

    def test_unknown_kind_in_tool_call_fails_closed(self):
        client = _make_client(collection_handler=_mock_handler)
        resp = client.post(
            "/api/dispatch",
            json={"kind": "bogus_kind", "name": "some_tool", "args": {}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok_count"] == 0
        assert data["error_count"] == 1
        assert data["results"][0]["ok"] is False

    def test_unregistered_kind_no_handler(self):
        client = _make_client(collection_handler=_mock_handler)
        resp = client.post(
            "/api/dispatch",
            json={"tool_calls": [{"kind": "skill", "name": "some_skill", "args": {}}]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["results"][0]["ok"] is False


# ── Permission check bypass paths ──────────────────────────────────────


class TestPermissionBypassPaths:
    def test_none_role_denies_privileged_kind_collection(self):
        client = _make_client(
            collection_handler=_mock_handler,
            role=None,
        )
        resp = client.post(
            "/api/dispatch",
            json={"kind": "collection", "name": "some.collection.module", "args": {}},
        )
        data = resp.json()
        assert data["results"][0]["ok"] is False
        assert data["results"][0]["error"] == "capability_denied"

    def test_none_role_denies_privileged_kind_role(self):
        client = _make_client(role_handler=_mock_handler, role=None)
        resp = client.post(
            "/api/dispatch",
            json={"kind": "role", "name": "planner", "args": {}},
        )
        data = resp.json()
        assert data["results"][0]["ok"] is False
        assert data["results"][0]["error"] == "capability_denied"

    def test_unrestricted_role_bypasses_privileged_kinds(self):
        client = _make_client(
            collection_handler=_mock_handler,
            role=UNRESTRICTED_ROLE,
        )
        resp = client.post(
            "/api/dispatch",
            json={"kind": "collection", "name": "general_ludd.agent.init", "args": {}},
        )
        data = resp.json()
        assert data["results"][0]["ok"] is True

    def test_string_role_may_dispatch_correctly(self):
        client = _make_client(
            collection_handler=_mock_handler,
            role="self_improve_agent",
        )
        resp = client.post(
            "/api/dispatch",
            json={"kind": "collection", "name": "test.module", "args": {}},
        )
        data = resp.json()
        assert data["results"][0]["ok"] is True


# ── Rate limiting / call-count capping ─────────────────────────────────


class TestCallCountCapping:
    def test_exactly_20_calls_accepted(self):
        client = _make_client(collection_handler=_mock_handler)
        calls = [{"kind": "collection", "name": f"ns.coll.mod{i}", "args": {}} for i in range(20)]
        resp = client.post("/api/dispatch", json={"tool_calls": calls})
        assert resp.status_code == 200
        assert resp.json()["count"] == 20

    def test_21_calls_rejected_422(self):
        client = _make_client(collection_handler=_mock_handler)
        calls = [{"kind": "collection", "name": f"ns.coll.mod{i}", "args": {}} for i in range(21)]
        resp = client.post("/api/dispatch", json={"tool_calls": calls})
        assert resp.status_code == 422
        assert "exceeds" in resp.json()["detail"]

    def test_single_call_at_limit_is_fine(self):
        client = _make_client(mcp_handler=_mock_handler, role=UNRESTRICTED_ROLE)
        resp = client.post(
            "/api/dispatch",
            json={"kind": "mcp", "name": "simple_tool", "args": {}},
        )
        assert resp.status_code == 200

    def test_empty_tool_calls_list_422(self):
        client = _make_client()
        resp = client.post("/api/dispatch", json={"tool_calls": []})
        assert resp.status_code == 422

    def test_empty_body_without_capability_422(self):
        client = _make_client()
        resp = client.post("/api/dispatch", json={})
        assert resp.status_code == 422


# ── Concurrent dispatch requests ───────────────────────────────────────


class TestConcurrentDispatch:
    def test_multiple_kind_based_dispatch_is_sequential(self):
        client = _make_client(
            collection_handler=_mock_handler,
            mcp_handler=_mock_handler,
            role=UNRESTRICTED_ROLE,
        )
        resp = client.post(
            "/api/dispatch",
            json={
                "tool_calls": [
                    {"kind": "collection", "name": "ns.coll.a", "args": {"x": 1}},
                    {"kind": "mcp", "name": "mcp_tool", "args": {"y": 2}},
                    {"kind": "collection", "name": "ns.coll.b", "args": {"z": 3}},
                ]
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 3
        assert data["ok_count"] == 3
        assert data["error_count"] == 0

    def test_concurrent_clients_do_not_corrupt_each_other(self):
        """Multiple TestClients hitting the same server should not interfere."""
        reg = _build_registry()
        client = _make_cap_client(reg, _mock_handler)

        # sequential but overlapping requests
        r1 = client.post(
            "/api/dispatch",
            json={"capability": "travel", "action": "flight_search", "args": {"origin": "SFO"}},
        )
        r2 = client.post(
            "/api/dispatch",
            json={"capability": "chemistry", "action": "reaction_balance", "args": {"eq": "H2+O2"}},
        )
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["results"][0]["name"] == "general_ludd.travel.flight_search"
        assert r2.json()["results"][0]["name"] == "general_ludd.chemistry.reaction_balance"


# ── Invalid payload rejection ──────────────────────────────────────────


class TestInvalidPayloadRejection:
    def test_non_dict_args_in_capability_dispatch(self):
        """args that is not a dict should be treated as empty dict."""
        reg = _build_registry()
        client = _make_cap_client(reg, _mock_handler)
        resp = client.post(
            "/api/dispatch",
            json={"capability": "travel", "action": "flight_search", "args": [1, 2, 3]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok_count"] == 1
        assert data["results"][0]["output"]["args"] == {}

    def test_missing_action_in_capability_dispatch_422(self):
        client = _make_cap_client(_build_registry(), _mock_handler)
        resp = client.post("/api/dispatch", json={"capability": "travel"})
        assert resp.status_code == 422

    def test_empty_capability_string_422(self):
        client = _make_cap_client(_build_registry(), _mock_handler)
        resp = client.post("/api/dispatch", json={"capability": "", "action": "search"})
        assert resp.status_code == 422

    def test_non_string_capability_falls_through(self):
        """A non-string capability falls through to tool-call parsing (422)."""
        client = _make_cap_client(_build_registry(), _mock_handler)
        resp = client.post("/api/dispatch", json={"capability": 123, "action": "search"})
        assert resp.status_code == 422

    def test_no_capability_registry_503(self):
        client = _make_cap_client(registry=None)
        resp = client.post(
            "/api/dispatch",
            json={"capability": "travel", "action": "search"},
        )
        assert resp.status_code == 503

    def test_missing_kind_and_name_422(self):
        client = _make_client()
        resp = client.post("/api/dispatch", json={"args": {"some": "data"}})
        assert resp.status_code == 422

    def test_no_capability_or_collection_in_cap_endpoint_422(self):
        client = _make_cap_client(_build_registry())
        resp = client.post("/api/dispatch/capability", json={"payload": {"x": 1}})
        assert resp.status_code == 422


# ── Dispatch to each registered capability ─────────────────────────────


class TestDispatchToEachCapability:
    def test_dispatch_by_travel_tag(self):
        client = _make_cap_client(_build_registry(), _mock_handler)
        resp = client.post(
            "/api/dispatch",
            json={"capability": "travel", "action": "flight_search", "args": {"origin": "SFO"}},
        )
        data = resp.json()
        assert data["results"][0]["ok"] is True
        assert data["results"][0]["name"] == "general_ludd.travel.flight_search"

    def test_dispatch_by_flights_tag(self):
        client = _make_cap_client(_build_registry(), _mock_handler)
        resp = client.post(
            "/api/dispatch",
            json={"capability": "flights", "action": "flight_search", "args": {}},
        )
        data = resp.json()
        assert data["results"][0]["name"] == "general_ludd.travel.flight_search"

    def test_dispatch_by_hotels_tag(self):
        client = _make_cap_client(_build_registry(), _mock_handler)
        resp = client.post(
            "/api/dispatch",
            json={"capability": "hotels", "action": "hotel_search", "args": {}},
        )
        data = resp.json()
        assert data["results"][0]["name"] == "general_ludd.travel.hotel_search"

    def test_dispatch_by_chemistry_tag(self):
        client = _make_cap_client(_build_registry(), _mock_handler)
        resp = client.post(
            "/api/dispatch",
            json={"capability": "chemistry", "action": "mol_weight", "args": {"formula": "H2O"}},
        )
        data = resp.json()
        assert data["results"][0]["name"] == "general_ludd.chemistry.mol_weight"

    def test_dispatch_by_molecule_tag(self):
        client = _make_cap_client(_build_registry(), _mock_handler)
        resp = client.post(
            "/api/dispatch",
            json={"capability": "molecule", "action": "parse_smiles", "args": {}},
        )
        data = resp.json()
        assert data["results"][0]["name"] == "general_ludd.chemistry.parse_smiles"

    def test_dispatch_by_deploy_tag_finds_multiple_collections(self):
        client = _make_cap_client(_build_registry(), _mock_handler)
        resp = client.post(
            "/api/dispatch",
            json={"capability": "deploy", "action": "deploy_app", "args": {}},
        )
        data = resp.json()
        assert data["ok_count"] == 1
        assert "deploy_app" in data["results"][0]["name"]

    def test_kind_based_collection_dispatch_with_handler(self):
        client = _make_client(collection_handler=_mock_handler, role=UNRESTRICTED_ROLE)
        resp = client.post(
            "/api/dispatch",
            json={"kind": "collection", "name": "general_ludd.agent.init", "args": {"project": "test"}},
        )
        data = resp.json()
        assert data["ok_count"] == 1
        assert data["results"][0]["ok"] is True
        assert data["results"][0]["output"]["args"] == {"project": "test"}

    def test_kind_based_mcp_dispatch(self):
        client = _make_client(mcp_handler=_mock_handler, role=UNRESTRICTED_ROLE)
        resp = client.post(
            "/api/dispatch",
            json={"kind": "mcp", "name": "fs.read", "args": {"path": "/tmp/x"}},
        )
        data = resp.json()
        assert data["ok_count"] == 1
        assert data["results"][0]["name"] == "fs.read"


# ── Response format validation ─────────────────────────────────────────


class TestResponseFormatValidation:
    def test_tool_call_response_has_required_keys(self):
        client = _make_client(collection_handler=_mock_handler)
        resp = client.post(
            "/api/dispatch",
            json={"tool_calls": [{"kind": "collection", "name": "ns.coll.mod", "args": {}}]},
        )
        data = resp.json()
        for key in ("results", "count", "ok_count", "error_count"):
            assert key in data, f"missing key: {key}"
        assert data["count"] == data["ok_count"] + data["error_count"]

    def test_capability_dispatch_response_has_required_keys(self):
        reg = _build_registry()
        client = _make_cap_client(reg, _mock_handler)
        resp = client.post(
            "/api/dispatch",
            json={"capability": "travel", "action": "flight_search", "args": {}},
        )
        data = resp.json()
        for key in ("results", "count", "ok_count", "error_count"):
            assert key in data, f"missing key: {key}"

    def test_each_result_has_standard_keys(self):
        client = _make_client(collection_handler=_mock_handler)
        resp = client.post(
            "/api/dispatch",
            json={
                "tool_calls": [
                    {"kind": "collection", "name": "ns.coll.a", "args": {}},
                    {"kind": "collection", "name": "ns.coll.b", "args": {}},
                ]
            },
        )
        data = resp.json()
        for result in data["results"]:
            for key in ("ok", "kind", "name", "output", "error"):
                assert key in result, f"missing key in result: {key}"

    def test_error_result_has_null_output(self):
        client = _make_client(collection_handler=_mock_handler)
        resp = client.post(
            "/api/dispatch",
            json={"kind": "bogus", "name": "tool", "args": {}},
        )
        data = resp.json()
        result = data["results"][0]
        assert result["ok"] is False
        assert result["error"] is not None
        assert result["output"] is None

    def test_recent_endpoint_returns_valid_shape(self):
        reg = _build_registry()
        client = _make_cap_client(reg, _mock_handler)
        client.post(
            "/api/dispatch",
            json={"capability": "travel", "action": "flight_search", "args": {}},
        )
        resp = client.get("/api/dispatch/recent")
        data = resp.json()
        assert "recent" in data
        assert "total" in data
        assert isinstance(data["recent"], list)

    def test_available_endpoint_lists_registered_kinds(self):
        client = _make_client(collection_handler=_mock_handler, mcp_handler=_mock_handler)
        resp = client.get("/api/dispatch/available")
        data = resp.json()
        assert "registered_kinds" in data
        assert "collection" in data["registered_kinds"]
        assert "mcp" in data["registered_kinds"]


# ── Handler behaviour ──────────────────────────────────────────────────


class TestHandlerBehaviour:
    def test_handler_exception_yields_error_result(self):
        client = _make_client(collection_handler=_failing_handler, role=UNRESTRICTED_ROLE)
        resp = client.post(
            "/api/dispatch",
            json={"kind": "collection", "name": "ns.coll.mod", "args": {}},
        )
        data = resp.json()
        result = data["results"][0]
        assert result["ok"] is False
        assert result["error"] == "handler_error"

    def test_async_handler_works(self):
        client = _make_client(collection_handler=_async_handler, role=UNRESTRICTED_ROLE)
        resp = client.post(
            "/api/dispatch",
            json={"kind": "collection", "name": "ns.coll.mod", "args": {"x": 1}},
        )
        data = resp.json()
        assert data["results"][0]["ok"] is True
        assert data["results"][0]["output"] == {"invoked": "ns.coll.mod", "args": {"x": 1}, "status": "ok"}


# ── JSON string body parsing ───────────────────────────────────────────


class TestJsonStringParsing:
    def test_json_string_body_parsed_as_dict(self):
        """parse_tool_calls accepts a JSON-encoded string."""
        import json

        client = _make_client(collection_handler=_mock_handler, role=UNRESTRICTED_ROLE)
        resp = client.post(
            "/api/dispatch",
            content=json.dumps({"kind": "collection", "name": "ns.coll.mod", "args": {}}),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok_count"] == 1

    def test_json_string_with_tool_calls_key(self):
        import json

        client = _make_client(collection_handler=_mock_handler, role=UNRESTRICTED_ROLE)
        resp = client.post(
            "/api/dispatch",
            content=json.dumps({"tool_calls": [{"kind": "collection", "name": "ns.coll.mod", "args": {}}]}),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json()["count"] == 1


# ── Capability endpoint list and registry ──────────────────────────────


class TestCapabilityListAndRegistry:
    def test_capabilities_list_returns_all_tags(self):
        client = _make_cap_client(_build_registry())
        resp = client.get("/api/dispatch/capabilities")
        data = resp.json()
        assert "travel" in data["capabilities"]
        assert "chemistry" in data["capabilities"]
        assert "deploy" in data["capabilities"]

    def test_capability_registry_endpoint_returns_full_data(self):
        client = _make_cap_client(_build_registry())
        resp = client.get("/api/dispatch/capability/registry")
        data = resp.json()
        assert "collections" in data
        assert len(data["collections"]) == 4
        assert "tag_index" in data

    def test_no_registry_endpoints_404(self):
        client = _make_cap_client(registry=None)
        assert client.get("/api/dispatch/capabilities").status_code == 404
        assert client.get("/api/dispatch/capability/registry").status_code == 404
        assert client.post("/api/dispatch/capability", json={"capability": "x"}).status_code == 404


# ── Recent dispatch ring-buffer ────────────────────────────────────────


class TestRecentDispatchBuffer:
    def test_recent_buffer_captures_dispatches(self):
        reg = _build_registry()
        client = _make_cap_client(reg, _mock_handler)
        client.post("/api/dispatch", json={"capability": "travel", "action": "flight_search", "args": {}})
        resp = client.get("/api/dispatch/recent")
        data = resp.json()
        assert data["total"] >= 1
        assert data["recent"][0]["name"] == "general_ludd.travel.flight_search"

    def test_recent_limit_param(self):
        reg = _build_registry()
        client = _make_cap_client(reg, _mock_handler)
        for i in range(5):
            client.post(
                "/api/dispatch",
                json={"capability": "travel", "action": f"mod{i}", "args": {}},
            )
        resp = client.get("/api/dispatch/recent", params={"limit": 3})
        data = resp.json()
        assert data["total"] >= 5
        assert len(data["recent"]) == 3
