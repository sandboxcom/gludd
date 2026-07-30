"""Dedup verification: no two connector files define the same class name (#76).

After canonicalizing the 7 duplicate connector pairs, this test verifies
that no two files under ``src/general_ludd/connectors/`` define a class with
the same name.  Internal utility classes (transports, error types) that are
intentionally duplicated per self-contained module are allowlisted.

Design constraints:
  * Uses AST parsing (stdlib ``ast``) — no imports, no side effects.
  * Excludes ``__init__.py`` and the shared-infra modules (``base.py``,
    ``registry.py``, ``normalize.py``, ``ingest.py``, ``ingest_formats.py``).
  * Allowlisted names: utility classes that are conventionally duplicated
    across self-contained connector modules by design.
"""

from __future__ import annotations

import ast
from pathlib import Path

CONNECTORS_DIR = (
    Path(__file__).resolve().parents[2]
    / "src" / "general_ludd" / "connectors"
)

_EXCLUDED_FILES = frozenset(
    {
        "__init__",
        "base",
        "registry",
        "normalize",
        "ingest",
        "ingest_formats",
        "_protocols",
        "_errors",
    }
)

_UTILITY_ALLOWLIST: frozenset[str] = frozenset(
    {
        # Transport abstractions (per-module, self-contained — signature varies)
        "HttpTransport",
        "_Transport",
        "Transport",
        "_UrllibTransport",
        "_UrllibResponse",
        "_HttpxTransport",
        "_DefaultTransport",
        "TransportResponse",
        # Error types (per-module, self-contained)
        "NomadSSRFError",
        "NomadTransportError",
        "PredicateValidationError",
        "PredicateError",
        "OpenShiftConfigError",
        "ElasticsearchConfigError",
        "StatsdParseError",
        "MqttBrokerBlockedError",
        "PathConfinementError",
        "_ConfigError",
        "_MissingToken",
        "_PysnmpUnavailable",
        # Runners / helpers (per-module, self-contained)
        "CommandRunner",
        "RunResult",
        "FileReader",
        "_Client",
        "_AwsClient",
        "HttpGet",
        "Record",
        "ContainerdConfig",
        "RedfishConfig",
        # Concrete connector Sources (must each be unique)
        # No Source class names in the allowlist — duplicates here are bugs.
        # AWS response TypedDicts / shapes (per-module, self-contained)
        "CloudTrailLookupEvent",
        "CloudWatchLogEvent",
        "FilterLogEventsResponse",
        "HealthStatus",
        "LookupEventsResponse",
        "NormalizedRecord",
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _class_names_in_file(file_path: Path) -> set[str]:
    """Extract concrete top-level class names from a Python source file via AST."""
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"))
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            is_protocol = any(
                (isinstance(base, ast.Name) and base.id == "Protocol")
                or (isinstance(base, ast.Attribute) and base.attr == "Protocol")
                for base in node.bases
            )
            if is_protocol:
                continue
            names.add(node.name)
    return names


def _build_duplicate_map() -> dict[str, list[str]]:
    """Return {class_name: [module_stem, ...]} for duplicates found."""
    class_to_modules: dict[str, list[str]] = {}
    for py_file in sorted(CONNECTORS_DIR.glob("*.py")):
        stem = py_file.stem
        if stem in _EXCLUDED_FILES:
            continue
        for cls_name in _class_names_in_file(py_file):
            if cls_name in _UTILITY_ALLOWLIST:
                continue
            if cls_name.startswith("_"):
                continue
            class_to_modules.setdefault(cls_name, []).append(stem)

    return {
        name: modules
        for name, modules in class_to_modules.items()
        if len(modules) > 1
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_connectors_directory_exists() -> None:
    assert CONNECTORS_DIR.is_dir(), f"Connectors directory not found: {CONNECTORS_DIR}"


def test_protocol_interfaces_are_not_concrete_duplicate_candidates(
    tmp_path: Path,
) -> None:
    source = tmp_path / "connector.py"
    source.write_text(
        "from typing import Protocol\n"
        "class Transport(Protocol):\n"
        "    pass\n"
        "class ConcreteConnector:\n"
        "    pass\n",
        encoding="utf-8",
    )
    assert _class_names_in_file(source) == {"ConcreteConnector"}


def test_no_duplicate_class_names_across_connector_files() -> None:
    """No two connector files define the same non-allowlisted class name."""
    duplicates = _build_duplicate_map()
    msg_lines: list[str] = []
    for cls_name, modules in sorted(duplicates.items()):
        msg_lines.append(f"  {cls_name}: defined in {', '.join(modules)}")
    assert not duplicates, (
        "Duplicate class names found across connector modules:\n"
        + "\n".join(msg_lines)
        + "\n\nIf this is a legitimate utility class duplicated by convention, "
        + "add it to _UTILITY_ALLOWLIST in this test file. "
        + "If this is a true duplicate connector, merge or delete the non-canonical."
    )


def test_windows_event_only_one_module() -> None:
    """The old windows_event.py must not exist."""
    assert not (CONNECTORS_DIR / "windows_event.py").is_file(), (
        "windows_event.py still exists — it was superseded by windows_event_log.py"
    )


def test_docker_api_only_one_module() -> None:
    """The old docker_api.py must not exist."""
    assert not (CONNECTORS_DIR / "docker_api.py").is_file(), (
        "docker_api.py still exists — it was superseded by docker_engine.py"
    )


def test_tempo_zipkin_deduped() -> None:
    """tempo_zipkin.py must not exist — the standalone tempo.py and zipkin.py
    are canonical (63 combined tests vs 28 for the combined class)."""
    assert not (CONNECTORS_DIR / "tempo_zipkin.py").is_file(), (
        "tempo_zipkin.py still exists — the standalone tempo.py and zipkin.py "
        "are canonical"
    )


def test_connector_files_are_parseable() -> None:
    """Every .py file in the connectors directory must parse without error."""
    failures: list[str] = []
    for py_file in sorted(CONNECTORS_DIR.glob("*.py")):
        stem = py_file.stem
        if stem in _EXCLUDED_FILES:
            continue
        try:
            ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError as e:
            failures.append(f"{py_file.name}: {e}")
    assert not failures, (
        "Connector files with syntax errors:\n" + "\n".join(failures)
    )
