"""
trace_evidence_examiner -- Standalone module_utils for the trace_evidence_examiner
Ansible role. Wraps materials_forensics.analyze_trace_evidence with file I/O.

Public surface:
    examine_trace_evidence(evidence_type, sample_data, reference_data) -> dict
    write_verdict(result, output_dir)                                   -> Path
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .materials_forensics import analyze_trace_evidence


def examine_trace_evidence(
    evidence_type: str,
    sample_data: dict[str, Any],
    reference_data: dict[str, Any],
) -> dict[str, Any]:
    """Run trace evidence analysis and return enriched result.

    Args:
        evidence_type: One of 'fiber', 'hair', 'glass', 'paint', 'soil',
                       'gsr', 'toolmark', 'footwear', 'tire'.
        sample_data: Dict of sample measurements.
        reference_data: Dict of reference measurements.

    Returns:
        dict with analysis result + timestamp + evidence_type.
    """
    result = analyze_trace_evidence(evidence_type, sample_data, reference_data)
    result["timestamp"] = datetime.now(timezone.utc).isoformat()
    result["analysis_module"] = "trace_evidence_examiner"
    return result


def write_verdict(result: dict[str, Any], output_dir: str) -> Path:
    """Write the analysis verdict as a JSON file.

    Args:
        result: The analysis result dict from examine_trace_evidence.
        output_dir: Directory to write the verdict file.

    Returns:
        Path to the written verdict file.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    out_file = out / "trace_evidence_verdict.json"
    out_file.write_text(json.dumps(result, indent=2, default=str))
    return out_file


__all__ = ["examine_trace_evidence", "write_verdict"]
