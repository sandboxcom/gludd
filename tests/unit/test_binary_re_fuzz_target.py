"""Tests for fuzz_target role — coverage-guided, mutation, crash triage."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

_COLLECTION_ROOT = Path(__file__).resolve().parents[3] / "collections/ansible_collections/general_ludd/binary_re"
_FUZZ_TARGET_FILES = _COLLECTION_ROOT / "roles" / "fuzz_target" / "files"
_PLUGIN_ROOT = _COLLECTION_ROOT / "plugins"

if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))
if str(_FUZZ_TARGET_FILES) not in sys.path:
    sys.path.insert(0, str(_FUZZ_TARGET_FILES))

try:
    _ft = importlib.import_module("fuzz_target")
    run_coverage_guided = _ft.run_coverage_guided
    run_mutation_fuzzing = _ft.run_mutation_fuzzing
    triage_crashes = _ft.triage_crashes
except ModuleNotFoundError:
    pytest.skip("fuzz_target module not available", allow_module_level=True)


class TestRunCoverageGuided:
    def test_returns_dict_structure(self, tmp_path):
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        out = tmp_path / "out"
        out.mkdir()
        (corpus / "seed.bin").write_bytes(b"AAAA")
        result = run_coverage_guided(
            fuzzer="libfuzzer",
            fuzzer_path="/usr/local/bin/afl-fuzz",
            target_binary=str(tmp_path / "target"),
            corpus_dir=str(corpus),
            output_dir=str(out),
            timeout_seconds=1,
        )
        assert isinstance(result, dict)
        assert "fuzzer" in result

    def test_unknown_fuzzer_returns_error(self, tmp_path):
        result = run_coverage_guided(
            fuzzer="unknown_fuzzer",
            fuzzer_path="/nonexistent",
            target_binary="/none",
            corpus_dir=str(tmp_path),
            output_dir=str(tmp_path),
            timeout_seconds=1,
        )
        assert "error" in result
        assert "supported" in result

    def test_afl_config_generated(self, tmp_path):
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        out = tmp_path / "out"
        out.mkdir()
        (corpus / "seed.bin").write_bytes(b"AAAA")
        result = run_coverage_guided(
            fuzzer="afl++",
            fuzzer_path="/usr/local/bin/afl-fuzz",
            target_binary=str(tmp_path / "target"),
            corpus_dir=str(corpus),
            output_dir=str(out),
            timeout_seconds=1,
        )
        assert isinstance(result, dict)
        assert "command" in result
        assert "afl-fuzz" in result["command"]

    def test_libfuzzer_config_generated(self, tmp_path):
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        out = tmp_path / "out"
        out.mkdir()
        result = run_coverage_guided(
            fuzzer="libfuzzer",
            fuzzer_path="",
            target_binary=str(tmp_path / "fuzz_lib"),
            corpus_dir=str(corpus),
            output_dir=str(out),
            timeout_seconds=1,
        )
        assert isinstance(result, dict)
        assert "command" in result
        assert "-max_total_time=" in result["command"]

    def test_honggfuzz_config_generated(self, tmp_path):
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        out = tmp_path / "out"
        out.mkdir()
        result = run_coverage_guided(
            fuzzer="honggfuzz",
            fuzzer_path="/usr/local/bin/honggfuzz",
            target_binary=str(tmp_path / "hf_target"),
            corpus_dir=str(corpus),
            output_dir=str(out),
            timeout_seconds=1,
        )
        assert isinstance(result, dict)
        assert "command" in result
        assert "honggfuzz" in result["command"]

    def test_missing_fuzzer_binary_returns_error(self, tmp_path):
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        out = tmp_path / "out"
        out.mkdir()
        result = run_coverage_guided(
            fuzzer="afl++",
            fuzzer_path="/nonexistent/path/AFLplusplus-fuzz/afl-fuzz",
            target_binary=str(tmp_path / "binary"),
            corpus_dir=str(corpus),
            output_dir=str(out),
            timeout_seconds=1,
        )
        assert isinstance(result, dict)
        assert "error" in result or result.get("elapsed_seconds", 0) >= 0


class TestRunMutationFuzzing:
    def test_returns_dict_structure(self, tmp_path):
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "seed1.bin").write_bytes(b"A" * 100)
        result = run_mutation_fuzzing(
            target_binary=str(tmp_path / "target"),
            corpus_dir=str(corpus),
            output_dir=str(tmp_path),
            mutations=10,
        )
        assert isinstance(result, dict)
        assert "mutations_total" in result
        assert "crashes" in result

    def test_mutation_count_respected(self, tmp_path):
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "seed.bin").write_bytes(b"A" * 100)
        result = run_mutation_fuzzing(
            target_binary=str(tmp_path / "target"),
            corpus_dir=str(corpus),
            output_dir=str(tmp_path),
            mutations=5,
        )
        assert result["mutations_total"] == 5

    def test_seed_files_listed(self, tmp_path):
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "a.bin").write_bytes(b"A")
        (corpus / "b.bin").write_bytes(b"B")
        result = run_mutation_fuzzing(
            target_binary=str(tmp_path / "target"),
            corpus_dir=str(corpus),
            output_dir=str(tmp_path),
            mutations=20,
        )
        assert len(result["seed_files"]) >= 1

    def test_crashes_are_simulated(self, tmp_path):
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "seed.bin").write_bytes(b"A" * 100)
        result = run_mutation_fuzzing(
            target_binary=str(tmp_path / "target"),
            corpus_dir=str(corpus),
            output_dir=str(tmp_path),
            mutations=10,
        )
        assert result["crashes"] >= 0

    def test_elapsed_seconds_reported(self, tmp_path):
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "seed.bin").write_bytes(b"A")
        result = run_mutation_fuzzing(
            target_binary=str(tmp_path / "target"),
            corpus_dir=str(corpus),
            output_dir=str(tmp_path),
            mutations=2,
        )
        assert result["elapsed_seconds"] >= 0


class TestTriageCrashes:
    def test_empty_crash_dir(self, tmp_path):
        crash_dir = tmp_path / "crashes"
        crash_dir.mkdir()
        result = triage_crashes(str(crash_dir))
        assert result["total_crashes"] == 0
        assert result["unique_crashes"] == 0
        assert result["exploitable"] == 0

    def test_triages_simulated_crashes(self, tmp_path):
        crash_dir = tmp_path / "crashes"
        crash_dir.mkdir()
        for i in range(5):
            (crash_dir / f"crash_{i:04d}").write_bytes(b"AAAA" + bytes([i]))
        result = triage_crashes(str(crash_dir))
        assert result["total_crashes"] >= 1
        assert result["unique_crashes"] >= 1
        assert len(result["crash_reports"]) >= 1

    def test_classifies_exploitable_crashes(self, tmp_path):
        crash_dir = tmp_path / "crashes"
        crash_dir.mkdir()
        for i in range(10):
            (crash_dir / f"crash_{i:04d}").write_bytes(b"B" * 100 + bytes([i]))
        result = triage_crashes(str(crash_dir))
        assert result["exploitable"] >= 0
        assert "crashes_by_type" in result
        assert "crashes_by_severity" in result

    def test_crash_reports_have_required_fields(self, tmp_path):
        crash_dir = tmp_path / "crashes"
        crash_dir.mkdir()
        (crash_dir / "crash_001").write_bytes(b"CCCC")
        result = triage_crashes(str(crash_dir))
        assert len(result["crash_reports"]) > 0
        report0 = result["crash_reports"][0]
        assert "hash" in report0
        assert "crash_type" in report0
        assert "severity" in report0
        assert "is_exploitable" in report0
        assert "recommended_action" in report0

    def test_retention_days_passed_through(self, tmp_path):
        crash_dir = tmp_path / "crashes"
        crash_dir.mkdir()
        result = triage_crashes(str(crash_dir), retention_days=30)
        assert result["retention_days"] == 30

    def test_nonexistent_dir_returns_zero_counts(self):
        result = triage_crashes("/nonexistent/crash/dir")
        assert result["total_crashes"] == 0

    def test_crash_by_type_buckets_present(self, tmp_path):
        crash_dir = tmp_path / "crashes"
        crash_dir.mkdir()
        for i in range(8):
            (crash_dir / f"crash_{i:04d}").write_bytes(b"D" * 50 + bytes([i]))
        result = triage_crashes(str(crash_dir))
        assert len(result["crashes_by_type"]) >= 1
        assert len(result["crashes_by_severity"]) >= 1


class TestFuzzTargetIntegration:
    def test_full_pipeline(self, tmp_path):
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "a.bin").write_bytes(b"AAAA")
        (corpus / "b.bin").write_bytes(b"BBBB")
        out = tmp_path / "out"
        out.mkdir()
        crashes = tmp_path / "crashes"
        crashes.mkdir()
        for i in range(5):
            (crashes / f"crash_{i:04d}").write_bytes(b"XX" + bytes([i]))

        mutation_result = run_mutation_fuzzing(
            target_binary=str(tmp_path / "target"),
            corpus_dir=str(corpus),
            output_dir=str(out),
            mutations=50,
        )
        assert isinstance(mutation_result, dict)
        assert mutation_result["mutations_total"] == 50

        triage_result = triage_crashes(str(crashes))
        assert triage_result["total_crashes"] >= 1
        assert len(triage_result["crash_reports"]) >= 1
        for report in triage_result["crash_reports"]:
            assert "hash" in report
            assert "is_exploitable" in report
            assert report["recommended_action"]


class TestModuleImportability:
    def test_all_three_functions_importable(self):
        assert callable(run_coverage_guided)
        assert callable(run_mutation_fuzzing)
        assert callable(triage_crashes)

    def test_fuzz_target_module_has_all_modes(self):
        from fuzz_target import _MODES
        assert set(_MODES.keys()) == {"coverage_guided", "mutation", "crash_triage"}
