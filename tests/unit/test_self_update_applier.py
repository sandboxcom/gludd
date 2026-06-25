"""Unit tests for the self-update UpdateApplier (#81 part 2).

The applier safely APPLIES an update plan. It is fully decoupled: a ``SafeWriter``
and a ``CapabilityChecker`` are injected. The applier owns a PROTECTED_PATH
deny-list and NEVER writes a protected path regardless of capability. ``code`` kind
plans are never blind-applied — they are returned as a proposal for the
self-improve A/B + hot_reload path.

These tests do NOT import self_update.router or self_update.__init__ (owned by a
sibling agent). They construct a minimal structural plan locally.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from general_ludd.self_update.applier import (
    PROTECTED_PATH_MARKERS,
    ApplyResult,
    UpdateApplier,
)


@dataclass
class _Plan:
    """Local structural stand-in for the real update plan shape."""

    kind: str
    capability_required: str
    target_paths: list[str] = field(default_factory=list)


class _FakeWriter:
    """Records every write so tests can assert SafeWriter was / was not called."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, str]] = []

    def write(self, path: str, content: str) -> None:
        self.writes.append((path, content))


class _FixedChecker:
    """CapabilityChecker that allows exactly the capabilities it is seeded with."""

    def __init__(self, allowed: set[str]) -> None:
        self._allowed = allowed

    def allows(self, capability: str) -> bool:
        return capability in self._allowed


def _applier(
    writer: _FakeWriter,
    allowed: set[str],
    workspace_root: Path = Path("."),
) -> UpdateApplier:
    return UpdateApplier(
        writer=writer,
        capability_checker=_FixedChecker(allowed),
        workspace_root=workspace_root,
    )


# --- config write, capability allowed, valid yaml -> applied -----------------


def test_valid_config_with_capability_is_applied_and_written() -> None:
    writer = _FakeWriter()
    applier = _applier(writer, {"config_self_modify"})
    plan = _Plan(
        kind="config",
        capability_required="config_self_modify",
        target_paths=["config/some_setting.yml"],
    )

    result = applier.apply(plan, "key: value\nother: 3\n")

    assert isinstance(result, ApplyResult)
    assert result.status == "applied"
    assert result.target_paths == ["config/some_setting.yml"]
    assert writer.writes == [("config/some_setting.yml", "key: value\nother: 3\n")]


@pytest.mark.parametrize("kind", ["config", "yaml", "role"])
def test_yaml_kinds_are_applied(kind: str) -> None:
    writer = _FakeWriter()
    applier = _applier(writer, {"config_self_modify"})
    plan = _Plan(
        kind=kind,
        capability_required="config_self_modify",
        target_paths=["config/thing.yml"],
    )

    result = applier.apply(plan, "a: 1\n")

    assert result.status == "applied"
    assert len(writer.writes) == 1


# --- protected / guardrail path -> denied, NOT written -----------------------


@pytest.mark.parametrize(
    "protected",
    [
        "config/guardrails.yml",
        "secrets/openbao.yml",
        ".opencode/plugin/enforce-make.ts",
        ".claude/settings.json",
        "collections/.../module_utils/capability_policy.py",
        "collections/.../module_utils/action_policy.py",
        "collections/.../module_utils/fs_write_policy.py",
        ".opencode/plugin/enforce-anything.ts",
        "config/permissions.yml",
        # CI/build surface — must never be rewritten via self-update
        ".github/workflows/release.yml",
        ".github/workflows/build.yml",
        "pyproject.toml",
        "Makefile",
        "alembic.ini",
        "db/migrations/001_init.sql",
        "Dockerfile",
        "setup.cfg",
        "tox.ini",
        ".pre-commit-config.yaml",
    ],
)
def test_protected_path_is_denied_and_not_written(protected: str) -> None:
    writer = _FakeWriter()
    # Capability is granted — the deny must come from the PROTECTED_PATH list,
    # proving protection is independent of capability.
    applier = _applier(writer, {"config_self_modify"})
    plan = _Plan(
        kind="config",
        capability_required="config_self_modify",
        target_paths=[protected],
    )

    result = applier.apply(plan, "a: 1\n")

    assert result.status == "denied"
    assert writer.writes == []
    # Evidence must cite the offending protected path.
    assert protected in result.evidence


def test_protected_marker_in_mixed_target_set_denies_all() -> None:
    writer = _FakeWriter()
    applier = _applier(writer, {"config_self_modify"})
    plan = _Plan(
        kind="config",
        capability_required="config_self_modify",
        target_paths=["config/ok.yml", "config/guardrails.yml"],
    )

    result = applier.apply(plan, "a: 1\n")

    assert result.status == "denied"
    assert writer.writes == []


def test_protected_markers_constant_is_nonempty_and_covers_required_terms() -> None:
    # Guardrail-integrity: the deny-list must exist and include the mandated terms.
    required = {
        "guardrails",
        "secrets",
        ".opencode",
        ".claude",
        "capability_policy",
        "action_policy",
        "fs_write_policy",
        "enforce-",
        "permissions",
        # CI/build surface added to prevent secret exfiltration via workflow rewrites
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
    }
    assert required.issubset(set(PROTECTED_PATH_MARKERS))


