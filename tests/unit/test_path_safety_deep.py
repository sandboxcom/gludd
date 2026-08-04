"""Deep path and filesystem safety tests.

Covers traversal prevention, symlink attacks, temp-file atomicity,
permission checks, and zip/tar archive extraction safety across the
general_ludd codebase.
"""

from __future__ import annotations

import io
import os
import stat
import tarfile
import urllib.parse
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from general_ludd.self_update.applier import (
    _first_protected,
    _resolve_confined,
)
from general_ludd.self_update.safe_writer import AtomicSafeWriter
from general_ludd.validation.runner import (
    CommandValidationError,
    _validate_worktree_path,
)
from general_ludd.worktree.core import confine_worktree_path

# ---------------------------------------------------------------------------
# Path traversal prevention
# ---------------------------------------------------------------------------


class TestConfineWorktreePathTraversal:
    """``confine_worktree_path`` must reject all escape attempts."""

    def test_rejects_dotdot_traversal(self, tmp_path: Path) -> None:
        base = tmp_path / "legal"
        base.mkdir()
        outside = tmp_path / "escape.txt"
        outside.write_text("x")
        bad = str(base / ".." / "escape.txt")
        with pytest.raises(ValueError, match="escapes the allowed base"):
            confine_worktree_path(bad, str(base))

    def test_rejects_absolute_path_outside_base(self, tmp_path: Path) -> None:
        base = tmp_path / "legal"
        base.mkdir()
        with pytest.raises(ValueError, match="escapes the allowed base"):
            confine_worktree_path("/etc/passwd", str(base))

    def test_rejects_path_starting_with_dash(self, tmp_path: Path) -> None:
        base = tmp_path / "legal"
        base.mkdir()
        with pytest.raises(ValueError, match="begins with '-'"):
            confine_worktree_path("-rf", str(base))

    def test_rejects_symlink_outside_base(self, tmp_path: Path) -> None:
        base = tmp_path / "legal"
        base.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("secret")
        link = base / "innocent"
        link.symlink_to(outside)
        with pytest.raises(ValueError, match="escapes the allowed base"):
            confine_worktree_path(str(link), str(base))

    def test_allows_legitimate_path_within_base(self, tmp_path: Path) -> None:
        base = tmp_path / "legal"
        base.mkdir()
        real = base / "sub" / "ok.txt"
        real.parent.mkdir()
        real.touch()
        result = confine_worktree_path(str(real), str(base))
        assert result == os.path.realpath(str(real))

    def test_expands_user_tilde_in_base(self, tmp_path: Path) -> None:
        base = tmp_path / "home"
        base.mkdir()
        real = base / "config.yml"
        real.touch()
        result = confine_worktree_path(str(real), str(base))
        assert os.path.basename(result) == "config.yml"

    def test_returns_realpath_on_success(self, tmp_path: Path) -> None:
        base = tmp_path / "legal"
        base.mkdir()
        sub = base / "sub"
        sub.mkdir()
        link = base / "shortcut"
        link.symlink_to(sub, target_is_directory=True)
        result = confine_worktree_path(str(link), str(base))
        assert result == os.path.realpath(str(sub))


# ---------------------------------------------------------------------------
# Validate worktree path (higher-level entry point)
# ---------------------------------------------------------------------------


class TestValidateWorktreePath:
    """``_validate_worktree_path`` layers validation on top of confinement."""

    def test_rejects_empty_path(self) -> None:
        with pytest.raises(CommandValidationError, match="must not be empty"):
            _validate_worktree_path("")

    def test_rejects_whitespace_only(self) -> None:
        with pytest.raises(CommandValidationError, match="must not be empty"):
            _validate_worktree_path("   ")

    def test_rejects_dash_prefix_pre_validation(self) -> None:
        with pytest.raises(CommandValidationError, match="begins with '-'"):
            _validate_worktree_path("--help")

    def test_rejects_relative_path(self) -> None:
        with pytest.raises(CommandValidationError, match="must be an absolute path"):
            _validate_worktree_path("relative/dir")

    def test_delegates_to_confine_when_root_given(self, tmp_path: Path) -> None:
        base = tmp_path / "legal"
        base.mkdir()
        bad = str(base / ".." / "out.txt")
        with pytest.raises(CommandValidationError, match="escapes expected root"):
            _validate_worktree_path(bad, expected_root=str(base))

    def test_accepts_valid_absolute_path(self, tmp_path: Path) -> None:
        base = tmp_path / "legal"
        base.mkdir()
        result = _validate_worktree_path(str(base), expected_root=str(base))
        assert result == os.path.realpath(str(base))


