"""SAST scanning tests — verify bandit runs and finds no high-severity issues."""

import json
import subprocess
import tempfile
from pathlib import Path
from typing import ClassVar

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
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT), timeout=180)
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
            if r.get("issue_confidence") == "HIGH"
            and r.get("issue_severity") == "HIGH"
            and r.get("line_number") not in self.SHELL_TRUE_ALLOWLIST.get(r.get("filename", ""), set())
            and r.get("line_number") not in self.B413_ALLOWLIST.get(r.get("filename", ""), set())
        ]
        assert len(high_issues) == 0, f"High-severity SAST issues found: {[r['test_id'] for r in high_issues]}"

    KNOWN_FALSE_POSITIVE_LINES: ClassVar[dict[str, set[int]]] = {
        "src/general_ludd/auth/browser_login.py": {85, 96, 105, 114, 123, 132, 144},
        "src/general_ludd/smoke.py": {1343},
        # token_id="unknown" is an STS status label for "token not found",
        # not a credential; bandit's B106 funcarg heuristic matches the name.
        "src/general_ludd/auth/sts.py": {154},
        # STUN protocol method param (RFC 8489 MESSAGE-INTEGRITY password),
        # defaulted to empty — not a hardcoded secret.
        "src/general_ludd/network/nat_traversal.py": {159},
    }

    # Intrinsic findings: the acceptance engine's exec() is the containment
    # boundary itself (static forbidden-scan first, subprocess probe, runtime
    # budget, FS-diff side-effect detection) — evaluating model-generated code
    # under those controls IS the feature; the sandbox process backend's
    # shell=True is the sandbox executor (preexec_fn applies restrictions).
    EXEC_ALLOWLIST: ClassVar[dict[str, set[int]]] = {
        "src/general_ludd/game_gen/acceptance.py": {347},
    }
    SHELL_TRUE_ALLOWLIST: ClassVar[dict[str, set[int]]] = {
        "src/general_ludd/sandbox/backends/process_backend.py": {65},
    }
    # B413 blacklists the legacy ``Crypto`` namespace; this repo pins the
    # MAINTAINED PyCryptodome fork which installs that same namespace —
    # bandit cannot distinguish the two distributions.
    B413_ALLOWLIST: ClassVar[dict[str, set[int]]] = {
        "src/general_ludd/algorithms/salsa20.py": {19},
    }

    def test_no_hardcoded_secrets(self) -> None:
        report = _run_bandit_json(["-t", "B106,B107"])
        real_hits = [
            r
            for r in report.get("results", [])
            if r["line_number"] not in self.KNOWN_FALSE_POSITIVE_LINES.get(r["filename"], set())
        ]
        assert len(real_hits) == 0, (
            f"Hardcoded secrets detected: {len(real_hits)} instances in {set(r['filename'] for r in real_hits)}"
        )

    def test_no_exec_usage(self) -> None:
        report = _run_bandit_json(["-t", "B102"])
        real_hits = [
            r
            for r in report.get("results", [])
            if r["line_number"] not in self.EXEC_ALLOWLIST.get(r["filename"], set())
        ]
        assert len(real_hits) == 0, "exec() usage detected outside the acceptance-engine containment boundary"

    def test_no_insecure_yaml_load(self) -> None:
        report = _run_bandit_json()
        yaml_issues = [r for r in report.get("results", []) if r.get("test_id") == "B506"]
        assert len(yaml_issues) == 0, f"Insecure yaml.load() usage: {len(yaml_issues)} instances"
