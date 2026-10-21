"""Native Make transport carries the configured SSH identity to both observers."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 40


@pytest.mark.parametrize(("ledger_rc", "status_rc"), [(0, 0), (5, 0), (0, 7), (5, 7)])
def test_pipeline_status_uses_configured_key_for_both_native_git_lookups(
    tmp_path: Path, ledger_rc: int, status_rc: int,
) -> None:
    """Both lookups use one explicit identity and preserve either owner's failure."""
    content = compose_makefile(ROOT / "Makefile")
    start = content.index("\npipeline-status:") + 1
    end = content.index("\n\n", start)
    makefile = tmp_path / "Makefile"
    makefile.write_text(content[start:end] + "\n", encoding="utf-8")
    capture = tmp_path / "transport.jsonl"
    phases = tmp_path / "phases.jsonl"
    git = tmp_path / "git"
    git.write_text(
        f"#!{sys.executable}\n"
        "import json, os\nfrom pathlib import Path\n"
        f"with Path({str(capture)!r}).open('a') as stream:\n"
        "    stream.write(json.dumps(os.environ.get('GIT_SSH_COMMAND')) + '\\n')\n"
        f"print({SHA!r})\n",
        encoding="utf-8",
    )
    git.chmod(0o755)
    uv = tmp_path / "uv.py"
    uv.write_text(
        "import json, sys\nfrom pathlib import Path\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from scripts import pipeline_status as owner\n"
        "assert sys.argv[1:3] == ['run', 'python']\n"
        f"with Path({str(tmp_path / 'launches.jsonl')!r}).open('a') as stream:\n"
        "    stream.write(json.dumps(Path(sys.argv[3]).name) + '\\n')\n"
        "def observe(phase, result):\n"
        f"    assert owner.remote_head(branch='development', remote='sandboxcom') == {SHA!r}\n"
        f"    with Path({str(phases)!r}).open('a') as stream:\n"
        "        stream.write(json.dumps(phase) + '\\n')\n"
        "    return result\n"
        "if Path(sys.argv[3]).name == 'ci_failure_ledger.py':\n"
        f"    sys.exit(observe('ci_failure_ledger.py', {ledger_rc!r}))\n"
        f"owner.ci_failure_ledger.main = lambda argv: observe('ci_failure_ledger.py', {ledger_rc!r})\n"
        f"owner._status = lambda args: observe('pipeline_status.py', {status_rc!r})\n"
        "sys.exit(owner.main(sys.argv[4:]))\n",
        encoding="utf-8",
    )
    environment = {**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}"}
    environment.pop("GIT_SSH_COMMAND", None)
    key = tmp_path / "deploy-key"
    result = subprocess.run(
        [
            "make", "--no-print-directory", "-f", str(makefile), "pipeline-status",
            f"UV={shlex.quote(sys.executable)} {shlex.quote(str(uv))}",
            f"SSH_KEY={key}", "PIPELINE_STATUS_REPO=sandboxcom/gludd",
            "PIPELINE_STATUS_BRANCH=development", "PIPELINE_STATUS_REMOTE=sandboxcom",
            "PIPELINE_STATUS_SHA=", f"PIPELINE_STATUS_FAILURE_LEDGER={tmp_path / 'ledger.json'}",
            "PIPELINE_STATUS_VALIDATE_ONLY=0",
            f"PIPELINE_STATUS_OUTPUT={tmp_path / 'snapshot.json'}",
        ],
        cwd=tmp_path, env=environment, text=True, capture_output=True, check=False, timeout=30,
    )
    assert (result.returncode == 0) is (ledger_rc == status_rc == 0), result.stdout + result.stderr
    assert [json.loads(line) for line in phases.read_text().splitlines()] == [
        "ci_failure_ledger.py", "pipeline_status.py",
    ]
    transports = [json.loads(line) for line in capture.read_text().splitlines()]
    assert len(transports) == 2
    assert all(
        command is not None
        and shlex.split(command) == ["ssh", "-i", str(key), "-o", "StrictHostKeyChecking=accept-new"]
        for command in transports
    ), transports
    launches = [json.loads(line) for line in (tmp_path / "launches.jsonl").read_text().splitlines()]
    assert launches == ["pipeline_status.py"]
