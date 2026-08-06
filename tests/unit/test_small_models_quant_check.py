"""Unit tests for GGUF quantization quality checks."""

from __future__ import annotations

import struct

from general_ludd.small_models.quant_check import (
    GGUFQuantInfo,
    QuantCheckResult,
    quantize_level_for_name,
    verify_gguf_header,
)


def _build_gguf(header: dict, tensor_infos: list[dict] | None = None, metadata_kv: dict | None = None) -> bytes:
    """Build a minimal valid GGUF file from header + optional tensor info."""
    gguf_magic = b"GGUF"
    version = header.get("version", 3)
    tensor_count = header.get("tensor_count", len(tensor_infos) if tensor_infos else 0)
    metadata_kv_count = header.get("metadata_kv_count", 1 + len(metadata_kv or {}))

    buf = bytearray()
    buf.extend(gguf_magic)
    buf.extend(struct.pack("<I", version))
    buf.extend(struct.pack("<Q", tensor_count))
    buf.extend(struct.pack("<Q", metadata_kv_count))

    arch_key = b"general.architecture"
    buf.extend(struct.pack("<Q", len(arch_key)))
    buf.extend(arch_key)
    buf.extend(struct.pack("<I", 8))
    arch_val = b"llama"
    buf.extend(struct.pack("<Q", len(arch_val)))
    buf.extend(arch_val)

    for key, (typ, val) in (metadata_kv or {}).items():
        key_bytes = key.encode()
        buf.extend(struct.pack("<Q", len(key_bytes)))
        buf.extend(key_bytes)
        buf.extend(struct.pack("<I", typ))
        if typ == 8:
            val_bytes = val.encode() if isinstance(val, str) else val
            buf.extend(struct.pack("<Q", len(val_bytes)))
            buf.extend(val_bytes)
        elif typ == 6:
            buf.extend(struct.pack("<f", val))

    if tensor_infos:
        for ti in tensor_infos:
            name_bytes = ti["name"].encode()
            buf.extend(struct.pack("<Q", len(name_bytes)))
            buf.extend(name_bytes)
            buf.extend(struct.pack("<I", ti.get("n_dims", 2)))
            for dim in ti.get("dimensions", [4, 4]):
                buf.extend(struct.pack("<Q", dim))
            buf.extend(struct.pack("<I", ti.get("ggml_type", 0)))
            buf.extend(struct.pack("<Q", ti.get("offset", 0)))

    return bytes(buf)


class TestGGUFHeader:
    def test_valid_header_minimal(self):
        data = _build_gguf({"version": 3, "tensor_count": 0, "metadata_kv_count": 1})
        header = verify_gguf_header(data)
        assert header.valid
        assert header.version == 3
        assert header.tensor_count == 0
        assert header.file_size == len(data)

    def test_invalid_magic(self):
        data = b"XXXX" + struct.pack("<I", 3) + struct.pack("<Q", 0) + struct.pack("<Q", 1)
        header = verify_gguf_header(data)
        assert not header.valid
        assert "magic" in header.errors[0].lower()

    def test_truncated_header(self):
        data = b"GGUF" + b"\x03\x00\x00\x00"  # incomplete
        header = verify_gguf_header(data)
        assert not header.valid

    def test_version_unsupported(self):
        data = b"GGUF" + struct.pack("<I", 99) + struct.pack("<Q", 0) + struct.pack("<Q", 0)
        header = verify_gguf_header(data)
        assert not header.valid

    def test_gguf_v3_with_tensors(self):
        tensor_infos = [
            {"name": "blk.0.attn.weight", "n_dims": 2, "dimensions": [64, 64], "ggml_type": 10},
            {"name": "blk.1.attn.weight", "n_dims": 2, "dimensions": [64, 64], "ggml_type": 10},
        ]
        data = _build_gguf({"version": 3, "tensor_count": 2, "metadata_kv_count": 1}, tensor_infos)
        header = verify_gguf_header(data)
        assert header.valid
        assert header.tensor_count == 2


