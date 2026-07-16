"""TDD tests for the forensics chain of custody module.

Tests the module at:
``collections/ansible_collections/general_ludd/forensics/plugins/module_utils/chain_of_custody.py``

Imports directly via :mod:`importlib` from its file path.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "forensics"
    / "plugins"
    / "module_utils"
    / "chain_of_custody.py"
)


def _load_module() -> Any:
    """Import chain_of_custody.py from disk, bypassing ansible collection path."""
    spec = importlib.util.spec_from_file_location(
        "chain_of_custody", str(MODULE_PATH)
    )
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["chain_of_custody"] = mod
    spec.loader.exec_module(mod)
    return mod


_mod = _load_module()
EvidenceItem = _mod.EvidenceItem
ChainOfCustody = _mod.ChainOfCustody
create_chain_of_custody = _mod.create_chain_of_custody
add_evidence_item = _mod.add_evidence_item
log_transfer = _mod.log_transfer
verify_chain = _mod.verify_chain
seal_evidence = _mod.seal_evidence
break_seal = _mod.break_seal
assess_contamination_risk = _mod.assess_contamination_risk
generate_chain_report = _mod.generate_chain_report
verify_digital_signature = _mod.verify_digital_signature
EVIDENCE_TYPES = _mod.EVIDENCE_TYPES
STORAGE_CONDITIONS = _mod.STORAGE_CONDITIONS
CONTAMINATION_RISK_LEVELS = _mod.CONTAMINATION_RISK_LEVELS
PACKAGING_PROTOCOLS = _mod.PACKAGING_PROTOCOLS
LABELING_STANDARD = _mod.LABELING_STANDARD
EVIDENCE_STATUSES = _mod.EVIDENCE_STATUSES


# ── fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def case_id() -> str:
    """Realistic case identifier."""
    return "2024-07-HOMI-0042"


@pytest.fixture
def coc(case_id: str) -> Any:
    """Fresh ChainOfCustody with no evidence."""
    return create_chain_of_custody(case_id)


@pytest.fixture
def knife_evidence(coc: Any) -> Any:
    """PHYSICAL evidence item (bloody knife)."""
    return add_evidence_item(
        coc,
        type="PHYSICAL",
        description="Kitchen knife with suspected blood stains, 8-inch blade, wooden handle",
        location="123 Maple Drive, Kitchen, countertop near sink",
        collector="HERNANDEZ, Maria; badge#4271",
        packaging="PAPER_BAG",
        hazard_warnings=["BIOHAZARD", "SHARP"],
    )


@pytest.fixture
def hard_drive_evidence(coc: Any) -> Any:
    """DIGITAL evidence item (suspect hard drive)."""
    return add_evidence_item(
        coc,
        type="DIGITAL",
        description="Seagate Barracuda 2TB SATA HDD, S/N: Z4T8K9LP",
        location="123 Maple Drive, Home Office, under desk",
        collector="CHEN, Wei; badge#5502",
        packaging="ANTI_STATIC_BAG",
        storage_conditions="CLEAN_ROOM",
    )


@pytest.fixture
def blood_sample_evidence(coc: Any) -> Any:
    """BIOLOGICAL evidence item (blood swab)."""
    return add_evidence_item(
        coc,
        type="BIOLOGICAL",
        description="Blood swab from bathroom floor tile grout, sample A-1",
        location="123 Maple Drive, Master Bathroom, floor near tub",
        collector="HERNANDEZ, Maria; badge#4271",
        packaging="STERILE_CONTAINER",
        storage_conditions="REFRIGERATED",
        hazard_warnings=["BIOHAZARD"],
    )


@pytest.fixture
def fiber_evidence(coc: Any) -> Any:
    """TRACE evidence item (carpet fibers)."""
    return add_evidence_item(
        coc,
        type="TRACE",
        description="Blue synthetic carpet fibers collected from victim clothing",
        location="123 Maple Drive, Living Room carpet edge",
        collector="ODUYA, Jamal; badge#6190",
        packaging="STERILE_CONTAINER",
        storage_conditions="CLEAN_ROOM",
    )


@pytest.fixture
def fingerprint_evidence(coc: Any) -> Any:
    """IMPRESSION evidence item (latent fingerprint lift)."""
    return add_evidence_item(
        coc,
        type="IMPRESSION",
        description="Latent fingerprint lift from front door interior handle, card #L-007",
        location="123 Maple Drive, Front Door, interior handle",
        collector="ODUYA, Jamal; badge#6190",
        packaging="PLASTIC_BAG",
        storage_conditions="EVIDENCE_LOCKER",
    )


@pytest.fixture
def populated_coc(
    coc: Any,
    knife_evidence: Any,
    hard_drive_evidence: Any,
    blood_sample_evidence: Any,
    fiber_evidence: Any,
    fingerprint_evidence: Any,
) -> Any:
    """Chain with 5 evidence items of all types."""
    return coc


# ── 1. EvidenceItem creation and validation ─────────────────────────


class TestEvidenceItemCreation:
    """EvidenceItem creation and validation."""

    def test_create_valid_physical_evidence(self) -> None:
        """Arrange: valid PHYSICAL evidence fields."""
        item = EvidenceItem(
            id="EVI-A1B2C3",
            type="PHYSICAL",
            description="Bloody kitchen knife, 8-inch blade",
            collection_date="2024-07-14T08:30:00Z",
            location="123 Maple Drive, Kitchen counter",
            collector="HERNANDEZ, Maria; badge#4271",
            packaging="PAPER_BAG",
            storage_conditions="EVIDENCE_LOCKER",
        )
        assert item.id == "EVI-A1B2C3"
        assert item.type == "PHYSICAL"
        assert item.description == "Bloody kitchen knife, 8-inch blade"
        assert item.location == "123 Maple Drive, Kitchen counter"
        assert item.collector == "HERNANDEZ, Maria; badge#4271"
        assert item.packaging == "PAPER_BAG"
        assert item.storage_conditions == "EVIDENCE_LOCKER"
        assert item.status == "COLLECTED"
        assert item.contamination_risk == "NONE"
        assert item.seal_number is None
        assert item.seal_history == []
        assert item.hazard_warnings == []

    def test_create_valid_digital_evidence_with_faraday_bag(self) -> None:
        """Arrange: DIGITAL evidence with FARADAY_BAG packaging."""
        item = EvidenceItem(
            id="EVI-DIG001",
            type="DIGITAL",
            description="iPhone 15 Pro, seized from suspect",
            collection_date="2024-07-15T14:00:00Z",
            location="456 Oak Avenue, Bedroom nightstand",
            collector="CHEN, Wei; badge#5502",
            packaging="FARADAY_BAG",
            storage_conditions="CLEAN_ROOM",
            contamination_risk="LOW",
            hazard_warnings=["BATTERY"],
        )
        assert item.packaging == "FARADAY_BAG"
        assert item.storage_conditions == "CLEAN_ROOM"
        assert item.contamination_risk == "LOW"
        assert item.hazard_warnings == ["BATTERY"]

    def test_create_valid_biological_evidence_with_full_metadata(self) -> None:
        """Arrange: BIOLOGICAL evidence with hazard warnings and photographs."""
        item = EvidenceItem(
            id="EVI-BIO042",
            type="BIOLOGICAL",
            description="Blood sample, tube B-12 from victim",
            collection_date="2024-07-14T09:15:00Z",
            location="Central Hospital, ER Bay 3",
            collector="NGUYEN, Tran; badge#8812",
            packaging="STERILE_CONTAINER",
            storage_conditions="FROZEN",
            contamination_risk="MEDIUM",
            hazard_warnings=["BIOHAZARD", "SHARPS"],
            photographs=["IMG_001.jpg", "IMG_002.jpg"],
            notes="Collected per sexual assault evidence kit protocol",
        )
        assert item.type == "BIOLOGICAL"
        assert item.storage_conditions == "FROZEN"
        assert item.contamination_risk == "MEDIUM"
        assert len(item.hazard_warnings) == 2
        assert len(item.photographs) == 2
        assert "sexual assault" in item.notes

    def test_reject_empty_id(self) -> None:
        """Act/Assert: empty id raises ValueError."""
        with pytest.raises(ValueError, match="id, description, location, and collector"):
            EvidenceItem(
                id="",
                type="PHYSICAL",
                description="Valid description",
                collection_date="2024-07-14T08:30:00Z",
                location="Valid location",
                collector="Valid collector",
            )

    def test_reject_unknown_evidence_type(self) -> None:
        """Act/Assert: unknown type raises ValueError."""
        with pytest.raises(ValueError, match="Unknown type"):
            EvidenceItem(
                id="EVI-BAD01",
                type="EXOTIC",
                description="Some evidence",
                collection_date="2024-07-14T08:30:00Z",
                location="Somewhere",
                collector="Smith, John",
            )

    def test_reject_unknown_storage_condition(self) -> None:
        """Act/Assert: unknown storage condition raises ValueError."""
        with pytest.raises(ValueError, match="Unknown storage"):
            EvidenceItem(
                id="EVI-BAD02",
                type="PHYSICAL",
                description="Some evidence",
                collection_date="2024-07-14T08:30:00Z",
                location="Somewhere",
                collector="Smith, John",
                storage_conditions="OUTER_SPACE",
            )

    def test_reject_unknown_packaging(self) -> None:
        """Act/Assert: unknown packaging raises ValueError."""
        with pytest.raises(ValueError, match="Unknown packaging"):
            EvidenceItem(
                id="EVI-BAD03",
                type="PHYSICAL",
                description="Some evidence",
                collection_date="2024-07-14T08:30:00Z",
                location="Somewhere",
                collector="Smith, John",
                packaging="BUBBLE_WRAP",
            )


# ── 2. ChainOfCustody creation ──────────────────────────────────────


class TestChainOfCustodyCreation:
    """ChainOfCustody creation tests."""

    def test_create_chain_with_valid_case_id(self) -> None:
        """Act: create ChainOfCustody directly with case_id."""
        chain = ChainOfCustody(case_id="2024-07-ROBB-0019")
        assert chain.case_id == "2024-07-ROBB-0019"
        assert chain.evidence_items == {}
        assert chain.transfer_log == []
        assert chain.digital_signatures == {}
        assert chain.created_at is not None
        assert chain.last_modified is not None

    def test_reject_empty_case_id(self) -> None:
        """Act/Assert: empty case_id raises ValueError."""
        with pytest.raises(ValueError, match="case_id must be non-empty"):
            ChainOfCustody(case_id="")

    def test_chain_has_created_at_and_last_modified_timestamps(self) -> None:
        """Act: timestamps are set to current UTC."""
        chain = ChainOfCustody(case_id="2024-08-ARSO-0055")
        assert "T" in chain.created_at
        assert "T" in chain.last_modified
        assert chain.created_at == chain.last_modified


# ── 3. create_chain_of_custody function ─────────────────────────────


class TestCreateChainOfCustody:
    """create_chain_of_custody function tests."""

    def test_create_chain_of_custody_valid(self) -> None:
        """Act: call create_chain_of_custody."""
        chain = create_chain_of_custody("2024-09-FIRE-0012")
        assert isinstance(chain, ChainOfCustody)
        assert chain.case_id == "2024-09-FIRE-0012"
        assert len(chain.evidence_items) == 0

    def test_create_chain_of_custody_logs_creation_event(self) -> None:
        """Assert: transfer_log contains creation event."""
        chain = create_chain_of_custody("2024-10-BURG-0088")
        assert len(chain.transfer_log) == 1
        assert chain.transfer_log[0]["event"] == "chain_created"
        assert chain.transfer_log[0]["case_id"] == "2024-10-BURG-0088"

    def test_reject_empty_case_id_string(self) -> None:
        """Act/Assert: empty string raises ValueError."""
        with pytest.raises(ValueError, match="case_id must be a non-empty string"):
            create_chain_of_custody("")


# ── 4. add_evidence_item ────────────────────────────────────────────


class TestAddEvidenceItem:
    """add_evidence_item function tests."""

    def test_add_physical_evidence(self, coc: Any) -> None:
        """Act: add PHYSICAL evidence with paper bag."""
        ev = add_evidence_item(
            coc,
            type="PHYSICAL",
            description=".44 Magnum revolver, S/N: R44712",
            location="456 Elm Street, Under mattress",
            collector="RODRIGUEZ, Carlos; badge#3319",
            packaging="PAPER_BAG",
        )
        assert ev.type == "PHYSICAL"
        assert ev.packaging == "PAPER_BAG"
        assert ev.storage_conditions == "EVIDENCE_LOCKER"
        assert ev.contamination_risk == "LOW"
        assert ev.status == "COLLECTED"
        assert ev.id.startswith("EVI-")
        assert ev.id in coc.evidence_items

    def test_add_digital_evidence_default_storage(self, coc: Any) -> None:
        """Act: DIGITAL evidence auto-assigns CLEAN_ROOM storage."""
        ev = add_evidence_item(
            coc,
            type="DIGITAL",
            description="USB thumb drive, 64GB Kingston, blue",
            location="789 Pine Road, Desk drawer",
            collector="CHEN, Wei; badge#5502",
        )
        assert ev.storage_conditions == "CLEAN_ROOM"
        assert ev.packaging == "ANTI_STATIC_BAG"

    def test_add_biological_evidence_with_explicit_storage(self, coc: Any) -> None:
        """Act: BIOLOGICAL with explicit FROZEN storage."""
        ev = add_evidence_item(
            coc,
            type="BIOLOGICAL",
            description="Hair sample with root, envelope H-03",
            location="Victim vehicle, driver headrest",
            collector="NGUYEN, Tran; badge#8812",
            packaging="STERILE_CONTAINER",
            storage_conditions="FROZEN",
        )
        assert ev.storage_conditions == "FROZEN"
        assert ev.packaging == "STERILE_CONTAINER"

    def test_add_evidence_updates_chain_last_modified(self, coc: Any) -> None:
        """Assert: chain last_modified updated after adding evidence."""
        old_ts = coc.last_modified
        add_evidence_item(
            coc,
            type="TRACE",
            description="Glass fragment from broken window",
            location="Point of entry, living room floor",
            collector="ODUYA, Jamal; badge#6190",
        )
        assert coc.last_modified != old_ts

    def test_add_evidence_logs_collection_event(self, coc: Any) -> None:
        """Assert: transfer_log contains evidence_collected event."""
        transfer_count_before = len(coc.transfer_log)
        add_evidence_item(
            coc,
            type="IMPRESSION",
            description="Shoe print, dirt transfer on linoleum, cast C-012",
            location="Kitchen floor near back door",
            collector="HERNANDEZ, Maria; badge#4271",
        )
        added_events = coc.transfer_log[transfer_count_before:]
        assert any(e["event"] == "evidence_collected" for e in added_events)

    def test_reject_invalid_packaging(self, coc: Any) -> None:
        """Act/Assert: unknown packaging raises ValueError."""
        with pytest.raises(ValueError, match="Unknown packaging"):
            add_evidence_item(
                coc,
                type="PHYSICAL",
                description="Test item",
                location="Test location",
                collector="Test person",
                packaging="SHOEBOX",
            )

    def test_reject_non_chain_argument(self) -> None:
        """Act/Assert: passing non-ChainOfCustody raises TypeError."""
        with pytest.raises(TypeError, match="Expected ChainOfCustody"):
            add_evidence_item(
                {"not": "a chain"},
                type="PHYSICAL",
                description="Test item",
                location="Test location",
                collector="Test person",
            )


# ── 5. log_transfer ─────────────────────────────────────────────────


class TestLogTransfer:
    """log_transfer function tests."""

    def test_log_valid_transfer(self, populated_coc: Any, knife_evidence: Any) -> None:
        """Act: log a transfer of the knife evidence."""
        record = log_transfer(
            knife_evidence.id,
            populated_coc,
            from_person="HERNANDEZ, Maria; badge#4271",
            to_person="LAB-TECH, Priya; badge#9903",
            reason="Transfer to forensic biology lab for serology analysis",
        )
        assert record["evidence_id"] == knife_evidence.id
        assert record["from"] == "HERNANDEZ, Maria; badge#4271"
        assert record["to"] == "LAB-TECH, Priya; badge#9903"
        assert record["reason"] == "Transfer to forensic biology lab for serology analysis"
        assert "transfer_id" in record
        assert "timestamp" in record
        assert record["evidence_type"] == "PHYSICAL"
        assert record["previous_status"] == "COLLECTED"

    def test_transfer_updates_evidence_status(self, populated_coc: Any, hard_drive_evidence: Any) -> None:
        """Assert: evidence status set to TRANSFERRED after log_transfer."""
        log_transfer(
            hard_drive_evidence.id,
            populated_coc,
            from_person="CHEN, Wei; badge#5502",
            to_person="DIGITAL-FORENSICS, Alex; badge#7701",
            reason="Transfer to digital forensics lab for imaging",
        )
        assert hard_drive_evidence.status == "TRANSFERRED"

    def test_transfer_appends_to_chain_log(self, populated_coc: Any, fiber_evidence: Any) -> None:
        """Assert: transfer record appended to chain transfer_log."""
        log_count_before = len(populated_coc.transfer_log)
        log_transfer(
            fiber_evidence.id,
            populated_coc,
            from_person="ODUYA, Jamal; badge#6190",
            to_person="LAB-TECH, Priya; badge#9903",
            reason="Transfer to trace evidence lab for microscopy",
        )
        assert len(populated_coc.transfer_log) == log_count_before + 1

    def test_reject_transfer_missing_evidence(self, coc: Any) -> None:
        """Act/Assert: non-existent evidence_id raises ValueError."""
        with pytest.raises(ValueError, match="not found in chain"):
            log_transfer(
                "EVI-DEADBE",
                coc,
                from_person="Smith, John",
                to_person="Jones, Jane",
                reason="Test",
            )

    def test_reject_transfer_same_person(self, populated_coc: Any, knife_evidence: Any) -> None:
        """Act/Assert: same from_person and to_person raises ValueError."""
        with pytest.raises(ValueError, match="must be different"):
            log_transfer(
                knife_evidence.id,
                populated_coc,
                from_person="HERNANDEZ, Maria; badge#4271",
                to_person="HERNANDEZ, Maria; badge#4271",
                reason="Self-transfer not allowed",
            )

    def test_reject_transfer_empty_reason(self, populated_coc: Any, knife_evidence: Any) -> None:
        """Act/Assert: empty reason string raises ValueError."""
        with pytest.raises(ValueError, match="reason must be a non-empty string"):
            log_transfer(
                knife_evidence.id,
                populated_coc,
                from_person="HERNANDEZ, Maria; badge#4271",
                to_person="LAB-TECH, Priya; badge#9903",
                reason="",
            )

    def test_multiple_transfers_chain_properly(
        self, populated_coc: Any, blood_sample_evidence: Any
    ) -> None:
        """Act: log 3 sequential transfers; Assert: chain continuity."""
        t1 = log_transfer(
            blood_sample_evidence.id,
            populated_coc,
            from_person="HERNANDEZ, Maria; badge#4271",
            to_person="EVIDENCE-CLERK, Sarah; badge#4400",
            reason="Check-in to evidence vault",
        )
        t2 = log_transfer(
            blood_sample_evidence.id,
            populated_coc,
            from_person="EVIDENCE-CLERK, Sarah; badge#4400",
            to_person="DNA-ANALYST, Robert; badge#8815",
            reason="Transfer to DNA lab for STR analysis",
        )
        t3 = log_transfer(
            blood_sample_evidence.id,
            populated_coc,
            from_person="DNA-ANALYST, Robert; badge#8815",
            to_person="EVIDENCE-CLERK, Sarah; badge#4400",
            reason="Return to evidence vault after analysis",
        )
        assert t1["to"] == t2["from"]
        assert t2["to"] == t3["from"]


# ── 6. verify_chain ─────────────────────────────────────────────────


class TestVerifyChain:
    """verify_chain function tests."""

    def test_perfect_chain_single_item(self, coc: Any) -> None:
        """Arrange: one item with collection event only."""
        ev = add_evidence_item(
            coc,
            type="PHYSICAL",
            description="Crowbar, red paint transfer, 24-inch",
            location="Back alley behind 555 Commerce St",
            collector="RODRIGUEZ, Carlos; badge#3319",
        )
        result = verify_chain(ev.id, coc)
        assert result["is_valid"] is True
        assert result["gaps"] == []
        assert result["issues"] == []
        assert result["evidence_id"] == ev.id
        assert result["case_id"] == coc.case_id

    def test_chain_with_gap_reports_custody_gap(
        self, populated_coc: Any, knife_evidence: Any
    ) -> None:
        """Arrange: transfer where from_person doesn't match prior to_person."""
        log_transfer(
            knife_evidence.id,
            populated_coc,
            from_person="HERNANDEZ, Maria; badge#4271",
            to_person="LAB-TECH, Priya; badge#9903",
            reason="Transfer to lab",
        )
        log_transfer(
            knife_evidence.id,
            populated_coc,
            from_person="STRANGER, Unknown; badge#0000",
            to_person="EVIDENCE-CLERK, Sarah; badge#4400",
            reason="Transfer to vault",
        )
        result = verify_chain(knife_evidence.id, populated_coc)
        assert result["is_valid"] is True
        assert len(result["gaps"]) == 1
        assert any(g["type"] == "custody_gap" for g in result["gaps"])

    def test_chain_with_no_collection_event_fails(self, coc: Any) -> None:
        """Arrange: create evidence item manually without add_evidence_item."""
        ev = EvidenceItem(
            id="EVI-MAN01",
            type="PHYSICAL",
            description="Manual item without collection log",
            collection_date="2024-07-14T08:00:00Z",
            location="Test location",
            collector="Test collector",
        )
        coc.evidence_items[ev.id] = ev
        result = verify_chain(ev.id, coc)
        assert "No collection event in chain" in result["issues"]
        assert len(result["gaps"]) > 0

    def test_chain_with_no_events_fails(self, coc: Any) -> None:
        """Arrange: evidence with zero transfer_log entries."""
        ev = EvidenceItem(
            id="EVI-NOEV01",
            type="DIGITAL",
            description="Ghost evidence",
            collection_date="2024-07-14T08:00:00Z",
            location="Nowhere",
            collector="Nobody",
        )
        coc.evidence_items[ev.id] = ev
        result = verify_chain(ev.id, coc)
        assert not result["is_valid"]
        assert any(g["type"] == "no_events" for g in result["gaps"])

    def test_sealed_without_history_reported(self, populated_coc: Any, fingerprint_evidence: Any) -> None:
        """Arrange: set seal_number without seal_history entries."""
        fingerprint_evidence.seal_number = "SEAL-009"
        result = verify_chain(fingerprint_evidence.id, populated_coc)
        assert any(g["type"] == "seal_without_history" for g in result["gaps"])
        assert "Sealed but no seal events recorded" in result["issues"]

    def test_missing_evidence_raises_error(self, coc: Any) -> None:
        """Act/Assert: non-existent evidence_id raises ValueError."""
        with pytest.raises(ValueError, match="not found in chain"):
            verify_chain("EVI-FAKE99", coc)


