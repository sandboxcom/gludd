"""GGUF quantization quality verification — header parsing, layer count, tensor shapes."""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

_GGUF_MAGIC = b"GGUF"
_HEADER_MIN_SIZE = struct.calcsize("<4sIQQ")
_GGML_TYPE_NAMES: dict[int, str] = {
    0: "F32",
    1: "F16",
    2: "Q4_0",
    3: "Q4_1",
    6: "Q5_0",
    7: "Q5_1",
    8: "Q8_0",
    9: "Q8_1",
    10: "Q2_K",
    11: "Q3_K",
    12: "Q4_K",
    13: "Q5_K",
    14: "Q6_K",
    15: "Q8_K",
    16: "IQ2_XXS",
    17: "IQ2_XS",
    18: "IQ3_XXS",
    19: "IQ1_S",
    20: "IQ4_NL",
    21: "IQ3_S",
    22: "IQ2_S",
    23: "IQ4_XS",
    24: "I8",
    25: "I16",
    26: "I32",
    27: "I64",
    28: "F64",
}

_QUANT_LEVEL_RE = re.compile(
    r"(?:^|[-_])("
    r"(?:IQ\d_\w+|Q[2-8]_[K0-9]\w*|"
    r"F16|F32|F64|"
    r"I8|I16|I32|I64"
    r")"
    r")(?=\.gguf$)",
    re.IGNORECASE,
)


@dataclass
class GGUFHeader:
    """Parsed GGUF file header."""

    valid: bool = False
    version: int = 0
    tensor_count: int = 0
    metadata_kv_count: int = 0
    file_size: int = 0
    architecture: str = "unknown"
    metadata: dict[str, object] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


@dataclass
class GGUFMetadata:
    """Key-value metadata extracted from a GGUF file."""

    values: dict[str, object] = field(default_factory=dict)
    architecture: str = "unknown"
    quantization_version: float | None = None
    block_count: int | None = None
    context_length: int | None = None


@dataclass
class GGUFQuantInfo:
    """Quantization information derived from filename and header."""

    quantization_level: str = "unknown"
    file_type: str | None = None
    bits_per_weight: float | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    @classmethod
    def from_filename(cls, filename: str) -> GGUFQuantInfo:
        """Extract quantization level from a GGUF filename."""
        basename = Path(filename).name
        return cls(quantization_level=quantize_level_for_name(basename))


@dataclass
class QuantCheckResult:
    """Result of a GGUF quality verification."""

    valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    layer_count: int = 0
    quantization_level: str = "unknown"
    tensor_types: dict[str, int] = field(default_factory=dict)
    header_version: int = 0
    file_path: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "layer_count": self.layer_count,
            "quantization_level": self.quantization_level,
            "tensor_types": self.tensor_types,
            "header_version": self.header_version,
            "file_path": self.file_path,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> QuantCheckResult:
        raw_errors = data.get("errors", [])
        raw_warnings = data.get("warnings", [])
        raw_tensor_types = data.get("tensor_types", {})
        errors: list[str] = list(raw_errors) if isinstance(raw_errors, list) else []
        warnings: list[str] = list(raw_warnings) if isinstance(raw_warnings, list) else []
        tensor_types: dict[str, int] = dict(raw_tensor_types) if isinstance(raw_tensor_types, dict) else {}
        raw_layer_count = data.get("layer_count", 0)
        raw_header_version = data.get("header_version", 0)
        layer_count: int = raw_layer_count if isinstance(raw_layer_count, int) else int(str(raw_layer_count))
        header_version: int = (
            raw_header_version if isinstance(raw_header_version, int) else int(str(raw_header_version))
        )
        return cls(
            valid=bool(data.get("valid", True)),
            errors=errors,
            warnings=warnings,
            layer_count=layer_count,
            quantization_level=str(data.get("quantization_level", "unknown")),
            tensor_types=tensor_types,
            header_version=header_version,
            file_path=str(data.get("file_path", "")),
        )


def quantize_level_for_name(filename: str) -> str:
    """Return the quantization level encoded in the filename, or 'unknown'."""
    m = _QUANT_LEVEL_RE.search(filename)
    return m.group(1).upper() if m else "unknown"


def _read_string(data: bytes, offset: int) -> tuple[str, int]:
    length = struct.unpack_from("<Q", data, offset)[0]
    offset += 8
    value = data[offset : offset + length].decode("utf-8", errors="replace")
    offset += length
    return value, offset


def _read_metadata_value(data: bytes, offset: int, value_type: int) -> tuple[object, int]:
    if value_type == 8:  # string
        length = struct.unpack_from("<Q", data, offset)[0]
        offset += 8
        value: object = data[offset : offset + length].decode("utf-8", errors="replace")
        offset += length
        return value, offset
    if value_type == 6:  # float32
        value = struct.unpack_from("<f", data, offset)[0]
        offset += 4
        return value, offset
    if value_type == 2:  # int16
        value = struct.unpack_from("<h", data, offset)[0]
        offset += 2
        return value, offset
    if value_type == 4:  # int32
        value = struct.unpack_from("<i", data, offset)[0]
        offset += 4
        return value, offset
    if value_type == 10:  # int64
        value = struct.unpack_from("<q", data, offset)[0]
        offset += 8
        return value, offset
    if value_type == 12:  # uint64
        value = struct.unpack_from("<Q", data, offset)[0]
        offset += 8
        return value, offset
    if value_type == 0:  # uint8
        value = data[offset]
        offset += 1
        return value, offset
    if value_type == 1:  # int8
        value = struct.unpack_from("<b", data, offset)[0]
        offset += 1
        return value, offset
    if value_type == 3:  # uint16
        value = struct.unpack_from("<H", data, offset)[0]
        offset += 2
        return value, offset
    if value_type == 5:  # uint32
        value = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        return value, offset
    if value_type == 7:  # bool
        value = bool(data[offset])
        offset += 1
        return value, offset
    if value_type == 9:  # array
        array_type = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        array_len = struct.unpack_from("<Q", data, offset)[0]
        offset += 8
        items: list[object] = []
        for _ in range(array_len):
            item, offset = _read_metadata_value(data, offset, array_type)
            items.append(item)
        return items, offset
    return None, offset


def verify_gguf_header(data: bytes) -> GGUFHeader:
    """Parse and validate a GGUF file header from raw bytes."""
    header = GGUFHeader(file_size=len(data))
    if len(data) < _HEADER_MIN_SIZE:
        header.errors.append("File too small for GGUF header")
        return header
    if data[:4] != _GGUF_MAGIC:
        header.errors.append(f"Invalid GGUF magic: {data[:4]!r}")
        return header
    header.version = struct.unpack_from("<I", data, 4)[0]
    if header.version not in (1, 2, 3):
        header.errors.append(f"Unsupported GGUF version: {header.version}")
        return header
    header.tensor_count = struct.unpack_from("<Q", data, 8)[0]
    header.metadata_kv_count = struct.unpack_from("<Q", data, 16)[0]
    header.valid = True

    offset = _HEADER_MIN_SIZE
    metadata: dict[str, object] = {}
    for _ in range(header.metadata_kv_count):
        if offset >= len(data):
            header.errors.append("Truncated metadata section")
            break
        key, offset = _read_string(data, offset)
        if offset + 4 > len(data):
            header.errors.append(f"Truncated value type for key {key}")
            break
        value_type = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        value, offset = _read_metadata_value(data, offset, value_type)
        metadata[key] = value
        if key == "general.architecture":
            header.architecture = str(value)

    header.metadata = metadata
    return header
