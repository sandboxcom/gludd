"""SBOM generation tests."""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def _generate_sbom(out_path: Path) -> subprocess.CompletedProcess:
    """Generate a CycloneDX SBOM to out_path. Caller checks returncode/output —
    never parse blindly (a transient uv/tool failure leaves an empty file)."""
    return subprocess.run(
        ["uv", "run", "cyclonedx-py", "environment", ".venv", "-o", str(out_path), "--of", "JSON"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=180,
    )


class TestSBOM:
    def test_cyclonedx_in_dev_deps(self) -> None:
        import tomllib

        with open(ROOT / "pyproject.toml", "rb") as f:
            data = tomllib.load(f)
        dev_deps = data.get("project", {}).get("optional-dependencies", {}).get("dev", [])
        assert any("cyclonedx" in d for d in dev_deps)

    def test_sbom_generation_succeeds(self, tmp_path: Path) -> None:
        out = tmp_path / "sbom.json"
        result = _generate_sbom(out)
        assert result.returncode == 0, f"SBOM generation failed: {result.stderr[-400:]}"

    def test_sbom_is_valid_json(self, tmp_path: Path) -> None:
        # Self-contained: generate to an ISOLATED tmp file (no shared dist/ file
        # raced across xdist workers, no ordering dependency on the other test)
        # and verify generation SUCCEEDED before parsing — so a transient uv/tool
        # failure surfaces as a clear assert with stderr, not a cryptic
        # JSONDecodeError on an empty/partial shared file.
        out = tmp_path / "sbom.json"
        result = _generate_sbom(out)
        assert result.returncode == 0 and out.exists(), (
            f"SBOM generation failed (rc={result.returncode}): {result.stderr[-400:]}"
        )
        content = out.read_text().strip()
        assert content, f"SBOM file is empty; stderr={result.stderr[-400:]}"
        data = json.loads(content)
        assert "components" in data or "bomFormat" in data