class TestGGUFMetadata:
    def test_metadata_extraction(self):
        meta_kv = {
            "general.quantization_version": (6, 2.0),
            "llama.block_count": (8, "32"),
            "llama.context_length": (8, "4096"),
        }
        data = _build_gguf({"version": 3, "tensor_count": 0, "metadata_kv_count": 4}, metadata_kv=meta_kv)
        header = verify_gguf_header(data)
        assert header.valid
        assert isinstance(header.metadata, dict)

    def test_architecture_detected(self):
        data = _build_gguf({"version": 3, "tensor_count": 0, "metadata_kv_count": 1})
        header = verify_gguf_header(data)
        assert header.architecture == "llama"


class TestGGUFQuantInfo:
    def test_default_unknown(self):
        info = GGUFQuantInfo()
        assert info.quantization_level == "unknown"
        assert info.file_type is None

    def test_from_filename_q4_k_m(self):
        info = GGUFQuantInfo.from_filename("model-q4_k_m.gguf")
        assert info.quantization_level == "Q4_K_M"

    def test_from_filename_f16(self):
        info = GGUFQuantInfo.from_filename("model-f16.gguf")
        assert info.quantization_level == "F16"

    def test_from_filename_unknown(self):
        info = GGUFQuantInfo.from_filename("model.gguf")
        assert info.quantization_level == "unknown"

    def test_from_filename_uppercase(self):
        info = GGUFQuantInfo.from_filename("model-q5_1.gguf")
        assert "Q5_1" in info.quantization_level


class TestQuantCheckResult:
    def test_defaults(self):
        r = QuantCheckResult()
        assert r.valid
        assert r.errors == []
        assert r.warnings == []
        assert r.layer_count == 0
        assert r.quantization_level == "unknown"

    def test_with_errors(self):
        r = QuantCheckResult(
            valid=False,
            errors=["bad magic"],
            warnings=["low bit precision"],
            layer_count=24,
            quantization_level="Q4_K_M",
        )
        assert not r.valid
        assert "bad magic" in r.errors
        assert "low bit precision" in r.warnings
        assert r.layer_count == 24
        assert r.quantization_level == "Q4_K_M"


class TestQuantizeLevelForName:
    def test_q4_k_m(self):
        assert quantize_level_for_name("model-q4_k_m.gguf") == "Q4_K_M"

    def test_q8_0(self):
        assert quantize_level_for_name("model-q8_0.gguf") == "Q8_0"

    def test_f16(self):
        assert quantize_level_for_name("model-f16.gguf") == "F16"

    def test_unknown(self):
        assert quantize_level_for_name("model.gguf") == "unknown"

    def test_q2_k(self):
        assert quantize_level_for_name("model-q2_k.gguf") == "Q2_K"


class TestVerifyGGUFHeaderEdgeCases:
    def test_zero_tensors(self):
        data = _build_gguf({"version": 3, "tensor_count": 0, "metadata_kv_count": 1})
        header = verify_gguf_header(data)
        assert header.valid
        assert header.tensor_count == 0

    def test_empty_data(self):
        header = verify_gguf_header(b"")
        assert not header.valid

    def test_large_tensor_count(self):
        tensor_count = 256
        data = _build_gguf({"version": 3, "tensor_count": tensor_count, "metadata_kv_count": 1})
        header = verify_gguf_header(data)
        assert header.valid
        assert header.tensor_count == tensor_count


class TestQuantCheckResultSerialization:
    def test_to_dict(self):
        r = QuantCheckResult(layer_count=32, quantization_level="Q4_K_M")
        d = r.to_dict()
        assert d["valid"] is True
        assert d["layer_count"] == 32
        assert d["quantization_level"] == "Q4_K_M"

    def test_from_dict(self):
        d = {"valid": False, "errors": ["bad"], "warnings": [], "layer_count": 0, "quantization_level": "F16"}
        r = QuantCheckResult.from_dict(d)
        assert not r.valid
        assert "bad" in r.errors
        assert r.quantization_level == "F16"

    def test_roundtrip(self):
        r = QuantCheckResult(
            valid=True,
            errors=[],
            warnings=["warning"],
            layer_count=24,
            quantization_level="Q4_K_M",
        )
        assert QuantCheckResult.from_dict(r.to_dict()) == r
