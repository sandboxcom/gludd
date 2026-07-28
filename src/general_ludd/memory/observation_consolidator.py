"""Observation consolidation — facts → observations with evidence tracking.

Mirrors Hindsight's observation model: group raw facts by subject,
deduplicate, produce evidence-grounded beliefs (observations) with
source quotes, proof counts, confidence scores, and freshness tracking.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import os
import re
import threading
import time
from collections import defaultdict
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)

OBSERVATIONS_NAMESPACE = "observations"
DEFAULT_KEYWORD_EVIDENCE = "evidence_memory_consolidation"


# ── protocol for input facts ───────────────────────────────────────────────────


class HasContent(Protocol):
    content: str

    @property
    def source(self) -> str: ...
    @property
    def timestamp(self) -> str: ...


@dataclass
class MemoryFact:
    fact_id: str
    content: str
    source: str = ""
    timestamp: float = 0.0


# ── output types ───────────────────────────────────────────────────────────────


@dataclass
class EvidenceRef:
    fact_id: str
    quote: str
    timestamp: float


@dataclass
class Observation:
    observation_id: str
    subject: str
    statement: str
    evidence: list[EvidenceRef] = field(default_factory=list)
    proof_count: int = 0
    confidence: float = 0.0
    created_at: float = 0.0
    updated_at: float = 0.0
    stale: bool = False
    contradictions: list[str] = field(default_factory=list)


# ── consolidator ───────────────────────────────────────────────────────────────


class ObservationConsolidator:
    """Group facts → deduplicate → consolidate → produce observations.

    Each observation carries linked evidence (exact quotes from source
    facts), a Bayesian-style confidence score, and a staleness flag when
    newer unconsolidated facts exist.
    """

    def __init__(
        self,
        *,
        similarity_threshold: float = 0.62,
        default_confidence_floor: float = 0.15,
        max_contradictions_stored: int = 20,
    ) -> None:
        self.similarity_threshold = similarity_threshold
        self.default_confidence_floor = default_confidence_floor
        self.max_contradictions_stored = max_contradictions_stored
        self._last_consolidation_ts: float = 0.0

    # ---------------------------------------------------------------- consolidate

    def consolidate(self, facts: Sequence[MemoryFact]) -> list[Observation]:
        now = time.time()
        self._last_consolidation_ts = now

        deduped = self.deduplicate(facts)
        subject_groups = self._group_by_subject(deduped)

        observations: list[Observation] = []
        for subject, group in subject_groups.items():
            observations.extend(self._merge_to_observations(subject, group, now))

        return observations

    # ---------------------------------------------------------------- update

    def update(
        self,
        existing: Observation,
        new_facts: Sequence[MemoryFact],
    ) -> Observation:
        now = time.time()
        recalculated = Observation(
            observation_id=existing.observation_id,
            subject=existing.subject,
            statement=existing.statement,
            evidence=list(existing.evidence),
            proof_count=existing.proof_count,
            confidence=existing.confidence,
            created_at=existing.created_at,
            updated_at=now,
            stale=False,
            contradictions=list(existing.contradictions),
        )

        for fact in new_facts:
            ev = EvidenceRef(
                fact_id=fact.fact_id,
                quote=fact.content,
                timestamp=fact.timestamp,
            )
            recalculated.evidence.append(ev)

        recalculated.proof_count = len(recalculated.evidence)
        recalculated.confidence = self.compute_confidence(
            recalculated.proof_count,
            len(recalculated.contradictions),
        )

        return recalculated

    # ---------------------------------------------------------------- deduplicate

    def deduplicate(self, facts: Sequence[MemoryFact]) -> list[MemoryFact]:
        if len(facts) <= 1:
            return list(facts)

        kept: list[MemoryFact] = []
        for fact in facts:
            duplicate = False
            for existing in kept:
                if self._similarity(fact.content, existing.content) >= self.similarity_threshold:
                    duplicate = True
                    break
            if not duplicate:
                kept.append(fact)

        return kept

    # ---------------------------------------------------------------- confidence

    @staticmethod
    def compute_confidence(evidence_count: int, contradiction_count: int) -> float:
        if evidence_count <= 0:
            return 0.0

        evidence_weight = min(evidence_count / 7.0, 1.0)
        contradiction_penalty = min(contradiction_count * 0.18, 0.80)
        confidence = max(0.0, min(1.0, evidence_weight - contradiction_penalty))
        return round(max(confidence, 0.05), 4)

    # ---------------------------------------------------------------- staleness

    def mark_stale(
        self,
        observations: list[Observation],
        newer_fact_timestamp: float,
    ) -> list[Observation]:
        for obs in observations:
            obs.stale = newer_fact_timestamp > obs.updated_at
        return observations

    # ---------------------------------------------------------------- subject extraction

    def _group_by_subject(self, facts: Sequence[MemoryFact]) -> dict[str, list[MemoryFact]]:
        groups: dict[str, list[MemoryFact]] = defaultdict(list)
        for fact in facts:
            subject = self._extract_subject(fact.content)
            groups[subject].append(fact)
        return dict(groups)

    @staticmethod
    def _extract_subject(content: str) -> str:
        entities = _extract_names(content)
        if entities:
            return entities[0]
        lower = content.lower()
        for keyword in ("user", "project", "agent", "system", "code", "test"):
            if keyword in lower:
                return keyword.title()
        return "general"

    def _merge_to_observations(
        self, subject: str, facts: list[MemoryFact], now: float
    ) -> list[Observation]:
        primary, contradictory = self._categorize_facts(subject, facts)

        observations: list[Observation] = []
        if primary:
            obs = self._build_observation(subject, primary, contradictory, now)
            observations.append(obs)
        for entry in contradictory:
            obs = self._build_observation(subject, [entry], [], now)
            observations.append(obs)

        return observations

    def _categorize_facts(
        self, subject: str, facts: list[MemoryFact]
    ) -> tuple[list[MemoryFact], list[MemoryFact]]:
        if len(facts) <= 1:
            return list(facts), []

        cluster_k = max(2, min(len(facts) // 3, 5))
        clusters: list[list[MemoryFact]] = _agglomerative_cluster(
            facts, self.similarity_threshold, cluster_k
        )

        if not clusters:
            return list(facts), []

        largest = max(clusters, key=len)
        contradictory: list[MemoryFact] = []
        seen = set(id(f) for f in largest)
        for cluster in clusters:
            if cluster is not largest:
                for fact in cluster:
                    if id(fact) not in seen:
                        contradictory.append(fact)
                        seen.add(id(fact))

        return largest, contradictory

    def _build_observation(
        self,
        subject: str,
        supporting: list[MemoryFact],
        contradictory: list[MemoryFact],
        now: float,
    ) -> Observation:
        statement = _synthesize_statement(supporting)
        obs_id = _hash_observation_id(subject, statement)

        evidence = [
            EvidenceRef(fact_id=f.fact_id, quote=f.content, timestamp=f.timestamp)
            for f in supporting
        ]

        contradiction_texts = [f.content for f in contradictory]
        proof_count = len(evidence)
        confidence = self.compute_confidence(proof_count, len(contradiction_texts))

        return Observation(
            observation_id=obs_id,
            subject=subject,
            statement=statement,
            evidence=evidence,
            proof_count=proof_count,
            confidence=confidence,
            created_at=now,
            updated_at=now,
            stale=False,
            contradictions=contradiction_texts[:self.max_contradictions_stored],
        )

    # ---------------------------------------------------------------- similarity

    @staticmethod
    def _similarity(a: str, b: str) -> float:
        a_norm = _normalize(a)
        b_norm = _normalize(b)
        if a_norm == b_norm:
            return 1.0

        a_words = set(a_norm.split())
        b_words = set(b_norm.split())
        if not a_words or not b_words:
            return 0.0

        intersection = a_words & b_words
        union = a_words | b_words
        jaccard = len(intersection) / len(union)

        a_bigrams = _bigrams(a_norm)
        b_bigrams = _bigrams(b_norm)
        if a_bigrams and b_bigrams:
            bigram_intersection = a_bigrams & b_bigrams
            bigram_union = a_bigrams | b_bigrams
            bigram_jaccard = len(bigram_intersection) / len(bigram_union)
        else:
            bigram_jaccard = 0.0

        return 0.55 * jaccard + 0.45 * bigram_jaccard


compute_confidence = ObservationConsolidator.compute_confidence


# ── observation store ──────────────────────────────────────────────────────────


class ObservationStore:
    """Thread-safe persistent store for observations.

    Uses a JSON file on disk for simplicity (matches LocalAgentMemory pattern).
    """

    def __init__(self, store_path: str = ".gludd/observations.json") -> None:
        self._path = os.path.expanduser(os.path.expandvars(store_path))
        self._lock = threading.Lock()
        self._observations: dict[str, Observation] = {}
        self._load()

    # ---------------------------------------------------------------- put / get

    def put(self, observation: Observation) -> None:
        with self._lock:
            previous = self._observations.copy()
            self._observations[observation.observation_id] = deepcopy(observation)
            try:
                self._persist()
            except BaseException:
                self._observations = previous
                raise

    def put_all(self, observations: list[Observation]) -> None:
        with self._lock:
            previous = self._observations.copy()
            for obs in observations:
                self._observations[obs.observation_id] = deepcopy(obs)
            try:
                self._persist()
            except BaseException:
                self._observations = previous
                raise

    def get(self, observation_id: str) -> Observation | None:
        with self._lock:
            observation = self._observations.get(observation_id)
            return deepcopy(observation)

    # ---------------------------------------------------------------- query

    def get_by_subject(self, subject: str) -> list[Observation]:
        with self._lock:
            return [deepcopy(o) for o in self._observations.values() if o.subject == subject]

    def get_fresh(self) -> list[Observation]:
        with self._lock:
            return [deepcopy(o) for o in self._observations.values() if not o.stale]

    def get_stale(self) -> list[Observation]:
        with self._lock:
            return [deepcopy(o) for o in self._observations.values() if o.stale]

    def get_above_confidence(self, threshold: float) -> list[Observation]:
        with self._lock:
            return [deepcopy(o) for o in self._observations.values() if o.confidence >= threshold]

    def list_all(self) -> list[Observation]:
        with self._lock:
            return [deepcopy(o) for o in self._observations.values()]

    # ---------------------------------------------------------------- mutate

    def delete(self, observation_id: str) -> bool:
        with self._lock:
            previous = self._observations.copy()
            existed = observation_id in previous
            self._observations.pop(observation_id, None)
            if existed:
                try:
                    self._persist()
                except BaseException:
                    self._observations = previous
                    raise
            return existed

    def clear(self) -> None:
        with self._lock:
            previous = self._observations.copy()
            self._observations.clear()
            try:
                self._persist()
            except BaseException:
                self._observations = previous
                raise

    # ---------------------------------------------------------------- persistence

    def _load(self) -> None:
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path) as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            return

        for obs_id, obj in data.items():
            try:
                evidence = [
                    EvidenceRef(
                        fact_id=e["fact_id"],
                        quote=e["quote"],
                        timestamp=e["timestamp"],
                    )
                    for e in obj.get("evidence", [])
                ]
                self._observations[obs_id] = Observation(
                    observation_id=obj["observation_id"],
                    subject=obj["subject"],
                    statement=obj["statement"],
                    evidence=evidence,
                    proof_count=obj.get("proof_count", len(evidence)),
                    confidence=obj["confidence"],
                    created_at=obj["created_at"],
                    updated_at=obj["updated_at"],
                    stale=obj.get("stale", False),
                    contradictions=obj.get("contradictions", []),
                )
            except (KeyError, TypeError):
                continue

    def _persist(self) -> None:
        dirname = os.path.dirname(self._path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
        out: dict[str, dict[str, Any]] = {}
        for obs_id, obs in self._observations.items():
            out[obs_id] = {
                "observation_id": obs.observation_id,
                "subject": obs.subject,
                "statement": obs.statement,
                "evidence": [
                    {"fact_id": e.fact_id, "quote": e.quote, "timestamp": e.timestamp}
                    for e in obs.evidence
                ],
                "proof_count": obs.proof_count,
                "confidence": obs.confidence,
                "created_at": obs.created_at,
                "updated_at": obs.updated_at,
                "stale": obs.stale,
                "contradictions": obs.contradictions,
            }
        tmp = self._path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(out, fh, indent=2, default=str)
        os.replace(tmp, self._path)

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._observations)


# ── internal helpers ───────────────────────────────────────────────────────────


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", text.lower())


def _bigrams(text: str) -> set[tuple[str, str]]:
    words = text.split()
    return set(itertools.pairwise(words))


def _extract_names(content: str) -> list[str]:
    names: list[str] = []
    for match in re.finditer(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b", content):
        candidate = match.group(1).strip()
        if candidate.lower() not in _COMMON_NOUNS:
            names.append(candidate)
    return names


_COMMON_NOUNS: frozenset[str] = frozenset({
    "I", "We", "He", "She", "They", "It", "This", "That", "The",
    "A", "An", "And", "Or", "But", "Not", "Is", "Are", "Was", "Were",
    "Has", "Have", "Do", "Does", "Will", "Would", "Can", "Could",
    "Should", "May", "Might", "All", "Any", "Each", "Every", "Some",
    "No", "Yes", "More", "Most", "Other", "Only", "Just", "New",
    "Good", "Bad", "High", "Low", "Big", "Small", "First", "Last",
})


def _synthesize_statement(facts: list[MemoryFact]) -> str:
    if not facts:
        return ""
    if len(facts) == 1:
        return facts[0].content

    words_seq: list[list[str]] = [_normalize(f.content).split() for f in facts]
    common: set[str] = set(words_seq[0])
    for ws in words_seq[1:]:
        common &= set(ws)

    if len(common) >= 3:
        ordered = [w for w in words_seq[0] if w in common]
        return " ".join(ordered)

    return facts[0].content


def _hash_observation_id(subject: str, statement: str) -> str:
    joined = f"{subject}||{statement}"
    return hashlib.sha256(joined.encode()).hexdigest()[:16]


def _agglomerative_cluster(
    facts: list[MemoryFact],
    threshold: float,
    k: int,
) -> list[list[MemoryFact]]:
    n = len(facts)
    if n == 0:
        return []
    if n == 1:
        return [[facts[0]]]

    clusters: list[list[MemoryFact]] = [[f] for f in facts]
    done: set[int] = set()

    while len(clusters) > k:
        best_i, best_j, best_score = -1, -1, -1.0
        for i in range(len(clusters)):
            if i in done:
                continue
            for j in range(i + 1, len(clusters)):
                if j in done:
                    continue
                score = _cluster_similarity(clusters[i], clusters[j])
                if score > best_score:
                    best_score = score
                    best_i, best_j = i, j

        if best_score < threshold:
            break

        merged = clusters[best_i] + clusters[best_j]
        done.add(best_j)
        clusters[best_i] = merged

        new_clusters = [c for idx, c in enumerate(clusters) if idx not in done]
        clusters = new_clusters
        done = set()

    out: list[list[MemoryFact]] = []
    for cl in clusters:
        out.append(cl)

    return out


def _cluster_similarity(
    ca: list[MemoryFact], cb: list[MemoryFact]
) -> float:
    total = 0.0
    count = 0
    for fa in ca:
        for fb in cb:
            total += _text_jaccard(
                _normalize(fa.content),
                _normalize(fb.content),
            )
            count += 1
    return total / count if count > 0 else 0.0


def _text_jaccard(a: str, b: str) -> float:
    a_set = set(a.split())
    b_set = set(b.split())
    if not a_set or not b_set:
        return 0.0
    return len(a_set & b_set) / len(a_set | b_set)
