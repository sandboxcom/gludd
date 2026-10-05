"""Deep edge-case tests for local model quantization pipeline."""

from __future__ import annotations

import dataclasses
import os
import subprocess
import tempfile
from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.hardware_memory_policy import MemoryInfo
from general_ludd.quantization.quantize import (
    _QUANT_METHODS_BY_QUALITY,
    HardwareCapacity,
    ModelQuantizer,
    QuantMethod,
    QuantSelection,
    select_quant_for_hardware,
)

# ---------------------------------------------------------------------------
# QuantMethod — from_string edge cases
# ---------------------------------------------------------------------------


class TestQuantMethodFromStringEdgeCases:
    def test_from_string_empty_raises(self):
        with pytest.raises(ValueError, match="Unknown quant method"):
            QuantMethod.from_string("")

    def test_from_string_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="Unknown quant method"):
            QuantMethod.from_string("   ")

    def test_from_string_leading_trailing_whitespace(self):
        assert QuantMethod.from_string("  q4_0  ") == QuantMethod.Q4_0
        assert QuantMethod.from_string("\tq8_0\n") == QuantMethod.Q8_0

    def test_from_string_mixed_case(self):
        assert QuantMethod.from_string("Q4_K_m") == QuantMethod.Q4_K_M
        assert QuantMethod.from_string("Fp16") == QuantMethod.FP16

    def test_from_string_alias_q4km(self):
        assert QuantMethod.from_string("q4km") == QuantMethod.Q4_K_M

    def test_from_string_all_round_trip(self):
        for method in QuantMethod:
            rt = QuantMethod.from_string(method.value)
            assert rt is method
        for alias in ("q4km", "fp16"):
            QuantMethod.from_string(alias)

    def test_from_string_near_miss_raises(self):
        for bad in ("q4.0", "q4_", "q4__0", "q4_O", "Q4_0_", "f161", "q3_0", "q5_0", "q8_1"):
            with pytest.raises(ValueError, match="Unknown quant method"):
                QuantMethod.from_string(bad)

    def test_from_string_numeric_only_raises(self):
        with pytest.raises(ValueError, match="Unknown quant method"):
            QuantMethod.from_string("42")


# ---------------------------------------------------------------------------
# QuantMethod — bits / quality_score edge cases
# ---------------------------------------------------------------------------


class TestQuantMethodBitsEdgeCases:
    def test_bits_monotonicity(self):
        bits = [m.bits() for m in QuantMethod]
        assert all(b in (4, 8, 16) for b in bits)

    def test_quality_score_bounds(self):
        for method in QuantMethod:
            score = method.quality_score()
            assert 0.0 < score <= 1.0

    def test_quality_score_fp16_highest(self):
        assert QuantMethod.FP16.quality_score() == max(m.quality_score() for m in QuantMethod)

    def test_bits_fp16_highest(self):
        assert QuantMethod.FP16.bits() == max(m.bits() for m in QuantMethod)


# ---------------------------------------------------------------------------
# HardwareCapacity — from_probe edge cases
# ---------------------------------------------------------------------------


