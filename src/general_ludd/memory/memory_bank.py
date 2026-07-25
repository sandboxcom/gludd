"""Mental Models and Memory Banks — isolated memory spaces with curated knowledge.

Implements:
  - Disposition: persona tuning parameters (skepticism, literalism, empathy)
  - MemoryBankConfig: identity + rules for an isolated memory bank
  - MentalModel: user-curated summaries with priority over raw facts
  - MemoryBank: isolated bank combining mental models + fact storage
  - MemoryBankRegistry: CRUD management for bank instances

Mental models are retrieved BEFORE facts in recall, and disposition values
feed into the reflect synthesis to produce persona-appropriate responses.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# === Dataclasses =============================================================


@dataclass
class Disposition:
    """Persona tuning for a memory bank's reflect behavior.

    1 = low, 5 = high. Default 3 (neutral) on all axes.
    """

    skepticism: int = 3  # how much to question / fact-check
    literalism: int = 3  # how literally to follow directives
    empathy: int = 3  # how warmly / supportively to respond

    def __post_init__(self) -> None:
        for name in ("skepticism", "literalism", "empathy"):
            val = getattr(self, name)
            if not (1 <= val <= 5):
                raise ValueError(
                    f"Disposition.{name} must be 1-5, got {val}"
                )

    def to_dict(self) -> dict[str, int]:
        return {
            "skepticism": self.skepticism,
            "literalism": self.literalism,
            "empathy": self.empathy,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Disposition:
        return cls(
            skepticism=int(data.get("skepticism", 3)),
            literalism=int(data.get("literalism", 3)),
            empathy=int(data.get("empathy", 3)),
        )


@dataclass
class MemoryBankConfig:
    """Immutable identity and rules for a memory bank."""

    bank_id: str
    mission: str = ""
    directives: list[str] = field(default_factory=list)
    disposition: Disposition = field(default_factory=Disposition)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bank_id": self.bank_id,
            "mission": self.mission,
            "directives": list(self.directives),
            "disposition": self.disposition.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryBankConfig:
        disp_raw = data.get("disposition", {})
        disposition = Disposition.from_dict(disp_raw) if isinstance(disp_raw, dict) else Disposition()
        return cls(
            bank_id=str(data.get("bank_id", "")),
            mission=str(data.get("mission", "")),
            directives=list(data.get("directives", [])),
            disposition=disposition,
        )


@dataclass
class MentalModel:
    """User-curated summary with priority over raw facts."""

    model_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    subject: str = ""
    content: str = ""
    priority: int = 5  # higher = surfaces first; 1-10
    created_by: str = "system"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.priority = max(1, min(10, self.priority))

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "subject": self.subject,
            "content": self.content,
            "priority": self.priority,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MentalModel:
        return cls(
            model_id=str(data.get("model_id", uuid.uuid4().hex[:12])),
            subject=str(data.get("subject", "")),
            content=str(data.get("content", "")),
            priority=int(data.get("priority", 5)),
            created_by=str(data.get("created_by", "system")),
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
            tags=list(data.get("tags", [])),
        )


@dataclass
class MemoryEntry:
    """A raw fact stored within a bank."""

    entry_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    content: str = ""
    source: str = ""
    created_at: float = field(default_factory=time.time)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "content": self.content,
            "source": self.source,
            "created_at": self.created_at,
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryEntry:
        return cls(
            entry_id=str(data.get("entry_id", uuid.uuid4().hex[:12])),
            content=str(data.get("content", "")),
            source=str(data.get("source", "")),
            created_at=float(data.get("created_at", time.time())),
            tags=list(data.get("tags", [])),
        )


@dataclass
class MemoryBankResult:
    """Result from a memory bank recall operation."""

    mental_models: list[MentalModel] = field(default_factory=list)
    facts: list[MemoryEntry] = field(default_factory=list)
    synthesized: str = ""


# === MemoryBank ==============================================================


class MemoryBank:
    """Isolated memory space with mental models + facts.

    Mental models are user-curated summaries that take priority over raw facts
    in recall and reflect operations.  Disposition values tune the reflect
    synthesis to produce persona-appropriate responses.
    """

    def __init__(self, config: MemoryBankConfig) -> None:
        self._config = config
        self._mental_models: dict[str, MentalModel] = {}
        self._facts: dict[str, MemoryEntry] = {}
        self._lock = threading.Lock()

    @property
    def config(self) -> MemoryBankConfig:
        return self._config

    @property
    def bank_id(self) -> str:
        return self._config.bank_id

    # --- Mental Model CRUD ---------------------------------------------------

    def add_mental_model(self, model: MentalModel) -> MentalModel:
        if not model.model_id:
            model.model_id = uuid.uuid4().hex[:12]
        model.updated_at = time.time()
        if not model.created_at:
            model.created_at = model.updated_at
        with self._lock:
            self._mental_models[model.model_id] = model
        return model

    def get_mental_models(
        self, subject_filter: str | None = None
    ) -> list[MentalModel]:
        with self._lock:
            models = list(self._mental_models.values())
        if subject_filter is not None:
            fl = subject_filter.lower()
            models = [
                m
                for m in models
                if fl in m.subject.lower() or any(fl in t.lower() for t in m.tags)
            ]
        models.sort(key=lambda m: m.priority, reverse=True)
        return models

    def update_mental_model(self, model_id: str, content: str) -> MentalModel | None:
        with self._lock:
            existing = self._mental_models.get(model_id)
            if existing is None:
                return None
            existing.content = content
            existing.updated_at = time.time()
        return existing

    def delete_mental_model(self, model_id: str) -> bool:
        with self._lock:
            if model_id in self._mental_models:
                del self._mental_models[model_id]
                return True
            return False

    # --- Fact / MemoryEntry CRUD ---------------------------------------------

    def retain(self, fact: MemoryEntry) -> MemoryEntry:
        if not fact.entry_id:
            fact.entry_id = uuid.uuid4().hex[:12]
        if not fact.created_at:
            fact.created_at = time.time()
        with self._lock:
            self._facts[fact.entry_id] = fact
        return fact

    def get_facts(self, tag_filter: str | None = None) -> list[MemoryEntry]:
        with self._lock:
            facts = list(self._facts.values())
        if tag_filter is not None:
            fl = tag_filter.lower()
            facts = [f for f in facts if any(fl in t.lower() for t in f.tags)]
        facts.sort(key=lambda f: f.created_at, reverse=True)
        return facts

    def delete_fact(self, entry_id: str) -> bool:
        with self._lock:
            if entry_id in self._facts:
                del self._facts[entry_id]
                return True
            return False

    # --- Recall & Reflect ----------------------------------------------------

    def recall(
        self, query: str, context: dict[str, Any] | None = None
    ) -> MemoryBankResult:
        ql = query.lower()
        qterms = _tokenize(ql)

        models = self._score_mental_models(qterms, ql)
        facts = self._score_facts(qterms, ql)

        synthesized = self.reflect(query)

        return MemoryBankResult(
            mental_models=models,
            facts=facts,
            synthesized=synthesized,
        )

    def reflect(self, query: str) -> str:
        qterms = _tokenize(query.lower())
        models = self._score_mental_models(qterms, query.lower())
        facts = self._score_facts(qterms, query.lower())

        return self._synthesize(query, models, facts)

    # --- Private helpers -----------------------------------------------------

    def _score_mental_models(
        self, qterms: list[str], ql: str
    ) -> list[MentalModel]:
        scored: list[tuple[MentalModel, float]] = []
        with self._lock:
            for model in self._mental_models.values():
                score = _score_text(
                    qterms, ql, f"{model.subject} {model.content} {' '.join(model.tags)}"
                )
                if model.subject.lower() in ql or ql in model.subject.lower():
                    score += 0.3
                priority_boost = (model.priority - 5) * 0.04
                score = min(1.0, score + priority_boost)
                if score > 0:
                    scored.append((model, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [m for m, _ in scored]

    def _score_facts(
        self, qterms: list[str], ql: str
    ) -> list[MemoryEntry]:
        scored: list[tuple[MemoryEntry, float]] = []
        with self._lock:
            for fact in self._facts.values():
                text_blob = f"{fact.content} {fact.source} {' '.join(fact.tags)}"
                score = _score_text(qterms, ql, text_blob)
                if score > 0:
                    scored.append((fact, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [f for f, _ in scored]

    def _synthesize(
        self,
        query: str,
        models: list[MentalModel],
        facts: list[MemoryEntry],
    ) -> str:
        disp = self._config.disposition
        parts: list[str] = []

        if self._config.mission:
            parts.append(f"[Mission: {self._config.mission}]")

        if models:
            model_lines = "\n".join(
                f"  [{m.priority}] {m.subject}: {m.content}"
                for m in models[:5]
            )
            parts.append(f"Mental Models (highest priority):\n{model_lines}")

        if facts:
            fact_lines = "\n".join(
                f"  - {f.content}" for f in facts[:5]
            )
            parts.append(f"Facts:\n{fact_lines}")

        if not models and not facts:
            parts.append("No relevant mental models or facts found.")

        directives_note = ""
        if self._config.directives:
            dir_text = "; ".join(self._config.directives[:3])
            directives_note = f"\nDirectives: {dir_text}"

        disposition_note = (
            f"\nDisposition: skepticism={disp.skepticism}/5, "
            f"literalism={disp.literalism}/5, empathy={disp.empathy}/5"
        )

        synthesized = (
            f"Reflect on: {query}\n" + "\n".join(parts)
            + directives_note + disposition_note
        )

        return synthesized


# === MemoryBankRegistry ======================================================


class MemoryBankRegistry:
    """Thread-safe registry for MemoryBank instances.

    Manages bank lifecycle: create, retrieve, list, delete.
    """

    def __init__(self) -> None:
        self._banks: dict[str, MemoryBank] = {}
        self._lock = threading.Lock()

    def create_bank(self, config: MemoryBankConfig) -> MemoryBank:
        with self._lock:
            if config.bank_id in self._banks:
                raise ValueError(
                    f"Bank '{config.bank_id}' already exists. "
                    f"Use delete_bank first or choose a different ID."
                )
            bank = MemoryBank(config)
            self._banks[config.bank_id] = bank
        return bank

    def get_bank(self, bank_id: str) -> MemoryBank | None:
        with self._lock:
            return self._banks.get(bank_id)

    def get_or_create_bank(self, config: MemoryBankConfig) -> MemoryBank:
        with self._lock:
            existing = self._banks.get(config.bank_id)
            if existing is not None:
                return existing
            bank = MemoryBank(config)
            self._banks[config.bank_id] = bank
            return bank

    def list_banks(self) -> list[MemoryBankConfig]:
        with self._lock:
            return [bank.config for bank in self._banks.values()]

    def delete_bank(self, bank_id: str) -> bool:
        with self._lock:
            if bank_id in self._banks:
                del self._banks[bank_id]
                return True
            return False

    def bank_count(self) -> int:
        with self._lock:
            return len(self._banks)


# === Helpers =================================================================


def _tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9_]+", text)
    stop = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "can", "shall", "to", "of", "in", "for",
        "on", "with", "at", "by", "from", "as", "into", "through", "during",
        "and", "but", "or", "not", "no", "nor", "so", "yet", "both", "either",
        "neither", "each", "every", "all", "any", "few", "more", "most",
        "other", "some", "such", "only", "own", "same", "than", "too", "very",
        "just", "it", "its", "that", "this", "these", "those",
    }
    return [w for w in words if w not in stop and len(w) > 1]


def _score_text(
    qterms: list[str], ql: str, text_blob: str
) -> float:
    text_l = text_blob.lower()
    score = 0.0

    if ql in text_l:
        score += 0.4

    for term in qterms:
        if term in text_l:
            score += 0.08

    return min(1.0, round(score, 4))


def load_bank_templates(templates_path: str) -> dict[str, MemoryBankConfig]:
    import yaml

    with open(templates_path) as fh:
        raw = yaml.safe_load(fh)

    if not isinstance(raw, dict) or "templates" not in raw:
        raise ValueError("Invalid templates file: missing 'templates' key")

    result: dict[str, MemoryBankConfig] = {}
    for name, tpl in raw["templates"].items():
        disp_data = tpl.get("disposition", {})
        config = MemoryBankConfig(
            bank_id=tpl.get("bank_id", name),
            mission=tpl.get("mission", ""),
            directives=list(tpl.get("directives", [])),
            disposition=Disposition(
                skepticism=int(disp_data.get("skepticism", 3)),
                literalism=int(disp_data.get("literalism", 3)),
                empathy=int(disp_data.get("empathy", 3)),
            ),
        )
        result[name] = config
    return result
