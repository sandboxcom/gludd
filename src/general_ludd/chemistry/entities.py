"""Chemical entity resolution and registry (CHEM Phase A, §4.1).

Resolves SMILES / InChI / Molfile / common names into :class:`ChemicalEntity`
records. The key invariant (spec §4.1) is:

    "Canonicalization never erases the submitted representation. Tautomers,
    protomers, conformers, stereoisomers, isotopologues, salts, solvates, and
    mixtures are related records, not silently interchangeable strings."

This module delegates the heavy identity work to :mod:`general_ludd.chemistry.core`
(which already preserves submitted SMILES and detects stereo/isotope/salt
markers) and wraps the dict output in the typed schema. The
:class:`EntityRegistry` lets callers register resolved entities and record
*relations* between them (tautomer / stereoisomer / salt-component / solvate)
without merging their records.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from pydantic import BaseModel as _BaseModel

from general_ludd.chemistry import core as _core
from general_ludd.chemistry.schemas import (
    ChemicalEntity,
    ChemicalStructure,
    ComponentRecord,
    EntityKind,
    FractionBasis,
    IdentifierRecord,
    IsotopeStatus,
    NameRecord,
    StereoStatus,
    StructureRepresentation,
    ValidationRecord,
    ValidationStatus,
)

_INCHI_PREFIX_RE = re.compile(r"^InChI=", re.IGNORECASE)
_MOLFILE_HEADER_RE = re.compile(r"^\s*(M\s{2}|V[23]000)", re.MULTILINE)


def _new_id() -> str:
    return str(uuid.uuid4())


def _detect_representation(query: str) -> StructureRepresentation:
    """Identify the structural representation format of ``query``.

    Order matters: molfile is matched by its multi-line header; InChI by its
    prefix; otherwise we fall back to SMILES (the format produced by
    :mod:`core` for common names and arbitrary structure strings).
    """
    if _INCHI_PREFIX_RE.match(query):
        return StructureRepresentation.inchi
    if _MOLFILE_HEADER_RE.search(query):
        return StructureRepresentation.molfile
    return StructureRepresentation.smiles


def _stereo_status_from_core(value: str) -> StereoStatus:
    raw = _core._stereo_status(value)
    if raw == "specified":
        return StereoStatus.specified
    if raw == "partial":
        return StereoStatus.partial
    return StereoStatus.unknown


def _isotope_status_from_core(value: str) -> IsotopeStatus:
    raw = _core._isotope_status(value)
    if raw == "specified":
        return IsotopeStatus.specified
    if raw == "unknown":
        return IsotopeStatus.unknown
    return IsotopeStatus.natural


def _dict_to_entity(record: dict[str, Any]) -> ChemicalEntity:
    """Convert a core ``resolve_identity`` dict into a typed ChemicalEntity.

    The submitted representation is ALWAYS preserved verbatim; the canonical
    value returned by core (which may have been canonicalized) is recorded in
    ``structure.value``. When no canonicalization happened the two are equal.
    """
    structure_in = record.get("structure", {})
    submitted = str(structure_in.get("submitted") or structure_in.get("value") or "")
    canonical_value = str(structure_in.get("value") or submitted)
    representation = _detect_representation(submitted) if submitted else StructureRepresentation.unknown

    stereo = _stereo_status_from_core(canonical_value) if canonical_value else StereoStatus.unknown
    isotopes = _isotope_status_from_core(canonical_value) if canonical_value else IsotopeStatus.natural
    charge = int(structure_in.get("charge", 0) or 0)

    names_in = record.get("names") or []
    names = [
        NameRecord(value=str(n.get("value", "")), type=str(n.get("type", "preferred")))
        for n in names_in
        if n.get("value")
    ]

    components_in = record.get("components") or []
    components: list[ComponentRecord] = []
    for c in components_in:
        try:
            basis_raw = str(c.get("basis", "mole"))
            try:
                basis = FractionBasis(basis_raw)
            except ValueError:
                basis = FractionBasis.mole
            components.append(
                ComponentRecord(
                    entity_id=str(c.get("entity_id", _new_id())),
                    fraction=float(c.get("fraction", 0.0)),
                    basis=basis,
                    uncertainty=float(c.get("uncertainty", 0.0)),
                )
            )
        except Exception:  # tolerate unknown component shapes
            continue

    identifiers = []
    for ident in record.get("identifiers") or []:
        val = ident.get("value")
        if val:
            identifiers.append(
                IdentifierRecord(
                    scheme=str(ident.get("scheme", "formula")),
                    value=str(val),
                )
            )

    validation: list[ValidationRecord] = []
    for v in record.get("validation") or []:
        status_raw = str(v.get("status", "warning"))
        try:
            status = ValidationStatus(status_raw)
        except ValueError:
            status = ValidationStatus.warning
        validation.append(ValidationRecord(check=str(v.get("check", "unknown")), status=status))

    kind_raw = str(record.get("kind", "compound"))
    try:
        kind = EntityKind(kind_raw)
    except ValueError:
        kind = EntityKind.compound

    return ChemicalEntity(
        entity_id=str(record.get("entity_id") or _new_id()),
        kind=kind,
        names=names,
        structure=ChemicalStructure(
            representation=representation,
            value=canonical_value,
            submitted=submitted,
            canonicalizer=str(structure_in.get("canonicalizer") or _core.CANONICALIZER),
            stereochemistry=stereo,
            isotopes=isotopes,
            charge=charge,
        ),
        components=components,
        identifiers=identifiers,
        validation=validation,
    )


def resolve_entity(query: str | dict[str, Any]) -> ChemicalEntity:
    """Resolve a name/structure token into a :class:`ChemicalEntity`.

    The submitted string is preserved verbatim in ``structure.submitted`` —
    downstream canonicalization never overwrites it.
    """
    q = str(query.get("query", "")).strip() if isinstance(query, dict) else str(query).strip()
    if not q:
        return ChemicalEntity(
            entity_id=_new_id(),
            kind=EntityKind.compound,
            structure=ChemicalStructure(
                representation=StructureRepresentation.unknown,
                value="",
                submitted="",
                canonicalizer=_core.CANONICALIZER,
                stereochemistry=StereoStatus.unknown,
                isotopes=IsotopeStatus.unknown,
                charge=0,
            ),
            validation=[
                ValidationRecord(check="identity_resolution", status=ValidationStatus.fail, note="empty query")
            ],
        )

    record = _core.resolve_identity({"query": q})
    # Preserve the user's exact submitted text (core may have substituted the
    # canonical SMILES from its name registry; we always carry the original).
    record.setdefault("structure", {})["submitted"] = q
    return _dict_to_entity(record)


class RelatedRecord(_BaseModel):
    """A relation between two registered entities (tautomer / salt / solvate …)."""

    model_config = {"extra": "forbid"}

    entity_id: str
    relation: str


class EntityRegistry:
    """Store distinct chemical entities and their explicit relations.

    Per spec §4.1, distinct structures (tautomers, stereoisomers, salts,
    solvates, isotopologues, mixtures) are NEVER collapsed into a single
    record. Instead they are registered separately and connected via
    :meth:`link_related` so callers can navigate the relation graph without
    losing the original representation.
    """

    def __init__(self) -> None:
        """Initialize a ``EntityRegistry`` instance."""
        self._entities: dict[str, ChemicalEntity] = {}
        self._relations: dict[str, list[RelatedRecord]] = {}

    def register(self, entity: ChemicalEntity) -> ChemicalEntity:
        """Insert (or upsert by ``entity_id``) a resolved entity."""
        self._entities[entity.entity_id] = entity
        self._relations.setdefault(entity.entity_id, [])
        return entity

    def get(self, entity_id: str) -> ChemicalEntity | None:
        """Return get."""
        return self._entities.get(entity_id)

    def link_related(self, source_id: str, target_id: str, relation: str) -> None:
        """Record an asymmetric relation ``source -[relation]-> target``.

        Relations are stored both directions to keep the graph navigable, but
        the label is preserved verbatim on each edge (no rewriting of
        ``"salt_of"`` to ``"parent_of"``).
        """
        if source_id not in self._entities:
            raise KeyError(f"unknown source entity: {source_id}")
        if target_id not in self._entities:
            raise KeyError(f"unknown target entity: {target_id}")
        forward = RelatedRecord(entity_id=target_id, relation=relation)
        reverse = RelatedRecord(entity_id=source_id, relation=f"inverse:{relation}")
        self._relations.setdefault(source_id, []).append(forward)
        self._relations.setdefault(target_id, []).append(reverse)

    def related(self, entity_id: str) -> list[RelatedRecord]:
        """Return all relations recorded for ``entity_id`` (forward + inverse)."""
        return list(self._relations.get(entity_id, []))

    def all_entities(self) -> list[ChemicalEntity]:
        """Execute ``all_entities``."""
        return list(self._entities.values())


__all__ = [
    "EntityRegistry",
    "RelatedRecord",
    "resolve_entity",
]
