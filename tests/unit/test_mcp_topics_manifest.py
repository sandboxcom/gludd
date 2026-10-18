"""Fail-closed contracts for sharded MCP topic documentation."""

from __future__ import annotations

import hashlib
from collections import UserDict
from collections.abc import ItemsView, Mapping
from datetime import date
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


@pytest.mark.parametrize("count", [6, 12])
@pytest.mark.parametrize("max_lines", [5, 2000])
@pytest.mark.parametrize("action", ["chunks", "write"])
def test_independent_topic_sizing_has_one_native_dump_per_topic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    count: int, max_lines: int, action: str,
) -> None:
    topics = {f"general_ludd.agent.topic_{index:03d}": {"value": index}
              for index in reversed(range(count))}
    native_dump = mcp_topics._dump_topics
    calls: list[tuple[str, ...]] = []

    def measured_dump(values: Mapping[str, object]) -> str:
        calls.append(tuple(sorted(values)))
        return native_dump(values)

    monkeypatch.setattr(mcp_topics, "_dump_topics", measured_dump)
    expected: list[tuple[str, ...]]
    if action == "chunks":
        chunks = mcp_topics._topic_chunks(topics, max_lines=max_lines)
        expected = [(name,) for name in sorted(topics)]
    else:
        paths = mcp_topics.write_topics(
            tmp_path / "MCP_TOOLS_TOPICS.yml", topics, max_lines=max_lines,
        )
        assert mcp_topics.load_topics(paths[0]) == topics
        chunks = [yaml.safe_load(path.read_text(encoding="utf-8")) for path in paths[1:]]
        expected = [(name,) for name in sorted(topics)] + [tuple(chunk) for chunk in chunks]
    assert calls == expected
    assert [name for chunk in chunks for name in chunk] == sorted(topics)
    assert all(len(native_dump(chunk).splitlines()) <= max_lines for chunk in chunks)


def _native_greedy_topic_bytes(
    topics: Mapping[str, object], max_lines: int,
) -> list[bytes]:
    remaining = sorted(topics.items())
    payloads: list[bytes] = []
    while remaining:
        accepted: dict[str, object] = {}
        for length in range(1, len(remaining) + 1):
            trial = dict(remaining[:length])
            if len(mcp_topics._dump_topics(trial).splitlines()) > max_lines:
                if not accepted:
                    raise mcp_topics.TopicsManifestError("single MCP topic exceeds reference bound")
                break
            accepted = trial
        payloads.append(mcp_topics._dump_topics(accepted).encode("utf-8"))
        remaining = remaining[len(accepted):]
    return payloads


@pytest.mark.parametrize("padding", [0, 1, 8, 1000])
def test_independent_topic_shards_preserve_native_bytes_and_exact_boundaries(
    tmp_path: Path, padding: int,
) -> None:
    values: list[object] = [
        {"nested": [{"empty": []}, {"empty": {}}], "scalar": None},
        {"scalars": [True, False, 0, -7, 2.5, float("inf"), float("nan")]},
        {"text": "on: [quoted] # punctuation\ntrailing\n"},
        {"text": "Unicode café 雪\u2028separator\u2029paragraph"},
        {"text": "wrapped description " * 16},
        {"text": "", "same": ["repeated", "repeated"]},
    ]
    topics = {f"general_ludd.agent.topic_{index}": value
              for index, value in reversed(list(enumerate(values)))}
    native = mcp_topics._dump_topics(topics).encode("utf-8")
    singletons = b"".join(mcp_topics._dump_topics({name: value}).encode("utf-8")
                          for name, value in sorted(topics.items()))
    assert native == singletons
    minimum = max(len(mcp_topics._dump_topics({name: value}).splitlines())
                  for name, value in topics.items())
    maximum = min(mcp_topics.HARD_MAX_LINES, minimum + padding)
    expected = _native_greedy_topic_bytes(topics, maximum)
    manifest = tmp_path / "MCP_TOOLS_TOPICS.yml"
    paths = mcp_topics.write_topics(manifest, topics, max_lines=maximum)
    assert [path.read_bytes() for path in paths[1:]] == expected
    parts = yaml.safe_load(manifest.read_text(encoding="utf-8"))["parts"]
    assert [part["sha256"] for part in parts] == [
        hashlib.sha256(payload).hexdigest() for payload in expected
    ]
    assert manifest.read_bytes() == yaml.safe_dump(
        {"schema": mcp_topics.SCHEMA, "parts": parts},
        sort_keys=False, allow_unicode=True,
    ).encode("utf-8")


