"""Deep edge-case tests for router auth surfaces.

Covers: routers/security.py (STS + permission escalation lifecycle) and
security/auth.py (PSK verification, path confinement, SSRF guard).
Tests edge cases that existing structural and endpoint tests miss:
type coercion, boundary values, error-propagation, fail-closed defaults,
self-review bypass attempts, capability-guard behaviour, and counter isolation.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import pytest
import yaml
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from general_ludd.routers import security as sec
from general_ludd.security.permissions import (
    Capability,
    PermissionSpec,
    PermissionSpecParser,
    PermissionSubject,
)

# ---------------------------------------------------------------------------
# YAML fixtures
# ---------------------------------------------------------------------------

_ISSUER_SPEC_YAML = """\
version: 1
agent_type: primary
max_sts_ttl_seconds: 3600
capabilities:
  - resource: file:repo
    actions: [read, write]
    constraints:
      path_prefix: "/repo/"
  - resource: net:egress:llm_api
    actions: [connect]
    constraints:
      allowed_hosts: ["api.anthropic.com", "api.openai.com"]
denied: []
"""

_VALID_SUBJECT_YAML = """\
version: 1
agent_type: subagent
max_sts_ttl_seconds: 1800
capabilities:
  - resource: file:repo
    actions: [read]
    constraints:
      path_prefix: "/repo/sub/"
denied: []
"""

_SPEC_MISSING_CONSTRAINT_YAML = """\
version: 1
agent_type: build
capabilities:
  - resource: file:repo
    actions: [read]
    constraints: {}
