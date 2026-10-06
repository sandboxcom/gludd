#!/usr/bin/env python3
"""
build_deck.py — Generate the gludd reveal.js deck from live project data.

Reads README.md, .game-audit-report.json, and other live artifacts to produce
docs/presentation/deck/index.html. Missing data renders as honest "NO DATA"
placeholders rather than fabricated numbers.

Usage:
    python3 scripts/build_deck.py            # generate deck
    python3 scripts/build_deck.py --serve    # generate + start local server
    python3 scripts/build_deck.py --check    # verify honesty (banned words, % match)
"""

import argparse
import http.server
import json
import re
import shutil
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import ClassVar
from urllib.parse import parse_qs, unquote, urlsplit

if __package__:
    from scripts.vendor_presentation_assets import validate_assets
else:
    from vendor_presentation_assets import validate_assets

ROOT = Path(__file__).resolve().parent.parent
DECK_DIR = ROOT / "docs" / "presentation" / "deck"
DECK_FILE = DECK_DIR / "index.html"
README = ROOT / "README.md"
GAME_AUDIT = ROOT / ".game-audit-report.json"
TEST_COUNT_CACHE = ROOT / ".test-count-cache.txt"

# Scratch dir used by `make deck-serve` / `--serve` to preview a token-resolved
# copy of the deck WITHOUT mutating the tracked template in docs/presentation/deck/.
# The tracked index.html stays a literal {{TOKEN}} template in git; only this
# throwaway copy ever gets rewritten with live values.
PREVIEW_DIR = Path("/tmp/gludd-deck-preview")
SOURCE_ALLOWLIST = "source-allowlist.json"
SOURCE_MAX_BYTES = 2 * 1024 * 1024
GITHUB_REPOSITORY = "https://github.com/sandboxcom/gludd"

_SOURCE_TOKEN = re.compile(
    r"(?P<path>(?:\.github|collections|config|docs|infra|molecule|scripts|src|tests)/"
    r"[A-Za-z0-9_.@/+\-]+|README\.md|TASKS\.md|BUGS\.md|Makefile|pyproject\.toml)"
    r"(?::(?P<start>[1-9][0-9]*)(?:-(?P<end>[1-9][0-9]*))?)?"
)

BANNED_MARKETING = [
    "production-ready", "blazing", "seamless", "enterprise-grade",
    "revolutionary", "effortless", "best-in-class", "world-class",
    "game-changing", "next-gen",
]


def parse_readme_status_table() -> list[dict]:
    """Parse the README status table rows into feature dicts."""
    text = README.read_text()
    features = []
    in_table = False
    for line in text.splitlines():
        if "STATUS-TABLE:START" in line:
            in_table = True
            continue
        if "STATUS-TABLE:END" in line:
            break
        if not in_table:
            continue
        if line.startswith("| ") and "Feature" not in line and "---" not in line:
            parts = [p.strip() for p in line.split("|")[1:-1]]
            if len(parts) >= 3:
                features.append({
                    "name": parts[0],
                    "pct": parts[1],
                    "evidence": parts[2] if len(parts) > 2 else "",
                })
    return features


def get_test_count() -> int:
    """Get the collected test count."""
    result = subprocess.run(
        ["make", "test-count"],
        capture_output=True, text=True,
        cwd=str(ROOT),
    )
    for line in result.stdout.splitlines():
        m = re.search(r"(\d+)\s+tests?\s+collected", line)
        if m:
            return int(m.group(1))
    return 0


def get_git_sha(*, short: bool = True) -> str:
    """Get the current HEAD SHA in display or immutable-link form."""
    result = subprocess.run(
        ["git", "rev-parse", "--short=7" if short else "--verify", "HEAD"],
        capture_output=True, text=True,
        cwd=str(ROOT),
    )
    return result.stdout.strip()


def get_version() -> str:
    """Get project version from pyproject.toml."""
    ppt = ROOT / "pyproject.toml"
    if ppt.exists():
        text = ppt.read_text()
        m = re.search(r'version\s*=\s*"([^"]+)"', text)
        if m:
            return m.group(1)
    return "unknown"