# ── 7. seal_evidence / break_seal ───────────────────────────────────


class TestSealEvidence:
    """seal_evidence function tests."""

    def test_seal_evidence_sets_status(self, populated_coc: Any, knife_evidence: Any) -> None:
        """Act: seal the knife evidence."""
        result = seal_evidence(knife_evidence.id, populated_coc, seal_number="SEAL-0042")
        assert result.status == "SEALED"
        assert result.seal_number == "SEAL-0042"

    def test_seal_evidence_appends_seal_history(self, populated_coc: Any, hard_drive_evidence: Any) -> None:
        """Assert: seal_history contains sealed event."""
        seal_evidence(
            hard_drive_evidence.id, populated_coc,
            seal_number="SEAL-FAR-001", sealed_by="CHEN, Wei; badge#5502",
        )
        assert len(hard_drive_evidence.seal_history) == 1
        assert hard_drive_evidence.seal_history[0]["event"] == "sealed"
        assert hard_drive_evidence.seal_history[0]["seal_number"] == "SEAL-FAR-001"
        assert hard_drive_evidence.seal_history[0]["applied_by"] == "CHEN, Wei; badge#5502"

    def test_cannot_seal_already_sealed_evidence(
        self, populated_coc: Any, knife_evidence: Any
    ) -> None:
        """Act/Assert: double-seal raises ValueError."""
        seal_evidence(knife_evidence.id, populated_coc, seal_number="SEAL-001")
        with pytest.raises(ValueError, match="already sealed"):
            seal_evidence(knife_evidence.id, populated_coc, seal_number="SEAL-002")


