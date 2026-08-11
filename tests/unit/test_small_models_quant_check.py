"""Unit tests for GGUF quantization quality checks."""

from __future__ import annotations

import struct

import pytest

from general_ludd.small_models.quant_check import (
    GGUFHeader,
    GGUFMetadata,
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


# ── deep tests ──────────────────────────────────────────────────────────


class TestReadStringEdgeCases:
    def _call_read_string(self, data: bytes, offset: int) -> tuple[str, int]:
        from general_ludd.small_models.quant_check import _read_string

        return _read_string(data, offset)

    def test_empty_string(self):
        data = struct.pack("<Q", 0) + b"next"
        value, offset = self._call_read_string(data, 0)
        assert value == ""
        assert offset == 8

    def test_ascii_normal(self):
        value_bytes = b"hello"
        data = struct.pack("<Q", len(value_bytes)) + value_bytes
        value, offset = self._call_read_string(data, 0)
        assert value == "hello"
        assert offset == 8 + 5

    def test_utf8_multibyte(self):
        utf8_bytes = "café".encode()
        data = struct.pack("<Q", len(utf8_bytes)) + utf8_bytes
        value, _ = self._call_read_string(data, 0)
        assert value == "café"

    def test_utf8_cjk(self):
        cjk_bytes = "模型".encode()
        data = struct.pack("<Q", len(cjk_bytes)) + cjk_bytes
        value, _ = self._call_read_string(data, 0)
        assert value == "模型"

    def test_decode_errors_replaced(self):
        data = struct.pack("<Q", 3) + b"\xff\xfe\xfd"
        value, _ = self._call_read_string(data, 0)
        assert "\ufffd" in value


class TestReadMetadataValueAllTypes:
    def _pack_value(self, typ: int, payload: bytes) -> bytes:
        return struct.pack("<I", typ) + payload

    def _call_read(self, data: bytes, offset: int, value_type: int) -> object:
        from general_ludd.small_models.quant_check import _read_metadata_value

        return _read_metadata_value(data, offset, value_type)

    def test_uint8(self):
        val, off = self._call_read(b"\x2a", 0, 0)
        assert val == 42
        assert off == 1

    def test_int8(self):
        val, off = self._call_read(struct.pack("<b", -1), 0, 1)
        assert val == -1
        assert off == 1

    def test_int16(self):
        val, off = self._call_read(struct.pack("<h", -1000), 0, 2)
        assert val == -1000
        assert off == 2

    def test_uint16(self):
        val, off = self._call_read(struct.pack("<H", 65000), 0, 3)
        assert val == 65000
        assert off == 2

    def test_int32(self):
        val, off = self._call_read(struct.pack("<i", -1_000_000), 0, 4)
        assert val == -1_000_000
        assert off == 4

    def test_uint32(self):
        val, off = self._call_read(struct.pack("<I", 4_000_000_000), 0, 5)
        assert val == 4_000_000_000
        assert off == 4

    def test_float32(self):
        val, off = self._call_read(struct.pack("<f", 3.140000104904175), 0, 6)
        assert val == pytest.approx(3.14, rel=1e-5)
        assert off == 4

    def test_bool_true(self):
        val, off = self._call_read(b"\x01", 0, 7)
        assert val is True
        assert off == 1

    def test_bool_false(self):
        val, off = self._call_read(b"\x00", 0, 7)
        assert val is False
        assert off == 1

    def test_string(self):
        s = b"metadata_value"
        data = struct.pack("<Q", len(s)) + s
        val, off = self._call_read(data, 0, 8)
        assert val == "metadata_value"
        assert off == 8 + len(s)

    def test_array_of_ints(self):
        items = struct.pack("<i", 10) + struct.pack("<i", 20) + struct.pack("<i", 30)
        data = struct.pack("<I", 4) + struct.pack("<Q", 3) + items
        val, off = self._call_read(data, 0, 9)
        assert val == [10, 20, 30]
        assert off == 24

    def test_array_of_strings(self):
        s1, s2 = b"a", b"bb"
        items = struct.pack("<Q", 1) + s1 + struct.pack("<Q", 2) + s2
        data = struct.pack("<I", 8) + struct.pack("<Q", 2) + items
        val, _ = self._call_read(data, 0, 9)
        assert val == ["a", "bb"]

    def test_int64(self):
        val, off = self._call_read(struct.pack("<q", -(2**40)), 0, 10)
        assert val == -(2**40)
        assert off == 8

    def test_uint64_value(self):
        val, off = self._call_read(struct.pack("<Q", 0xFFFFFFFFFFFFFFFF), 0, 12)
        assert val == 0xFFFFFFFFFFFFFFFF
        assert off == 8

    def test_unknown_type_returns_none(self):
        val, off = self._call_read(b"\xff" * 100, 0, 99)
        assert val is None
        assert off == 0


class TestVerifyGGUFHeaderTruncation:
    def test_truncated_at_metadata_value_type(self):
        data = b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0) + struct.pack("<Q", 2)
        key = b"a_key"
        data += struct.pack("<Q", len(key)) + key
        header = verify_gguf_header(data)
        assert not header.valid or "Truncated" in str(header.errors).lower() or len(header.errors) > 0

    def test_truncated_mid_string_read(self):
        data = b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0) + struct.pack("<Q", 1)
        data += struct.pack("<Q", 999)
        header = verify_gguf_header(data)
        assert len(header.errors) > 0

    def test_offset_exactly_at_boundary(self):
        data = _build_gguf({"version": 3, "tensor_count": 0, "metadata_kv_count": 1})
        header = verify_gguf_header(data)
        assert header.valid

    def test_version_1_accepts(self):
        data = b"GGUF" + struct.pack("<I", 1) + struct.pack("<Q", 0) + struct.pack("<Q", 0)
        header = verify_gguf_header(data)
        assert header.valid

    def test_version_2_accepts(self):
        data = b"GGUF" + struct.pack("<I", 2) + struct.pack("<Q", 0) + struct.pack("<Q", 0)
        header = verify_gguf_header(data)
        assert header.valid

    def test_no_architecture_metadata(self):
        data = b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0) + struct.pack("<Q", 1)
        key = b"different.key"
        data += struct.pack("<Q", len(key)) + key
        data += struct.pack("<I", 8)
        val = b"some_value"
        data += struct.pack("<Q", len(val)) + val
        header = verify_gguf_header(data)
        assert header.valid
        assert header.architecture == "unknown"

    def test_file_size_tracked(self):
        data = _build_gguf({"version": 3, "tensor_count": 0, "metadata_kv_count": 1})
        header = verify_gguf_header(data)
        assert header.file_size == len(data)

    def test_large_metadata_kv_count_truncation(self):
        data = b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0) + struct.pack("<Q", 1000000)
        header = verify_gguf_header(data)
        assert len(header.errors) > 0