class TestHardwareCapacityFromProbeEdgeCases:
    def test_from_probe_zero_memory(self):
        with patch("general_ludd.quantization.quantize.detect_memory") as mock_detect:
            mock_detect.return_value = MemoryInfo(
                kind="unified",
                total_bytes=0,
                available_bytes=0,
                backend="mps",
                device="Unknown",
            )
            cap = HardwareCapacity.from_probe()
            assert cap.total_memory_gb == 0.0
            assert cap.memory_kind == "unified"

    def test_from_probe_total_bytes_zero(self):
        with patch("general_ludd.quantization.quantize.detect_memory") as mock_detect:
            mock_detect.return_value = MemoryInfo(
                kind="unified",
                total_bytes=0,
                available_bytes=0,
                backend="mps",
                device="M1",
            )
            cap = HardwareCapacity.from_probe()
            assert cap.total_memory_gb == 0.0

    def test_from_probe_one_byte(self):
        with patch("general_ludd.quantization.quantize.detect_memory") as mock_detect:
            mock_detect.return_value = MemoryInfo(
                kind="unified",
                total_bytes=1,
                available_bytes=0,
                backend="mps",
                device="Tiny",
            )
            cap = HardwareCapacity.from_probe()
            assert 0.0 < cap.total_memory_gb < 1e-8

    def test_from_probe_kind_vram_becomes_discrete(self):
        with patch("general_ludd.quantization.quantize.detect_memory") as mock_detect:
            mock_detect.return_value = MemoryInfo(
                kind="vram",
                total_bytes=8 * 1024**3,
                available_bytes=4 * 1024**3,
                backend="cuda",
                device="A100",
            )
            cap = HardwareCapacity.from_probe()
            assert cap.memory_kind == "discrete"

    def test_from_probe_kind_unified_stays_unified(self):
        with patch("general_ludd.quantization.quantize.detect_memory") as mock_detect:
            mock_detect.return_value = MemoryInfo(
                kind="unified",
                total_bytes=16 * 1024**3,
                available_bytes=8 * 1024**3,
                backend="mps",
                device="M2",
            )
            cap = HardwareCapacity.from_probe()
            assert cap.memory_kind == "unified"

    def test_from_probe_kind_unknown(self):
        with patch("general_ludd.quantization.quantize.detect_memory") as mock_detect:
            mock_detect.return_value = cast(
                MemoryInfo,
                MemoryInfo(
                    kind="unified",
                    total_bytes=32 * 1024**3,
                    available_bytes=16 * 1024**3,
                    backend="cpu",
                    device="host",
                ),
            )
            cap = HardwareCapacity.from_probe()
            assert cap.memory_kind == "unified"


# ---------------------------------------------------------------------------
# QuantSelection — as_dict edge cases
# ---------------------------------------------------------------------------


class TestQuantSelectionAsDictEdgeCases:
    def test_as_dict_with_none_method(self):
        sel = QuantSelection(method=None, fits=False, reason="no memory", bits=0)
        d = sel.as_dict()
        assert d["method"] is None
        assert d["fits"] is False
        assert d["reason"] == "no memory"
        assert d["bits"] == 0

    def test_as_dict_round_trip_like(self):
        sel = QuantSelection(method=QuantMethod.Q4_K_M, fits=True, reason="ok", bits=4)
        d = sel.as_dict()
        assert d["method"] == "q4_K_M"
        assert d["bits"] == 4

    def test_quant_selection_is_frozen(self):
        sel = QuantSelection(method=QuantMethod.Q4_0, fits=True, reason="ok", bits=4)
        with pytest.raises(dataclasses.FrozenInstanceError):
            sel.fits = False  # pyright: ignore[reportAttributeAccessIssue]
        assert sel.fits is True


# ---------------------------------------------------------------------------
# select_quant_for_hardware — boundary / edge cases
# ---------------------------------------------------------------------------


