"""Load and maintain the routed behavioral-specification corpus.

The public document is an index.  Its ordered manifest points at bounded
Markdown shards whose marked payloads compose to the exact historical source.
Plain Markdown paths remain supported so unit tests and one-off fixtures do not
need to construct a routed corpus.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INDEX = ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md"
PROLOGUE_START = "<!-- behavioral-spec-prologue:start -->"
PROLOGUE_END = "<!-- behavioral-spec-prologue:end -->"
MANIFEST_START = "<!-- behavioral-spec-shards:start -->"
MANIFEST_END = "<!-- behavioral-spec-shards:end -->"
CONTENT_START = "<!-- behavioral-spec-content:start -->"
CONTENT_END = "<!-- behavioral-spec-content:end -->"
SOURCE_DIGEST = re.compile(
    r"^<!-- behavioral-spec-source-sha256: ([0-9a-f]{64}) -->$", re.MULTILINE
)
ROUTE = re.compile(r"^- `([^`]+)`$", re.MULTILINE)
SPEC_HEADING = re.compile(r"^### ([A-Z]+\d+) — (.+)$", re.MULTILINE)
HEADING_BOUNDARY = re.compile(r"^##(?:#)? ", re.MULTILINE)
MAX_DOCUMENT_LINES = 2_499
MAX_PAYLOAD_LINES = 2_480


class BehavioralSpecLayoutError(ValueError):
    """Raised when a routed corpus is missing, ambiguous, or corrupt."""


@dataclass(frozen=True)
class Shard:
    """One ordered shard and the exact source payload it owns."""

    path: Path
    payload: str
    first_id: str
    first_title: str
    last_id: str
    section_titles: tuple[str, ...]


def _extract_marked(text: str, start: str, end: str, owner: Path) -> str:
    if text.count(start) != 1 or text.count(end) != 1:
        raise BehavioralSpecLayoutError(
            f"{owner}: expected exactly one {start!r} and {end!r} marker"
        )
    opening = start + "\n"
    closing = "\n" + end
    start_at = text.index(opening) + len(opening)
    end_at = text.index(closing, start_at)
    return text[start_at:end_at]


def _manifest_routes(index_path: Path, text: str) -> tuple[str, ...]:
    block = _extract_marked(text, MANIFEST_START, MANIFEST_END, index_path)
    routes = tuple(ROUTE.findall(block))
    nonempty = tuple(line for line in block.splitlines() if line.strip())
    if len(routes) != len(nonempty):
        raise BehavioralSpecLayoutError(
            f"{index_path}: manifest accepts only '- `relative/path.md`' rows"
        )
    if not routes:
        raise BehavioralSpecLayoutError(f"{index_path}: empty shard manifest")
    if len(routes) != len(set(routes)):
        raise BehavioralSpecLayoutError(f"{index_path}: duplicate shard route")
    return routes


def behavioral_spec_shards(
    index_path: Path = DEFAULT_INDEX,
) -> tuple[Path, ...]:
    """Return validated shard paths in canonical manifest order.

    A file without routing markers is a supported plain fixture and has no
    shards.  A routed index fails closed on traversal, duplicates, omissions,
    missing files, or unlisted Markdown siblings.
    """
    index_path = Path(index_path)
    text = index_path.read_text(encoding="utf-8")
    has_start = MANIFEST_START in text
    has_end = MANIFEST_END in text
    if not has_start and not has_end:
        return ()
    if has_start != has_end:
        raise BehavioralSpecLayoutError(f"{index_path}: incomplete shard manifest")

    routes = _manifest_routes(index_path, text)
    root = index_path.parent.resolve()
    shards: list[Path] = []
    for route in routes:
        relative = Path(route)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.suffix != ".md"
            or not relative.parts
            or relative.parts[0] != "behavioral"
        ):
            raise BehavioralSpecLayoutError(
                f"{index_path}: unsafe shard route {route!r}"
            )
        candidate = (index_path.parent / relative).resolve()
        if root not in candidate.parents:
            raise BehavioralSpecLayoutError(
                f"{index_path}: shard escapes spec directory: {route!r}"
            )
        if not candidate.is_file():
            raise BehavioralSpecLayoutError(f"{index_path}: missing shard {route}")
        shards.append(candidate)

    shard_dir = index_path.parent / "behavioral"
    discovered = set(shard_dir.glob("*.md")) if shard_dir.is_dir() else set()
    routed = set(shards)
    extras = sorted(discovered - routed)
    if extras:
        rendered = ", ".join(path.name for path in extras)
        raise BehavioralSpecLayoutError(
            f"{index_path}: unrouted shard(s): {rendered}"
        )
    return tuple(shards)


def load_behavioral_specs(index_path: Path = DEFAULT_INDEX) -> str:
    """Compose a routed corpus exactly, or read a plain fixture unchanged."""
    index_path = Path(index_path)
    index_text = index_path.read_text(encoding="utf-8")
    shards = behavioral_spec_shards(index_path)
    if not shards:
        return index_text

    prologue = _extract_marked(
        index_text, PROLOGUE_START, PROLOGUE_END, index_path
    )
    payloads = [
        _extract_marked(
            shard.read_text(encoding="utf-8"), CONTENT_START, CONTENT_END, shard
        )
        for shard in shards
    ]
    corpus = prologue + "\n\n" + "".join(payloads)
    digests = SOURCE_DIGEST.findall(index_text)
    if len(digests) != 1:
        raise BehavioralSpecLayoutError(
            f"{index_path}: expected exactly one source SHA-256 marker"
        )
    actual = hashlib.sha256(corpus.encode("utf-8")).hexdigest()
    if actual != digests[0]:
        raise BehavioralSpecLayoutError(
            f"{index_path}: digest mismatch: expected {digests[0]}, got {actual}"
        )
    return corpus


def _source_parts(source: str) -> tuple[str, str]:
    boundary = source.find("\n## ")
    if boundary < 0:
        raise BehavioralSpecLayoutError(
            "behavioral source has no top-level specification section"
        )
    # The boundary begins at the second newline in ``prologue\n\n##``. Keep
    # the prologue itself newline-free so the composer owns the exact blank
    # line between metadata and the first section.
    prologue = source[:boundary].removesuffix("\n")
    body = source[boundary + 1 :]
    if not SPEC_HEADING.search(body):
        raise BehavioralSpecLayoutError("behavioral source has no spec headings")
    return prologue, body


def _split_payloads(body: str) -> tuple[str, ...]:
    lines = body.splitlines(keepends=True)
    boundaries = [
        index
        for index, line in enumerate(lines)
        if line.startswith("## ") or line.startswith("### ")
    ]
    boundaries.append(len(lines))
    if not boundaries or boundaries[0] != 0:
        raise BehavioralSpecLayoutError("spec body must begin with a heading")

    chunks: list[str] = []
    start = 0
    while start < len(lines):
        limit = min(start + MAX_PAYLOAD_LINES, len(lines))
        candidates = [point for point in boundaries if start < point <= limit]
        if not candidates:
            raise BehavioralSpecLayoutError(
                f"spec beginning at source line {start + 1} exceeds shard limit"
            )
        h2_candidates = [
            point
            for point in candidates
            if point < len(lines)
            and lines[point].startswith("## ")
            and point - start >= MAX_PAYLOAD_LINES // 2
        ]
        end = max(h2_candidates or candidates)
        chunks.append("".join(lines[start:end]))
        start = end
    return tuple(chunks)


def _anchor(spec_id: str, title: str) -> str:
    value = re.sub(r"[^a-z0-9 -]", "", f"{spec_id} — {title}".lower())
    return value.replace(" ", "-")


def _shard_name(number: int, first_id: str, last_id: str) -> str:
    return f"{number:02d}-{first_id.lower()}-{last_id.lower()}.md"


def _shard_wrapper(shard: Shard) -> str:
    title = f"Behavioral specifications {shard.first_id}-{shard.last_id}"
    sections = "; ".join(shard.section_titles) or "continued ordered specifications"
    return (
        f"# {title}\n\n"
        "[← Behavioral specification index](../BEHAVIORAL_SPECS.md)\n\n"
        f"Ordered source sections: {sections}.\n\n"
        "## Routed specifications\n\n"
        f"{CONTENT_START}\n{shard.payload}"
        f"\n{CONTENT_END}\n"
    )


def _index_document(prologue: str, shards: tuple[Shard, ...], digest: str) -> str:
    manifest = "\n".join(
        f"- `behavioral/{shard.path.name}`" for shard in shards
    )
    rows = []
    for shard in shards:
        relative = f"behavioral/{shard.path.name}"
        anchor = _anchor(shard.first_id, shard.first_title)
        sections = "; ".join(shard.section_titles) or "continued specifications"
        rows.append(
            f"| [{shard.first_id}-{shard.last_id}]({relative}#{anchor}) | "
            f"{sections} |"
        )
    route_table = "\n".join(rows)
    return f"""{PROLOGUE_START}
{prologue}
{PROLOGUE_END}