def get_game_audit() -> dict | None:
    """Load game audit report if it exists."""
    if GAME_AUDIT.exists():
        return json.loads(GAME_AUDIT.read_text())
    return None


def count_roles() -> int:
    """Count Ansible roles."""
    result = subprocess.run(
        ["make", "collection-roles"],
        capture_output=True, text=True,
        cwd=str(ROOT),
    )
    return len([line for line in result.stdout.strip().splitlines() if line.strip()])


def build_deck_data() -> dict:
    """Collect all live data for the deck."""
    data = {
        "version": get_version(),
        "git_sha": get_git_sha(),
        "git_sha_full": get_git_sha(short=False),
        "test_count": get_test_count(),
        "role_count": count_roles(),
        "features": parse_readme_status_table(),
        "generated_at": subprocess.run(
            ["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"],
            capture_output=True, text=True,
        ).stdout.strip(),
    }

    game_audit = get_game_audit()
    if game_audit:
        data["game_audit"] = game_audit

    return data


def apply_tokens(html: str, data: dict) -> tuple[str, list[str]]:
    """Replace {{TOKEN}} placeholders in HTML with live data values.

    Returns the rewritten HTML and a list of tokens that were not found in
    the template (so the caller can warn about stale tokens).
    """
    test_count = data.get("test_count", 0)
    role_count = data.get("role_count", 0)
    replacements = {
        "{{VERSION}}": str(data.get("version", "unknown")),
        "{{TEST_COUNT}}": f"{test_count:,}",
        "{{ROLE_COUNT}}": str(role_count),
        "{{GIT_SHA}}": str(data.get("git_sha", "unknown")),
        "{{GIT_SHA_FULL}}": str(data.get("git_sha_full", "unknown")),
        "{{GENERATED_AT}}": str(data.get("generated_at", "")),
    }
    missing = [tok for tok in replacements if tok not in html]
    for token, value in replacements.items():
        html = html.replace(token, value)
    return html, missing


class SourceRequestError(ValueError):
    """A content-free rejection raised by the loopback source boundary."""

    def __init__(self, category: str, status: int = 404) -> None:
        super().__init__(category)
        self.category = category
        self.status = status


def _decode_source_path(raw_path: str) -> str:
    """Decode at most two URL layers and reject ambiguous residual escapes."""
    value = raw_path
    for _ in range(2):
        decoded = unquote(value)
        if decoded == value:
            break
        value = decoded
    if "%" in value or "\x00" in value or "\\" in value:
        raise SourceRequestError("invalid-path", 400)
    return value


def resolve_source_request(
    raw_path: str,
    *,
    repo_root: Path = ROOT,
    allowlist: set[str] | frozenset[str],
    max_bytes: int = SOURCE_MAX_BYTES,
) -> Path:
    """Resolve one bounded allowlisted UTF-8 file without leaking host paths."""
    decoded = _decode_source_path(raw_path)
    relative = Path(decoded)
    if not decoded or relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise SourceRequestError("invalid-path", 400)
    canonical = relative.as_posix()
    if canonical not in allowlist:
        raise SourceRequestError("not-allowlisted")
    root = repo_root.resolve()
    try:
        candidate = (root / relative).resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise SourceRequestError("not-found") from exc
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise SourceRequestError("outside-root", 403) from exc
    if not candidate.is_file():
        raise SourceRequestError("not-a-file")
    try:
        size = candidate.stat().st_size
    except OSError as exc:
        raise SourceRequestError("not-readable") from exc
    if size > max_bytes:
        raise SourceRequestError("too-large", 413)
    try:
        payload = candidate.read_bytes()
        if b"\x00" in payload:
            raise UnicodeError("NUL byte")
        payload.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise SourceRequestError("not-utf8", 415) from exc
    return candidate


def _line_count(path: Path) -> int:
    """Return a text file's logical line count."""
    text = path.read_text(encoding="utf-8")
    return len(text.splitlines())


