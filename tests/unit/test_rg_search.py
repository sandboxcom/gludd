"""Tests for ripgrep-backed search (RgSearch): argv, NDJSON parse, locator, fail-soft."""

from __future__ import annotations

import base64
import json
import logging
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import NoReturn

import pytest

from general_ludd.code_intelligence.rg_search import RgMatch, RgResult, RgSearch

_RG_SEARCH_LOGGER = "general_ludd.code_intelligence.rg_search"

# --- argv construction ---------------------------------------------------


def test_build_argv_uses_double_dash_and_json() -> None:
    argv = RgSearch.build_argv("/bin/rg", "needle", "src")
    assert argv[0] == "/bin/rg"
    assert "--json" in argv
    # -- must precede the query so a dash-query is never parsed as an option
    dd = argv.index("--")
    assert argv[dd + 1] == "needle"
    assert argv[dd + 2] == "src"


def test_build_argv_serializes_ripgrep_file_output_buffering() -> None:
    argv = RgSearch.build_argv("/bin/rg", "needle", "src")
    separator = argv.index("--")

    assert argv[separator - 2 : separator] == ["--threads", "1"]


def test_build_argv_dash_query_guarded_by_double_dash() -> None:
    argv = RgSearch.build_argv("/bin/rg", "-foo", ".")
    dd = argv.index("--")
    # the dash-query sits AFTER --, so rg treats it as a pattern, not a flag
    assert argv[dd + 1] == "-foo"
    assert "-foo" not in argv[:dd]


def test_build_argv_globs_types_flags() -> None:
    argv = RgSearch.build_argv(
        "/bin/rg",
        "q",
        "root",
        globs=["*.py", "!*_test.py"],
        types=["py", "rust"],
        flags=["-i", "--word-regexp"],
    )
    assert argv.count("-g") == 2
    assert "*.py" in argv and "!*_test.py" in argv
    assert argv.count("-t") == 2
    assert "py" in argv and "rust" in argv
    assert "-i" in argv and "--word-regexp" in argv
    # everything sits before the -- query separator
    dd = argv.index("--")
    for tok in ("-g", "-t", "-i", "--word-regexp"):
        assert argv.index(tok) < dd


# --- NDJSON parsing ------------------------------------------------------


def _match_event(path: str, line_number: int, text: str) -> str:
    return json.dumps(
        {
            "type": "match",
            "data": {
                "path": {"text": path},
                "lines": {"text": text},
                "line_number": line_number,
                "absolute_offset": 0,
                "submatches": [],
            },
        }
    )


def test_parse_match_events() -> None:
    stream = "\n".join(
        [
            json.dumps({"type": "begin", "data": {"path": {"text": "a.py"}}}),
            _match_event("a.py", 3, "def foo():\n"),
            _match_event("a.py", 9, "    foo()\n"),
            json.dumps({"type": "end", "data": {"path": {"text": "a.py"}}}),
        ]
    )
    matches = RgSearch.parse_json_stream(stream)
    assert matches == [
        RgMatch(file="a.py", line=3, text="def foo():"),
        RgMatch(file="a.py", line=9, text="    foo()"),
    ]


def test_parse_summary_event_ignored() -> None:
    summary = json.dumps(
        {"type": "summary", "data": {"stats": {"matched_lines": 0}, "elapsed_total": {}}}
    )
    assert RgSearch.parse_json_stream(summary) == []


def test_parse_base64_bytes_path_decoded() -> None:
    raw_path = b"weird/\xff/name.py"
    event = json.dumps(
        {
            "type": "match",
            "data": {
                "path": {"bytes": base64.b64encode(raw_path).decode("ascii")},
                "lines": {"text": "hit\n"},
                "line_number": 1,
            },
        }
    )
    matches = RgSearch.parse_json_stream(event)
    assert len(matches) == 1
    # the 0xff byte is replaced but the surrounding path survives the decode
    assert matches[0].file.startswith("weird/")
    assert matches[0].file.endswith("/name.py")
    assert matches[0].line == 1
    assert matches[0].text == "hit"


def test_parse_non_json_lines_ignored() -> None:
    stream = "\n".join(
        [
            "this is not json",
            _match_event("a.py", 1, "x\n"),
            "{ broken",
            "",
        ]
    )
    matches = RgSearch.parse_json_stream(stream)
    assert matches == [RgMatch(file="a.py", line=1, text="x")]


# --- locator (bundled > PATH) -------------------------------------------


