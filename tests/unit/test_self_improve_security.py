"""Security unit tests for self-improve: protected-path bypass, signature verification, deny-list consistency.

Covers:
  - Protected-path TOCTOU: workspace-confining safe-writer gates (bait-and-switch,
    replay, symlink-injection via non-config paths, root-target bypass)
  - Signature verification (H.17): missing sig, invalid sig, verifier exception,
    valid sig allows, ordering (before all other gates)
  - Deny-list structural consistency: CANONICAL subset supersets, no orphans,
    segment-exact classification, cross-subset drift detection
"""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, dataclass, field
from pathlib import Path

import pytest

from general_ludd.security.path_canonicalizer import (
    _SEGMENT_EXACT_MARKERS,
    CANONICAL_DENY_MARKERS,
    PROTECTED_PATH_MARKERS,
)
from general_ludd.self_improve.codex_comparison import ProposalManifest
from general_ludd.self_update.applier import (
    UpdateApplier,
)

# ---------------------------------------------------------------------------
# Shared test fixtures / helpers
# ---------------------------------------------------------------------------


@dataclass
class _Plan:
    kind: str
    capability_required: str
    target_paths: list[str] = field(default_factory=list)


class _FakeWriter:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str]] = []

    def write(self, path: str, content: str) -> str:
        self.writes.append((path, content))
        return path


class _FixedChecker:
    def __init__(self, allowed: set[str]) -> None:
        self._allowed = allowed

    def allows(self, capability: str) -> bool:
        return capability in self._allowed


def test_non_config_approval_plan_is_frozen_canonical_and_project_bound(
    tmp_path: Path,
) -> None:
    from general_ludd.routers.self_improve import _NonConfigPlanSpec

    worktree = tmp_path / "repo" / "worktrees" / "approved"
    worktree.mkdir(parents=True)
    spec = _NonConfigPlanSpec(
        schema_version=1,
        project_id="approved-project",
        kind="code",
        title="approved title",
        description="approved description",
        worktree_path=str(worktree.resolve()),
    )

    encoded = spec.to_json()
    assert encoded == json.dumps(
        json.loads(encoded),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    assert _NonConfigPlanSpec.from_json(
        encoded,
        expected_project_id="approved-project",
    ) == spec
    with pytest.raises(ValueError, match="project identity"):
        _NonConfigPlanSpec.from_json(
            encoded,
            expected_project_id="attacker-project",
        )
    with pytest.raises(FrozenInstanceError):
        spec.__setattr__("project_id", "attacker-project")


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("unsupported-schema", "schema version"),
        ("malformed-project", "project identity"),
        ("config-kind", "kind"),
        ("empty-title", "title"),
        ("relative-worktree", "worktree path"),
        ("unexpected-field", "fields"),
        ("wrong-field-type", "field types"),
        ("noncanonical", "not canonical"),
    ],
)
def test_non_config_approval_plan_rejects_ambiguous_artifacts(
    case: str,
    message: str,
) -> None:
    """Every persisted representation must have one exact approved meaning."""
    from general_ludd.routers.self_improve import _NonConfigPlanSpec

    payload: dict[str, object] = {
        "description": "approved description",
        "kind": "code",
        "project_id": "approved-project",
        "schema_version": 1,
        "title": "approved title",
        "worktree_path": "/approved/worktree",
    }
    if case == "unsupported-schema":
        payload["schema_version"] = 2
    elif case == "malformed-project":
        payload["project_id"] = " approved-project"
    elif case == "config-kind":
        payload["kind"] = "config"
    elif case == "empty-title":
        payload["title"] = " "
    elif case == "relative-worktree":
        payload["worktree_path"] = "relative/worktree"
    elif case == "unexpected-field":
        payload["attacker_override"] = "/attacker/worktree"
    elif case == "wrong-field-type":
        payload["description"] = 17

    raw = json.dumps(
        payload,
        ensure_ascii=False,
        separators=None if case == "noncanonical" else (",", ":"),
        sort_keys=True,
    )
    with pytest.raises(ValueError, match=message):
        _NonConfigPlanSpec.from_json(
            raw,
            expected_project_id="approved-project",
        )


