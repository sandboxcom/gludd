"""Unit tests for local model quantization pipeline."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from general_ludd.quantization.quantize import (
    HardwareCapacity,
    ModelQuantizer,
    QuantMethod,
    select_quant_for_hardware,
)


class TestQuantMethod:
    def test_quant_method_values(self):
        assert QuantMethod.Q4_0.value == "q4_0"
        assert QuantMethod.Q4_K_M.value == "q4_K_M"
        assert QuantMethod.Q8_0.value == "q8_0"
        assert QuantMethod.FP16.value == "f16"

    def test_quant_method_bits(self):
        assert QuantMethod.Q4_0.bits() == 4
        assert QuantMethod.Q4_K_M.bits() == 4
        assert QuantMethod.Q8_0.bits() == 8
        assert QuantMethod.FP16.bits() == 16

    def test_quant_method_quality_score(self):
        assert QuantMethod.FP16.quality_score() == 1.0
        assert QuantMethod.Q8_0.quality_score() > QuantMethod.Q4_0.quality_score()
        assert QuantMethod.Q4_K_M.quality_score() > QuantMethod.Q4_0.quality_score()

    def test_from_string_valid(self):
        assert QuantMethod.from_string("q4_0") == QuantMethod.Q4_0
        assert QuantMethod.from_string("q4_K_M") == QuantMethod.Q4_K_M
        assert QuantMethod.from_string("q8_0") == QuantMethod.Q8_0
        assert QuantMethod.from_string("f16") == QuantMethod.FP16

    def test_from_string_case_insensitive(self):
        assert QuantMethod.from_string("Q4_0") == QuantMethod.Q4_0
        assert QuantMethod.from_string("Q8_0") == QuantMethod.Q8_0

    def test_from_string_unknown_raises(self):
        try:
            QuantMethod.from_string("q3_0")
        except ValueError:
            pass
        else:
            raise AssertionError("Expected ValueError for unknown quant method")


class TestHardwareCapacity:
    def test_from_probe_unified_memory(self):
        with patch("general_ludd.quantization.quantize.detect_memory") as mock_detect:
            from general_ludd.hardware_memory_policy import MemoryInfo

            mock_detect.return_value = MemoryInfo(
                kind="unified",
                total_bytes=16 * 1024**3,
                available_bytes=14 * 1024**3,
                backend="mps",
                device="Apple M2",
            )
            cap = HardwareCapacity.from_probe()
            assert cap.memory_kind == "unified"
            assert cap.total_memory_gb >= 14.0
            assert cap.backend == "mps"

    def test_from_probe_discrete_vram(self):
        with patch("general_ludd.quantization.quantize.detect_memory") as mock_detect:
            from general_ludd.hardware_memory_policy import MemoryInfo

            mock_detect.return_value = MemoryInfo(
                kind="vram",
                total_bytes=8 * 1024**3,
                available_bytes=7 * 1024**3,
                backend="cuda",
                device="RTX 3070",
            )
            cap = HardwareCapacity.from_probe()
            assert cap.memory_kind == "discrete"
            assert cap.total_memory_gb >= 7.0


class TestQuantSelection:
    def test_quant_for_hardware_unified_8gb(self):
        cap = HardwareCapacity(
            memory_kind="unified",
            total_memory_gb=8.0,
            backend="mps",
            device="Apple M1",
        )
        sel = select_quant_for_hardware(cap, params_b=3.0)
        assert sel.method in (QuantMethod.Q4_0, QuantMethod.Q4_K_M, QuantMethod.Q8_0)
        assert sel.fits is True

    def test_quant_for_hardware_unified_16gb_larger_model(self):
        cap = HardwareCapacity(
            memory_kind="unified",
            total_memory_gb=16.0,
            backend="mps",
            device="Apple M2",
        )
        sel = select_quant_for_hardware(cap, params_b=7.0)
        assert sel.method in (QuantMethod.Q4_0, QuantMethod.Q4_K_M, QuantMethod.Q8_0)

    def test_quant_for_hardware_discrete_8gb(self):
        cap = HardwareCapacity(
            memory_kind="discrete",
            total_memory_gb=8.0,
            backend="cuda",
            device="RTX 3070",
        )
        sel = select_quant_for_hardware(cap, params_b=7.0)
        assert sel.method in (QuantMethod.Q4_K_M, QuantMethod.Q8_0)

    def test_quant_for_hardware_unified_32gb_prefers_q8(self):
        cap = HardwareCapacity(
            memory_kind="unified",
            total_memory_gb=32.0,
            backend="mps",
            device="Apple M3 Max",
        )
        sel = select_quant_for_hardware(cap, params_b=3.0)
        assert sel.method == QuantMethod.Q8_0
        assert sel.fits is True

    def test_quant_rejects_too_large_model(self):
        cap = HardwareCapacity(
            memory_kind="discrete",
            total_memory_gb=4.0,
            backend="cuda",
            device="GT 1030",
        )
        sel = select_quant_for_hardware(cap, params_b=13.0)
        assert sel.fits is False
        assert sel.method is not None

    def test_quant_unknown_capacity_fails_closed(self):
        cap = HardwareCapacity(
            memory_kind="unknown",
            total_memory_gb=0.0,
            backend="unknown",
            device="unknown",
        )
        sel = select_quant_for_hardware(cap, params_b=3.0)
        assert sel.fits is False


class TestModelQuantizerInit:
    def test_default_quantizer_has_required_methods(self):
        with (
            patch("shutil.which", return_value=None),
            patch.object(ModelQuantizer, "_find_bundled_llama_quantize", return_value=None),
        ):
            q = ModelQuantizer()
        assert hasattr(q, "convert_to_gguf")
        assert hasattr(q, "quantize")
        assert hasattr(q, "available_methods")
        assert q.llama_cpp_quantize_path is None

    def test_with_llama_cpp_path(self):
        q = ModelQuantizer(llama_cpp_quantize_path="/usr/local/bin/llama-quantize")
        assert q.llama_cpp_quantize_path == "/usr/local/bin/llama-quantize"

    def test_available_methods_returns_set(self):
        q = ModelQuantizer(llama_cpp_quantize_path="/usr/local/bin/llama-quantize")
        methods = q.available_methods()
        assert isinstance(methods, set)
        assert QuantMethod.Q4_0 in methods
        assert QuantMethod.Q4_K_M in methods
        assert QuantMethod.Q8_0 in methods

    def test_available_methods_requires_quantize_tool_for_local(self):
        with (
            patch("shutil.which", return_value=None),
            patch.object(ModelQuantizer, "_find_bundled_llama_quantize", return_value=None),
        ):
            q = ModelQuantizer(llama_cpp_quantize_path=None)
        assert q._can_quantize_locally() is False


class TestModelQuantizerConvertGGUF:
    def test_convert_no_input_raises(self):
        q = ModelQuantizer()
        try:
            q.convert_to_gguf("", "/tmp/out.gguf")
        except ValueError:
            pass
        else:
            raise AssertionError("Expected ValueError for empty input")

    def test_convert_to_gguf_builds_command(self):
        q = ModelQuantizer(
            convert_script_path="/usr/local/bin/convert_hf_to_gguf.py",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, "model")
            os.makedirs(input_path)
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                result = q.convert_to_gguf(input_path, os.path.join(tmpdir, "out.gguf"))
                assert result is True
                mock_run.assert_called_once()

    def test_convert_to_gguf_failure_returns_false(self):
        q = ModelQuantizer(
            convert_script_path="/usr/local/bin/convert_hf_to_gguf.py",
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            result = q.convert_to_gguf("/tmp/model", "/tmp/model.gguf")
            assert result is False

    def test_convert_output_dir_created(self):
        q = ModelQuantizer(
            convert_script_path="/usr/local/bin/convert_hf_to_gguf.py",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, "model")
            os.makedirs(input_path)
            out_path = os.path.join(tmpdir, "subdir", "out.gguf")
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                result = q.convert_to_gguf(input_path, out_path)
                assert result is True
                assert Path(out_path).parent.exists()


class TestModelQuantizerQuantize:
    def test_quantize_no_llama_cpp_returns_false(self):
        q = ModelQuantizer(llama_cpp_quantize_path=None)
        assert q.quantize("/tmp/model.gguf", "/tmp/model-q4_0.gguf", QuantMethod.Q4_0) is False

    def test_quantize_builds_command(self):
        q = ModelQuantizer(llama_cpp_quantize_path="/usr/local/bin/llama-quantize")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = q.quantize("/tmp/model.gguf", "/tmp/model-q4_0.gguf", QuantMethod.Q4_0)
            assert result is True
            mock_run.assert_called_once()

    def test_quantize_invalid_method_raises(self):
        q = ModelQuantizer(llama_cpp_quantize_path="/usr/local/bin/llama-quantize")
        try:
            q.quantize("/tmp/model.gguf", "/tmp/out.gguf", QuantMethod.FP16)
        except ValueError:
            pass
        else:
            raise AssertionError("Expected ValueError for FP16 quantize (not a llama.cpp quant type)")

    def test_quantize_failure_returns_false(self):
        q = ModelQuantizer(llama_cpp_quantize_path="/usr/local/bin/llama-quantize")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = q.quantize("/tmp/model.gguf", "/tmp/model-q8_0.gguf", QuantMethod.Q8_0)
            assert result is True


class TestEndToEndPipeline:
    def test_full_pipeline_steps(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, "model")
            os.makedirs(input_path)
            f16_gguf = os.path.join(tmpdir, "model-f16.gguf")
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                q = ModelQuantizer(
                    convert_script_path="/usr/local/bin/convert_hf_to_gguf.py",
                    llama_cpp_quantize_path="/usr/local/bin/llama-quantize",
                )

                cap = HardwareCapacity(
                    memory_kind="unified",
                    total_memory_gb=16.0,
                    backend="mps",
                    device="Apple M2",
                )
                sel = select_quant_for_hardware(cap, params_b=1.5)

                converted = q.convert_to_gguf(input_path, f16_gguf)
                assert converted is True

                quantized = q.quantize(
                    f16_gguf,
                    os.path.join(tmpdir, f"model-{sel.method.value}.gguf"),
                    sel.method,
                )
                assert quantized is True

    def test_convert_then_quantize_q4_k_m(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, "model")
            os.makedirs(input_path)
            f16_gguf = os.path.join(tmpdir, "model-f16.gguf")
            q4_gguf = os.path.join(tmpdir, "model-q4_K_M.gguf")
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                q = ModelQuantizer(
                    convert_script_path="/usr/local/bin/convert_hf_to_gguf.py",
                    llama_cpp_quantize_path="/usr/local/bin/llama-quantize",
                )
                assert q.convert_to_gguf(input_path, f16_gguf) is True
                assert q.quantize(f16_gguf, q4_gguf, QuantMethod.Q4_K_M) is True

    def test_convert_then_quantize_q8_0(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, "model")
            os.makedirs(input_path)
            f16_gguf = os.path.join(tmpdir, "model-f16.gguf")
            q8_gguf = os.path.join(tmpdir, "model-q8_0.gguf")
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                q = ModelQuantizer(
                    convert_script_path="/usr/local/bin/convert_hf_to_gguf.py",
                    llama_cpp_quantize_path="/usr/local/bin/llama-quantize",
                )
                assert q.convert_to_gguf(input_path, f16_gguf) is True
                assert q.quantize(f16_gguf, q8_gguf, QuantMethod.Q8_0) is True