class TestBreakSeal:
    """break_seal function tests."""

    def test_break_seal_sets_unsealed_status(
        self, populated_coc: Any, blood_sample_evidence: Any
    ) -> None:
        """Act: seal then break seal."""
        seal_evidence(blood_sample_evidence.id, populated_coc, seal_number="SEAL-BIO-099")
        result = break_seal(
            blood_sample_evidence.id,
            populated_coc,
            reason="DNA extraction for STR analysis per court order #2024-771",
            authorized_by="JUDGE-THOMPSON, Elena; badge#J-441",
        )
        assert result.status == "UNSEALED"
        assert result.seal_number is None

    def test_break_seal_records_in_seal_history(
        self, populated_coc: Any, fiber_evidence: Any
    ) -> None:
        """Assert: seal_history contains both sealed and seal_broken events."""
        seal_evidence(fiber_evidence.id, populated_coc, seal_number="SEAL-TRC-012")
        break_seal(
            fiber_evidence.id,
            populated_coc,
            reason="Microscopy examination required",
            authorized_by="SUPERVISOR, David; badge#D-882",
        )
        events = [s["event"] for s in fiber_evidence.seal_history]
        assert "sealed" in events
        assert "seal_broken" in events

    def test_cannot_break_seal_on_unsealed_evidence(
        self, populated_coc: Any, fingerprint_evidence: Any
    ) -> None:
        """Act/Assert: breaking unsealed evidence raises ValueError."""
        with pytest.raises(ValueError, match="not sealed"):
            break_seal(
                fingerprint_evidence.id,
                populated_coc,
                reason="Attempt to examine",
                authorized_by="Investigator",
            )

    def test_break_seal_with_empty_authorization_raises(self, populated_coc: Any, knife_evidence: Any) -> None:
        """Act/Assert: empty authorized_by raises ValueError."""
        seal_evidence(knife_evidence.id, populated_coc, seal_number="SEAL-GAP-001")
        with pytest.raises(ValueError, match="authorized_by must be a non-empty string"):
            break_seal(
                knife_evidence.id,
                populated_coc,
                reason="Valid reason",
                authorized_by="",
            )


