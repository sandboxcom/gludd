"""C14 — Permissions / capability lattice regression tests.

Pins the four C14 defects (docs/AGENTIC_IMPLEMENTATION_SPEC.md §C14 +
docs/design/WAVE_C_DESIGNS_2026-07-10.md C-SEC-1 / C-SEC-1b):

1. ``test_deny_lists_are_consistent`` — the protected-path deny-lists must not
   drift between ``security/capability_lattice.py`` (the canonical daemon-side
   source) and the ``self_update`` apply modules.
2. ``test_intersect_constraints_narrows_not_widens`` — the spec-intersection is
   a lattice MEET: the result must be a subset of BOTH inputs, never a widening
   of either (property-test ``result ⊆ a`` and ``result ⊆ b``).
3. ``test_sts_redelegation_does_not_escalate_ttl`` — an STS token minted from an
   already-narrowed spec can never carry a TTL longer than that spec permits
   (monotonic non-increasing TTL down a delegation chain).
4. ``test_denied_grants_not_propagated`` — a ``denied`` grant is enforced
   through delegation: it is honoured at ``validate``, propagated into a minted
   child token, and blocks a re-delegation request for the denied action.
"""

from __future__ import annotations

import pytest

from general_ludd.security.permissions import (
    Capability,
    PermissionDeniedError,
    PermissionSpec,
    PermissionSpecParser,
    PermissionSubject,
)
from general_ludd.security.sts import StsIssuer, StsToken

# ---------------------------------------------------------------------------
# helpers (match the construction conventions in test_permission_intersection)
# ---------------------------------------------------------------------------


def _file_cap(prefix: str, actions: list[str]) -> Capability:
    return Capability(
        resource="file:repo", actions=actions, constraints={"path_prefix": prefix}
    )


def _net_cap(
    *,
    hosts: list[str] | None = None,
    ports: list[int] | None = None,
    actions: list[str] | None = None,
) -> Capability:
    constraints: dict[str, object] = {}
    if hosts is not None:
        constraints["allowed_hosts"] = hosts
    if ports is not None:
        constraints["allowed_ports"] = ports
    return Capability(
        resource="net:egress:any",
        actions=actions or ["connect"],
        constraints=constraints,
    )


def _secret_cap(paths: list[str], actions: list[str]) -> Capability:
    return Capability(
        resource="secret:openbao",
        actions=actions,
        constraints={"openbao_paths": paths},
    )


def _spec(
    caps: list[Capability],
    *,
    agent_type: str = "x",
    denied: list[Capability] | None = None,
    max_ttl: int = 3600,
    subject: PermissionSubject = PermissionSubject.AGENT,
) -> PermissionSpec:
    return PermissionSpec(
        version=1,
        agent_type=agent_type,
        parent_agent_id=None,
        capabilities=caps,
        denied=denied or [],
        max_sts_ttl_seconds=max_ttl,
        max_subagent_permissions="same_or_fewer",
        subject=subject,
    )


# ---------------------------------------------------------------------------
# 1. Deny-list drift
# ---------------------------------------------------------------------------


def test_deny_lists_are_consistent() -> None:
    """The protected-path deny-lists share ONE canonical source (no drift).

    ``capability_lattice.PROTECTED_PATH_SEGMENTS`` is the canonical daemon-side
    set of harness control-surface directory segments. The ``self_update`` apply
    ladder MUST derive its hard-deny segments from that set rather than keeping
    an independent literal that can silently drift, and the applier's broader
    marker list MUST cover every canonical segment.
    """
    from general_ludd.security import capability_lattice as lattice
    from general_ludd.self_update import applier as su_applier
    from general_ludd.self_update import apply as su_apply

    canonical = set(lattice.PROTECTED_PATH_SEGMENTS)
    assert canonical, "canonical segment set must not be empty"

    # apply.py's hard-deny segments are DERIVED from the canonical set (single
    # source of truth) — not an independently-maintained literal.
    assert set(su_apply._HARD_DENY_SEGMENTS) == canonical
    assert tuple(sorted(canonical)) == su_apply._HARD_DENY_SEGMENTS

    # applier.py's (deliberately broader, decoupled) marker list must still
    # cover every canonical protected segment so the two modules cannot diverge.
    markers_lower = {m.lower() for m in su_applier.PROTECTED_PATH_MARKERS}
    for seg in canonical:
        assert seg.lower() in markers_lower, (
            f"applier deny-list drifted: missing canonical segment {seg!r}"
        )

    # The permission/policy modules themselves stay protected stems — the
    # lattice remains the authority for what may never be self-modified.
    assert {"permissions", "permission", "capability_lattice"} <= (
        lattice.PROTECTED_FILE_STEMS
    )