class TestQuantCheckResultFromDictMalformed:
    def test_layer_count_is_string(self):
        d = {"layer_count": "42", "valid": True}
        r = QuantCheckResult.from_dict(d)
        assert r.layer_count == 42

    def test_layer_count_is_float_str(self):
        d = {"layer_count": "24.0"}
        with pytest.raises(ValueError):
            QuantCheckResult.from_dict(d)

    def test_valid_is_none(self):
        d = {"valid": None}
        r = QuantCheckResult.from_dict(d)
        assert r.valid is False

    def test_valid_is_zero(self):
        d = {"valid": 0}
        r = QuantCheckResult.from_dict(d)
        assert r.valid is False

    def test_errors_not_a_list(self):
        d = {"errors": "bad magic"}
        r = QuantCheckResult.from_dict(d)
        assert r.errors == []

    def test_warnings_not_a_list(self):
        d = {"warnings": 123}
        r = QuantCheckResult.from_dict(d)
        assert r.warnings == []

    def test_tensor_types_not_a_dict(self):
        d = {"tensor_types": [("a", 1)]}
        r = QuantCheckResult.from_dict(d)
        assert r.tensor_types == {}

    def test_header_version_not_int(self):
        d = {"header_version": "3"}
        r = QuantCheckResult.from_dict(d)
        assert r.header_version == 3

    def test_missing_all_keys(self):
        r = QuantCheckResult.from_dict({})
        assert r.valid
        assert r.layer_count == 0
        assert r.quantization_level == "unknown"
        assert r.errors == []

    def test_partial_keys_present(self):
        d = {"layer_count": 7, "header_version": 2}
        r = QuantCheckResult.from_dict(d)
        assert r.layer_count == 7
        assert r.header_version == 2
        assert r.valid


