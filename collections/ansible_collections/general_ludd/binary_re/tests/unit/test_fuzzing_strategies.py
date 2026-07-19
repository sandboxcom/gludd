"""Tests for fuzzing_strategies module — enums, configs, corpus, harnesses."""

from __future__ import annotations

from pathlib import Path

import pytest

from plugins.module_utils.fuzzing_strategies import (
    FuzzingStrategy,
    CrashSeverity,
    CrashBucket,
    CORPUS_SIZE_RULES,
    AflPlusPlusConfig,
    LibFuzzerConfig,
    HonggfuzzConfig,
    ZzufConfig,
    RadamsaConfig,
    FuzzingJob,
    CrashInfo,
    classify_crash,
    seed_selection,
    minimize_corpus,
    triage_crash,
    seed_selection_strategy,
    generate_c_harness,
    generate_python_harness,
    create_afl_config,
    create_libfuzzer_config,
    create_honggfuzz_config,
    CRASH_SIGNAL_BUCKETS,
    CRASH_SEVERITY_SIGNALS,
    ASAN_PATTERNS,
)


class TestFuzzingStrategy:
    def test_four_strategies_exist(self):
        values = {s.value for s in FuzzingStrategy}
        assert values == {"mutation", "generation", "coverage_guided", "symbolic_concolic"}

    def test_uniqueness(self):
        vals = [s.value for s in FuzzingStrategy]
        assert len(vals) == len(set(vals))


class TestCrashSeverity:
    def test_values(self):
        assert CrashSeverity.INFO.value == "info"
        assert CrashSeverity.LOW.value == "low"
        assert CrashSeverity.MEDIUM.value == "medium"
        assert CrashSeverity.HIGH.value == "high"
        assert CrashSeverity.CRITICAL.value == "critical"


class TestCrashBucket:
    def test_all_buckets_exist(self):
        buckets = {b.value for b in CrashBucket}
        assert "stack_buffer_overflow" in buckets
        assert "heap_use_after_free" in buckets
        assert "heap_double_free" in buckets
        assert "null_dereference" in buckets
        assert "format_string" in buckets
        assert "integer_overflow" in buckets
        assert "use_after_return" in buckets
        assert "bad_cast" in buckets
        assert "race_condition" in buckets
        assert "memory_leak" in buckets
        assert "unknown" in buckets


class TestCorpusSizeRules:
    def test_all_strategies_have_rules(self):
        for strategy in FuzzingStrategy:
            assert strategy in CORPUS_SIZE_RULES, f"No rules for {strategy}"
            rules = CORPUS_SIZE_RULES[strategy]
            assert "min_seeds" in rules
            assert "max_corpus_mb" in rules

    def test_coverage_guided_highest_corpus(self):
        assert CORPUS_SIZE_RULES[FuzzingStrategy.COVERAGE_GUIDED]["max_corpus_mb"] == 1000

    def test_symbolic_concolic_smallest_corpus(self):
        assert CORPUS_SIZE_RULES[FuzzingStrategy.SYMBOLIC_CONCOLIC]["max_corpus_mb"] == 50


class TestAflPlusPlusConfig:
    def test_default_values(self):
        cfg = AflPlusPlusConfig()
        assert cfg.binary_path == "afl-fuzz"
        assert cfg.memory_limit_mb == 800
        assert cfg.timeout_ms == 1000
        assert cfg.use_cmplog is True
        assert cfg.use_asan is True
        assert cfg.use_ubsan is False

    def test_to_command(self):
        cfg = AflPlusPlusConfig(
            target_binary="./fuzz_test",
            input_dir="./in",
            output_dir="./out",
            dictionary="./dict.dict",
            parallel_instances=1,
        )
        cmd = cfg.to_command()
        assert "afl-fuzz" in cmd
        assert "-i" in cmd
        assert "./in" in cmd
        assert "-o" in cmd
        assert "./out" in cmd
        assert "-x" in cmd
        assert "./dict.dict" in cmd
        assert "./fuzz_test" in cmd

    def test_multi_instance_uses_master_flag(self):
        cfg = AflPlusPlusConfig(parallel_instances=2)
        cmd = cfg.to_command()
        assert "-M" in cmd


class TestLibFuzzerConfig:
    def test_default_values(self):
        cfg = LibFuzzerConfig()
        assert cfg.corpus_dir == "./corpus"
        assert cfg.max_len == 4096
        assert cfg.max_total_time == 3600
        assert cfg.use_value_profile is True

    def test_to_command(self):
        cfg = LibFuzzerConfig(
            target_binary="./fuzz_lib",
            corpus_dir="./corpus",
            artifact_dir="./crash",
            dict_path="./dict",
            runs=10000,
        )
        cmd = cfg.to_command()
        assert "./fuzz_lib" in cmd
        assert "./corpus" in cmd
        assert "-artifact_prefix=./crash/" in cmd
        assert "-dict=./dict" in cmd
        assert "-runs=10000" in cmd

    def test_unlimited_runs(self):
        cfg = LibFuzzerConfig(runs=-1)
        cmd = cfg.to_command()
        assert not any(s.startswith("-runs=") for s in cmd)


