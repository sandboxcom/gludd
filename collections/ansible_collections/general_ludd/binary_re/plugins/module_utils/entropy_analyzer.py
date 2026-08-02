"""Entropy analysis for binary files — Shannon entropy per section, heatmap,
packing detection, and encrypted-section identification.

High-entropy sections (>7.0 bits/byte) are a strong indicator of packing
(UPX, ASPack, etc.) because compressed data is near-uniform random.
Encrypted sections typically exceed 7.5 bits/byte and show uniform high
entropy across the whole section in the heatmap (no low-entropy headers or
padding inside the region).
"""

from __future__ import annotations

import math
import struct
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from plugins.module_utils.obfuscation_techniques import (
    DetectionConfidence,
    _identify_file_type,
)

PACKING_ENTROPY_THRESHOLD: float = 7.0
ENCRYPTED_ENTROPY_THRESHOLD: float = 7.5
HEATMAP_DEFAULT_BLOCK_SIZE: int = 256

_ELF_MAGIC = b"\x7fELF"
_PE_MAGIC = b"MZ"


@dataclass
class SectionEntropy:
    """Entropy of a single parsed section of a binary."""

    name: str
    offset: int
    size: int
    entropy: float
    is_high_entropy: bool


@dataclass
class EntropyHeatmap:
    """Per-block entropy over a binary — (offset, entropy) buckets."""

    buckets: list[tuple[int, float]]
    block_size: int
    total_size: int


@dataclass
class EntropyAnalysisResult:
    """Full entropy analysis output for a binary."""

    file_type: str
    overall_entropy: float
    sections: list[SectionEntropy] = field(default_factory=list)
    heatmap: EntropyHeatmap = field(
        default_factory=lambda: EntropyHeatmap(buckets=[], block_size=0, total_size=0)
    )
    packed: bool = False
    packing_confidence: DetectionConfidence = DetectionConfidence.LOW
    encrypted_sections: list[SectionEntropy] = field(default_factory=list)
    suspicious_sections: list[SectionEntropy] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


def shannon_entropy(data: bytes) -> float:
    """Shannon entropy in bits per byte (0.0 .. 8.0)."""
    if not data:
        return 0.0
    counts = [0] * 256
    for b in data:
        counts[b] += 1
    n = len(data)
    entropy = 0.0
    for c in counts:
        if c > 0:
            p = c / n
            entropy -= p * math.log2(p)
    return entropy


def _parse_pe_sections(data: bytes) -> list[tuple[str, int, int, bytes]]:
    """Parse PE section table → (name, offset, size, data)."""
    sections: list[tuple[str, int, int, bytes]] = []
    try:
        if data[:2] != _PE_MAGIC:
            return sections
        pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
        if pe_offset <= 0 or pe_offset + 4 > len(data):
            return sections
        if data[pe_offset : pe_offset + 4] != b"PE\x00\x00":
            return sections
        coff = pe_offset + 4
        if coff + 20 > len(data):
            return sections
        num_sections = struct.unpack_from("<H", data, coff + 2)[0]
        opt_hdr = coff + 20
        if opt_hdr + 2 > len(data):
            return sections
        magic = struct.unpack_from("<H", data, opt_hdr)[0]
        section_table = opt_hdr + (112 if magic == 0x20B else 96)
        for i in range(num_sections):
            hdr = section_table + (i * 40)
            if hdr + 40 > len(data):
                break
            name = data[hdr : hdr + 8].rstrip(b"\x00").decode("ascii", errors="ignore")
            vsize = struct.unpack_from("<I", data, hdr + 8)[0]
            raw_size = struct.unpack_from("<I", data, hdr + 16)[0]
            raw_offset = struct.unpack_from("<I", data, hdr + 20)[0]
            read_size = min(raw_size, vsize) if vsize else raw_size
            if raw_offset > 0 and raw_offset + read_size <= len(data) and read_size > 0:
                sections.append((name, raw_offset, read_size, data[raw_offset : raw_offset + read_size]))
    except (struct.error, IndexError, ValueError):
        pass
    return sections


def _parse_elf_sections(data: bytes) -> list[tuple[str, int, int, bytes]]:
    """Parse ELF section headers → (name, offset, size, data)."""
    sections: list[tuple[str, int, int, bytes]] = []
    try:
        if data[:4] != _ELF_MAGIC:
            return sections
        is_64 = data[4] == 2
        if data[5] == 0:
            return sections
        if is_64:
            if len(data) < 64:
                return sections
            (
                _e_type, _e_machine, _e_version, _e_entry, _e_phoff,
                shoff, _e_flags, _e_ehsize, _e_phentsize, _e_phnum,
                shentsize, shnum, shstrndx,
            ) = struct.unpack_from("<HHIQQQIHHHHHH", data, 16)
        else:
            if len(data) < 52:
                return sections
            (
                _e_type, _e_machine, _e_version, _e_entry, _e_phoff,
                shoff, _e_flags, _e_ehsize, _e_phentsize, _e_phnum,
                shentsize, shnum, shstrndx,
            ) = struct.unpack_from("<HHIIIIIHHHHH", data, 16)
        if shoff <= 0 or shnum == 0:
            return sections
        strtab_hdr = shoff + (shstrndx * shentsize)
        if strtab_hdr + shentsize > len(data):
            return sections
        if is_64:
            _n, strtab_type, _f, _a, strtab_off, strtab_size = struct.unpack_from("<IIQQQQ", data, strtab_hdr)
        else:
            _n, strtab_type, _f, _a, strtab_off, strtab_size = struct.unpack_from("<IIIII", data, strtab_hdr)
        if strtab_type != 3 or strtab_off + strtab_size > len(data):
            return sections
        strtab = data[strtab_off : strtab_off + strtab_size]
        for i in range(shnum):
            sec_hdr = shoff + (i * shentsize)
            if sec_hdr + shentsize > len(data):
                break
            if is_64:
                name_idx, sec_type, _flags, _addr, sec_off, sec_size = struct.unpack_from("<IIQQQQ", data, sec_hdr)
            else:
                name_idx, sec_type, _flags, _addr, sec_off, sec_size = struct.unpack_from("<IIIII", data, sec_hdr)
            if sec_type == 8:  # SHT_NOBITS (.bss) — no file bytes
                continue
            end = strtab.find(b"\x00", name_idx)
            name = strtab[name_idx:end].decode("ascii", errors="ignore") if end != -1 else ""
            if sec_off + sec_size > len(data) or sec_size == 0:
                continue
            sections.append((name, sec_off, sec_size, data[sec_off : sec_off + sec_size]))
    except (struct.error, IndexError, ValueError):
        pass
    return sections


