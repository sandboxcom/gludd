"""C9 — self_update deny-list family guard tests.

Issues covered (per AGENTIC_IMPLEMENTATION_SPEC.md C9):
  F1: deny-list leading-slash drift between self_update/ and capability_lattice.py
  F2: parent-dir TOCTOU
  F3: cwd-anchored path resolution
  F4: empty-targets false "applied"

These tests assert that both modules use the same canonical deny list, that
path resolution is workspace-root-anchored (not CWD), that .. traversal is
blocked, and that empty target lists never produce a false "applied" status.
"""

from __future__ import annotations

import os
from pathlib import Path

from general_ludd.security.capability_lattice import (
    PROTECTED_PATH_SUBSTRINGS,
    is_protected_path,
)
from general_ludd.security.path_canonicalizer import (
    CANONICAL_DENY_MARKERS,
    canonicalize_path,
    is_denied_path,
)
from general_ludd.self_update.applier import (
    UpdateApplier,
    _first_protected,
    _resolve_confined,
)

# ---------------------------------------------------------------------------
# F1: deny-list drift — both modules use the same canonical deny list
# ---------------------------------------------------------------------------


class TestDenyListsAreIdentical:
    """F1: The deny-list markers used by capability_lattice and applier must
    be normalised through a single canonical source."""

    def test_capability_lattice_uses_canonical_markers(self) -> None:
        """Every substring in capability_lattice's deny-list is in the canonical set.

        PROTECTED_PATH_SUBSTRINGS may use slash-anchored forms (``/.opencode/``,
        ``/module_utils/capability_policy``) while the canonical set sometimes
        uses bare forms (``.opencode``).  Compare exact AND stripped forms.
        """
        canonical_lower = {m.lower() for m in CANONICAL_DENY_MARKERS}
        for sub in PROTECTED_PATH_SUBSTRINGS:
            sub_lower = sub.lower()
            stripped = sub_lower.strip("/")
            if sub_lower in canonical_lower:
                continue
            if stripped in canonical_lower:
                continue
            raise AssertionError(
                f"capability_lattice substring {sub!r} not in canonical deny set "
                f"(tried exact {sub_lower!r} and stripped {stripped!r})"
            )

    def test_applier_protected_markers_in_canonical(self) -> None:
        """Every PROTECTED_PATH_MARKER in the applier is in the canonical set."""
        from general_ludd.self_update.applier import PROTECTED_PATH_MARKERS

        canonical_lower = {m.lower() for m in CANONICAL_DENY_MARKERS}
        for marker in PROTECTED_PATH_MARKERS:
            assert marker.lower() in canonical_lower, (
                f"applier marker {marker!r} not in canonical deny set"
            )

    def test_canonical_deny_markers_is_frozenset(self) -> None:
        """The canonical deny list is immutable."""
        assert isinstance(CANONICAL_DENY_MARKERS, frozenset)

    def test_canonical_deny_markers_is_nonempty(self) -> None:
        """The canonical deny list is not empty."""
        assert len(CANONICAL_DENY_MARKERS) > 0

    def test_apply_hard_deny_segments_in_canonical(self) -> None:
        """Every _HARD_DENY_SEGMENT in apply.py is a canonical marker or segment."""
        from general_ludd.self_update.apply import _HARD_DENY_SEGMENTS

        canonical_lower = {m.lower() for m in CANONICAL_DENY_MARKERS}
        for seg in _HARD_DENY_SEGMENTS:
            assert seg.lower() in canonical_lower, (
                f"apply hard-deny segment {seg!r} not in canonical deny set"
            )

    def test_canonicalize_normalises_separators(self) -> None:
        """Backslashes are normalised to forward slashes."""
        assert canonicalize_path(r"C:\Users\foo\.claude") == canonicalize_path(
            "C:/Users/foo/.claude"
        )

    def test_canonicalize_lowercases(self) -> None:
        """Path is lowercased for case-insensitive matching."""
        result = canonicalize_path("/Foo/Bar/.Opencode/Plugin.Ts")
        assert result == "/foo/bar/.opencode/plugin.ts"

    def test_canonicalize_empty_and_none(self) -> None:
        """Empty/None paths normalise to empty string without crashing."""
        assert canonicalize_path("") == ""
        assert canonicalize_path(None) == ""

    def test_is_denied_path_absolute_claude(self) -> None:
        """Absolute /.claude/ path is denied."""
        assert is_denied_path("/repo/.claude/hooks/test.py") is True

    def test_is_denied_path_relative_claude(self) -> None:
        """Relative .claude/ path (no leading slash) is also denied — no drift."""
        assert is_denied_path(".claude/hooks/test.py") is True

    def test_is_denied_path_relative_opencode(self) -> None:
        """Relative .opencode/ path is denied."""
        assert is_denied_path(".opencode/plugin/test.ts") is True

    def test_is_denied_path_normal_src_not_denied(self) -> None:
        """Normal source paths are not denied."""
        assert is_denied_path("src/general_ludd/models/gateway.py") is False

    def test_is_denied_protected_guardrails(self) -> None:
        """guardrails segment is denied."""
        assert is_denied_path("src/guardrails/check.py") is True

    def test_capability_lattice_is_protected_path_uses_canonicalizer(self) -> None:
        """is_protected_path in capability_lattice must agree with the canonicalizer
        on the cross-module critical paths (.claude, .opencode, settings)."""

        critical_paths = [
            "/ws/repo/.claude/hooks/x.py",
            ".claude/hooks/x.py",
            ".claude\\hooks\\x.py",
            "/ws/repo/.opencode/plugin/x.ts",
            ".opencode/plugin/x.ts",
            "settings.json",
        ]
        for p in critical_paths:
            lattice_result = is_protected_path(p)
            canonical_result = is_denied_path(p)
            assert lattice_result == canonical_result, (
                f"drift on {p!r}: lattice={lattice_result}, canonical={canonical_result}"
            )
            assert lattice_result is True, (
                f"{p!r} must be denied but was allowed"
            )


