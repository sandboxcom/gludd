"""Known repaired advisory floors for the locked Node quality-tool graph."""

from __future__ import annotations

import json
from base64 import b64decode, b64encode
from pathlib import Path

import pytest
from packaging.specifiers import SpecifierSet

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("package", "affected"),
    [
        ("braces", "<=3.0.3"),
        ("js-yaml", ">=5.0.0,<5.4.1"),
        ("markdown-it", "<14.3.1"),
        ("smol-toml", "<=1.8.0"),
        ("katex", ">=0.11.0,<0.18.2"),
        ("toml", "<=5.0.0"),
        ("zod", "<4.6.6"),
    ],
)
def test_quality_lock_excludes_versions_with_available_security_fixes(package: str, affected: str) -> None:
    """All copies exclude the published affected range, including nested copies."""
    lock = json.loads((ROOT / ".opencode/package-lock.json").read_text())
    copies = [
        (name, record["version"])
        for name, record in lock["packages"].items()
        if name == f"node_modules/{package}" or name.endswith(f"/node_modules/{package}")
    ]

    assert all(version not in SpecifierSet(affected) for _name, version in copies), copies


def test_advisory_leaf_overrides_preserve_existing_plugin_and_effect_inputs() -> None:
    manifest = json.loads((ROOT / ".opencode/package.json").read_text())
    lock = json.loads((ROOT / ".opencode/package-lock.json").read_text())
    assert manifest["dependencies"]["@opencode-ai/plugin"] == "1.18.5"
    assert manifest.get("overrides") == {
        "@opencode-ai/plugin": {"zod": "4.6.7", "effect": {"toml": "5.0.1"}},
    }
    packages = lock["packages"]
    for package, version in (
        ("@opencode-ai/plugin", "1.18.5"),
        ("@opencode-ai/sdk", "1.18.5"),
        ("effect", "4.0.0-beta.83"),
        ("toml", "5.0.1"),
        ("zod", "4.6.7"),
    ):
        assert packages[f"node_modules/{package}"]["version"] == version
    for package, expected_digest in (
        ("toml", (
            18, 166, 13, 3, 246, 47, 80, 217,
            221, 172, 13, 129, 220, 237, 168, 143,
            50, 208, 161, 52, 43, 7, 23, 153,
            245, 66, 35, 67, 9, 60, 183, 93,
            40, 123, 169, 251, 119, 7, 37, 7,
            7, 125, 33, 214, 80, 76, 226, 176,
            123, 171, 214, 95, 224, 23, 182, 94,
            136, 158, 213, 209, 109, 234, 93, 197,
        )),
        ("zod", (
            131, 197, 182, 15, 105, 20, 233, 175,
            46, 158, 215, 52, 231, 119, 215, 214,
            229, 65, 43, 51, 59, 202, 160, 49,
            91, 253, 243, 194, 79, 55, 251, 164,
            114, 23, 111, 146, 40, 235, 149, 249,
            205, 48, 4, 56, 84, 195, 53, 117,
            61, 161, 154, 166, 102, 71, 250, 185,
            156, 83, 51, 223, 251, 112, 134, 194,
        )),
    ):
        algorithm, suffix = packages[f"node_modules/{package}"]["integrity"].split("-", 1)
        assert algorithm == "sha512"
        decoded = b64decode(suffix, validate=True)
        assert decoded == bytes(expected_digest)
        assert b64encode(decoded).decode("ascii") == suffix
