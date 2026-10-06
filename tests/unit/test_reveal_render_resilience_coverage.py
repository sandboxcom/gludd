"""Deterministic branch coverage for the presentation builder and local server."""

from __future__ import annotations

import json
import subprocess
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from scripts import build_deck


def _deck_data() -> dict[str, object]:
    return {
        "version": "0.1.2",
        "git_sha": "a" * 7,
        "git_sha_full": "a" * 40,
        "test_count": 12,
        "role_count": 3,
        "features": [],
        "generated_at": "2026-10-06T00:00:00Z",
    }


def test_live_data_helpers_parse_success_and_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Deck evidence readers expose grounded values and safe fallbacks."""
    readme = tmp_path / "README.md"
    readme.write_text(
        "before\n<!-- STATUS-TABLE:START -->\n"
        "| Feature | Status | Evidence |\n|---|---|---|\n"
        "| Mermaid | 100% | browser |\n<!-- STATUS-TABLE:END -->\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(build_deck, "README", readme)
    assert build_deck.parse_readme_status_table() == [
        {"name": "Mermaid", "pct": "100%", "evidence": "browser"}
    ]

    responses = iter(
        (
            subprocess.CompletedProcess([], 0, "noise\n123 tests collected\n", ""),
            subprocess.CompletedProcess([], 0, "no count\n", ""),
            subprocess.CompletedProcess([], 0, "deadbee\n", ""),
            subprocess.CompletedProcess([], 0, "role-a\n\nrole-b\n", ""),
        )
    )
    monkeypatch.setattr(build_deck.subprocess, "run", lambda *args, **kwargs: next(responses))
    assert build_deck.get_test_count() == 123
    assert build_deck.get_test_count() == 0
    assert build_deck.get_git_sha() == "deadbee"
    assert build_deck.count_roles() == 2

    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(build_deck, "ROOT", root)
    assert build_deck.get_version() == "unknown"
    (root / "pyproject.toml").write_text('[project]\nversion = "9.8.7"\n', encoding="utf-8")
    assert build_deck.get_version() == "9.8.7"
    audit = root / "audit.json"
    monkeypatch.setattr(build_deck, "GAME_AUDIT", audit)
    assert build_deck.get_game_audit() is None
    audit.write_text('{"status": "pass"}\n', encoding="utf-8")
    assert build_deck.get_game_audit() == {"status": "pass"}


def test_build_deck_data_composes_all_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    """The data contract includes the full immutable SHA and optional audit."""
    monkeypatch.setattr(build_deck, "get_version", lambda: "1.2.3")
    monkeypatch.setattr(build_deck, "get_git_sha", lambda *, short=True: "short" if short else "f" * 40)
    monkeypatch.setattr(build_deck, "get_test_count", lambda: 42)
    monkeypatch.setattr(build_deck, "count_roles", lambda: 7)
    monkeypatch.setattr(build_deck, "parse_readme_status_table", lambda: [{"name": "x"}])
    monkeypatch.setattr(build_deck, "get_game_audit", lambda: {"status": "pass"})
    monkeypatch.setattr(
        build_deck.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0, "2026-10-06T00:00:00Z\n", ""),
    )

    data = build_deck.build_deck_data()

    assert data == {
        "version": "1.2.3",
        "git_sha": "short",
        "git_sha_full": "f" * 40,
        "test_count": 42,
        "role_count": 7,
        "features": [{"name": "x"}],
        "generated_at": "2026-10-06T00:00:00Z",
        "game_audit": {"status": "pass"},
    }


def test_citation_parser_preserves_markup_and_rejects_bad_ranges(tmp_path: Path) -> None:
    """Citation conversion is idempotent, bounded, and markup-safe."""
    root = tmp_path / "repo"
    source = root / "src" / "pkg" / "demo.py"
    source.parent.mkdir(parents=True)
    source.write_text("one\ntwo\n", encoding="utf-8")
    html = (
        "<!doctype html><!--keep--><p>src/pkg/demo.py:1 &amp; "
        "<script>src/pkg/demo.py:2</script><img src='x'/></p>"
    )
    linked, citations = build_deck.link_source_citations(html, "b" * 40, root=root)
    assert "<!doctype html>" in linked
    assert "<!--keep-->" in linked
    assert "&amp;" in linked
    assert "<script>src/pkg/demo.py:2</script>" in linked
    assert citations == {"src/pkg/demo.py"}
    assert build_deck.link_source_citations("src/pkg/missing.py", "b" * 40, root=root)[1] == set()
    with pytest.raises(ValueError, match="40 lowercase"):
        build_deck.link_source_citations("src/pkg/demo.py", "mutable", root=root)
    for invalid in ("src/pkg/demo.py:2-1", "src/pkg/demo.py:1-3"):
        with pytest.raises(ValueError, match="outside file bounds"):
            build_deck.link_source_citations(invalid, "b" * 40, root=root)


@pytest.mark.parametrize("raw", ("bad%zz", "bad%00name", r"src\demo.py", ""))
def test_source_resolver_rejects_ambiguous_paths(tmp_path: Path, raw: str) -> None:
    """Residual escapes, NUL, backslash, and empty/dot paths are invalid."""
    with pytest.raises(build_deck.SourceRequestError, match="invalid-path"):
        build_deck.resolve_source_request(raw, repo_root=tmp_path, allowlist={raw})


def test_source_resolver_rejects_repository_directory(tmp_path: Path) -> None:
    """The repository root is valid syntax but is never served as a file."""
    with pytest.raises(build_deck.SourceRequestError, match="not-a-file"):
        build_deck.resolve_source_request(".", repo_root=tmp_path, allowlist={"."})


def test_source_resolver_rejects_allowlisted_directory_and_missing_file(tmp_path: Path) -> None:
    """An allowlist does not turn directories or vanished files into source."""
    folder = tmp_path / "src"
    folder.mkdir()
    with pytest.raises(build_deck.SourceRequestError, match="not-a-file"):
        build_deck.resolve_source_request("src", repo_root=tmp_path, allowlist={"src"})
    with pytest.raises(build_deck.SourceRequestError, match="not-found"):
        build_deck.resolve_source_request("gone.py", repo_root=tmp_path, allowlist={"gone.py"})


def test_preview_copy_resolves_tokens_and_writes_exact_allowlist(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Local preview copies assets, links citations, and leaves its source intact."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "index.html").write_text(
        "<p>{{VERSION}} README.md:1</p>",
        encoding="utf-8",
    )
    monkeypatch.setattr(build_deck, "DECK_DIR", source)
    monkeypatch.setattr(build_deck, "validate_assets", lambda _path: None)
    preview = tmp_path / "preview"
    preview.mkdir()
    (preview / "old.txt").write_text("old", encoding="utf-8")

    result = build_deck.build_preview_copy(preview, data=_deck_data())

    assert result == preview
    html = (preview / "index.html").read_text(encoding="utf-8")
    assert "0.1.2" in html
    assert f"blob/{'a' * 40}/README.md#L1" in html
    payload = json.loads((preview / build_deck.SOURCE_ALLOWLIST).read_text(encoding="utf-8"))
    assert payload["paths"] == ["README.md"]


@pytest.mark.parametrize(
    "payload",
    (
        "not-json",
        '{"schema":"wrong","paths":[]}',
        '{"schema":"gludd-source-allowlist/v1","paths":"bad"}',
    ),
)
def test_source_allowlist_loader_rejects_invalid_contract(tmp_path: Path, payload: str) -> None:
    """The local endpoint fails closed when the generated contract is invalid."""
    (tmp_path / build_deck.SOURCE_ALLOWLIST).write_text(payload, encoding="utf-8")
    with pytest.raises(RuntimeError, match="missing or invalid"):
        build_deck._load_source_allowlist(tmp_path)


def test_source_handler_serves_prefixed_assets_and_bounded_source(tmp_path: Path) -> None:
    """The loopback HTTP boundary serves the Pages prefix and safe text only."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("ready", encoding="utf-8")
    handler = build_deck.source_request_handler(
        serve_dir=site,
        repo_root=repo,
        allowlist=frozenset({"README.md"}),
        url_prefix="/gludd/",
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/gludd/", timeout=2) as response:
            assert response.read() == b"ready"
        with urllib.request.urlopen(
            f"http://{host}:{port}/__gludd_source__?path=README.md",
            timeout=2,
        ) as response:
            assert response.read() == b"hello\n"
            assert response.headers["X-Content-Type-Options"] == "nosniff"
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(
                f"http://{host}:{port}/__gludd_source__?path=missing.py",
                timeout=2,
            )
        assert error.value.code == 404
        error.value.close()
        with pytest.raises(urllib.error.HTTPError) as malformed:
            urllib.request.urlopen(f"http://{host}:{port}/__gludd_source__", timeout=2)
        assert malformed.value.code == 400
        malformed.value.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_source_handler_keeps_hash_navigation_revalidation_on_a_2xx_response(
    tmp_path: Path,
) -> None:
    """WebKit's conditional top-level navigation must not surface a 304 response."""
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("ready", encoding="utf-8")
    handler = build_deck.source_request_handler(
        serve_dir=site,
        repo_root=tmp_path,
        allowlist=frozenset(),
        url_prefix="/gludd/",
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    url = f"http://{host}:{port}/gludd/"
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            modified = response.headers["Last-Modified"]
            cache_control = response.headers["Cache-Control"]
        request = urllib.request.Request(url, headers={"If-Modified-Since": modified})
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                status = response.status
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
        assert cache_control == "no-store"
        assert status == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_serve_deck_binds_loopback_and_stops_cleanly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The preview server uses the allowlist and treats Ctrl-C as a clean stop."""
    (tmp_path / build_deck.SOURCE_ALLOWLIST).write_text(
        '{"schema":"gludd-source-allowlist/v1","paths":[]}',
        encoding="utf-8",
    )
    observed: dict[str, object] = {}

    class FakeServer:
        def __init__(self, address: tuple[str, int], handler: object) -> None:
            observed["address"] = address
            observed["handler"] = handler

        def __enter__(self) -> FakeServer:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def serve_forever(self) -> None:
            raise KeyboardInterrupt

    monkeypatch.setattr(build_deck.http.server, "ThreadingHTTPServer", FakeServer)
    build_deck.serve_deck(8123, serve_dir=tmp_path, url_prefix="gludd")
    assert observed["address"] == ("127.0.0.1", 8123)


def test_honesty_check_reports_every_banned_token() -> None:
    """Marketing lint returns stable, actionable findings."""
    violations = build_deck.honesty_check("production-ready and blazing")
    assert violations == [
        "Banned marketing token: 'production-ready'",
        "Banned marketing token: 'blazing'",
    ]
    assert build_deck.honesty_check("plain evidence") == []


def test_main_preview_data_and_build_modes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """All non-server CLI modes preserve their documented side effects."""
    calls: list[str] = []
    monkeypatch.setattr(build_deck, "build_preview_copy", lambda: calls.append("preview"))
    monkeypatch.setattr(build_deck, "build_deck_data", _deck_data)
    monkeypatch.setattr(build_deck, "validate_assets", lambda _path: calls.append("validate"))

    monkeypatch.setattr(build_deck.sys, "argv", ["build_deck.py", "--preview"])
    build_deck.main()
    assert calls == ["preview"]

    monkeypatch.setattr(build_deck.sys, "argv", ["build_deck.py", "--data"])
    build_deck.main()

    root = tmp_path / "repo"
    deck = root / "docs" / "presentation" / "deck"
    deck.mkdir(parents=True)
    index = deck / "index.html"
    index.write_text("{{VERSION}} {{GIT_SHA_FULL}}", encoding="utf-8")
    monkeypatch.setattr(build_deck, "ROOT", root)
    monkeypatch.setattr(build_deck, "DECK_DIR", deck)
    monkeypatch.setattr(build_deck, "DECK_FILE", index)
    monkeypatch.setattr(build_deck.sys, "argv", ["build_deck.py", "--build"])
    build_deck.main()
    assert "0.1.2" in index.read_text(encoding="utf-8")
    assert (deck / build_deck.SOURCE_ALLOWLIST).is_file()
    assert (root / "docs" / "presentation" / "deck-data.json").is_file()


def test_main_check_failure_and_serve_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Honesty failures exit nonzero while serve delegates to the safe server."""
    root = tmp_path / "repo"
    deck = root / "docs" / "presentation" / "deck"
    deck.mkdir(parents=True)
    index = deck / "index.html"
    index.write_text("production-ready", encoding="utf-8")
    monkeypatch.setattr(build_deck, "ROOT", root)
    monkeypatch.setattr(build_deck, "DECK_DIR", deck)
    monkeypatch.setattr(build_deck, "DECK_FILE", index)
    monkeypatch.setattr(build_deck, "validate_assets", lambda _path: None)
    monkeypatch.setattr(build_deck, "build_deck_data", _deck_data)
    monkeypatch.setattr(build_deck.sys, "argv", ["build_deck.py", "--check"])
    with pytest.raises(SystemExit) as failure:
        build_deck.main()
    assert failure.value.code == 1

    index.write_text("evidence", encoding="utf-8")
    preview = tmp_path / "preview"
    served: list[tuple[int, Path, str]] = []
    monkeypatch.setattr(build_deck, "build_preview_copy", lambda: preview)
    monkeypatch.setattr(
        build_deck,
        "serve_deck",
        lambda port, serve_dir, url_prefix="": served.append((port, serve_dir, url_prefix)),
    )
    monkeypatch.setattr(
        build_deck.sys,
        "argv",
        ["build_deck.py", "--serve", "--port", "8124", "--url-prefix", "/gludd/"],
    )
    build_deck.main()
    assert served == [(8124, preview, "/gludd/")]


def test_main_reports_missing_deck(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A missing authored deck fails before writing derived data."""
    root = tmp_path / "repo"
    (root / "docs" / "presentation" / "deck").mkdir(parents=True)
    monkeypatch.setattr(build_deck, "ROOT", root)
    monkeypatch.setattr(build_deck, "DECK_DIR", root / "docs" / "presentation" / "deck")
    monkeypatch.setattr(build_deck, "DECK_FILE", root / "missing.html")
    monkeypatch.setattr(build_deck, "validate_assets", lambda _path: None)
    monkeypatch.setattr(build_deck, "build_deck_data", _deck_data)
    monkeypatch.setattr(build_deck.sys, "argv", ["build_deck.py"])
    with pytest.raises(SystemExit) as failure:
        build_deck.main()
    assert failure.value.code == 1
