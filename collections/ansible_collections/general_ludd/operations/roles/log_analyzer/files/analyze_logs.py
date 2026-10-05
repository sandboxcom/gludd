#!/usr/bin/env python3
"""CLI entry point for log analysis, invoked by the log_analyzer Ansible role."""
from __future__ import annotations

import argparse
import json
import sys

from ansible_collections.general_ludd.operations.plugins.module_utils.log_analyzer import (
    analyze,
)


def main() -> None:
    p = argparse.ArgumentParser(description="Analyse log files for errors and clusters")
    p.add_argument("--log-dir", required=True, help="Directory containing log files")
    p.add_argument("--glob", dest="glob_str", required=True, help="Glob pattern for log files")
    p.add_argument("--output-dir", required=True, help="Output directory for reports")
    p.add_argument("--error-threshold", type=float, default=0.1)
    p.add_argument("--cluster-window", type=int, default=300)
    p.add_argument("--min-cluster-size", type=int, default=2)
    args = p.parse_args()

    result = analyze(
        log_dir=args.log_dir,
        glob_pattern=args.glob_str,
        output_dir=args.output_dir,
        error_threshold=args.error_threshold,
        cluster_window=args.cluster_window,
        min_cluster_size=args.min_cluster_size,
    )
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
