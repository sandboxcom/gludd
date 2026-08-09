"""Tests for ansible_tools module_utils — bridge ansible modules to model-callable tools."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.xdist_group("ansible_tools")

_COLLECTIONS_ROOT = str(Path(__file__).resolve().parents[2] / "collections")
if _COLLECTIONS_ROOT not in sys.path:
    sys.path.insert(0, _COLLECTIONS_ROOT)

_AT = "ansible_collections.general_ludd.agent.plugins.module_utils.ansible_tools"

_MODULE_BODY_TEMPLATE = textwrap.dedent("""\
    #!/usr/bin/python
    \"\"\"
    {doc_section}
    {examples_section}
    {return_section}
    \"\"\"

    from ansible.module_utils.basic import AnsibleModule

    def main():
        module = AnsibleModule(argument_spec=dict(
            {arg_spec}
        ), supports_check_mode=True)
        module.exit_json(changed=False)

    if __name__ == "__main__":
        main()
    """)

_MODULE_NO_DOC = textwrap.dedent("""\
    #!/usr/bin/python
    from ansible.module_utils.basic import AnsibleModule

    def main():
        module = AnsibleModule(argument_spec=dict(name=dict(type="str")), supports_check_mode=True)
        module.exit_json(changed=False)

    if __name__ == "__main__":
        main()
    """)


def _make_module(
    tmp_path: Path,
    name: str,
    doc_section: str = "",
    examples_section: str = "",
    return_section: str = "",
    arg_spec: str = "name=dict(type='str', default='')",
) -> Path:
    modules_dir = tmp_path / "plugins" / "modules"
    modules_dir.mkdir(parents=True)
    body = _MODULE_BODY_TEMPLATE.format(
        doc_section=doc_section,
        examples_section=examples_section,
        return_section=return_section,
        arg_spec=arg_spec,
    )
    (modules_dir / f"{name}.py").write_text(body)
    return modules_dir


def _make_collection(
    tmp_path: Path, modules: dict[str, str] | None = None, namespace: str = "general_ludd", collection: str = "agent"
) -> Path:
    root = tmp_path / "ansible_collections" / namespace / collection
    root.mkdir(parents=True)
    if modules:
        mod_dir = root / "plugins" / "modules"
        mod_dir.mkdir(parents=True)
        for name, body in modules.items():
            (mod_dir / f"{name}.py").write_text(body)
    return root


SIMPLE_DOC = textwrap.dedent("""\
    DOCUMENTATION:
      module: test_mod
      short_description: Test module for tool discovery
      description:
        - A simple test module.
        - It validates inputs.
      options:
        name:
          description: The target name.
          type: str
          required: true
        count:
          description: Number of iterations.
          type: int
          default: 1
        enabled:
          description: Whether to enable the feature.
          type: bool
          default: true
        tags:
          description: Optional list of tags.
          type: list
          elements: str
          default: []
    """)

SIMPLE_EXAMPLES = textwrap.dedent("""\
    EXAMPLES:
      - name: Call test module
        general_ludd.agent.test_mod:
          name: "example"
          count: 5
    """)

SIMPLE_RETURN = textwrap.dedent("""\
    RETURN:
      result:
        description: The result string.
        type: str
        returned: always
    """)


# ---------------------------------------------------------------------------
# AnsibleToolSchema
# ---------------------------------------------------------------------------


class TestAnsibleToolSchema:
    def test_basic_schema_structure(self):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        params = {
            "name": {
                "description": "Target name",
                "type": "str",
                "required": True,
            },
            "count": {
                "description": "Number of iterations",
                "type": "int",
                "default": 1,
            },
        }
        schema = ansible_tools.AnsibleToolSchema.build(
            name="test_tool",
            description="A test tool",
            params=params,
        )
        assert schema["name"] == "test_tool"
        assert schema["description"] == "A test tool"
        assert "parameters" in schema
        assert schema["parameters"]["type"] == "object"
        assert "required" in schema["parameters"]
        assert "name" in schema["parameters"]["required"]

    def test_required_field(self):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        params = {
            "mandatory": {"description": "Required param", "type": "str", "required": True},
            "optional": {"description": "Optional param", "type": "int", "default": 42},
        }
        schema = ansible_tools.AnsibleToolSchema.build("t", "d", params)
        assert schema["parameters"]["required"] == ["mandatory"]
        assert "optional" not in schema["parameters"]["required"]

    def test_ansible_type_mapping(self):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        params = {
            "s": {"type": "str"},
            "i": {"type": "int"},
            "b": {"type": "bool"},
            "f": {"type": "float"},
            "l": {"type": "list"},
            "d": {"type": "dict"},
            "p": {"type": "path"},
            "r": {"type": "raw"},
        }
        schema = ansible_tools.AnsibleToolSchema.build("t", "d", params)
        props = schema["parameters"]["properties"]
        assert props["s"]["type"] == "string"
        assert props["i"]["type"] == "integer"
        assert props["b"]["type"] == "boolean"
        assert props["f"]["type"] == "number"
        assert props["l"]["type"] == "array"
        assert props["d"]["type"] == "object"
        assert props["p"]["type"] == "string"
        assert props["r"]["type"] == "string"

    def test_includes_defaults(self):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        params = {
            "opt": {"type": "str", "default": "hello", "description": "desc"},
        }
        schema = ansible_tools.AnsibleToolSchema.build("t", "d", params)
        assert schema["parameters"]["properties"]["opt"]["default"] == "hello"

    def test_enum_from_choices(self):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        params = {
            "mode": {"type": "str", "choices": ["read", "write", "execute"], "description": "m"},
        }
        schema = ansible_tools.AnsibleToolSchema.build("t", "d", params)
        assert schema["parameters"]["properties"]["mode"]["enum"] == ["read", "write", "execute"]

    def test_no_log_params_excluded(self):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        params = {
            "name": {"type": "str", "description": "n"},
            "psk": {"type": "str", "no_log": True, "description": "secret"},
        }
        schema = ansible_tools.AnsibleToolSchema.build("t", "d", params)
        assert "psk" in schema["parameters"]["properties"]
        if "no_log_params" in schema:
            assert "psk" in schema["no_log_params"]


# ---------------------------------------------------------------------------
# DOCUMENTATION / EXAMPLES / RETURN parsing
# ---------------------------------------------------------------------------


class TestDocstringParsing:
    def test_extract_doc_section(self):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        info = ansible_tools._parse_module_doc(SIMPLE_DOC + SIMPLE_EXAMPLES + SIMPLE_RETURN)
        assert "module" in info["doc"]
        assert info["doc"]["module"] == "test_mod"
        assert "options" in info["doc"]
        assert "name" in info["doc"]["options"]
        assert info["doc"]["options"]["name"]["required"] is True

    def test_extract_examples_section(self):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        text = SIMPLE_DOC + SIMPLE_EXAMPLES + SIMPLE_RETURN
        info = ansible_tools._parse_module_doc(text)
        assert "examples" in info
        assert len(info["examples"]) == 1

    def test_extract_return_section(self):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        text = SIMPLE_DOC + SIMPLE_EXAMPLES + SIMPLE_RETURN
        info = ansible_tools._parse_module_doc(text)
        assert "return" in info
        assert "result" in info["return"]

    def test_empty_docstring_returns_empty(self):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        info = ansible_tools._parse_module_doc("")
        assert info["doc"] is None
        assert info["examples"] is None
        assert info["return"] is None

    def test_no_doc_marker_returns_empty(self):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        info = ansible_tools._parse_module_doc("some random text\nno DOCUMENTATION here")
        assert info["doc"] is None

    def test_doc_only_no_options(self):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        minimal = textwrap.dedent("""\
            DOCUMENTATION:
              module: minimal
              short_description: Just a header
        """)
        info = ansible_tools._parse_module_doc(minimal)
        assert info["doc"]["module"] == "minimal"
        assert info["doc"].get("options") is None

    def test_params_from_doc(self):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        info = ansible_tools._parse_module_doc(SIMPLE_DOC)
        params = ansible_tools._extract_params(info["doc"])
        assert "name" in params
        assert params["name"]["required"] is True
        assert params["name"]["type"] == "str"
        assert "count" in params
        assert params["count"]["default"] == 1

    def test_params_from_doc_no_options(self):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        minimal = textwrap.dedent("""\
            DOCUMENTATION:
              module: minimal
        """)
        info = ansible_tools._parse_module_doc(minimal)
        params = ansible_tools._extract_params(info["doc"])
        assert params == {}


# ---------------------------------------------------------------------------
# YAML parsing from module docstrings
# ---------------------------------------------------------------------------


class TestYamlExtraction:
    def test_extract_yaml_block_simple(self):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        doc = textwrap.dedent("""\
            DOCUMENTATION:
              module: foo
              short_description: bar
            EXAMPLES:
              - name: test
                foo:
                  x: 1
            RETURN:
              value:
                type: str
        """)
        blocks = ansible_tools._extract_yaml_blocks(doc)
        assert "DOCUMENTATION" in blocks
        assert "EXAMPLES" in blocks
        assert "RETURN" in blocks

    def test_extract_yaml_block_nested(self):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        doc = textwrap.dedent("""\
            DOCUMENTATION:
              module: foo
              options:
                name:
                  description: The name
                  type: str
                  required: true
                sub:
                  description: Sub options
                  type: dict
                  options:
                    key:
                      type: str
        """)
        blocks = ansible_tools._extract_yaml_blocks(doc)
        parsed = ansible_tools._parse_yaml_safe(blocks["DOCUMENTATION"])
        assert parsed["options"]["name"]["required"] is True
        assert parsed["options"]["sub"]["options"]["key"]["type"] == "str"

    def test_extract_yaml_with_comments(self):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        doc = textwrap.dedent("""\
            # comment line
            DOCUMENTATION:
              # inner comment
              module: foo
              short_description: >-
                A long description
                that spans lines
        """)
        blocks = ansible_tools._extract_yaml_blocks(doc)
        assert "DOCUMENTATION" in blocks

    def test_no_yaml_blocks(self):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        blocks = ansible_tools._extract_yaml_blocks("just some text")
        assert blocks == {}

    def test_parse_with_yaml_module(self):
        import yaml
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        raw = textwrap.dedent("""\
            DOCUMENTATION:
              module: test
              short_description: desc
              options:
                name:
                  type: str
                  required: true
                  default: "hello"
        """)
        blocks = ansible_tools._extract_yaml_blocks(raw)
        result = yaml.safe_load(blocks["DOCUMENTATION"])
        assert result["options"]["name"]["type"] == "str"
        assert result["options"]["name"]["default"] == "hello"


# ---------------------------------------------------------------------------
# AnsibleToolAdapter
# ---------------------------------------------------------------------------


class TestAnsibleToolAdapter:
    def test_adapter_construction(self, tmp_path):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        mod_dir = _make_module(
            tmp_path, "test_mod", doc_section=SIMPLE_DOC, examples_section=SIMPLE_EXAMPLES, return_section=SIMPLE_RETURN
        )
        adapter = ansible_tools.AnsibleToolAdapter(
            module_name="test_mod",
            module_path=str(mod_dir / "test_mod.py"),
            collection_path=str(tmp_path),
        )
        assert adapter.module_name == "test_mod"
        assert adapter.tool_name == "test_mod"

    def test_adapter_to_tool_schema(self, tmp_path):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        root = _make_collection(
            tmp_path,
            {
                "test_mod": _MODULE_BODY_TEMPLATE.format(
                    doc_section=SIMPLE_DOC,
                    examples_section=SIMPLE_EXAMPLES,
                    return_section=SIMPLE_RETURN,
                    arg_spec="name=dict(type='str', required=True), count=dict(type='int', default=1)",
                )
            },
        )
        adapter = ansible_tools.AnsibleToolAdapter(
            "test_mod", module_path=str(root / "plugins" / "modules" / "test_mod.py"), collection_path=str(root)
        )
        schema = adapter.to_tool_schema()
        assert schema["name"] == "test_mod"
        assert "description" in schema
        assert schema["parameters"]["type"] == "object"

    def test_adapter_reads_documentation(self, tmp_path):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        root = _make_collection(
            tmp_path,
            {
                "test_mod": _MODULE_BODY_TEMPLATE.format(
                    doc_section=SIMPLE_DOC,
                    examples_section=SIMPLE_EXAMPLES,
                    return_section=SIMPLE_RETURN,
                    arg_spec="name=dict(type='str', required=True)",
                )
            },
        )
        adapter = ansible_tools.AnsibleToolAdapter(
            "test_mod", module_path=str(root / "plugins" / "modules" / "test_mod.py"), collection_path=str(root)
        )
        assert adapter.doc_info is not None
        assert adapter.doc_info["doc"]["module"] == "test_mod"

    def test_adapter_no_documentation_graceful(self, tmp_path):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        root = _make_collection(tmp_path, {"bare_mod": _MODULE_NO_DOC})
        adapter = ansible_tools.AnsibleToolAdapter(
            "bare_mod", module_path=str(root / "plugins" / "modules" / "bare_mod.py"), collection_path=str(root)
        )
        schema = adapter.to_tool_schema()
        assert schema["name"] == "bare_mod"
        assert schema["description"] == ""


# ---------------------------------------------------------------------------
# discover_tools
# ---------------------------------------------------------------------------


class TestDiscoverTools:
    def test_discovers_modules_in_collection(self, tmp_path):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        root = _make_collection(
            tmp_path,
            {
                "mod_a": _MODULE_BODY_TEMPLATE.format(
                    doc_section=SIMPLE_DOC.replace("test_mod", "mod_a"),
                    examples_section=SIMPLE_EXAMPLES.replace("test_mod", "mod_a"),
                    return_section=SIMPLE_RETURN,
                    arg_spec="name=dict(type='str', required=True)",
                ),
                "mod_b": _MODULE_BODY_TEMPLATE.format(
                    doc_section=SIMPLE_DOC.replace("test_mod", "mod_b"),
                    examples_section=SIMPLE_EXAMPLES.replace("test_mod", "mod_b"),
                    return_section=SIMPLE_RETURN,
                    arg_spec="name=dict(type='str', required=True)",
                ),
            },
        )
        tools = ansible_tools.discover_tools(str(root))
        assert len(tools) == 2
        names = {t.module_name for t in tools}
        assert "mod_a" in names
        assert "mod_b" in names

    def test_skips_non_python_files(self, tmp_path):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        root = _make_collection(
            tmp_path,
            {
                "mod_a": _MODULE_BODY_TEMPLATE.format(
                    doc_section=SIMPLE_DOC.replace("test_mod", "mod_a"),
                    examples_section="",
                    return_section="",
                    arg_spec="name=dict(type='str', required=True)",
                ),
            },
        )
        (root / "plugins" / "modules" / "README.md").write_text("not a module")
        (root / "plugins" / "modules" / "__init__.py").write_text("")
        tools = ansible_tools.discover_tools(str(root))
        assert len(tools) == 1
        assert tools[0].module_name == "mod_a"

    def test_empty_modules_dir(self, tmp_path):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        root = _make_collection(tmp_path)
        tools = ansible_tools.discover_tools(str(root))
        assert tools == []

    def test_nonexistent_path(self):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        tools = ansible_tools.discover_tools(str(Path("/nonexistent/collection/path")))
        assert tools == []

    def test_returns_adapters_not_raw(self, tmp_path):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        root = _make_collection(
            tmp_path,
            {
                "mod_a": _MODULE_BODY_TEMPLATE.format(
                    doc_section=SIMPLE_DOC.replace("test_mod", "mod_a"),
                    examples_section="",
                    return_section="",
                    arg_spec="name=dict(type='str', required=True)",
                )
            },
        )
        tools = ansible_tools.discover_tools(str(root))
        for t in tools:
            assert isinstance(t, ansible_tools.AnsibleToolAdapter)
            assert hasattr(t, "to_tool_schema")
            assert hasattr(t, "call")


# ---------------------------------------------------------------------------
# call_tool via subprocess
# ---------------------------------------------------------------------------


class TestCallTool:
    def test_call_tool_invokes_module(self, tmp_path):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        root = _make_collection(
            tmp_path,
            {
                "test_mod": _MODULE_BODY_TEMPLATE.format(
                    doc_section=SIMPLE_DOC,
                    examples_section=SIMPLE_EXAMPLES,
                    return_section=SIMPLE_RETURN,
                    arg_spec="name=dict(type='str', required=True)",
                )
            },
        )
        adapter = ansible_tools.AnsibleToolAdapter(
            "test_mod", module_path=str(root / "plugins" / "modules" / "test_mod.py"), collection_path=str(root)
        )
        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = json.dumps({"changed": False, "result": "ok"})
            mock_result.stderr = ""
            mock_run.return_value = mock_result
            result = adapter.call({"name": "test"})
            assert result["success"] is True
            assert result["module"] == "test_mod"

    def test_call_tool_on_error(self, tmp_path):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        root = _make_collection(
            tmp_path,
            {
                "test_mod": _MODULE_BODY_TEMPLATE.format(
                    doc_section=SIMPLE_DOC,
                    examples_section="",
                    return_section="",
                    arg_spec="name=dict(type='str', required=True)",
                )
            },
        )
        adapter = ansible_tools.AnsibleToolAdapter(
            "test_mod", module_path=str(root / "plugins" / "modules" / "test_mod.py"), collection_path=str(root)
        )
        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_result.stdout = ""
            mock_result.stderr = "something failed"
            mock_run.return_value = mock_result
            result = adapter.call({"name": "test"})
            assert result["success"] is False
            assert "something failed" in result.get("error", "")

    def test_call_tool_timeout(self, tmp_path):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        root = _make_collection(
            tmp_path,
            {
                "test_mod": _MODULE_BODY_TEMPLATE.format(
                    doc_section=SIMPLE_DOC,
                    examples_section="",
                    return_section="",
                    arg_spec="name=dict(type='str', required=True)",
                )
            },
        )
        adapter = ansible_tools.AnsibleToolAdapter(
            "test_mod", module_path=str(root / "plugins" / "modules" / "test_mod.py"), collection_path=str(root)
        )
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 30)):
            result = adapter.call({"name": "test"})
            assert result["success"] is False
            assert "timeout" in result.get("error", "").lower()

    def test_call_tool_subprocess_error(self, tmp_path):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        root = _make_collection(
            tmp_path,
            {
                "test_mod": _MODULE_BODY_TEMPLATE.format(
                    doc_section=SIMPLE_DOC,
                    examples_section="",
                    return_section="",
                    arg_spec="name=dict(type='str', required=True)",
                )
            },
        )
        adapter = ansible_tools.AnsibleToolAdapter(
            "test_mod", module_path=str(root / "plugins" / "modules" / "test_mod.py"), collection_path=str(root)
        )
        with patch("subprocess.run", side_effect=OSError("exec not found")):
            result = adapter.call({"name": "test"})
            assert result["success"] is False
            assert "exec not found" in result.get("error", "")

    def test_call_tool_passes_secret_params(self, tmp_path):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        root = _make_collection(
            tmp_path,
            {
                "test_mod": _MODULE_BODY_TEMPLATE.format(
                    doc_section=SIMPLE_DOC,
                    examples_section="",
                    return_section="",
                    arg_spec="name=dict(type='str', required=True)",
                )
            },
        )
        adapter = ansible_tools.AnsibleToolAdapter(
            "test_mod", module_path=str(root / "plugins" / "modules" / "test_mod.py"), collection_path=str(root)
        )
        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = json.dumps({"changed": False})
            mock_result.stderr = ""
            mock_run.return_value = mock_result
            adapter.call({"name": "test", "psk": "secret123"})
            args = mock_run.call_args[0][0]
            assert "psk=secret123" in args


# ---------------------------------------------------------------------------
# register_collection_tools
# ---------------------------------------------------------------------------


class TestRegisterCollectionTools:
    def test_returns_list_of_tool_schemas(self, tmp_path):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        root = _make_collection(
            tmp_path,
            {
                "mod_a": _MODULE_BODY_TEMPLATE.format(
                    doc_section=SIMPLE_DOC.replace("test_mod", "mod_a"),
                    examples_section="",
                    return_section="",
                    arg_spec="name=dict(type='str', required=True)",
                ),
                "mod_b": _MODULE_BODY_TEMPLATE.format(
                    doc_section=SIMPLE_DOC.replace("test_mod", "mod_b"),
                    examples_section="",
                    return_section="",
                    arg_spec="name=dict(type='str', required=True)",
                ),
            },
        )
        result = ansible_tools.register_collection_tools(str(root))
        assert isinstance(result, dict)
        assert "tools" in result
        assert "collection" in result
        assert len(result["tools"]) == 2
        schemas = [t["function"] for t in result["tools"]]
        names = {s["name"] for s in schemas}
        assert "mod_a" in names
        assert "mod_b" in names

    def test_empty_collection_returns_empty(self, tmp_path):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        root = _make_collection(tmp_path)
        result = ansible_tools.register_collection_tools(str(root))
        assert result["tools"] == []

    def test_collection_name_in_result(self, tmp_path):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        root = _make_collection(
            tmp_path,
            {
                "mod_a": _MODULE_BODY_TEMPLATE.format(
                    doc_section=SIMPLE_DOC.replace("test_mod", "mod_a"),
                    examples_section="",
                    return_section="",
                    arg_spec="name=dict(type='str', required=True)",
                )
            },
        )
        result = ansible_tools.register_collection_tools(str(root))
        assert result["collection"] in ("agent", "general_ludd.agent")

    def test_model_callable_format(self, tmp_path):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        root = _make_collection(
            tmp_path,
            {
                "mod_a": _MODULE_BODY_TEMPLATE.format(
                    doc_section=SIMPLE_DOC.replace("test_mod", "mod_a"),
                    examples_section="",
                    return_section="",
                    arg_spec="name=dict(type='str', required=True)",
                )
            },
        )
        result = ansible_tools.register_collection_tools(str(root))
        for entry in result["tools"]:
            assert "type" in entry
            assert entry["type"] == "function"
            assert "function" in entry
            assert "name" in entry["function"]
            assert "description" in entry["function"]
            assert "parameters" in entry["function"]


# ---------------------------------------------------------------------------
# Integration: end-to-end with a fake collection
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_full_discover_to_call(self, tmp_path):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        doc_str = textwrap.dedent("""\
            DOCUMENTATION:
              module: e2e_mod
              short_description: End-to-end test module
              description:
                - A module for end-to-end testing.
              options:
                message:
                  description: The message to echo.
                  type: str
                  required: true
                repeat:
                  description: Number of times to repeat.
                  type: int
                  default: 1
            EXAMPLES:
              - name: Use e2e module
                general_ludd.agent.e2e_mod:
                  message: "hello"
            RETURN:
              output:
                description: Echoed output.
                type: str
                returned: always
        """)
        root = _make_collection(
            tmp_path,
            {
                "e2e_mod": _MODULE_BODY_TEMPLATE.format(
                    doc_section=doc_str,
                    examples_section="",
                    return_section="",
                    arg_spec="message=dict(type='str', required=True), repeat=dict(type='int', default=1)",
                )
            },
        )

        tools = ansible_tools.discover_tools(str(root))
        assert len(tools) == 1
        adapter = tools[0]
        schema = adapter.to_tool_schema()
        assert schema["name"] == "e2e_mod"
        assert schema["parameters"]["required"] == ["message"]
        assert schema["parameters"]["properties"]["message"]["type"] == "string"
        assert schema["parameters"]["properties"]["repeat"]["default"] == 1

        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = json.dumps({"changed": False, "output": "hello"})
            mock_result.stderr = ""
            mock_run.return_value = mock_result
            result = adapter.call({"message": "hello", "repeat": 2})
            assert result["success"] is True
            assert result["module"] == "e2e_mod"

    def test_register_and_call(self, tmp_path):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        doc_str = textwrap.dedent("""\
            DOCUMENTATION:
              module: reg_mod
              short_description: Registration test module
              options:
                value:
                  description: Input value.
                  type: str
                  required: true
            EXAMPLES: []
            RETURN: {}
        """)
        root = _make_collection(
            tmp_path,
            {
                "reg_mod": _MODULE_BODY_TEMPLATE.format(
                    doc_section=doc_str,
                    examples_section="",
                    return_section="",
                    arg_spec="value=dict(type='str', required=True)",
                )
            },
        )
        result = ansible_tools.register_collection_tools(str(root))
        assert len(result["tools"]) == 1
        assert result["tools"][0]["function"]["parameters"]["required"] == ["value"]


# ---------------------------------------------------------------------------
# JSON schema round-trip validation
# ---------------------------------------------------------------------------


class TestSchemaRoundTrip:
    def test_schema_is_valid_json(self, tmp_path):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        root = _make_collection(
            tmp_path,
            {
                "mod_a": _MODULE_BODY_TEMPLATE.format(
                    doc_section=SIMPLE_DOC.replace("test_mod", "mod_a"),
                    examples_section="",
                    return_section="",
                    arg_spec="name=dict(type='str', required=True)",
                )
            },
        )
        tools = ansible_tools.discover_tools(str(root))
        schema = tools[0].to_tool_schema()
        serialized = json.dumps(schema)
        roundtrip = json.loads(serialized)
        assert roundtrip == schema

    def test_openai_function_format(self, tmp_path):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        root = _make_collection(
            tmp_path,
            {
                "mod_a": _MODULE_BODY_TEMPLATE.format(
                    doc_section=SIMPLE_DOC.replace("test_mod", "mod_a"),
                    examples_section="",
                    return_section="",
                    arg_spec="name=dict(type='str', required=True)",
                )
            },
        )
        result = ansible_tools.register_collection_tools(str(root))
        entry = result["tools"][0]
        assert "type" in entry
        assert entry["type"] == "function"
        assert "function" in entry
        assert entry["function"]["name"] == "mod_a"
        assert "parameters" in entry["function"]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_module_with_complex_doc(self, tmp_path):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        complex_doc = textwrap.dedent("""\
            DOCUMENTATION:
              module: complex_mod
              short_description: Complex module with many options
              description:
                - First paragraph.
              options:
                src:
                  description: Source path.
                  type: path
                  required: true
                dest:
                  description: Destination path.
                  type: path
                  required: true
                mode:
                  description: File mode.
                  type: str
                  default: "0644"
                owner:
                  description: File owner.
                  type: str
              notes:
                - Requires permission.
        """)
        root = _make_collection(
            tmp_path,
            {
                "complex_mod": _MODULE_BODY_TEMPLATE.format(
                    doc_section=complex_doc,
                    examples_section="",
                    return_section="",
                    arg_spec=(
                        "src=dict(type='str', required=True), dest=dict(type='str', required=True), "
                        "mode=dict(type='str', default='0644'), owner=dict(type='str'), "
                        "nested_config=dict(type='dict')"
                    ),
                )
            },
        )
        tools = ansible_tools.discover_tools(str(root))
        assert len(tools) == 1
        schema = tools[0].to_tool_schema()
        assert "src" in schema["parameters"]["required"]
        assert "dest" in schema["parameters"]["required"]
        assert "mode" in schema["parameters"]["properties"]
        assert schema["parameters"]["properties"]["mode"]["default"] == "0644"
        assert "owner" in schema["parameters"]["properties"]
        assert "owner" not in schema["parameters"]["required"]

    def test_module_python_executable(self, tmp_path):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        py_body = textwrap.dedent("""\
            #!/usr/bin/python
            \"\"\"DOCUMENTATION:
              module: with_hashbang
              short_description: Has a hashbang
              options: {}
            \"\"\"
            import sys
            def main():
                print("ok")
            if __name__ == "__main__":
                main()
        """)
        root = _make_collection(tmp_path, {"with_hashbang": py_body})
        tools = ansible_tools.discover_tools(str(root))
        assert len(tools) == 1
        assert tools[0].module_name == "with_hashbang"

    def test_too_large_module_doc(self, tmp_path):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        huge_desc = "A" * 10000
        huge_doc = textwrap.dedent(f"""\
            DOCUMENTATION:
              module: huge_mod
              short_description: Large module
              options:
                name:
                  type: str
                  description: {huge_desc}
        """)
        root = _make_collection(
            tmp_path,
            {
                "huge_mod": _MODULE_BODY_TEMPLATE.format(
                    doc_section=huge_doc, examples_section="", return_section="", arg_spec="name=dict(type='str')"
                )
            },
        )
        tools = ansible_tools.discover_tools(str(root), max_doc_kb=1)
        assert len(tools) == 0

    def test_module_in_subdir_not_scanned(self, tmp_path):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        root = _make_collection(
            tmp_path,
            {
                "mod_a": _MODULE_BODY_TEMPLATE.format(
                    doc_section=SIMPLE_DOC.replace("test_mod", "mod_a"),
                    examples_section="",
                    return_section="",
                    arg_spec="name=dict(type='str', required=True)",
                )
            },
        )
        sub = root / "plugins" / "modules" / "sub"
        sub.mkdir()
        (sub / "hidden.py").write_text(
            _MODULE_BODY_TEMPLATE.format(
                doc_section=SIMPLE_DOC.replace("test_mod", "hidden"),
                examples_section="",
                return_section="",
                arg_spec="name=dict(type='str', required=True)",
            )
        )
        tools = ansible_tools.discover_tools(str(root))
        names = {t.module_name for t in tools}
        assert "mod_a" in names


# ---------------------------------------------------------------------------
# Parameter validation
# ---------------------------------------------------------------------------


class TestParameterValidation:
    def test_validate_missing_required(self):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        params = {"name": {"type": "str", "required": True}}
        schema = ansible_tools.AnsibleToolSchema.build("t", "d", params)
        valid, errors = ansible_tools.validate_params({"name": "ok"}, schema)
        assert valid is True
        assert errors == []

    def test_validate_missing_required_fails(self):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        params = {"name": {"type": "str", "required": True}}
        schema = ansible_tools.AnsibleToolSchema.build("t", "d", params)
        valid, errors = ansible_tools.validate_params({}, schema)
        assert valid is False
        assert len(errors) > 0

    def test_validate_optional_missing_ok(self):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        params = {"opt": {"type": "str", "default": "hello"}}
        schema = ansible_tools.AnsibleToolSchema.build("t", "d", params)
        valid, _errors = ansible_tools.validate_params({}, schema)
        assert valid is True

    def test_validate_type_mismatch(self):
        from ansible_collections.general_ludd.agent.plugins.module_utils import ansible_tools

        params = {"count": {"type": "int", "required": True}}
        schema = ansible_tools.AnsibleToolSchema.build("t", "d", params)
        valid, _errors = ansible_tools.validate_params({"count": "not_a_number"}, schema)
        assert valid is False