# ── 8. assess_contamination_risk ────────────────────────────────────


class TestAssessContaminationRisk:
    """assess_contamination_risk function tests."""

    def test_physical_paper_bag_room_temp_elevated_risk(self) -> None:
        """Arrange: PHYSICAL + paper bag (permeable) + room temp = LOW+ risk."""
        evidence = EvidenceItem(
            id="EVI-RSK01",
            type="PHYSICAL",
            description="Clothing item with possible GSR residue",
            collection_date="2024-07-14T10:00:00Z",
            location="Crime scene",
            collector="HERNANDEZ, Maria; badge#4271",
            packaging="PAPER_BAG",
            storage_conditions="ROOM_TEMP",
        )
        result = assess_contamination_risk(evidence)
        assert result["risk_level"] in ("LOW", "MEDIUM", "HIGH")
        assert result["risk_score"] >= 1
        assert result["evidence_id"] == "EVI-RSK01"

    def test_biological_multiple_seal_breaks_high_risk(self) -> None:
        """Arrange: BIOLOGICAL + 2 seal breaks = elevated risk."""
        evidence = EvidenceItem(
            id="EVI-RSK02",
            type="BIOLOGICAL",
            description="Blood sample, multiple unsealings",
            collection_date="2024-07-14T09:00:00Z",
            location="Lab",
            collector="NGUYEN, Tran; badge#8812",
            packaging="STERILE_CONTAINER",
            storage_conditions="REFRIGERATED",
            seal_history=[
                {"event": "sealed", "timestamp": "2024-07-14T09:30:00Z",
                 "seal_number": "S1", "applied_by": "A"},
                {"event": "seal_broken", "timestamp": "2024-07-15T10:00:00Z",
                 "seal_number": "S1", "broken_by": "B",
                 "reason": "Test 1"},
                {"event": "sealed", "timestamp": "2024-07-15T11:00:00Z",
                 "seal_number": "S2", "applied_by": "C"},
                {"event": "seal_broken", "timestamp": "2024-07-16T12:00:00Z",
                 "seal_number": "S2", "broken_by": "D",
                 "reason": "Test 2"},
            ],
        )
        result = assess_contamination_risk(evidence)
        assert result["risk_score"] >= 3
        assert result["risk_level"] in ("MEDIUM", "HIGH", "CRITICAL")
        assert "Seal broken 2x" in " ".join(result["reasoning"])

    def test_digital_faraday_bag_clean_room_low_risk(self) -> None:
        """Arrange: DIGITAL + FARADAY_BAG + CLEAN_ROOM = low risk."""
        evidence = EvidenceItem(
            id="EVI-RSK03",
            type="DIGITAL",
            description="Forensically imaged SSD",
            collection_date="2024-07-14T14:00:00Z",
            location="Data center rack 7",
            collector="CHEN, Wei; badge#5502",
            packaging="FARADAY_BAG",
            storage_conditions="CLEAN_ROOM",
        )
        result = assess_contamination_risk(evidence)
        assert result["risk_level"] == "LOW"
        assert result["risk_score"] == 1
        assert not result["requires_mitigation"]

    def test_many_handlers_elevates_risk(self) -> None:
        """Arrange: 5+ unique handlers across seal events."""
        evidence = EvidenceItem(
            id="EVI-RSK04",
            type="TRACE",
            description="Fiber sample, many handlers",
            collection_date="2024-07-14T08:00:00Z",
            location="Lab",
            collector="Handler-A",
            seal_history=[
                {"event": "sealed", "timestamp": "T1", "seal_number": "S1", "applied_by": "Handler-A"},
                {"event": "seal_broken", "timestamp": "T2", "reason": "R1", "broken_by": "Handler-B"},
                {"event": "sealed", "timestamp": "T3", "seal_number": "S2", "applied_by": "Handler-C"},
                {"event": "seal_broken", "timestamp": "T4", "reason": "R2", "broken_by": "Handler-D"},
                {"event": "sealed", "timestamp": "T5", "seal_number": "S3", "applied_by": "Handler-E"},
            ],
        )
        result = assess_contamination_risk(evidence)
        assert "Handler count" in " ".join(result["reasoning"])
        assert result["risk_score"] >= 4