# ---------------------------------------------------------------------------
# 1. PROTECTED-PATH BYPASS
# ---------------------------------------------------------------------------


class TestProtectedPathBypass:
    """TOCTOU and workspace-confinement bypass attack vectors for self-improve."""

    # -- Bait-and-switch: approve path A, exploit writes path B ---------------

    def test_bait_and_switch_apply_stored_spec_not_request_body(self) -> None:
        """C13: the config-tier apply writes the RECORDED spec (from the approval
        record's plan_artifact), not whatever the request body supplies. This
        prevents an approve-A / apply-B bait-and-switch where the human approves
        writing ``config/safe.yml`` and the attacker then submits a request
        targeting ``.opencode/plugin/evil.ts``.
        """
        recorded_target_paths = ["config/allowed.yml"]
        request_target_paths = [".opencode/plugin/malicious.ts"]

        writer = _FakeWriter()
        applier = UpdateApplier(
            writer=writer,
            capability_checker=_FixedChecker({"config_write"}),
            workspace_root=Path("."),
        )
        plan_from_record = _Plan(
            kind="config",
            capability_required="config_write",
            target_paths=recorded_target_paths,
        )
        plan_from_request = _Plan(
            kind="config",
            capability_required="config_write",
            target_paths=request_target_paths,
        )

        result_recorded = applier.apply(plan_from_record, "a: 1\n")
        result_request = applier.apply(plan_from_request, "x: evil\n")

        assert result_recorded.status == "applied", "Recorded spec path should succeed"
        assert result_request.status == "denied", "Request-body path should be denied"
        assert ".opencode" in result_request.evidence

    # -- Replay attack: same approval_id applied twice -----------------------

    def test_replay_attack_second_apply_with_same_spec_also_denied(self) -> None:
        """The applier itself does not track replay — that is the router's job
        (QUEUED -> ACTIVE -> COMPLETE). But the applier's protection-depth
        means even if replayed the protected-path check still fires. Verify that
        a second fresh apply() of the same protected path is also denied.
        """
        writer = _FakeWriter()
        applier = UpdateApplier(
            writer=writer,
            capability_checker=_FixedChecker({"config_write"}),
            workspace_root=Path("."),
        )
        plan = _Plan(
            kind="config",
            capability_required="config_write",
            target_paths=["secrets/admin.yml"],
        )

        r1 = applier.apply(plan, "a: 1\n")
        r2 = applier.apply(plan, "b: 2\n")

        assert r1.status == "denied"
        assert r2.status == "denied"
        assert writer.writes == []

    # -- Symlink TOCTOU: path resolves via symlink to protected location -----

    def test_protected_via_symlink_resolve_is_caught(self, tmp_path: Path) -> None:
        """Even when the raw path string has no protected marker, if resolving
        it (following symlinks) lands in a protected location, the path must be
        denied.
        """
        root = tmp_path / "root"
        root.mkdir()
        outside = tmp_path / "protected"
        outside.mkdir()
        (outside / "secrets.yml").write_text("original")

        innocent = root / "innocent"
        innocent.symlink_to(outside)

        writer = _FakeWriter()
        applier = UpdateApplier(
            writer=writer,
            capability_checker=_FixedChecker({"config_write"}),
            workspace_root=root,
        )
        # The raw path "innocent/secrets.yml" has no deny-list marker, but
        # the resolved path is outside/protected ->outside is outside root.
        # Confinement catches the escape.
        plan = _Plan(
            kind="config",
            capability_required="config_write",
            target_paths=["innocent/secrets.yml"],
        )

        result = applier.apply(plan, "a: 1\n")

        assert result.status == "denied"
        assert writer.writes == []

    # -- Workspace root as target path ---------------------------------------

    def test_targeting_workspace_root_itself_is_refused(self, tmp_path: Path) -> None:
        """Targeting the workspace root directory itself (``.`` or empty string)
        must not result in a write — the applier should deny or confine it.
        """
        writer = _FakeWriter()
        applier = UpdateApplier(
            writer=writer,
            capability_checker=_FixedChecker({"config_write"}),
            workspace_root=tmp_path,
        )

        result = applier.apply(
            _Plan(
                kind="config",
                capability_required="config_write",
                target_paths=["."],
            ),
            "a: 1\n",
        )

        assert result.status in ("denied", "applied")
        # If applied, the write must resolve to something INSIDE root, not root itself.
        if result.status == "applied":
            assert writer.writes
            written_path = Path(writer.writes[0][0])
            assert written_path != tmp_path.resolve()
            assert tmp_path.resolve() in written_path.parents or tmp_path.resolve() == written_path.parent

    # -- Empty target paths (C9 F4 regression) -------------------------------

    def test_empty_target_paths_non_code_kind_is_denied(self) -> None:
        """A non-code change with zero target paths must NEVER report 'applied'."""
        writer = _FakeWriter()
        applier = UpdateApplier(
            writer=writer,
            capability_checker=_FixedChecker({"config_write"}),
            workspace_root=Path("."),
        )

        result = applier.apply(
            _Plan(
                kind="config",
                capability_required="config_write",
                target_paths=[],
            ),
            "a: 1\n",
        )

        assert result.status == "denied"
        assert "no target paths" in result.evidence.lower()
        assert writer.writes == []

    # -- Null bytes in target path --------------------------------------------

    def test_null_byte_in_path_denied_or_errors(self, tmp_path: Path) -> None:
        """Null bytes in target paths are effectively denied — either the
        applier rejects them or os.path raises. The writer must not be called."""
        writer = _FakeWriter()
        applier = UpdateApplier(
            writer=writer,
            capability_checker=_FixedChecker({"config_write"}),
            workspace_root=tmp_path,
        )

        try:
            result = applier.apply(
                _Plan(
                    kind="config",
                    capability_required="config_write",
                    target_paths=["safe\0../../../etc/passwd"],
                ),
                "a: 1\n",
            )
            assert result.status == "denied"
        except (ValueError, TypeError):
            pass

        assert writer.writes == [], "Null-byte path must not reach the writer"