# --- code kind -> proposed, never written ------------------------------------


def test_code_kind_is_proposed_never_written() -> None:
    writer = _FakeWriter()
    applier = _applier(writer, {"code_self_modify"})
    plan = _Plan(
        kind="code",
        capability_required="code_self_modify",
        target_paths=["src/general_ludd/foo.py"],
    )

    result = applier.apply(plan, "def f():\n    return 1\n")

    assert result.status == "proposed"
    assert writer.writes == []
    # The change content is carried as the proposal payload for downstream A/B.
    assert "def f()" in result.evidence


# --- capability denied -> denied ---------------------------------------------


def test_capability_denied_is_denied_and_not_written() -> None:
    writer = _FakeWriter()
    applier = _applier(writer, set())  # nothing allowed
    plan = _Plan(
        kind="config",
        capability_required="config_self_modify",
        target_paths=["config/ok.yml"],
    )

    result = applier.apply(plan, "a: 1\n")

    assert result.status == "denied"
    assert writer.writes == []


def test_capability_checked_before_write_for_code_too() -> None:
    writer = _FakeWriter()
    applier = _applier(writer, set())
    plan = _Plan(
        kind="code",
        capability_required="code_self_modify",
        target_paths=["src/general_ludd/foo.py"],
    )

    result = applier.apply(plan, "x = 1\n")

    assert result.status == "denied"
    assert writer.writes == []


# --- invalid yaml -> denied (fail closed) ------------------------------------


def test_invalid_yaml_is_denied_and_not_written() -> None:
    writer = _FakeWriter()
    applier = _applier(writer, {"config_self_modify"})
    plan = _Plan(
        kind="config",
        capability_required="config_self_modify",
        target_paths=["config/broken.yml"],
    )

    # Unbalanced bracket / bad indentation that yaml.safe_load rejects.
    result = applier.apply(plan, "a: [1, 2\n  b: : : oops\n")

    assert result.status == "denied"
    assert writer.writes == []


def test_unknown_kind_fails_closed_to_denied() -> None:
    writer = _FakeWriter()
    applier = _applier(writer, {"config_self_modify"})
    plan = _Plan(
        kind="mystery",
        capability_required="config_self_modify",
        target_paths=["config/ok.yml"],
    )

    result = applier.apply(plan, "a: 1\n")

    assert result.status == "denied"
    assert writer.writes == []


def test_writer_failure_fails_closed_to_denied() -> None:
    class _BoomWriter:
        def write(self, path: str, content: str) -> None:
            raise OSError("disk full")

    applier = UpdateApplier(
        writer=_BoomWriter(),
        capability_checker=_FixedChecker({"config_self_modify"}),
        workspace_root=Path("."),
    )
    plan = _Plan(
        kind="config",
        capability_required="config_self_modify",
        target_paths=["config/ok.yml"],
    )

    result = applier.apply(plan, "a: 1\n")

    assert result.status == "denied"


# --- path traversal / workspace confinement -> denied, NOT written ----------


def test_traversal_escape_is_denied_and_not_written(tmp_path: Path) -> None:
    """A ``../`` path resolving outside the workspace root is refused."""
    writer = _FakeWriter()
    applier = UpdateApplier(
        writer=writer,
        capability_checker=_FixedChecker({"config_self_modify"}),
        workspace_root=tmp_path,
    )
    plan = _Plan(
        kind="config",
        capability_required="config_self_modify",
        target_paths=["../../../../../../../etc/passwd"],
    )

    result = applier.apply(plan, "a: 1\n")

    assert result.status == "denied"
    assert writer.writes == []
    assert "workspace root" in result.evidence


def test_percent_encoded_traversal_is_denied(tmp_path: Path) -> None:
    """Percent-encoded ``../`` is decoded before the confinement check."""
    writer = _FakeWriter()
    applier = UpdateApplier(
        writer=writer,
        capability_checker=_FixedChecker({"config_self_modify"}),
        workspace_root=tmp_path,
    )
    plan = _Plan(
        kind="config",
        capability_required="config_self_modify",
        target_paths=["%2e%2e%2f%2e%2e%2f%2e%2e%2fetc%2fpasswd"],
    )

    result = applier.apply(plan, "a: 1\n")

    assert result.status == "denied"
    assert writer.writes == []


def test_absolute_path_outside_root_is_denied(tmp_path: Path) -> None:
    """An absolute path resolving outside the workspace root is refused."""
    writer = _FakeWriter()
    applier = UpdateApplier(
        writer=writer,
        capability_checker=_FixedChecker({"config_self_modify"}),
        workspace_root=tmp_path,
    )
    plan = _Plan(
        kind="config",
        capability_required="config_self_modify",
        target_paths=["/etc/passwd"],
    )

    result = applier.apply(plan, "a: 1\n")

    assert result.status == "denied"
    assert writer.writes == []


