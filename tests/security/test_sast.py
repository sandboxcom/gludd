"""SAST scanning tests — verify bandit runs and finds no high-severity issues."""
import json
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def _run_bandit_json(extra_args: list[str] | None = None) -> dict:
    """Run bandit (``-o <file>``, the proven path) and return its JSON report.

    Hardened over the original: a transient ``uv``/bandit failure used to leave
    an EMPTY report file and surface as a cryptic ``JSONDecodeError`` with no
    cause. Now we retry, and if the report is still empty we raise with bandit's
    stderr so the real reason is visible (observability invariant: never swallow
    a subprocess error).
    """
    cmd_base = ["uv", "run", "bandit", "-r", "src/", "-f", "json"]
    last = ""
    for attempt in range(3):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        cmd = [*cmd_base, "-o", str(tmp_path), *(extra_args or [])]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, cwd=str(ROOT), timeout=180
        )
        try:
            content = tmp_path.read_text().strip()
        finally:
            tmp_path.unlink(missing_ok=True)
        if content:
            try:
                return json.loads(content)
            except json.JSONDecodeError as exc:  # pragma: no cover - defensive
                last = f"attempt {attempt}: report not JSON: {exc}; stderr={proc.stderr[-400:]}"
                continue
        last = f"attempt {attempt}: bandit wrote empty report (rc={proc.returncode}); stderr={proc.stderr[-400:]}"
    raise AssertionError(f"bandit did not produce a JSON report after retries — {last}")


class TestSAST:
    def test_bandit_config_exists(self) -> None:
        import tomllib

        with open(ROOT / "pyproject.toml", "rb") as f:
            data = tomllib.load(f)
        dev_deps = data.get("project", {}).get("optional-dependencies", {}).get("dev", [])
        assert any("bandit" in d for d in dev_deps)

    def test_bandit_runs_on_source(self) -> None:
        report = _run_bandit_json()
        high_issues = [
            r
            for r in report.get("results", [])
            if r.get("issue_confidence") == "HIGH" and r.get("issue_severity") == "HIGH"
        ]
        assert len(high_issues) == 0, f"High-severity SAST issues found: {[r['test_id'] for r in high_issues]}"

    def test_no_hardcoded_secrets(self) -> None:
        report = _run_bandit_json(["-t", "B106,B107"])
        assert len(report.get("results", [])) == 0, "Hardcoded secrets detected"

    def test_no_exec_usage(self) -> None:
        report = _run_bandit_json(["-t", "B102"])
        assert len(report.get("results", [])) == 0, "exec() usage detected"

    def test_no_insecure_yaml_load(self) -> None:
        report = _run_bandit_json()
        yaml_issues = [r for r in report.get("results", []) if r.get("test_id") == "B506"]
        assert len(yaml_issues) == 0, f"Insecure yaml.load() usage: {len(yaml_issues)} instances"