class TestQuantizeLevelForNameAllVariants:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("model-Q4_0.gguf", "Q4_0"),
            ("model-Q4_1.gguf", "Q4_1"),
            ("model-Q5_0.gguf", "Q5_0"),
            ("model-Q5_1.gguf", "Q5_1"),
            ("model-Q8_0.gguf", "Q8_0"),
            ("model-Q8_1.gguf", "Q8_1"),
            ("model-Q2_K.gguf", "Q2_K"),
            ("model-Q3_K.gguf", "Q3_K"),
            ("model-Q4_K.gguf", "Q4_K"),
            ("model-Q5_K.gguf", "Q5_K"),
            ("model-Q6_K.gguf", "Q6_K"),
            ("model-Q8_K.gguf", "Q8_K"),
            ("model-IQ2_XXS.gguf", "IQ2_XXS"),
            ("model-IQ2_XS.gguf", "IQ2_XS"),
            ("model-IQ3_XXS.gguf", "IQ3_XXS"),
            ("model-IQ1_S.gguf", "IQ1_S"),
            ("model-IQ4_NL.gguf", "IQ4_NL"),
            ("model-IQ3_S.gguf", "IQ3_S"),
            ("model-IQ2_S.gguf", "IQ2_S"),
            ("model-IQ4_XS.gguf", "IQ4_XS"),
            ("model-F16.gguf", "F16"),
            ("model-F32.gguf", "F32"),
            ("model-F64.gguf", "F64"),
            ("model-I8.gguf", "I8"),
            ("model-I16.gguf", "I16"),
            ("model-I32.gguf", "I32"),
            ("model-I64.gguf", "I64"),
        ],
    )
    def test_each_known_variant(self, name: str, expected: str):
        assert quantize_level_for_name(name) == expected

    def test_lowercase(self):
        assert quantize_level_for_name("model-q4_k_m.gguf") == "Q4_K_M"

    def test_mixed_case(self):
        assert quantize_level_for_name("LLAMA-Q4_K_M.GGUF") == "Q4_K_M"

    def test_path_with_directories(self):
        result = quantize_level_for_name("/home/user/models/llama-3b-q8_0.gguf")
        assert result == "Q8_0"

    def test_multiple_hints_matches_first(self):
        result = quantize_level_for_name("model-q4_k_m_f16.gguf")
        assert result == "Q4_K_M_F16"

    def test_quant_in_middle_anchored(self):
        result = quantize_level_for_name("my_q8_0_model.gguf")
        assert "Q8_0" in result

    def test_underscore_prefix(self):
        result = quantize_level_for_name("_q4_0.gguf")
        assert result == "Q4_0"

    def test_no_dot_gguf(self):
        result = quantize_level_for_name("model-q4_k_m.bin")
        assert result == "unknown"

    def test_plain_name_no_ext(self):
        assert quantize_level_for_name("model") == "unknown"


class TestGGUFQuantInfoEdgeCases:
    def test_bits_per_weight_default_none(self):
        info = GGUFQuantInfo()
        assert info.bits_per_weight is None
        assert info.file_type is None

    def test_metadata_default_factory_is_isolated(self):
        a = GGUFQuantInfo()
        b = GGUFQuantInfo()
        a.metadata["key"] = "val"
        assert "key" not in b.metadata

    def test_from_filename_preserves_defaults(self):
        info = GGUFQuantInfo.from_filename("model.gguf")
        assert info.bits_per_weight is None
        assert info.file_type is None

    def test_from_filename_with_bits_per_weight(self):
        info = GGUFQuantInfo(
            quantization_level="Q4_K_M",
            bits_per_weight=4.5,
            file_type="Q4_K_M",
            metadata={"source": "test"},
        )
        assert info.bits_per_weight == 4.5
        assert info.file_type == "Q4_K_M"
        assert info.metadata["source"] == "test"

    def test_repr_does_not_crash(self):
        info = GGUFQuantInfo(quantization_level="Q4_K_M", bits_per_weight=4.5)
        assert "Q4_K_M" in repr(info)


class TestGGUFHeaderEdgeCases:
    def test_all_fields_preserved(self):
        header = GGUFHeader(
            valid=True,
            version=3,
            tensor_count=100,
            metadata_kv_count=10,
            file_size=4096,
            architecture="falcon",
            metadata={"key": "value"},
            errors=[],
        )
        assert header.valid
        assert header.version == 3
        assert header.tensor_count == 100
        assert header.metadata_kv_count == 10
        assert header.architecture == "falcon"

    def test_errors_default_factory_isolated(self):
        a = GGUFHeader()
        b = GGUFHeader()
        a.errors.append("err")
        assert b.errors == []

    def test_metadata_default_factory_isolated(self):
        a = GGUFHeader()
        b = GGUFHeader()
        a.metadata["k"] = "v"
        assert b.metadata == {}

    def test_invalid_default(self):
        header = GGUFHeader()
        assert not header.valid
        assert header.file_size == 0
        assert header.architecture == "unknown"


