"""Tests for deobfuscate role — packing, CFG flattening, string deobfuscation, opaque predicates."""

from __future__ import annotations

import importlib
import struct
import sys
from pathlib import Path

import pytest

_COLLECTION_ROOT = Path(__file__).resolve().parents[3] / "collections/ansible_collections/general_ludd/binary_re"
_DEOBFUSCATE_FILES = _COLLECTION_ROOT / "roles" / "deobfuscate" / "files"
_PLUGIN_ROOT = _COLLECTION_ROOT / "plugins"

if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))
if str(_DEOBFUSCATE_FILES) not in sys.path:
    sys.path.insert(0, str(_DEOBFUSCATE_FILES))

try:
    _deob = importlib.import_module("deobfuscate")
    detect_packing = _deob.detect_packing
    detect_cfg_flattening = _deob.detect_cfg_flattening
    deobfuscate_strings = _deob.deobfuscate_strings
    detect_opaque_predicates = _deob.detect_opaque_predicates
except ModuleNotFoundError:
    pytest.skip("deobfuscate module not available", allow_module_level=True)


class TestDetectPacking:
    def test_returns_dict_structure(self, tmp_path):
        f = tmp_path / "test.bin"
        f.write_bytes(b"\x00" * 100)
        result = detect_packing(str(f))
        assert isinstance(result, dict)
        assert "packed" in result
        assert "file_type" in result
        assert "detections" in result

    def test_detects_upx_packing(self, tmp_path):
        f = tmp_path / "upx_sample.bin"
        f.write_bytes(b"\x00" * 200 + b"UPX0" + b"UPX1" + b"\x00" * 200)
        result = detect_packing(str(f))
        assert result["packed"] is True

    def test_detects_as_pack_string_marker(self, tmp_path):
        f = tmp_path / "aspack_sample.bin"
        f.write_bytes(b"\x00" * 100 + b"ASPack" + b"\x00" * 100)
        result = detect_packing(str(f))
        assert result["packed"] is True

    def test_detects_mpress_string_marker(self, tmp_path):
        f = tmp_path / "mpress_sample.bin"
        f.write_bytes(b"\x00" * 100 + b"MPRESS" + b"\x00" * 100)
        result = detect_packing(str(f))
        assert result["packed"] is True

    def test_clean_binary_not_packed(self, tmp_path):
        f = tmp_path / "clean.bin"
        f.write_bytes(b"\x00" * 2000)
        result = detect_packing(str(f))
        assert result["packed"] is False

    def test_missing_file_returns_error(self, tmp_path):
        result = detect_packing(str(tmp_path / "does_not_exist.bin"))
        assert "error" in result
        assert result["packed"] is False

    def test_pe_with_packer_section(self, tmp_path):
        header = bytearray(0x200)
        header[0] = ord("M")
        header[1] = ord("Z")
        struct.pack_into("<I", header, 0x3c, 0x80)
        header[0x80 : 0x84] = b"PE\x00\x00"
        struct.pack_into("<H", header, 0x80 + 6, 1)
        shdr = 0x80 + 4 + 20 + 96
        header[shdr : shdr + 8] = b".aspack\x00"
        struct.pack_into("<I", header, shdr + 8, 0x1000)
        struct.pack_into("<I", header, shdr + 16, 0x200)
        struct.pack_into("<I", header, shdr + 20, 0x400)
        f = tmp_path / "pe_aspack.bin"
        f.write_bytes(bytes(header) + bytes(0x200))
        result = detect_packing(str(f))
        assert result["packed"] is True
        assert result["file_type"] == "PE"

    def test_elf_identified_correctly(self, tmp_path):
        elf = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 8 + b"\x00" * 200
        f = tmp_path / "elf.bin"
        f.write_bytes(elf)
        result = detect_packing(str(f))
        assert result["file_type"] == "ELF"


class TestDetectCfgFlattening:
    def test_returns_dict_structure(self, tmp_path):
        f = tmp_path / "bin.bin"
        f.write_bytes(b"\x00" * 500)
        result = detect_cfg_flattening(str(f))
        assert isinstance(result, dict)
        assert "flattened" in result
        assert "confidence" in result
        assert "markers" in result

    def test_clean_binary_not_flattened(self, tmp_path):
        f = tmp_path / "clean.bin"
        f.write_bytes(b"\x90" * 2000)
        result = detect_cfg_flattening(str(f))
        assert result["confidence"] in ("low", "none")

    def test_missing_file_returns_error(self, tmp_path):
        result = detect_cfg_flattening("/does/not/exist.bin")
        assert "error" in result

    def test_detects_high_indirect_branch_ratio(self, tmp_path):
        data = bytearray(4096)
        for i in range(0, len(data) - 1, 2):
            if i % 4 == 0:
                data[i] = 0xff
            else:
                data[i] = 0xe9
        f = tmp_path / "flattened.bin"
        f.write_bytes(bytes(data))
        result = detect_cfg_flattening(str(f))
        assert result["confidence"] in ("high", "medium")
        assert len(result["markers"]) > 0


