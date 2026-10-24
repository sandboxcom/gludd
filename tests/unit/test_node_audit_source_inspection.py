"""Native fixed-source Node advisory research stays bounded and read-only."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
TARGET = "inspect-node-audit-source"
MODE = "NODE_AUDIT_SOURCE_VALIDATE_ONLY"
RECURSION = {"MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES", "GNUMAKEFLAGS", "MAKEFILES"}
URLS = (
    "https://api.github.com/advisories/GHSA-4qx8-w7c3-pj3p",
    "https://api.github.com/advisories/GHSA-9j65-hcg3-gc64",
    "https://api.github.com/advisories/GHSA-9qmh-m7c6-f92p",
    "https://api.github.com/advisories/GHSA-mrhx-c7xc-m3w5",
    "https://registry.npmjs.org/toml/latest",
    "https://registry.npmjs.org/zod/latest",
    "https://registry.npmjs.org/@opencode-ai%2fplugin/latest",
    "https://registry.npmjs.org/-/package/effect/dist-tags",
    "https://api.github.com/repos/colinhacks/zod/issues/1872",
)


def _run(
    tmp_path: Path, *, mode: str = "1", overrides: bool = False,
    origin: str = "command-line", fail_at: int = 0, failure_code: int = 22,
    target: str = TARGET,
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, object]]]:
    binaries = tmp_path / "owned-bin"
    binaries.mkdir(exist_ok=True)
    capture = tmp_path / "curl.jsonl"
    curl = binaries / "curl"
    curl.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        f"capture = Path({str(capture)!r})\n"
        "number = len(capture.read_text().splitlines()) + 1 if capture.exists() else 1\n"
        "with capture.open('a', encoding='utf-8') as stream:\n"
        "    stream.write(json.dumps({'argv': sys.argv[1:], 'env': dict(os.environ)}) + '\\n')\n"
        "print('OWNED_NODE_SOURCE_' + str(number), flush=True)\n"
        f"if number == {fail_at}:\n"
        "    print('native source transport refusal', file=sys.stderr)\n"
        f"    raise SystemExit({failure_code})\n",
        encoding="utf-8",
    )
    curl.chmod(0o700)
    forbidden = tmp_path / "forbidden-command"
    for command in ("npm", "gh"):
        executable = binaries / command
        executable.write_text(
            f"#!{sys.executable}\nfrom pathlib import Path\n"
            f"Path({str(forbidden)!r}).touch()\nraise SystemExit(91)\n", encoding="utf-8",
        )
        executable.chmod(0o700)
    makefile = tmp_path / "owned-node-research.mk"
    makefile.write_text(
        f"include {ROOT / 'make/00-foundation.mk'}\n"
        "unrelated-node-source-probe:\n\t@curl probe\n"
        "indirect-node-source-probe: inspect-node-audit-source\n", encoding="utf-8",
    )
    environment = {key: value for key, value in os.environ.items()
                   if key not in RECURSION | {MODE}}
    environment.update(
        PATH=str(binaries) + os.pathsep + environment.get("PATH", os.defpath),
        GH_TOKEN="owned-fixture-token", GITHUB_TOKEN="owned-fixture-token",
        CURL_HOME=str(tmp_path / "unused-curl-config"),
        HTTPS_PROXY="http://127.0.0.1:9", ALL_PROXY="http://127.0.0.1:9",
        URL="https://unreviewed.invalid/environment",
        GLUDD_NODE_SOURCE_UNRELATED="retained carrier",
        UV_PROJECT_ENVIRONMENT=sys.prefix, VIRTUAL_ENV=sys.prefix, UV_NO_SYNC="1",
        UV_PYTHON=f"{sys.version_info.major}.{sys.version_info.minor}",
        UV_PYTHON_DOWNLOADS="never",
    )
    if origin == "environment":
        environment[MODE] = mode
    result = subprocess.run(
        ["make", "--no-print-directory", *(["-e"] if overrides else []),
         "-f", str(makefile), target,
         *([MODE + "=" + mode] if origin == "command-line" else []),
         "URL=https://unreviewed.invalid/command-line", "PYTHON=" + sys.executable],
        cwd=tmp_path, env=environment, capture_output=True, text=True, check=False, timeout=15,
    )
    calls = [cast(dict[str, object], json.loads(line)) for line in capture.read_text().splitlines()] \
        if capture.exists() else []
    assert not forbidden.exists(), "public source research must not install, relock or authenticate"
    return result, calls


@pytest.mark.parametrize("overrides", [False, True])
def test_node_source_plan_is_pure_and_has_no_caller_url(tmp_path: Path, overrides: bool) -> None:
    result, calls = _run(tmp_path, overrides=overrides)
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == []
    assert "plan_only=true network=false requests=9" in result.stdout
    for url in URLS:
        assert url in result.stdout
    assert "unreviewed.invalid" not in result.stdout + result.stderr
    assert "gate_evidence=false" in result.stdout and "release_ready=false" in result.stdout


@pytest.mark.parametrize("overrides", [False, True])
def test_node_source_live_uses_exact_bounded_public_argv(tmp_path: Path, overrides: bool) -> None:
    result, calls = _run(tmp_path, mode="0", overrides=overrides)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(calls) == 9
    for index, (call, url) in enumerate(zip(calls, URLS, strict=True), start=1):
        arguments = cast(list[str], call["argv"])
        assert arguments[0] == "--disable"
        assert arguments[-1] == url
        for option, value in (
            ("--connect-timeout", "10"), ("--max-time", "20"),
            ("--max-filesize", "2097152"), ("--retry", "0"),
            ("--max-redirs", "0"), ("--proto", "=https"), ("--proto-redir", "=https"),
            ("--proxy", ""), ("--noproxy", "*"),
        ):
            assert arguments[arguments.index(option) + 1] == value
        for option in ("--location", "--fail-with-body", "--silent", "--show-error", "--tlsv1.2"):
            assert option in arguments
        assert not {"--user", "--netrc", "--header", "-H", "-K", "--config"} & set(arguments)
        environment = cast(dict[str, str], call["env"])
        assert environment["LC_ALL"] == "C"
        assert not {"GH_TOKEN", "GITHUB_TOKEN", "CURL_HOME", "HTTPS_PROXY", "ALL_PROXY", MODE,
                    "URL", "GLUDD_NODE_SOURCE_UNRELATED"} & environment.keys()
        assert f"OWNED_NODE_SOURCE_{index}" in result.stdout
    assert result.stdout.count("NODE_AUDIT_SOURCE_BEGIN ") == 9
    assert result.stdout.count("NODE_AUDIT_SOURCE_END ") == 9
    assert "completed=true requests=9" in result.stdout
    assert "fix_availability=unverified" in result.stdout
    assert "owned-fixture-token" not in result.stdout + result.stderr


@pytest.mark.parametrize("failure_at,failure_code", [(1, 22), (5, 28), (8, 47), (9, 22)])
def test_node_source_failure_stops_without_hiding_prior_raw(
    tmp_path: Path, failure_at: int, failure_code: int,
) -> None:
    result, calls = _run(tmp_path, mode="0", fail_at=failure_at, failure_code=failure_code)
    assert result.returncode == 2
    assert len(calls) == failure_at
    for index in range(1, failure_at + 1):
        assert f"OWNED_NODE_SOURCE_{index}" in result.stdout
    assert f"native_status={failure_code}" in result.stdout
    assert "native source transport refusal" in result.stderr
    assert f"Error {failure_code}" in result.stderr
    assert "completed=true" not in result.stdout
    assert "no_unchanged_retry=true" in result.stdout


@pytest.mark.parametrize("overrides", [False, True])
@pytest.mark.parametrize("origin", ["command-line", "environment"])
@pytest.mark.parametrize("invalid", ["", "2", "true", "01", "1 ", " 1"])
def test_node_source_mode_matches_native_literal_boundary(
    tmp_path: Path, overrides: bool, origin: str, invalid: str,
) -> None:
    result, calls = _run(tmp_path, mode=invalid, overrides=overrides, origin=origin)
    if origin == "command-line" and invalid == " 1":
        witness = tmp_path / "owned-native-mode.mk"
        witness.write_text("mode:\n\t@printf '%s' '$(value " + MODE + ")'\n", encoding="utf-8")
        environment = {key: value for key, value in os.environ.items()
                       if key not in RECURSION | {MODE}}
        native = subprocess.run(
            ["make", "--no-print-directory", *(["-e"] if overrides else []),
             "-f", str(witness), "mode", MODE + "=" + invalid],
            cwd=tmp_path, env=environment, capture_output=True, text=True, check=False, timeout=10,
        )
        assert native.returncode == 0 and native.stdout == "1"
        assert result.returncode == 0, result.stdout + result.stderr
        assert "plan_only=true network=false" in result.stdout
    else:
        assert result.returncode != 0
        assert MODE + " must be literal 0 or 1" in result.stderr
    assert calls == []


@pytest.mark.parametrize("overrides", [False, True])
@pytest.mark.parametrize("origin", ["command-line", "environment"])
@pytest.mark.parametrize("payload_form", ["make-shell", "backticks"])
def test_node_source_literal_guard_has_native_same_origin_evaluation_witness(
    tmp_path: Path, overrides: bool, origin: str, payload_form: str,
) -> None:
    marker = tmp_path / "owned evaluation witness"
    command = "touch " + shlex.quote(str(marker))
    payload = "$(shell " + command + ")" if payload_form == "make-shell" else "`" + command + "`"
    witness = tmp_path / "owned-unguarded.mk"
    witness.write_text("witness:\n\t@printf '%s' \"$(" + MODE + ")\" >/dev/null\n", encoding="utf-8")
    environment = {key: value for key, value in os.environ.items()
                   if key not in RECURSION | {MODE}}
    if origin == "environment":
        environment[MODE] = payload
    result = subprocess.run(
        ["make", "--no-print-directory", *(["-e"] if overrides else []),
         "-f", str(witness), "witness",
         *([MODE + "=" + payload] if origin == "command-line" else [])],
        cwd=tmp_path, env=environment, capture_output=True, text=True, check=False, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker.exists(), "the actual same input/origin must first demonstrate evaluation"
    marker.unlink()
    guarded, calls = _run(tmp_path, mode=payload, overrides=overrides, origin=origin)
    assert guarded.returncode != 0
    assert MODE + " must be literal 0 or 1" in guarded.stderr
    assert not marker.exists()
    assert calls == []


@pytest.mark.parametrize("overrides", [False, True])
def test_node_source_literal_boundary_is_scoped_to_owned_target(tmp_path: Path, overrides: bool) -> None:
    result, calls = _run(
        tmp_path, mode="unrelated-owned-value", overrides=overrides,
        origin="environment", target="unrelated-node-source-probe",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(calls) == 1 and calls[0]["argv"] == ["probe"]
    environment = cast(dict[str, str], calls[0]["env"])
    assert environment[MODE] == "unrelated-owned-value"
    assert environment["GLUDD_NODE_SOURCE_UNRELATED"] == "retained carrier"


@pytest.mark.parametrize("overrides", [False, True])
def test_node_source_transport_requires_an_explicit_direct_goal(tmp_path: Path, overrides: bool) -> None:
    result, calls = _run(tmp_path, mode="0", overrides=overrides, target="indirect-node-source-probe")
    assert result.returncode != 0
    assert "inspect-node-audit-source must be invoked directly" in result.stderr
    assert calls == []