# ── 9. generate_chain_report ───────────────────────────────────────


class TestGenerateChainReport:
    """generate_chain_report function tests."""

    def test_empty_chain_report(self, coc: Any) -> None:
        """Arrange: chain with zero evidence items."""
        report = generate_chain_report(coc)
        assert report["case_id"] == coc.case_id
        assert report["evidence_count"] == 0
        assert report["evidence_items"] == []
        assert report["total_transfers"] == 0
        assert report["chain_integrity"]["total_items"] == 0
        assert "generated_at" in report

    def test_populated_chain_report(self, populated_coc: Any) -> None:
        """Arrange: chain with 5 evidence items."""
        report = generate_chain_report(populated_coc)
        assert report["evidence_count"] == 5
        assert len(report["evidence_items"]) == 5
        assert report["chain_integrity"]["total_items"] == 5
        assert "type_summary" in report
        assert "storage_summary" in report
        assert "risk_summary" in report

    def test_report_includes_per_item_verification(
        self, populated_coc: Any, knife_evidence: Any
    ) -> None:
        """Assert: each item summary includes chain_verified and risk_score."""
        seal_evidence(knife_evidence.id, populated_coc, seal_number="SEAL-RPT-001")
        report = generate_chain_report(populated_coc)
        knife_summary = next(
            s for s in report["evidence_items"] if s["evidence_id"] == knife_evidence.id
        )
        assert "chain_verified" in knife_summary
        assert "risk_score" in knife_summary
        assert "seal_events" in knife_summary
        assert "transfers" in knife_summary

    def test_report_type_summary_counts_all_five_types(self, populated_coc: Any) -> None:
        """Assert: type_summary has all 5 evidence types."""
        report = generate_chain_report(populated_coc)
        assert report["type_summary"]["PHYSICAL"] == 1
        assert report["type_summary"]["DIGITAL"] == 1
        assert report["type_summary"]["BIOLOGICAL"] == 1
        assert report["type_summary"]["TRACE"] == 1
        assert report["type_summary"]["IMPRESSION"] == 1


