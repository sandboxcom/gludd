"""Fail-closed contract for the routed behavioral-specification corpus."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest
from scripts.behavioral_specs import (
    CONTENT_END,
    CONTENT_START,
    MANIFEST_END,
    MANIFEST_START,
    BehavioralSpecLayoutError,
    behavioral_spec_shards,
    load_behavioral_specs,
    write_behavioral_specs,
)
from scripts.verify_spec_enforcement_claims import extract_enforcement_refs

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md"
SHARD_DIR = INDEX.parent / "behavioral"
SOURCE_SHA256 = "36f7cbb2a90b3ee606a732433ce18b3133d46fc159cb5ffbb70d71fb5dcd851c"
SOURCE_LINE_COUNT = 21_495
SPEC_HEADING = re.compile(r"^### ([A-Z]+\d+) — (.+)$", re.MULTILINE)


def _write_index(path: Path, routes: list[str], digest: str = "0" * 64) -> None:
    route_lines = "\n".join(f"- `{route}`" for route in routes)
    path.write_text(
        "<!-- behavioral-spec-prologue:start -->\n"
        "# Behavioral specifications\n"
        "<!-- behavioral-spec-prologue:end -->\n\n"
        f"<!-- behavioral-spec-source-sha256: {digest} -->\n"
        f"{MANIFEST_START}\n{route_lines}\n{MANIFEST_END}\n",
        encoding="utf-8",
    )


def _write_shard(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"# Routed shard\n\n{CONTENT_START}\n{payload}\n{CONTENT_END}\n",
        encoding="utf-8",
    )


def test_composed_corpus_is_byte_identical_to_pre_split_source() -> None:
    """Every original byte remains present and in order after routing."""
    corpus = load_behavioral_specs(INDEX)

    assert len(corpus.splitlines()) == SOURCE_LINE_COUNT
    assert hashlib.sha256(corpus.encode("utf-8")).hexdigest() == SOURCE_SHA256


def test_index_and_every_shard_remain_below_repository_line_limit() -> None:
    documents = (INDEX, *behavioral_spec_shards(INDEX))

    assert len(documents) > 2
    oversized = {
        path.relative_to(ROOT).as_posix(): len(path.read_text().splitlines())
        for path in documents
        if len(path.read_text().splitlines()) >= 2_500
    }
    assert oversized == {}


def test_manifest_routes_every_shard_once_and_only_once() -> None:
    routes = behavioral_spec_shards(INDEX)
    discovered = tuple(sorted(SHARD_DIR.glob("*.md")))

    assert routes == discovered
    assert len(routes) == len(set(routes))


def test_route_links_target_the_first_stable_anchor_in_each_shard() -> None:
    index_text = INDEX.read_text(encoding="utf-8")
    for shard in behavioral_spec_shards(INDEX):
        payload = (
            shard.read_text(encoding="utf-8")
            .split(CONTENT_START + "\n", 1)[1]
            .split("\n" + CONTENT_END, 1)[0]
        )
        match = SPEC_HEADING.search(payload)
        assert match is not None
        first_id, first_title = match.groups()
        anchor = re.sub(
            r"[^a-z0-9 -]", "", f"{first_id} — {first_title}".lower()
        ).replace(" ", "-")
        relative = shard.relative_to(INDEX.parent).as_posix()

        assert f"({relative}#{anchor})" in index_text


def test_spec_ids_and_content_markers_are_globally_unique() -> None:
    corpus = load_behavioral_specs(INDEX)
    ids = [match.group(1) for match in SPEC_HEADING.finditer(corpus)]

    assert len(ids) >= 2_000
    assert len(ids) == len(set(ids))
    for shard in behavioral_spec_shards(INDEX):
        text = shard.read_text(encoding="utf-8")
        assert text.count(CONTENT_START) == 1
        assert text.count(CONTENT_END) == 1


def test_manifest_markers_are_unique() -> None:
    text = INDEX.read_text(encoding="utf-8")

    assert text.count(MANIFEST_START) == 1
    assert text.count(MANIFEST_END) == 1


def test_loader_fails_closed_for_missing_and_duplicate_shards(tmp_path: Path) -> None:
    index = tmp_path / "BEHAVIORAL_SPECS.md"
    _write_index(index, ["behavioral/01.md"])

    with pytest.raises(BehavioralSpecLayoutError, match="missing shard"):
        load_behavioral_specs(index)

    _write_shard(tmp_path / "behavioral" / "01.md", "## A\n")
    _write_index(index, ["behavioral/01.md", "behavioral/01.md"])
    with pytest.raises(BehavioralSpecLayoutError, match="duplicate shard"):
        load_behavioral_specs(index)


def test_loader_fails_closed_for_unrouted_or_corrupt_content(tmp_path: Path) -> None:
    index = tmp_path / "BEHAVIORAL_SPECS.md"
    routed = tmp_path / "behavioral" / "01.md"
    _write_shard(routed, "## A\n")
    _write_shard(tmp_path / "behavioral" / "02.md", "## B\n")
    _write_index(index, ["behavioral/01.md"])

    with pytest.raises(BehavioralSpecLayoutError, match="unrouted shard"):
        load_behavioral_specs(index)

    (tmp_path / "behavioral" / "02.md").unlink()
    with pytest.raises(BehavioralSpecLayoutError, match="digest mismatch"):
        load_behavioral_specs(index)


def test_plain_fixture_files_remain_supported(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture.md"
    fixture.write_text("### AA001 — fixture\n", encoding="utf-8")

    assert load_behavioral_specs(fixture) == "### AA001 — fixture\n"
    assert behavioral_spec_shards(fixture) == ()


def test_writer_publishes_a_reconstructable_routed_fixture(tmp_path: Path) -> None:
    index = tmp_path / "BEHAVIORAL_SPECS.md"
    _write_index(index, ["behavioral/old.md"])
    source = (
        "# Behavioral specifications\n\n"
        "**Version:** test\n\n"
        "---\n\n"
        "## Test specifications\n\n"
        "### AA001 — fixture\n"
        "**Category:** Test\n"
        "**Enforcement:** `test`\n"
        "**Behavior:** The exact fixture is reconstructed.\n"
    )

    write_behavioral_specs(source, index)

    assert load_behavioral_specs(index) == source
    assert all(
        len(path.read_text(encoding="utf-8").splitlines()) < 2_500
        for path in (index, *behavioral_spec_shards(index))
    )


def test_enforcement_claim_parser_ignores_uppercase_placeholders() -> None:
    body = "**Enforcement:** `enforce-pipeline-kickoff.ts` receipt and explicit `REF`"

    files, targets = extract_enforcement_refs(body)

    assert files == ["enforce-pipeline-kickoff.ts"]
    assert targets == []