def test_locate_prefers_bundled(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BB:
        def get_bundled_binary_path(self, name: str) -> str:
            assert name == "rg"
            return "/dist/binaries/rg"

    monkeypatch.setattr(
        "general_ludd.filestore.bootstrap.BinaryBootstrapper", lambda *a, **k: _BB()
    )
    monkeypatch.setattr(
        "general_ludd.code_intelligence.rg_search.Path.is_file", lambda self: True
    )
    monkeypatch.setattr(
        "general_ludd.code_intelligence.rg_search.shutil.which",
        lambda name: "/usr/bin/rg",
    )
    assert RgSearch.locate_rg() == "/dist/binaries/rg"


def test_locate_falls_back_to_path(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BB:
        def get_bundled_binary_path(self, name: str) -> None:
            return None

    monkeypatch.setattr(
        "general_ludd.filestore.bootstrap.BinaryBootstrapper", lambda *a, **k: _BB()
    )
    monkeypatch.setattr(
        "general_ludd.code_intelligence.rg_search.shutil.which",
        lambda name: "/usr/bin/rg",
    )
    assert RgSearch.locate_rg() == "/usr/bin/rg"


# --- fail-soft -----------------------------------------------------------


def test_search_missing_rg_is_fail_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(RgSearch, "_resolve_rg", lambda self: None)
    result = RgSearch().search("q")
    assert isinstance(result, RgResult)
    assert result.available is False
    assert result.matches == []
    assert result.error


def test_search_exit1_is_no_match_not_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(RgSearch, "_resolve_rg", lambda self: "/bin/rg")

    def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = RgSearch().search("q")
    assert result.available is True
    assert result.matches == []
    assert result.error is None
    assert result.returncode == 1


def test_search_exit2_surfaces_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(RgSearch, "_resolve_rg", lambda self: "/bin/rg")

    def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=2, stdout="", stderr="regex parse error\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = RgSearch().search("(")
    assert result.available is True
    assert result.matches == []
    assert result.error == "regex parse error"
    assert result.returncode == 2


def test_search_timeout_is_fail_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(RgSearch, "_resolve_rg", lambda self: "/bin/rg")

    def fake_run(*args: object, **kwargs: object) -> NoReturn:
        raise subprocess.TimeoutExpired(cmd="rg", timeout=1.0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = RgSearch().search("q")
    assert result.available is False
    assert "timed out" in (result.error or "")


def test_search_oserror_is_fail_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(RgSearch, "_resolve_rg", lambda self: "/bin/rg")

    def fake_run(*args: object, **kwargs: object) -> NoReturn:
        raise OSError("exec format error")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = RgSearch().search("q")
    assert result.available is False
    assert "exec format error" in (result.error or "")


def test_search_exit0_parses_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(RgSearch, "_resolve_rg", lambda self: "/bin/rg")
    stdout = _match_event("a.py", 5, "match here\n")

    def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = RgSearch().search("match")
    assert result.available is True
    assert result.matches == [RgMatch(file="a.py", line=5, text="match here")]
    assert result.returncode == 0


@pytest.mark.parametrize("rc", [0, 1])
def test_search_clean_runs_are_available(
    monkeypatch: pytest.MonkeyPatch, rc: int
) -> None:
    monkeypatch.setattr(RgSearch, "_resolve_rg", lambda self: "/bin/rg")

    def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=rc, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert RgSearch().search("q").available is True


def test_search_passes_resolved_root_to_rg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    root = workspace / "src"
    root.mkdir(parents=True)
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(RgSearch, "_resolve_rg", lambda self: "/bin/rg")
    seen: dict[str, list[str]] = {}

    def fake_run(
        argv: list[str], *args: object, **kwargs: object
    ) -> SimpleNamespace:
        seen["argv"] = argv
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = RgSearch().search("q", root="src")

    assert result.available is True
    assert seen["argv"][-1] == str(root.resolve())


def test_search_refuses_missing_root_before_invoking_rg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(RgSearch, "_resolve_rg", lambda self: "/bin/rg")

    def fake_run(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("rg must not run for a missing root")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = RgSearch().search("q", root="missing")

    assert result.available is False
    assert result.matches == []
    assert "not a directory" in (result.error or "")


def test_search_refuses_symlink_escape_before_invoking_rg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    escape = workspace / "escape"
    escape.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(RgSearch, "_resolve_rg", lambda self: "/bin/rg")

    def fake_run(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("rg must not run for a symlink escape")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = RgSearch(allowed_roots=[str(workspace)]).search("q", root=str(escape))

    assert result.available is False
    assert result.matches == []
    assert "outside allowed directories" in (result.error or "")


# --- flag-injection allowlist + signal-kill return code ------------------


def test_build_argv_drops_disallowed_flags(caplog: pytest.LogCaptureFixture) -> None:
    """Only allowlisted boolean flags survive; injected flags are dropped."""
    # Defensive reset, matching the fix applied to the other caplog-propagation
    # failures in this shard (see f7638e73): a sibling test running earlier on
    # the same xdist worker can leave this module's logger (or an ancestor) at
    # propagate=False / a suppressive level, which silently empties
    # `caplog.messages` for every test that runs after it without ever
    # touching this file. Pin the target logger explicitly so the assertion
    # below is deterministic regardless of what another test left behind.
    logging.getLogger(_RG_SEARCH_LOGGER).propagate = True
    with caplog.at_level(logging.WARNING, logger=_RG_SEARCH_LOGGER):
        argv = RgSearch.build_argv(
            "/bin/rg", "q", ".", flags=["-i", "--passthru", "--pre", "cat"]
        )
    # allowlisted flag survives
    assert "-i" in argv
    # injected rg flags (and a --pre value) are dropped before the -- separator
    assert "--passthru" not in argv
    assert "--pre" not in argv
    assert "cat" not in argv
    dd = argv.index("--")
    assert argv[dd + 1] == "q"
    assert any("--passthru" in m for m in caplog.messages)


def test_search_negative_returncode_is_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A signal-killed rg (negative rc) must surface an error, not parse empty."""
    monkeypatch.setattr(RgSearch, "_resolve_rg", lambda self: "/bin/rg")

    def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=-9, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = RgSearch().search("q")
    assert result.available is True
    assert result.matches == []
    assert result.error  # surfaced, not silently swallowed
    assert result.returncode == -9