def _link_source_text(
    value: str,
    *,
    sha_full: str,
    root: Path,
    citations: set[str],
) -> str:
    """Replace valid repository tokens in one citation text node."""

    def replace(match: re.Match[str]) -> str:
        relative = match.group("path").rstrip(".,;)")
        candidate = root / relative
        is_directory_hint = relative.endswith("/")
        if not candidate.exists():
            return match.group(0)
        start_text = match.group("start")
        end_text = match.group("end")
        if candidate.is_dir() or is_directory_hint:
            clean = relative.rstrip("/")
            href = f"{GITHUB_REPOSITORY}/tree/{sha_full}/{clean}"
            return f'<a class="source-link" href="{href}" data-source-path="{clean}/">{match.group(0)}</a>'
        if not candidate.is_file():
            return match.group(0)
        line_suffix = ""
        line_attr = ""
        if start_text:
            start = int(start_text)
            end = int(end_text or start_text)
            if end < start or end > _line_count(candidate):
                raise ValueError(f"source citation outside file bounds: {relative}:{start}-{end}")
            canonical_lines = f"{start}-{end}"
            line_suffix = f"#L{start}" if start == end else f"#L{start}-L{end}"
            line_attr = f' data-source-lines="{canonical_lines}"'
        citations.add(relative)
        href = f"{GITHUB_REPOSITORY}/blob/{sha_full}/{relative}{line_suffix}"
        label = match.group(0)
        return (
            f'<a class="source-link" href="{href}" data-source-path="{relative}"'
            f'{line_attr}>{label}</a>'
        )

    return _SOURCE_TOKEN.sub(replace, value)