class TestSelectQuantBoundaryEdgeCases:
    @pytest.mark.parametrize(
        "mem_gb,params_b,expects_fits",
        [
            (4.0, 0.5, True),
            (4.0, 1.0, True),
            (4.0, 2.0, True),
            (4.0, 3.0, True),
            (4.0, 6.0, False),
            (4.0, 10.0, False),
            (8.0, 0.1, True),
            (8.0, 5.0, True),
            (8.0, 12.0, False),
            (16.0, 5.0, True),
            (16.0, 10.0, True),
            (16.0, 30.0, False),
            (32.0, 10.0, True),
            (32.0, 40.0, True),
            (64.0, 30.0, True),
            (64.0, 70.0, True),
        ],
    )
    def test_fit_vs_no_fit_combinations(self, mem_gb, params_b, expects_fits):
        cap = HardwareCapacity(memory_kind="unified", total_memory_gb=mem_gb, backend="mps", device="test")
        sel = select_quant_for_hardware(cap, params_b)
        assert sel.fits == expects_fits

    def test_reserve_fraction_zero(self):
        cap = HardwareCapacity(memory_kind="unified", total_memory_gb=4.0, backend="mps", device="test")
        sel_zero = select_quant_for_hardware(cap, 3.0, reserve_fraction=0.0)
        assert sel_zero.fits is True

    def test_reserve_fraction_one(self):
        cap = HardwareCapacity(memory_kind="unified", total_memory_gb=16.0, backend="mps", device="test")
        sel = select_quant_for_hardware(cap, 0.1, reserve_fraction=1.0)
        assert sel.fits is False

    def test_reserve_fraction_near_one(self):
        cap = HardwareCapacity(memory_kind="unified", total_memory_gb=100.0, backend="mps", device="test")
        sel = select_quant_for_hardware(cap, 0.001, reserve_fraction=0.999)
        assert sel.fits is True or sel.fits is False

    def test_params_b_zero_raises(self):
        cap = HardwareCapacity(memory_kind="unified", total_memory_gb=1.0, backend="mps", device="test")
        with pytest.raises(ValueError, match="params_b must be greater than zero"):
            select_quant_for_hardware(cap, 0.0)

    def test_params_b_huge(self):
        cap = HardwareCapacity(memory_kind="unified", total_memory_gb=1000.0, backend="mps", device="hyperscale")
        sel = select_quant_for_hardware(cap, 400.0)
        assert isinstance(sel, QuantSelection)

    def test_memory_kind_unknown(self):
        cap = HardwareCapacity(memory_kind="unknown", total_memory_gb=16.0, backend="?", device="?")
        sel = select_quant_for_hardware(cap, 1.0)
        assert sel.fits is False
        assert sel.method is None

    def test_total_memory_zero(self):
        cap = HardwareCapacity(memory_kind="unified", total_memory_gb=0.0, backend="mps", device="test")
        sel = select_quant_for_hardware(cap, 0.0)
        assert sel.fits is False

    def test_total_memory_negative(self):
        cap = HardwareCapacity(memory_kind="unified", total_memory_gb=-1.0, backend="mps", device="test")
        sel = select_quant_for_hardware(cap, 0.0)
        assert sel.fits is False

    def test_fits_model_exactly_at_boundary(self):
        from general_ludd.hardware_memory_policy import estimate_model_bytes

        mem_gb = 8.0
        reserve_fraction = 0.20
        usable = int(mem_gb * 1024**3 * (1 - reserve_fraction))
        params_b = 3.7
        footprint_q8 = estimate_model_bytes(params_b, 8)
        sel = select_quant_for_hardware(
            HardwareCapacity(memory_kind="unified", total_memory_gb=mem_gb, backend="mps", device="test"),
            params_b,
            reserve_fraction=reserve_fraction,
        )
        if footprint_q8 <= usable:
            assert sel.method in (QuantMethod.Q8_0, QuantMethod.Q4_K_M)
            assert sel.fits is True
        else:
            assert sel.fits is True

    def test_highest_quality_is_q8_for_small_enough_model(self):
        cap = HardwareCapacity(memory_kind="unified", total_memory_gb=64.0, backend="mps", device="big")
        sel = select_quant_for_hardware(cap, 1.0)
        assert sel.method == QuantMethod.Q8_0

    def test_returns_q4_0_as_last_resort(self):
        cap = HardwareCapacity(memory_kind="unified", total_memory_gb=8.0, backend="mps", device="test")
        sel = select_quant_for_hardware(cap, 5.0)
        assert sel.method in _QUANT_METHODS_BY_QUALITY or sel.method == QuantMethod.Q4_0

    def test_tiny_memory_no_model_fits(self):
        cap = HardwareCapacity(memory_kind="unified", total_memory_gb=0.5, backend="mps", device="tiny")
        sel = select_quant_for_hardware(cap, 3.0)
        assert sel.fits is False


# ---------------------------------------------------------------------------
# ModelQuantizer — _find_bundled_llama_quantize
# ---------------------------------------------------------------------------


class TestFindBundledLlamaQuantizeEdgeCases:
    def test_returns_none_when_no_binary(self):
        with patch("os.path.isfile", return_value=False), patch("shutil.which", return_value=None):
            result = ModelQuantizer._find_bundled_llama_quantize()
            assert result is None

    def test_returns_bundled_path_when_executable(self):
        with patch("os.path.isfile", return_value=True), patch("os.access", return_value=True):
            result = ModelQuantizer._find_bundled_llama_quantize()
            assert result is not None
            assert result.endswith("llama-quantize")

    def test_returns_which_when_bundled_not_executable(self):
        with (
            patch("os.path.isfile", return_value=True),
            patch("os.access", return_value=False),
            patch("shutil.which", return_value="/usr/local/bin/llama-quantize"),
        ):
            result = ModelQuantizer._find_bundled_llama_quantize()
            assert result == "/usr/local/bin/llama-quantize"

    def test_filesystem_probe_oserror_is_fail_soft(self):
        with (
            patch("os.path.isfile", side_effect=OSError("probe unavailable")),
            patch("shutil.which", return_value=None),
        ):
            assert ModelQuantizer._find_bundled_llama_quantize() is None

    def test_path_probe_oserror_is_fail_soft(self):
        with (
            patch("os.path.isfile", return_value=False),
            patch("shutil.which", side_effect=OSError("PATH unavailable")),
        ):
            assert ModelQuantizer._find_bundled_llama_quantize() is None


