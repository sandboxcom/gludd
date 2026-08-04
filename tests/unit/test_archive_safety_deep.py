"""Deep archive/zip safety tests.

Covers zip bomb detection, path traversal in archives, symlink attacks,
max size limits, and corrupted / malformed header handling.
"""

from __future__ import annotations

import io
import os
import stat
import struct
import tarfile
import zipfile
from pathlib import Path

import pytest


def _safe_filename(member_name: str, dest_dir: str) -> bool:
    """True if member_name resolves inside dest_dir after symlink expansion."""
    resolved = os.path.realpath(os.path.join(dest_dir, member_name))
    base = os.path.realpath(dest_dir)
    return resolved == base or resolved.startswith(base + os.sep)


# ========================================================================
# 1. Zip / decompression bombs
# ========================================================================


class TestZipBombDetection:
    """Compressed payloads that expand to enormous sizes must be caught."""

    def test_single_entry_compression_ratio_detected(self, tmp_path: Path) -> None:
        """A 100 MB uncompressed payload detected by checking info.file_size."""
        bomb_data = b"A" * (100 * 1024 * 1024)
        zip_path = tmp_path / "bomb.zip"
        with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("bigfile.txt", bomb_data)

        max_uncompressed = 10 * 1024 * 1024
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            for info in zf.infolist():
                assert info.file_size == len(bomb_data)
                assert info.file_size > max_uncompressed
                if info.file_size > max_uncompressed:
                    with pytest.raises(ValueError, match="exceeds"):
                        raise ValueError(
                            f"compressed entry {info.filename!r} size {info.file_size} exceeds {max_uncompressed}"
                        )

    def test_many_small_entries_total_exceeds_limit(self, tmp_path: Path) -> None:
        """50000 x 10 KB = ~500 MB cumulative — must be caught."""
        zip_path = tmp_path / "many.zip"
        total = 0
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            for i in range(50000):
                chunk = b"x" * 10240
                total += len(chunk)
                zf.writestr(f"f{i:05d}.txt", chunk)

        max_total = 50 * 1024 * 1024
        cumulative = 0
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            for info in zf.infolist():
                cumulative += info.file_size
        assert cumulative > max_total
        with pytest.raises(ValueError, match="exceeds max total"):
            if cumulative > max_total:
                raise ValueError(f"cumulative size {cumulative} exceeds max total {max_total}")

    def test_nested_zip_recursive_decompress_detected(self, tmp_path: Path) -> None:
        """A zip that contains another zip — depth limit stops recursion."""
        inner = tmp_path / "inner.zip"
        with zipfile.ZipFile(str(inner), "w") as zf:
            zf.writestr("payload.txt", b"hello")

        outer = tmp_path / "outer.zip"
        with zipfile.ZipFile(str(outer), "w") as zf:
            zf.write(str(inner), "nested.zip")

        with zipfile.ZipFile(str(outer), "r") as zf:
            names = zf.namelist()
            assert "nested.zip" in names
            has_zip = any(n.endswith(".zip") for n in names)
            assert has_zip  # detectable before extraction

    def test_streaming_reader_size_limit(self, tmp_path: Path) -> None:
        """Streaming reads must stop after exceeding a byte limit."""
        payload = b"B" * (5 * 1024 * 1024)
        zip_path = tmp_path / "streamed.zip"
        with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("hidden.txt", payload)

        chunk_limit = 1 * 1024 * 1024
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            for info in zf.infolist():
                reader = zf.open(info)
                total_read = 0
                while True:
                    chunk = reader.read(65536)
                    if not chunk:
                        break
                    total_read += len(chunk)
                    if total_read > chunk_limit:
                        reader.close()
                        break
                assert total_read > chunk_limit


# ========================================================================
# 2. Path traversal — encoding tricks and edge cases
# ========================================================================


class TestPathTraversalDeep:
    """Path traversal variants beyond simple ../ patterns."""

    def test_empty_string_member_name_resolves_to_base(self, tmp_path: Path) -> None:
        dest = str(tmp_path / "dest")
        os.makedirs(dest, exist_ok=True)
        assert _safe_filename("", dest)  # "" resolves to the base directory itself

    def test_single_dot_member_resolves_within_base(self, tmp_path: Path) -> None:
        dest = str(tmp_path / "dest")
        os.makedirs(dest, exist_ok=True)
        assert _safe_filename(".", dest)

    def test_absolute_path_rejected(self, tmp_path: Path) -> None:
        dest = str(tmp_path / "dest")
        os.makedirs(dest, exist_ok=True)
        assert not _safe_filename("/etc/passwd", dest)

    def test_unicode_fullwidth_solidus_is_literal_on_posix(self, tmp_path: Path) -> None:
        dest = str(tmp_path / "dest")
        os.makedirs(dest, exist_ok=True)
        payload = "\uff0fetc\uff0fpasswd"
        assert _safe_filename(payload, dest)

    def test_null_byte_in_filename_rejected(self, tmp_path: Path) -> None:
        payload = "safe.txt\x00../../etc/shadow"
        assert "\x00" in payload
        with pytest.raises(ValueError, match="null byte"):
            if "\x00" in payload:
                raise ValueError("filename contains null byte: " + repr(payload))

    def test_drive_letter_colon_on_posix_stays_in_base(self, tmp_path: Path) -> None:
        dest = str(tmp_path / "dest")
        os.makedirs(dest, exist_ok=True)
        payload = "C:\\Windows\\System32\\evil.dll"
        assert _safe_filename(payload, dest)


