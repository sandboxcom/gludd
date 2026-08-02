"""Tests for entropy_analyzer module — Shannon entropy, heatmap, packing/encrypted detection."""

from __future__ import annotations

import random
import struct

import pytest
from plugins.module_utils.entropy_analyzer import (
    ENCRYPTED_ENTROPY_THRESHOLD,
    HEATMAP_DEFAULT_BLOCK_SIZE,
    PACKING_ENTROPY_THRESHOLD,
    EntropyAnalysisResult,
    EntropyHeatmap,
    SectionEntropy,
    analyze_entropy,
    build_entropy_heatmap,
    compute_section_entropies,
    detect_encrypted_sections,
    detect_packing,
    shannon_entropy,
)


class TestShannonEntropy:
    def test_empty_bytes_is_zero(self):
        assert shannon_entropy(b"") == 0.0

    def test_single_byte_is_zero(self):
        assert shannon_entropy(b"\x00") == 0.0

    def test_all_same_byte_is_zero(self):
        assert shannon_entropy(b"\x00" * 1024) == 0.0

    def test_uniform_distribution_is_max(self):
        # Every byte value once -> entropy = 8.0 bits
        data = bytes(range(256))
        e = shannon_entropy(data)
        assert e == pytest.approx(8.0, abs=1e-9)

    def test_two_values_is_one_bit(self):
        # Half 0x00, half 0xFF -> entropy = 1.0 bit
        data = b"\x00" * 128 + b"\xff" * 128
        assert shannon_entropy(data) == pytest.approx(1.0, abs=1e-9)

    def test_high_entropy_for_random(self):
        rng = random.Random(42)
        data = bytes(rng.randrange(256) for _ in range(4096))
        assert shannon_entropy(data) > 7.8

    def test_returns_float(self):
        assert isinstance(shannon_entropy(b"hello world"), float)

    def test_small_text_data_low_entropy(self):
        # ASCII text in narrow range
        text = b"the quick brown fox jumps over the lazy dog " * 10
        assert shannon_entropy(text) < 5.0


class TestConstants:
    def test_packing_threshold_in_expected_range(self):
        assert 6.5 <= PACKING_ENTROPY_THRESHOLD <= 7.5

    def test_encrypted_threshold_higher_than_packing(self):
        assert ENCRYPTED_ENTROPY_THRESHOLD > PACKING_ENTROPY_THRESHOLD

    def test_encrypted_threshold_at_or_below_eight(self):
        assert ENCRYPTED_ENTROPY_THRESHOLD <= 8.0

    def test_heatmap_block_size_positive_power_of_two(self):
        assert HEATMAP_DEFAULT_BLOCK_SIZE > 0
        assert (HEATMAP_DEFAULT_BLOCK_SIZE & (HEATMAP_DEFAULT_BLOCK_SIZE - 1)) == 0


class TestSectionEntropy:
    def test_creation(self):
        s = SectionEntropy(name=".text", offset=0x1000, size=0x200, entropy=6.2, is_high_entropy=False)
        assert s.name == ".text"
        assert s.offset == 0x1000
        assert s.size == 0x200
        assert s.entropy == 6.2
        assert s.is_high_entropy is False

    def test_high_entropy_flag(self):
        s = SectionEntropy(name=".packed", offset=0, size=100, entropy=7.9, is_high_entropy=True)
        assert s.is_high_entropy is True


class TestEntropyHeatmap:
    def test_creation(self):
        buckets = [(0, 4.0), (256, 7.8), (512, 4.2)]
        hm = EntropyHeatmap(buckets=buckets, block_size=256, total_size=768)
        assert hm.buckets == buckets
        assert hm.block_size == 256
        assert hm.total_size == 768

    def test_no_buckets_for_empty(self):
        hm = build_entropy_heatmap(b"", block_size=256)
        assert hm.buckets == []
        assert hm.total_size == 0

    def test_single_block(self):
        hm = build_entropy_heatmap(b"\x00" * 100, block_size=256)
        assert len(hm.buckets) == 1
        assert hm.buckets[0][0] == 0
        assert hm.buckets[0][1] == pytest.approx(0.0, abs=1e-9)
        assert hm.total_size == 100

    def test_multiple_blocks_aligned(self):
        data = b"\x00" * 256 + bytes(range(256)) * 2
        hm = build_entropy_heatmap(data, block_size=256)
        assert len(hm.buckets) == 3
        assert hm.buckets[0][0] == 0
        assert hm.buckets[1][0] == 256
        assert hm.buckets[2][0] == 512
        # First block all-zero -> entropy 0
        assert hm.buckets[0][1] == pytest.approx(0.0, abs=1e-9)
        # Middle blocks uniform -> entropy ~8
        assert hm.buckets[1][1] == pytest.approx(8.0, abs=1e-9)

    def test_offsets_are_block_aligned(self):
        hm = build_entropy_heatmap(b"\x00" * 1000, block_size=128)
        for i, (offset, _) in enumerate(hm.buckets):
            assert offset == i * 128

    def test_custom_block_size(self):
        hm = build_entropy_heatmap(b"\x00" * 64, block_size=32)
        assert hm.block_size == 32
        assert len(hm.buckets) == 2

    def test_total_size_matches(self):
        data = b"\x00" * 333
        hm = build_entropy_heatmap(data, block_size=64)
        assert hm.total_size == 333