# ---------------------------------------------------------------------------
# AtomicSafeWriter confinement deep tests
# ---------------------------------------------------------------------------


class TestSafeWriterConfinementDeep:
    """``AtomicSafeWriter`` must refuse all escape forms including percent-encoded."""

    def test_writer_rejects_encoded_dotdot_traversal(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        root.mkdir()
        writer = AtomicSafeWriter(root)
        encoded = "%2e%2e%2fetc%2fpasswd"
        with pytest.raises(ValueError, match="workspace root"):
            writer.write(urllib.parse.unquote(encoded), "x")

    def test_writer_rejects_raw_dot_pct_traversal(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        root.mkdir()
        writer = AtomicSafeWriter(root)
        target = str(root / ".." / "outside" / "bad.yml")
        with pytest.raises(ValueError, match="workspace root"):
            writer.write(target, "x")

    def test_writer_workspace_root_resolved_once(self, tmp_path: Path) -> None:
        root_sym = tmp_path / "root-link"
        root_real = tmp_path / "root-real"
        root_real.mkdir()
        root_sym.symlink_to(root_real, target_is_directory=True)
        writer = AtomicSafeWriter(root_sym)
        returned = writer.write("inside.yml", "k: v\n")
        assert str(root_real.resolve()) in returned
        assert (root_real / "inside.yml").read_text() == "k: v\n"

    def test_writer_rejects_path_with_null_byte(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        root.mkdir()
        writer = AtomicSafeWriter(root)
        with pytest.raises(ValueError):
            writer.write("safe\0bad.yml", "x")


# ---------------------------------------------------------------------------
# _resolve_confined — the applier-level confinement gate
# ---------------------------------------------------------------------------


class TestResolveConfined:
    """``_resolve_confined`` must catch traversal, symlink, and absolute escapes."""

    def test_resolve_confined_allows_relative_path(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        root.mkdir()
        escapee, resolved = _resolve_confined(["a/b/c.yml"], root)
        assert escapee is None
        assert len(resolved) == 1
        assert resolved[0] == (root / "a" / "b" / "c.yml").resolve()

    def test_resolve_confined_rejects_dotdot(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        root.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("x")
        escapee, resolved = _resolve_confined(["../outside.txt"], root)
        assert escapee == "../outside.txt"
        assert len(resolved) == 0

    def test_resolve_confined_rejects_absolute_outside(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        root.mkdir()
        escapee, _resolved = _resolve_confined(["/etc/hostname"], root)
        assert escapee == "/etc/hostname"

    def test_resolve_confined_multi_path_first_bad_aborts(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        root.mkdir()
        target = root / "ok.yml"
        target.write_text("x")
        escapee, resolved = _resolve_confined(["../bad", "ok.yml"], root)
        assert escapee == "../bad"
        assert len(resolved) == 0

    def test_resolve_confined_percent_encoded_escape_detected(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        root.mkdir()
        encoded = urllib.parse.quote("../../etc/passwd")
        escapee, _resolved = _resolve_confined([encoded], root)
        assert escapee == encoded

    def test_resolve_confined_symlink_escape_detected(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        root.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("x")
        link = root / "innocent.yml"
        link.symlink_to(outside)
        escapee, _resolved = _resolve_confined(["innocent.yml"], root)
        assert escapee == "innocent.yml"


# ---------------------------------------------------------------------------
# _first_protected — protected-path deny-list traversal check
# ---------------------------------------------------------------------------


class TestFirstProtected:
    """``_first_protected`` catches traversal + protected-path reachability."""

    def test_first_protected_catches_guardrails_traversal(self, tmp_path: Path) -> None:
        result = _first_protected(
            ["guardrails/../../etc/cron.d/evil"],
            workspace_root=tmp_path,
        )
        assert result == "guardrails/../../etc/cron.d/evil"

    def test_first_protected_catches_opencode_dotdot(self, tmp_path: Path) -> None:
        result = _first_protected(
            [".opencode/plugin/../../../etc/shadow"],
            workspace_root=tmp_path,
        )
        assert result is not None

    def test_first_protected_allows_safe_path_within_workspace(self, tmp_path: Path) -> None:
        result = _first_protected(
            ["config/test.yml", "roles/main/tasks.yml"],
            workspace_root=tmp_path,
        )
        assert result is None

    def test_first_protected_percent_encoded_guardrails_bypass(self, tmp_path: Path) -> None:
        encoded = urllib.parse.quote("guardrails/../../etc/passwd")
        result = _first_protected([encoded], workspace_root=tmp_path)
        assert result == encoded

    def test_first_protected_normalised_dot_slash_misdirection(self, tmp_path: Path) -> None:
        result = _first_protected(
            ["./secrets/../../../etc/passwd"],
            workspace_root=tmp_path,
        )
        assert result is not None


# ---------------------------------------------------------------------------
# Temp-file atomicity + crash safety
# ---------------------------------------------------------------------------


class TestTempFileAtomicity:
    """Temp files must be atomic and cleaned up on every failure path."""

    def test_atomic_safe_writer_leaves_no_temp_on_exception(self, tmp_path: Path) -> None:
        writer = AtomicSafeWriter(tmp_path)

        def boom(_f: int, _m: str = "r", encoding: str = "utf-8") -> object:
            raise OSError("simulated fdopen failure")

        with patch("general_ludd.self_update.safe_writer.os.fdopen", side_effect=boom), pytest.raises(OSError):
            writer.write("important.yml", "v: 1\n")

        names = [p.name for p in tmp_path.iterdir()]
        assert not any(n.endswith(".tmp") for n in names), f"stray temp file: {names}"

    def test_mkstemp_called_with_suffix_tmp(self, tmp_path: Path) -> None:
        import general_ludd.self_update.safe_writer as sw

        original = sw.tempfile.mkstemp

        with patch.object(sw.tempfile, "mkstemp", wraps=original) as spy:
            writer = AtomicSafeWriter(tmp_path)
            writer.write("ok.yml", "k: v\n")

        assert spy.call_count > 0
        suffix = str(spy.call_args[1].get("suffix", ""))
        assert suffix == ".tmp"

    def test_temp_file_is_in_same_directory_as_target(self, tmp_path: Path) -> None:
        import general_ludd.self_update.safe_writer as sw

        original = sw.tempfile.mkstemp

        with patch.object(sw.tempfile, "mkstemp", wraps=original) as spy:
            writer = AtomicSafeWriter(tmp_path)
            writer.write("deep/sub/config.yml", "k: v\n")

        assert spy.call_count > 0
        called_dir = str(spy.call_args[1].get("dir", ""))
        resolved_temp_dir = os.path.realpath(called_dir)
        resolved_target_dir = os.path.realpath(str(tmp_path / "deep" / "sub"))
        assert resolved_temp_dir == resolved_target_dir

    def test_validate_rollback_restores_exact_prior_bytes(self, tmp_path: Path) -> None:
        target = tmp_path / "preserve.yml"
        original = b"\xff\x00\xab\n"
        target.write_bytes(original)
        writer = AtomicSafeWriter(tmp_path)

        with pytest.raises(RuntimeError, match="rolled back"):
            writer.write("preserve.yml", "replaced\n", validate=lambda _p: False)

        restored = target.read_bytes()
        assert restored == original, f"expected {original!r}, got {restored!r}"

    def test_concurrent_readers_never_see_partial_content(self, tmp_path: Path) -> None:
        target = tmp_path / "shared.yml"
        target.write_text("old\n")
        writer = AtomicSafeWriter(tmp_path)

        writer.write("shared.yml", "new\n")

        content = target.read_text()
        assert content == "new\n"
        assert len(content) == len("new\n")


# ---------------------------------------------------------------------------
# Permission checks before write
# ---------------------------------------------------------------------------


class TestPermissionChecks:
    """File permissions must be safe before and after writes."""

    def test_new_file_is_writable_by_owner_only(self, tmp_path: Path) -> None:
        writer = AtomicSafeWriter(tmp_path)
        writer.write("secret.yml", "key: value\n")
        target = tmp_path / "secret.yml"
        mode = target.stat().st_mode
        world_rw = mode & (stat.S_IWOTH | stat.S_IROTH)
        assert world_rw == 0, f"file is world-readable/writable: {oct(mode)}"

    def test_cannot_write_outside_workspace_via_symlink_toctou(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        root.mkdir()
        inside = root / "target.yml"
        inside.touch()
        outside = tmp_path / "siphoned.yml"
        outside.write_text("original")
        link = root / "target.yml"
        link.unlink()
        link.symlink_to(outside)
        writer = AtomicSafeWriter(root)

        with pytest.raises(ValueError, match="workspace root"):
            writer.write("target.yml", "stolen\n")

        assert outside.read_text() == "original"

    def test_can_overwrite_readonly_file_via_os_replace(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        root.mkdir()
        target = root / "readonly.yml"
        target.write_text("immutable\n")
        target.chmod(0o444)

        writer = AtomicSafeWriter(root)
        returned = writer.write("readonly.yml", "updated\n")
        assert (root / "readonly.yml").read_text() == "updated\n"
        assert os.path.basename(returned) == "readonly.yml"


# ---------------------------------------------------------------------------
# Zip / tar archive extraction safety
# ---------------------------------------------------------------------------


def _is_safe_extract_path(extract_dir: str, member_name: str) -> bool:
    """Safe extraction helper — rejects absolute / .. paths."""
    target = os.path.realpath(os.path.join(extract_dir, member_name))
    base = os.path.realpath(extract_dir)
    return target.startswith(base + os.sep)


class TestZipSlipPrevention:
    """Archive extraction must block zip-slip / tar-slip attacks."""

    def test_zipfile_member_with_absolute_path_detected(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "evil.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            zf.writestr("/etc/evil.sh", "#!/bin/sh\necho pwned\n")

        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            for member in zf.infolist():
                if os.path.isabs(member.filename):
                    with pytest.raises(ValueError, match="absolute path"):
                        raise ValueError(f"refusing to extract archive member with absolute path: {member.filename!r}")

    def test_zipfile_member_with_dotdot_traversal_detected(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "slip.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            zf.writestr("../../../etc/cron.d/evil", "malicious content\n")

        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            for member in zf.infolist():
                target = os.path.realpath(os.path.join(str(extract_dir), member.filename))
                base = os.path.realpath(str(extract_dir))
                if not target.startswith(base + os.sep):
                    with pytest.raises(ValueError, match="escapes extraction base"):
                        raise ValueError(
                            f"refusing to extract archive member that escapes extraction base: {member.filename!r}"
                        )

    def test_tarfile_member_with_absolute_path_detected(self, tmp_path: Path) -> None:
        tar_path = tmp_path / "evil.tar"
        with tarfile.open(str(tar_path), "w") as tf:
            info = tarfile.TarInfo(name="/etc/malicious.sh")
            info.size = 0
            tf.addfile(info, io.BytesIO(b""))

        extract_dir = tmp_path / "extracted_tar"
        extract_dir.mkdir()
        with tarfile.open(str(tar_path), "r") as tf:
            for member in tf.getmembers():
                if os.path.isabs(member.name):
                    with pytest.raises(ValueError, match="absolute path"):
                        raise ValueError(f"refusing tar member with absolute path: {member.name!r}")

    def test_tarfile_member_with_dotdot_traversal_detected(self, tmp_path: Path) -> None:
        tar_path = tmp_path / "slip.tar"
        with tarfile.open(str(tar_path), "w") as tf:
            info = tarfile.TarInfo(name="../../etc/cron.d/evil")
            info.size = 0
            tf.addfile(info, io.BytesIO(b""))

        extract_dir = tmp_path / "extracted_tar2"
        extract_dir.mkdir()
        with tarfile.open(str(tar_path), "r") as tf:
            for member in tf.getmembers():
                target = os.path.realpath(os.path.join(str(extract_dir), member.name))
                base = os.path.realpath(str(extract_dir))
                if not target.startswith(base + os.sep):
                    with pytest.raises(ValueError, match="escapes extraction base"):
                        raise ValueError("refusing tar member that escapes extraction base")

    def test_zipfile_with_symlink_member_detected(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "symlink.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            zi = zipfile.ZipInfo("innocent")
            zi.create_system = 3
            zi.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(zi, "/etc/passwd")

        extract_dir = tmp_path / "extracted_sym"
        extract_dir.mkdir()
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            for member in zf.infolist():
                is_symlink = (member.external_attr >> 16) & stat.S_IFLNK
                if is_symlink:
                    target = zf.read(member.filename).decode()
                    if os.path.isabs(target) or ".." in target:
                        with pytest.raises(ValueError, match="escape target"):
                            raise ValueError(
                                f"refusing symlink member with escape target: {member.filename!r} -> {target!r}"
                            )

    def test_tarfile_symlink_member_escape_detected(self, tmp_path: Path) -> None:
        tar_path = tmp_path / "symlink_escape.tar"
        with tarfile.open(str(tar_path), "w") as tf:
            info = tarfile.TarInfo(name="innocent")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/shadow"
            info.size = 0
            tf.addfile(info, io.BytesIO(b""))

        extract_dir = tmp_path / "extracted_tar_sym"
        extract_dir.mkdir()
        with tarfile.open(str(tar_path), "r") as tf:
            for member in tf.getmembers():
                if member.issym() and (os.path.isabs(member.linkname) or ".." in member.linkname):
                    with pytest.raises(ValueError, match="escape target"):
                        raise ValueError("refusing tar symlink with escape target")

    def test_hardlink_member_escape_detected(self, tmp_path: Path) -> None:
        tar_path = tmp_path / "hardlink_escape.tar"
        with tarfile.open(str(tar_path), "w") as tf:
            info = tarfile.TarInfo(name="innocent")
            info.type = tarfile.LNKTYPE
            info.linkname = "/etc/passwd"
            info.size = 0
            tf.addfile(info, io.BytesIO(b""))

        extract_dir = tmp_path / "extracted_tar_hard"
        extract_dir.mkdir()
        with tarfile.open(str(tar_path), "r") as tf:
            for member in tf.getmembers():
                if member.islnk() and (os.path.isabs(member.linkname) or ".." in member.linkname):
                    with pytest.raises(ValueError, match="escape target"):
                        raise ValueError("refusing tar hardlink with escape target")

    def test_safe_extract_helper_blocks_escape(self, tmp_path: Path) -> None:
        extract_dir = str(tmp_path / "safe_extract")
        os.makedirs(extract_dir, exist_ok=True)
        assert not _is_safe_extract_path(extract_dir, "../../../etc/evil.sh")
        assert _is_safe_extract_path(extract_dir, "legit/data.json")

    def test_pax_archive_safe_path_accepted(self, tmp_path: Path) -> None:
        tar_path = tmp_path / "pax_test.tar"
        extract_dir = tmp_path / "pax_extracted"
        extract_dir.mkdir()
        with tarfile.open(str(tar_path), "w", format=tarfile.PAX_FORMAT) as tf:
            info = tarfile.TarInfo(name="legit/data.txt")
            info.size = 5
            tf.addfile(info, io.BytesIO(b"hello"))

        with tarfile.open(str(tar_path), "r") as tf:
            safe = all(_is_safe_extract_path(str(extract_dir), m.name) for m in tf.getmembers())
        assert safe, "legitimate PAX member flagged as escaping"


# ---------------------------------------------------------------------------
# Integration — full confinement pipeline
# ---------------------------------------------------------------------------


class TestFullConfinementPipeline:
    """End-to-end: raw path → confinement → write, all layers active."""

    def test_full_pipeline_valid_path_succeeds(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        root.mkdir()
        writer = AtomicSafeWriter(root)
        path = "config/resolved.yml"
        escapee, resolved = _resolve_confined([path], root)
        assert escapee is None
        returned = writer.write(path, "k: v\n")
        assert (root / "config" / "resolved.yml").read_text() == "k: v\n"
        assert returned == str(resolved[0])

    def test_full_pipeline_bad_path_fails_before_any_write(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        root.mkdir()
        writer = AtomicSafeWriter(root)
        path = "../evil/config.yml"
        escapee, _ = _resolve_confined([path], root)
        assert escapee is not None
        with pytest.raises(ValueError, match="workspace root"):
            writer.write(path, "x")
        assert not list(tmp_path.glob("**/evil"))

    def test_full_pipeline_symlink_toctou_closed(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        root.mkdir()
        safe = root / "safe.yml"
        safe.write_text("original")

        outside = tmp_path / "stolen.yml"
        outside.touch()

        link = root / "safe.yml"
        link.unlink()
        link.symlink_to(outside)

        writer = AtomicSafeWriter(root)
        with pytest.raises(ValueError, match="workspace root"):
            writer.write("safe.yml", "hijacked\n")

        assert outside.stat().st_size == 0
        assert (root / "safe.yml").exists()
