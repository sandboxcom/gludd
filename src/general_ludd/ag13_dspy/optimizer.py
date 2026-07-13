"""Metric-driven prompt optimizer with mutation strategies."""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence

from general_ludd.ag13_dspy.registry import PromptSpec, PromptTemplate

MetricFn = Callable[[str, str, str], float]
TrainExample = tuple[str, str]


def _mutate_reorder(template: str) -> str:
    lines = template.split("\n")
    if len(lines) < 2:
        return template
    i, j = random.sample(range(len(lines)), k=min(2, len(lines)))
    lines[i], lines[j] = lines[j], lines[i]
    return "\n".join(lines)


def _mutate_reword(template: str) -> str:
    replacements = {
        "Classify": "Categorize",
        "classify": "categorize",
        "Generate": "Produce",
        "generate": "produce",
        "extract": "pull",
        "Extract": "Pull",
        "summarize": "condense",
        "Summarize": "Condense",
        "analyze": "examine",
        "Analyze": "Examine",
    }
    result = template
    for old, new in replacements.items():
        result = result.replace(old, new)
    return result


def _mutate_trim(template: str) -> str:
    lines = template.split("\n")
    if len(lines) <= 1:
        return template
    idx = random.randrange(len(lines))
    return "\n".join(lines[:idx] + lines[idx + 1 :])


MUTATION_STRATEGIES: dict[str, Callable[[str], str]] = {
    "reorder_sections": _mutate_reorder,
    "reword": _mutate_reword,
    "trim": _mutate_trim,
}


def _exact_match(candidate_text: str, expected: str, actual: str) -> float:
    return 1.0 if expected.strip() == actual.strip() else 0.0


def _contains_all(candidate_text: str, expected: str, actual: str) -> float:
    del candidate_text
    if not expected:
        return 1.0
    tokens = expected.lower().split()
    matched = sum(1 for t in tokens if t in actual.lower())
    return matched / len(tokens)


def _semantic_similarity(candidate_text: str, expected: str, actual: str) -> float:
    del candidate_text
    e_words = set(expected.lower().split())
    a_words = set(actual.lower().split())
    if not e_words:
        return 1.0
    return len(e_words & a_words) / len(e_words)


BUILTIN_METRICS: dict[str, MetricFn] = {
    "exact_match": _exact_match,
    "contains_all": _contains_all,
    "semantic_similarity": _semantic_similarity,
}


class PromptOptimizer:
    """Runs a metric-driven scoring loop over candidate template mutations.

    Stores the highest-scoring template in ``self.best_template`` and its score
    in ``self.best_score``.
    """

    def __init__(
        self,
        spec: PromptSpec,
        base_template: str,
        metric: MetricFn | str = "contains_all",
        train_set: Sequence[TrainExample] = (),
        max_rounds: int = 5,
        candidates_per_round: int = 3,
        strategies: Sequence[str] | None = None,
        seed: int | None = None,
    ) -> None:
        self.spec = spec
        self.base_template = base_template
        self._metric_fn = _resolve_metric(metric)
        self.train_set = list(train_set)
        self.max_rounds = max_rounds
        self.candidates_per_round = candidates_per_round
        self.strategies = list(strategies) if strategies else list(MUTATION_STRATEGIES)
        self.best_template: PromptTemplate | None = None
        self.best_score: float = -math.inf
        if seed is not None:
            random.seed(seed)

    def score_one(self, template: PromptTemplate) -> float:
        if not self.train_set:
            return self.best_score
        scores: list[float] = []
        for inputs_text, expected in self.train_set:
            rendered = template.call(**{k: inputs_text for k in self.spec.inputs} or {"text": inputs_text})
            scores.append(self._metric_fn(template.template, expected, rendered))
        return sum(scores) / len(scores) if scores else 0.0

    def optimize(self) -> PromptTemplate:
        current = PromptTemplate(spec=self.spec, template=self.base_template)
        self.best_template = current
        self.best_score = self.score_one(current)

        for _round in range(self.max_rounds):
            candidates = [current]
            for _ in range(self.candidates_per_round):
                strategy_name = random.choice(self.strategies)
                strategy_fn = MUTATION_STRATEGIES.get(strategy_name)
                if strategy_fn:
                    mutated_text = strategy_fn(current.template)
                    candidates.append(
                        PromptTemplate(spec=self.spec, template=mutated_text),
                    )

            best_of_round = candidates[0]
            best_round_score = self.score_one(candidates[0])
            for c in candidates[1:]:
                s = self.score_one(c)
                if s > best_round_score:
                    best_of_round = c
                    best_round_score = s

            if best_round_score > self.best_score:
                self.best_template = best_of_round
                self.best_score = best_round_score

            if best_round_score >= 1.0:
                break

            current = best_of_round

        assert self.best_template is not None
        return self.best_template


def _resolve_metric(metric: MetricFn | str) -> MetricFn:
    if isinstance(metric, str):
        fn = BUILTIN_METRICS.get(metric)
        if fn is None:
            raise ValueError(f"Unknown metric: {metric}")
        return fn
    return metric