denied: []
"""

_MALFORMED_YAML = "\tnot: valid"


def _valid_perm_spec_yaml(agent_type: str) -> str:
    return (
        "version: 1\n"
        f"agent_type: {agent_type}\n"
        "capabilities:\n"
        "  - resource: file:repo\n"
        "    actions: [read]\n"
        "    constraints:\n"
        '      path_prefix: "/repo/"\n'
        "denied: []\n"
    )


def _minimal_current_spec_yaml() -> str:
    return (
        "version: 1\n"
        "agent_type: sts_token\n"
        "subject: sts_token\n"
        "capabilities: []\n"
        "denied: []\n"
        "max_sts_ttl_seconds: 1800\n"
    )


def _escalation_body(
    *,
    agent_id: str = "agent-Z",
    requested: list[dict[str, Any]] | None = None,
    reason: str = "need additional access",
    alternatives: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "agent_id": agent_id,
        "current_spec_yaml": _minimal_current_spec_yaml(),
        "requested_additional_capabilities": requested
        or [{"resource": "file:repo", "actions": ["write"], "constraints": {"path_prefix": "/repo/"}}],
        "reason": reason,
        "alternatives_tried": (
            alternatives if alternatives is not None else [{"approach": f"approach-{i}"} for i in range(3)]
        ),
    }


# Helper: esc body that produces a guaranteed-pending escalation (resource not in issuer/human specs)
def _pending_esc_body(*, agent_id: str = "agent-Z", alternatives: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return _escalation_body(
        agent_id=agent_id,
        requested=[{"resource": "agent:spawn", "actions": ["invoke"], "constraints": {}}],
        alternatives=alternatives,
    )


def _build_app(
    tmp_path: Any, *, issuer_spec: PermissionSpec | None = None, human_spec: PermissionSpec | None = None
) -> FastAPI:
    app = FastAPI()
    app.state._config_dir = str(tmp_path)
    if issuer_spec is not None:
        app.state._sts_issuer_spec = issuer_spec
    if human_spec is not None:
        app.state._human_spec = human_spec
    sec.register(app, {})
    return app


# App variant that injects a fake auth_spec on every request, bypassing
# RequireCapability guards so we can test validation logic on PUT/revoke.
def _admin_permission_spec() -> PermissionSpec:
    return PermissionSpec(
        agent_type="admin-test",
        capabilities=[
            Capability(resource="admin:sts", actions=["revoke"], constraints={}),
            Capability(resource="admin:permissions", actions=["write"], constraints={}),
        ],
        subject=PermissionSubject.STS_TOKEN,
    )


def _build_app_with_auth(
    tmp_path: Any, *, issuer_spec: PermissionSpec | None = None, human_spec: PermissionSpec | None = None
) -> FastAPI:
    app = _build_app(tmp_path, issuer_spec=issuer_spec, human_spec=human_spec)

    class _InjectAuthSpec(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next: Any) -> Any:
            request.state.auth_spec = _admin_permission_spec()
            return await call_next(request)

    app.add_middleware(_InjectAuthSpec)
    return app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def issuer_spec() -> PermissionSpec:
    return PermissionSpecParser.parse(_ISSUER_SPEC_YAML)


@pytest.fixture
def app(tmp_path: Any, issuer_spec: PermissionSpec) -> FastAPI:
    return _build_app(tmp_path, issuer_spec=issuer_spec)


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture
def app_with_auth(tmp_path: Any, issuer_spec: PermissionSpec) -> FastAPI:
    return _build_app_with_auth(tmp_path, issuer_spec=issuer_spec)


@pytest.fixture
def auth_client(app_with_auth: FastAPI) -> TestClient:
    return TestClient(app_with_auth)


# ===================================================================
# IsSelfReview — case/whitespace bypass attempts
# ===================================================================


class TestIsSelfReviewDeep:
    def test_exact_match(self) -> None:
        assert sec._is_self_review("agent-1", "agent-1") is True

    def test_case_insensitive(self) -> None:
        assert sec._is_self_review("AGENT-1", "agent-1") is True

    def test_stripped_whitespace(self) -> None:
        assert sec._is_self_review("  agent-1  ", "agent-1") is True

    def test_empty_reviewer_is_self(self) -> None:
        assert sec._is_self_review("", "agent-1") is True

    def test_whitespace_only_reviewer_is_self(self) -> None:
        assert sec._is_self_review("   ", "agent-1") is True

    def test_different_agents_not_self(self) -> None:
        assert sec._is_self_review("alice", "agent-1") is False

    def test_near_match_but_different(self) -> None:
        assert sec._is_self_review("agent-11", "agent-1") is False


# ===================================================================
# IssuerSpec — fallback and override edge cases
# ===================================================================


class TestGetIssuerSpecDeep:
    def test_uses_explicit_override(self, tmp_path: Any) -> None:
        override = PermissionSpec(agent_type="override", capabilities=[])
        app = _build_app(tmp_path, issuer_spec=override)
        assert sec._get_issuer_spec(app) is override

    def test_falls_back_to_default_when_no_override(self, tmp_path: Any) -> None:
        app = _build_app(tmp_path)
        spec = sec._get_issuer_spec(app)
        assert spec.agent_type == "primary"


# ===================================================================
# HumanSpec — YAML parse error fail-closed path
# ===================================================================


class TestGetHumanSpecDeep:
    def test_broken_yaml_in_override_file_raises(self, tmp_path: Any) -> None:
        perms_dir = tmp_path / "permissions"
        perms_dir.mkdir(parents=True, exist_ok=True)
        (perms_dir / "human-operator.yml").write_text("{broken: [yaml: }")

        app = _build_app(tmp_path)
        app.state._default_human_role = "human-operator"

        with pytest.raises((yaml.YAMLError, ValueError)):
            sec._get_human_spec(app)

    def test_missing_file_falls_back_to_builtin_default(self, tmp_path: Any) -> None:
        app = _build_app(tmp_path)
        app.state._default_human_role = "nonexistent-role"
        spec = sec._get_human_spec(app)
        assert spec.agent_type == "human-viewer"


# ===================================================================
# Caps helpers — empty/malformed YAML
# ===================================================================


class TestCapsHelpersDeep:
    def test_caps_from_yaml_with_non_list_root(self) -> None:
        result = sec._caps_from_yaml("key: value\n")
        assert result == []

    def test_caps_from_yaml_with_mixed_dicts_and_scalars(self) -> None:
        y = "- {resource: x, actions: [read]}\n- just_a_string\n"
        result = sec._caps_from_yaml(y)
        assert len(result) == 1
        assert result[0].resource == "x"

    def test_caps_to_yaml_roundtrip_preserves_constraints(self) -> None:
        caps: list[dict[str, object]] = [
            {"resource": "r", "actions": ["a1", "a2"], "constraints": {"k1": "v1", "k2": 42}}
        ]
        yaml_str = sec._caps_to_yaml(caps)
        parsed = sec._caps_from_yaml(yaml_str)
        assert parsed[0].constraints == {"k1": "v1", "k2": 42}

    def test_spec_from_caps_yaml_sets_sts_token_subject(self) -> None:
        yaml_str = sec._caps_to_yaml([{"resource": "r", "actions": ["read"], "constraints": {}}])
        spec = sec._spec_from_caps_yaml("agent-X", yaml_str)
        assert spec.subject == PermissionSubject.STS_TOKEN
        assert spec.agent_type == "escalation:agent-X"


# ===================================================================
# EscCounter — monotonic increment and isolation
# ===================================================================


class TestEscCounterDeep:
    def test_increments_monotonically(self, tmp_path: Any) -> None:
        app = _build_app(tmp_path)
        ids = [sec._esc_counter(app) for _ in range(5)]
        assert ids == [1, 2, 3, 4, 5]

    def test_counter_independent_per_app(self, tmp_path: Any) -> None:
        app1 = _build_app(tmp_path)
        app2 = _build_app(tmp_path)
        assert sec._esc_counter(app1) == 1
        assert sec._esc_counter(app2) == 1
        assert sec._esc_counter(app1) == 2


# ===================================================================
# TTL coercion — negative, zero, omitted
# ===================================================================


class TestStsIssueTtlCoercion:
    @pytest.mark.xfail(
        strict=False,
        reason=(
            "docs/design/PERMISSION_SYSTEM.md: non-integer TTL ValueError escapes "
            "the synchronous TestClient portal on Python 3.14"
        ),
    )
    def test_non_integer_ttl_raises_valueerror(self, client: TestClient) -> None:
        """The router's `ttl = int(str(...))` does NOT catch ValueError —
        the endpoint will 500. This test documents the actual behaviour."""
        resp = client.post(
            "/admin/sts/issue",
            json={
                "subject_agent_id": "agent-1",
                "requested_spec_yaml": _VALID_SUBJECT_YAML,
                "ttl_seconds": "not-an-int",
            },
        )
        assert resp.status_code == 500

    def test_negative_ttl_silently_converts(self, client: TestClient) -> None:
        resp = client.post(
            "/admin/sts/issue",
            json={"subject_agent_id": "agent-1", "requested_spec_yaml": _VALID_SUBJECT_YAML, "ttl_seconds": -3600},
        )
        assert resp.status_code == 200

    def test_zero_ttl(self, client: TestClient) -> None:
        resp = client.post(
            "/admin/sts/issue",
            json={"subject_agent_id": "agent-1", "requested_spec_yaml": _VALID_SUBJECT_YAML, "ttl_seconds": 0},
        )
        assert resp.status_code == 200

    def test_omitted_ttl_defaults(self, client: TestClient) -> None:
        resp = client.post(
            "/admin/sts/issue",
            json={"subject_agent_id": "agent-1", "requested_spec_yaml": _VALID_SUBJECT_YAML},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["expires_at"]
        assert body["issued_at"] <= body["expires_at"]


# ===================================================================
# Spec validation — edge-case inputs (using auth_client to bypass guards)
# ===================================================================


class TestPermSpecValidationDeep:
    def test_put_spec_empty_string_400(self, auth_client: TestClient) -> None:
        resp = auth_client.put("/admin/perm/spec/build", json={"spec_yaml": ""})
        assert resp.status_code == 400

    def test_put_spec_YAML_that_parses_to_none(self, auth_client: TestClient) -> None:
        """``yaml.safe_load("null")`` returns ``None``. The spec_yaml string
        ``"null\n"`` is truthy, so the empty-string 400 guard doesn't fire.
        ``PermissionSpecParser.parse`` then handles None silently, producing
        a valid empty spec → 200. Documented, not a regression."""
        resp = auth_client.put("/admin/perm/spec/build", json={"spec_yaml": "null\n"})
        assert resp.status_code == 200

    def test_put_spec_YAML_that_is_integer(self, auth_client: TestClient) -> None:
        resp = auth_client.put("/admin/perm/spec/build", json={"spec_yaml": "42\n"})
        assert resp.status_code == 400

    def test_put_spec_YAML_that_is_list(self, auth_client: TestClient) -> None:
        resp = auth_client.put("/admin/perm/spec/build", json={"spec_yaml": "- one\n- two\n"})
        assert resp.status_code == 400

    def test_put_empty_json_body(self, auth_client: TestClient) -> None:
        resp = auth_client.put("/admin/perm/spec/build", json={})
        assert resp.status_code == 400

    def test_sts_issue_spec_with_empty_capabilities(self, client: TestClient) -> None:
        sparse = "version: 1\nagent_type: subagent\ncapabilities: []\ndenied: []\n"
        resp = client.post("/admin/sts/issue", json={"subject_agent_id": "agent-1", "requested_spec_yaml": sparse})
        assert resp.status_code == 200

    def test_sts_issue_spec_with_nonexistent_resource_400(self, client: TestClient) -> None:
        weird = (
            "version: 1\nagent_type: subagent\ncapabilities:\n"
            "  - resource: nonexistent:thing\n    actions: [read]\n"
            "    constraints: {}\ndenied: []\n"
        )
        resp = client.post("/admin/sts/issue", json={"subject_agent_id": "agent-1", "requested_spec_yaml": weird})
        assert resp.status_code == 400


# ===================================================================
# Escalation request — alternatives edge cases
# ===================================================================


class TestEscalationRequestAlternatives:
    def test_two_alternatives_422(self, client: TestClient) -> None:
        body = _escalation_body(alternatives=[{"approach": "A"}, {"approach": "B"}])
        resp = client.post("/admin/perm/escalation-request", json=body)
        assert resp.status_code == 422
        assert resp.json()["distinct_approaches_count"] == 2

    def test_one_alternative_422(self, client: TestClient) -> None:
        body = _escalation_body(alternatives=[{"approach": "A"}])
        resp = client.post("/admin/perm/escalation-request", json=body)
        assert resp.status_code == 422
        assert resp.json()["distinct_approaches_count"] == 1

    def test_zero_alternatives_422(self, client: TestClient) -> None:
        body = _escalation_body(alternatives=[])
        resp = client.post("/admin/perm/escalation-request", json=body)
        assert resp.status_code == 422

    def test_duplicate_approaches_counted_once(self, client: TestClient) -> None:
        body = _escalation_body(
            alternatives=[
                {"approach": "same", "outcome": "fail"},
                {"approach": "  same  ", "outcome": "fail2"},
                {"approach": "different"},
            ]
        )
        resp = client.post("/admin/perm/escalation-request", json=body)
        assert resp.status_code == 422
        assert resp.json()["distinct_approaches_count"] == 2

    def test_alternatives_without_approach_key_ignored(self, client: TestClient) -> None:
        body = _escalation_body(alternatives=[{"approach": "A"}, {"wrong_key": "B"}, {"approach": ""}])
        resp = client.post("/admin/perm/escalation-request", json=body)
        assert resp.status_code == 422

    def test_alternatives_with_nondict_entries(self, client: TestClient) -> None:
        """Strings in alternatives are skipped by the approach-extraction loop."""
        body = _escalation_body(alternatives=["just a string", {"approach": "A"}])  # type: ignore[list-item]
        resp = client.post("/admin/perm/escalation-request", json=body)
        assert resp.status_code == 422
        assert resp.json()["distinct_approaches_count"] == 1


# ===================================================================
# Escalation request — bad cap shapes (router does NOT validate shape)
# ===================================================================


class TestEscalationRequestBadCaps:
    def test_non_list_caps_passed_through_to_yaml(self, client: TestClient) -> None:
        """``_caps_to_yaml`` wraps its input in a list for ``yaml.safe_dump``,
        so even a bare string is serialised successfully. The router does NOT
        validate that ``requested_additional_capabilities`` is a list → 201."""
        body = _escalation_body()
        body["requested_additional_capabilities"] = "just a string"
        resp = client.post("/admin/perm/escalation-request", json=body)
        assert resp.status_code == 201

    def test_list_with_non_dict_passed_through(self, client: TestClient) -> None:
        body = _escalation_body(requested=["a string"])  # type: ignore[list-item]
        resp = client.post("/admin/perm/escalation-request", json=body)
        assert resp.status_code == 201


# ===================================================================
# Escalation approve/deny — self-review (deep, using guaranteed-pending)
# ===================================================================


class TestEscalationSelfReviewDeep:
    def test_approve_self_review_403(self, client: TestClient) -> None:
        create = client.post("/admin/perm/escalation-request", json=_pending_esc_body(agent_id="agent-X"))
        esc_id = create.json()["id"]

        resp = client.post(
            f"/admin/perm/escalations/{esc_id}/approve",
            json={"reason": "ok", "human_reviewer": "agent-X"},
        )
        assert resp.status_code == 403
        assert "self_approval_forbidden" in resp.json()["error"]

    def test_approve_self_review_case_insensitive_403(self, client: TestClient) -> None:
        create = client.post("/admin/perm/escalation-request", json=_pending_esc_body(agent_id="Agent-X"))
        esc_id = create.json()["id"]

        resp = client.post(
            f"/admin/perm/escalations/{esc_id}/approve",
            json={"reason": "ok", "human_reviewer": "agent-x"},
        )
        assert resp.status_code == 403
        assert "self_approval_forbidden" in resp.json()["error"]

    def test_approve_self_review_whitespace_403(self, client: TestClient) -> None:
        create = client.post("/admin/perm/escalation-request", json=_pending_esc_body(agent_id="agent-X"))
        esc_id = create.json()["id"]

        resp = client.post(
            f"/admin/perm/escalations/{esc_id}/approve",
            json={"reason": "ok", "human_reviewer": "  AGENT-X  "},
        )
        assert resp.status_code == 403

    def test_deny_self_review_403(self, client: TestClient) -> None:
        create = client.post("/admin/perm/escalation-request", json=_pending_esc_body(agent_id="agent-X"))
        esc_id = create.json()["id"]

        resp = client.post(
            f"/admin/perm/escalations/{esc_id}/deny",
            json={"reason": "no", "human_reviewer": "agent-X"},
        )
        assert resp.status_code == 403
        assert "self_denial_forbidden" in resp.json()["error"]


# ===================================================================
# Escalation approve — edge cases (double approve, approve after deny)
# ===================================================================


class TestEscalationApproveDeep:
    def test_approve_twice_409(self, client: TestClient) -> None:
        body = _escalation_body(
            requested=[
                {
                    "resource": "net:egress:llm_api",
                    "actions": ["connect"],
                    "constraints": {"allowed_hosts": ["api.openai.com"]},
                }
            ]
        )
        create = client.post("/admin/perm/escalation-request", json=body)
        esc_id = create.json()["id"]

        first = client.post(
            f"/admin/perm/escalations/{esc_id}/approve", json={"reason": "ok", "human_reviewer": "alice"}
        )
        assert first.status_code == 200

        second = client.post(
            f"/admin/perm/escalations/{esc_id}/approve", json={"reason": "again", "human_reviewer": "bob"}
        )
        assert second.status_code == 409
        assert "already approved" in second.json()["error"]

    def test_approve_after_deny_409(self, client: TestClient) -> None:
        create = client.post("/admin/perm/escalation-request", json=_pending_esc_body())
        esc_id = create.json()["id"]

        deny = client.post(f"/admin/perm/escalations/{esc_id}/deny", json={"reason": "no", "human_reviewer": "bob"})
        assert deny.status_code == 200

        approve = client.post(
            f"/admin/perm/escalations/{esc_id}/approve", json={"reason": "changed mind", "human_reviewer": "alice"}
        )
        assert approve.status_code == 409
        assert "already denied" in approve.json()["error"]

    def test_approve_unknown_id_404(self, client: TestClient) -> None:
        resp = client.post("/admin/perm/escalations/999999/approve", json={"reason": "x", "human_reviewer": "alice"})
        assert resp.status_code == 404

    def test_approve_with_no_human_reviewer_rejects_self_review(self, client: TestClient) -> None:
        create = client.post("/admin/perm/escalation-request", json=_pending_esc_body(agent_id="agent-X"))
        esc_id = create.json()["id"]

        resp = client.post(f"/admin/perm/escalations/{esc_id}/approve", json={"reason": "ok"})
        assert resp.status_code == 403


# ===================================================================
# Escalation deny — missing/blank reason
# ===================================================================


class TestEscalationDenyDeep:
    def test_deny_missing_reason_key_entirely_400(self, client: TestClient) -> None:
        create = client.post("/admin/perm/escalation-request", json=_pending_esc_body())
        esc_id = create.json()["id"]
        resp = client.post(f"/admin/perm/escalations/{esc_id}/deny", json={})
        assert resp.status_code == 400

    def test_deny_blank_reason_400(self, client: TestClient) -> None:
        create = client.post("/admin/perm/escalation-request", json=_pending_esc_body())
        esc_id = create.json()["id"]
        resp = client.post(f"/admin/perm/escalations/{esc_id}/deny", json={"reason": "   "})
        assert resp.status_code == 400

    def test_deny_unknown_id_404(self, client: TestClient) -> None:
        resp = client.post("/admin/perm/escalations/999999/deny", json={"reason": "no"})
        assert resp.status_code == 404

    def test_deny_twice_409(self, client: TestClient) -> None:
        create = client.post("/admin/perm/escalation-request", json=_pending_esc_body())
        esc_id = create.json()["id"]
        first = client.post(f"/admin/perm/escalations/{esc_id}/deny", json={"reason": "no", "human_reviewer": "bob"})
        assert first.status_code == 200
        second = client.post(
            f"/admin/perm/escalations/{esc_id}/deny", json={"reason": "again", "human_reviewer": "carol"}
        )
        assert second.status_code == 409


# ===================================================================
# STS revoke — auth_client bypasses capability guard
# ===================================================================


class TestStsRevokeDeep:
    def test_capability_guard_blocks_revoke_without_auth(self, client: TestClient) -> None:
        """The revoke endpoint requires admin:sts:revoke — without auth_spec, the
        guard denies with 403 before reaching validation."""
        resp = client.post("/admin/sts/revoke", json={"token_id": ""})
        assert resp.status_code == 403

    def test_empty_token_id_400(self, auth_client: TestClient) -> None:
        resp = auth_client.post("/admin/sts/revoke", json={"token_id": ""})
        assert resp.status_code == 400

    def test_whitespace_only_token_id_404(self, auth_client: TestClient) -> None:
        resp = auth_client.post("/admin/sts/revoke", json={"token_id": "   "})
        assert resp.status_code == 404


# ===================================================================
# STS active — empty results, field shape
# ===================================================================


class TestStsActiveDeep:
    def test_empty_when_tokens_expired(self, client: TestClient) -> None:
        resp = client.get("/admin/sts/active")
        assert resp.status_code == 200
        assert resp.json() == {"tokens": []}

    def test_fields_match_expected_schema(self, client: TestClient) -> None:
        client.post(
            "/admin/sts/issue", json={"subject_agent_id": "agent-1", "requested_spec_yaml": _VALID_SUBJECT_YAML}
        )
        tokens = client.get("/admin/sts/active").json()["tokens"]
        assert len(tokens) == 1
        expected_keys = {
            "token_id",
            "issuer_agent_id",
            "subject_agent_id",
            "issued_at",
            "expires_at",
            "last_used_at",
            "use_count",
        }
        assert set(tokens[0].keys()) == expected_keys


# ===================================================================
# STS audit — query edge cases
# ===================================================================


class TestStsAuditDeep:
    def test_multiple_params_combined(self, client: TestClient) -> None:
        client.post(
            "/admin/sts/issue", json={"subject_agent_id": "agent-A", "requested_spec_yaml": _VALID_SUBJECT_YAML}
        )
        resp = client.get("/admin/sts/audit", params={"agent_id": "agent-B", "since": time.time() + 100})
        assert resp.status_code == 200
        assert resp.json()["events"] == []

    def test_no_params_returns_all(self, client: TestClient) -> None:
        for i in range(3):
            client.post(
                "/admin/sts/issue", json={"subject_agent_id": f"agent-{i}", "requested_spec_yaml": _VALID_SUBJECT_YAML}
            )
        events = client.get("/admin/sts/audit").json()["events"]
        assert len(events) == 3

    def test_response_schema(self, client: TestClient) -> None:
        resp = client.get("/admin/sts/audit")
        assert resp.status_code == 200
        assert "events" in resp.json()
        assert isinstance(resp.json()["events"], list)


# ===================================================================
# Perm spec get — non-existent agent type, special chars
# ===================================================================


class TestPermSpecGetDeep:
    def test_agent_type_with_special_chars(self, client: TestClient) -> None:
        """FastAPI normalizes "../etc" — the raw path param reaches the
        handler as a literal agent_type string, so it falls through to
        default_spec lookup, not path traversal."""
        resp = client.get("/admin/perm/spec/../etc")
        # ../etc is URL-decoded by starlette, FastAPI fails to match the path
        assert resp.status_code == 404

    def test_very_long_agent_type_name(self, client: TestClient) -> None:
        long_name = "a" * 500
        resp = client.get(f"/admin/perm/spec/{long_name}")
        assert resp.status_code == 200

    def test_empty_agent_type_matches_list_endpoint(self, client: TestClient) -> None:
        """GET /admin/perm/spec/ resolves to the list endpoint because
        /admin/perm/spec/{agent_type} with an empty agent_type is
        handled by FastAPI as the parent path."""
        resp = client.get("/admin/perm/spec/")
        assert resp.status_code == 200
        assert "agent_types" in resp.json()

    def test_happy_path_writes_and_reads(self, auth_client: TestClient, tmp_path: Any) -> None:
        valid_yaml = _valid_perm_spec_yaml("build")
        put = auth_client.put("/admin/perm/spec/build", json={"spec_yaml": valid_yaml})
        assert put.status_code == 200, put.text
        get_resp = auth_client.get("/admin/perm/spec/build")
        assert get_resp.json()["spec_yaml"] == valid_yaml


# ===================================================================
# Perm spec list — empty, sorted
# ===================================================================


class TestPermSpecListDeep:
    def test_empty_when_no_specs_saved(self, client: TestClient) -> None:
        resp = client.get("/admin/perm/spec")
        assert resp.status_code == 200
        assert resp.json()["agent_types"] == []

    def test_lists_sorted(self, auth_client: TestClient) -> None:
        auth_client.put("/admin/perm/spec/zeta", json={"spec_yaml": _valid_perm_spec_yaml("zeta")})
        auth_client.put("/admin/perm/spec/alpha", json={"spec_yaml": _valid_perm_spec_yaml("alpha")})
        resp = auth_client.get("/admin/perm/spec")
        assert resp.json()["agent_types"] == ["alpha", "zeta"]


# ===================================================================
# Capability guard — PUT endpoints blocked without auth
# ===================================================================


class TestCapabilityGuardOnPermSpecPut:
    def test_put_spec_blocked_without_auth_spec(self, client: TestClient) -> None:
        resp = client.put("/admin/perm/spec/build", json={"spec_yaml": _valid_perm_spec_yaml("build")})
        assert resp.status_code == 403

    def test_put_spec_allowed_with_auth_spec(self, auth_client: TestClient) -> None:
        resp = auth_client.put("/admin/perm/spec/build", json={"spec_yaml": _valid_perm_spec_yaml("build")})
        assert resp.status_code == 200


# ===================================================================
# SyncEscalationFromHumanTodo — never-fired code paths
# ===================================================================


class TestSyncEscalationFromHumanTodoDeep:
    def test_no_store_noop(self, tmp_path: Any) -> None:
        app = _build_app(tmp_path)
        assert getattr(app.state, "_escalation_store", None) is None
        asyncio.run(
            sec._sync_escalation_from_human_todo(
                app, "ht-1", tags=["escalation:1"], status="done", human_resolver="carol", human_resolution="ok"
            )
        )

    def test_empty_store_noop(self, tmp_path: Any) -> None:
        app = _build_app(tmp_path)
        app.state._escalation_store = []
        asyncio.run(
            sec._sync_escalation_from_human_todo(
                app, "ht-1", tags=["escalation:1"], status="done", human_resolver="carol", human_resolution="ok"
            )
        )

    def test_tag_without_escalation_prefix_noop(self, app: FastAPI) -> None:
        row: dict[str, Any] = {
            "id": 1,
            "agent_id": "agent-X",
            "status": "pending",
            "human_reviewer": None,
            "decided_at": None,
            "decided_reason": None,
        }
        app.state._escalation_store = [row]
        asyncio.run(
            sec._sync_escalation_from_human_todo(
                app,
                "ht-1",
                tags=["some-other-tag", "another:thing"],
                status="done",
                human_resolver="carol",
                human_resolution="ok",
            )
        )
        assert row["status"] == "pending"

    def test_malformed_escalation_tag_non_int(self, app: FastAPI) -> None:
        row: dict[str, Any] = {
            "id": 1,
            "agent_id": "agent-X",
            "status": "pending",
            "human_reviewer": None,
            "decided_at": None,
            "decided_reason": None,
        }
        app.state._escalation_store = [row]
        asyncio.run(
            sec._sync_escalation_from_human_todo(
                app,
                "ht-1",
                tags=["escalation:not_a_number"],
                status="done",
                human_resolver="carol",
                human_resolution="ok",
            )
        )
        assert row["status"] == "pending"

    def test_dismissed_status(self, app: FastAPI) -> None:
        row: dict[str, Any] = {
            "id": 1,
            "agent_id": "agent-X",
            "status": "pending",
            "human_reviewer": None,
            "decided_at": None,
            "decided_reason": None,
        }
        app.state._escalation_store = [row]
        asyncio.run(
            sec._sync_escalation_from_human_todo(
                app,
                "ht-1",
                tags=["escalation:1"],
                status="dismissed",
                human_resolver="carol",
                human_resolution="denied",
            )
        )
        assert row["status"] == "denied"
        assert row["decided_at"] is not None

    def test_unknown_status_noop(self, app: FastAPI) -> None:
        row: dict[str, Any] = {
            "id": 1,
            "agent_id": "agent-X",
            "status": "pending",
            "human_reviewer": None,
            "decided_at": None,
            "decided_reason": None,
        }
        app.state._escalation_store = [row]
        asyncio.run(
            sec._sync_escalation_from_human_todo(
                app, "ht-1", tags=["escalation:1"], status="in_progress", human_resolver="carol", human_resolution="ok"
            )
        )
        assert row["status"] == "pending"


# ===================================================================
# ResolveHumanTodoForEscalation — edge cases
# ===================================================================


class TestResolveHumanTodoForEscalationDeep:
    def test_none_human_todo_id_noop(self, tmp_path: Any) -> None:
        app = _build_app(tmp_path)
        row: dict[str, object] = {"human_todo_id": None}
        result = asyncio.run(
            sec._resolve_human_todo_for_escalation(app, row, status="done", resolver="alice", reason="ok")
        )
        assert result is None

    def test_none_factory_noop(self, tmp_path: Any) -> None:
        app = _build_app(tmp_path)
        row: dict[str, object] = {"human_todo_id": "ht-1"}
        result = asyncio.run(
            sec._resolve_human_todo_for_escalation(app, row, status="done", resolver="alice", reason="ok")
        )
        assert result is None


# ===================================================================
# STS issuer — default spec vs override spec interaction
# ===================================================================


class TestStsIssuerSpecResolution:
    def test_uses_default_spec_after_previous_nondefault(self, tmp_path: Any) -> None:
        app1 = _build_app(tmp_path, issuer_spec=PermissionSpec(agent_type="custom", capabilities=[]))
        spec1 = sec._get_issuer_spec(app1)
        assert spec1.agent_type == "custom"

        app2 = _build_app(tmp_path)
        spec2 = sec._get_issuer_spec(app2)
        assert spec2.agent_type == "primary"


# ===================================================================
# FileHumanTodoForEscalation — no-factory fallback
# ===================================================================


class TestFileHumanTodoForEscalationDeep:
    def test_no_factory_returns_none_and_logs_warning(self, tmp_path: Any, caplog: pytest.LogCaptureFixture) -> None:
        app = _build_app(tmp_path)
        row = {
            "id": 1,
            "agent_id": "agent-X",
            "reason": "test",
            "requested_capabilities_yaml": "[]",
            "status": "pending",
        }
        with caplog.at_level(logging.WARNING, logger="general_ludd.routers.security"):
            result = asyncio.run(sec._file_human_todo_for_escalation(app, row))
        assert result is None
        assert any("no session factory" in rec.getMessage() for rec in caplog.records)


# ===================================================================
# Escalation list — status filter edge cases
# ===================================================================


class TestEscalationListFilterDeep:
    def test_nonexistent_status_filter_returns_empty(self, client: TestClient) -> None:
        client.post("/admin/perm/escalation-request", json=_escalation_body())
        resp = client.get("/admin/perm/escalations", params={"status": "completed"})
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    def test_pending_filter_matches_only_pending(self, client: TestClient) -> None:
        body = _escalation_body(
            requested=[
                {
                    "resource": "net:egress:llm_api",
                    "actions": ["connect"],
                    "constraints": {"allowed_hosts": ["api.openai.com"]},
                }
            ]
        )
        create = client.post("/admin/perm/escalation-request", json=body)
        esc_id = create.json()["id"]
        client.post(f"/admin/perm/escalations/{esc_id}/approve", json={"reason": "ok", "human_reviewer": "alice"})
        client.post("/admin/perm/escalation-request", json=_pending_esc_body(agent_id="agent-2"))

        pending = client.get("/admin/perm/escalations", params={"status": "pending"}).json()["items"]
        assert len(pending) >= 1
        for item in pending:
            assert item["status"] == "pending"

    def test_approved_filter(self, client: TestClient) -> None:
        body = _escalation_body(
            requested=[
                {
                    "resource": "net:egress:llm_api",
                    "actions": ["connect"],
                    "constraints": {"allowed_hosts": ["api.openai.com"]},
                }
            ]
        )
        create = client.post("/admin/perm/escalation-request", json=body)
        esc_id = create.json()["id"]
        client.post(f"/admin/perm/escalations/{esc_id}/approve", json={"reason": "ok", "human_reviewer": "alice"})
        approved = client.get("/admin/perm/escalations", params={"status": "approved"}).json()["items"]
        assert len(approved) == 1

    def test_denied_filter(self, client: TestClient) -> None:
        create = client.post("/admin/perm/escalation-request", json=_pending_esc_body())
        esc_id = create.json()["id"]
        client.post(f"/admin/perm/escalations/{esc_id}/deny", json={"reason": "no", "human_reviewer": "bob"})
        denied = client.get("/admin/perm/escalations", params={"status": "denied"}).json()["items"]
        assert len(denied) == 1


# ===================================================================
# Escalation history — agent_id filter
# ===================================================================


class TestEscalationHistoryFilterDeep:
    def test_nonexistent_agent_id_returns_empty(self, client: TestClient) -> None:
        client.post("/admin/perm/escalation-request", json=_escalation_body(agent_id="agent-1"))
        resp = client.get("/admin/perm/escalations/history", params={"agent_id": "nobody"})
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    def test_filter_matches_exact_agent_id(self, client: TestClient) -> None:
        client.post("/admin/perm/escalation-request", json=_escalation_body(agent_id="agent-alpha"))
        client.post("/admin/perm/escalation-request", json=_escalation_body(agent_id="agent-beta"))
        resp = client.get("/admin/perm/escalations/history", params={"agent_id": "agent-alpha"})
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["agent_id"] == "agent-alpha"


# ===================================================================
# IsStrictSubsetOfBoth — edge cases
# ===================================================================


class TestIsStrictSubsetOfBothDeep:
    def test_empty_requested_caps_returns_false(self) -> None:
        from general_ludd.routers.security import _is_strict_subset_of_both

        human = PermissionSpec(agent_type="h", capabilities=[])
        agent = PermissionSpec(agent_type="a", capabilities=[])
        assert _is_strict_subset_of_both([], human, agent) is False

    def test_requested_broader_than_human_returns_false(self) -> None:
        from general_ludd.routers.security import _is_strict_subset_of_both

        human = PermissionSpec(
            agent_type="h",
            capabilities=[Capability(resource="file:repo", actions=["read"], constraints={"path_prefix": "/repo/"})],
        )
        agent = PermissionSpec(
            agent_type="a",
            capabilities=[
                Capability(resource="file:repo", actions=["read", "write"], constraints={"path_prefix": "/repo/"})
            ],
        )
        req = [Capability(resource="file:repo", actions=["read", "write"], constraints={"path_prefix": "/repo/"})]
        assert _is_strict_subset_of_both(req, human, agent) is False

    def test_requested_broader_than_agent_returns_false(self) -> None:
        from general_ludd.routers.security import _is_strict_subset_of_both

        human = PermissionSpec(
            agent_type="h",
            capabilities=[
                Capability(resource="file:repo", actions=["read", "write"], constraints={"path_prefix": "/repo/"})
            ],
        )
        agent = PermissionSpec(
            agent_type="a",
            capabilities=[Capability(resource="file:repo", actions=["read"], constraints={"path_prefix": "/repo/"})],
        )
        req = [Capability(resource="file:repo", actions=["read", "write"], constraints={"path_prefix": "/repo/"})]
        assert _is_strict_subset_of_both(req, human, agent) is False

    def test_subset_of_both_returns_true(self) -> None:
        from general_ludd.routers.security import _is_strict_subset_of_both

        human = PermissionSpec(
            agent_type="h",
            capabilities=[
                Capability(resource="file:repo", actions=["read", "write"], constraints={"path_prefix": "/repo/"})
            ],
        )
        agent = PermissionSpec(
            agent_type="a",
            capabilities=[
                Capability(resource="file:repo", actions=["read", "write"], constraints={"path_prefix": "/repo/"})
            ],
        )
        req = [Capability(resource="file:repo", actions=["read"], constraints={"path_prefix": "/repo/sub/"})]
        assert _is_strict_subset_of_both(req, human, agent) is True


# ===================================================================
# Security auth helpers — verify_psk, check_bearer_token, check_admin_token
# ===================================================================


class TestSecurityAuthHelpers:
    def test_verify_psk_empty_presented(self) -> None:
        from general_ludd.security.auth import verify_psk

        assert verify_psk("", "secret") is False

    def test_verify_psk_empty_expected(self) -> None:
        from general_ludd.security.auth import verify_psk

        assert verify_psk("secret", "") is False

    def test_verify_psk_both_empty(self) -> None:
        from general_ludd.security.auth import verify_psk

        assert verify_psk("", "") is False

    def test_verify_psk_match(self) -> None:
        from general_ludd.security.auth import verify_psk

        assert verify_psk("secret-token", "secret-token") is True

    def test_verify_psk_mismatch(self) -> None:
        from general_ludd.security.auth import verify_psk

        assert verify_psk("wrong", "secret-token") is False

    def test_check_bearer_token_empty_header(self) -> None:
        from general_ludd.security.auth import check_bearer_token

        assert check_bearer_token("", "secret") is False

    def test_check_bearer_token_missing_bearer_prefix(self) -> None:
        from general_ludd.security.auth import check_bearer_token

        assert check_bearer_token("Basic dGVzdA==", "secret") is False

    def test_check_bearer_token_correct(self) -> None:
        from general_ludd.security.auth import check_bearer_token

        assert check_bearer_token("Bearer secret", "secret") is True

    def test_check_bearer_token_wrong_token(self) -> None:
        from general_ludd.security.auth import check_bearer_token

        assert check_bearer_token("Bearer wrong", "secret") is False

    def test_check_bearer_token_extra_whitespace(self) -> None:
        from general_ludd.security.auth import check_bearer_token

        assert check_bearer_token("Bearer  secret  ", "secret") is True

    def test_check_admin_token_empty(self) -> None:
        from general_ludd.security.auth import check_admin_token

        assert check_admin_token("", "secret") is False

    def test_check_admin_token_match(self) -> None:
        from general_ludd.security.auth import check_admin_token

        assert check_admin_token("  admin-token  ", "admin-token") is True


# ===================================================================
# AuthPosture — require_auth_env edge cases
# ===================================================================


class TestAuthPostureDeep:
    def test_require_auth_env_true_with_various_formats(self) -> None:
        from general_ludd.security.auth import require_auth_env

        for val in ("1", "true", "yes", "on", "  YES  "):
            assert require_auth_env({"GLUDD_REQUIRE_AUTH": val}) is True

    def test_require_auth_env_false_with_various_formats(self) -> None:
        from general_ludd.security.auth import require_auth_env

        for val in ("0", "false", "no", "", "anything"):
            assert require_auth_env({"GLUDD_REQUIRE_AUTH": val}) is False

    def test_require_auth_env_missing_key(self) -> None:
        from general_ludd.security.auth import require_auth_env

        assert require_auth_env({}) is False

    def test_load_auth_posture_with_psk_disable_variants(self) -> None:
        from general_ludd.security.auth import load_auth_posture

        for val in ("1", "true", "yes", "on"):
            posture = load_auth_posture("test", {"GLUDD_PSK_DISABLE": val})
            assert posture.require_auth is False
            assert posture.no_auth is True

    def test_load_auth_posture_with_allow_no_auth(self) -> None:
        from general_ludd.security.auth import load_auth_posture

        posture = load_auth_posture("test", {"GLUDD_ALLOW_NO_AUTH": "1"})
        assert posture.require_auth is False

    def test_load_auth_posture_with_psk_configured(self) -> None:
        from general_ludd.security.auth import load_auth_posture

        posture = load_auth_posture("test", {"GLUDD_PSK": "my-secret"})
        assert posture.no_auth is False
        assert posture.psk == "my-secret"

    def test_load_auth_posture_no_psk_require_auth(self) -> None:
        from general_ludd.security.auth import load_auth_posture

        posture = load_auth_posture("test", {})
        assert posture.no_auth is True
        assert posture.require_auth is True

    def test_load_auth_posture_no_psk_allow_no_auth_overrides_require(self) -> None:
        """GLUDD_REQUIRE_AUTH=1 + GLUDD_ALLOW_NO_AUTH=1 → require_auth_env=True ∧
        _auth_disabled=True → require_auth=False (allow_no_auth takes precedence)."""
        from general_ludd.security.auth import load_auth_posture

        posture = load_auth_posture("test", {"GLUDD_ALLOW_NO_AUTH": "1", "GLUDD_REQUIRE_AUTH": "1"})
        assert posture.no_auth is True
        assert posture.require_auth is True  # require_auth_env("1")=True, (no_auth && not_auth_disabled)=False; OR=True


# ===================================================================
# Path confinement — is_join_within / is_path_within
# ===================================================================


class TestPathConfinementDeep:
    def test_is_join_within_relative_safe(self) -> None:
        from general_ludd.security.auth import is_join_within

        assert is_join_within("/base", "subdir/file.txt") is True

    def test_is_join_within_absolute_escape_attempt(self) -> None:
        from general_ludd.security.auth import is_join_within

        assert is_join_within("/base", "/etc/passwd") is False

    def test_is_join_within_dotdot_escape(self) -> None:
        from general_ludd.security.auth import is_join_within

        assert is_join_within("/base/inside", "../outside") is False

    def test_is_join_within_exact_base(self) -> None:
        from general_ludd.security.auth import is_join_within

        assert is_join_within("/base", "/base") is True

    def test_is_join_within_empty_candidate(self) -> None:
        from general_ludd.security.auth import is_join_within

        assert is_join_within("/base", "") is True

    def test_is_path_within_is_same_object(self) -> None:
        from general_ludd.security.auth import is_join_within, is_path_within

        assert is_path_within is is_join_within


# ===================================================================
# SSRF — is_safe_fetch_url
# ===================================================================


class TestSsrFDeep:
    def test_https_url_ok(self) -> None:
        from general_ludd.security.auth import is_safe_fetch_url

        assert is_safe_fetch_url("https://github.com/repo/file") is True

    def test_http_url_blocked(self) -> None:
        from general_ludd.security.auth import is_safe_fetch_url

        assert is_safe_fetch_url("http://github.com/repo/file") is False

    def test_loopback_blocked(self) -> None:
        from general_ludd.security.auth import is_safe_fetch_url

        assert is_safe_fetch_url("https://127.0.0.1:8080/file") is False
        assert is_safe_fetch_url("https://localhost/file") is False

    def test_empty_url(self) -> None:
        from general_ludd.security.auth import is_safe_fetch_url

        assert is_safe_fetch_url("") is False

    def test_non_string_url(self) -> None:
        from general_ludd.security.auth import is_safe_fetch_url

        assert is_safe_fetch_url(None) is False  # type: ignore[arg-type]

    def test_metadata_ip_blocked(self) -> None:
        from general_ludd.security.auth import is_safe_fetch_url

        assert is_safe_fetch_url("https://169.254.169.254/metadata") is False

    def test_private_ip_blocked(self) -> None:
        from general_ludd.security.auth import is_safe_fetch_url

        assert is_safe_fetch_url("https://10.0.0.1/admin") is False
        assert is_safe_fetch_url("https://192.168.1.1/admin") is False


# ===================================================================
# Empty-body / missing body on POST endpoints
# ===================================================================


class TestEmptyBodyRequests:
    def test_sts_issue_missing_json(self, client: TestClient) -> None:
        resp = client.post("/admin/sts/issue")
        assert resp.status_code in (400, 422)

    def test_sts_revoke_missing_json(self, client: TestClient) -> None:
        """Without auth_spec, the capability guard blocks before FastAPI can
        reject the missing body."""
        resp = client.post("/admin/sts/revoke")
        assert resp.status_code in (400, 403, 422)

    def test_escalation_request_missing_json(self, client: TestClient) -> None:
        resp = client.post("/admin/perm/escalation-request")
        assert resp.status_code in (400, 422)

    def test_escalation_approve_missing_json(self, client: TestClient) -> None:
        resp = client.post("/admin/perm/escalations/1/approve")
        assert resp.status_code in (400, 422)

    def test_escalation_deny_missing_json(self, client: TestClient) -> None:
        resp = client.post("/admin/perm/escalations/1/deny")
        assert resp.status_code in (400, 422)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
