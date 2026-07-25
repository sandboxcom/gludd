"""Quantization artifact monitor — tunable demon subsystem.

Detects when model responses show signs of quantization (lower precision
than claimed), indicating the deployment may be serving a compressed variant.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class QuantizationScore:
    """Per-response quantization artifact score (0-1, higher = more likely quantized)."""

    model_id: str
    score: float
    artifacts: list[str] = field(default_factory=list)
    threshold_exceeded: bool = False
    checked_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "score": round(self.score, 4),
            "artifacts": self.artifacts,
            "threshold_exceeded": self.threshold_exceeded,
            "checked_at": self.checked_at,
        }


@dataclass
class MonitorConfig:
    alert_threshold: float = 0.7
    check_interval_s: int = 300
    max_history_samples: int = 1000
    cooldown_alerts_s: int = 600

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_threshold": self.alert_threshold,
            "check_interval_s": self.check_interval_s,
            "max_history_samples": self.max_history_samples,
            "cooldown_alerts_s": self.cooldown_alerts_s,
        }


_REPETITIVE_PATTERN = re.compile(r"(.+?)\1{4,}")
_VOCAB_SIMPLIFIERS = {
    r"\b(er|um|uh)\b": "hesitation",
    r"\b(is|are|were|was)\s+(is|are|were|was)\b": "verb repetition",
    r"\b(very|really|quite|extremely)\s+(very|really|quite|extremely)\b": "intensifier stacking",
    r"\b(the|a|an)\s+(the|a|an)\b": "article doubling",
}


class QuantizationMonitor:
    """Tunable subsystem that monitors model responses for quantization artifacts.

    Runs as a background task in the demon. Alerts when a model shows signs
    of being quantized (lower precision than claimed), indicating the
    deployment might be serving a compressed/optimized variant.
    """

    def __init__(self, config: MonitorConfig | None = None) -> None:
        self._config = config or MonitorConfig()
        self._scores: dict[str, list[QuantizationScore]] = {}
        self._alerts: list[dict[str, Any]] = []
        self._last_alert_at: dict[str, float] = {}
        self._running = False
        self._task: asyncio.Task[None] | None = None

    @property
    def config(self) -> MonitorConfig:
        return self._config

    def configure(self, **kwargs: Any) -> MonitorConfig:
        for key, value in kwargs.items():
            if hasattr(self._config, key):
                setattr(self._config, key, value)
        logger.info("QuantizationMonitor reconfigured: %s", self._config.to_dict())
        return self._config

    def check_response(self, model_id: str, response: str) -> QuantizationScore:
        artifact_scores: list[float] = []
        artifacts_found: list[str] = []

        rep_score, rep_desc = _score_repetitive_patterns(response)
        if rep_score > 0:
            artifact_scores.append(rep_score)
            artifacts_found.append(rep_desc)

        vocab_score, vocab_desc = _score_vocabulary_reduction(response)
        if vocab_score > 0:
            artifact_scores.append(vocab_score)
            artifacts_found.append(vocab_desc)

        prec_score, prec_desc = _score_precision_loss(response)
        if prec_score > 0:
            artifact_scores.append(prec_score)
            artifacts_found.append(prec_desc)

        if not artifact_scores:
            combined = 0.0
        else:
            combined = round(
                sum(artifact_scores) / len(artifact_scores) + _independence_bonus(artifact_scores),
                4,
            )
        combined = max(0.0, min(1.0, combined))

        exceeded = combined >= self._config.alert_threshold
        score = QuantizationScore(
            model_id=model_id,
            score=combined,
            artifacts=artifacts_found,
            threshold_exceeded=exceeded,
        )

        self._record_score(model_id, score)
        if exceeded:
            self._maybe_alert(model_id, score)

        return score

    def _record_score(self, model_id: str, score: QuantizationScore) -> None:
        if model_id not in self._scores:
            self._scores[model_id] = []
        self._scores[model_id].append(score)
        if len(self._scores[model_id]) > self._config.max_history_samples:
            self._scores[model_id] = self._scores[model_id][
                -self._config.max_history_samples:
            ]

    def _maybe_alert(self, model_id: str, score: QuantizationScore) -> None:
        now = time.time()
        last = self._last_alert_at.get(model_id, 0)
        if now - last < self._config.cooldown_alerts_s:
            return
        self._last_alert_at[model_id] = now
        alert = {
            "model_id": model_id,
            "score": score.score,
            "artifacts": score.artifacts,
            "threshold": self._config.alert_threshold,
            "alerted_at": now,
        }
        self._alerts.append(alert)
        logger.warning(
            "Quantization alert: model=%s score=%.3f artifacts=%s",
            model_id,
            score.score,
            score.artifacts,
        )

    def get_history(self, model_id: str | None = None) -> dict[str, list[QuantizationScore]]:
        if model_id:
            return {model_id: list(self._scores.get(model_id, []))}
        return {mid: list(scores) for mid, scores in self._scores.items()}

    def get_alerts(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._alerts[-limit:]

    def status(self) -> dict[str, Any]:
        model_count = len(self._scores)
        total_scores = sum(len(s) for s in self._scores.values())
        above_threshold = sum(
            1 for scores in self._scores.values()
            for s in scores if s.threshold_exceeded
        )
        return {
            "running": self._running,
            "config": self._config.to_dict(),
            "models_tracked": model_count,
            "total_checks": total_scores,
            "alerts_fired": len(self._alerts),
            "scores_above_threshold": above_threshold,
        }

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        logger.info(
            "QuantizationMonitor started: threshold=%.2f interval=%ds",
            self._config.alert_threshold,
            self._config.check_interval_s,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with __import__("contextlib").suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("QuantizationMonitor stopped")


def _score_repetitive_patterns(response: str) -> tuple[float, str]:
    matches = _REPETITIVE_PATTERN.findall(response)
    if not matches:
        return 0.0, ""
    repeat_count = len(matches)
    response_len = max(len(response), 1)
    density = min(1.0, repeat_count / (response_len / 100) * 5)
    return round(density, 4), f"repetitive_patterns:{repeat_count}"


def _score_vocabulary_reduction(response: str) -> tuple[float, str]:
    hits = 0
    patterns_found: list[str] = []
    for pattern, label in _VOCAB_SIMPLIFIERS.items():
        count = len(re.findall(pattern, response, re.IGNORECASE))
        if count > 0:
            hits += count
            patterns_found.append(label)

    if hits == 0:
        return 0.0, ""

    words = response.split()
    word_count = max(len(words), 1)
    density = min(1.0, (hits / max(1, word_count / 100)) * 2.0)
    return round(density, 4), f"vocab_reduction:{','.join(patterns_found[:3])}"


def _score_precision_loss(response: str) -> tuple[float, str]:
    indicators = 0
    details: list[str] = []

    truncated = re.findall(r"\d+\.\d{1,2}(?!\d)", response)
    if len(truncated) >= 3:
        indicators += 1
        details.append("truncated_decimals")

    if re.search(r"(approximately|roughly|about|around|~\s*\d+)", response, re.IGNORECASE):
        indicators += 1
        details.append("approx_language")

    if re.search(r"\b\d+\s*(plus or minus|±|~)\s*\d+\b", response, re.IGNORECASE):
        indicators += 1
        details.append("uncertainty_expression")

    vague = len(re.findall(
        r"\b(some|few|several|many|multiple|various)\s+"
        r"(\d+|numbers?|values?|results?|times?)\b",
        response, re.IGNORECASE,
    ))
    if vague >= 2:
        indicators += 1
        details.append("vague_quantities")

    if indicators == 0:
        return 0.0, ""

    score = min(1.0, indicators * 0.3)
    return round(score, 4), f"precision_loss:{','.join(details)}"


def _independence_bonus(scores: list[float]) -> float:
    nonzero = [s for s in scores if s > 0]
    if len(nonzero) < 2:
        return 0.0
    return round(min(0.3, len(nonzero) * 0.15), 4)