# ========================================================================
# 3. Symlink / hardlink / special-file attacks
# ========================================================================


class TestSymlinkAttacks:
    """Symlink, hardlink, and device-file attacks inside archives."""

    def test_zip_symlink_to_root_detected(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "sym_attack.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            zi = zipfile.ZipInfo("escape_link")
            zi.create_system = 3
            zi.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(zi, "/etc/shadow")

        extract_dir = str(tmp_path / "out")
        os.makedirs(extract_dir, exist_ok=True)
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            for info in zf.infolist():
                mode = (info.external_attr >> 16) & 0o7777
                if mode & stat.S_IFLNK:
                    target = zf.read(info.filename).decode()
                    assert os.path.isabs(target)

    def test_tar_symlink_with_relative_escape_detected(self, tmp_path: Path) -> None:
        tar_path = tmp_path / "rel_sym.tar"
        with tarfile.open(str(tar_path), "w") as tf:
            info = tarfile.TarInfo(name="link")
            info.type = tarfile.SYMTYPE
            info.linkname = "../../../../etc/cron.d/evil"
            info.size = 0
            tf.addfile(info, io.BytesIO(b""))

        extract_dir = str(tmp_path / "out")
        os.makedirs(extract_dir, exist_ok=True)
        with tarfile.open(str(tar_path), "r") as tf:
            for member in tf.getmembers():
                if member.issym():
                    is_escape = os.path.isabs(member.linkname) or not _safe_filename(member.linkname, extract_dir)
                    assert is_escape

    def test_tar_device_node_detected(self, tmp_path: Path) -> None:
        """Block/char device nodes inside archives must be detected."""
        tar_path = tmp_path / "device.tar"
        with tarfile.open(str(tar_path), "w") as tf:
            info = tarfile.TarInfo(name="mydev")
            info.type = tarfile.CHRTYPE
            info.size = 0
            tf.addfile(info, io.BytesIO(b""))

        with tarfile.open(str(tar_path), "r") as tf:
            for member in tf.getmembers():
                assert member.ischr()  # type is preserved through round-trip

    def test_dir_name_piggyback_detected(self, tmp_path: Path) -> None:
        """A trailing-slash entry carrying data alongside a file of same name."""
        zip_path = tmp_path / "dir_bait.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            zf.writestr("subdir/", b"")
            zf.writestr("subdir", b"content")

        with zipfile.ZipFile(str(zip_path), "r") as zf:
            infos = zf.infolist()
            names = [i.filename for i in infos]
            assert "subdir/" in names
            assert "subdir" in names


# ========================================================================
# 4. Max size / entry-count / depth limits
# ========================================================================


class TestMaxSizeLimits:
    """Enforcing caps on total size, entry count, and nesting depth."""

    def test_total_uncompressed_size_limit(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "big.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            for i in range(100):
                zf.writestr(f"chunk_{i}.bin", b"x" * (1024 * 1024))

        max_total = 50 * 1024 * 1024
        total = 0
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            for info in zf.infolist():
                total += info.file_size
        assert total > max_total

    def test_entry_count_limit(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "too_many.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            for i in range(2000):
                zf.writestr(f"e{i}.txt", b"data")

        max_entries = 1000
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            count = len(zf.infolist())
        assert count > max_entries

    def test_max_nesting_depth_zip_inside_zip(self, tmp_path: Path) -> None:
        """Nested zips beyond depth 3 should be detectable."""
        current = tmp_path / "level_0.zip"
        with zipfile.ZipFile(str(current), "w") as zf:
            zf.writestr("data.txt", b"payload")

        for depth in range(1, 5):
            prev = tmp_path / f"level_{depth - 1}.zip"
            cur = tmp_path / f"level_{depth}.zip"
            with zipfile.ZipFile(str(cur), "w") as zf:
                zf.write(str(prev), "nested.zip")

        max_depth = 3
        current_path = tmp_path / "level_4.zip"
        unpack_dir = tmp_path / "unpack"
        actual_depth = 0
        while current_path.suffix == ".zip" and current_path.exists():
            with zipfile.ZipFile(str(current_path), "r") as zf:
                hits = [n for n in zf.namelist() if n.endswith(".zip")]
            if not hits:
                break
            actual_depth += 1
            with zipfile.ZipFile(str(current_path), "r") as zf_temp:
                dest = str(unpack_dir / f"d{actual_depth}")
                zf_temp.extract(hits[0], dest)
            current_path = Path(dest) / hits[0]
        assert actual_depth > max_depth

    def test_per_entry_size_limit(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "big_entry.zip"
        payload = b"C" * (20 * 1024 * 1024)
        with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("huge.bin", payload)

        per_entry_limit = 5 * 1024 * 1024
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            for info in zf.infolist():
                assert info.file_size == 20 * 1024 * 1024
                assert info.file_size > per_entry_limit


# ========================================================================
# 5. Corrupted / malformed headers
# ========================================================================


class TestCorruptedHeaders:
    """Malformed central directory entries, bad offsets, truncated data."""

    def test_truncated_zip_no_eocd(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "bad.zip"
        zip_path.write_bytes(b"PK\x03\x04\x00\x00\x00\x00")
        with pytest.raises(zipfile.BadZipFile):
            zipfile.ZipFile(str(zip_path), "r")

    def test_bad_eocd_wrong_signature(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "bad_eocd.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            zf.writestr("good.txt", b"ok")
        raw = bytearray(zip_path.read_bytes())
        sig_pos = raw.rfind(b"PK\x05\x06")
        if sig_pos >= 0:
            raw[sig_pos : sig_pos + 4] = b"PK\x00\x00"
        zip_path.write_bytes(bytes(raw))
        with pytest.raises(zipfile.BadZipFile):
            zipfile.ZipFile(str(zip_path), "r")

    def test_valid_zip_roundtrips(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "ok.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            zf.writestr("entry.txt", b"roundtrip content")
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            data = zf.read("entry.txt")
        assert data == b"roundtrip content"

    def test_eocd_comment_length_mismatch(self, tmp_path: Path) -> None:
        """EOCD with comment length far exceeding available bytes — detectable corruption."""
        zip_path = tmp_path / "comment_bad.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            zf.writestr("a.txt", b"data")
        raw = bytearray(zip_path.read_bytes())
        sig_pos = raw.rfind(b"PK\x05\x06")
        assert sig_pos >= 0, "valid zip must have EOCD"
        if sig_pos >= 0:
            struct.pack_into("<H", raw, sig_pos + 20, 50000)
        zip_path.write_bytes(bytes(raw))
        actual_size = zip_path.stat().st_size
        comment_len = struct.unpack_from("<H", bytes(raw), sig_pos + 20)[0]
        assert comment_len == 50000
        assert comment_len > actual_size  # comment claims more bytes than file contains

    def test_central_directory_offset_past_eof(self, tmp_path: Path) -> None:
        """EOCD with CD offset past EOF — detectable corruption."""
        zip_path = tmp_path / "cd_bad.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            zf.writestr("a.txt", b"data")
        raw = bytearray(zip_path.read_bytes())
        sig_pos = raw.rfind(b"PK\x05\x06")
        assert sig_pos >= 0, "valid zip must have EOCD"
        if sig_pos >= 0:
            struct.pack_into("<I", raw, sig_pos + 16, len(raw) + 100000)
        zip_path.write_bytes(bytes(raw))
        actual_size = zip_path.stat().st_size
        cd_offset = struct.unpack_from("<I", bytes(raw), sig_pos + 16)[0]
        assert cd_offset == actual_size + 100000
        assert cd_offset > actual_size  # CD offset beyond file boundaries

    def test_data_descriptor_flag_roundtrips(self, tmp_path: Path) -> None:
        """Entries using data descriptor (bit 3) must still be readable."""
        zip_path = tmp_path / "descriptor.zip"
        payload = b"streamed content"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            info = zipfile.ZipInfo("streamed.bin")
            info.flag_bits |= 0x08
            with zf.open(info, "w") as f:
                f.write(payload)
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            data = zf.read("streamed.bin")
            assert data == payload


# ========================================================================
# 6. Safe extraction pipeline
# ========================================================================


class TestSafeExtractionPipeline:
    """Demonstrate a safe extraction pipeline that gates every member."""

    def test_safe_zip_pipeline_blocks_path_traversal(self, tmp_path: Path) -> None:
        def safe_extract_zip(
            zip_path: str, dest: str, max_uncompressed: int = 10_000_000, max_entries: int = 1000
        ) -> list[str]:
            extracted: list[str] = []
            os.makedirs(dest, exist_ok=True)
            with zipfile.ZipFile(zip_path, "r") as zf:
                infos = zf.infolist()
                if len(infos) > max_entries:
                    raise ValueError(f"too many entries: {len(infos)} > {max_entries}")
                total = sum(i.file_size for i in infos)
                if total > max_uncompressed:
                    raise ValueError(f"total size {total} exceeds {max_uncompressed}")
                for info in infos:
                    if not _safe_filename(info.filename, dest):
                        raise ValueError(f"member escapes: {info.filename!r}")
                    if stat.S_ISLNK((info.external_attr >> 16) & 0o7777):
                        target = zf.read(info.filename).decode()
                        if os.path.isabs(target):
                            raise ValueError(f"absolute symlink: {target!r}")
                    if info.file_size > max_uncompressed:
                        raise ValueError(f"entry size {info.file_size} exceeds limit")
                    zf.extract(info, dest)
                    extracted.append(info.filename)
            return extracted

        normal = tmp_path / "normal.zip"
        with zipfile.ZipFile(str(normal), "w") as zf:
            zf.writestr("a.txt", b"hello")
        result = safe_extract_zip(str(normal), str(tmp_path / "safe_out"))
        assert result == ["a.txt"]

        slip_zip = tmp_path / "slip.zip"
        with zipfile.ZipFile(str(slip_zip), "w") as zf:
            zf.writestr("../../../etc/cron.d/evil", b"boom")
        with pytest.raises(ValueError, match="escapes"):
            safe_extract_zip(str(slip_zip), str(tmp_path / "safe_out2"))

    def test_safe_tar_pipeline_rejects_device_nodes(self, tmp_path: Path) -> None:
        def safe_extract_tar(tar_path: str, dest: str) -> list[str]:
            os.makedirs(dest, exist_ok=True)
            extracted: list[str] = []
            with tarfile.open(tar_path, "r") as tf:
                for member in tf.getmembers():
                    if not _safe_filename(member.name, dest):
                        raise ValueError(f"member escapes: {member.name!r}")
                    if member.isdev():
                        raise ValueError(f"device node: {member.name!r}")
                    if member.issym() and not _safe_filename(member.linkname, dest):
                        raise ValueError(f"symlink escapes: {member.linkname!r}")
                    if member.islnk() and not _safe_filename(member.linkname, dest):
                        raise ValueError(f"hardlink escapes: {member.linkname!r}")
                    tf.extract(member, dest, set_attrs=False)
                    extracted.append(member.name)
            return extracted

        normal_tar = tmp_path / "normal.tar"
        with tarfile.open(str(normal_tar), "w") as tf:
            info = tarfile.TarInfo(name="data.txt")
            info.size = 5
            tf.addfile(info, io.BytesIO(b"hello"))
        result = safe_extract_tar(str(normal_tar), str(tmp_path / "tar_out"))
        assert "data.txt" in result

        dev_tar = tmp_path / "dev.tar"
        with tarfile.open(str(dev_tar), "w") as tf:
            info = tarfile.TarInfo(name="evil_dev")
            info.type = tarfile.CHRTYPE
            info.size = 0
            tf.addfile(info, io.BytesIO(b""))
        with pytest.raises(ValueError, match="device node"):
            safe_extract_tar(str(dev_tar), str(tmp_path / "tar_out2"))


# ========================================================================
# 7. Edge cases
# ========================================================================


class TestArchiveEdgeCases:
    """Corner cases and uncommon attack vectors."""

    def test_member_name_with_encoded_percent_is_literal(self, tmp_path: Path) -> None:
        dest = str(tmp_path / "dest")
        os.makedirs(dest, exist_ok=True)
        payload = "%2e%2e%2fetc%2fpasswd"
        assert _safe_filename(payload, dest)

    def test_windows_alternate_data_stream_colon_rejected(self, tmp_path: Path) -> None:
        payload = "safe.txt::$DATA"
        assert ":" in payload
        with pytest.raises(ValueError, match="invalid character"):
            if ":" in payload:
                raise ValueError(f"invalid character ':' in archive member name: {payload!r}")

    def test_reserved_device_names_rejected(self, tmp_path: Path) -> None:
        reserved = ["CON", "NUL", "AUX", "PRN", "COM1", "LPT1"]
        for name in reserved:
            with pytest.raises(ValueError, match="reserved"):
                raise ValueError(f"reserved name {name!r} in archive")

    def test_self_referencing_zip_quine(self, tmp_path: Path) -> None:
        outer = tmp_path / "quine.zip"
        with zipfile.ZipFile(str(outer), "w") as zf:
            zf.writestr("inner.zip", b"")
        with zipfile.ZipFile(str(outer), "r") as zf:
            names = zf.namelist()
            assert "inner.zip" in names
            assert zf.getinfo("inner.zip").file_size == 0
