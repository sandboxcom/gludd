"""Tests for fuzzing_strategies — enums, configs, crash classification, corpus mgmt."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

_COLLECTION_ROOT = Path(__file__).resolve().parents[2] / "collections/ansible_collections/general_ludd/binary_re"
_MODULE_UTILS = _COLLECTION_ROOT / "plugins" / "module_utils"

if str(_MODULE_UTILS) not in sys.path:
    sys.path.insert(0, str(_MODULE_UTILS))

try:
    _fs = importlib.import_module("fuzzing_strategies")
    FuzzingStrategy = _fs.FuzzingStrategy
    CrashSeverity = _fs.CrashSeverity
    CrashBucket = _fs.CrashBucket
    AflPlusPlusConfig = _fs.AflPlusPlusConfig
    CrashInfo = _fs.CrashInfo
    FuzzingJob = _fs.FuzzingJob
    classify_crash = _fs.classify_crash
    seed_selection = _fs.seed_selection
    minimize_corpus = _fs.minimize_corpus
    triage_crash = _fs.triage_crash
    seed_selection_strategy = _fs.seed_selection_strategy
    generate_c_harness = _fs.generate_c_harness
    generate_python_harness = _fs.generate_python_harness
    create_afl_config = _fs.create_afl_config
    create_libfuzzer_config = _fs.create_libfuzzer_config
    create_honggfuzz_config = _fs.create_honggfuzz_config
    CORPUS_SIZE_RULES = _fs.CORPUS_SIZE_RULES
    CRASH_SIGNAL_BUCKETS = _fs.CRASH_SIGNAL_BUCKETS
    CRASH_SEVERITY_SIGNALS = _fs.CRASH_SEVERITY_SIGNALS
    ASAN_PATTERNS = _fs.ASAN_PATTERNS
except ModuleNotFoundError:
    pytest.skip("fuzzing_strategies module not available", allow_module_level=True)


class TestEnums:
    def test_fuzzing_strategy_values(self):
        assert FuzzingStrategy.MUTATION.value == "mutation"
        assert FuzzingStrategy.GENERATION.value == "generation"
        assert FuzzingStrategy.COVERAGE_GUIDED.value == "coverage_guided"
        assert FuzzingStrategy.SYMBOLIC_CONCOLIC.value == "symbolic_concolic"
        assert len(FuzzingStrategy) == 4

    def test_crash_severity_values(self):
        assert CrashSeverity.INFO.value == "info"
        assert CrashSeverity.LOW.value == "low"
        assert CrashSeverity.CRITICAL.value == "critical"
        assert len(CrashSeverity) == 5

    def test_crash_bucket_known_values(self):
        assert CrashBucket.STACK_BUFFER_OVERFLOW.value == "stack_buffer_overflow"
        assert CrashBucket.HEAP_USE_AFTER_FREE.value == "heap_use_after_free"
        assert CrashBucket.NULL_DEREFERENCE.value == "null_dereference"
        assert CrashBucket.UNKNOWN.value == "unknown"
        assert len(CrashBucket) == 19


class TestCrashInfo:
    def test_hash_from_fields(self):
        crash = CrashInfo(
            input_path="/tmp/crash1",
            signal_number=11,
            fault_address="0x00000000",
            crash_type=CrashBucket.NULL_DEREFERENCE,
            severity=CrashSeverity.HIGH,
        )
        assert len(crash.hash) == 16
        assert crash.hash == crash._compute_hash()

    def test_hash_precomputed_not_overwritten(self):
        crash = CrashInfo(
            input_path="/tmp/crash1",
            signal_number=11,
            fault_address="0x0",
            crash_type=CrashBucket.NULL_DEREFERENCE,
            severity=CrashSeverity.HIGH,
            hash="abcdef0123456789",
        )
        assert crash.hash == "abcdef0123456789"

    def test_different_inputs_different_hash(self):
        c1 = CrashInfo(
            input_path="/tmp/a", signal_number=11, fault_address="0x0",
            crash_type=CrashBucket.NULL_DEREFERENCE, severity=CrashSeverity.HIGH,
        )
        c2 = CrashInfo(
            input_path="/tmp/b", signal_number=6, fault_address="0xdead",
            crash_type=CrashBucket.HEAP_BUFFER_OVERFLOW, severity=CrashSeverity.CRITICAL,
        )
        assert c1.hash != c2.hash


class TestClassifyCrash:
    def test_classify_crash_known_signal(self):
        bucket, severity = classify_crash(11, "0x00000000")
        assert bucket == CrashBucket.NULL_DEREFERENCE
        assert severity == CrashSeverity.HIGH

    def test_classify_crash_sigsegv_nonnull(self):
        bucket, severity = classify_crash(11, "0xdeadbeef")
        assert bucket == CrashBucket.STACK_BUFFER_OVERFLOW
        assert severity == CrashSeverity.HIGH

    def test_classify_crash_unknown_signal(self):
        bucket, severity = classify_crash(99, "0x12345")
        assert bucket == CrashBucket.UNKNOWN
        assert severity == CrashSeverity.MEDIUM

    def test_classify_crash_sanitizer_output(self):
        bucket, severity = classify_crash(11, "0x1234",
            sanitizer_output="ERROR: AddressSanitizer: heap-use-after-free on address 0x...")
        assert bucket == CrashBucket.HEAP_USE_AFTER_FREE
        assert severity == CrashSeverity.CRITICAL

    def test_classify_crash_sanitizer_heap_overflow(self):
        bucket, severity = classify_crash(6, "0xbeef",
            sanitizer_output="heap-buffer-overflow")
        assert bucket == CrashBucket.HEAP_BUFFER_OVERFLOW
        assert severity == CrashSeverity.CRITICAL

    def test_classify_crash_signal_4(self):
        bucket, _severity = classify_crash(4, "0x0")
        assert bucket == CrashBucket.STACK_BUFFER_OVERFLOW

    def test_classify_crash_signal_8(self):
        bucket, _severity = classify_crash(8, "0x0")
        assert bucket == CrashBucket.INTEGER_OVERFLOW


class TestSeedSelection:
    def test_seed_selection_mutation(self, tmp_path):
        files = []
        for i in range(15):
            f = tmp_path / f"seed_{i}.bin"
            f.write_bytes(b"\x00" * (i + 1) * 10)
            files.append(str(f))
        selected = seed_selection(files, FuzzingStrategy.MUTATION)
        assert len(selected) == 10
        sizes = [Path(p).stat().st_size for p in selected]
        assert sizes == sorted(sizes)

    def test_seed_selection_generation(self, tmp_path):
        files = []
        for i in range(5):
            f = tmp_path / f"seed_{i}.bin"
            f.write_bytes(b"\x00" * (i + 1))
            files.append(str(f))
        selected = seed_selection(files, FuzzingStrategy.GENERATION)
        assert len(selected) == 1
        assert selected[0] == files[0]

    def test_seed_selection_coverage_guided(self, tmp_path):
        files = []
        for i in range(15):
            f = tmp_path / f"seed_{i}.bin"
            f.write_bytes(b"\x00" * (i + 1) * 10)
            files.append(str(f))
        selected = seed_selection(files, FuzzingStrategy.COVERAGE_GUIDED)
        assert len(selected) == 5
        sizes = [Path(p).stat().st_size for p in selected]
        assert sizes == sorted(sizes, reverse=True)

    def test_seed_selection_symbolic_concolic(self, tmp_path):
        f = tmp_path / "seed.bin"
        f.write_bytes(b"\x00")
        selected = seed_selection([str(f)], FuzzingStrategy.SYMBOLIC_CONCOLIC)
        assert len(selected) == 1

    def test_seed_selection_empty_corpus(self):
        assert seed_selection([], FuzzingStrategy.MUTATION) == []
        assert seed_selection([], FuzzingStrategy.GENERATION) == []


class TestMinimizeCorpus:
    def test_minimize_corpus_dedup_by_hash(self):
        crashes = [
            CrashInfo(
                input_path=f"/tmp/crash_{i}",
                signal_number=11,
                fault_address="0x0",
                crash_type=CrashBucket.NULL_DEREFERENCE,
                severity=CrashSeverity.HIGH,
            )
            for i in range(10)
        ]
        result = minimize_corpus(crashes)
        assert len(result) == 1

    def test_minimize_corpus_keeps_highest_severity_first(self):
        c1 = CrashInfo(
            input_path="/tmp/low", signal_number=4, fault_address="0x1",
            crash_type=CrashBucket.STACK_BUFFER_OVERFLOW, severity=CrashSeverity.LOW,
        )
        c2 = CrashInfo(
            input_path="/tmp/crit", signal_number=6, fault_address="0x2",
            crash_type=CrashBucket.HEAP_BUFFER_OVERFLOW, severity=CrashSeverity.CRITICAL,
        )
        result = minimize_corpus([c1, c2])
        assert len(result) == 2
        assert result[0].severity == CrashSeverity.CRITICAL

    def test_minimize_corpus_empty(self):
        assert minimize_corpus([]) == []


class TestTriageCrash:
    def test_triage_crash_exploitable_heap_uaf(self):
        crash = CrashInfo(
            input_path="/tmp/crash1", signal_number=6, fault_address="0xdead",
            crash_type=CrashBucket.HEAP_USE_AFTER_FREE, severity=CrashSeverity.CRITICAL,
        )
        report = triage_crash(crash)
        assert report["is_exploitable"] is True
        assert "heap corruption" in report["exploitability_notes"][0]
        assert "Prioritize" in report["recommended_action"]

    def test_triage_crash_exploitable_stack_overflow(self):
        crash = CrashInfo(
            input_path="/tmp/crash2", signal_number=4, fault_address="0xbeef",
            crash_type=CrashBucket.STACK_BUFFER_OVERFLOW, severity=CrashSeverity.HIGH,
        )
        report = triage_crash(crash)
        assert report["is_exploitable"] is True
        assert "RIP/EIP" in report["recommended_action"]

    def test_triage_crash_non_exploitable_null_deref(self):
        crash = CrashInfo(
            input_path="/tmp/crash3", signal_number=11, fault_address="0x0",
            crash_type=CrashBucket.NULL_DEREFERENCE, severity=CrashSeverity.HIGH,
        )
        report = triage_crash(crash)
        assert report["is_exploitable"] is False
        assert "DoS" in report["exploitability_notes"][0]

    def test_triage_crash_unknown_bucket(self):
        crash = CrashInfo(
            input_path="/tmp/crash4", signal_number=1, fault_address="0x1",
            crash_type=CrashBucket.UNKNOWN, severity=CrashSeverity.MEDIUM,
        )
        report = triage_crash(crash)
        assert report["is_exploitable"] is False
        assert "Investigate" in report["recommended_action"]

    def test_triage_crash_with_sanitizer_report(self):
        crash = CrashInfo(
            input_path="/tmp/crash5", signal_number=6, fault_address="0xdead",
            crash_type=CrashBucket.HEAP_BUFFER_OVERFLOW, severity=CrashSeverity.CRITICAL,
            sanitizer_report="heap-buffer-overflow at 0x...",
        )
        report = triage_crash(crash)
        assert report["has_sanitizer_report"] is True


class TestSeedSelectionStrategy:
    def test_selection_strategy_mutation(self):
        steps = seed_selection_strategy(FuzzingStrategy.MUTATION)
        assert "collect_valid_inputs" in steps
        assert "afl_cmin" in steps
        assert len(steps) == 4

    def test_selection_strategy_generation(self):
        steps = seed_selection_strategy(FuzzingStrategy.GENERATION)
        assert "grammar_based_generation" in steps
        assert len(steps) == 3

    def test_selection_strategy_coverage_guided(self):
        steps = seed_selection_strategy(FuzzingStrategy.COVERAGE_GUIDED)
        assert "corpus_distillation" in steps
        assert "coverage_weighted_selection" in steps
        assert len(steps) == 5

    def test_selection_strategy_symbolic(self):
        steps = seed_selection_strategy(FuzzingStrategy.SYMBOLIC_CONCOLIC)
        assert "symbolic_seed_generation" in steps
        assert len(steps) == 3


class TestGenerateHarness:
    def test_generate_c_harness_basic(self):
        output = generate_c_harness(
            header_file="myheader.h",
            fuzz_call="my_parse(data, size)",
            min_size=4,
        )
        assert '#include "myheader.h"' in output
        assert "LLVMFuzzerTestOneInput" in output
        assert "if (size < 4)" in output
        assert "my_parse(data, size)" in output
        assert "#include <stdint.h>" in output
        assert "extern" not in output

    def test_generate_c_harness_cpp(self):
        output = generate_c_harness(
            header_file="myheader.hpp",
            fuzz_call="parse(data, size)",
            use_cpp=True,
            setup_code="std::vector<uint8_t> buf(data, data + size);",
        )
        assert "extern \"C\"" in output
        assert "#include <cstdint>" in output
        assert "#include <vector>" in output
        assert "std::vector<uint8_t> buf(data, data + size);" in output

    def test_generate_python_harness(self):
        output = generate_python_harness(
            module_name="mypackage",
            target_function="parse_input",
            fuzz_call="parse_input(fdp.ConsumeString(100))",
        )
        assert "import atheris" in output
        assert "from mypackage import parse_input" in output
        assert "atheris.FuzzedDataProvider" in output
        assert "parse_input(fdp.ConsumeString(100))" in output


class TestConfigFactories:
    def test_create_afl_config(self):
        config = create_afl_config("/usr/bin/target")
        assert config.target_binary == "/usr/bin/target"
        assert config.input_dir == "./corpus/seeds"
        assert config.output_dir == "./corpus/output"
        assert config.use_cmplog is True
        assert config.use_asan is True
        assert config.timeout_ms == 1000
        assert config.memory_limit_mb == 800

    def test_create_libfuzzer_config(self):
        config = create_libfuzzer_config("/usr/bin/target", corpus_dir="/tmp/corpus")
        assert config.target_binary == "/usr/bin/target"
        assert config.corpus_dir == "/tmp/corpus"
        assert config.use_value_profile is True
        assert config.max_total_time == 3600

    def test_create_honggfuzz_config(self):
        config = create_honggfuzz_config("/usr/bin/target", input_dir="./inputs")
        assert config.target_binary == "/usr/bin/target"
        assert config.input_dir == "./inputs"
        assert config.use_asan is True
        assert config.threads == 4
        assert config.timeout == 10

    def test_afl_config_to_command(self):
        config = create_afl_config("/usr/bin/target")
        cmd = config.to_command()
        assert "afl-fuzz" in cmd
        assert "/usr/bin/target" in cmd
        assert "-t" in cmd
        assert "1000" in cmd
        assert "-m" in cmd
        assert "800" in cmd

    def test_libfuzzer_config_to_command(self):
        config = create_libfuzzer_config("/usr/bin/target", "/tmp/corp")
        cmd = config.to_command()
        assert "/usr/bin/target" in cmd
        assert "/tmp/corp" in cmd
        assert any("max_len" in a for a in cmd)
        assert any("use_value_profile" in a for a in cmd)

    def test_honggfuzz_config_to_command(self):
        config = create_honggfuzz_config("/usr/bin/target", "./in")
        cmd = config.to_command()
        assert "honggfuzz" in cmd
        assert "/usr/bin/target" in cmd
        assert "./in" in cmd
        assert "4" in cmd


class TestFuzzingJob:
    def test_fuzzing_job_creation(self):
        config = create_afl_config("/bin/target")
        job = FuzzingJob(
            strategy=FuzzingStrategy.COVERAGE_GUIDED,
            tool_config=config,
            target_binary="/bin/target",
            corpus_dir="/tmp/corpus",
            timeout_seconds=7200,
            tags=["asan", "fuzz"],
        )
        assert job.strategy == FuzzingStrategy.COVERAGE_GUIDED
        assert job.timeout_seconds == 7200
        assert job.tags == ["asan", "fuzz"]


class TestCorpusSizeRules:
    def test_corpus_size_rules_coverage(self):
        assert FuzzingStrategy.MUTATION in CORPUS_SIZE_RULES
        assert FuzzingStrategy.GENERATION in CORPUS_SIZE_RULES
        assert FuzzingStrategy.COVERAGE_GUIDED in CORPUS_SIZE_RULES
        assert FuzzingStrategy.SYMBOLIC_CONCOLIC in CORPUS_SIZE_RULES
        assert CORPUS_SIZE_RULES[FuzzingStrategy.COVERAGE_GUIDED]["min_seeds"] == 5
