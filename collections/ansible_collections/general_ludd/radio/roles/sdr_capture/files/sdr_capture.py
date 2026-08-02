#!/usr/bin/env python3
"""
sdr_capture — SDR IQ sample capture, statistics, and JSON verdict.

Usage:
    python sdr_capture.py --freq FREQ_HZ --sample-rate RATE --duration SEC
        [--gain GAIN] [--device DEV] [--format FMT] [--output-dir DIR]

Output: JSON with capture statistics, file metadata, and verdict.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import struct
from dataclasses import dataclass
from typing import Any

FORMAT_BYTES: dict[str, int] = {
    "int8": 1,
    "int16": 2,
    "float32": 4,
}


@dataclass
class CaptureResult:
    freq_hz: int
    sample_rate: int
    duration_sec: float
    sample_count: int
    format: str
    format_bytes: int
    device_index: int
    gain: str
    output_file: str
    output_dir: str
    tool: str
    rc: int = -1
    stderr: str = ""
    file_size_bytes: int = 0
    actual_sample_count: int = 0
    actual_duration_sec: float = 0.0
    i_min: float = 0.0
    i_max: float = 0.0
    i_mean: float = 0.0
    i_std: float = 0.0
    i_rms: float = 0.0
    q_min: float = 0.0
    q_max: float = 0.0
    q_mean: float = 0.0
    q_std: float = 0.0
    q_rms: float = 0.0
    dc_offset_i: float = 0.0
    dc_offset_q: float = 0.0
    peak_power_db: float = 0.0
    avg_power_db: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "freq_hz": self.freq_hz,
            "sample_rate": self.sample_rate,
            "duration_sec": self.duration_sec,
            "sample_count": self.sample_count,
            "format": self.format,
            "format_bytes": self.format_bytes,
            "device_index": self.device_index,
            "gain": self.gain,
            "output_file": self.output_file,
            "output_dir": self.output_dir,
            "tool": self.tool,
            "rc": self.rc,
            "stderr": self.stderr,
            "file_size_bytes": self.file_size_bytes,
            "actual_sample_count": self.actual_sample_count,
            "actual_duration_sec": round(self.actual_duration_sec, 4),
            "iq_stats": {
                "i_min": round(self.i_min, 4),
                "i_max": round(self.i_max, 4),
                "i_mean": round(self.i_mean, 4),
                "i_std": round(self.i_std, 4),
                "i_rms": round(self.i_rms, 4),
                "q_min": round(self.q_min, 4),
                "q_max": round(self.q_max, 4),
                "q_mean": round(self.q_mean, 4),
                "q_std": round(self.q_std, 4),
                "q_rms": round(self.q_rms, 4),
                "dc_offset_i": round(self.dc_offset_i, 4),
                "dc_offset_q": round(self.dc_offset_q, 4),
                "peak_power_db": round(self.peak_power_db, 2),
                "avg_power_db": round(self.avg_power_db, 2),
            },
            "verdict": "success" if self.rc == 0 else "skipped",
        }


def _read_iq_samples(file_path: str, fmt: str) -> tuple[list[float], list[float]]:
    i_samples: list[float] = []
    q_samples: list[float] = []

    try:
        with open(file_path, "rb") as f:
            data = f.read()
    except (OSError, FileNotFoundError):
        return i_samples, q_samples

    if fmt == "int8":
        items = struct.unpack(f"{len(data)}b", data)
    elif fmt == "int16":
        items = struct.unpack(f"{len(data) // 2}h", data)
    elif fmt == "float32":
        items = struct.unpack(f"{len(data) // 4}f", data)
    else:
        return i_samples, q_samples

    for i in range(0, len(items) - 1, 2):
        i_samples.append(float(items[i]))
        q_samples.append(float(items[i + 1]))

    return i_samples, q_samples


def _compute_sample_stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"min": 0.0, "max": 0.0, "mean": 0.0, "std": 0.0, "rms": 0.0}

    n = len(values)
    mean_val = sum(values) / n
    var = sum((v - mean_val) ** 2 for v in values) / n
    std = math.sqrt(var)
    rms = math.sqrt(sum(v ** 2 for v in values) / n)

    return {
        "min": min(values),
        "max": max(values),
        "mean": round(mean_val, 4),
        "std": round(std, 4),
        "rms": round(rms, 4),
    }


def capture_iq(
    freq_hz: int,
    sample_rate: int,
    duration_sec: float,
    gain: str = "auto",
    device_index: int = 0,
    fmt: str = "int16",
    output_dir: str = "/tmp/gludd-sdr-capture",
    tool: str = "rtl_sdr",
) -> dict[str, Any]:
    fmt_bytes = FORMAT_BYTES.get(fmt, 2)
    sample_count = int(duration_sec * sample_rate)
    output_file = os.path.join(output_dir, "iq_samples.bin")

    os.makedirs(output_dir, exist_ok=True)

    result = CaptureResult(
        freq_hz=freq_hz,
        sample_rate=sample_rate,
        duration_sec=duration_sec,
        sample_count=sample_count,
        format=fmt,
        format_bytes=fmt_bytes,
        device_index=device_index,
        gain=gain,
        output_file=output_file,
        output_dir=output_dir,
        tool=tool,
    )

    tool_path = os.environ.get("SDR_CAPTURE_TOOL_PATH", "")
    if not tool_path:
        import shutil
        tool_path = shutil.which(tool) or ""

    if not tool_path:
        result.rc = -1
        result.stderr = f"{tool} not found"
        return result.to_dict()

    try:
        import subprocess

        cmd = [
            tool_path,
            "-f", str(freq_hz),
            "-s", str(sample_rate),
            "-g", str(gain),
            "-n", str(sample_count),
            str(output_file),
        ]

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=max(30, int(duration_sec * 2)))
        result.rc = proc.returncode
        result.stderr = proc.stderr[:1000] if proc.stderr else ""

    except FileNotFoundError:
        result.rc = -1
        result.stderr = f"tool not found: {tool_path}"
    except subprocess.TimeoutExpired:
        result.rc = -1
        result.stderr = "capture timed out"
    except Exception as exc:
        result.rc = -1
        result.stderr = str(exc)

    if os.path.isfile(output_file):
        result.file_size_bytes = os.path.getsize(output_file)
        result.actual_sample_count = result.file_size_bytes // (fmt_bytes * 2)
        result.actual_duration_sec = result.actual_sample_count / sample_rate if sample_rate else 0.0

        i_data, q_data = _read_iq_samples(output_file, fmt)
        if i_data and q_data:
            i_stats = _compute_sample_stats(i_data)
            q_stats = _compute_sample_stats(q_data)
            result.i_min = i_stats["min"]
            result.i_max = i_stats["max"]
            result.i_mean = i_stats["mean"]
            result.i_std = i_stats["std"]
            result.i_rms = i_stats["rms"]
            result.q_min = q_stats["min"]
            result.q_max = q_stats["max"]
            result.q_mean = q_stats["mean"]
            result.q_std = q_stats["std"]
            result.q_rms = q_stats["rms"]
            result.dc_offset_i = i_stats["mean"]
            result.dc_offset_q = q_stats["mean"]
            result.peak_power_db = 10.0 * math.log10(max(
                i_stats["rms"] ** 2 + q_stats["rms"] ** 2,
                1e-12,
            ))
            result.avg_power_db = 10.0 * math.log10(
                (i_stats["rms"] ** 2 + q_stats["rms"] ** 2) / 2.0 + 1e-12
            )

    return result.to_dict()


def main() -> None:
    parser = argparse.ArgumentParser(description="SDR IQ sample capture and analysis")
    parser.add_argument("--freq", type=int, default=100_000_000, help="Center frequency in Hz")
    parser.add_argument("--sample-rate", type=int, default=2_048_000, help="Sample rate in Hz")
    parser.add_argument("--duration", type=float, default=1.0, help="Capture duration in seconds")
    parser.add_argument("--gain", type=str, default="auto", help="Gain setting")
    parser.add_argument("--device", type=int, default=0, help="Device index")
    parser.add_argument("--format", choices=["int8", "int16", "float32"], default="int16", help="Sample format")
    parser.add_argument("--output-dir", type=str, default="/tmp/gludd-sdr-capture", help="Output directory")
    parser.add_argument("--tool", type=str, default="rtl_sdr", help="SDR tool name")
    parser.add_argument("--tool-path", type=str, default="", help="Override tool path")
    args = parser.parse_args()

    if args.tool_path:
        os.environ["SDR_CAPTURE_TOOL_PATH"] = args.tool_path

    result = capture_iq(
        freq_hz=args.freq,
        sample_rate=args.sample_rate,
        duration_sec=args.duration,
        gain=args.gain,
        device_index=args.device,
        fmt=args.format,
        output_dir=args.output_dir,
        tool=args.tool,
    )

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