# ---------------------------------------------------------------------------
# 2. Intersection must narrow, never widen (result ⊆ both inputs)
# ---------------------------------------------------------------------------


def _assert_meet(a: PermissionSpec, b: PermissionSpec) -> PermissionSpec:
    """Intersect ``a`` and ``b`` and assert the result is a subset of both."""
    result = PermissionSpecParser.intersection(a, b)
    assert PermissionSpecParser.is_subset(result, a), (
        f"intersection widened past a: {result.capabilities} !⊆ {a.capabilities}"
    )
    assert PermissionSpecParser.is_subset(result, b), (
        f"intersection widened past b: {result.capabilities} !⊆ {b.capabilities}"
    )
    return result


def test_intersect_constraints_narrows_not_widens() -> None:
    """The intersection is a lattice MEET: result ⊆ a AND result ⊆ b, always."""
    # --- file: nested prefixes -> the narrower wins, never the wider ---------
    a = _spec([_file_cap("/repo/", ["read", "write"])])
    b = _spec([_file_cap("/repo/sub/", ["read"])])
    result = _assert_meet(a, b)
    cap = result.capability_for("file:repo")
    assert cap is not None
    assert cap.constraints["path_prefix"] == "/repo/sub/"  # narrower
    assert set(cap.actions) == {"read"}  # intersected actions

    # --- file: disjoint prefixes -> capability dropped (no shared scope) -----
    a2 = _spec([_file_cap("/repo/", ["read"])])
    b2 = _spec([_file_cap("/etc/", ["read"])])
    result2 = _assert_meet(a2, b2)
    assert result2.capability_for("file:repo") is None

    # --- net: one side constrains ports, the other does NOT ------------------
    # ``a`` restricts egress to port 443; ``b`` restricts only hosts (ports
    # unconstrained). The MEET must PRESERVE a's port restriction — a subagent
    # must never gain "all ports" it did not previously hold. This is the live
    # widening defect: dropping the port key silently re-opens every port.
    a3 = _spec([_net_cap(hosts=["x", "y"], ports=[443])])
    b3 = _spec([_net_cap(hosts=["x"])])
    result3 = _assert_meet(a3, b3)
    cap3 = result3.capability_for("net:egress:any")
    assert cap3 is not None
    assert cap3.constraints.get("allowed_hosts") == ["x"]  # host intersection
    assert cap3.constraints.get("allowed_ports") == [443], (
        "port constraint was widened away — subagent gained ports it never held"
    )

    # --- secret: openbao_paths set-intersected, never widened ----------------
    # (secret openbao_paths intersection keeps only the shared exact patterns;
    #  disjoint exact globs -> dropped. Same-pattern -> kept.)
    a5 = _spec([_secret_cap(["p/*", "q/*"], ["read"])])
    b5 = _spec([_secret_cap(["q/*"], ["read"])])
    result5 = _assert_meet(a5, b5)
    cap5 = result5.capability_for("secret:openbao")
    assert cap5 is not None
    assert cap5.constraints["openbao_paths"] == ["q/*"]


# ---------------------------------------------------------------------------
# 3. STS re-delegation must not escalate TTL
# ---------------------------------------------------------------------------