class _CitationLinker(HTMLParser):
    """Preserve authored HTML while linking text inside typed citation nodes."""

    _VOID_TAGS: ClassVar[frozenset[str]] = frozenset({
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr",
    })
    _SKIP_TAGS: ClassVar[frozenset[str]] = frozenset({"pre", "script", "style"})

    def __init__(self, sha_full: str, root: Path) -> None:
        super().__init__(convert_charrefs=False)
        self.sha_full = sha_full
        self.root = root
        self.parts: list[str] = []
        self.citations: set[str] = set()
        self._skip_depth = 0
        self._anchor_depth = 0
        self._stack: list[tuple[bool, bool]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = set()
        for name, value in attrs:
            if name == "class" and value:
                classes.update(value.split())
        enters_skip = tag in self._SKIP_TAGS or "mermaid" in classes
        enters_anchor = tag == "a"
        if tag in self._VOID_TAGS:
            self.parts.append(self.get_starttag_text())
            return
        self._stack.append((enters_skip, enters_anchor))
        self._skip_depth += int(enters_skip)
        self._anchor_depth += int(enters_anchor)
        self.parts.append(self.get_starttag_text())

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del tag, attrs
        self.parts.append(self.get_starttag_text())

    def handle_endtag(self, tag: str) -> None:
        self.parts.append(f"</{tag}>")
        if self._stack:
            leaves_skip, leaves_anchor = self._stack.pop()
            self._skip_depth -= int(leaves_skip)
            self._anchor_depth -= int(leaves_anchor)

    def handle_data(self, data: str) -> None:
        if not self._skip_depth and not self._anchor_depth:
            data = _link_source_text(
                data,
                sha_full=self.sha_full,
                root=self.root,
                citations=self.citations,
            )
        self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self.parts.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        self.parts.append(f"<!{decl}>")

    def handle_pi(self, data: str) -> None:
        self.parts.append(f"<?{data}>")


def link_source_citations(html_text: str, sha_full: str, *, root: Path = ROOT) -> tuple[str, set[str]]:
    """Link typed citation text to immutable GitHub destinations."""
    if not re.fullmatch(r"[0-9a-f]{40}", sha_full):
        raise ValueError("git SHA for source links must be 40 lowercase hex characters")
    parser = _CitationLinker(sha_full, root)
    parser.feed(html_text)
    parser.close()
    return "".join(parser.parts), parser.citations


def honesty_check(html: str) -> list[str]:
    """Check deck HTML for honesty violations. Returns list of violations."""
    violations = []

    for token in BANNED_MARKETING:
        if token.lower() in html.lower():
            violations.append(f"Banned marketing token: '{token}'")

    return violations


def build_preview_copy(preview_dir: Path = PREVIEW_DIR, *, data: dict | None = None) -> Path:
    """Build a token-resolved copy of the deck in a scratch dir for local preview.

    Copies the entire tracked deck dir (index.html + assets) to `preview_dir`,
    then applies {{TOKEN}} substitution to ONLY the copied index.html. The
    tracked docs/presentation/deck/ is never written to, so `make deck-serve`
    can show real numbers without dirtying the template that CI's
    `make deck-build` resolves at publish time.

    Returns the path to the scratch dir (ready to be served as-is).
    """
    validate_assets(DECK_DIR / "vendor")
    if preview_dir.exists():
        shutil.rmtree(preview_dir)
    shutil.copytree(DECK_DIR, preview_dir)

    data = data or build_deck_data()
    preview_index = preview_dir / "index.html"
    html_text = preview_index.read_text()
    html_text, missing = apply_tokens(html_text, data)
    html_text, citations = link_source_citations(
        html_text,
        str(data["git_sha_full"]),
    )
    preview_index.write_text(html_text)
    (preview_dir / SOURCE_ALLOWLIST).write_text(
        json.dumps({"schema": "gludd-source-allowlist/v1", "paths": sorted(citations)}, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"Preview copy built: {preview_dir} (tracked template untouched)")
    if missing:
        print(f"  WARNING: tokens not found in template: {', '.join(missing)}")
    return preview_dir


def _load_source_allowlist(serve_dir: Path) -> frozenset[str]:
    """Load the generated exact source allowlist for the loopback viewer."""
    path = serve_dir / SOURCE_ALLOWLIST
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema") != "gludd-source-allowlist/v1":
            raise ValueError("unexpected source allowlist schema")
        values = payload["paths"]
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise ValueError("invalid source allowlist paths")
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise RuntimeError("source allowlist is missing or invalid") from exc
    return frozenset(values)


def source_request_handler(
    *,
    serve_dir: Path,
    repo_root: Path,
    allowlist: frozenset[str],
    url_prefix: str = "",
) -> type[http.server.SimpleHTTPRequestHandler]:
    """Create a static handler with one fail-closed loopback source endpoint."""
    normalized_prefix = "/" + url_prefix.strip("/") if url_prefix.strip("/") else ""

    class SourceRequestHandler(http.server.SimpleHTTPRequestHandler):
        """Serve presentation assets and bounded authored repository sources."""

        server_version = "GluddDeck/1"

        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, directory=str(serve_dir), **kwargs)

        def do_GET(self) -> None:
            request = urlsplit(self.path)
            if request.path == "/__gludd_source__":
                self._serve_source(request.query)
                return
            static_path = request.path
            if normalized_prefix and (
                request.path == normalized_prefix or request.path.startswith(normalized_prefix + "/")
            ):
                suffix = request.path[len(normalized_prefix):] or "/"
                self.path = suffix + (("?" + request.query) if request.query else "")
                static_path = suffix
            self._no_store_document = static_path in {"", "/", "/index.html"}
            if self._no_store_document:
                for header in ("If-Modified-Since", "If-None-Match"):
                    if header in self.headers:
                        del self.headers[header]
            super().do_GET()

        def end_headers(self) -> None:
            if getattr(self, "_no_store_document", False):
                self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def _serve_source(self, query: str) -> None:
            values = parse_qs(query, keep_blank_values=True)
            if set(values) != {"path"} or len(values["path"]) != 1:
                self.send_error(400, "invalid source request")
                return
            try:
                source = resolve_source_request(
                    values["path"][0],
                    repo_root=repo_root,
                    allowlist=allowlist,
                )
                payload = source.read_bytes()
            except SourceRequestError as exc:
                self.send_error(exc.status, "source unavailable")
                return
            except OSError:
                self.send_error(404, "source unavailable")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            """Log without query strings or filesystem details."""
            safe_request = urlsplit(self.path).path
            sys.stderr.write(f"deck-server {self.client_address[0]} {safe_request} {args[-1] if args else '-'}\n")

    return SourceRequestHandler


class DeckThreadingHTTPServer(http.server.ThreadingHTTPServer):
    """Keep expected browser disconnects from obscuring preview diagnostics."""

    def handle_error(
        self,
        request: object,
        client_address: tuple[str, int],
    ) -> None:
        error = sys.exception()
        if isinstance(error, (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


def serve_deck(port: int = 8080, serve_dir: Path = DECK_DIR, *, url_prefix: str = "") -> None:
    """Serve the deck and bounded source viewer on the loopback interface."""
    allowlist = _load_source_allowlist(serve_dir)
    handler = source_request_handler(
        serve_dir=serve_dir,
        repo_root=ROOT,
        allowlist=allowlist,
        url_prefix=url_prefix,
    )
    shown_prefix = "/" + url_prefix.strip("/") + "/" if url_prefix.strip("/") else "/"
    print(f"Serving deck at http://127.0.0.1:{port}{shown_prefix}", flush=True)
    print("Press Ctrl+C to stop.")
    with DeckThreadingHTTPServer(("127.0.0.1", port), handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the gludd reveal.js deck")
    parser.add_argument("--serve", action="store_true",
                        help="Build a token-resolved scratch copy (leaving the tracked "
                             "template untouched) and serve it locally")
    parser.add_argument("--check", action="store_true", help="Run honesty check on deck HTML")
    parser.add_argument("--build", action="store_true",
                        help="Regenerate index.html by applying {{TOKEN}} placeholders from live data")
    parser.add_argument("--preview", action="store_true",
                        help="Build the resolved scratch copy used by --serve, without "
                             "starting the HTTP server (non-interactive; for verification)")
    parser.add_argument("--port", type=int, default=8080, help="Port for --serve")
    parser.add_argument("--url-prefix", default="", help="Optional static prefix, for example /gludd/")
    parser.add_argument("--data", action="store_true", help="Print deck data JSON to stdout")
    args = parser.parse_args()

    if args.preview:
        build_preview_copy()
        return

    if args.data:
        data = build_deck_data()
        print(json.dumps(data, indent=2))
        return

    # Collect live data
    validate_assets(DECK_DIR / "vendor")
    data = build_deck_data()

    # The deck HTML is primarily authored in docs/presentation/deck/index.html.
    # The build step currently regenerates data bindings. Full dynamic rendering
    # (Jinja2 template + partials) is Wave 1-2 of BUILD_TASK_LIST.md.
    deck_path = DECK_FILE
    if not deck_path.exists():
        print(f"ERROR: Deck file not found: {deck_path}", file=sys.stderr)
        sys.exit(1)

    html = deck_path.read_text()

    # When --build is set, rewrite index.html by replacing {{TOKEN}} placeholders
    # with live values from the collected data. This is what makes the deck
    # regenerate from data rather than staying static/stale.
    if args.build:
        html, missing = apply_tokens(html, data)
        html, citations = link_source_citations(html, str(data["git_sha_full"]))
        deck_path.write_text(html)
        (DECK_DIR / SOURCE_ALLOWLIST).write_text(
            json.dumps({"schema": "gludd-source-allowlist/v1", "paths": sorted(citations)}, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Deck HTML rewritten: {deck_path}")
        if missing:
            print(f"  WARNING: tokens not found in template: {', '.join(missing)}")

    # Honesty check
    violations = honesty_check(html)
    if violations:
        print("HONESTY CHECK FAILED:", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        if args.check:
            sys.exit(1)

    # Write deck-data.json for downstream consumers
    data_path = ROOT / "docs" / "presentation" / "deck-data.json"
    data_path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"Deck data written: {data_path}")

    print(f"Deck ready: {deck_path}")
    print(f"  Version:    {data['version']}")
    print(f"  SHA:        {data['git_sha']}")
    print(f"  Tests:      {data['test_count']}")
    print(f"  Roles:      {data['role_count']}")
    print(f"  Features:   {len(data['features'])}")
    if violations:
        print(f"  HONESTY:    {len(violations)} violation(s) — banned marketing tokens found")

    if args.serve:
        preview_dir = build_preview_copy()
        serve_deck(args.port, serve_dir=preview_dir, url_prefix=args.url_prefix)


if __name__ == "__main__":
    main()