class TestHonggfuzzConfig:
    def test_default_values(self):
        cfg = HonggfuzzConfig()
        assert cfg.timeout == 10
        assert cfg.threads == 4
        assert cfg.use_asan is True

    def test_to_command(self):
        cfg = HonggfuzzConfig(target_binary="./hf_target", input_dir="./in", output_dir="./out")
        cmd = cfg.to_command()
        assert "honggfuzz" in cmd
        assert "-i" in cmd
        assert "./in" in cmd
        assert "-o" in cmd
        assert "./out" in cmd
        assert "./hf_target" in cmd


class TestZzufConfig:
    def test_to_command(self):
        cfg = ZzufConfig(seed=42, ratio=0.01)
        cmd = cfg.to_command(["./target", "@@"])
        assert "zzuf" in cmd
        assert "-s" in cmd
        assert "42" in cmd
        assert "-r" in cmd
        assert "0.01" in cmd
        assert "./target" in cmd


class TestRadamsaConfig:
    def test_to_command(self):
        cfg = RadamsaConfig(count=50, seed=7)
        cmd = cfg.to_command()
        assert "radamsa" in cmd
        assert "-n" in cmd
        assert "50" in cmd
        assert "-s" in cmd
        assert "7" in cmd

    def test_default_no_seed(self):
        cfg = RadamsaConfig(count=100)
        cmd = cfg.to_command()
        assert "-s" not in cmd


class TestFuzzingJob:
    def test_creation_with_afl(self):
        afl_cfg = AflPlusPlusConfig(target_binary="./target")
        job = FuzzingJob(
            strategy=FuzzingStrategy.COVERAGE_GUIDED,
            tool_config=afl_cfg,
            target_binary="./target",
            corpus_dir="./corpus",
            tags=["asan", "x86_64"],
        )
        assert job.strategy == FuzzingStrategy.COVERAGE_GUIDED
        assert job.timeout_seconds == 3600
        assert "asan" in job.tags


class TestCrashInfo:
    def test_hash_generated(self):
        info = CrashInfo(
            input_path="crash_001",
            signal_number=11,
            fault_address="0x0",
            crash_type=CrashBucket.NULL_DEREFERENCE,
            severity=CrashSeverity.HIGH,
        )
        assert len(info.hash) == 16

    def test_hash_consistent(self):
        info1 = CrashInfo("a", 11, "0x0", CrashBucket.NULL_DEREFERENCE, CrashSeverity.HIGH)
        info2 = CrashInfo("b", 11, "0x0", CrashBucket.NULL_DEREFERENCE, CrashSeverity.HIGH)
        assert info1.hash == info2.hash


class TestClassifyCrash:
    def test_null_deref(self):
        bucket, severity = classify_crash(11, "0x0")
        assert bucket == CrashBucket.NULL_DEREFERENCE
        assert severity == CrashSeverity.HIGH

    def test_segv_non_null(self):
        bucket, severity = classify_crash(11, "0x7fff0000")
        assert bucket == CrashBucket.STACK_BUFFER_OVERFLOW
        assert severity == CrashSeverity.HIGH

    def test_unknown_signal(self):
        bucket, severity = classify_crash(99, "0x0")
        assert bucket == CrashBucket.UNKNOWN
        assert severity == CrashSeverity.MEDIUM

    def test_abort_signal(self):
        bucket, severity = classify_crash(6, "0x400000")
        assert bucket == CrashBucket.HEAP_BUFFER_OVERFLOW
        assert severity == CrashSeverity.HIGH

    def test_fpe_signal(self):
        bucket, severity = classify_crash(8, "0x400000")
        assert bucket == CrashBucket.INTEGER_OVERFLOW
        assert severity == CrashSeverity.HIGH

    def test_sanitizer_use_after_free(self):
        bucket, severity = classify_crash(11, "0x0", sanitizer_output="heap-use-after-free detected")
        assert bucket == CrashBucket.HEAP_USE_AFTER_FREE
        assert severity == CrashSeverity.CRITICAL

    def test_sanitizer_heap_buffer_overflow(self):
        bucket, severity = classify_crash(11, "0x0", sanitizer_output="ERROR: heap-buffer-overflow")
        assert bucket == CrashBucket.HEAP_BUFFER_OVERFLOW
        assert severity == CrashSeverity.CRITICAL

    def test_sanitizer_double_free(self):
        bucket, severity = classify_crash(6, "0x0", sanitizer_output="double-free on address")
        assert bucket == CrashBucket.HEAP_DOUBLE_FREE
        assert severity == CrashSeverity.CRITICAL

    def test_sanitizer_null_deref(self):
        bucket, severity = classify_crash(11, "0x0", sanitizer_output="null-dereference")
        assert bucket == CrashBucket.NULL_DEREFERENCE
        assert severity == CrashSeverity.HIGH

    def test_sanitizer_stack_overflow(self):
        bucket, severity = classify_crash(11, "0x0", sanitizer_output="stack-buffer-overflow at")
        assert bucket == CrashBucket.STACK_BUFFER_OVERFLOW
        assert severity == CrashSeverity.CRITICAL

    def test_sanitizer_integer_overflow(self):
        bucket, severity = classify_crash(0, "0x0", sanitizer_output="integer-overflow in")
        assert bucket == CrashBucket.INTEGER_OVERFLOW
        assert severity == CrashSeverity.MEDIUM

    def test_sanitizer_memory_leak(self):
        bucket, severity = classify_crash(0, "0x0", sanitizer_output="memory-leak")
        assert bucket == CrashBucket.MEMORY_LEAK
        assert severity == CrashSeverity.LOW

    def test_sanitizer_division_by_zero(self):
        bucket, severity = classify_crash(8, "0x0", sanitizer_output="division-by-zero at")
        assert bucket == CrashBucket.DIVIDE_BY_ZERO
        assert severity == CrashSeverity.MEDIUM