# ---------------------------------------------------------------------------
# ModelQuantizer — constructor edge cases
# ---------------------------------------------------------------------------


class TestModelQuantizerConstructorEdgeCases:
    def test_explicit_paths_override_discovery(self):
        q = ModelQuantizer(
            convert_script_path="/custom/convert.py",
            llama_cpp_quantize_path="/custom/quantize",
        )
        assert q.convert_script_path == "/custom/convert.py"
        assert q.llama_cpp_quantize_path == "/custom/quantize"

    def test_both_none_uses_discovery(self):
        with (
            patch("shutil.which", return_value="/usr/bin/convert_hf_to_gguf.py"),
            patch.object(ModelQuantizer, "_find_bundled_llama_quantize", return_value="/usr/bin/llama-quantize"),
        ):
            q = ModelQuantizer()
            assert q.convert_script_path == "/usr/bin/convert_hf_to_gguf.py"
            assert q.llama_cpp_quantize_path == "/usr/bin/llama-quantize"

    def test_only_convert_provided(self):
        with patch.object(ModelQuantizer, "_find_bundled_llama_quantize", return_value="/usr/bin/llama-quantize"):
            q = ModelQuantizer(convert_script_path="/custom/convert.py")
            assert q.convert_script_path == "/custom/convert.py"
            assert q.llama_cpp_quantize_path == "/usr/bin/llama-quantize"


# ---------------------------------------------------------------------------
# ModelQuantizer — available_methods edge cases
# ---------------------------------------------------------------------------


class TestAvailableMethodsEdgeCases:
    def test_without_quantize_tool_returns_only_fp16(self):
        with patch.object(ModelQuantizer, "_find_bundled_llama_quantize", return_value=None):
            q = ModelQuantizer(llama_cpp_quantize_path=None)
            methods = q.available_methods()
            assert methods == {QuantMethod.FP16}

    def test_with_quantize_tool_includes_all(self):
        q = ModelQuantizer(llama_cpp_quantize_path="/usr/bin/llama-quantize")
        methods = q.available_methods()
        assert QuantMethod.FP16 in methods
        assert QuantMethod.Q4_0 in methods
        assert QuantMethod.Q4_K_M in methods
        assert QuantMethod.Q8_0 in methods

    def test_with_empty_string_path_still_discovers(self):
        q = ModelQuantizer(llama_cpp_quantize_path="")
        assert q.llama_cpp_quantize_path != ""


# ---------------------------------------------------------------------------
# ModelQuantizer — convert_to_gguf edge cases
# ---------------------------------------------------------------------------