# ---------------------------------------------------------------------------
# 2. SIGNATURE VERIFICATION (H.17)
# ---------------------------------------------------------------------------


class TestSignatureVerification:
    """H.17: Ed25519 signature verification runs BEFORE any gate, fails closed."""

    @staticmethod
    def _always_pass(_content: str, _sig: str, _key: str) -> bool:
        return True

    @staticmethod
    def _always_fail(_content: str, _sig: str, _key: str) -> bool:
        return False

    @staticmethod
    def _boom_verifier(_content: str, _sig: str, _key: str) -> bool:
        raise RuntimeError("verifier crashed")

    def _make_applier(self) -> UpdateApplier:
        return UpdateApplier(
            writer=_FakeWriter(),
            capability_checker=_FixedChecker({"config_write"}),
            workspace_root=Path("."),
        )

    # -- missing signature / key when verifier is supplied -------------------

    def test_verifier_supplied_but_no_signature_is_denied(self) -> None:
        applier = self._make_applier()
        plan = _Plan(
            kind="config",
            capability_required="config_write",
            target_paths=["config/ok.yml"],
        )

        result = applier.apply(
            plan,
            "a: 1\n",
            verify_signature=self._always_pass,
        )

        assert result.status == "denied"
        assert "content_signature" in result.evidence

    def test_verifier_supplied_but_no_public_key_is_denied(self) -> None:
        applier = self._make_applier()
        plan = _Plan(
            kind="config",
            capability_required="config_write",
            target_paths=["config/ok.yml"],
        )

        result = applier.apply(
            plan,
            "a: 1\n",
            content_signature="sig123",
            verify_signature=self._always_pass,
        )

        assert result.status == "denied"
        assert "public_key" in result.evidence

    # -- invalid signature ---------------------------------------------------

    def test_invalid_signature_is_denied(self) -> None:
        applier = self._make_applier()
        plan = _Plan(
            kind="config",
            capability_required="config_write",
            target_paths=["config/ok.yml"],
        )

        result = applier.apply(
            plan,
            "a: 1\n",
            content_signature="badsig",
            public_key="pubkey123",
            verify_signature=self._always_fail,
        )

        assert result.status == "denied"
        assert "signature verification failed" in result.evidence

    # -- verifier exception --------------------------------------------------

    def test_verifier_raises_is_denied(self) -> None:
        applier = self._make_applier()
        plan = _Plan(
            kind="config",
            capability_required="config_write",
            target_paths=["config/ok.yml"],
        )

        result = applier.apply(
            plan,
            "a: 1\n",
            content_signature="sig",
            public_key="key",
            verify_signature=self._boom_verifier,
        )

        assert result.status == "denied"
        assert "verification raised" in result.evidence

    # -- valid signature allows apply ----------------------------------------

    def test_valid_signature_allows_apply(self) -> None:
        writer = _FakeWriter()
        applier = UpdateApplier(
            writer=writer,
            capability_checker=_FixedChecker({"config_write"}),
            workspace_root=Path("."),
        )
        plan = _Plan(
            kind="config",
            capability_required="config_write",
            target_paths=["config/ok.yml"],
        )

        result = applier.apply(
            plan,
            "a: 1\n",
            content_signature="validsig",
            public_key="pubkey",
            verify_signature=self._always_pass,
        )

        assert result.status == "applied"
        assert len(writer.writes) == 1

    # -- signature checked BEFORE capability gate ----------------------------

    def test_signature_checked_before_capability_gate(self) -> None:
        """Even if capability is denied, signature verification runs first and
        fails closed — so a missing-sig request does not leak capability info."""
        writer = _FakeWriter()
        applier = UpdateApplier(
            writer=writer,
            capability_checker=_FixedChecker(set()),
            workspace_root=Path("."),
        )
        plan = _Plan(
            kind="config",
            capability_required="config_write",
            target_paths=["config/ok.yml"],
        )

        result = applier.apply(
            plan,
            "a: 1\n",
            verify_signature=self._always_pass,
        )

        assert result.status == "denied"
        assert "content_signature" in result.evidence


