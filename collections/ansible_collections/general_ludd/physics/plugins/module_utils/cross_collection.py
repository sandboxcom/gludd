"""
cross_collection -- Registry and dispatch layer for cross-collection integration.

Maps topic domains to the collections and roles that service them, enabling
agents to discover and call related modules across collection boundaries.

Usage:
    from cross_collection import get_cross_collection_help, call_collection_role

    help_text = get_cross_collection_help("propagation")
    result = call_collection_role("radio", "propagation_model", {"distance_km": 10})
"""

from __future__ import annotations

from typing import Any

_CROSS_REGISTRY: dict[str, list[dict[str, str]]] = {
    "propagation": [
        {"collection": "general_ludd.radio", "role": "propagation_model",
         "module": "propagation_models.py", "note": "RF path-loss: ITM, Hata, free-space, rain"},
        {"collection": "general_ludd.physics", "role": "electrodynamics",
         "module": "electrodynamics.py", "note": "EM wave propagation, Maxwell, antenna gain"},
    ],
    "signal_processing": [
        {"collection": "general_ludd.radio", "role": "signal_identify",
         "module": "modulation_schemes.py", "note": "Modulation classification, baud rate detection"},
        {"collection": "general_ludd.physics", "role": "spectroscopy_analysis",
         "module": "spectroscopy.py", "note": "Spectrum fitting, peak deconvolution, baseline correction"},
    ],
    "electromagnetics": [
        {"collection": "general_ludd.physics", "role": "electrodynamics",
         "module": "electrodynamics.py", "note": "Maxwell equations, fields, waves, polarization"},
        {"collection": "general_ludd.radio", "role": "antenna_design",
         "module": "antenna_types.py", "note": "Antenna types, radiation patterns, impedance matching"},
    ],
    "cryptography": [
        {"collection": "general_ludd.binary_re", "role": "cyberchef_transform",
         "module": "", "note": "Encoding/decoding/encryption pipelines via CyberChef"},
        {"collection": "general_ludd.security", "role": "ssl_cert",
         "module": "", "note": "SSL/TLS certificate management, PKI operations"},
        {"collection": "general_ludd.security", "role": "hsm_operations",
         "module": "", "note": "Hardware Security Module key operations"},
    ],
    "reverse_engineering": [
        {"collection": "general_ludd.binary_re", "role": "gdb_analyze",
         "module": "", "note": "GDB automation, breakpoints, stack traces"},
        {"collection": "general_ludd.binary_re", "role": "radare2_analyze",
         "module": "", "note": "r2-based reversing, disassembly, entropy scan"},
        {"collection": "general_ludd.binary_re", "role": "ghidra_analyze",
         "module": "", "note": "Ghidra headless auto-analysis"},
        {"collection": "general_ludd.binary_re", "role": "deobfuscate",
         "module": "", "note": "Packing detection, CFG flattening, string deobfuscation"},
    ],
    "vulnerability": [
        {"collection": "general_ludd.security", "role": "sql_injection",
         "module": "", "note": "SQL injection detection and exploitation"},
        {"collection": "general_ludd.security", "role": "command_injection",
         "module": "", "note": "Command injection detection and exploitation"},
        {"collection": "general_ludd.security", "role": "prompt_injection",
         "module": "", "note": "Prompt injection detection for LLM systems"},
        {"collection": "general_ludd.binary_re", "role": "prompt_injection_scan",
         "module": "prompt_injection_detector.py", "note": "Binary/script prompt-injection scanning"},
    ],
    "fuzzing": [
        {"collection": "general_ludd.binary_re", "role": "fuzz_target",
         "module": "fuzzing_strategies.py", "note": "AFL++/libFuzzer harness, corpus management"},
    ],
    "spectroscopy": [
        {"collection": "general_ludd.physics", "role": "spectroscopy",
         "module": "spectroscopy.py", "note": "Simulate IR/Raman/UV-Vis/NMR spectra"},
        {"collection": "general_ludd.physics", "role": "mass_spectrometry",
         "module": "", "note": "Isotope pattern prediction, fragmentation trees"},
    ],
    "mathematics": [
        {"collection": "general_ludd.physics", "role": "math_solver",
         "module": "math_identities.py", "note": "Symbolic integration, ODE/PDE, matrix decomposition"},
        {"collection": "general_ludd.physics", "role": "math_modeler",
         "module": "math_modeler.py", "note": "Statistics, regression, ODE solving"},
    ],
    "chemistry": [
        {"collection": "general_ludd.physics", "role": "organic_chemistry",
         "module": "organic_chemistry.py", "note": "Reaction prediction, retrosynthesis"},
        {"collection": "general_ludd.physics", "role": "organic_synthesist",
         "module": "organic_synthesis.py", "note": "Synthesis planning, yield prediction"},
        {"collection": "general_ludd.physics", "role": "thermodynamics",
         "module": "thermodynamics.py", "note": "Enthalpy, entropy, phase diagrams"},
    ],
    "quantum": [
        {"collection": "general_ludd.physics", "role": "quantum_mechanics",
         "module": "quantum_mechanics.py", "note": "Schrodinger eq, eigenstates, wavefunctions"},
        {"collection": "general_ludd.physics", "role": "quantum_computer",
         "module": "quantum_computer.py", "note": "Quantum circuit simulation and algorithms"},
    ],
    "governance": [
        {"collection": "general_ludd.governance", "role": "governance_navigator",
         "module": "governing_bodies.py", "note": "Multi-country governance system navigation"},
        {"collection": "general_ludd.governance", "role": "tax_currency_info",
         "module": "tax_currency.py", "note": "Tax system and currency data lookup"},
    ],
    "forensics": [
        {"collection": "general_ludd.physics", "role": "spectroscopy",
         "module": "spectroscopy.py", "note": "Spectroscopic analysis for material identification"},
        {"collection": "general_ludd.physics", "role": "mass_spectrometry",
         "module": "", "note": "Mass spec for compound identification"},
        {"collection": "general_ludd.binary_re", "role": "ghidra_analyze",
         "module": "", "note": "Binary forensic analysis and reverse engineering"},
        {"collection": "general_ludd.binary_re", "role": "deobfuscate",
         "module": "obfuscation_techniques.py", "note": "Deobfuscation for forensic artifact analysis"},
    ],
    "networking": [
        {"collection": "general_ludd.networking", "role": "networking",
         "module": "", "note": "Core networking configuration and analysis"},
    ],
    "computer_science": [
        {"collection": "general_ludd.binary_re", "role": "ghidra_analyze",
         "module": "", "note": "Binary analysis, disassembly, and reverse engineering"},
        {"collection": "general_ludd.binary_re", "role": "deobfuscate",
         "module": "obfuscation_techniques.py", "note": "Deobfuscation, CFG analysis, packing detection"},
        {"collection": "general_ludd.physics", "role": "math_solver",
         "module": "math_identities.py", "note": "Algorithmic math: complexity analysis, graph theory, optimization"},
        {"collection": "general_ludd.security", "role": "audit_framework",
         "module": "", "note": "Security audit frameworks: static analysis, compliance, evidence collection"},
    ],
}


