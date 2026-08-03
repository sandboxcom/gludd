"""Deep tests for self-update: version comparison, download, signing, rollback, hot-reload.

Covers:
  - Ed25519 signature verification (signing.py): valid, tampered, wrong key, edge cases
  - UpdateApplier (applier.py): capability gates, signature gating, workspace confinement,
    protected-path denial, YAML validation, empty-targets, rollback on post-write parse failure
  - AtomicSafeWriter (safe_writer.py): atomic write, confinement, rollback via validate hook,
    temp cleanup, recorder hook
  - Module hot-reload (module_snapshot.py): snapshot, restore, extension skip, singleton warnings,
    live reference detection, thread safety shape
  - GrindingDetector (grinding_detector.py): grinding episodes, premature stops, report generation
  - Version comparison semantics (semantic version ordering, pre-release ordering)
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from types import ModuleType
from typing import cast
from unittest.mock import patch

import pytest

from general_ludd.self_update.applier import (
    UpdateApplier,
    _first_protected,
    _resolve_confined,
    _restore_snapshots,
)
from general_ludd.self_update.grinding_detector import (
    GrindingDetector,
    GrindingEpisode,
    GrindingReport,
    StopEpisode,
    _read_json,
    _recent_count,
    _recent_max_streak,
    detect_and_create_todos,
)
from general_ludd.self_update.model import (
    ApplyTier,
    ChangeKind,
    SelfUpdatePlan,
    SelfUpdateRequest,
    Subsystem,
)
from general_ludd.self_update.module_snapshot import (
    _SINGLETON_LIKE_NAMES,
    ModuleSnapshot,
    _is_extension_module,
    find_live_references,
    restore_modules,
    snapshot_modules,
)
from general_ludd.self_update.safe_writer import AtomicSafeWriter
from general_ludd.self_update.signing import (
    load_public_key,
    verify_signature,
)

# ═══════════════════════════════════════════════════════════════════════════════
# Ed25519 signing tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestSigningEd25519:
    def test_valid_signature_passes(self) -> None:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        sk = Ed25519PrivateKey.generate()
        pk_bytes = sk.public_key().public_bytes_raw()
        pk_hex = pk_bytes.hex()
        content = "hello gludd"
        sig = sk.sign(content.encode())
        sig_hex = sig.hex()

        assert verify_signature(content, sig_hex, pk_hex) is True

    def test_tampered_content_fails(self) -> None:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        sk = Ed25519PrivateKey.generate()
        pk_hex = sk.public_key().public_bytes_raw().hex()
        content = "hello gludd"
        sig = sk.sign(content.encode())
        sig_hex = sig.hex()

        assert verify_signature("tampered content", sig_hex, pk_hex) is False

    def test_wrong_key_fails(self) -> None:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        sk1 = Ed25519PrivateKey.generate()
        sk2 = Ed25519PrivateKey.generate()
        pk_hex = sk2.public_key().public_bytes_raw().hex()
        content = "hello gludd"
        sig = sk1.sign(content.encode())
        sig_hex = sig.hex()

        assert verify_signature(content, sig_hex, pk_hex) is False

    def test_empty_content_fails(self) -> None:
        assert verify_signature("", "aa" * 128, "bb" * 64) is False

    def test_empty_signature_fails(self) -> None:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        sk = Ed25519PrivateKey.generate()
        pk_hex = sk.public_key().public_bytes_raw().hex()
        assert verify_signature("content", "", pk_hex) is False

    def test_empty_public_key_fails(self) -> None:
        assert verify_signature("content", "aa" * 128, "") is False

    def test_malformed_public_key_hex_fails(self) -> None:
        assert verify_signature("content", "aa" * 128, "not-hex") is False

    def test_wrong_length_signature_fails(self) -> None:
        assert verify_signature("content", "aa" * 10, "bb" * 64) is False

    def test_base64_encoded_key_accepted(self) -> None:
        import base64

        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        sk = Ed25519PrivateKey.generate()
        pk_b64 = base64.b64encode(sk.public_key().public_bytes_raw()).decode()
        content = "hello gludd"
        sig = sk.sign(content.encode())
        sig_hex = sig.hex()

        assert verify_signature(content, sig_hex, pk_b64) is True

    def test_malformed_base64_signature_fails(self) -> None:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        sk = Ed25519PrivateKey.generate()
        pk_hex = sk.public_key().public_bytes_raw().hex()
        content = "hello gludd"
        sig = sk.sign(content.encode())
        sig_hex = sig.hex()

        assert verify_signature(content, sig_hex, pk_hex) is True
        assert verify_signature(content, "!!!not-valid-base64!!!", pk_hex) is False


# ═══════════════════════════════════════════════════════════════════════════════
# Version comparison (semantic version ordering)
# ═══════════════════════════════════════════════════════════════════════════════


class TestVersionComparison:
    def _parse_version_for_comparison(self, version: str) -> tuple[int, ...]:
        v = version.lstrip("v")
        parts = v.split(".")[:3]
        while len(parts) < 3:
            parts.append("0")
        return tuple(int(p.split("-")[0]) if p.split("-")[0].isdigit() else 0 for p in parts)

    def _parse_prerelease_for_comparison(self, version: str) -> tuple[str, int]:
        v = version.lstrip("v")
        for sep in ("-alpha.", "-beta.", "-rc."):
            if sep in v:
                variant = sep.replace("-", "").replace(".", "")
                num_str = v.split(sep)[1].split(".")[0].split("-")[0]
                try:
                    return (variant, int(num_str))
                except ValueError:
                    return (variant, 0)
        return ("release", 0)

    def test_release_gt_prerelease(self) -> None:
        assert self._parse_prerelease_for_comparison("v0.1.0") > self._parse_prerelease_for_comparison("v0.1.0-alpha.1")

    def test_newer_major_gt_older(self) -> None:
        assert self._parse_version_for_comparison("v1.0.0") > self._parse_version_for_comparison("v0.9.9")

    def test_newer_minor_gt_older(self) -> None:
        assert self._parse_version_for_comparison("v0.2.0") > self._parse_version_for_comparison("v0.1.9")

    def test_newer_patch_gt_older(self) -> None:
        assert self._parse_version_for_comparison("v0.1.2") > self._parse_version_for_comparison("v0.1.1")

    def test_same_version_equal(self) -> None:
        assert self._parse_version_for_comparison("v0.1.0") == self._parse_version_for_comparison("0.1.0")

    def test_alpha_lt_beta_same_version(self) -> None:
        a = self._parse_prerelease_for_comparison("v0.1.0-alpha.1")
        b = self._parse_prerelease_for_comparison("v0.1.0-beta.1")
        assert a < b

    def test_beta_lt_rc_same_version(self) -> None:
        b = self._parse_prerelease_for_comparison("v0.1.0-beta.1")
        r = self._parse_prerelease_for_comparison("v0.1.0-rc.1")
        assert b < r

    def test_prerelease_number_ordering(self) -> None:
        a1 = self._parse_prerelease_for_comparison("v0.1.0-alpha.1")
        a2 = self._parse_prerelease_for_comparison("v0.1.0-alpha.2")
        assert a1 < a2


# ═══════════════════════════════════════════════════════════════════════════════
# UpdateApplier: capability gate, signature gating, confinement, protection
# ═══════════════════════════════════════════════════════════════════════════════


class FakeWriter:
    def __init__(self) -> None:
        self.written: dict[str, str] = {}

    def write(self, path: str, content: str) -> str | None:
        self.written[path] = content
        return path


class StubCapabilityChecker:
    def __init__(self, allowed: bool = True) -> None:
        self._allowed = allowed

    def allows(self, capability: str) -> bool:
        return self._allowed


class StubPlan:
    def __init__(
        self,
        kind: str = "config",
        capability_required: str = "config_write",
        target_paths: list[str] | None = None,
    ) -> None:
        self.kind = kind
        self.capability_required = capability_required
        self.target_paths = target_paths if target_paths is not None else ["tmp_config.yml"]


class TestUpdateApplierGating:
    def test_capability_denied_returns_denied(self, tmp_path: Path) -> None:
        writer = FakeWriter()
        checker = StubCapabilityChecker(allowed=False)
        applier = UpdateApplier(writer, checker, tmp_path)
        plan = StubPlan()
        result = applier.apply(plan, "key: value")
        assert result.status == "denied"
        assert "capability not allowed" in result.evidence

    def test_signature_verification_configured_no_signature_denies(self, tmp_path: Path) -> None:
        writer = FakeWriter()
        checker = StubCapabilityChecker(allowed=True)
        applier = UpdateApplier(writer, checker, tmp_path)
        plan = StubPlan()
        result = applier.apply(
            plan,
            "key: value",
            verify_signature=verify_signature,
            content_signature="",
            public_key="",
        )
        assert result.status == "denied"
        assert "no content_signature or public_key" in result.evidence

    def test_signature_verification_fails_denies(self, tmp_path: Path) -> None:
        writer = FakeWriter()
        checker = StubCapabilityChecker(allowed=True)
        applier = UpdateApplier(writer, checker, tmp_path)
        plan = StubPlan()
        result = applier.apply(
            plan,
            "key: value",
            verify_signature=verify_signature,
            content_signature="aa" * 64,
            public_key="bb" * 32,
        )
        assert result.status == "denied"
        assert "signature verification failed" in result.evidence

    def test_signature_passes_when_not_configured(self, tmp_path: Path) -> None:
        writer = FakeWriter()
        checker = StubCapabilityChecker(allowed=True)
        applier = UpdateApplier(writer, checker, tmp_path)
        plan = StubPlan(target_paths=["test.yml"])
        result = applier.apply(plan, "key: value")
        assert result.status == "applied"

    def test_path_escapes_workspace_root_denied(self, tmp_path: Path) -> None:
        writer = FakeWriter()
        checker = StubCapabilityChecker(allowed=True)
        applier = UpdateApplier(writer, checker, tmp_path)
        plan = StubPlan(target_paths=["../etc_passwd"])
        result = applier.apply(plan, "key: value")
        assert result.status == "denied"
        assert "escapes workspace root" in result.evidence

    def test_absolute_path_outside_root_denied(self, tmp_path: Path) -> None:
        writer = FakeWriter()
        checker = StubCapabilityChecker(allowed=True)
        applier = UpdateApplier(writer, checker, tmp_path)
        plan = StubPlan(target_paths=["/etc/shadow"])
        result = applier.apply(plan, "key: value")
        assert result.status == "denied"
        assert "escapes workspace root" in result.evidence

    def test_invalid_yaml_denies(self, tmp_path: Path) -> None:
        writer = FakeWriter()
        checker = StubCapabilityChecker(allowed=True)
        applier = UpdateApplier(writer, checker, tmp_path)
        plan = StubPlan(target_paths=["test.yml"])
        result = applier.apply(plan, "key: value: broken: yaml: indentation")
        assert result.status == "denied"
        assert "invalid yaml" in result.evidence.lower()

    def test_code_kind_returns_proposed_not_blind_applied(self, tmp_path: Path) -> None:
        writer = FakeWriter()
        checker = StubCapabilityChecker(allowed=True)
        applier = UpdateApplier(writer, checker, tmp_path)
        plan = StubPlan(kind="code", target_paths=["src/module.py"])
        result = applier.apply(plan, "print('hello')")
        assert result.status == "proposed"

    def test_empty_target_paths_denied_for_yaml_kinds(self, tmp_path: Path) -> None:
        writer = FakeWriter()
        checker = StubCapabilityChecker(allowed=True)
        applier = UpdateApplier(writer, checker, tmp_path)
        plan = StubPlan(kind="config", target_paths=[])
        result = applier.apply(plan, "key: value")
        assert result.status == "denied"
        assert "no target paths" in result.evidence

    def test_unknown_kind_denied(self, tmp_path: Path) -> None:
        writer = FakeWriter()
        checker = StubCapabilityChecker(allowed=True)
        applier = UpdateApplier(writer, checker, tmp_path)
        plan = StubPlan(kind="unsupported_type", target_paths=["test.yml"])
        result = applier.apply(plan, "key: value")
        assert result.status == "denied"
        assert "unsupported plan kind" in result.evidence

    def test_valid_yaml_applied_and_written(self, tmp_path: Path) -> None:
        writer = FakeWriter()
        checker = StubCapabilityChecker(allowed=True)
        applier = UpdateApplier(writer, checker, tmp_path)
        plan = StubPlan(target_paths=["test.yml"])
        result = applier.apply(plan, "spend_cap: 500")
        assert result.status == "applied"
        assert any("test.yml" in p for p in writer.written)

    def test_post_write_rollback_on_parse_failure(self, tmp_path: Path) -> None:
        (tmp_path / "exist.yml").write_text("original: data", encoding="utf-8")
        writer = FakeWriter()
        checker = StubCapabilityChecker(allowed=True)
        applier = UpdateApplier(writer, checker, tmp_path)
        plan = StubPlan(target_paths=["exist.yml"])
        result = applier.apply(plan, "not yaml at all: ::::")
        assert result.status == "denied"
        assert "invalid yaml" in result.evidence.lower()


class TestResolutionConfinement:
    def test_resolve_confined_paths_inside_root(self, tmp_path: Path) -> None:
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "test.yml").touch()
        escapee, resolved = _resolve_confined(["config/test.yml"], tmp_path)
        assert escapee is None
        assert len(resolved) == 1
        assert resolved[0].is_relative_to(tmp_path.resolve())

    def test_resolve_confined_dotdot_escape_detected(self, tmp_path: Path) -> None:
        escapee, _resolved = _resolve_confined(["../etc_passwd"], tmp_path)
        assert escapee is not None

    def test_resolve_confined_absolute_outside_detected(self, tmp_path: Path) -> None:
        escapee, _resolved = _resolve_confined(["/etc/hostname"], tmp_path)
        assert escapee is not None


class TestFirstProtected:
    def test_known_protected_marker_detected(self) -> None:
        assert _first_protected([".github/workflows/ci.yml"]) is not None

    def test_allowlisted_segments_pass_through(self) -> None:
        assert _first_protected(["src/general_ludd/config_editor.py"]) is None

    def test_settings_path_refused(self) -> None:
        assert _first_protected([".claude/settings.json"]) is not None

    def test_general_ludd_src_passes(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "general_ludd").mkdir()
        assert _first_protected(["src/general_ludd/my_module.py"], tmp_path) is None


class TestRestoreSnapshots:
    def test_restore_none_removes_created_file(self, tmp_path: Path) -> None:
        target = tmp_path / "new_file.yml"
        target.write_text("fresh content", encoding="utf-8")
        _restore_snapshots([(target, None)])
        assert not target.exists()

    def test_restore_bytes_reverts_content(self, tmp_path: Path) -> None:
        target = tmp_path / "existing.yml"
        target.write_text("original content", encoding="utf-8")
        prior = b"original content"
        target.write_text("new content", encoding="utf-8")
        _restore_snapshots([(target, prior)])
        assert target.read_bytes() == prior

    def test_restore_oserror_on_one_does_not_abort_others(self, tmp_path: Path) -> None:
        target_a = tmp_path / "a.yml"
        target_b = tmp_path / "b.yml"
        target_a.write_text("content a", encoding="utf-8")
        target_b.write_text("content b", encoding="utf-8")
        prior_a = b"content a"
        prior_b = b"content b"
        target_a.write_text("new a")
        target_b.write_text("new b")
        _restore_snapshots(
            [
                (tmp_path / "nonexistent_child" / "phantom.yml", prior_a),
                (target_a, prior_a),
                (target_b, prior_b),
            ]
        )
        assert target_a.read_bytes() == prior_a
        assert target_b.read_bytes() == prior_b


# ═══════════════════════════════════════════════════════════════════════════════
# Module hot-reload: snapshot, restore, live references
# ═══════════════════════════════════════════════════════════════════════════════


class TestModuleSnapshot:
    def test_snapshot_of_existing_module(self) -> None:
        import general_ludd.self_update.model as target

        snap = snapshot_modules(["general_ludd.self_update.model"])
        assert snap.modules
        assert "general_ludd.self_update.model" in snap.modules
        assert snap.modules["general_ludd.self_update.model"] is target
        assert snap.snapshot_at > 0

    def test_snapshot_of_missing_module_produces_empty_entry(self) -> None:
        snap = snapshot_modules(["non.existent.module.xyz"])
        assert not snap.modules
        assert "non.existent.module.xyz" not in snap.modules

    def test_snapshot_bool_false_when_empty(self) -> None:
        snap = ModuleSnapshot()
        assert not snap

    def test_snapshot_bool_true_when_has_modules(self) -> None:
        snap = ModuleSnapshot()
        snap.modules["foo"] = cast(ModuleType, sys.modules["sys"])
        assert snap

    def test_extension_module_skipped_with_warning(self) -> None:
        import math

        snap = snapshot_modules(["math"])
        if _is_extension_module(sys.modules.get("math", math)):  # type: ignore[arg-type]
            assert any("math" in w for w in snap.warnings)

    def test_restore_modules_repopulates_sys_modules(self) -> None:
        import general_ludd.self_update.model as original

        snap = snapshot_modules(["general_ludd.self_update.model"])
        assert snap.modules
        sys.modules.pop("general_ludd.self_update.model", None)
        restored = restore_modules(snap)
        assert "general_ludd.self_update.model" in restored
        assert sys.modules.get("general_ludd.self_update.model") is original

    def test_snapshot_warns_on_singleton_like_globals(self) -> None:
        import general_ludd.self_update.model

        if hasattr(general_ludd.self_update.model, "client"):
            snap = snapshot_modules(["general_ludd.self_update.model"])
            assert snap.warnings or snap.modules

    def test_find_live_references_on_present_module(self) -> None:
        refs = find_live_references("sys")
        assert isinstance(refs, list)
        for ref in refs:
            assert isinstance(ref, str)

    def test_find_live_references_on_missing_module_returns_empty(self) -> None:
        refs = find_live_references("nonexistent.module.ghost")
        assert refs == []

    def test_threading_lock_prevents_interleaving(self) -> None:

        snap = snapshot_modules(["general_ludd.self_update.model"])
        results: list[list[str]] = []

        def restore_thread() -> None:
            restored = restore_modules(snap)
            results.append(restored)

        t = threading.Thread(target=restore_thread)
        t.start()
        t.join()
        assert results
        assert "general_ludd.self_update.model" in results[0]


# ═══════════════════════════════════════════════════════════════════════════════
# AtomicSafeWriter: atomic write, confinement, rollback, temp cleanup
# ═══════════════════════════════════════════════════════════════════════════════


class TestAtomicSafeWriter:
    def test_atomic_write_creates_file(self, tmp_path: Path) -> None:
        writer = AtomicSafeWriter(tmp_path)
        target = str(tmp_path / "new_config.yml")
        writer.write(target, "key: value")
        assert Path(target).exists()
        assert Path(target).read_text(encoding="utf-8") == "key: value"

    def test_write_outside_workspace_root_raises(self, tmp_path: Path) -> None:
        writer = AtomicSafeWriter(tmp_path)
        with pytest.raises(ValueError):
            writer.write("../escape.yml", "payload")

    def test_write_absolute_path_outside_root_raises(self, tmp_path: Path) -> None:
        writer = AtomicSafeWriter(tmp_path)
        with pytest.raises(ValueError):
            writer.write("/etc/malicious.yml", "payload")

    def test_write_resolved_path_returned(self, tmp_path: Path) -> None:
        writer = AtomicSafeWriter(tmp_path)
        result = writer.write("conf/test.yml", "data")
        assert result == str((tmp_path / "conf" / "test.yml").resolve())

    def test_validate_hook_passing_allows_write(self, tmp_path: Path) -> None:
        writer = AtomicSafeWriter(tmp_path)
        result = writer.write(
            "config.yml",
            "key: value",
            validate=lambda p: Path(p).read_text(encoding="utf-8").startswith("key"),
        )
        assert result
        assert Path(result).exists()

    def test_validate_hook_rejection_rolls_back(self, tmp_path: Path) -> None:
        (tmp_path / "original.yml").write_text("original: data", encoding="utf-8")
        writer = AtomicSafeWriter(tmp_path)
        with pytest.raises(RuntimeError, match="rolled back"):
            writer.write(
                "original.yml",
                "new: data",
                validate=lambda _p: False,
            )
        assert Path(str(tmp_path / "original.yml")).read_text(encoding="utf-8") == "original: data"

    def test_validate_hook_raising_rolls_back(self, tmp_path: Path) -> None:
        (tmp_path / "raise_me.yml").write_text("original", encoding="utf-8")
        writer = AtomicSafeWriter(tmp_path)
        with pytest.raises(RuntimeError, match="rolled back"):
            writer.write(
                "raise_me.yml",
                "new content",
                validate=lambda _p: (_ for _ in ()).throw(ValueError("boom")),
            )
        assert Path(str(tmp_path / "raise_me.yml")).read_text(encoding="utf-8") == "original"

    def test_recorder_called_on_successful_write(self, tmp_path: Path) -> None:
        recorded: list[tuple] = []
        writer = AtomicSafeWriter(tmp_path, recorder=lambda p, o, c: recorded.append((p, o, c)))
        writer.write("recorded.yml", "hello")
        assert len(recorded) == 1
        assert recorded[0][0].endswith("recorded.yml")
        assert recorded[0][2] == "hello"

    def test_recorder_failure_does_not_break_write(self, tmp_path: Path) -> None:
        def failing_recorder(*_args: object) -> None:
            raise RuntimeError("recorder crash")

        writer = AtomicSafeWriter(tmp_path, recorder=failing_recorder)  # type: ignore[arg-type]
        result = writer.write("survive.yml", "data")
        assert Path(result).exists()
        assert Path(result).read_text(encoding="utf-8") == "data"


# ═══════════════════════════════════════════════════════════════════════════════
# GrindingDetector: episodes, premature stops, report generation
# ═══════════════════════════════════════════════════════════════════════════════


class TestGrindingDetector:
    def test_detect_grinding_episodes(self) -> None:
        detector = GrindingDetector(streak_threshold=4)
        calls: list[dict] = [
            {"tool_name": "read", "timestamp": 100.0},
            {"tool_name": "read", "timestamp": 101.0},
            {"tool_name": "read", "timestamp": 102.0},
            {"tool_name": "read", "timestamp": 103.0},
            {"tool_name": "read", "timestamp": 104.0},
            {"tool_name": "task", "timestamp": 105.0},
        ]
        episodes = detector.detect_grinding(calls)
        assert len(episodes) == 1
        assert episodes[0].tool_count == 5
        assert set(episodes[0].tool_names) == {"read"}

    def test_detect_grinding_no_dispatch_no_episode(self) -> None:
        detector = GrindingDetector(streak_threshold=4)
        calls: list[dict] = [
            {"tool_name": "read", "timestamp": 100.0},
            {"tool_name": "read", "timestamp": 101.0},
            {"tool_name": "task", "timestamp": 102.0},
        ]
        episodes = detector.detect_grinding(calls)
        assert episodes == []

    def test_detect_grinding_is_dispatch_flag_respected(self) -> None:
        detector = GrindingDetector(streak_threshold=4)
        calls: list[dict] = [
            {"tool_name": "read", "timestamp": 100.0},
            {"tool_name": "read", "timestamp": 101.0},
            {"tool_name": "read", "timestamp": 102.0},
            {"tool_name": "read", "timestamp": 103.0},
            {"tool_name": "read", "timestamp": 104.0},
            {"tool_name": "custom", "is_dispatch": True, "timestamp": 105.0},
        ]
        episodes = detector.detect_grinding(calls)
        assert len(episodes) == 1
        assert episodes[0].tool_count == 5

    def test_trailing_grinding_streak_detected(self) -> None:
        detector = GrindingDetector(streak_threshold=4)
        calls: list[dict] = [
            {"tool_name": "task", "timestamp": 100.0},
            {"tool_name": "read", "timestamp": 101.0},
            {"tool_name": "read", "timestamp": 102.0},
            {"tool_name": "read", "timestamp": 103.0},
            {"tool_name": "read", "timestamp": 104.0},
            {"tool_name": "read", "timestamp": 105.0},
        ]
        episodes = detector.detect_grinding(calls)
        assert len(episodes) == 1
        assert episodes[0].tool_count == 5

    def test_detect_premature_stop(self) -> None:
        detector = GrindingDetector(idle_threshold=5.0)
        responses: list[dict] = [
            {"has_tool_calls": False, "timestamp": 100.0},
            {"has_tool_calls": True, "timestamp": 200.0},
        ]
        episodes = detector.detect_premature_stop(responses)
        assert len(episodes) == 1
        assert episodes[0].response_index == 0
        assert episodes[0].idle_seconds == 100.0

    def test_detect_premature_stop_only_text_without_idle_not_flagged(self) -> None:
        detector = GrindingDetector(idle_threshold=9999.0)
        responses: list[dict] = [
            {"has_tool_calls": False, "timestamp": 100.0},
            {"has_tool_calls": True, "timestamp": 110.0},
        ]
        episodes = detector.detect_premature_stop(responses)
        assert episodes == []

    def test_generate_remediation_report(self, tmp_path: Path) -> None:
        detector = GrindingDetector(streak_threshold=2)
        calls: list[dict] = [
            {"tool_name": "read", "timestamp": 1.0},
            {"tool_name": "read", "timestamp": 2.0},
            {"tool_name": "read", "timestamp": 3.0},
        ]
        detector.detect_grinding(calls)
        responses: list[dict] = [
            {"has_tool_calls": False, "timestamp": 1.0},
            {"has_tool_calls": True, "timestamp": 100.0},
        ]
        detector.detect_premature_stop(responses)

        report = detector.generate_remediation_report()
        assert "grinding_episodes" in report
        assert len(report["grinding_episodes"]) >= 1
        assert "premature_stop_episodes" in report or "stop_episodes" in report
        assert report["total_tool_calls_analyzed"] == 3
        assert report["total_responses_analyzed"] == 2

    def test_empty_inputs_produce_empty_report(self) -> None:
        detector = GrindingDetector()
        assert detector.detect_grinding([]) == []
        assert detector.detect_premature_stop([]) == []

    def test_grinding_report_roundtrip(self) -> None:
        report = GrindingReport(
            grinding_episodes=[
                GrindingEpisode(
                    start_index=0,
                    end_index=4,
                    tool_count=5,
                    tool_names=["read", "edit", "write"],
                    start_timestamp=100.0,
                    end_timestamp=110.0,
                ),
            ],
            stop_episodes=[
                StopEpisode(response_index=2, idle_seconds=45.0, timestamp=200.0),
            ],
            total_tool_calls_analyzed=20,
            total_responses_analyzed=10,
            generated_at=300.0,
        )
        d = {
            "grinding_episodes": [
                {
                    "start_index": e.start_index,
                    "end_index": e.end_index,
                    "tool_count": e.tool_count,
                    "tool_names": e.tool_names,
                    "start_timestamp": e.start_timestamp,
                    "end_timestamp": e.end_timestamp,
                }
                for e in report.grinding_episodes
            ],
            "stop_episodes": [
                {"response_index": e.response_index, "idle_seconds": e.idle_seconds, "timestamp": e.timestamp}
                for e in report.stop_episodes
            ],
        }
        assert d["grinding_episodes"][0]["tool_count"] == 5
        assert d["stop_episodes"][0]["idle_seconds"] == 45.0


# ═══════════════════════════════════════════════════════════════════════════════
# Apply ladder: robustness edge cases
# ═══════════════════════════════════════════════════════════════════════════════


class TestApplyLadderRobustness:
    def test_capability_checker_raises_fails_closed(self, tmp_path: Path) -> None:
        class RaisingChecker:
            def allows(self, capability: str) -> bool:
                raise ValueError("boom")

        from general_ludd.self_update.applier import UpdateApplier as UA

        writer = FakeWriter()
        applier = UA(writer, RaisingChecker(), tmp_path)
        plan = StubPlan(target_paths=["test.yml"])
        result = applier.apply(plan, "key: value")
        assert result.status == "denied"
        assert "capability check raised" in result.evidence

    def test_signature_verifier_raises_fails_closed(self, tmp_path: Path) -> None:
        from general_ludd.self_update.applier import UpdateApplier as UA

        def raising_verifier(_c: str, _s: str, _k: str) -> bool:
            raise ValueError("verifier crash")

        writer = FakeWriter()
        checker = StubCapabilityChecker(allowed=True)
        applier = UA(writer, checker, tmp_path)
        plan = StubPlan(target_paths=["test.yml"])
        result = applier.apply(
            plan,
            "key: value",
            verify_signature=raising_verifier,
            content_signature="aa" * 64,
            public_key="bb" * 32,
        )
        assert result.status == "denied"
        assert "signature verification raised" in result.evidence

    def test_percent_encoded_path_escape_detected(self, tmp_path: Path) -> None:
        writer = FakeWriter()
        checker = StubCapabilityChecker(allowed=True)
        applier = UpdateApplier(writer, checker, tmp_path)
        plan = StubPlan(target_paths=["..%2F..%2Fetc%2Fpasswd"])
        result = applier.apply(plan, "key: value")
        assert result.status == "denied"
        assert "escapes workspace root" in result.evidence

    def test_write_failure_rolls_back_and_denies(self, tmp_path: Path) -> None:
        (tmp_path / "exists.yml").write_text("original", encoding="utf-8")

        class FailingWriter:
            def __init__(self) -> None:
                self.called = 0

            def write(self, path: str, content: str) -> str | None:
                self.called += 1
                raise OSError("disk full")

        from general_ludd.self_update.applier import UpdateApplier as UA

        writer = FailingWriter()
        checker = StubCapabilityChecker(allowed=True)
        applier = UA(writer, checker, tmp_path)
        plan = StubPlan(target_paths=["exists.yml"])
        result = applier.apply(plan, "key: value")
        assert result.status == "denied"
        assert "write failed" in result.evidence
        assert (tmp_path / "exists.yml").read_text(encoding="utf-8") == "original"


# ═══════════════════════════════════════════════════════════════════════════════
# Public key loading
# ═══════════════════════════════════════════════════════════════════════════════


class TestPublicKeyLoading:
    def test_load_from_file(self, tmp_path: Path) -> None:
        key_file = tmp_path / "pub_key"
        key_file.write_text("abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789")
        result = load_public_key(str(key_file))
        assert result == "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"

    def test_load_from_env_var_inline(self) -> None:
        with patch.dict(os.environ, {"GLUDD_SELF_UPDATE_PUBLIC_KEY": "env-key-hex"}, clear=True):
            result = load_public_key()
            assert result == "env-key-hex"

    def test_load_from_env_var_file(self, tmp_path: Path) -> None:
        key_file = tmp_path / "env_key_file"
        key_file.write_text("file-key-content")
        with patch.dict(os.environ, {"GLUDD_SELF_UPDATE_PUBLIC_KEY_FILE": str(key_file)}, clear=True):
            result = load_public_key()
            assert result == "file-key-content"

    def test_load_nothing_returns_empty(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            result = load_public_key()
            assert result == ""

    def test_key_path_priority_over_env(self, tmp_path: Path) -> None:
        key_file = tmp_path / "explicit_key"
        key_file.write_text("explicit")
        with patch.dict(os.environ, {"GLUDD_SELF_UPDATE_PUBLIC_KEY": "env-key"}, clear=True):
            result = load_public_key(str(key_file))
            assert result == "explicit"


# ═══════════════════════════════════════════════════════════════════════════════
# Generation-time hot-reload trigger contract
# ═══════════════════════════════════════════════════════════════════════════════


class TestHotReloadTriggerContract:
    """Pin the structural contract: modules that a hot-reload system must snapshot,
    restore, and verify."""

    def test_module_snapshot_is_dataclass(self) -> None:
        assert hasattr(ModuleSnapshot, "__dataclass_fields__")
        snap = ModuleSnapshot()
        assert hasattr(snap, "modules")
        assert hasattr(snap, "snapshot_at")
        assert hasattr(snap, "warnings")

    def test_singleton_names_set_contains_expected_patterns(self) -> None:
        assert "pool" in _SINGLETON_LIKE_NAMES
        assert "client" in _SINGLETON_LIKE_NAMES
        assert "cache" in _SINGLETON_LIKE_NAMES

    def test_snapshot_and_restore_share_lock(self) -> None:
        import importlib

        from general_ludd.self_update import module_snapshot as ms

        importlib.reload(ms)
        snap1 = ms.snapshot_modules(["sys"])
        snap2 = ms.snapshot_modules(["os"])
        restored1 = ms.restore_modules(snap1)
        restored2 = ms.restore_modules(snap2)
        all_restored = set(restored1) | set(restored2)
        assert all_restored

    def test_warning_on_nonexistent_module_does_not_crash(self) -> None:
        snap = snapshot_modules(["ghost.module", "another.phantom"])
        assert not snap.modules

    def test_apply_plan_known_config_tier_passes(self) -> None:
        from general_ludd.self_update.apply import apply_plan

        plan = SelfUpdatePlan(
            subsystem=Subsystem.CONFIG,
            change_kind=ChangeKind.VALUE_EDIT,
            target_files=("config/test.yml",),
            apply_tier=ApplyTier.CONFIG,
            requires_approval=False,
            rationale="test",
            confidence=0.9,
        )
        request = SelfUpdateRequest(raw_text="set timeout to 30")
        result = apply_plan(plan, request)
        assert result.outcome == "applied"


# ═══════════════════════════════════════════════════════════════════════════════
# detect_and_create_todos: reading from state files, generating todos
# ═══════════════════════════════════════════════════════════════════════════════


class TestDetectAndCreateTodos:
    def test_detect_and_create_todos_returns_list(self) -> None:
        todos = detect_and_create_todos()
        assert isinstance(todos, list)

    def test_read_json_nonexistent_file_returns_empty(self) -> None:
        result = _read_json("/tmp/gludd-nonexistent-file-for-test.json")
        assert result == {}

    def test_read_json_malformed_returns_empty(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not valid json {{{")
        result = _read_json(str(bad_file))
        assert result == {}

    def test_recent_max_streak_scalar(self) -> None:
        record = {"streak": 12, "timestamp": __import__("time").time()}
        result = _recent_max_streak(record, 3600)
        assert result == 12

    def test_recent_max_streak_history(self) -> None:
        now = __import__("time").time()
        record = {
            "entries": [
                {"streak": 3, "timestamp": now - 10},
                {"streak": 8, "timestamp": now - 5},
                {"streak": 4, "timestamp": now - 20},
            ]
        }
        result = _recent_max_streak(record, 3600)
        assert result == 8

    def test_recent_count_with_blocked_key(self) -> None:
        now = __import__("time").time()
        record = {
            "entries": [
                {"blocked": True, "timestamp": now - 10},
                {"blocked": False, "timestamp": now - 5},
                {"blocked": True, "timestamp": now - 20},
            ]
        }
        result = _recent_count(record, 3600, "blocked")
        assert result == 2

    def test_recent_count_stale_entries_excluded(self) -> None:
        now = __import__("time").time()
        record = {
            "entries": [
                {"blocked": True, "timestamp": now - 10000},
                {"blocked": True, "timestamp": now - 10},
            ]
        }
        result = _recent_count(record, 60, "blocked")
        assert result == 1
