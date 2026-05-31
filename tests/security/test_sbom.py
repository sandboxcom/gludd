"""SBOM generation tests."""
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent


class TestSBOM:
    def test_cyclonedx_in_dev_deps(self) -> None:
        import tomllib

        with open(ROOT / "pyproject.toml", "rb") as f:
            data = tomllib.load(f)
        dev_deps = data.get("project", {}).get("optional-dependencies", {}).get("dev", [])
        assert any("cyclonedx" in d for d in dev_deps)

    def test_sbom_generation_succeeds(self) -> None:
        result = subprocess.run(
            [
                "uv",
                "run",
                "cyclonedx-py",
                "environment",
                ".venv",
                "-o",
                "dist/sbom-test.json",
                "--of",
                "JSON",
            ],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            timeout=120,
        )
        assert result.returncode == 0, f"SBOM generation failed: {result.stderr}"

    def test_sbom_is_valid_json(self) -> None:
        sbom_path = ROOT / "dist" / "sbom-test.json"
        if not sbom_path.exists():
            pytest.skip("SBOM not yet generated")
        with open(sbom_path) as f:
            data = json.load(f)
        assert "components" in data or "bomFormat" in data
