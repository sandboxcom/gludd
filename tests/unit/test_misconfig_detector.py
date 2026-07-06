"""Dedup verification: exactly one MisconfigDetector class exists (#76).

After canonicalizing the two MisconfigDetector implementations into
``model_deploy_check.py``, this test verifies:
1. Only ONE ``MisconfigDetector`` class exists in ``src/general_ludd/infra/``.
2. The deleted ``misconfig_detector.py`` is absent.
3. The canonical ``MisconfigDetector`` instantiates and produces findings.
"""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from typing import Any, cast

import pytest

from general_ludd.infra.model_deploy_check import MisconfigDetector as Canonical

SRC_INFRA = Path(__file__).resolve().parents[2] / "src" / "general_ludd" / "infra"


# ---------------------------------------------------------------------------
# Single-definition checks
# ---------------------------------------------------------------------------


def test_misconfig_detector_file_is_absent() -> None:
    """The old misconfig_detector.py must not exist."""
    assert not (SRC_INFRA / "misconfig_detector.py").is_file(), (
        "misconfig_detector.py still exists — it should have been deleted"
    )


def test_exactly_one_misconfig_detector_class_in_infra() -> None:
    """Only model_deploy_check.py defines MisconfigDetector; no duplicates."""
    count = 0
    locations: list[str] = []
    for py_file in sorted(SRC_INFRA.glob("*.py")):
        name = py_file.stem
        if name.startswith("_"):
            continue
        try:
            module = importlib.import_module(f"general_ludd.infra.{name}")
        except ImportError:
            continue
        for attr_name, attr_value in vars(module).items():
            if (
                attr_name == "MisconfigDetector"
                and inspect.isclass(attr_value)
                and attr_value.__module__ == f"general_ludd.infra.{name}"
            ):
                count += 1
                locations.append(f"{name}.py")
    assert count == 1, (
        f"Expected 1 MisconfigDetector class defined in src/general_ludd/infra/, "
        f"found {count}: {locations}"
    )


# ---------------------------------------------------------------------------
# Canonical class works
# ---------------------------------------------------------------------------


def test_canonical_instantiates() -> None:
    detector = Canonical()
    assert detector is not None


def test_canonical_check_returns_list() -> None:
    detector = Canonical()
    cfg = {
        "engine": "vllm",
        "model": {
            "name": "test-model",
            "num_layers": 32,
            "num_kv_heads": 8,
            "head_dim": 128,
            "params_b": 8.0,
        },
        "gpu_memory_utilization": 0.90,
        "tensor_parallel_size": 1,
        "pipeline_parallel_size": 1,
        "max_model_len": 8192,
        "max_num_seqs": 16,
        "enforce_eager": False,
    }
    gpu = {
        "gpu_type": "H100",
        "gpu_count": 1,
        "vram_gb": 80.0,
        "arch": "hopper",
        "has_nvlink": True,
        "supports_fp8": True,
        "compute_capability": 9.0,
    }
    findings = detector.check(cfg, gpu)
    assert isinstance(findings, list)


def test_canonical_detects_bad_config() -> None:
    detector = Canonical()
    cfg = {
        "engine": "vllm",
        "model": {
            "name": "test-model",
            "num_layers": 32,
            "num_kv_heads": 8,
            "head_dim": 128,
            "params_b": 8.0,
        },
        "gpu_memory_utilization": 0.99,
        "max_model_len": 1_000_000,
        "max_num_seqs": 256,
    }
    gpu = {
        "gpu_type": "H100",
        "gpu_count": 1,
        "vram_gb": 80.0,
        "arch": "hopper",
        "has_nvlink": True,
        "supports_fp8": True,
        "compute_capability": 9.0,
    }
    findings = detector.check(cfg, gpu)
    rule_ids = {f.rule_id for f in findings}
    assert "a" in rule_ids or "b" in rule_ids, (
        f"Expected at least rule a or b to fire on bad config, got: {rule_ids}"
    )


def test_malformed_input_never_raises() -> None:
    detector = Canonical()
    for bad in [None, "string", 42, []]:
        try:
            findings = cast(Any, detector).check(bad)
            assert isinstance(findings, list)
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"MisconfigDetector.check raised on {bad!r}: {exc}")