class TestComputeSectionEntropies:
    def test_empty_bytes_no_sections(self):
        sections = compute_section_entropies(b"")
        assert sections == []

    def test_random_bytes_no_magic_no_sections(self):
        sections = compute_section_entropies(b"\x00" * 1000)
        assert sections == []

    def test_pe_with_one_section(self):
        header = bytearray(0x200)
        header[0] = ord("M")
        header[1] = ord("Z")
        e_lfanew = 0x80
        struct.pack_into("<I", header, 0x3c, e_lfanew)
        header[e_lfanew : e_lfanew + 4] = b"PE\x00\x00"
        coff = e_lfanew + 4
        struct.pack_into("<H", header, coff + 2, 1)  # num_sections = 1
        opt_hdr = coff + 20
        struct.pack_into("<H", header, opt_hdr, 0x20b)  # PE32+ magic
        sec_table = opt_hdr + 112
        name = b".text\x00\x00\x00"
        header[sec_table : sec_table + 8] = name
        struct.pack_into("<I", header, sec_table + 8, 0x200)    # virtual size
        struct.pack_into("<I", header, sec_table + 16, 0x200)   # raw size
        struct.pack_into("<I", header, sec_table + 20, 0x200)   # raw offset (right after header)
        sec_data = bytes(range(256)) * 2  # uniform -> high entropy
        full = bytes(header) + sec_data
        sections = compute_section_entropies(full)
        assert len(sections) == 1
        assert sections[0].name == ".text"
        assert sections[0].entropy == pytest.approx(8.0, abs=1e-9)
        assert sections[0].is_high_entropy is True
        assert sections[0].offset == 0x200
        assert sections[0].size == 0x200

    def test_pe_low_entropy_section_flagged_false(self):
        header = bytearray(0x200)
        header[0] = ord("M")
        header[1] = ord("Z")
        e_lfanew = 0x80
        struct.pack_into("<I", header, 0x3c, e_lfanew)
        header[e_lfanew : e_lfanew + 4] = b"PE\x00\x00"
        coff = e_lfanew + 4
        struct.pack_into("<H", header, coff + 2, 1)
        opt_hdr = coff + 20
        struct.pack_into("<H", header, opt_hdr, 0x20b)
        sec_table = opt_hdr + 112
        header[sec_table : sec_table + 8] = b".data\x00\x00\x00"
        struct.pack_into("<I", header, sec_table + 8, 0x200)
        struct.pack_into("<I", header, sec_table + 16, 0x200)
        struct.pack_into("<I", header, sec_table + 20, 0x200)
        sec_data = b"AAAA" * 128  # very low entropy
        full = bytes(header) + sec_data
        sections = compute_section_entropies(full)
        assert len(sections) == 1
        assert sections[0].is_high_entropy is False


class TestDetectPacking:
    def test_no_sections_not_packed(self):
        packed, conf, evidence = detect_packing([])
        assert packed is False
        assert evidence == []

    def test_low_entropy_sections_not_packed(self):
        sections = [
            SectionEntropy(name=".text", offset=0, size=100, entropy=5.5, is_high_entropy=False),
            SectionEntropy(name=".data", offset=100, size=50, entropy=4.0, is_high_entropy=False),
        ]
        packed, conf, evidence = detect_packing(sections)
        assert packed is False

    def test_single_high_entropy_section_packed(self):
        sections = [
            SectionEntropy(name=".text", offset=0, size=1000, entropy=7.5, is_high_entropy=True),
        ]
        packed, conf, evidence = detect_packing(sections)
        assert packed is True
        assert any("7.5" in e for e in evidence)

    def test_multiple_high_entropy_sections_higher_confidence(self):
        sections = [
            SectionEntropy(name=".text", offset=0, size=1000, entropy=7.6, is_high_entropy=True),
            SectionEntropy(name=".rdata", offset=1000, size=800, entropy=7.8, is_high_entropy=True),
            SectionEntropy(name=".data", offset=2000, size=500, entropy=7.4, is_high_entropy=True),
        ]
        packed, conf, evidence = detect_packing(sections)
        assert packed is True