class TestDeobfuscateStrings:
    def test_returns_dict_structure(self, tmp_path):
        f = tmp_path / "bin.bin"
        f.write_bytes(b"Hello World\x00Test\x00\x00\x00")
        result = deobfuscate_strings(str(f))
        assert isinstance(result, dict)
        assert "encrypted_strings" in result
        assert "deobfuscated" in result
        assert "confidence" in result

    def test_extracts_printable_strings(self, tmp_path):
        f = tmp_path / "printable.bin"
        f.write_bytes(b"Hello World\x00Test String\x00More\x00")
        result = deobfuscate_strings(str(f))
        assert result["native_printable_strings"] >= 3

    def test_xor_deobfuscation_with_key_hint(self, tmp_path):
        f = tmp_path / "xor_test.bin"
        key = 0x55
        original = b"SecretPassword123"
        xored = bytes(b ^ key for b in original)
        padding = b"\x00" * 16
        f.write_bytes(padding + xored + padding)
        result = deobfuscate_strings(str(f), key_hint=key)
        assert result["deobfuscated"] > 0

    def test_missing_file_returns_error(self, tmp_path):
        result = deobfuscate_strings(str(tmp_path / "nope.bin"))
        assert "error" in result

    def test_detects_string_encryption_api(self, tmp_path):
        f = tmp_path / "crypto.bin"
        data = b"CryptDecrypt" + b"\x00" * 100 + b"RtlDecryptMemory"
        f.write_bytes(data)
        result = deobfuscate_strings(str(f))
        assert result["encrypted_strings"] > 0


class TestDetectOpaquePredicates:
    def test_returns_dict_structure(self, tmp_path):
        f = tmp_path / "bin.bin"
        f.write_bytes(b"\x00" * 500)
        result = detect_opaque_predicates(str(f))
        assert isinstance(result, dict)
        assert "detected" in result
        assert "confidence" in result
        assert "patterns" in result

    def test_clean_binary_no_predicates(self, tmp_path):
        f = tmp_path / "clean.bin"
        f.write_bytes(b"\x90" * 2000)
        result = detect_opaque_predicates(str(f))
        assert result["detected"] is False

    def test_missing_file_returns_error(self, tmp_path):
        result = detect_opaque_predicates("/does/not/exist.bin")
        assert "error" in result

    def test_detects_xor_eax_jz_pattern(self, tmp_path):
        f = tmp_path / "opaque.bin"
        data = b"\x00" * 100 + b"\x31\xc0\x74" + b"\x00" * 100
        f.write_bytes(data)
        result = detect_opaque_predicates(str(f))
        assert result["detected"] is True
        assert len(result["patterns"]) > 0

    def test_detects_cmp_eax_jz_pattern(self, tmp_path):
        f = tmp_path / "opaque2.bin"
        data = b"\x00" * 100 + b"\x39\xc0\x74" + b"\x00" * 100
        f.write_bytes(data)
        result = detect_opaque_predicates(str(f))
        assert result["detected"] is True

    def test_detects_multiple_patterns_high_confidence(self, tmp_path):
        f = tmp_path / "opaque_multi.bin"
        data = (
            b"\x31\xc0\x74" + b"\x00" * 20 +
            b"\x33\xc0\x74" + b"\x00" * 20 +
            b"\x85\xc0\x74" + b"\x00" * 20 +
            b"\x83\xf8\x00\x74"
        )
        f.write_bytes(data)
        result = detect_opaque_predicates(str(f))
        assert result["detected"] is True
        assert result["confidence"] == "high"


class TestDeobfuscateIntegration:
    def test_full_binary_with_multiple_obfuscations(self, tmp_path):
        f = tmp_path / "obfuscated.bin"
        data = (
            b"UPX0" + b"\x00" * 96 +
            b"\x00" * 200 +
            b"\xff\xe9\xff\xe9" * 500 +
            b"\x31\xc0\x74" * 10 +
            b"\x00" * 100 +
            b"__strdecrypt" +
            b"\x00" * 100
        )
        f.write_bytes(data)
        packing = detect_packing(str(f))
        cfg = detect_cfg_flattening(str(f))
        strings = deobfuscate_strings(str(f))
        opaque = detect_opaque_predicates(str(f))
        assert packing["packed"] is True
        assert cfg["confidence"] in ("high", "medium")
        assert strings["encrypted_strings"] > 0
        assert opaque["detected"] is True


class TestModuleImportability:
    def test_all_four_functions_importable(self):
        assert callable(detect_packing)
        assert callable(detect_cfg_flattening)
        assert callable(deobfuscate_strings)
        assert callable(detect_opaque_predicates)

    def test_deobfuscate_module_has_all_modes(self):
        modes = {"packing", "cfg_flattening", "strings", "opaque_predicates"}
        from deobfuscate import _MODES
        assert set(_MODES.keys()) == modes
