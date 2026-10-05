"""Typed dispatch adapter for the collection's forensic Ansible module."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

from .chain_of_custody import create_chain_of_custody
from .materials_forensics import classify_fingerprint, match_dna_profile
from .photo_forensics import (
    compute_ela,
    detect_ai_generated,
    detect_modifications,
    extract_metadata,
    identify_camera,
)
from .trace_evidence_examiner import examine_trace_evidence

AnalysisResult = dict[str, Any]
AnalysisHandler = Callable[[Mapping[str, object]], AnalysisResult]


def _mapping(arguments: Mapping[str, object], key: str) -> dict[str, Any]:
    value = arguments.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return cast(dict[str, Any], value)


def _required_string(arguments: Mapping[str, object], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _dna(arguments: Mapping[str, object]) -> AnalysisResult:
    return match_dna_profile(
        sample=_mapping(arguments, "sample"),
        reference=_mapping(arguments, "reference"),
        analysis_type=_required_string(arguments, "analysis_type"),
    )


def _fingerprint(arguments: Mapping[str, object]) -> AnalysisResult:
    return classify_fingerprint(_mapping(arguments, "data"))


def _chain_of_custody(arguments: Mapping[str, object]) -> AnalysisResult:
    chain = create_chain_of_custody(_required_string(arguments, "case_id"))
    return {
        "case_id": chain.case_id,
        "created_at": chain.created_at,
        "status": "initialized",
    }


def _photo(arguments: Mapping[str, object]) -> AnalysisResult:
    image_path = Path(_required_string(arguments, "image_path"))
    analysis_types = arguments.get("analysis_types")
    if not isinstance(analysis_types, list) or not analysis_types:
        raise ValueError("analysis_types must be a non-empty list")
    selected = [str(value) for value in analysis_types]
    allowed: dict[str, Callable[[bytes], AnalysisResult]] = {
        "metadata": extract_metadata,
        "ela": compute_ela,
        "modification": detect_modifications,
        "ai_detection": detect_ai_generated,
        "camera_id": identify_camera,
    }
    unsupported = sorted(set(selected) - allowed.keys())
    if unsupported:
        raise ValueError(f"unsupported photo analysis type(s): {', '.join(unsupported)}")
    data = image_path.read_bytes()
    if not data:
        raise ValueError("image_path must identify a non-empty file")
    result: AnalysisResult = {name: allowed[name](data) for name in selected}
    result.update({"analysis_types_run": selected, "image_path": str(image_path)})
    return result


def _trace(arguments: Mapping[str, object]) -> AnalysisResult:
    result = examine_trace_evidence(
        evidence_type=_required_string(arguments, "evidence_type"),
        sample_data=_mapping(arguments, "sample"),
        reference_data=_mapping(arguments, "reference"),
    )
    output_dir = Path(_required_string(arguments, "output_dir"))
    result["output_path"] = str(output_dir / "trace_evidence_verdict.json")
    return result


_HANDLERS: dict[str, AnalysisHandler] = {
    "chain_of_custody": _chain_of_custody,
    "dna": _dna,
    "fingerprint": _fingerprint,
    "photo": _photo,
    "trace": _trace,
}


def run_analysis(operation: str, arguments: Mapping[str, object]) -> AnalysisResult:
    """Run one supported forensic operation without ambient import paths."""
    handler = _HANDLERS.get(operation)
    if handler is None:
        raise ValueError(f"unsupported forensic operation: {operation}")
    return handler(arguments)


__all__ = ["run_analysis"]
