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
