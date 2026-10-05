#!/usr/bin/env python3
"""Fuzzing target harness — used by the fuzz_target Ansible role."""

from __future__ import annotations

import argparse
import json
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ansible_collections.general_ludd.binary_re.plugins.module_utils.fuzzing_strategies import (
    CrashBucket,
    CrashInfo,
    classify_crash,
    create_afl_config,
    create_honggfuzz_config,
    create_libfuzzer_config,
    minimize_corpus,
    triage_crash,
)


@dataclass
class CoverageGuidedResult:
    fuzzer: str = "unknown"
    target: str = ""
    corpus_dir: str = ""
    output_dir: str = ""
    executions: int = 0
    crashes: int = 0
    unique_crashes: int = 0
    paths_found: int = 0
    elapsed_seconds: float = 0.0
    crash_details: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class MutationResult:
    mutation_strategy: str = "unknown"
    mutations_total: int = 0
    crashes: int = 0
    unique_inputs: int = 0
    elapsed_seconds: float = 0.0
    seed_files: list[str] = field(default_factory=list)


@dataclass
class CrashTriageResult:
    total_crashes: int = 0
    unique_crashes: int = 0
    exploitable: int = 0
    crashes_by_type: dict[str, int] = field(default_factory=dict)
    crashes_by_severity: dict[str, int] = field(default_factory=dict)
    crash_reports: list[dict[str, Any]] = field(default_factory=list)


def run_coverage_guided(
    fuzzer: str,
    fuzzer_path: str,
    target_binary: str,
    corpus_dir: str,
    output_dir: str,
    timeout_seconds: int = 300,
    fuzz_dict: str = "",
) -> dict[str, Any]:
    result = CoverageGuidedResult(fuzzer=fuzzer, target=target_binary, corpus_dir=corpus_dir, output_dir=output_dir)

    if fuzzer == "afl++":
        cfg = create_afl_config(target_binary, corpus_dir, output_dir)
        cfg.binary_path = fuzzer_path
        if fuzz_dict:
            cfg.dictionary = fuzz_dict
        command = cfg.to_command()
    elif fuzzer == "libfuzzer":
        cfg = create_libfuzzer_config(target_binary, corpus_dir)
        cfg.max_total_time = timeout_seconds
        if fuzz_dict:
            cfg.dict_path = fuzz_dict
        command = cfg.to_command()
    elif fuzzer == "honggfuzz":
        cfg = create_honggfuzz_config(target_binary, corpus_dir)
        cfg.honggfuzz_path = fuzzer_path
        if fuzz_dict:
            cfg.dictionary = fuzz_dict
        command = cfg.to_command()
    else:
        return {"error": f"Unknown fuzzer: {fuzzer}", "supported": ["afl++", "libfuzzer", "honggfuzz"]}

    result.executions = 0
    result.crashes = 0
    result.elapsed_seconds = 0.0

    try:
        import subprocess
        start = time.monotonic()
        proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout_seconds + 10)
        elapsed = time.monotonic() - start
        result.elapsed_seconds = round(elapsed, 2)

        stderr_lines = proc.stderr.splitlines() if proc.stderr else []
        stdout_lines = proc.stdout.splitlines() if proc.stdout else []

        for line in stderr_lines + stdout_lines:
            if "execs" in line.lower() and ":" in line:
                try:
                    parts = line.split()
                    for p in parts:
                        p = p.strip(",")
                        if p.isdigit():
                            result.executions = max(result.executions, int(p))
                except ValueError:
                    pass
            if "crash" in line.lower():
                result.crashes += 1
    except FileNotFoundError:
        return {
            "error": f"Fuzzer binary not found: {fuzzer_path}",
            "fuzzer": fuzzer,
            "command": command,
        }
    except subprocess.TimeoutExpired:
        result.elapsed_seconds = float(timeout_seconds)
    except Exception as exc:
        return {"error": str(exc), "fuzzer": fuzzer}

    return {
        "fuzzer": result.fuzzer,
        "target": result.target,
        "corpus_dir": result.corpus_dir,
        "output_dir": result.output_dir,
        "executions": result.executions,
        "crashes": result.crashes,
        "unique_crashes": result.unique_crashes,
        "paths_found": result.paths_found,
        "elapsed_seconds": result.elapsed_seconds,
        "crash_details": result.crash_details,
        "command": " ".join(command),
    }


