"""Fail-closed contracts for sharded MCP topic documentation."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml
from scripts import mcp_topics


def _manifest(path: Path, parts: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({"schema": mcp_topics.SCHEMA, "parts": parts}, sort_keys=False),
        encoding="utf-8",
    )


def test_writer_rejects_empty_topics(tmp_path: Path) -> None:
    with pytest.raises(mcp_topics.TopicsManifestError, match="must not be empty"):
        mcp_topics.write_topics(tmp_path / "MCP_TOOLS_TOPICS.yml", {})


def test_loader_rejects_path_traversal(tmp_path: Path) -> None:
    manifest = tmp_path / "MCP_TOOLS_TOPICS.yml"
    _manifest(
        manifest,
        [{"path": "../outside.yml", "sha256": "0" * 64, "topic_count": 1}],
    )
    with pytest.raises(mcp_topics.TopicsManifestError, match="unsafe"):
        mcp_topics.load_topics(manifest)


def test_loader_rejects_tampered_shard(tmp_path: Path) -> None:
    manifest = tmp_path / "MCP_TOOLS_TOPICS.yml"
    paths = mcp_topics.write_topics(
        manifest,
        {"general_ludd.agent.gludd_ping": {"module": "gludd_ping"}},
    )
    paths[1].write_text("general_ludd.agent.gludd_ping: tampered\n", encoding="utf-8")
    with pytest.raises(mcp_topics.TopicsManifestError, match="digest mismatch"):
        mcp_topics.load_topics(manifest)


def test_loader_rejects_count_mismatch(tmp_path: Path) -> None:
    manifest = tmp_path / "MCP_TOOLS_TOPICS.yml"
    mcp_topics.write_topics(
        manifest,
        {"general_ludd.agent.gludd_ping": {"module": "gludd_ping"}},
    )
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    data["parts"][0]["topic_count"] = 2
    manifest.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(mcp_topics.TopicsManifestError, match="count mismatch"):
        mcp_topics.load_topics(manifest)


def test_loader_rejects_globally_reordered_parts(tmp_path: Path) -> None:
    manifest = tmp_path / "MCP_TOOLS_TOPICS.yml"
    mcp_topics.write_topics(
        manifest,
        {
            "general_ludd.agent.gludd_alpha": {},
            "general_ludd.agent.gludd_zulu": {},
        },
        max_lines=1,
    )
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    data["parts"].reverse()
    manifest.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(mcp_topics.TopicsManifestError, match="globally unique and ordered"):
        mcp_topics.load_topics(manifest)


def test_loader_rejects_invalid_utf8_with_valid_digest(tmp_path: Path) -> None:
    manifest = tmp_path / "MCP_TOOLS_TOPICS.yml"
    shard = tmp_path / "mcp-tool-topics" / "topics-001.yml"
    shard.parent.mkdir(parents=True, exist_ok=True)
    payload = b"\xff\n"
    shard.write_bytes(payload)
    _manifest(
        manifest,
        [
            {
                "path": "mcp-tool-topics/topics-001.yml",
                "sha256": hashlib.sha256(payload).hexdigest(),
                "topic_count": 1,
            }
        ],
    )
    with pytest.raises(mcp_topics.TopicsManifestError, match="UTF-8"):
        mcp_topics.load_topics(manifest)


@pytest.mark.parametrize("max_lines", [0, mcp_topics.HARD_MAX_LINES + 1])
def test_writer_rejects_out_of_range_shard_limits(
    tmp_path: Path,
    max_lines: int,
) -> None:
    with pytest.raises(mcp_topics.TopicsManifestError, match="max_lines"):
        mcp_topics.write_topics(
            tmp_path / "MCP_TOOLS_TOPICS.yml",
            {"general_ludd.agent.gludd_ping": {}},
            max_lines=max_lines,
        )


@pytest.mark.parametrize("name", ["gludd_ping", 3])
def test_writer_rejects_invalid_topic_names(tmp_path: Path, name: object) -> None:
    with pytest.raises(mcp_topics.TopicsManifestError, match="invalid MCP topic name"):
        mcp_topics.write_topics(
            tmp_path / "MCP_TOOLS_TOPICS.yml",
            {name: {}},  # type: ignore[dict-item]
        )


def test_writer_rejects_one_topic_larger_than_a_shard(tmp_path: Path) -> None:
    with pytest.raises(mcp_topics.TopicsManifestError, match="single MCP topic"):
        mcp_topics.write_topics(
            tmp_path / "MCP_TOOLS_TOPICS.yml",
            {"general_ludd.agent.gludd_ping": {"lines": ["a", "b"]}},
            max_lines=1,
        )


def test_writer_and_artifact_inventory_round_trip_multiple_shards(tmp_path: Path) -> None:
    manifest = tmp_path / "MCP_TOOLS_TOPICS.yml"
    topics = {
        "general_ludd.agent.gludd_alpha": {"description": "alpha"},
        "general_ludd.agent.gludd_zulu": {"description": "zulu"},
    }

    written = mcp_topics.write_topics(manifest, topics, max_lines=2)

    assert len(written) == 3
    assert mcp_topics.topic_artifact_paths(manifest) == written
    assert mcp_topics.load_topics(manifest) == topics


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[unterminated\n", "cannot read"),
        ("schema: wrong\nparts: []\n", "schema"),
        (
            f"schema: {mcp_topics.SCHEMA}\nparts: []\nextra: true\n",
            "unknown fields",
        ),
        (f"schema: {mcp_topics.SCHEMA}\nparts: []\n", "non-empty list"),
        (f"schema: {mcp_topics.SCHEMA}\nparts:\n  - no\n", "must be mappings"),
    ],
)
def test_loader_rejects_invalid_manifest_shapes(
    tmp_path: Path,
    text: str,
    message: str,
) -> None:
    manifest = tmp_path / "MCP_TOOLS_TOPICS.yml"
    manifest.write_text(text, encoding="utf-8")

    with pytest.raises(mcp_topics.TopicsManifestError, match=message):
        mcp_topics.load_topics(manifest)


def test_loader_rejects_missing_manifest(tmp_path: Path) -> None:
    with pytest.raises(mcp_topics.TopicsManifestError, match="cannot read"):
        mcp_topics.load_topics(tmp_path / "missing.yml")


@pytest.mark.parametrize(
    ("part", "message"),
    [
        (
            {
                "path": "mcp-tool-topics/topics-001.yml",
                "sha256": "0" * 64,
                "topic_count": 1,
                "extra": True,
            },
            "unknown fields",
        ),
        ({"path": 3, "sha256": "0" * 64, "topic_count": 1}, "must be a string"),
        ({"path": "/tmp/topics.yml", "sha256": "0" * 64, "topic_count": 1}, "unsafe"),
        ({"path": "elsewhere/topics.yml", "sha256": "0" * 64, "topic_count": 1}, "unsafe"),
        ({"path": "mcp-tool-topics/topics.txt", "sha256": "0" * 64, "topic_count": 1}, "unsafe"),
    ],
)
def test_loader_rejects_invalid_part_descriptors(
    tmp_path: Path,
    part: dict[str, object],
    message: str,
) -> None:
    manifest = tmp_path / "MCP_TOOLS_TOPICS.yml"
    _manifest(manifest, [part])

    with pytest.raises(mcp_topics.TopicsManifestError, match=message):
        mcp_topics.load_topics(manifest)


def test_loader_rejects_symlink_part(tmp_path: Path) -> None:
    manifest = tmp_path / "MCP_TOOLS_TOPICS.yml"
    outside = tmp_path / "outside.yml"
    outside.write_text("general_ludd.agent.gludd_ping: {}\n", encoding="utf-8")
    shard = tmp_path / "mcp-tool-topics" / "topics-001.yml"
    shard.parent.mkdir()
    shard.symlink_to(outside)
    _manifest(
        manifest,
        [{"path": "mcp-tool-topics/topics-001.yml", "sha256": "0" * 64, "topic_count": 1}],
    )

    with pytest.raises(mcp_topics.TopicsManifestError, match="escapes"):
        mcp_topics.load_topics(manifest)


def _single_part_manifest(tmp_path: Path, payload: bytes, **overrides: object) -> Path:
    manifest = tmp_path / "MCP_TOOLS_TOPICS.yml"
    shard = tmp_path / "mcp-tool-topics" / "topics-001.yml"
    shard.parent.mkdir(parents=True, exist_ok=True)
    shard.write_bytes(payload)
    part: dict[str, object] = {
        "path": "mcp-tool-topics/topics-001.yml",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "topic_count": 1,
    }
    part.update(overrides)
    _manifest(manifest, [part])
    return manifest


def test_loader_rejects_missing_and_oversized_parts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = tmp_path / "MCP_TOOLS_TOPICS.yml"
    _manifest(
        manifest,
        [{"path": "mcp-tool-topics/missing.yml", "sha256": "0" * 64, "topic_count": 1}],
    )
    with pytest.raises(mcp_topics.TopicsManifestError, match="cannot read MCP topic part"):
        mcp_topics.load_topics(manifest)

    payload = b"general_ludd.agent.gludd_ping: {}\n"
    manifest = _single_part_manifest(tmp_path, payload)
    monkeypatch.setattr(mcp_topics, "MAX_PART_BYTES", len(payload) - 1)
    with pytest.raises(mcp_topics.TopicsManifestError, match="byte limit"):
        mcp_topics.load_topics(manifest)


def test_loader_rejects_line_limit_and_invalid_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"general_ludd.agent.gludd_ping:\n  description: ping\n"
    manifest = _single_part_manifest(tmp_path, payload)
    monkeypatch.setattr(mcp_topics, "HARD_MAX_LINES", 1)
    with pytest.raises(mcp_topics.TopicsManifestError, match="line limit"):
        mcp_topics.load_topics(manifest)

    monkeypatch.setattr(mcp_topics, "HARD_MAX_LINES", 2_499)
    manifest = _single_part_manifest(tmp_path, payload, sha256="not-a-digest")
    with pytest.raises(mcp_topics.TopicsManifestError, match=r"invalid.*digest"):
        mcp_topics.load_topics(manifest)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"[unterminated\n", "invalid MCP topic part YAML"),
        (b"- general_ludd.agent.gludd_ping\n", "must be a mapping"),
        (b"invalid_name: {}\n", "invalid MCP topic name"),
    ],
)
def test_loader_rejects_invalid_part_payloads(
    tmp_path: Path,
    payload: bytes,
    message: str,
) -> None:
    manifest = _single_part_manifest(tmp_path, payload)

    with pytest.raises(mcp_topics.TopicsManifestError, match=message):
        mcp_topics.load_topics(manifest)


@pytest.mark.parametrize("topic_count", [True, "1"])
def test_loader_rejects_non_integer_topic_counts(
    tmp_path: Path,
    topic_count: object,
) -> None:
    payload = b"general_ludd.agent.gludd_ping: {}\n"
    manifest = _single_part_manifest(tmp_path, payload, topic_count=topic_count)

    with pytest.raises(mcp_topics.TopicsManifestError, match="count mismatch"):
        mcp_topics.load_topics(manifest)