def test_one_escape_in_set_denies_all(tmp_path: Path) -> None:
    """If any path in the set escapes, the whole apply is denied."""
    writer = _FakeWriter()
    applier = UpdateApplier(
        writer=writer,
        capability_checker=_FixedChecker({"config_self_modify"}),
        workspace_root=tmp_path,
    )
    plan = _Plan(
        kind="config",
        capability_required="config_self_modify",
        target_paths=["config/ok.yml", "../../../etc/evil"],
    )

    result = applier.apply(plan, "a: 1\n")

    assert result.status == "denied"
    assert writer.writes == []


def test_path_inside_root_still_applied(tmp_path: Path) -> None:
    """Regression guard: a legitimate relative path under root is applied."""
    writer = _FakeWriter()
    applier = UpdateApplier(
        writer=writer,
        capability_checker=_FixedChecker({"config_self_modify"}),
        workspace_root=tmp_path,
    )
    plan = _Plan(
        kind="config",
        capability_required="config_self_modify",
        target_paths=["config/ok.yml"],
    )

    result = applier.apply(plan, "a: 1\n")

    assert result.status == "applied"
    assert writer.writes == [("config/ok.yml", "a: 1\n")]


# --- CI/build path protection regression (secret-exfiltration vector) --------


@pytest.mark.parametrize(
    "ci_path",
    [
        ".github/workflows/release.yml",
        ".github/workflows/build.yml",
        "pyproject.toml",
        "Makefile",
        "alembic.ini",
        "db/migrations/001_init.sql",
        "Dockerfile",
    ],
)
def test_ci_build_paths_are_denied_regardless_of_capability(
    tmp_path: Path, ci_path: str
) -> None:
    """Regression: CI/build paths must be hard-denied even when capability is granted.

    Rewriting .github/workflows/* would allow exfiltrating build-runner secrets;
    rewriting pyproject.toml/Makefile/alembic would subvert the build and migration
    surface. The protected-path check must block these regardless of capability.
    """
    writer = _FakeWriter()
    applier = UpdateApplier(
        writer=writer,
        capability_checker=_FixedChecker({"config_self_modify"}),
        workspace_root=tmp_path,
    )
    plan = _Plan(
        kind="config",
        capability_required="config_self_modify",
        target_paths=[ci_path],
    )

    result = applier.apply(plan, "a: 1\n")

    assert result.status == "denied", (
        f"Expected 'denied' for protected CI/build path {ci_path!r}, "
        f"got {result.status!r} — evidence: {result.evidence}"
    )
    assert writer.writes == [], "SafeWriter must not be called for protected paths"
    assert "protected path" in result.evidence, (
        f"Evidence must cite 'protected path'; got: {result.evidence!r}"
    )


# --- segment-exact marker regression: alembic/makefile/dockerfile ------------


@pytest.mark.parametrize(
    "allowed_path",
    [
        "src/alembic_runner.py",
        "utils/makefile_parser.py",
        "src/dockerfile_parser.py",
        "lib/alembic_helpers.py",
        "tools/makefile_utils.py",
        "build/dockerfile_linter.py",
    ],
)
def test_segment_exact_false_positives_are_allowed(
    tmp_path: Path, allowed_path: str
) -> None:
    """Regression: bare-word markers (alembic/makefile/dockerfile) must NOT
    block files that merely contain the marker as a substring in their name
    (e.g. alembic_runner.py, makefile_parser.py, dockerfile_parser.py).
    """
    writer = _FakeWriter()
    applier = UpdateApplier(
        writer=writer,
        capability_checker=_FixedChecker({"config_self_modify"}),
        workspace_root=tmp_path,
    )
    plan = _Plan(
        kind="config",
        capability_required="config_self_modify",
        target_paths=[allowed_path],
    )

    result = applier.apply(plan, "a: 1\n")

    assert result.status == "applied", (
        f"Expected 'applied' for non-protected path {allowed_path!r}, "
        f"got {result.status!r} — evidence: {result.evidence}"
    )
    assert writer.writes == [(allowed_path, "a: 1\n")]


@pytest.mark.parametrize(
    "blocked_path",
    [
        # alembic as a directory segment or exact file
        "alembic/versions/0001_init.py",
        "alembic/env.py",
        "alembic.ini",
        # makefile as exact basename (various cases)
        "Makefile",
        "makefile",
        "MAKEFILE",
        "build/Makefile",
        # dockerfile as exact basename (various cases)
        "Dockerfile",
        "dockerfile",
        "DOCKERFILE",
        "docker/Dockerfile",
    ],
)
def test_segment_exact_true_positives_are_blocked(
    tmp_path: Path, blocked_path: str
) -> None:
    """Regression: alembic dir/ini, Makefile basename, Dockerfile basename must
    all still be hard-denied by the segment-exact check.
    """
    writer = _FakeWriter()
    applier = UpdateApplier(
        writer=writer,
        capability_checker=_FixedChecker({"config_self_modify"}),
        workspace_root=tmp_path,
    )
    plan = _Plan(
        kind="config",
        capability_required="config_self_modify",
        target_paths=[blocked_path],
    )

    result = applier.apply(plan, "a: 1\n")

    assert result.status == "denied", (
        f"Expected 'denied' for protected path {blocked_path!r}, "
        f"got {result.status!r} — evidence: {result.evidence}"
    )
    assert writer.writes == []