class TestGGUFMetadataEdgeCases:
    def test_defaults(self):
        m = GGUFMetadata()
        assert m.values == {}
        assert m.architecture == "unknown"
        assert m.quantization_version is None
        assert m.block_count is None
        assert m.context_length is None

    def test_values_isolation(self):
        a = GGUFMetadata()
        b = GGUFMetadata()
        a.values["x"] = 1
        assert b.values == {}

    def test_all_fields_set(self):
        m = GGUFMetadata(
            values={"a": 1},
            architecture="mistral",
            quantization_version=2.0,
            block_count=32,
            context_length=8192,
        )
        assert m.values["a"] == 1
        assert m.architecture == "mistral"
        assert m.quantization_version == 2.0
        assert m.block_count == 32
        assert m.context_length == 8192


class TestQuantCheckResultEquality:
    def test_equal(self):
        r1 = QuantCheckResult(layer_count=24, quantization_level="Q4_K_M")
        r2 = QuantCheckResult(layer_count=24, quantization_level="Q4_K_M")
        assert r1 == r2

    def test_not_equal_layer_count(self):
        r1 = QuantCheckResult(layer_count=24)
        r2 = QuantCheckResult(layer_count=32)
        assert r1 != r2

    def test_not_equal_errors(self):
        r1 = QuantCheckResult(errors=["bad"])
        r2 = QuantCheckResult(errors=["bad"], warnings=["w"])
        assert r1 != r2

    def test_unhashable_due_to_mutable_fields(self):
        r = QuantCheckResult(layer_count=24, quantization_level="Q4_K_M")
        try:
            hash(r)
            raise AssertionError("should have raised TypeError")
        except TypeError:
            assert True


class TestQuantCheckResultToDict:
    def test_tensor_types_roundtrip(self):
        r = QuantCheckResult(tensor_types={"F16": 100, "Q4_K": 200})
        d = r.to_dict()
        assert d["tensor_types"] == {"F16": 100, "Q4_K": 200}

    def test_file_path_roundtrip(self):
        r = QuantCheckResult(file_path="/tmp/model.gguf")
        d = r.to_dict()
        assert d["file_path"] == "/tmp/model.gguf"

    def test_to_dict_shares_errors_reference(self):
        r = QuantCheckResult(errors=["err1"])
        d = r.to_dict()
        d["errors"].append("err2")
        assert r.errors == ["err1", "err2"]

    def test_to_dict_shares_warnings_reference(self):
        r = QuantCheckResult(warnings=["w1"])
        d = r.to_dict()
        d["warnings"].append("w2")
        assert r.warnings == ["w1", "w2"]


class TestVerifyGGUFHeaderDeepEdgeCases:
    def test_magic_only_barely_valid(self):
        data = bytearray()
        data.extend(b"GGUF")
        data.extend(struct.pack("<I", 3))
        data.extend(struct.pack("<Q", 0))
        data.extend(struct.pack("<Q", 0))
        header = verify_gguf_header(bytes(data))
        assert header.valid
        assert header.tensor_count == 0
        assert header.metadata_kv_count == 0

    def test_metadata_key_exceeds_data(self):
        buf = bytearray()
        buf.extend(b"GGUF")
        buf.extend(struct.pack("<I", 3))
        buf.extend(struct.pack("<Q", 0))
        buf.extend(struct.pack("<Q", 1))
        buf.extend(struct.pack("<Q", 999999))
        header = verify_gguf_header(bytes(buf))
        assert len(header.errors) > 0

    def test_value_type_exceeds_data_after_key(self):
        buf = bytearray()
        buf.extend(b"GGUF")
        buf.extend(struct.pack("<I", 3))
        buf.extend(struct.pack("<Q", 0))
        buf.extend(struct.pack("<Q", 1))
        key = b"a"
        buf.extend(struct.pack("<Q", len(key)))
        buf.extend(key)
        header = verify_gguf_header(bytes(buf))
        assert not header.valid or len(header.errors) > 0

    def test_metadata_loop_breaks_on_truncation(self):
        buf = bytearray()
        buf.extend(b"GGUF")
        buf.extend(struct.pack("<I", 3))
        buf.extend(struct.pack("<Q", 0))
        buf.extend(struct.pack("<Q", 3))
        key = b"k1"
        buf.extend(struct.pack("<Q", len(key)))
        buf.extend(key)
        buf.extend(struct.pack("<I", 8))
        val = b"v1"
        buf.extend(struct.pack("<Q", len(val)))
        buf.extend(val)
        buf.extend(struct.pack("<Q", 999))
        header = verify_gguf_header(bytes(buf))
        assert len(header.errors) > 0