def compute_section_entropies(data: bytes) -> list[SectionEntropy]:
    """Parse a binary and compute per-section Shannon entropy."""
    file_type = _identify_file_type(data)
    if file_type == "PE":
        parsed = _parse_pe_sections(data)
    elif file_type == "ELF":
        parsed = _parse_elf_sections(data)
    else:
        return []
    results: list[SectionEntropy] = []
    for name, offset, size, sec_data in parsed:
        e = shannon_entropy(sec_data)
        results.append(
            SectionEntropy(
                name=name,
                offset=offset,
                size=size,
                entropy=e,
                is_high_entropy=e > PACKING_ENTROPY_THRESHOLD,
            )
        )
    return results


def build_entropy_heatmap(data: bytes, block_size: int = HEATMAP_DEFAULT_BLOCK_SIZE) -> EntropyHeatmap:
    """Sliding-block entropy heatmap. Each bucket is (offset, entropy)."""
    if block_size <= 0 or not data:
        return EntropyHeatmap(buckets=[], block_size=block_size, total_size=len(data))
    buckets: list[tuple[int, float]] = []
    for offset in range(0, len(data), block_size):
        block = data[offset : offset + block_size]
        buckets.append((offset, shannon_entropy(block)))
    return EntropyHeatmap(buckets=buckets, block_size=block_size, total_size=len(data))


def detect_packing(
    sections: Sequence[SectionEntropy],
) -> tuple[bool, DetectionConfidence, list[str]]:
    """Detect packing from high-entropy section count.

    Returns (packed, confidence, evidence).
    """
    high = [s for s in sections if s.is_high_entropy]
    if not high:
        return False, DetectionConfidence.LOW, []
    evidence: list[str] = []
    for s in high:
        evidence.append(f"section {s.name} entropy {s.entropy:.2f} > {PACKING_ENTROPY_THRESHOLD:.1f}")
    if len(high) >= 2:
        return True, DetectionConfidence.HIGH, evidence
    return True, DetectionConfidence.MEDIUM, evidence


def detect_encrypted_sections(
    sections: Sequence[SectionEntropy],
    heatmap: EntropyHeatmap | None,
) -> list[SectionEntropy]:
    """Identify sections likely encrypted: entropy >= encrypted threshold.

    If a heatmap is supplied and overlaps the section, the section is only
    flagged when every overlapping block also exceeds the packing threshold
    (encrypted regions are uniformly high-entropy, unlike packed regions
    that may have a low-entropy stub or header).
    """
    flagged: list[SectionEntropy] = []
    for s in sections:
        if s.entropy < ENCRYPTED_ENTROPY_THRESHOLD:
            continue
        if heatmap is not None and heatmap.buckets:
            overlapping = [
                be for off, be in heatmap.buckets
                if off + heatmap.block_size > s.offset and off < s.offset + s.size
            ]
            if overlapping and not all(be > PACKING_ENTROPY_THRESHOLD for be in overlapping):
                continue
        flagged.append(s)
    return flagged


def analyze_entropy(
    binary_path_or_bytes: str | Path | bytes | bytearray,
) -> EntropyAnalysisResult:
    """End-to-end entropy analysis of a binary file or raw bytes."""
    if isinstance(binary_path_or_bytes, bytearray):
        data = bytes(binary_path_or_bytes)
    elif isinstance(binary_path_or_bytes, bytes):
        data = binary_path_or_bytes
    elif isinstance(binary_path_or_bytes, (str, Path)):
        path = Path(binary_path_or_bytes)
        try:
            data = path.read_bytes()
        except OSError:
            return EntropyAnalysisResult(
                file_type="unknown",
                overall_entropy=0.0,
            )
    else:
        return EntropyAnalysisResult(file_type="unknown", overall_entropy=0.0)

    file_type = _identify_file_type(data)
    overall = shannon_entropy(data)
    sections = compute_section_entropies(data)
    heatmap = build_entropy_heatmap(data)
    packed, conf, pack_evidence = detect_packing(sections)
    encrypted = detect_encrypted_sections(sections, heatmap)
    suspicious = [s for s in sections if s.is_high_entropy and s not in encrypted]

    evidence: list[str] = []
    evidence.append(f"overall file entropy {overall:.2f} bits/byte")
    evidence.extend(pack_evidence)
    for s in encrypted:
        evidence.append(f"encrypted section {s.name} entropy {s.entropy:.2f} >= {ENCRYPTED_ENTROPY_THRESHOLD:.1f}")

    return EntropyAnalysisResult(
        file_type=file_type,
        overall_entropy=overall,
        sections=sections,
        heatmap=heatmap,
        packed=packed,
        packing_confidence=conf,
        encrypted_sections=encrypted,
        suspicious_sections=suspicious,
        evidence=evidence,
    )
