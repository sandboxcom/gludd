"""Tests for the PHASE-1 MCP tool generator + docs-presence guard.

Covers scripts/gen_mcp_tools.py and scripts/mcp_docs_check.py:
  * the generator builds a tool def for a real gludd_* module with the right
    name / description / input_schema-from-argument_spec;
  * argument_spec -> JSON-schema mapping (type/required/choices/default);
  * the docs-check passes for a documented module and fails for an undocumented
    stub;
  * the generated manifest is valid JSON with one entry per gludd_* module.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
MODULES_DIR = (
    ROOT
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "agent"
    / "plugins"
    / "modules"
)


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gen = _load("gen_mcp_tools")
docs_check = _load("mcp_docs_check")


# ---------------------------------------------------------------------------
# generator: tool def for a real module
# ---------------------------------------------------------------------------
def test_generate_produces_one_tool_per_module():
    tool_defs, topics = gen.generate()
    module_paths = gen.iter_module_paths()
    assert len(module_paths) > 0, "no gludd_* modules found"
    assert len(tool_defs) == len(module_paths)
    assert len(topics) == len(module_paths)


def test_tool_def_for_gludd_ping():
    tool_defs, _ = gen.generate()
    by_name = {t["name"]: t for t in tool_defs}
    name = "general_ludd.agent.gludd_ping"
    assert name in by_name
    td = by_name[name]
    assert td["server_id"] == "ansible"
    # description weaves short_description + description.
    assert "daemon" in td["description"].lower()
    schema = td["input_schema"]
    assert schema["type"] == "object"
    props = schema["properties"]
    # ping has daemon_url / psk / timeout, all with defaults, none required.
    assert set(props) == {"daemon_url", "psk", "timeout"}
    assert props["daemon_url"]["type"] == "string"
    assert props["daemon_url"]["default"] == "http://localhost:8000"
    assert props["timeout"]["type"] == "integer"
    assert props["timeout"]["default"] == 10
    # psk is no_log -> flagged sensitive.
    assert props["psk"].get("x-no-log") is True
    assert "required" not in schema  # no required options on ping


def test_tool_def_required_and_choices():
    """gludd_git has a required 'op' with choices covering all git ops."""
    tool_defs, _ = gen.generate()
    by_name = {t["name"]: t for t in tool_defs}
    td = by_name["general_ludd.agent.gludd_git"]
    schema = td["input_schema"]
    assert "path" in schema["required"]
    assert "op" in schema["required"]

    assert schema["properties"]["op"]["enum"] == [
        "init",
        "clone",
        "commit",
        "gated_commit",
        "current_branch",
        "branch",
        "branch_list",
        "branch_delete",
        "worktree_list",
        "worktree_create",
        "worktree_remove",
        "merge",
        "gated_merge",
        "push",
        "verify_remote",
        "tag_release",
        "tag_checkpoint",
        "release_tag",
        "checkpoint_tag",
        "state",
        "batch_push",
        "release_cut",
        "release_delete",
        "release_recut",
        "ci_verdict",
        "ci_cancel",
    ]


def test_argspec_to_json_schema_unit():
    spec = {
        "name": {"type": "str", "required": True},
        "count": {"type": "int", "default": 5},
        "mode": {"type": "str", "choices": ["a", "b"], "default": "a"},
        "flag": {"type": "bool", "default": False},
        "secret": {"type": "str", "no_log": True},
    }
    schema = gen.argspec_to_json_schema(spec)
    assert schema["type"] == "object"
    assert schema["required"] == ["name"]
    p = schema["properties"]
    assert p["name"]["type"] == "string"
    assert p["count"]["type"] == "integer"
    assert p["count"]["default"] == 5
    assert p["mode"]["enum"] == ["a", "b"]
    assert p["flag"]["type"] == "boolean"
    assert p["secret"]["x-no-log"] is True


def test_extract_argument_spec_from_source():
    src = MODULES_DIR.joinpath("gludd_ping.py").read_text(encoding="utf-8")
    spec = gen.extract_argument_spec(src)
    assert set(spec) == {"daemon_url", "psk", "timeout"}
    assert spec["timeout"]["type"] == "int"
    assert spec["timeout"]["default"] == 10


def test_extract_argument_spec_from_local_helper_call():
    """A module may keep its literal argument spec in a local typed helper."""
    source = """
def _argument_spec():
    return {
        "path": {"type": "str", "required": True},
        "op": {"type": "str", "choices": ["read", "write"]},
    }