class TestSeedSelection:
    def test_empty_corpus(self):
        result = seed_selection([], FuzzingStrategy.MUTATION)
        assert result == []

    def test_mutation_strategy(self, tmp_path):
        files = []
        for i in range(20):
            f = tmp_path / f"seed_{i:04d}.bin"
            f.write_bytes(b"A" * (i + 1) * 10)
            files.append(str(f))
        result = seed_selection(files, FuzzingStrategy.MUTATION)
        assert len(result) <= CORPUS_SIZE_RULES[FuzzingStrategy.MUTATION]["min_seeds"]
        assert len(result) > 0

    def test_generation_returns_one(self, tmp_path):
        f = tmp_path / "seed.bin"
        f.write_bytes(b"A" * 100)
        result = seed_selection([str(f)], FuzzingStrategy.GENERATION)
        assert len(result) == 1

    def test_coverage_guided(self, tmp_path):
        files = []
        for i in range(10):
            f = tmp_path / f"cov_{i}.bin"
            f.write_bytes(b"B" * (i + 1) * 100)
            files.append(str(f))
        result = seed_selection(files, FuzzingStrategy.COVERAGE_GUIDED)
        assert len(result) > 0

    def test_symbolic_concolic(self, tmp_path):
        f = tmp_path / "sym.bin"
        f.write_bytes(b"C" * 50)
        result = seed_selection([str(f)], FuzzingStrategy.SYMBOLIC_CONCOLIC)
        assert len(result) == 1


class TestMinimizeCorpus:
    def test_deduplicates_by_hash(self):
        crashes = [
            CrashInfo("a", 11, "0x0", CrashBucket.NULL_DEREFERENCE, CrashSeverity.HIGH),
            CrashInfo("b", 11, "0x0", CrashBucket.NULL_DEREFERENCE, CrashSeverity.HIGH),
            CrashInfo("c", 6, "0x400000", CrashBucket.HEAP_BUFFER_OVERFLOW, CrashSeverity.HIGH),
        ]
        result = minimize_corpus(crashes)
        assert len(result) == 2

    def test_sorts_by_severity(self):
        crashes = [
            CrashInfo("a", 4, "0x0", CrashBucket.INTEGER_OVERFLOW, CrashSeverity.LOW),
            CrashInfo("b", 11, "0x0", CrashBucket.NULL_DEREFERENCE, CrashSeverity.HIGH),
            CrashInfo("c", 6, "0x0", CrashBucket.HEAP_DOUBLE_FREE, CrashSeverity.CRITICAL),
        ]
        result = minimize_corpus(crashes)
        assert result[0].severity == CrashSeverity.CRITICAL