## Routed specification corpus

The numbered specifications now live in the ordered shards below. Consumers
must use `scripts.behavioral_specs.load_behavioral_specs`; it composes the exact
historical source, verifies the SHA-256 marker, and fails closed for a missing,
duplicate, corrupt, or unrouted shard. Every `### ID — title` heading is retained
verbatim, so IDs and generated heading anchors remain stable inside their routed
file.

The split is a zero-downtime documentation migration: writers stage and verify
all shards before atomically replacing this small routing index. Rollback means
reverting the index, loader, and shard set together; the pinned semantic digest
reconstructs and verifies the pre-split monolith before that rollback is used.

Practitioner reports explain both sides of the boundary. VS Code issue
[#301936](https://github.com/microsoft/vscode/issues/301936) records severe edit
latency once Markdown grows beyond 10,000 lines. GitHub Community discussion
[#4511](https://github.com/orgs/community/discussions/4511) records the long-lived
lack of file-level redirects after content moves, while discussion
[#60861](https://github.com/orgs/community/discussions/60861) records anchor-case
failures. The bounded index, relative routes, lowercase generated anchors, and
byte-for-byte corpus digest address those operational failure modes.

<!-- behavioral-spec-source-sha256: {digest} -->

### Routes

| Stable ID range | Source sections |
| --- | --- |
{route_table}

The following manifest is the loader's single ordered source of truth. A route
must appear exactly once and must resolve beneath `docs/specs/behavioral/`.

{MANIFEST_START}
{manifest}
{MANIFEST_END}
"""


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def write_behavioral_specs(source: str, index_path: Path = DEFAULT_INDEX) -> None:
    """Write a routed corpus, publishing its verified index last.

    Plain fixture paths are written as plain Markdown. The canonical index and
    any already-routed index are rendered into bounded shards.
    """
    index_path = Path(index_path)
    current = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
    routed = MANIFEST_START in current or index_path.resolve() == DEFAULT_INDEX.resolve()
    if not routed:
        _atomic_write(index_path, source)
        return

    prologue, body = _source_parts(source)
    payloads = _split_payloads(body)
    shard_dir = index_path.parent / "behavioral"
    rendered: list[Shard] = []
    for number, payload in enumerate(payloads, start=1):
        headings = list(SPEC_HEADING.finditer(payload))
        if not headings:
            raise BehavioralSpecLayoutError(
                f"shard candidate {number} has no stable specification ID"
            )
        first = headings[0]
        last = headings[-1]
        section_titles = tuple(
            line.removeprefix("## ").strip()
            for line in payload.splitlines()
            if line.startswith("## ")
        )
        filename = _shard_name(number, first.group(1), last.group(1))
        rendered.append(
            Shard(
                path=shard_dir / filename,
                payload=payload,
                first_id=first.group(1),
                first_title=first.group(2),
                last_id=last.group(1),
                section_titles=section_titles,
            )
        )

    shards = tuple(rendered)
    for shard in shards:
        document = _shard_wrapper(shard)
        if len(document.splitlines()) > MAX_DOCUMENT_LINES:
            raise BehavioralSpecLayoutError(
                f"{shard.path.name}: rendered shard exceeds {MAX_DOCUMENT_LINES} lines"
            )
        _atomic_write(shard.path, document)

    expected_paths = {shard.path.resolve() for shard in shards}
    for stale in shard_dir.glob("*.md"):
        if stale.resolve() not in expected_paths:
            stale.unlink()

    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    index_document = _index_document(prologue, shards, digest)
    if len(index_document.splitlines()) > MAX_DOCUMENT_LINES:
        raise BehavioralSpecLayoutError("rendered index exceeds repository line limit")
    _atomic_write(index_path, index_document)

    composed = load_behavioral_specs(index_path)
    if composed != source:
        raise BehavioralSpecLayoutError(
            "published behavioral corpus does not reconstruct its source"
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--split",
        action="store_true",
        help="migrate the canonical monolith into its routed index and shards",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="compose and validate the canonical routed corpus",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.split == args.check:
        raise SystemExit("choose exactly one of --split or --check")
    if args.split:
        source = DEFAULT_INDEX.read_text(encoding="utf-8")
        if MANIFEST_START in source:
            source = load_behavioral_specs(DEFAULT_INDEX)
        write_behavioral_specs(source, DEFAULT_INDEX)
    else:
        load_behavioral_specs(DEFAULT_INDEX)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
