"""Security admin router: STS token lifecycle + permission-spec management.

PSK-gated centrally by the daemon's ``auth_and_stats_middleware`` (every
``/admin/*`` path is challenged). This router does NOT re-check the PSK.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from general_ludd.security.permissions import (
    Capability,
    PermissionDeniedError,
    PermissionSpec,
    PermissionSpecParser,
    PermissionSubject,
)
from general_ludd.security.sts import StsAuditLog, StsIssuer


def _get_issuer(app: FastAPI) -> StsIssuer:
    iss = getattr(app.state, "_sts_issuer", None)
    if iss is None:
        iss = StsIssuer()
        app.state._sts_issuer = iss
    return iss


def _get_audit_log(app: FastAPI) -> StsAuditLog:
    log = getattr(app.state, "_sts_audit_log", None)
    if log is None:
        log = StsAuditLog()
        app.state._sts_audit_log = log
    return log


def _get_issuer_spec(app: FastAPI) -> PermissionSpec:
    """Resolve the issuer's PermissionSpec.

    Priority: app.state._sts_issuer_spec (explicit override, set by tests or
    daemon wiring) > default_spec("primary") from the shipped config defaults.
    """
    spec: PermissionSpec | None = getattr(app.state, "_sts_issuer_spec", None)
    if spec is not None:
        return spec
    from general_ludd.security.permissions import default_spec
    return default_spec("primary")


def _get_human_spec(app: FastAPI) -> PermissionSpec:
    """Resolve the active human user's PermissionSpec.

    Priority: app.state._human_spec (explicit, set per-session by the daemon
    or by tests) > config/permissions/<default_human_role>.yml if it exists
    > the in-code ``default_human_spec("human-operator")`` factory.
    """
    spec: PermissionSpec | None = getattr(app.state, "_human_spec", None)
    if spec is not None:
        return spec
    from general_ludd.security.permissions import default_human_spec
    role = getattr(app.state, "_default_human_role", None) or "human-operator"
    # Try config/permissions/<role>.yml first (operator overrides).
    config_dir = getattr(app.state, "_config_dir", None) or "."
    path = Path(config_dir) / "permissions" / f"{role}.yml"
    if path.exists():
        try:
            return PermissionSpecParser.parse_file(path)
        except Exception:
            pass
    return default_human_spec(role)


# ---------------------------------------------------------------------------
# Escalation store
#
# In-memory by default (single-worker daemon). A DB-backed repository can
# replace this without changing the endpoint surface; the schema is
# PermissionEscalationRequestModel (migration 013).
# ---------------------------------------------------------------------------


def _get_esc_store(app: FastAPI) -> list[dict[str, Any]]:
    store = getattr(app.state, "_escalation_store", None)
    if store is None:
        store = []
        app.state._escalation_store = store
    return store


def _esc_counter(app: FastAPI) -> int:
    counter = getattr(app.state, "_escalation_counter", None)
    if counter is None:
        counter = [0]
        app.state._escalation_counter = counter
    counter[0] += 1
    return int(counter[0])


def _caps_to_yaml(caps: list[dict[str, Any]]) -> str:
    return str(yaml.safe_dump(caps, sort_keys=False))


def _caps_from_yaml(caps_yaml: str) -> list[Capability]:
    data = yaml.safe_load(caps_yaml) or []
    out: list[Capability] = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        out.append(
            Capability(
                resource=str(item.get("resource", "")),
                actions=list(item.get("actions") or []),
                constraints=dict(item.get("constraints") or {}),
            )
        )
    return out


def _spec_from_caps_yaml(agent_id: str, caps_yaml: str) -> PermissionSpec:
    """Build a spec from a list-of-caps YAML blob (no spec envelope)."""
    caps = _caps_from_yaml(caps_yaml)
    return PermissionSpec(
        agent_type=f"escalation:{agent_id}",
        capabilities=caps,
        subject=PermissionSubject.STS_TOKEN,
    )


def _is_strict_subset_of_both(
    requested_caps: list[Capability],
    human_spec: PermissionSpec,
    agent_spec: PermissionSpec,
) -> bool:
    """Auto-approval predicate.

    True iff every requested capability is a strict subset of BOTH the human
    spec and the agent spec — i.e. the agent would have had this if not for
    an overly-narrow intersection.
    """
    if not requested_caps:
        return False
    req = PermissionSpec(
        agent_type="requested",
        capabilities=requested_caps,
        subject=PermissionSubject.STS_TOKEN,
    )
    return (
        PermissionSpecParser.is_subset(req, human_spec)
        and PermissionSpecParser.is_subset(req, agent_spec)
    )


def _perms_dir(app: FastAPI) -> Path:
    """Return the permissions directory Path, creating it if needed."""
    import os
    config_dir = getattr(app.state, "_config_dir", None) or os.getcwd()
    perms = Path(config_dir) / "permissions"
    perms.mkdir(parents=True, exist_ok=True)
    return perms


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:

    @app.post("/admin/sts/issue")
    async def admin_sts_issue(req: dict[str, Any]) -> Any:
        subject_agent_id = req.get("subject_agent_id")
        requested_yaml = req.get("requested_spec_yaml")
        ttl = int(req.get("ttl_seconds", 3600))
        if not subject_agent_id or not requested_yaml:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "subject_agent_id and requested_spec_yaml are required"
                },
            )
        try:
            subject_spec = PermissionSpecParser.parse(requested_yaml)
        except Exception as exc:
            return JSONResponse(status_code=400, content={"error": f"invalid requested_spec_yaml: {exc}"})
        errors = PermissionSpecParser.validate(subject_spec)
        if errors:
            return JSONResponse(status_code=400, content={"error": "spec validation failed", "details": errors})
        issuer_spec = _get_issuer_spec(app)
        issuer = _get_issuer(app)
        issuer_id = req.get("issuer_agent_id") or issuer_spec.agent_type
        try:
            token = issuer.issue(
                issuer_spec=issuer_spec,
                subject_spec_request=subject_spec,
                issuer_id=issuer_id,
                subject_id=subject_agent_id,
                ttl_seconds=ttl,
            )
        except PermissionDeniedError as exc:
            return JSONResponse(status_code=403, content={"error": "permission_denied", "detail": str(exc)})
        audit = _get_audit_log(app)
        audit.record_issue(token)
        return {
            "token_id": token.token_id,
            "expires_at": token.expires_at,
            "issued_at": token.issued_at,
            "spec_yaml": requested_yaml,
        }

    @app.get("/admin/sts/active")
    async def admin_sts_active() -> Any:
        issuer = _get_issuer(app)
        tokens = issuer.list_active()
        return {"tokens": [
            {
                "token_id": t.token_id,
                "issuer_agent_id": t.issuer_agent_id,
                "subject_agent_id": t.subject_agent_id,
                "issued_at": t.issued_at,
                "expires_at": t.expires_at,
                "last_used_at": t.last_used_at,
                "use_count": t.use_count,
            }
            for t in tokens
        ]}

    @app.post("/admin/sts/revoke")
    async def admin_sts_revoke(req: dict[str, Any]) -> Any:
        token_id = req.get("token_id")
        if not token_id:
            return JSONResponse(status_code=400, content={"error": "token_id is required"})
        issuer = _get_issuer(app)
        dropped = issuer.revoke(token_id)
        if not dropped:
            return JSONResponse(status_code=404, content={"error": "token not found or already expired"})
        audit = _get_audit_log(app)
        audit.record_expiry(token_id)
        return {"revoked": True, "token_id": token_id}

    @app.get("/admin/sts/audit")
    async def admin_sts_audit(
        agent_id: str | None = None,
        since: float | None = None,
        capability: str | None = None,
    ) -> Any:
        audit = _get_audit_log(app)
        events = audit.query(agent_id=agent_id, since=since, capability=capability)
        return {"events": events}

    @app.get("/admin/perm/spec/{agent_type}")
    async def admin_perm_spec_get(agent_type: str) -> Any:
        import yaml

        from general_ludd.security.permissions import default_spec
        perms = _perms_dir(app)
        path = perms / f"{agent_type}.yml"
        if not path.exists():
            spec = default_spec(agent_type)
            spec_yaml = yaml.safe_dump(
                {
                    "version": spec.version,
                    "agent_type": spec.agent_type,
                    "parent_agent_id": spec.parent_agent_id,
                    "max_sts_ttl_seconds": spec.max_sts_ttl_seconds,
                    "max_subagent_permissions": spec.max_subagent_permissions,
                    "capabilities": [
                        {
                            "resource": c.resource,
                            "actions": list(c.actions),
                            "constraints": dict(c.constraints),
                        }
                        for c in spec.capabilities
                    ],
                    "denied": [
                        {
                            "resource": c.resource,
                            "actions": list(c.actions),
                            "constraints": dict(c.constraints),
                        }
                        for c in spec.denied
                    ],
                },
                sort_keys=False,
            )
            return {"agent_type": agent_type, "spec_yaml": spec_yaml}
        spec_yaml = path.read_text()
        return {"agent_type": agent_type, "spec_yaml": spec_yaml}

    @app.put("/admin/perm/spec/{agent_type}")
    async def admin_perm_spec_put(agent_type: str, req: dict[str, Any]) -> Any:
        spec_yaml = req.get("spec_yaml")
        if not spec_yaml:
            return JSONResponse(status_code=400, content={"error": "spec_yaml is required"})
        try:
            spec = PermissionSpecParser.parse(spec_yaml)
        except Exception as exc:
            return JSONResponse(status_code=400, content={"error": f"parse error: {exc}"})
        errors = PermissionSpecParser.validate(spec)
        if errors:
            return JSONResponse(status_code=400, content={"error": "validation failed", "details": errors})
        perms = _perms_dir(app)
        path = perms / f"{agent_type}.yml"
        path.write_text(spec_yaml)
        return {"status": "saved", "agent_type": agent_type, "path": str(path)}

    @app.get("/admin/perm/spec")
    async def admin_perm_spec_list() -> Any:
        perms = _perms_dir(app)
        agent_types = sorted(p.stem for p in perms.glob("*.yml") if p.is_file())
        return {"agent_types": agent_types}

    # -----------------------------------------------------------------
    # Permission-escalation request flow
    # -----------------------------------------------------------------

    @app.post("/admin/perm/escalation-request")
    async def admin_perm_escalation_request(req: dict[str, Any]) -> Any:
        agent_id = req.get("agent_id")
        current_yaml = req.get("current_spec_yaml")
        requested_raw = req.get("requested_additional_capabilities")
        reason = req.get("reason")
        alternatives = req.get("alternatives_tried") or []
        if not agent_id or not current_yaml or not requested_raw or not reason:
            return JSONResponse(
                status_code=400,
                content={
                    "error": (
                        "agent_id, current_spec_yaml, "
                        "requested_additional_capabilities, reason are required"
                    ),
                },
            )
        # Validation: at least 3 distinct approaches documented.
        approaches = {
            str(a.get("approach", "")).strip()
            for a in alternatives
            if isinstance(a, dict) and a.get("approach")
        }
        if len(approaches) < 3:
            return JSONResponse(
                status_code=422,
                content={
                    "error": (
                        "insufficient alternatives_tried — agent must "
                        "document at least 3 distinct approaches attempted"
                    ),
                    "distinct_approaches_count": len(approaches),
                },
            )
        try:
            requested_caps = _caps_from_yaml(_caps_to_yaml(requested_raw))
        except Exception as exc:
            return JSONResponse(status_code=400, content={"error": f"invalid requested_additional_capabilities: {exc}"})

        human_spec = _get_human_spec(app)
        agent_spec = _get_issuer_spec(app)
        auto = _is_strict_subset_of_both(requested_caps, human_spec, agent_spec)
        status = "auto_approved" if auto else "pending"
        decided_reason = (
            "requested capabilities are within human+agent intersection; auto-granted"
            if auto else None
        )

        esc_id = _esc_counter(app)
        row: dict[str, Any] = {
            "id": esc_id,
            "agent_id": agent_id,
            "current_spec_yaml": current_yaml,
            "requested_capabilities_yaml": _caps_to_yaml(requested_raw),
            "reason": reason,
            "alternatives_tried_json": json.dumps(alternatives),
            "status": status,
            "human_reviewer": None,
            "decided_at": (datetime.now(UTC).isoformat() if auto else None),
            "decided_reason": decided_reason,
            "created_at": datetime.now(UTC).isoformat(),
        }
        _get_esc_store(app).append(row)

        # For auto_approved: immediately mint an STS token scoped to the
        # intersection of (current + requested) and the human spec.
        sts_token_id = None
        if auto:
            try:
                current_spec = PermissionSpecParser.parse(current_yaml)
            except Exception:
                current_spec = PermissionSpec(agent_type=agent_id, capabilities=[])
            augmented_caps = list(current_spec.capabilities) + requested_caps
            augmented = PermissionSpec(
                agent_type=agent_id,
                capabilities=augmented_caps,
                subject=PermissionSubject.STS_TOKEN,
            )
            effective = PermissionSpecParser.intersection(augmented, human_spec)
            issuer = _get_issuer(app)
            ttl = min(effective.max_sts_ttl_seconds, 3600)
            try:
                token = issuer.issue(
                    issuer_spec=agent_spec,
                    subject_spec_request=effective,
                    issuer_id=f"escalation-auto:{esc_id}",
                    subject_id=agent_id,
                    ttl_seconds=ttl,
                )
                sts_token_id = token.token_id
                _get_audit_log(app).record_issue(token)
            except PermissionDeniedError:
                # Conservative: if for any reason the auto-grant cannot be
                # minted, demote to pending for human review.
                row["status"] = "pending"
                row["decided_reason"] = None
                row["decided_at"] = None

        return JSONResponse(
            status_code=201,
            content={
                "id": esc_id,
                "status": row["status"],
                "decided_reason": row["decided_reason"],
                "sts_token_id": sts_token_id,
            },
        )

    @app.get("/admin/perm/escalations")
    async def admin_perm_escalations_list(status: str | None = None) -> Any:
        store = _get_esc_store(app)
        items = [dict(r) for r in store]
        if status:
            items = [i for i in items if i.get("status") == status]
        items.sort(key=lambda r: r.get("id", 0))
        return {"items": items}

    @app.get("/admin/perm/escalations/history")
    async def admin_perm_escalations_history(agent_id: str | None = None) -> Any:
        store = _get_esc_store(app)
        items = [dict(r) for r in store]
        if agent_id:
            items = [i for i in items if i.get("agent_id") == agent_id]
        items.sort(key=lambda r: r.get("id", 0))
        return {"items": items}

    def _find_esc(esc_id: int) -> dict[str, Any] | None:
        for row in _get_esc_store(app):
            if row.get("id") == esc_id:
                return row
        return None

    @app.post("/admin/perm/escalations/{esc_id}/approve")
    async def admin_perm_escalation_approve(esc_id: int, req: dict[str, Any]) -> Any:
        row = _find_esc(esc_id)
        if row is None:
            return JSONResponse(status_code=404, content={"error": "escalation not found"})
        if row["status"] != "pending":
            return JSONResponse(
                status_code=409,
                content={"error": f"escalation already {row['status']}"},
            )
        reason = req.get("reason") or "approved"
        human_spec = _get_human_spec(app)
        agent_spec = _get_issuer_spec(app)
        # Build the augmented spec (current + requested), then intersect
        # with human spec — human CANNOT grant more than they have.
        try:
            current_spec = PermissionSpecParser.parse(row["current_spec_yaml"])
        except Exception:
            current_spec = PermissionSpec(agent_type=row["agent_id"], capabilities=[])
        requested_caps = _caps_from_yaml(row["requested_capabilities_yaml"])
        augmented = PermissionSpec(
            agent_type=row["agent_id"],
            capabilities=list(current_spec.capabilities) + requested_caps,
            subject=PermissionSubject.STS_TOKEN,
        )
        effective = PermissionSpecParser.intersection(augmented, human_spec)
        issuer = _get_issuer(app)
        ttl = min(effective.max_sts_ttl_seconds, 3600)
        try:
            token = issuer.issue(
                issuer_spec=agent_spec,
                subject_spec_request=effective,
                issuer_id=f"escalation:{esc_id}",
                subject_id=row["agent_id"],
                ttl_seconds=ttl,
            )
        except PermissionDeniedError as exc:
            return JSONResponse(
                status_code=403,
                content={"error": "permission_denied", "detail": str(exc)},
            )
        _get_audit_log(app).record_issue(token)
        row["status"] = "approved"
        row["human_reviewer"] = req.get("human_reviewer")
        row["decided_at"] = datetime.now(UTC).isoformat()
        row["decided_reason"] = reason
        return {
            "id": esc_id,
            "status": "approved",
            "sts_token_id": token.token_id,
            "decided_reason": reason,
        }

    @app.post("/admin/perm/escalations/{esc_id}/deny")
    async def admin_perm_escalation_deny(esc_id: int, req: dict[str, Any]) -> Any:
        row = _find_esc(esc_id)
        if row is None:
            return JSONResponse(status_code=404, content={"error": "escalation not found"})
        if row["status"] != "pending":
            return JSONResponse(
                status_code=409,
                content={"error": f"escalation already {row['status']}"},
            )
        reason = req.get("reason")
        if not reason or not str(reason).strip():
            return JSONResponse(
                status_code=400,
                content={"error": "reason is required to deny an escalation"},
            )
        row["status"] = "denied"
        row["human_reviewer"] = req.get("human_reviewer")
        row["decided_at"] = datetime.now(UTC).isoformat()
        row["decided_reason"] = str(reason)
        return {"id": esc_id, "status": "denied", "decided_reason": reason}