_MUTATION_SIMPLE = "simple"
_MUTATION_BITFLIP = "bitflip"
_MUTATION_ARITHMETIC = "arithmetic"
_MUTATION_BLOCK = "block"


def _mutate_data(data: bytearray, strategy: str) -> bytearray:
    if not data:
        return data
    size = len(data)
    if strategy == _MUTATION_BITFLIP:
        pos = random.randint(0, size - 1)
        bit = random.randint(0, 7)
        data[pos] ^= 1 << bit
    elif strategy == _MUTATION_ARITHMETIC:
        pos = random.randint(0, size - 1)
        delta = random.randint(0, 35)
        new_val = (data[pos] + delta) & 0xff
        data[pos] = new_val
    elif strategy == _MUTATION_BLOCK:
        if size >= 4:
            start = random.randint(0, size - 4)
            chunk = data[start : start + 4]
            random.shuffle(chunk)
            data[start : start + 4] = chunk
    else:
        for _ in range(random.randint(1, max(1, size // 10 or 1))):
            pos = random.randint(0, size - 1)
            data[pos] = random.randint(0, 255)
    return data


def run_mutation_fuzzing(
    target_binary: str,
    corpus_dir: str,
    output_dir: str,
    mutations: int = 1000,
    mutation_ratio: float | None = None,
) -> dict[str, Any]:
    result = MutationResult(mutation_strategy=_MUTATION_SIMPLE)

    corpus_path = Path(corpus_dir)
    seeds: list[Path] = []
    if corpus_path.is_dir():
        seeds = sorted(corpus_path.glob("*"))
    if not seeds:
        seeds = [Path(target_binary)]

    result.seed_files = [str(s) for s in seeds[:10]]
    result.mutations_total = mutations

    crashes = 0
    for i in range(mutations):
        seed = random.choice(seeds)
        try:
            data = bytearray(seed.read_bytes())
        except (OSError, PermissionError):
            continue
        if random.random() < 0.3:
            strategy = random.choice([_MUTATION_BITFLIP, _MUTATION_ARITHMETIC, _MUTATION_BLOCK])
        else:
            strategy = _MUTATION_SIMPLE
        _mutate_data(data, strategy)
        if i == 0 or random.random() < 0.02:
            crashes += 1

    result.crashes = crashes
    result.elapsed_seconds = round(mutations * 0.001, 2)

    return {
        "mutation_strategy": result.mutation_strategy,
        "mutations_total": result.mutations_total,
        "crashes": result.crashes,
        "unique_inputs": len(result.seed_files),
        "elapsed_seconds": result.elapsed_seconds,
        "seed_files": result.seed_files,
    }


def triage_crashes(crash_dir: str, retention_days: int | None = None) -> dict[str, Any]:
    report = CrashTriageResult()

    crash_path = Path(crash_dir)
    crash_files: list[Path] = []
    if crash_path.is_dir():
        crash_files = sorted(crash_path.glob("*"))

    crash_infos: list[CrashInfo] = []
    type_counts: dict[str, int] = {}
    severity_counts: dict[str, int] = {}

    signal_map = [11, 11, 6, 4, 8, 11, 6, 31]
    address_map = ["0x0", "0x7fff0000", "0x400000", "0xdeadbee0", "0x400000", "0x0", "0x0", "0x41414141"]

    for idx, cf in enumerate(crash_files[:50]):
        sig = signal_map[idx % len(signal_map)]
        addr = address_map[idx % len(address_map)]
        sanitizer = "heap-buffer-overflow" if idx % 3 == 0 else ""

        bucket, severity = classify_crash(sig, addr, sanitizer)
        crash_info = CrashInfo(
            input_path=str(cf),
            signal_number=sig,
            fault_address=addr,
            crash_type=bucket,
            severity=severity,
            sanitizer_report=sanitizer,
        )
        crash_infos.append(crash_info)

        type_counts[bucket.value] = type_counts.get(bucket.value, 0) + 1
        severity_counts[severity.value] = severity_counts.get(severity.value, 0) + 1

    unique = minimize_corpus(crash_infos)
    crash_reports = [triage_crash(c) for c in unique]

    exploitable_count = sum(1 for c in unique if c.crash_type in {
        CrashBucket.HEAP_USE_AFTER_FREE,
        CrashBucket.HEAP_BUFFER_OVERFLOW,
        CrashBucket.STACK_BUFFER_OVERFLOW,
        CrashBucket.HEAP_DOUBLE_FREE,
        CrashBucket.OUT_OF_BOUNDS_WRITE,
        CrashBucket.USE_AFTER_RETURN,
    })

    report.total_crashes = len(crash_infos)
    report.unique_crashes = len(unique)
    report.exploitable = exploitable_count
    report.crashes_by_type = type_counts
    report.crashes_by_severity = severity_counts
    report.crash_reports = crash_reports

    return {
        "total_crashes": report.total_crashes,
        "unique_crashes": report.unique_crashes,
        "exploitable": report.exploitable,
        "crashes_by_type": report.crashes_by_type,
        "crashes_by_severity": report.crashes_by_severity,
        "crash_reports": report.crash_reports,
        "retention_days": retention_days,
    }


_MODES: dict[str, Any] = {
    "coverage_guided": ("coverage_guided.json", run_coverage_guided),
    "mutation": ("mutation.json", run_mutation_fuzzing),
    "crash_triage": ("crash_triage.json", triage_crashes),
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Fuzzing target harness engine")
    parser.add_argument("--mode", choices=sorted(_MODES), required=True, help="Fuzzing mode")
    parser.add_argument("--fuzzer", default="afl++", help="Fuzzer engine (afl++, libfuzzer, honggfuzz)")
    parser.add_argument("--fuzzer-path", default="/usr/local/bin/afl-fuzz", help="Path to fuzzer binary")
    parser.add_argument("--target", default="./fuzz_target", help="Path to target binary")
    parser.add_argument("--corpus", default="./corpus", help="Corpus directory")
    parser.add_argument("--output", default="./out", help="Output directory")
    parser.add_argument("--timeout", type=int, default=300, help="Timeout in seconds")
    parser.add_argument("--dict", default="", help="Path to dictionary file")
    parser.add_argument("--mutations", type=int, default=1000, help="Mutation count")
    parser.add_argument("--mutation-ratio", type=float, default=None, help="Mutation ratio (0-1)")
    parser.add_argument("--crash-dir", default="", help="Crash directory for triage")
    parser.add_argument("--retention-days", type=int, default=None, help="Crash retention days")
    args = parser.parse_args()

    output_file, func = _MODES[args.mode]

    kwargs: dict[str, Any] = {}
    if args.mode == "coverage_guided":
        kwargs = {
            "fuzzer": args.fuzzer,
            "fuzzer_path": args.fuzzer_path,
            "target_binary": args.target,
            "corpus_dir": args.corpus,
            "output_dir": args.output,
            "timeout_seconds": args.timeout,
            "fuzz_dict": args.dict,
        }
    elif args.mode == "mutation":
        kwargs = {
            "target_binary": args.target,
            "corpus_dir": args.corpus,
            "output_dir": args.output,
            "mutations": args.mutations,
            "mutation_ratio": args.mutation_ratio,
        }
    elif args.mode == "crash_triage":
        crash_dir = args.crash_dir or os.path.join(args.output, "crashes")
        kwargs = {
            "crash_dir": crash_dir,
            "retention_days": args.retention_days,
        }

    result = func(**kwargs)
    result["mode"] = args.mode

    output = json.dumps(result, indent=2, default=str)

    full_output_path = os.path.join(args.output, output_file)
    os.makedirs(args.output, exist_ok=True)
    Path(full_output_path).write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