class TestDetectEncryptedSections:
    def test_empty_returns_empty(self):
        assert detect_encrypted_sections([], None) == []

    def test_no_high_entropy_returns_empty(self):
        sections = [
            SectionEntropy(name=".text", offset=0, size=100, entropy=5.5, is_high_entropy=False),
        ]
        assert detect_encrypted_sections(sections, None) == []

    def test_section_above_encrypted_threshold_flagged(self):
        sections = [
            SectionEntropy(name=".text", offset=0, size=4096, entropy=7.95, is_high_entropy=True),
        ]
        encrypted = detect_encrypted_sections(sections, None)
        assert len(encrypted) == 1
        assert encrypted[0].name == ".text"

    def test_section_between_thresholds_not_flagged(self):
        sections = [
            SectionEntropy(name=".text", offset=0, size=4096, entropy=7.2, is_high_entropy=True),
        ]
        # 7.2 > PACKING but < ENCRYPTED threshold
        encrypted = detect_encrypted_sections(sections, None)
        assert encrypted == []

    def test_uses_heatmap_uniformity(self):
        # Section above encrypted threshold AND heatmap shows uniform high entropy
        sections = [
            SectionEntropy(name=".enc", offset=0, size=512, entropy=7.95, is_high_entropy=True),
        ]
        buckets = [(0, 7.95), (256, 7.96)]
        hm = EntropyHeatmap(buckets=buckets, block_size=256, total_size=512)
        encrypted = detect_encrypted_sections(sections, hm)
        assert len(encrypted) == 1


class TestAnalyzeEntropy:
    def test_empty_bytes_returns_result(self):
        result = analyze_entropy(b"")
        assert isinstance(result, EntropyAnalysisResult)
        assert result.file_type == "unknown"
        assert result.overall_entropy == 0.0
        assert result.sections == []
        assert result.packed is False
        assert result.encrypted_sections == []

    def test_overall_entropy_computed(self):
        data = bytes(range(256)) * 4
        result = analyze_entropy(data)
        assert result.overall_entropy == pytest.approx(8.0, abs=1e-9)

    def test_file_type_detected_pe(self):
        header = bytearray(0x200)
        header[0] = ord("M")
        header[1] = ord("Z")
        struct.pack_into("<I", header, 0x3c, 0x80)
        header[0x80 : 0x84] = b"PE\x00\x00"
        struct.pack_into("<H", header, 0x84 + 2, 1)
        opt_hdr = 0x84 + 20
        struct.pack_into("<H", header, opt_hdr, 0x20b)
        sec_table = opt_hdr + 112
        header[sec_table : sec_table + 8] = b".text\x00\x00\x00"
        struct.pack_into("<I", header, sec_table + 8, 0x200)
        struct.pack_into("<I", header, sec_table + 16, 0x200)
        struct.pack_into("<I", header, sec_table + 20, 0x200)
        sec_data = bytes(range(256)) * 2
        full = bytes(header) + sec_data
        result = analyze_entropy(full)
        assert result.file_type == "PE"
        assert len(result.sections) == 1
        assert result.sections[0].entropy > 7.0

    def test_packed_pe_detected(self):
        header = bytearray(0x200)
        header[0] = ord("M")
        header[1] = ord("Z")
        struct.pack_into("<I", header, 0x3c, 0x80)
        header[0x80 : 0x84] = b"PE\x00\x00"
        struct.pack_into("<H", header, 0x84 + 2, 1)
        opt_hdr = 0x84 + 20
        struct.pack_into("<H", header, opt_hdr, 0x20b)
        sec_table = opt_hdr + 112
        header[sec_table : sec_table + 8] = b".upx0\x00\x00\x00"
        struct.pack_into("<I", header, sec_table + 8, 0x1000)
        struct.pack_into("<I", header, sec_table + 16, 0x1000)
        struct.pack_into("<I", header, sec_table + 20, 0x200)
        rng = random.Random(123)
        sec_data = bytes(rng.randrange(256) for _ in range(0x1000))
        full = bytes(header) + sec_data
        result = analyze_entropy(full)
        assert result.packed is True
        assert len(result.evidence) >= 1

    def test_clean_low_entropy_not_packed(self):
        text = b"hello world " * 200
        result = analyze_entropy(text)
        assert result.packed is False

    def test_path_input(self, tmp_path):
        path = tmp_path / "sample.bin"
        path.write_bytes(bytes(range(256)) * 16)
        result = analyze_entropy(str(path))
        assert result.overall_entropy > 7.9
        assert isinstance(result, EntropyAnalysisResult)

    def test_nonexistent_path_no_crash(self):
        result = analyze_entropy("/nonexistent/path/xyz.bin")
        assert isinstance(result, EntropyAnalysisResult)
        assert result.file_type == "unknown"

    def test_bytearray_input(self):
        result = analyze_entropy(bytearray(bytes(range(256)) * 4))
        assert result.overall_entropy > 7.9

    def test_heatmap_in_result(self):
        data = b"\x00" * 256 + bytes(range(256)) * 2
        result = analyze_entropy(data)
        assert isinstance(result.heatmap, EntropyHeatmap)
        assert len(result.heatmap.buckets) >= 1

    def test_evidence_list_is_strings(self):
        result = analyze_entropy(bytes(range(256)) * 4)
        for e in result.evidence:
            assert isinstance(e, str)

    def test_invalid_input_type_returns_result(self):
        result = analyze_entropy(42)  # type: ignore[arg-type]
        assert isinstance(result, EntropyAnalysisResult)
        assert result.file_type == "unknown"