def list_topics() -> list[str]:
    """Return all registered cross-collection topic keys."""
    return sorted(_CROSS_REGISTRY.keys())


def get_cross_collection_help(topic: str) -> dict[str, Any]:
    """
    Return the registered collections and roles for a given topic.

    Returns dict with:
        topic: The queried topic
        entries: list of {collection, role, module, note}
        related_topics: topic keys that share at least one collection with the queried topic
    """
    entries = _CROSS_REGISTRY.get(topic, [])
    if not entries:
        return {"topic": topic, "entries": [], "related_topics": [], "error": f"No entries for topic '{topic}'. Available: {list_topics()}"}

    queried_collections = {e["collection"] for e in entries}
    related = []
    for other_topic, other_entries in _CROSS_REGISTRY.items():
        if other_topic == topic:
            continue
        other_collections = {e["collection"] for e in other_entries}
        if queried_collections & other_collections:
            related.append(other_topic)

    return {"topic": topic, "entries": entries, "related_topics": sorted(related)}


def call_collection_role(collection: str, role: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Dispatch a call to a named collection role.

    This is a routing layer that validates the collection/role exist in the registry
    and returns the dispatch intent. Actual role execution happens via Ansible.

    Parameters:
        collection: FQ collection name (e.g. "general_ludd.radio")
        role: Role name within the collection (e.g. "propagation_model")
        args: Optional dict of parameters to pass to the role

    Returns dict with:
        collection: The called collection
        role: The called role
        args: The passed arguments
        valid: Whether the collection+role combination exists in the registry
        topics: Topics this collection+role serves
    """
    if args is None:
        args = {}

    topics_served = []
    valid = False

    for topic, entries in _CROSS_REGISTRY.items():
        for entry in entries:
            if entry["collection"] == collection and entry["role"] == role:
                valid = True
                if topic not in topics_served:
                    topics_served.append(topic)

    return {
        "collection": collection,
        "role": role,
        "args": args,
        "valid": valid,
        "topics": topics_served,
    }


def collections_for_topic(topic: str) -> list[str]:
    """Return unique collection FQ names registered for a topic."""
    entries = _CROSS_REGISTRY.get(topic, [])
    return sorted({e["collection"] for e in entries})


def roles_for_collection(collection: str) -> list[dict[str, str]]:
    """Return all registered roles for a given collection FQ name."""
    results: dict[str, dict[str, str]] = {}
    for topic, entries in _CROSS_REGISTRY.items():
        for entry in entries:
            if entry["collection"] == collection:
                key = entry["role"]
                if key not in results:
                    results[key] = {"role": entry["role"], "topic": topic, "note": entry["note"]}
    return list(results.values())


__all__ = [
    "list_topics",
    "get_cross_collection_help",
    "call_collection_role",
    "collections_for_topic",
    "roles_for_collection",
]