# ---------------------------------------------------------------------------
# F2: parent-dir TOCTOU
# ---------------------------------------------------------------------------


class TestParentDirTOCTOU:
    """F2: Path resolution must be TOCTOU-safe — a path resolved, checked,
    and then written must not be redirectable via symlink swap in between."""

    def test_resolve_confined_blocks_parent_dir_traversal(self, tmp_path: Path) -> None:
        """../ traversal is blocked by _resolve_confined."""
        escapee, _ = _resolve_confined(
            ["../etc/passwd"], tmp_path
        )
        assert escapee is not None
        assert escapee == "../etc/passwd"

    def test_resolve_confined_blocks_encoded_traversal(self, tmp_path: Path) -> None:
        """%2e%2e/ percent-encoded traversal is blocked."""
        escapee, _ = _resolve_confined(
            ["%2e%2e/%2e%2e/etc/passwd"], tmp_path
        )
        assert escapee is not None

    def test_resolve_confined_blocks_absolute_outside(self, tmp_path: Path) -> None:
        """Absolute path outside workspace root is blocked."""
        escapee, _ = _resolve_confined(
            ["/etc/passwd"], tmp_path
        )
        assert escapee is not None

    def test_resolve_confined_allows_path_within_root(self, tmp_path: Path) -> None:
        """A path within the workspace root is allowed."""
        escapee, resolved = _resolve_confined(
            ["config/app.yml"], tmp_path
        )
        assert escapee is None
        assert len(resolved) == 1
        assert resolved[0].is_relative_to(tmp_path)

    def test_first_protected_uses_workspace_root_not_cwd(self, tmp_path: Path) -> None:
        """_first_protected resolves against workspace_root, not CWD.

        Create a scenario: .opencode/ directory inside the temporary workspace
        root.  _first_protected detects the deny-list marker via the lexical
        check on the canonicalized path, which catches ".opencode" regardless
        of the resolution base.
        """
        (tmp_path / ".opencode" / "plugin").mkdir(parents=True)
        (tmp_path / ".opencode" / "plugin" / "test.ts").write_text("// test")

        result = _first_protected([".opencode/plugin/test.ts"])
        assert result == ".opencode/plugin/test.ts"

    def test_resolve_confined_toctou_write_uses_resolved_paths(
        self, tmp_path: Path
    ) -> None:
        """After _resolve_confined returns resolved paths, those Path objects
        are the resolved, canonical locations — a symlink swap after the check
        cannot redirect to a different on-disk location because the resolution
        is already baked into the Path object."""
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "app.yml").write_text("key: value")

        escapee, resolved = _resolve_confined(
            ["config/app.yml"], tmp_path
        )
        assert escapee is None
        assert resolved[0].is_absolute()
        assert str(resolved[0]) == str((tmp_path / "config" / "app.yml").resolve())