module = AnsibleModule(argument_spec=_argument_spec())
"""

    spec = gen.extract_argument_spec(source)

    assert spec == {
        "path": {"type": "str", "required": True},
        "op": {"type": "str", "choices": ["read", "write"]},
    }


@pytest.mark.parametrize(
    "source",
    [
        "AnsibleModule(supports_check_mode=True)",
        "def build(value):\n    return {}\nAnsibleModule(argument_spec=build('x'))",
        "AnsibleModule(argument_spec=missing_helper())",
        "def empty():\n    value = 1\nAnsibleModule(argument_spec=empty())",
    ],
)
def test_extract_argument_spec_helper_resolution_fails_closed(source: str) -> None:
    assert gen.extract_argument_spec(source) == {}


def test_parse_doc_blocks():
    src = MODULES_DIR.joinpath("gludd_ping.py").read_text(encoding="utf-8")
    doc = gen.parse_doc_blocks(src)
    assert "DOCUMENTATION" in doc
    assert doc["DOCUMENTATION"]["short_description"]


def test_parse_doc_blocks_supports_ansible_constant_assignments():
    """Standard Ansible DOCUMENTATION/EXAMPLES/RETURN constants are parsed."""
    src = MODULES_DIR.joinpath("gludd_observe.py").read_text(encoding="utf-8")
    doc = gen.parse_doc_blocks(src)
    assert doc["DOCUMENTATION"]["module"] == "gludd_observe"
    assert doc["DOCUMENTATION"]["short_description"]
    assert "EXAMPLES" in doc
    assert "RETURN" in doc


# ---------------------------------------------------------------------------
# docs-check guard
# ---------------------------------------------------------------------------
def test_docs_check_passes_for_documented_module():
    path = MODULES_DIR / "gludd_ping.py"
    problems = docs_check.check_module(path)
    assert problems == []


def test_docs_check_accepts_standard_ansible_constants():
    path = MODULES_DIR / "gludd_observe.py"
    problems = docs_check.check_module(path)
    assert problems == []


def test_docs_check_all_real_modules_documented():
    """The real gludd_* surface should all be documented (gate-backing claim)."""
    offenders = {}
    for path in docs_check.iter_module_paths():
        problems = docs_check.check_module(path)
        if problems:
            offenders[path.stem] = problems
    assert offenders == {}, f"undocumented modules: {offenders}"


def test_docs_check_fails_for_undocumented_stub(tmp_path: Path, monkeypatch):
    stub = tmp_path / "gludd_undocumented.py"
    stub.write_text(
        "#!/usr/bin/python\n"
        "def main():\n"
        "    pass\n",
        encoding="utf-8",
    )
    problems = docs_check.check_module(stub)
    assert problems  # non-empty -> fails
    assert any("DOCUMENTATION" in p for p in problems)


def test_docs_check_fails_on_missing_short_description(tmp_path: Path):
    stub = tmp_path / "gludd_partial.py"
    stub.write_text(
        '"""\n'
        "DOCUMENTATION:\n"
        "  module: gludd_partial\n"
        "  options:\n"
        "    x:\n"
        "      type: str\n"
        '"""\n',
        encoding="utf-8",
    )
    problems = docs_check.check_module(stub)
    assert any("short_description" in p for p in problems)


def test_docs_check_main_exit_codes(tmp_path: Path, monkeypatch, capsys):
    # Point the guard at a temp dir with one good + one bad module.
    good = tmp_path / "gludd_good.py"
    good.write_text(
        '"""\n'
        "DOCUMENTATION:\n"
        "  module: gludd_good\n"
        "  short_description: a good module\n"
        "  options:\n"
        "    x:\n"
        "      type: str\n"
        '"""\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(docs_check, "MODULES_DIR", tmp_path)
    assert docs_check.main([]) == 0

    bad = tmp_path / "gludd_bad.py"
    bad.write_text("def main():\n    pass\n", encoding="utf-8")
    assert docs_check.main([]) == 1


# ---------------------------------------------------------------------------
# manifest artifact validity
# ---------------------------------------------------------------------------
def test_write_and_reload_manifest(tmp_path: Path, monkeypatch):
    manifest = tmp_path / "MCP_TOOLS_MANIFEST.json"
    topics = tmp_path / "MCP_TOOLS_TOPICS.yml"
    monkeypatch.setattr(gen, "MANIFEST_PATH", manifest)
    monkeypatch.setattr(gen, "TOPICS_PATH", topics)

    tool_defs, topic_map = gen.generate()
    gen.write_artifacts(tool_defs, topic_map)

    loaded = json.loads(manifest.read_text(encoding="utf-8"))
    assert isinstance(loaded, list)
    assert len(loaded) == len(gen.iter_module_paths())
    for entry in loaded:
        assert set(entry) >= {"name", "description", "input_schema", "server_id"}
        assert entry["name"].startswith("general_ludd.agent.gludd_")


def test_generator_main_check_is_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = tmp_path / "MCP_TOOLS_MANIFEST.json"
    topics = tmp_path / "MCP_TOOLS_TOPICS.yml"
    monkeypatch.setattr(gen, "MANIFEST_PATH", manifest)
    monkeypatch.setattr(gen, "TOPICS_PATH", topics)

    assert gen.main(["--check"]) == 0

    output = capsys.readouterr().out
    assert "Would write 39 tool defs" in output
    assert not manifest.exists()
    assert not topics.exists()


def test_generator_main_writes_both_canonical_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = tmp_path / "MCP_TOOLS_MANIFEST.json"
    topics = tmp_path / "MCP_TOOLS_TOPICS.yml"
    monkeypatch.setattr(gen, "MANIFEST_PATH", manifest)
    monkeypatch.setattr(gen, "TOPICS_PATH", topics)

    assert gen.main([]) == 0

    assert len(json.loads(manifest.read_text(encoding="utf-8"))) == 39
    assert "Wrote 39 MCP tool defs" in capsys.readouterr().out
    assert topics.read_text(encoding="utf-8")


def test_committed_manifest_is_valid_if_present():
    """The committed manifest must be valid + complete."""
    loaded = json.loads(gen.MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, list)
    assert len(loaded) == len(gen.iter_module_paths())


def test_tool_names_pass_registry_validation():
    """Generated names must satisfy the real MCPTool name regex (if importable)."""
    mcp_registry = pytest.importorskip("general_ludd.mcp.registry")
    tool_defs, _ = gen.generate()
    for td in tool_defs:
        # Raises on invalid name / fields.
        mcp_registry.MCPTool(**td)