# ── 10. verify_digital_signature ───────────────────────────────────


class TestVerifyDigitalSignature:
    """verify_digital_signature function tests."""

    def test_valid_signature_matches(self, populated_coc: Any, hard_drive_evidence: Any) -> None:
        """Arrange: compute expected SHA-256, verify matches."""
        canonical = "|".join([
            hard_drive_evidence.id,
            hard_drive_evidence.type,
            hard_drive_evidence.description,
            hard_drive_evidence.collection_date,
            hard_drive_evidence.collector,
            hard_drive_evidence.location,
        ])
        expected_sig = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        assert verify_digital_signature(populated_coc, hard_drive_evidence.id, expected_sig) is True

    def test_invalid_signature_rejected(self, populated_coc: Any, hard_drive_evidence: Any) -> None:
        """Arrange: wrong signature returns False."""
        result = verify_digital_signature(
            populated_coc,
            hard_drive_evidence.id,
            "deadbeef" * 8,
        )
        assert result is False

    def test_missing_evidence_raises_error(self, coc: Any) -> None:
        """Act/Assert: non-existent evidence_id raises ValueError."""
        with pytest.raises(ValueError, match="not found in chain"):
            verify_digital_signature(coc, "EVI-FAKE99", "aa" * 32)


# ── 11. Edge cases ──────────────────────────────────────────────────