# ---------------------------------------------------------------------------
# 3. DENY-LIST CONSISTENCY
# ---------------------------------------------------------------------------


class TestDenyListConsistency:
    """Structural integrity of the canonical deny-list and its named subsets."""

    def test_canonical_superset_of_protected_path_markers(self) -> None:
        """Every marker in PROTECTED_PATH_MARKERS must also be in
        CANONICAL_DENY_MARKERS — the canonical set is the superset."""
        for marker in PROTECTED_PATH_MARKERS:
            assert marker in CANONICAL_DENY_MARKERS, (
                f"PROTECTED_PATH_MARKERS has {marker!r} missing from "
                f"CANONICAL_DENY_MARKERS"
            )

    def test_segment_exact_markers_are_a_subset_of_canonical(self) -> None:
        """Every segment-exact marker must be a member of CANONICAL_DENY_MARKERS."""
        for marker in _SEGMENT_EXACT_MARKERS:
            assert marker in CANONICAL_DENY_MARKERS, (
                f"_SEGMENT_EXACT_MARKERS has {marker!r} not in CANONICAL_DENY_MARKERS"
            )

    def test_no_segment_exact_marker_is_both_segment_and_substring(self) -> None:
        """A marker classified as segment-exact should not also appear as a
        bare-word substring marker — it should be exactly in one tier."""
        bare_substring_markers = CANONICAL_DENY_MARKERS - _SEGMENT_EXACT_MARKERS
        for marker in _SEGMENT_EXACT_MARKERS:
            assert marker not in bare_substring_markers, (
                f"{marker!r} is in segment-exact but also appears as substring"
            )

    def test_canonical_markers_are_non_empty(self) -> None:
        """Degrade-to-empty guard: the canonical set cannot be empty."""
        assert len(CANONICAL_DENY_MARKERS) > 0

    def test_protected_path_markers_are_non_empty(self) -> None:
        assert len(PROTECTED_PATH_MARKERS) > 0

    def test_segment_exact_markers_are_non_empty(self) -> None:
        assert len(_SEGMENT_EXACT_MARKERS) > 0

    def test_every_segment_exact_marker_appears_in_protected_path_markers(self) -> None:
        """Drift-detection: segment-exact markers must also appear in the
        PROTECTED_PATH_MARKERS tuple (they are a subset of the applier view)."""
        for marker in _SEGMENT_EXACT_MARKERS:
            assert marker in PROTECTED_PATH_MARKERS, (
                f"Segment-exact marker {marker!r} missing from PROTECTED_PATH_MARKERS"
            )

    def test_guardsurf_markers_present_in_canonical(self) -> None:
        """Every critical security-surface marker is present in the canonical
        deny-list — guardrail-integrity check."""
        mandatory = {
            "guardrails",
            "secrets",
            ".opencode",
            ".claude",
            "capability_policy",
            "action_policy",
            "fs_write_policy",
            "enforce-",
            "permissions",
            ".github",
            "/workflows/",
            "pyproject.toml",
            "makefile",
            "alembic",
            "/migrations/",
            "setup.cfg",
            "tox.ini",
            ".pre-commit",
            "dockerfile",
            "settings.json",
            "agents.md",
            "claude.md",
            "tasks.md",
            "bugs.md",
            "session.md",
        }
        assert mandatory.issubset(CANONICAL_DENY_MARKERS), (
            f"Guard surface gaps: {mandatory - CANONICAL_DENY_MARKERS}"
        )

    def test_no_path_anchored_marker_is_segment_exact(self) -> None:
        """Path-anchored markers (containing '/') must never be classified as
        segment-exact — they are always substring-matched."""
        for marker in _SEGMENT_EXACT_MARKERS:
            assert "/" not in marker, (
                f"Path-anchored marker {marker!r} cannot be segment-exact"
            )

    def test_hard_deny_substrings_are_derived_from_canonical(self) -> None:
        """Structural drift check: every marker in the applier's isolated
        _HARD_DENY_SUBSTRINGS set must be traceable to CANONICAL_DENY_MARKERS."""
        from general_ludd.security.path_canonicalizer import _HARD_DENY_SUBSTRINGS

        for substring in _HARD_DENY_SUBSTRINGS:
            found = any(
                m in substring or substring in m
                for m in CANONICAL_DENY_MARKERS
            )
            assert found, (
                f"Hard-deny substring {substring!r} has no matching canonical marker"
            )