class TestTriageCrash:
    def test_use_after_free_exploitable(self):
        info = CrashInfo("crash_001", 11, "0x7fff", CrashBucket.HEAP_USE_AFTER_FREE, CrashSeverity.CRITICAL)
        report = triage_crash(info)
        assert report["is_exploitable"] is True
        assert "heap corruption" in report["exploitability_notes"][0]

    def test_null_deref_not_exploitable(self):
        info = CrashInfo("crash_002", 11, "0x0", CrashBucket.NULL_DEREFERENCE, CrashSeverity.HIGH)
        report = triage_crash(info)
        assert report["is_exploitable"] is False
        assert len(report["exploitability_notes"]) > 0

    def test_includes_recommended_action(self):
        info = CrashInfo("crash_003", 11, "0x0", CrashBucket.NULL_DEREFERENCE, CrashSeverity.HIGH)
        report = triage_crash(info)
        assert report["recommended_action"] != ""

    def test_stack_buffer_overflow_exploitable(self):
        info = CrashInfo("crash_004", 11, "0x41414141", CrashBucket.STACK_BUFFER_OVERFLOW, CrashSeverity.HIGH)
        report = triage_crash(info)
        assert report["is_exploitable"] is True

    def test_sanitizer_report_detected(self):
        info = CrashInfo(
            "crash_005", 6, "0x0", CrashBucket.HEAP_DOUBLE_FREE, CrashSeverity.CRITICAL,
            sanitizer_report="double-free on address 0x1234",
        )
        report = triage_crash(info)
        assert report.get("has_sanitizer_report") is True


class TestSeedSelectionStrategy:
    def test_mutation(self):
        result = seed_selection_strategy(FuzzingStrategy.MUTATION)
        assert "collect_valid_inputs" in result
        assert "minimize_corpus" in result
        assert "afl_cmin" in result

    def test_generation(self):
        result = seed_selection_strategy(FuzzingStrategy.GENERATION)
        assert "grammar_based_generation" in result

    def test_coverage_guided(self):
        result = seed_selection_strategy(FuzzingStrategy.COVERAGE_GUIDED)
        assert "afl_cmin" in result
        assert "corpus_distillation" in result

    def test_symbolic_concolic(self):
        result = seed_selection_strategy(FuzzingStrategy.SYMBOLIC_CONCOLIC)
        assert "symbolic_seed_generation" in result


class TestHarnessGeneration:
    def test_c_harness_basic(self):
        harness = generate_c_harness(
            header_file="parser.h",
            fuzz_call="parse_buffer(data, size);",
        )
        assert '#include "parser.h"' in harness
        assert "LLVMFuzzerTestOneInput" in harness
        assert "parse_buffer(data, size);" in harness

    def test_c_harness_with_min_size(self):
        harness = generate_c_harness(
            header_file="parser.h",
            fuzz_call="parse(data, size);",
            min_size=4,
        )
        assert "if (size < 4)" in harness

    def test_c_harness_with_setup(self):
        harness = generate_c_harness(
            header_file="parser.h",
            fuzz_call="parse(data, size);",
            setup_code="init_library();",
        )
        assert "init_library();" in harness

    def test_cpp_harness(self):
        harness = generate_c_harness(
            header_file="my_json.hpp",
            fuzz_call="parse_json(data, size);",
            use_cpp=True,
        )
        assert '#include "my_json.hpp"' in harness
        assert 'extern "C"' in harness

    def test_python_harness(self):
        harness = generate_python_harness(
            module_name="my_module",
            target_function="parse_input",
            fuzz_call="my_module.parse_input(data)",
        )
        assert "import atheris" in harness
        assert "from my_module import parse_input" in harness
        assert "my_module.parse_input(data)" in harness

    def test_python_harness_with_setup(self):
        harness = generate_python_harness(
            module_name="mylib",
            target_function="f",
            fuzz_call="f(data)",
            setup_code="init()",
        )
        assert "init()" in harness


class TestFactoryFunctions:
    def test_create_afl_config(self):
        cfg = create_afl_config("./target_bin")
        assert cfg.target_binary == "./target_bin"
        assert isinstance(cfg, AflPlusPlusConfig)

    def test_create_libfuzzer_config(self):
        cfg = create_libfuzzer_config("./fuzz_lib_target")
        assert cfg.target_binary == "./fuzz_lib_target"
        assert isinstance(cfg, LibFuzzerConfig)

    def test_create_honggfuzz_config(self):
        cfg = create_honggfuzz_config("./hf_target")
        assert cfg.target_binary == "./hf_target"
        assert isinstance(cfg, HonggfuzzConfig)


class TestAsanPatterns:
    def test_patterns_non_empty(self):
        assert len(ASAN_PATTERNS) >= 10

    def test_patterns_have_bucket_and_severity(self):
        for pattern, bucket, severity in ASAN_PATTERNS:
            assert isinstance(pattern, str)
            assert isinstance(bucket, CrashBucket)
            assert isinstance(severity, CrashSeverity)


class TestCrashSignalMappings:
    def test_signal_buckets(self):
        assert 4 in CRASH_SIGNAL_BUCKETS
        assert 6 in CRASH_SIGNAL_BUCKETS
        assert 11 in CRASH_SIGNAL_BUCKETS

    def test_signal_severity(self):
        assert CRASH_SEVERITY_SIGNALS[11] == CrashSeverity.HIGH