def test_sts_redelegation_does_not_escalate_ttl() -> None:
    """A token minted from a narrowed spec cannot outlive that spec's ceiling.

    The ``subject_spec_request`` here is an already-narrowed spec whose own
    ``max_sts_ttl_seconds`` is 300 (e.g. produced by a prior intersection with a
    time-limited human spec). Even though the broad issuer allows 3600 and the
    caller requests 3600, the minted token must be clamped to 300 — TTL is
    monotonically non-increasing down a delegation chain.
    """
    clock = [1000.0]
    issuer = StsIssuer(clock=lambda: clock[0])

    issuer_spec = _spec(
        [_file_cap("/repo/", ["read", "write"])],
        agent_type="primary",
        max_ttl=3600,
    )
    # A re-delegated / narrowed spec: same-or-fewer caps, but a 300s ceiling.
    narrowed = _spec(
        [_file_cap("/repo/sub/", ["read"])],
        agent_type="sts_token",
        max_ttl=300,
    )

    token = issuer.issue(
        issuer_spec=issuer_spec,
        subject_spec_request=narrowed,
        issuer_id="agent-A",
        subject_id="agent-B",
        ttl_seconds=3600,
    )

    lifetime = token.expires_at - token.issued_at
    assert lifetime == 300, (
        f"TTL escalated on re-delegation: got {lifetime}s, expected <= 300s"
    )
    # The stored spec's own ceiling is also clamped so a further hop cannot
    # re-raise it.
    assert token.spec.max_sts_ttl_seconds == 300

    # Sanity: a broad request against a broad spec still caps at the issuer max.
    wide = _spec([_file_cap("/repo/", ["read"])], max_ttl=3600)
    token2 = issuer.issue(
        issuer_spec=_spec([_file_cap("/repo/", ["read"])], max_ttl=1800),
        subject_spec_request=wide,
        issuer_id="agent-A",
        subject_id="agent-C",
        ttl_seconds=99999,
    )
    assert token2.expires_at - token2.issued_at == 1800


# ---------------------------------------------------------------------------
# 4. Denied grants are enforced through delegation
# ---------------------------------------------------------------------------


def test_denied_grants_not_propagated() -> None:
    """A ``denied`` grant blocks the action at validate, on mint, and on subset.

    Three enforcement points that a delegated (STS) permission must honour:

    a. ``validate`` — a denial beats a positive grant on the SAME spec.
    b. ``issue`` — the issuer's denials are propagated into the minted child
       token so the denial survives one hop of delegation.
    c. ``is_subset`` — a subagent may not REQUEST an action its issuer denies,
       so a re-delegation attempting to grant the denied action is refused.
    """
    clock = [1000.0]
    issuer = StsIssuer(clock=lambda: clock[0])

    # --- (a) validate: denial beats a positive grant on the same spec --------
    spec_with_denial = _spec(
        [_file_cap("/repo/", ["read", "write"])],
        denied=[Capability(resource="file:repo", actions=["write"])],
    )
    tok = StsToken(
        token_id="tok-a",
        issuer_agent_id="agent-A",
        subject_agent_id="agent-B",
        spec=spec_with_denial,
        issued_at=1000.0,
        expires_at=2000.0,
    )
    assert issuer.validate(tok, Capability("file:repo", ["read"])) is True
    assert issuer.validate(tok, Capability("file:repo", ["write"])) is False, (
        "denied action slipped through validate — a denied grant is inert"
    )

    # --- (b) issue: the issuer's denials propagate into the child token ------
    issuer_spec = _spec(
        [_file_cap("/repo/", ["read"])],
        denied=[Capability(resource="net:egress:any", actions=["connect"])],
    )
    subject_request = _spec([_file_cap("/repo/", ["read"])])
    child = issuer.issue(
        issuer_spec=issuer_spec,
        subject_spec_request=subject_request,
        issuer_id="agent-A",
        subject_id="agent-B",
        ttl_seconds=600,
    )
    child_denied = {(d.resource, tuple(sorted(d.actions))) for d in child.spec.denied}
    assert ("net:egress:any", ("connect",)) in child_denied, (
        "issuer denial did not propagate into the delegated token spec"
    )
    # And the propagated denial is enforced on the child token.
    assert (
        issuer.validate(child, Capability("net:egress:any", ["connect"])) is False
    )

    # --- (c) is_subset: cannot request an action the issuer denies -----------
    issuer_rw = _spec(
        [_file_cap("/repo/", ["read", "write"])],
        denied=[Capability(resource="file:repo", actions=["write"])],
    )
    request_denied_action = _spec([_file_cap("/repo/", ["read", "write"])])
    assert (
        PermissionSpecParser.is_subset(request_denied_action, issuer_rw) is False
    ), "is_subset let a request include an action the issuer explicitly denies"

    with pytest.raises(PermissionDeniedError):
        issuer.issue(
            issuer_spec=issuer_rw,
            subject_spec_request=request_denied_action,
            issuer_id="agent-A",
            subject_id="agent-B",
            ttl_seconds=600,
        )