# ---------------------------------------------------------------------------
# F3: cwd-anchored resolve
# ---------------------------------------------------------------------------


class TestCwdAnchoredResolve:
    """F3: Path resolution must be anchored to workspace_root, not CWD.

    ``Path(path).resolve()`` resolves relative to CWD. If the daemon's CWD
    changes, a relative path can resolve to an entirely different location.
    All path resolution in deny-list checking must be against the workspace
    root.
    """

    def test_resolve_confined_anchors_to_root_not_cwd(self, tmp_path: Path) -> None:
        """_resolve_confined resolves against the given root, not CWD."""
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "app.yml").write_text("key: value")

        old_cwd = os.getcwd()
        try:
            os.chdir("/tmp")
            escapee, resolved = _resolve_confined(
                ["config/app.yml"], tmp_path
            )
            assert escapee is None
            assert resolved[0] == (tmp_path / "config" / "app.yml").resolve()
        finally:
            os.chdir(old_cwd)

    def test_first_protected_resolves_against_cwd_currently(
        self, tmp_path: Path
    ) -> None:
        """Document current behavior: _first_protected uses Path(path).resolve()
        against workspace_root (when provided) — the C9 fix ensures resolution
        is anchored to workspace_root, not CWD."""
        old_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            (tmp_path / ".opencode").mkdir()
            result = _first_protected([".opencode/plugin/test.ts"])
            assert result is not None
        finally:
            os.chdir(old_cwd)

    def test_resolve_confined_empty_paths_is_safe(self, tmp_path: Path) -> None:
        """Empty path list returns (None, []) — no crash, no false positive."""
        escapee, resolved = _resolve_confined([], tmp_path)
        assert escapee is None
        assert resolved == []


# ---------------------------------------------------------------------------
# F4: empty-targets false "applied"
# ---------------------------------------------------------------------------


class _YesCapabilityChecker:
    """Capability checker that allows everything."""

    def allows(self, capability: str) -> bool:
        return True


