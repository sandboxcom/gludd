"""Native contracts for the fixed public browser-dependency source inspector."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import cast

import pytest
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]
PLAN = "inspect-presentation-browser-dependency-source"
LIVE = PLAN + "-live"
URLS = (
    "https://raw.githubusercontent.com/microsoft/playwright/v1.63.0/packages/playwright-core/src/server/registry/dependencies.ts",
    "https://raw.githubusercontent.com/microsoft/playwright/v1.63.0/packages/playwright-core/src/server/registry/nativeDeps.ts",
    "https://raw.githubusercontent.com/Debian/apt/main/doc/apt.conf.5.xml",
    "https://raw.githubusercontent.com/Debian/apt/main/doc/sources.list.5.xml",
    "https://api.github.com/repos/actions/runner-images/git/trees/main?recursive=1",
    "https://api.github.com/search/issues",
    "https://api.github.com/search/issues",
    "https://raw.githubusercontent.com/actions/runner-images/ubuntu24/20261012.334/images/ubuntu/scripts/build/configure-apt.sh",
    "https://raw.githubusercontent.com/actions/runner-images/ubuntu24/20261012.334/images/ubuntu/scripts/build/configure-apt-sources.sh",
)
QUERIES = (
    'q=repo:actions/runner-images is:issue "azure.archive.ubuntu.com" "Connection timed out" created:<2026-01-01',
    'q=repo:microsoft/playwright is:issue "azure.archive.ubuntu.com" "Connection timed out" created:<2026-01-01',
)


def _inspection_fragment() -> str:
    makefile = compose_makefile(ROOT / "Makefile")
    recipes = []
    for target in (PLAN, LIVE):
        marker = "\n" + target + ":\n"
        assert makefile.count(marker) == 1
        recipes.append(target + ":\n" + makefile.split(marker, 1)[1].split("\n\n", 1)[0])
    return ".PHONY: " + PLAN + " " + LIVE + "\n" + "\n\n".join(recipes) + "\n"


def _run_inspection(
    tmp_path: Path,
    target: str,
    *,
    environment_override: bool = False,
    fail_at: int = 0,
    failure_code: int = 22,
    search_payload: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, object]], Path]:
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    makefile = fixture / "Makefile"
    makefile.write_text(_inspection_fragment(), encoding="utf-8")
    binaries = fixture / "bin"
    binaries.mkdir()
    capture = fixture / "curl.jsonl"
    forbidden = fixture / "forbidden-command"
    curl = binaries / "curl"
    curl.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        f"path = pathlib.Path({str(capture)!r})\n"
        "number = len(path.read_text().splitlines()) + 1 if path.exists() else 1\n"
        "with path.open('a', encoding='utf-8') as output:\n"
        "    output.write(json.dumps({'argv': sys.argv[1:], 'env': dict(os.environ)}) + '\\n')\n"
        "print('RAW_SOURCE_FIXTURE_BODY_' + str(number), flush=True)\n"
        f"if number in (6, 7) and {search_payload!r} is not None:\n"
        f"    print({search_payload!r}, flush=True)\n"
        f"if number == {fail_at}:\n"
        "    print('fixture transport failure', file=sys.stderr, flush=True)\n"
        f"    raise SystemExit({failure_code})\n",
        encoding="utf-8",
    )
    curl.chmod(0o755)
    for command in ("gh", "apt", "apt-get", "sudo", "playwright", "uv"):
        tool = binaries / command
        tool.write_text(
            f"#!{sys.executable}\n"
            "import pathlib\n"
            f"pathlib.Path({str(forbidden)!r}).write_text({command!r})\n"
            "raise SystemExit(91)\n",
            encoding="utf-8",
        )
        tool.chmod(0o755)
    sentinel = fixture / "host-configuration-sentinel"
    sentinel.write_bytes(b"original source configuration\n")
    environment = os.environ.copy()
    for key in ("MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKELEVEL"):
        environment.pop(key, None)
    environment.update(
        PATH=str(binaries) + os.pathsep + os.defpath,
        HOME=str(fixture / "private-home"),
        GH_TOKEN="fixture-private-value",
        GITHUB_TOKEN="fixture-private-value",
        GH_HOST="private.invalid",
        CURL_HOME=str(fixture / "private-home"),
        CURL_CA_BUNDLE=str(fixture / "private-ca"),
        HTTPS_PROXY="http://127.0.0.1:9",
        http_proxy="http://127.0.0.1:9",
        ALL_PROXY="http://127.0.0.1:9",
        URL="https://unreviewed.invalid/source",
        PRESENTATION_BROWSER_SOURCE_VALIDATE_ONLY="invalid",
    )
    argv = ["make", "--no-print-directory", "-f", str(makefile)]
    if environment_override:
        argv.append("-e")
    argv.extend([target, "URL=https://command-line.invalid/source"])
    result = subprocess.run(
        argv,
        cwd=fixture,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    captured = (
        [cast(dict[str, object], json.loads(line)) for line in capture.read_text().splitlines()]
        if capture.exists()
        else []
    )
    assert sentinel.read_bytes() == b"original source configuration\n"
    assert not forbidden.exists(), "research must not invoke gh auth or system/package commands"
    return result, captured, fixture


@pytest.mark.parametrize("source_inspection_environment_override", [False, True])
def test_browser_source_inspection_plan_is_offline_with_no_user_url_authority(
    tmp_path: Path, source_inspection_environment_override: bool
) -> None:
    result, calls, _ = _run_inspection(
        tmp_path, PLAN, environment_override=source_inspection_environment_override
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == []
    assert "plan_only=true network=false" in result.stdout
    assert "requests=9" in result.stdout
    for url in URLS:
        assert url in result.stdout
    for query in QUERIES:
        assert query in result.stdout
    assert "unreviewed.invalid" not in result.stdout
    assert "command-line.invalid" not in result.stdout
    assert "fixture-private-value" not in result.stdout + result.stderr


@pytest.mark.parametrize("source_inspection_environment_override", [False, True])
def test_browser_source_inspection_live_uses_only_bounded_public_requests(
    tmp_path: Path, source_inspection_environment_override: bool
) -> None:
    result, calls, _ = _run_inspection(
        tmp_path, LIVE, environment_override=source_inspection_environment_override
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(calls) == len(URLS)
    for index, (call, url) in enumerate(zip(calls, URLS, strict=True), start=1):
        args = cast(list[str], call["argv"])
        assert args[0] == "--disable"
        assert args[-1] == url
        for option, value in (
            ("--connect-timeout", "10"), ("--max-time", "20"),
            ("--max-filesize", "2097152"), ("--retry", "0"),
            ("--max-redirs", "0"), ("--proto", "=https"),
            ("--proto-redir", "=https"), ("--proxy", ""), ("--noproxy", "*"),
        ):
            assert args[args.index(option) + 1] == value
        assert "--location" in args
        assert "--fail-with-body" in args
        assert "--silent" in args and "--show-error" in args
        assert "--tlsv1.2" in args
        assert "--write-out" in args
        assert not any(arg in args for arg in ("--user", "--netrc", "-H", "--header", "-K", "--config"))
        environment = cast(dict[str, str], call["env"])
        for private in (
            "HOME", "GH_TOKEN", "GITHUB_TOKEN", "GH_HOST", "CURL_HOME",
            "CURL_CA_BUNDLE", "HTTPS_PROXY", "http_proxy", "ALL_PROXY", "URL",
        ):
            assert private not in environment
        assert environment["LC_ALL"] == "C"
        assert f"RAW_SOURCE_FIXTURE_BODY_{index}" in result.stdout
    for call, query in zip(calls[5:7], QUERIES, strict=True):
        args = cast(list[str], call["argv"])
        assert "--get" in args
        assert args.count("--data-urlencode") == 5
        for value in (query, "per_page=5", "page=1", "sort=created", "order=asc"):
            assert value in args
    assert result.stdout.count("BROWSER_SOURCE_BEGIN ") == 9
    assert result.stdout.count("BROWSER_SOURCE_END ") == 9
    assert result.stdout.count("native_status=0") == 9
    assert "BROWSER_SOURCE_INSPECTION completed=true requests=9" in result.stdout
    assert "fixture-private-value" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("source_inspection_failure_at", "source_inspection_failure_code"),
    [(1, 22), (4, 28), (8, 47)],
)
def test_browser_source_inspection_preserves_native_failure_and_prior_raw_sources(
    tmp_path: Path,
    source_inspection_failure_at: int,
    source_inspection_failure_code: int,
) -> None:
    result, calls, _ = _run_inspection(
        tmp_path, LIVE,
        fail_at=source_inspection_failure_at,
        failure_code=source_inspection_failure_code,
    )
    assert result.returncode == 2
    assert len(calls) == source_inspection_failure_at
    for index in range(1, source_inspection_failure_at + 1):
        assert f"RAW_SOURCE_FIXTURE_BODY_{index}" in result.stdout
    assert f"native_status={source_inspection_failure_code}" in result.stdout
    assert "fixture transport failure" in result.stderr
    assert f"Error {source_inspection_failure_code}" in result.stderr
    assert "completed=true" not in result.stdout


def test_browser_source_inspection_unknown_goal_is_a_native_refusal(tmp_path: Path) -> None:
    result, calls, _ = _run_inspection(tmp_path, PLAN + "-unreviewed-url")
    assert result.returncode == 2
    assert "No rule to make target" in result.stderr
    assert calls == []


def test_browser_source_inspection_playwright_revision_matches_the_native_lock() -> None:
    lock = tomllib.loads(
        (ROOT / "requirements/profiles/presentation-test/uv.lock").read_text(encoding="utf-8")
    )
    packages = cast(list[dict[str, object]], lock["package"])
    assert [package["version"] for package in packages if package["name"] == "playwright"] == ["1.63.0"]
    fragment = _inspection_fragment()
    for url in URLS[:2]:
        assert url in fragment
    assert "v1.63.0" in fragment



def _assert_research_next_action(output: str) -> str:
    guidance = [line for line in output.splitlines() if line.startswith("AGENT_NEXT ")]
    assert len(guidance) == 1
    for field in ("research_only=true", "gate_evidence=false", "release_ready=false"):
        assert field in guidance[0]
    return guidance[0]


@pytest.mark.parametrize("source_inspection_plan_environment_override", [False, True])
def test_browser_source_inspection_plan_prompts_for_explicit_registered_live_action(
    tmp_path: Path, source_inspection_plan_environment_override: bool
) -> None:
    result, calls, _ = _run_inspection(
        tmp_path, PLAN, environment_override=source_inspection_plan_environment_override
    )
    assert result.returncode == 0
    assert calls == []
    guidance = _assert_research_next_action(result.stdout)
    assert f"target={PLAN} outcome=plan" in guidance
    assert f"next_owner={LIVE}" in guidance
    assert "next_action=explicit-live-retrieval-after-owned-receiving" in guidance
    assert "issue_relevance=unverified" in guidance
    assert "apt_repair=unverified" in guidance


@pytest.mark.parametrize(
    "source_inspection_search_payload",
    [
        '{"total_count":0,"items":[]}',
        '{"total_count":1,"items":[{"title":"Unrelated synthetic fixture","body":"No mirror evidence"}]}',
    ],
)
def test_browser_source_inspection_complete_prompts_for_evidence_review_without_relevance_claim(
    tmp_path: Path, source_inspection_search_payload: str
) -> None:
    result, calls, _ = _run_inspection(
        tmp_path, LIVE, search_payload=source_inspection_search_payload
    )
    assert result.returncode == 0
    assert len(calls) == 9
    assert result.stdout.count(source_inspection_search_payload) == 2
    guidance = _assert_research_next_action(result.stdout)
    assert f"target={LIVE} outcome=observed" in guidance
    assert "next_owner=show-lines" in guidance
    assert "next_action=retain-and-review-raw-primary-source-and-issue-bodies" in guidance
    assert "issue_relevance=unverified" in guidance
    assert "apt_repair=unverified" in guidance


@pytest.mark.parametrize(
    (
        "source_inspection_guidance_failure_at",
        "source_inspection_guidance_failure_code",
        "source_inspection_guidance_failed_name",
        "source_inspection_guidance_changed_input",
    ),
    [
        (1, 22, "playwright-dependencies", "verified-pinned-source-or-public-HTTP-access"),
        (4, 28, "apt-sources-upstream", "verified-source-endpoint-or-network-availability"),
        (8, 47, "runner-image-apt-source", "reviewed-direct-HTTPS-source-without-redirect"),
    ],
)
def test_browser_source_inspection_failed_source_prompts_for_changed_input_without_retry(
    tmp_path: Path,
    source_inspection_guidance_failure_at: int,
    source_inspection_guidance_failure_code: int,
    source_inspection_guidance_failed_name: str,
    source_inspection_guidance_changed_input: str,
) -> None:
    result, calls, _ = _run_inspection(
        tmp_path,
        LIVE,
        fail_at=source_inspection_guidance_failure_at,
        failure_code=source_inspection_guidance_failure_code,
    )
    assert result.returncode == 2
    assert len(calls) == source_inspection_guidance_failure_at
    assert f"Error {source_inspection_guidance_failure_code}" in result.stderr
    guidance = _assert_research_next_action(result.stdout)
    assert f"target={LIVE} outcome=failed" in guidance
    assert f"failed_source={source_inspection_guidance_failed_name}" in guidance
    assert f"native_status={source_inspection_guidance_failure_code}" in guidance
    assert f"changed_input={source_inspection_guidance_changed_input}" in guidance
    assert "next_owner=show-lines" in guidance
    assert "next_action=inspect-raw-failure-and-repair-proven-input" in guidance
    assert "no_unchanged_retry=true" in guidance
    assert "preserve_prior_raw=true" in guidance
    for index in range(1, source_inspection_guidance_failure_at + 1):
        assert f"RAW_SOURCE_FIXTURE_BODY_{index}" in result.stdout
    assert "completed=true" not in result.stdout