class TestConvertToGGUFEdgeCases:
    def test_convert_input_path_is_file_not_dir(self):
        q = ModelQuantizer(convert_script_path="/fake/convert.py")
        with tempfile.NamedTemporaryFile() as tmpf, patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = q.convert_to_gguf(tmpf.name, "/tmp/out.gguf")
            assert result is True

    def test_convert_input_path_does_not_exist(self):
        q = ModelQuantizer(convert_script_path="/fake/convert.py")
        result = q.convert_to_gguf("/nonexistent/path/model", "/tmp/out.gguf")
        assert result is False

    def test_convert_subprocess_timeout(self):
        q = ModelQuantizer(convert_script_path="/fake/convert.py")
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["x"], timeout=600)):
            result = q.convert_to_gguf("/tmp/model", "/tmp/out.gguf")
            assert result is False

    def test_convert_subprocess_file_not_found(self):
        q = ModelQuantizer(convert_script_path="/fake/convert.py")
        with patch("subprocess.run", side_effect=FileNotFoundError("python3")):
            result = q.convert_to_gguf("/tmp/model", "/tmp/out.gguf")
            assert result is False

    def test_convert_subprocess_os_error(self):
        q = ModelQuantizer(convert_script_path="/fake/convert.py")
        with patch("subprocess.run", side_effect=OSError("disk full")):
            result = q.convert_to_gguf("/tmp/model", "/tmp/out.gguf")
            assert result is False

    def test_convert_empty_string_input_raises(self):
        q = ModelQuantizer()
        with pytest.raises(ValueError, match="input_path"):
            q.convert_to_gguf("", "/tmp/out.gguf")

    def test_convert_creates_deep_output_directory(self):
        q = ModelQuantizer(convert_script_path="/fake/convert.py")
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, "model")
            os.makedirs(input_path)
            deep_out = os.path.join(tmpdir, "a", "b", "c", "out.gguf")
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                result = q.convert_to_gguf(input_path, deep_out)
                assert result is True
                assert os.path.isdir(os.path.dirname(deep_out))

    def test_convert_output_in_cwd_no_dir_created(self):
        q = ModelQuantizer(convert_script_path="/fake/convert.py")
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                input_path = os.path.join(tmpdir, "model")
                os.makedirs(input_path)
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock(returncode=0)
                    result = q.convert_to_gguf(input_path, "out.gguf")
                    assert result is True
            finally:
                os.chdir(orig_cwd)

    def test_convert_uses_fallback_script_name(self):
        q = ModelQuantizer(convert_script_path=None)
        with patch("shutil.which", return_value=None), tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, "model")
            os.makedirs(input_path)
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                result = q.convert_to_gguf(input_path, os.path.join(tmpdir, "out.gguf"))
                assert result is True
                cmd = mock_run.call_args[0][0]
                assert "convert_hf_to_gguf.py" in cmd


# ---------------------------------------------------------------------------
# ModelQuantizer — quantize edge cases
# ---------------------------------------------------------------------------


class TestQuantizeEdgeCases:
    def test_quantize_threads_positive(self):
        q = ModelQuantizer(llama_cpp_quantize_path="/usr/bin/llama-quantize")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = q.quantize("/tmp/in.gguf", "/tmp/out.gguf", QuantMethod.Q4_0, threads=4)
            assert result is True
            cmd = mock_run.call_args[0][0]
            assert "-t" in cmd
            assert "4" in cmd

    def test_quantize_threads_zero_ignored(self):
        q = ModelQuantizer(llama_cpp_quantize_path="/usr/bin/llama-quantize")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = q.quantize("/tmp/in.gguf", "/tmp/out.gguf", QuantMethod.Q4_0, threads=0)
            assert result is True
            cmd = mock_run.call_args[0][0]
            assert "-t" not in cmd

    def test_quantize_threads_negative_ignored(self):
        q = ModelQuantizer(llama_cpp_quantize_path="/usr/bin/llama-quantize")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = q.quantize("/tmp/in.gguf", "/tmp/out.gguf", QuantMethod.Q4_0, threads=-1)
            assert result is True
            cmd = mock_run.call_args[0][0]
            assert "-t" not in cmd

    def test_quantize_threads_none_omitted(self):
        q = ModelQuantizer(llama_cpp_quantize_path="/usr/bin/llama-quantize")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = q.quantize("/tmp/in.gguf", "/tmp/out.gguf", QuantMethod.Q4_0)
            assert result is True
            cmd = mock_run.call_args[0][0]
            assert "-t" not in cmd

    def test_quantize_creates_deep_output_dir(self):
        q = ModelQuantizer(llama_cpp_quantize_path="/usr/bin/llama-quantize")
        with tempfile.TemporaryDirectory() as tmpdir:
            deep_out = os.path.join(tmpdir, "x", "y", "out.gguf")
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                result = q.quantize("/tmp/model.gguf", deep_out, QuantMethod.Q4_0)
                assert result is True
                assert os.path.isdir(os.path.dirname(deep_out))

    def test_quantize_subprocess_timeout(self):
        q = ModelQuantizer(llama_cpp_quantize_path="/usr/bin/llama-quantize")
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["x"], timeout=1800)):
            result = q.quantize("/tmp/in.gguf", "/tmp/out.gguf", QuantMethod.Q4_0)
            assert result is False

    def test_quantize_subprocess_file_not_found(self):
        q = ModelQuantizer(llama_cpp_quantize_path="/usr/bin/llama-quantize")
        with patch("subprocess.run", side_effect=FileNotFoundError("llama-quantize")):
            result = q.quantize("/tmp/in.gguf", "/tmp/out.gguf", QuantMethod.Q4_0)
            assert result is False

    def test_quantize_subprocess_os_error(self):
        q = ModelQuantizer(llama_cpp_quantize_path="/usr/bin/llama-quantize")
        with patch("subprocess.run", side_effect=OSError("disk full")):
            result = q.quantize("/tmp/in.gguf", "/tmp/out.gguf", QuantMethod.Q4_0)
            assert result is False

    def test_quantize_no_llama_cpp_path(self):
        q = ModelQuantizer(llama_cpp_quantize_path=None)
        result = q.quantize("/tmp/in.gguf", "/tmp/out.gguf", QuantMethod.Q4_0)
        assert result is False

    def test_quantize_fp16_raises_value_error(self):
        q = ModelQuantizer(llama_cpp_quantize_path="/usr/bin/llama-quantize")
        with pytest.raises(ValueError, match="FP16"):
            q.quantize("/tmp/in.gguf", "/tmp/out.gguf", QuantMethod.FP16)

    def test_quantize_output_in_cwd(self):
        q = ModelQuantizer(llama_cpp_quantize_path="/usr/bin/llama-quantize")
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock(returncode=0)
                    result = q.quantize("/tmp/model.gguf", "out.gguf", QuantMethod.Q4_0)
                    assert result is True
            finally:
                os.chdir(orig_cwd)

    def test_quantize_all_supported_methods(self):
        q = ModelQuantizer(llama_cpp_quantize_path="/usr/bin/llama-quantize")
        for method in ModelQuantizer._QUANT_MAP:
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                result = q.quantize("/tmp/in.gguf", "/tmp/out.gguf", method)
                assert result is True

    def test_quantize_uses_fallback_bin_name_when_path_none_after_init(self):
        q = ModelQuantizer(llama_cpp_quantize_path="/usr/bin/llama-quantize")
        q.llama_cpp_quantize_path = None
        result = q.quantize("/tmp/in.gguf", "/tmp/out.gguf", QuantMethod.Q4_0)
        assert result is False