@pytest.mark.parametrize("shape", ["cross", "within", "empty", "cycle"])
def test_shared_and_recursive_topics_keep_native_alias_sizing(
    monkeypatch: pytest.MonkeyPatch, shape: str,
) -> None:
    shared: list[object] = [] if shape == "empty" else list(range(20))
    if shape == "cycle":
        shared.append(shared)
    if shape == "within":
        topics: dict[str, object] = {
            "general_ludd.agent.first": {"a": shared, "b": shared},
            "general_ludd.agent.second": {"value": 1},
        }
    else:
        topics = {"general_ludd.agent.first": shared, "general_ludd.agent.second": shared}
    maximum = len(mcp_topics._dump_topics(topics).splitlines())
    expected = _native_greedy_topic_bytes(topics, maximum)
    native_dump = mcp_topics._dump_topics
    calls: list[int] = []

    def measured_dump(values: Mapping[str, object]) -> str:
        calls.append(len(values))
        return native_dump(values)

    monkeypatch.setattr(mcp_topics, "_dump_topics", measured_dump)
    chunks = mcp_topics._topic_chunks(topics, max_lines=maximum)
    assert [native_dump(chunk).encode("utf-8") for chunk in chunks] == expected
    assert 2 in calls


@pytest.mark.parametrize("value", [(1, 2), b"bytes", {"set"}, {1: "key"}, date(2026, 10, 18), object()])
def test_nonstandard_topic_values_retain_native_success_or_error(value: object) -> None:
    topics = {"general_ludd.agent.first": value, "general_ludd.agent.second": {"value": 2}}
    try:
        expected = _native_greedy_topic_bytes(topics, 1000)
    except yaml.representer.RepresenterError as error:
        with pytest.raises(type(error)) as observed:
            mcp_topics._topic_chunks(topics, max_lines=1000)
        assert str(observed.value) == str(error)
    else:
        chunks = mcp_topics._topic_chunks(topics, max_lines=1000)
        assert [mcp_topics._dump_topics(chunk).encode("utf-8") for chunk in chunks] == expected


def test_custom_builtin_yaml_representer_keeps_native_sizing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def custom_string(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
        return dumper.represent_scalar("tag:yaml.org,2002:str", "custom " + value, style="|")

    monkeypatch.setattr(yaml.SafeDumper, "yaml_representers", {
        **yaml.SafeDumper.yaml_representers, str: custom_string,
    })
    topics = {"general_ludd.agent.first": {"value": "one"},
              "general_ludd.agent.second": {"value": "two"}}
    expected = _native_greedy_topic_bytes(topics, 1000)
    native_dump = mcp_topics._dump_topics
    calls: list[int] = []

    def measured_dump(values: Mapping[str, object]) -> str:
        calls.append(len(values))
        return native_dump(values)

    monkeypatch.setattr(mcp_topics, "_dump_topics", measured_dump)
    chunks = mcp_topics._topic_chunks(topics, max_lines=1000)
    assert [native_dump(chunk).encode("utf-8") for chunk in chunks] == expected
    assert 2 in calls


def test_custom_mapping_exposure_and_topic_mutations_stay_fresh() -> None:
    class ExposedTopics(UserDict[str, object]):
        def __init__(self, values: dict[str, object]) -> None:
            super().__init__(values)
            self.exposures = 0

        def items(self) -> ItemsView[str, object]:
            self.exposures += 1
            return super().items()

    values: dict[str, object] = {"general_ludd.agent.first": {"value": "one"},
              "general_ludd.agent.second": {"value": "two"}}
    topics = ExposedTopics(values)
    chunks = mcp_topics._topic_chunks(topics, max_lines=1000)
    assert topics.exposures == 1
    first = [mcp_topics._dump_topics(chunk).encode("utf-8") for chunk in chunks]
    topics["general_ludd.agent.second"] = {"value": "changed\n" * 10}
    chunks = mcp_topics._topic_chunks(topics, max_lines=1000)
    second = [mcp_topics._dump_topics(chunk).encode("utf-8") for chunk in chunks]
    assert first != second
    assert second == _native_greedy_topic_bytes(topics, 1000)



def test_live_generator_topics_keep_exact_native_shard_bytes(tmp_path: Path) -> None:
    from scripts import gen_mcp_tools

    _tools, topics = gen_mcp_tools.generate()
    assert topics
    assert mcp_topics._independent_builtin_topics(topics)
    expected = _native_greedy_topic_bytes(topics, mcp_topics.DEFAULT_MAX_LINES)
    paths = mcp_topics.write_topics(tmp_path / "MCP_TOOLS_TOPICS.yml", topics)
    assert [path.read_bytes() for path in paths[1:]] == expected
    assert mcp_topics.load_topics(paths[0]) == topics