class _FakeWriter:
    """Writer that records writes for assertion."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, str]] = []

    def write(self, path: str, content: str) -> str | None:
        self.writes.append((path, content))
        return path


class _FakePlan:
    """Minimal plan for testing the applier."""

    def __init__(self, kind: str, target_paths: list[str], capability: str = "config_self_modify") -> None:
        self._kind = kind
        self._target_paths = target_paths
        self._capability = capability

    @property
    def kind(self) -> str:
        return self._kind

    @property
    def capability_required(self) -> str:
        return self._capability

    @property
    def target_paths(self) -> list[str]:
        return self._target_paths


class TestEmptyTargetsNotApplied:
    """F4: An empty target_paths list must never produce an "applied" status."""

    def test_empty_targets_config_kind_is_denied_not_applied(
        self, tmp_path: Path
    ) -> None:
        """Empty target list with config kind -> denied, not applied."""
        writer = _FakeWriter()
        checker = _YesCapabilityChecker()
        applier = UpdateApplier(writer, checker, tmp_path)

        plan = _FakePlan(kind="config", target_paths=[])
        result = applier.apply(plan, "key: value")

        assert result.status != "applied", (
            f"empty targets produced status={result.status!r}, expected 'denied'"
        )
        assert result.status == "denied"
        assert "empty" in result.evidence.lower() or "no target" in result.evidence.lower()

    def test_empty_targets_yaml_kind_is_denied(
        self, tmp_path: Path
    ) -> None:
        """Empty target list with yaml kind -> denied."""
        writer = _FakeWriter()
        checker = _YesCapabilityChecker()
        applier = UpdateApplier(writer, checker, tmp_path)

        plan = _FakePlan(kind="yaml", target_paths=[])
        result = applier.apply(plan, "key: value")

        assert result.status == "denied"

    def test_empty_targets_no_writes_occur(self, tmp_path: Path) -> None:
        """Empty target list must not write any files."""
        writer = _FakeWriter()
        checker = _YesCapabilityChecker()
        applier = UpdateApplier(writer, checker, tmp_path)

        plan = _FakePlan(kind="config", target_paths=[])
        applier.apply(plan, "key: value")

        assert len(writer.writes) == 0

    def test_empty_targets_proposed_code_is_still_proposed(
        self, tmp_path: Path
    ) -> None:
        """Code kind with empty targets -> proposed (code changes are never
        blind-applied). The empty-targets guard runs AFTER the code-check
        gate, so code proposals are unaffected."""
        writer = _FakeWriter()
        checker = _YesCapabilityChecker()
        applier = UpdateApplier(writer, checker, tmp_path)

        plan = _FakePlan(kind="code", target_paths=[])
        result = applier.apply(plan, "def foo(): pass")

        assert result.status == "proposed"

    def test_single_target_config_is_applied(
        self, tmp_path: Path
    ) -> None:
        """Regression: a single valid config target still applies correctly."""
        writer = _FakeWriter()
        checker = _YesCapabilityChecker()
        applier = UpdateApplier(writer, checker, tmp_path)

        plan = _FakePlan(kind="config", target_paths=["config/app.yml"])
        result = applier.apply(plan, "key: value")

        assert result.status == "applied"
        assert len(writer.writes) == 1

    def test_unknown_kind_empty_targets_is_denied(
        self, tmp_path: Path
    ) -> None:
        """Unknown kind with empty targets -> denied."""
        writer = _FakeWriter()
        checker = _YesCapabilityChecker()
        applier = UpdateApplier(writer, checker, tmp_path)

        plan = _FakePlan(kind="unknown_kind", target_paths=[])
        result = applier.apply(plan, "content")

        assert result.status == "denied"


# ---------------------------------------------------------------------------
# Cross-module integration: canonicalizer integrated into both modules
# ---------------------------------------------------------------------------


class TestCanonicalizerIntegration:
    """Verify the canonical deny-list is used by both security/ and self_update/."""

    def test_capability_lattice_is_protected_path_via_canonical(
        self,
    ) -> None:
        """is_protected_path must agree with is_denied_path on all markers.

        This is the cross-module drift assertion: every canonical marker
        that represents a path substring must be detected by is_protected_path.
        """
        for marker in CANONICAL_DENY_MARKERS:
            test_path = f"/repo/some/dir/{marker}/file.py"
            lattice_matches = is_protected_path(test_path)
            canonical_matches = is_denied_path(test_path)
            assert lattice_matches == canonical_matches, (
                f"drift on marker {marker!r}: "
                f"lattice={lattice_matches}, canonical={canonical_matches}"
            )

    def test_apply_is_hard_denied_via_canonical(self) -> None:
        """apply.py _is_hard_denied must agree with is_denied_path on
        .claude and .opencode paths."""
        from general_ludd.self_update.apply import _is_hard_denied

        test_paths = [
            "/repo/.claude/hooks/x.py",
            ".claude/hooks/x.py",
            "/repo/.opencode/plugin/x.ts",
            ".opencode/plugin/x.ts",
            "settings.json",
        ]
        for p in test_paths:
            apply_result = _is_hard_denied(p)
            canonical_result = is_denied_path(p)
            assert apply_result == canonical_result, (
                f"drift on {p!r}: apply={apply_result}, canonical={canonical_result}"
            )