class TestEdgeCases:
    """Edge case and stress tests."""

    def test_empty_chain_verify_fails(self, coc: Any) -> None:
        """Arrange: verify non-existent evidence in empty chain."""
        with pytest.raises(ValueError, match="not found in chain"):
            verify_chain("EVI-GHOST", coc)

    def test_large_evidence_set(self, coc: Any) -> None:
        """Act: add 50 evidence items of mixed types."""
        for i in range(50):
            add_evidence_item(
                coc,
                type=["PHYSICAL", "DIGITAL", "BIOLOGICAL", "TRACE", "IMPRESSION"][i % 5],
                description=f"Bulk evidence item {i:04d}",
                location="Warehouse A, Bay 7",
                collector=f"OFFICER-{i % 10:02d}",
            )
        assert len(coc.evidence_items) == 50
        report = generate_chain_report(coc)
        assert report["evidence_count"] == 50

    def test_duplicate_evidence_ids_not_possible(self, coc: Any) -> None:
        """Assert: add_evidence_item always generates unique IDs."""
        ev1 = add_evidence_item(
            coc,
            type="PHYSICAL",
            description="Item one",
            location="Location A",
            collector="Collector A",
        )
        ev2 = add_evidence_item(
            coc,
            type="DIGITAL",
            description="Item two",
            location="Location B",
            collector="Collector B",
        )
        assert ev1.id != ev2.id

    def test_full_lifecycle_seal_unseal_reseal(self, populated_coc: Any, knife_evidence: Any) -> None:
        """Act: seal -> break -> reseal -> verify chain."""
        seal_evidence(knife_evidence.id, populated_coc, seal_number="SEAL-LIFE-001")
        break_seal(
            knife_evidence.id, populated_coc,
            reason="Serology swabbing for presumptive blood test",
            authorized_by="JUDGE-MARTINEZ, Ana; badge#J-115",
        )
        seal_evidence(knife_evidence.id, populated_coc, seal_number="SEAL-LIFE-002")
        assert len(knife_evidence.seal_history) == 3
        assert knife_evidence.seal_number == "SEAL-LIFE-002"
        result = verify_chain(knife_evidence.id, populated_coc)
        assert result["has_seal_history"] is True

    def test_transfer_chain_with_all_evidence_types(
        self, populated_coc: Any,
        knife_evidence: Any, hard_drive_evidence: Any,
        blood_sample_evidence: Any, fiber_evidence: Any,
        fingerprint_evidence: Any,
    ) -> None:
        """Act: transfer every piece of evidence once; Assert: all TRANSFERRED."""
        for ev in [
            knife_evidence, hard_drive_evidence, blood_sample_evidence,
            fiber_evidence, fingerprint_evidence,
        ]:
            log_transfer(
                ev.id, populated_coc,
                from_person=ev.collector,
                to_person="EVIDENCE-VAULT, Central; badge#VAULT",
                reason=f"Transfer to vault: {ev.type}",
            )
            assert ev.status == "TRANSFERRED"

    def test_data_tables_consistency(self) -> None:
        """Assert: data tables have expected cross-references."""
        for tkey, tdata in EVIDENCE_TYPES.items():
            default_storage = tdata["default_storage"]
            assert default_storage in STORAGE_CONDITIONS, (
                f"Evidence type '{tkey}' default storage '{default_storage}' not in STORAGE_CONDITIONS"
            )
            assert tkey in STORAGE_CONDITIONS[default_storage]["suitable"], (
                f"Storage '{default_storage}' does not list '{tkey}' as suitable"
            )
            default_pkg = tdata["default_packaging"]
            assert default_pkg in PACKAGING_PROTOCOLS
            assert tkey in PACKAGING_PROTOCOLS[default_pkg]["suitable_for"]

    def test_labeling_standard_required_fields(self) -> None:
        """Assert: all labeling standard fields are marked required."""
        for field, spec in LABELING_STANDARD.items():
            assert spec["required"] == "yes", (
                f"Label field '{field}' must be required"
            )

    def test_all_evidence_statuses_have_description(self) -> None:
        """Assert: all statuses have a desc key."""
        for status_key, status_data in EVIDENCE_STATUSES.items():
            assert "desc" in status_data, f"Status '{status_key}' missing desc"
            assert "terminal" in status_data, f"Status '{status_key}' missing terminal"

    def test_contamination_risk_levels_have_required_keys(self) -> None:
        """Assert: all risk levels have level, description, requires_mitigation."""
        for _level, data in CONTAMINATION_RISK_LEVELS.items():
            assert "level" in data
            assert "description" in data
            assert "requires_mitigation" in data

    def test_packaging_protocols_have_required_keys(self) -> None:
        """Assert: all packaging protocols have material and suitable_for."""
        for _proto, data in PACKAGING_PROTOCOLS.items():
            assert "material" in data
            assert "suitable_for" in data
            assert isinstance(data["suitable_for"], list)

    def test_chain_modified_after_seal_and_break(
        self, populated_coc: Any, hard_drive_evidence: Any
    ) -> None:
        """Assert: last_modified updates on seal and break_seal."""
        ts_before = populated_coc.last_modified
        seal_evidence(hard_drive_evidence.id, populated_coc, seal_number="SEAL-TS-001")
        assert populated_coc.last_modified != ts_before
        ts_after_seal = populated_coc.last_modified
        break_seal(
            hard_drive_evidence.id, populated_coc,
            reason="Forensic imaging",
            authorized_by="SUPERVISOR, David; badge#D-882",
        )
        assert populated_coc.last_modified != ts_after_seal

    def test_transfer_log_has_creation_plus_items(
        self, populated_coc: Any, knife_evidence: Any
    ) -> None:
        """Assert: transfer_log contains chain_created + evidence_collected entries."""
        events = [e["event"] for e in populated_coc.transfer_log]
        assert "chain_created" in events
        assert events.count("evidence_collected") == 5


# ── token count ─────────────────────────────────────────────────────


def test_count() -> None:
    """Ensure 35+ test functions exist."""
    import inspect
    import sys
    mod = sys.modules[__name__]
    test_funcs = [
        name for name, obj in inspect.getmembers(mod)
        if name.startswith("test_") and callable(obj)
    ]
    assert len(test_funcs) >= 35, f"Expected >=35 test functions, got {len(test_funcs)}"