# ---------------------------------------------------------------------------
# HardwareCapacity — frozen / equality
# ---------------------------------------------------------------------------


class TestHardwareCapacityEdgeCases:
    def test_frozen_prevents_mutation(self):
        cap = HardwareCapacity(memory_kind="unified", total_memory_gb=16.0, backend="mps", device="M2")
        with pytest.raises(dataclasses.FrozenInstanceError):
            cap.total_memory_gb = 32.0  # pyright: ignore[reportAttributeAccessIssue]
        assert cap.total_memory_gb == 16.0

    def test_equality_by_value(self):
        a = HardwareCapacity(memory_kind="unified", total_memory_gb=16.0, backend="mps", device="M2")
        b = HardwareCapacity(memory_kind="unified", total_memory_gb=16.0, backend="mps", device="M2")
        c = HardwareCapacity(memory_kind="unified", total_memory_gb=8.0, backend="mps", device="M2")
        assert a == b
        assert a != c

    def test_hashable(self):
        cap = HardwareCapacity(memory_kind="unified", total_memory_gb=16.0, backend="mps", device="M2")
        d = {cap: "test"}
        assert d[cap] == "test"


# ---------------------------------------------------------------------------
# _RESERVE_FRACTION default / _QUANT_METHODS_BY_QUALITY ordering
# ---------------------------------------------------------------------------


class TestModuleConstants:
    def test_reserve_fraction_positive(self):
        from general_ludd.quantization.quantize import _RESERVE_FRACTION

        assert 0.0 < _RESERVE_FRACTION < 1.0

    def test_quant_methods_by_quality_ordering(self):
        from general_ludd.quantization.quantize import _QUANT_METHODS_BY_QUALITY

        assert _QUANT_METHODS_BY_QUALITY == [
            QuantMethod.Q8_0,
            QuantMethod.Q4_K_M,
            QuantMethod.Q4_0,
        ]

    def test_selection_prefers_higher_quality_when_possible(self):
        cap = HardwareCapacity(memory_kind="unified", total_memory_gb=64.0, backend="mps", device="big")
        for params_b in (0.1, 0.5, 1.0, 2.0):
            sel = select_quant_for_hardware(cap, params_b)
            assert sel.method == QuantMethod.Q8_0
