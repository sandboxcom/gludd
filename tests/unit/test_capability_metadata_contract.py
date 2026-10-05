"""Distribution contract for Gludd collection capability metadata."""

from __future__ import annotations

import json
import shutil
import subprocess
import tarfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

from general_ludd.dispatch.capabilities import discover_capabilities

ROOT = Path(__file__).resolve().parents[2]
COLLECTIONS_ROOT = ROOT / "collections/ansible_collections/general_ludd"

# Published by Ansible for collection galaxy.yml metadata. Gludd extensions
# belong in a sidecar so Galaxy can validate and package the manifest cleanly.
SUPPORTED_GALAXY_KEYS = frozenset(
    {
        "authors",
        "build_ignore",
        "dependencies",
        "description",
        "documentation",
        "homepage",
        "issues",
        "license",
        "license_file",
        "manifest",
        "name",
        "namespace",
        "readme",
        "repository",
        "tags",
        "version",
    }
)
CAPABILITY_COLLECTIONS = {
    "binary_re": (
        "ghidra_analyze",
        "gdb_analyze",
        "radare2_analyze",
        "frida_instrument",
        "deobfuscate",
        "fuzz_target",
        "cyberchef_transform",
        "prompt_injection_scan",
        "pe_analyze",
        "elf_analyze",
        "macho_analyze",
        "disassembly",
    ),
    "governance": (
        "border_lookup",
        "crossing_info",
        "visa_regime",
        "body_lookup",
        "mandate_lookup",
        "tax_lookup",
        "tax_compliance",
        "currency_lookup",
        "fx_regime",
        "conflict_status",
        "sanctions_lookup",
        "treaty_lookup",
        "ratification_status",
        "service_lookup",
        "civil_registry",
        "official_lookup",
        "authority_chain",
        "classification_lookup",
        "clearance_check",
        "postal_lookup",
        "customs_declaration",
        "conscription_lookup",
        "veteran_status",
        "license_lookup",
        "permit_check",
        "jurisdiction_lookup",
        "classification_markings",
        "authority_registry",
        "contract_lookup",
        "contract_search",
    ),
    "language": (
        "language_detection",
        "translation",
        "transliteration",
        "unicode_analyze",
        "encoding_detect",
        "font_analyze",
        "homoglyph_scan",
        "phonetic_transcribe",
        "locale_format",
        "i18n_extract",
        "bom_detect",
    ),
    "radio": (
        "spectrum_scan",
        "sdr_capture",
        "decode_digital",
        "signal_identify",
        "regulation_lookup",
        "link_budget",
        "propagation_model",
        "antenna_design",
        "exam_quiz",
        "marine_decode",
    ),
    "sandbox": (
        "process_sandbox",
        "container_sandbox",
        "firecracker_vm",
        "unikernel_vm",
        "resource_limits",
        "network_policy",
        "security_policy",
        "execution_isolation",
        "backend_routing",
        "capability_routing",
    ),
    "travel": ("trip_planning", "flight_search", "hotel_search", "web_search"),
}


def _load_mapping(path: Path) -> Mapping[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, Mapping), f"{path} must contain a YAML mapping"
    return value


@pytest.mark.parametrize(
    "collection",
    sorted(path.parent for path in COLLECTIONS_ROOT.glob("*/galaxy.yml")),
    ids=lambda path: path.name,
)
def test_all_galaxy_manifests_use_published_schema(collection: Path) -> None:
    galaxy = _load_mapping(collection / "galaxy.yml")
    unsupported = set(galaxy) - SUPPORTED_GALAXY_KEYS

    assert not unsupported, f"{collection.name} has unsupported galaxy.yml keys: {sorted(unsupported)}"


@pytest.mark.parametrize("collection_name", sorted(CAPABILITY_COLLECTIONS))
def test_capability_sidecar_is_canonical_and_loaded_verbatim(collection_name: str) -> None:
    sidecar = _load_mapping(COLLECTIONS_ROOT / collection_name / "capabilities.yml")
    assert set(sidecar) <= {"model_capabilities", "role_capabilities"}
    model_capabilities = sidecar.get("model_capabilities", [])
    role_capabilities = sidecar.get("role_capabilities", {})
    assert isinstance(model_capabilities, list)
    assert isinstance(role_capabilities, Mapping)
    assert tuple(capability["name"] for capability in model_capabilities) == (
        CAPABILITY_COLLECTIONS[collection_name]
    )

    metadata = discover_capabilities().collections[collection_name]
    assert metadata.model_capabilities == model_capabilities
    assert metadata.role_capabilities == role_capabilities


@pytest.mark.parametrize("collection_name", sorted(CAPABILITY_COLLECTIONS))
def test_real_galaxy_build_is_warning_free_and_preserves_dependencies(
    collection_name: str, tmp_path: Path
) -> None:
    ansible_galaxy = shutil.which("ansible-galaxy")
    assert ansible_galaxy is not None, "ansible-galaxy is required to verify release artifacts"
    collection = COLLECTIONS_ROOT / collection_name
    process = subprocess.run(
        [
            ansible_galaxy,
            "collection",
            "build",
            str(collection),
            "--output-path",
            str(tmp_path),
            "--force",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    output = process.stdout + process.stderr
    assert process.returncode == 0, output
    assert "[WARNING]" not in output, output

    artifacts = list(tmp_path.glob(f"general_ludd-{collection_name}-*.tar.gz"))
    assert len(artifacts) == 1
    with tarfile.open(artifacts[0], "r:gz") as artifact:
        manifest_member = artifact.extractfile("MANIFEST.json")
        assert manifest_member is not None
        manifest = json.load(manifest_member)
    galaxy = _load_mapping(collection / "galaxy.yml")
    assert manifest["collection_info"]["dependencies"] == galaxy["dependencies"]
