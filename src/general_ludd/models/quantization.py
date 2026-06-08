"""Quantization detection, tracking, and routing integration.

Detection priority (highest to lowest confidence):
1. Provider API (Fireworks default_precision, TGI model_dtype)
2. HuggingFace metadata (safetensors dtype, gguf block, quantized tags)
3. Self-probe (arithmetic stress test, logprob entropy)
4. OpenRouter /endpoints (freeform quantization string) — last resort
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar

import httpx

logger = logging.getLogger(__name__)


class Precision(StrEnum):
    FP32 = "fp32"
    BF16 = "bf16"
    FP16 = "fp16"
    FP8 = "fp8"
    INT8 = "int8"
    INT4 = "int4"
    NF4 = "nf4"
    FP4 = "fp4"
    GGUF_Q4 = "gguf_q4"
    GGUF_Q5 = "gguf_q5"
    GGUF_Q8 = "gguf_q8"
    UNKNOWN = "unknown"

    def bits(self) -> int:
        _BITS: dict[str, int] = {
            "fp32": 32,
            "bf16": 16,
            "fp16": 16,
            "fp8": 8,
            "int8": 8,
            "int4": 4,
            "nf4": 4,
            "fp4": 4,
            "gguf_q4": 4,
            "gguf_q5": 5,
            "gguf_q8": 8,
            "unknown": 0,
        }
        return _BITS.get(self.value, 0)

    def quality_score(self) -> float:
        _SCORES: dict[str, float] = {
            "fp32": 1.0,
            "bf16": 0.98,
            "fp16": 0.98,
            "fp8": 0.85,
            "int8": 0.80,
            "int4": 0.55,
            "nf4": 0.60,
            "fp4": 0.50,
            "gguf_q4": 0.55,
            "gguf_q5": 0.65,
            "gguf_q8": 0.80,
            "unknown": 0.70,
        }
        return _SCORES.get(self.value, 0.70)

    @classmethod
    def from_string(cls, raw: str) -> Precision:
        normalized = raw.strip().lower().replace("-", "").replace("_", "")
        _MAP: dict[str, Precision] = {
            "fp32": cls.FP32,
            "float32": cls.FP32,
            "f32": cls.FP32,
            "bf16": cls.BF16,
            "bfloat16": cls.BF16,
            "bfloat": cls.BF16,
            "fp16": cls.FP16,
            "float16": cls.FP16,
            "f16": cls.FP16,
            "fp8": cls.FP8,
            "float8": cls.FP8,
            "fp8mm": cls.FP8,
            "fp8ar": cls.FP8,
            "fp8kv": cls.FP8,
            "int8": cls.INT8,
            "i8": cls.INT8,
            "int4": cls.INT4,
            "i4": cls.INT4,
            "nf4": cls.NF4,
            "fp4": cls.FP4,
        }
        if normalized in _MAP:
            return _MAP[normalized]
        gguf_match = re.match(r"q(\d)", normalized)
        if gguf_match:
            q_bits = int(gguf_match.group(1))
            if q_bits == 4:
                return cls.GGUF_Q4
            if q_bits == 5:
                return cls.GGUF_Q5
            if q_bits >= 8:
                return cls.GGUF_Q8
        if "gptq" in normalized:
            return cls.INT4
        if "awq" in normalized:
            return cls.INT4
        if "gguf" in normalized:
            return cls.GGUF_Q4
        if "quantiz" in normalized:
            return cls.UNKNOWN
        return cls.UNKNOWN


@dataclass
class QuantizationInfo:
    precision: str
    source: str = "unknown"
    confidence: float = 0.0
    provider_name: str | None = None
    bits_estimate: int | None = None
    raw_data: dict[str, Any] | None = None
    detected_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if self.bits_estimate is None:
            p = Precision.from_string(self.precision)
            self.bits_estimate = p.bits() or None


class FireworksDetector:
    """Primary source: Fireworks AI management API exposes default_precision."""

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    async def detect(self, model_id: str) -> list[QuantizationInfo]:
        if not self._api_key:
            return []
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    "https://api.fireworks.ai/management/v1/models",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
                if resp.status_code != 200:
                    return []
                data = resp.json()
        except Exception as exc:
            logger.debug("Fireworks quantization check failed: %s", exc)
            return []

        results: list[QuantizationInfo] = []
        for m in data.get("models", []):
            if m.get("name", "").lower() != model_id.lower():
                continue
            details = m.get("baseModelDetails", {})
            raw_precision = details.get("default_precision", "")
            if raw_precision:
                results.append(QuantizationInfo(
                    precision=Precision.from_string(raw_precision).value,
                    source="provider_api",
                    confidence=0.95,
                    provider_name="fireworks",
                    raw_data={"fireworks_precision": raw_precision},
                ))
        return results


class HuggingFaceDetector:
    """Secondary source: HuggingFace model card metadata."""

    async def detect(self, model_id: str) -> list[QuantizationInfo]:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"https://huggingface.co/api/models/{model_id}",
                    headers={"Accept": "application/json"},
                )
                if resp.status_code != 200:
                    return []
                data = resp.json()
        except Exception as exc:
            logger.debug("HF quantization check failed: %s", exc)
            return []

        results: list[QuantizationInfo] = []

        safetensors = data.get("safetensors", {})
        if isinstance(safetensors, dict):
            params = safetensors.get("parameters", {})
            if isinstance(params, dict):
                for dtype_str in params:
                    precision = Precision.from_string(dtype_str)
                    if precision != Precision.UNKNOWN:
                        results.append(QuantizationInfo(
                            precision=precision.value,
                            source="huggingface_metadata",
                            confidence=0.85,
                            provider_name="huggingface",
                            raw_data={"safetensors_dtype": dtype_str},
                        ))

        gguf = data.get("gguf")
        if isinstance(gguf, dict):
            for sibling in data.get("siblings", []):
                fname = sibling.get("rfilename", "")
                q_match = re.search(r"\.(Q\d[_A-Z]*)\.gguf$", fname, re.IGNORECASE)
                if q_match:
                    precision = Precision.from_string(q_match.group(1))
                    results.append(QuantizationInfo(
                        precision=precision.value,
                        source="huggingface_metadata",
                        confidence=0.90,
                        provider_name="huggingface",
                        raw_data={"gguf_file": fname},
                    ))

        tags = data.get("tags", [])
        card = data.get("cardData", {})
        quant_by = card.get("quantized_by", "")
        quant_tags = [t for t in tags if "quantized" in t.lower() or "gptq" in t.lower() or "awq" in t.lower()]
        for tag in quant_tags:
            tag_lower = tag.lower()
            if "gptq" in tag_lower or "awq" in tag_lower:
                results.append(QuantizationInfo(
                    precision=Precision.INT4.value,
                    source="huggingface_metadata",
                    confidence=0.80,
                    provider_name="huggingface",
                    raw_data={"tag": tag, "quantized_by": quant_by},
                ))
            elif "quantized" in tag_lower and "gguf" not in tag_lower:
                results.append(QuantizationInfo(
                    precision=Precision.UNKNOWN.value,
                    source="huggingface_metadata",
                    confidence=0.50,
                    provider_name="huggingface",
                    raw_data={"tag": tag, "quantized_by": quant_by},
                ))
        if quant_by and not quant_tags:
            results.append(QuantizationInfo(
                precision=Precision.INT4.value,
                source="huggingface_metadata",
                confidence=0.60,
                provider_name="huggingface",
                raw_data={"quantized_by": quant_by},
            ))

        return results


class SelfProbeDetector:
    """Tertiary source: probe model with arithmetic task to infer quantization."""

    _PROBLEMS: ClassVar[list[tuple[float, float, float]]] = [
        (7.8391, 4.2173, 2.9961),
        (3.14159, 2.71828, 1.41421),
        (9.8765, 1.2345, 6.7890),
    ]

    def arithmetic_probe_prompt(self) -> str:
        a, b, c = self._PROBLEMS[0]
        return (
            f"Compute the product: {a} x {b} x {c}. "
            "Show your work step by step and give the exact result to 10 decimal places."
        )

    def score_arithmetic_response(self, response: str, expected: float) -> float:
        numbers = re.findall(r"(\d+\.\d+)", response)
        if not numbers:
            return 0.0
        best_score = 0.0
        for num_str in numbers:
            try:
                val = float(num_str)
            except ValueError:
                continue
            if expected == 0:
                continue
            relative_error = abs(val - expected) / abs(expected)
            score = max(0.0, 1.0 - relative_error * 10)
            best_score = max(best_score, score)
        return best_score

    def infer_precision_from_score(self, score: float) -> str:
        if score >= 0.90:
            return Precision.FP16.value
        if score >= 0.75:
            return Precision.FP8.value
        if score >= 0.50:
            return Precision.INT8.value
        return Precision.INT4.value

    def logprob_entropy_score(self, logprobs: list[float]) -> float:
        if not logprobs:
            return 0.5
        max_lp = max(logprobs)
        gaps = [max_lp - lp for lp in logprobs]
        avg_gap = sum(gaps) / len(gaps)
        return max(0.0, min(1.0, 1.0 - avg_gap / 5.0))


class OpenRouterEndpointDetector:
    """Last resort: OpenRouter per-endpoint quantization field."""

    async def detect(self, model_slug: str) -> list[QuantizationInfo]:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"https://openrouter.ai/api/v1/models/{model_slug}/endpoints",
                )
                if resp.status_code != 200:
                    return []
                data = resp.json()
        except Exception as exc:
            logger.debug("OpenRouter endpoints check failed: %s", exc)
            return []

        endpoints = data.get("data", {}).get("endpoints", [])
        results: list[QuantizationInfo] = []
        for ep in endpoints:
            raw_quant = ep.get("quantization", "unknown")
            provider_name = ep.get("provider_name", "unknown")
            results.append(QuantizationInfo(
                precision=Precision.from_string(raw_quant).value,
                source="openrouter_endpoints",
                confidence=0.60 if raw_quant != "unknown" else 0.20,
                provider_name=provider_name,
                raw_data={
                    "tag": ep.get("tag", ""),
                    "openrouter_quantization": raw_quant,
                },
            ))
        return results


class QuantizationTracker:
    """Tracks quantization info per model, keeps highest-confidence result."""

    def __init__(self) -> None:
        self._data: dict[str, QuantizationInfo] = {}

    def update(self, model_id: str, info: QuantizationInfo) -> None:
        existing = self._data.get(model_id)
        if existing is None or info.confidence >= existing.confidence:
            self._data[model_id] = info

    def get(self, model_id: str) -> QuantizationInfo | None:
        return self._data.get(model_id)

    def list_all(self) -> dict[str, QuantizationInfo]:
        return dict(self._data)

    def check_drift(self, model_id: str, new_info: QuantizationInfo) -> bool:
        existing = self._data.get(model_id)
        if existing is None:
            return False
        return existing.precision != new_info.precision

    def to_dict(self) -> dict[str, dict[str, Any]]:
        return {
            mid: {
                "precision": qi.precision,
                "source": qi.source,
                "confidence": qi.confidence,
                "provider_name": qi.provider_name,
                "bits_estimate": qi.bits_estimate,
                "detected_at": qi.detected_at,
            }
            for mid, qi in self._data.items()
        }

    @classmethod
    def from_dict(cls, data: dict[str, dict[str, Any]]) -> QuantizationTracker:
        tracker = cls()
        for mid, d in data.items():
            tracker._data[mid] = QuantizationInfo(
                precision=d["precision"],
                source=d.get("source", "unknown"),
                confidence=d.get("confidence", 0.0),
                provider_name=d.get("provider_name"),
                bits_estimate=d.get("bits_estimate"),
                detected_at=d.get("detected_at", time.time()),
            )
        return tracker


_PRECISION_QUALITY_IMPACT: dict[str, str] = {
    "fp32": "none",
    "bf16": "none",
    "fp16": "none",
    "fp8": "slight",
    "int8": "moderate",
    "int4": "severe",
    "nf4": "severe",
    "fp4": "severe",
    "gguf_q4": "severe",
    "gguf_q5": "moderate",
    "gguf_q8": "slight",
    "unknown": "none",
}


def adjust_quality_for_quantization(
    base_quality: str,
    precision: str,
) -> str:
    impact = _PRECISION_QUALITY_IMPACT.get(precision, "none")
    if impact == "none":
        return base_quality
    if impact == "slight":
        if base_quality == "high":
            return "medium"
        return base_quality
    if impact in ("moderate", "severe"):
        if base_quality in ("high", "medium"):
            return "low"
        return base_quality
    return base_quality


_TASK_PRECISION_REQUIREMENTS: dict[str, str] = {
    "security_fix": "high",
    "optimization": "medium",
    "bug_fix": "low",
    "feature": "low",
    "refactor": "low",
    "test_write": "low",
    "code_review": "medium",
    "documentation": "none",
    "debugging": "medium",
    "integration": "low",
}


def task_precision_suitability(task_type: str, precision: str) -> str:
    requirement = _TASK_PRECISION_REQUIREMENTS.get(task_type, "low")
    precision_val = Precision.from_string(precision)
    quality = precision_val.quality_score()
    if requirement == "none":
        return "suitable"
    if requirement == "low":
        return "suitable" if quality >= 0.40 else "unsuitable"
    if requirement == "medium":
        if quality >= 0.75:
            return "suitable"
        if quality >= 0.55:
            return "caution"
        return "unsuitable"
    if requirement == "high":
        if quality >= 0.90:
            return "suitable"
        if quality >= 0.75:
            return "caution"
        return "unsuitable"
    return "suitable"


def rank_models_for_task(
    task_type: str,
    models: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    requirement = _TASK_PRECISION_REQUIREMENTS.get(task_type, "low")
    costs = [m.get("cost", 1.0) for m in models]
    max_cost = max(costs) if costs else 1.0
    if max_cost == 0:
        max_cost = 1.0
    scored = []
    for m in models:
        precision = m.get("precision", "unknown")
        p = Precision.from_string(precision)
        quality = p.quality_score()
        cost = m.get("cost", 1.0)
        cost_ratio = cost / max_cost if max_cost > 0 else 1.0
        if requirement == "high":
            score = quality * 0.9 - cost_ratio * 0.1
        elif requirement == "none":
            score = -cost_ratio * 0.9 + quality * 0.1
        else:
            score = quality * 0.5 - cost_ratio * 0.5
        scored.append((score, m))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [m for _, m in scored]