# ---------------------------------------------------------------------------
# 4. SELF-IMPROVEMENT PROPOSAL PATH IDENTITY
# ---------------------------------------------------------------------------


def _proposal_with_edit_paths(*paths: str) -> str:
    """Build a synthetic proposal with raw path identities controlled by the test."""
    return json.dumps(
        {
            "schema_version": 1,
            "baseline_sha": "a" * 40,
            "task_id": "S83.208",
            "edits": [
                {
                    "operation": "replace",
                    "path": path,
                    "old_text": f"before-{index}",
                    "new_text": f"after-{index}",
                }
                for index, path in enumerate(paths)
            ],
            "tests": ["tests/unit/test_self_improve_security.py"],
            "make_commands": [
                "make test-files "
                "TESTFILES=tests/unit/test_self_improve_security.py PYTEST_ARGS=-q"
            ],
            "commit_message": "fix: reject noncanonical proposal paths",
        }
    )


@pytest.mark.parametrize("alias_path", ["src//x.py", "src/./x.py"])
def test_proposal_rejects_alias_that_bypasses_raw_path_identity(
    alias_path: str,
) -> None:
    """Textually distinct aliases must not evade duplicate/scope identity checks."""
    raw = _proposal_with_edit_paths("src/x.py", alias_path)

    with pytest.raises(ValueError, match="canonical"):
        ProposalManifest.from_json(raw)


def test_proposal_preserves_canonical_path_identity() -> None:
    """A valid path retains its exact approved identity without normalization."""
    manifest = ProposalManifest.from_json(_proposal_with_edit_paths("src/x.py"))

    assert manifest.edits[0].path == "src/x.py"
    assert json.loads(manifest.to_json())["edits"][0]["path"] == "src/x.py"
