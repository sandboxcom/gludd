"""Fuzzing strategy enums, tool configs, corpus management, harness generation."""

from __future__ import annotations

import enum
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class FuzzingStrategy(enum.Enum):
    MUTATION = "mutation"
    GENERATION = "generation"
    COVERAGE_GUIDED = "coverage_guided"
    SYMBOLIC_CONCOLIC = "symbolic_concolic"


class CrashSeverity(enum.Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CrashBucket(enum.Enum):
    STACK_BUFFER_OVERFLOW = "stack_buffer_overflow"
    HEAP_BUFFER_OVERFLOW = "heap_buffer_overflow"
    HEAP_USE_AFTER_FREE = "heap_use_after_free"
    HEAP_DOUBLE_FREE = "heap_double_free"
    NULL_DEREFERENCE = "null_dereference"
    FORMAT_STRING = "format_string"
    INTEGER_OVERFLOW = "integer_overflow"
    INTEGER_UNDERFLOW = "integer_underflow"
    DIVIDE_BY_ZERO = "divide_by_zero"
    OUT_OF_BOUNDS_READ = "out_of_bounds_read"
    OUT_OF_BOUNDS_WRITE = "out_of_bounds_write"
    STACK_EXHAUSTION = "stack_exhaustion"
    USE_AFTER_RETURN = "use_after_return"
    BAD_CAST = "bad_cast"
    RACE_CONDITION = "race_condition"
    MEMORY_LEAK = "memory_leak"
    TIMEOUT = "timeout"
    ASSERTION_FAILURE = "assertion_failure"
    UNKNOWN = "unknown"


CORPUS_SIZE_RULES: dict[FuzzingStrategy, dict[str, int]] = {
    FuzzingStrategy.MUTATION: {"min_seeds": 10, "max_corpus_mb": 500},
    FuzzingStrategy.GENERATION: {"min_seeds": 1, "max_corpus_mb": 100},
    FuzzingStrategy.COVERAGE_GUIDED: {"min_seeds": 5, "max_corpus_mb": 1000},
    FuzzingStrategy.SYMBOLIC_CONCOLIC: {"min_seeds": 1, "max_corpus_mb": 50},
}


@dataclass
class AflPlusPlusConfig:
    binary_path: str = "afl-fuzz"
    input_dir: str = "./corpus/seeds"
    output_dir: str = "./corpus/output"
    target_binary: str = "./fuzz_target"
    target_args: list[str] = field(default_factory=lambda: ["@@"])
    memory_limit_mb: int = 800
    timeout_ms: int = 1000
    dictionary: str = ""
    use_cmplog: bool = True
    use_asan: bool = True
    use_ubsan: bool = False
    parallel_instances: int = 1
    extra_args: list[str] = field(default_factory=list)

    def to_command(self) -> list[str]:
        cmd: list[str] = [self.binary_path, "-i", self.input_dir, "-o", self.output_dir]
        cmd.extend(["-m", str(self.memory_limit_mb)])
        cmd.extend(["-t", str(self.timeout_ms)])
        if self.dictionary:
            cmd.extend(["-x", self.dictionary])
        if self.use_cmplog:
            cmd.append("-c")
            cmd.extend(["-l", "2AT"])
        if self.parallel_instances > 1:
            cmd.extend(["-M", "fuzzer01"])
        cmd.append("--")
        cmd.append(self.target_binary)
        cmd.extend(self.target_args)
        cmd.extend(self.extra_args)
        return cmd


@dataclass
class LibFuzzerConfig:
    target_binary: str = "./fuzz_target"
    corpus_dir: str = "./corpus"
    artifact_dir: str = "./artifacts"
    max_len: int = 4096
    runs: int = -1
    max_total_time: int = 3600
    dict_path: str = ""
    use_value_profile: bool = True
    extra_flags: list[str] = field(default_factory=list)

    def to_command(self) -> list[str]:
        cmd = [
            self.target_binary,
            self.corpus_dir,
            f"-artifact_prefix={self.artifact_dir}/",
            f"-max_len={self.max_len}",
        ]
        if self.runs > 0:
            cmd.append(f"-runs={self.runs}")
        if self.max_total_time > 0:
            cmd.append(f"-max_total_time={self.max_total_time}")
        if self.dict_path:
            cmd.append(f"-dict={self.dict_path}")
        if self.use_value_profile:
            cmd.append("-use_value_profile=1")
        cmd.extend(self.extra_flags)
        return cmd


@dataclass
class HonggfuzzConfig:
    honggfuzz_path: str = "honggfuzz"
    input_dir: str = "./corpus"
    output_dir: str = "./out"
    target_binary: str = "./fuzz_target"
    timeout: int = 10
    max_file_size: int = 1048576
    threads: int = 4
    use_asan: bool = True
    dictionary: str = ""
    extra_args: list[str] = field(default_factory=list)

    def to_command(self) -> list[str]:
        cmd = [
            self.honggfuzz_path,
            "-i",
            self.input_dir,
            "-o",
            self.output_dir,
            "-t",
            str(self.timeout),
            "-F",
            str(self.max_file_size),
            "-n",
            str(self.threads),
            "--",
            self.target_binary,
        ]
        if self.dictionary:
            cmd.extend(["-w", self.dictionary])
        cmd.extend(self.extra_args)
        return cmd


@dataclass
class ZzufConfig:
    seed: int = 0
    ratio: float = 0.004
    include_ranges: list[tuple[int, int]] = field(default_factory=list)
    exclude_ranges: list[tuple[int, int]] = field(default_factory=list)
    protect: list[bytes] = field(default_factory=list)

    def to_command(self, target_cmd: list[str]) -> list[str]:
        cmd = ["zzuf"]
        if self.seed != 0:
            cmd.extend(["-s", str(self.seed)])
        cmd.extend(["-r", str(self.ratio)])
        for start, end in self.include_ranges:
            cmd.extend(["-I", f"{start:X}-{end:X}"])
        for start, end in self.exclude_ranges:
            cmd.extend(["-E", f"{start:X}-{end:X}"])
        cmd.extend(target_cmd)
        return cmd


@dataclass
class RadamsaConfig:
    count: int = 100
    seed: int = 0
    mutations: list[str] = field(default_factory=list)
    pattern: str = ""

    def to_command(self) -> list[str]:
        cmd = ["radamsa", "-n", str(self.count)]
        if self.seed > 0:
            cmd.extend(["-s", str(self.seed)])
        if self.pattern:
            cmd.extend(["-p", self.pattern])
        if self.mutations:
            cmd.extend(["-m", ",".join(self.mutations)])
        return cmd


@dataclass
class FuzzingJob:
    strategy: FuzzingStrategy
    tool_config: AflPlusPlusConfig | LibFuzzerConfig | HonggfuzzConfig | ZzufConfig | RadamsaConfig
    target_binary: str
    corpus_dir: str
    timeout_seconds: int = 3600
    tags: list[str] = field(default_factory=list)


@dataclass
class CrashInfo:
    input_path: str
    signal_number: int
    fault_address: str
    crash_type: CrashBucket
    severity: CrashSeverity
    stacktrace: list[str] = field(default_factory=list)
    registers: dict[str, str] = field(default_factory=dict)
    sanitizer_report: str = ""
    minimized: bool = False
    minimized_path: str = ""
    hash: str = ""

    def __post_init__(self):
        if not self.hash:
            self.hash = self._compute_hash()

    def _compute_hash(self) -> str:
        content = f"{self.signal_number}:{self.fault_address}:{self.crash_type.value}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]


CRASH_SEVERITY_SIGNALS: dict[int, CrashSeverity] = {
    4: CrashSeverity.LOW,
    6: CrashSeverity.HIGH,
    8: CrashSeverity.HIGH,
    11: CrashSeverity.HIGH,
    28: CrashSeverity.HIGH,
    31: CrashSeverity.HIGH,
}


CRASH_SIGNAL_BUCKETS: dict[int, CrashBucket] = {
    4: CrashBucket.STACK_BUFFER_OVERFLOW,
    6: CrashBucket.HEAP_BUFFER_OVERFLOW,
    8: CrashBucket.INTEGER_OVERFLOW,
    11: CrashBucket.NULL_DEREFERENCE,
    28: CrashBucket.STACK_EXHAUSTION,
    31: CrashBucket.BAD_CAST,
}


ASAN_PATTERNS: list[tuple[str, CrashBucket, CrashSeverity]] = [
    ("heap-use-after-free", CrashBucket.HEAP_USE_AFTER_FREE, CrashSeverity.CRITICAL),
    ("heap-buffer-overflow", CrashBucket.HEAP_BUFFER_OVERFLOW, CrashSeverity.CRITICAL),
    ("stack-buffer-overflow", CrashBucket.STACK_BUFFER_OVERFLOW, CrashSeverity.CRITICAL),
    ("stack-use-after-return", CrashBucket.USE_AFTER_RETURN, CrashSeverity.HIGH),
    ("global-buffer-overflow", CrashBucket.OUT_OF_BOUNDS_WRITE, CrashSeverity.HIGH),
    ("double-free", CrashBucket.HEAP_DOUBLE_FREE, CrashSeverity.CRITICAL),
    ("null-dereference", CrashBucket.NULL_DEREFERENCE, CrashSeverity.HIGH),
    ("division-by-zero", CrashBucket.DIVIDE_BY_ZERO, CrashSeverity.MEDIUM),
    ("integer-overflow", CrashBucket.INTEGER_OVERFLOW, CrashSeverity.MEDIUM),
    ("out-of-bounds", CrashBucket.OUT_OF_BOUNDS_READ, CrashSeverity.HIGH),
    ("memory-leak", CrashBucket.MEMORY_LEAK, CrashSeverity.LOW),
]


def classify_crash(
    signal_number: int, fault_address: str, sanitizer_output: str = ""
) -> tuple[CrashBucket, CrashSeverity]:
    if sanitizer_output:
        for pattern, bucket, severity in ASAN_PATTERNS:
            if pattern in sanitizer_output.lower():
                return bucket, severity

    addr = fault_address.lower().replace("0x", "")
    try:
        addr_int = int(addr, 16) if addr else 0
    except ValueError:
        addr_int = -1

    if signal_number == 11 and addr_int == 0:
        return CrashBucket.NULL_DEREFERENCE, CrashSeverity.HIGH
    if signal_number == 11:
        return CrashBucket.STACK_BUFFER_OVERFLOW, CrashSeverity.HIGH
    if signal_number in CRASH_SIGNAL_BUCKETS:
        bucket = CRASH_SIGNAL_BUCKETS[signal_number]
        severity = CRASH_SEVERITY_SIGNALS.get(signal_number, CrashSeverity.MEDIUM)
        return bucket, severity
    return CrashBucket.UNKNOWN, CrashSeverity.MEDIUM


def seed_selection(corpus: list[str], strategy: FuzzingStrategy) -> list[str]:
    if not corpus:
        return []
    rules = CORPUS_SIZE_RULES.get(strategy, {"min_seeds": 1, "max_corpus_mb": 100})

    if strategy == FuzzingStrategy.MUTATION:
        selected = [c for c in corpus if Path(c).exists()]
        selected = sorted(selected, key=lambda p: Path(p).stat().st_size)[: rules["min_seeds"]]
        return selected

    if strategy == FuzzingStrategy.GENERATION:
        return corpus[:1]

    if strategy == FuzzingStrategy.COVERAGE_GUIDED:
        selected = [c for c in corpus if Path(c).exists()]
        if len(selected) > rules["min_seeds"]:
            selected = sorted(selected, key=lambda p: Path(p).stat().st_size, reverse=True)[: rules["min_seeds"]]
        return selected

    if strategy == FuzzingStrategy.SYMBOLIC_CONCOLIC:
        return [corpus[0]] if corpus else []

    return corpus


def minimize_corpus(crashes: list[CrashInfo]) -> list[CrashInfo]:
    seen_hashes: set[str] = set()
    unique_crashes: list[CrashInfo] = []
    for crash in sorted(crashes, key=lambda c: _severity_rank(c.severity), reverse=True):
        if crash.hash not in seen_hashes:
            seen_hashes.add(crash.hash)
            unique_crashes.append(crash)
    return unique_crashes


def _severity_rank(severity: CrashSeverity) -> int:
    ordering = {
        CrashSeverity.INFO: 0,
        CrashSeverity.LOW: 1,
        CrashSeverity.MEDIUM: 2,
        CrashSeverity.HIGH: 3,
        CrashSeverity.CRITICAL: 4,
    }
    return ordering.get(severity, 0)


def triage_crash(crash_info: CrashInfo) -> dict[str, Any]:
    exploitable_buckets = {
        CrashBucket.HEAP_USE_AFTER_FREE,
        CrashBucket.HEAP_BUFFER_OVERFLOW,
        CrashBucket.STACK_BUFFER_OVERFLOW,
        CrashBucket.HEAP_DOUBLE_FREE,
        CrashBucket.OUT_OF_BOUNDS_WRITE,
        CrashBucket.USE_AFTER_RETURN,
    }

    report: dict[str, Any] = {
        "hash": crash_info.hash,
        "input": crash_info.input_path,
        "crash_type": crash_info.crash_type.value,
        "severity": crash_info.severity.value,
        "signal": crash_info.signal_number,
        "fault_address": crash_info.fault_address,
        "is_exploitable": crash_info.crash_type in exploitable_buckets,
        "exploitability_notes": [],
        "recommended_action": "",
    }

    if crash_info.crash_type in exploitable_buckets:
        if crash_info.crash_type in {
            CrashBucket.HEAP_USE_AFTER_FREE,
            CrashBucket.HEAP_BUFFER_OVERFLOW,
            CrashBucket.HEAP_DOUBLE_FREE,
        }:
            report["exploitability_notes"].append("heap corruption — potentially exploitable")
            report["recommended_action"] = (
                "Prioritize for exploit development. Run under ASAN with detailed heap profiling."
            )
        elif crash_info.crash_type in {CrashBucket.STACK_BUFFER_OVERFLOW, CrashBucket.OUT_OF_BOUNDS_WRITE}:
            report["exploitability_notes"].append("stack/memory corruption — check overwritten return addresses")
            report["recommended_action"] = "Check for RIP/EIP control. Attempt to determine overwrite offset."
        elif crash_info.crash_type == CrashBucket.USE_AFTER_RETURN:
            report["exploitability_notes"].append(
                "use-after-return — may be exploitable if return value is controllable"
            )
            report["recommended_action"] = "Test with address sanitizer and track allocation lifetime."
    elif crash_info.crash_type == CrashBucket.NULL_DEREFERENCE:
        report["exploitability_notes"].append("null dereference — typically DoS only unless paired with another bug")
        report["recommended_action"] = "Fix for stability. Low exploitation priority unless chainable."
    else:
        report["recommended_action"] = (
            "Investigate crash root cause. Determine if attacker-controlled input reaches crash site."
        )

    if crash_info.sanitizer_report:
        report["has_sanitizer_report"] = True

    return report


def seed_selection_strategy(strategy: FuzzingStrategy) -> list[str]:
    if strategy == FuzzingStrategy.MUTATION:
        return [
            "collect_valid_inputs",
            "minimize_corpus",
            "afl_cmin",
            "remove_uninteresting_seeds",
        ]
    if strategy == FuzzingStrategy.GENERATION:
        return [
            "grammar_based_generation",
            "protocol_specification_seeds",
            "format_templates",
        ]
    if strategy == FuzzingStrategy.COVERAGE_GUIDED:
        return [
            "afl_cmin",
            "corpus_distillation",
            "coverage_weighted_selection",
            "remove_slow_seeds",
            "periodic_corpus_reseeding",
        ]
    if strategy == FuzzingStrategy.SYMBOLIC_CONCOLIC:
        return [
            "symbolic_seed_generation",
            "constraint_solving_seeds",
            "path_diversity_ranking",
        ]
    return ["default_collection"]


_FUZZING_HARNESS_C_TEMPLATE = """\
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include "{header_file}"

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {{
    if (size < {min_size}) return 0;

    {setup_code}

    {fuzz_call};

    return 0;
}}
"""

_FUZZING_HARNESS_CXX_TEMPLATE = """\
#include <cstdint>
#include <cstddef>
#include <cstdio>
#include <cstring>
#include <vector>
#include <string>
#include "{header_file}"

extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {{
    if (size < {min_size}) return 0;

    {setup_code}

    {fuzz_call};

    return 0;
}}
"""

_FUZZING_HARNESS_PYTHON_TEMPLATE = """\
import sys
import atheris

with atheris.instrument_imports():
    from {module_name} import {target_function}

def TestOneInput(data):
    fdp = atheris.FuzzedDataProvider(data)
    {setup_code}
    try:
        {fuzz_call}
    except Exception:
        pass

def main():
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()

if __name__ == "__main__":
    main()
"""


def generate_c_harness(
    header_file: str,
    fuzz_call: str,
    min_size: int = 0,
    setup_code: str = "",
    use_cpp: bool = False,
) -> str:
    template = _FUZZING_HARNESS_CXX_TEMPLATE if use_cpp else _FUZZING_HARNESS_C_TEMPLATE
    return template.format(
        header_file=header_file,
        min_size=min_size,
        setup_code=setup_code,
        fuzz_call=fuzz_call,
    )


def generate_python_harness(
    module_name: str,
    target_function: str,
    fuzz_call: str,
    setup_code: str = "",
) -> str:
    return _FUZZING_HARNESS_PYTHON_TEMPLATE.format(
        module_name=module_name,
        target_function=target_function,
        setup_code=setup_code,
        fuzz_call=fuzz_call,
    )


def create_afl_config(
    target_binary: str,
    input_dir: str = "./corpus/seeds",
    output_dir: str = "./corpus/output",
) -> AflPlusPlusConfig:
    return AflPlusPlusConfig(
        target_binary=target_binary,
        input_dir=input_dir,
        output_dir=output_dir,
    )


def create_libfuzzer_config(
    target_binary: str,
    corpus_dir: str = "./corpus",
) -> LibFuzzerConfig:
    return LibFuzzerConfig(
        target_binary=target_binary,
        corpus_dir=corpus_dir,
    )


def create_honggfuzz_config(
    target_binary: str,
    input_dir: str = "./corpus",
) -> HonggfuzzConfig:
    return HonggfuzzConfig(
        target_binary=target_binary,
        input_dir=input_dir,
    )