def test_path_scoped_denial_propagates_without_blocking_delegation() -> None:
    """A scoped carve-out travels with a broad grant instead of denying it all."""
    denied_path = "secret/data/gludd/build/prod-signing-key"
    issuer_spec = _spec(
        [_secret_cap(["secret/data/gludd/build/*"], ["read"])],
        denied=[_secret_cap([denied_path], ["read"])],
    )
    subject_request = _spec(
        [_secret_cap(["secret/data/gludd/build/*"], ["read"])]
    )

    assert PermissionSpecParser.is_subset(subject_request, issuer_spec) is True
    token = StsIssuer(clock=lambda: 1000.0).issue(
        issuer_spec=issuer_spec,
        subject_spec_request=subject_request,
        issuer_id="agent-A",
        subject_id="agent-B",
        ttl_seconds=600,
    )
    assert token.spec.is_denied("secret:openbao", "read", denied_path) is True
    assert (
        token.spec.is_denied(
            "secret:openbao", "read", "secret/data/gludd/build/safe-key"
        )
        is False
    )


def test_denied_union_preserves_distinct_scopes() -> None:
    """Equal resource/actions with different constraints are separate denials."""
    a = _spec(
        [_secret_cap(["*"], ["read"])],
        denied=[_secret_cap(["secret-a"], ["read"])],
    )
    b = _spec(
        [_secret_cap(["*"], ["read"])],
        denied=[_secret_cap(["secret-b"], ["read"])],
    )

    intersected = PermissionSpecParser.intersection(a, b)
    denied_paths = {
        tuple(cap.constraints["openbao_paths"]) for cap in intersected.denied
    }
    assert denied_paths == {("secret-a",), ("secret-b",)}

    token = StsIssuer(clock=lambda: 1000.0).issue(
        issuer_spec=a,
        subject_spec_request=b,
        issuer_id="agent-A",
        subject_id="agent-B",
        ttl_seconds=600,
    )
    delegated_paths = {
        tuple(cap.constraints["openbao_paths"]) for cap in token.spec.denied
    }
    assert delegated_paths == {("secret-a",), ("secret-b",)}


def test_secrets_manager_enforces_denied_paths() -> None:
    """A path-scoped ``denied`` carve-out is enforced at the secrets gate.

    The C-SEC-1 scenario: "allow read on build/* EXCEPT prod-signing-key". The
    denial must win over the broad allow for the carved-out path while leaving
    every other path readable.
    """
    from general_ludd.secrets.manager import (
        SecretPermissionDeniedError,
        SecretsManager,
    )

    spec = _spec(
        [_secret_cap(["secret/data/gludd/build/*"], ["read"])],
        denied=[
            _secret_cap(["secret/data/gludd/build/prod-signing-key"], ["read"])
        ],
    )
    mgr = SecretsManager(permission_spec=spec)

    # The carved-out path is refused even though the broad allow would match.
    with pytest.raises(SecretPermissionDeniedError):
        mgr._enforce_permission(
            "secret/data/gludd/build/prod-signing-key", action="read"
        )

    # A sibling path under the same allow glob is still permitted (no raise).
    mgr._enforce_permission("secret/data/gludd/build/other-key", action="read")
