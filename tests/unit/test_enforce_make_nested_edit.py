"""Runtime contract for nested prompt-prone edit denial."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / ".opencode" / "plugin" / "enforce-make.ts"


def _invoke_nested_edit(source: str) -> dict[str, object]:
    """Invoke the real Make enforcement hook with execution-wrapper source."""
    plugin_path = PLUGIN.as_posix()
    code = f"""
const mod = await import({json.dumps(plugin_path)})
const plugin = await mod.default({{}})
try {{
  const result = await plugin["tool.execute.before"](
    {{tool: "functions.exec", args: {{source: {json.dumps(source)}}}}},
    undefined,
  )
  console.log(JSON.stringify(result ?? {{allowed: true}}))
}} catch (error) {{
  const message = error instanceof Error ? error.message : String(error)
  console.log(JSON.stringify({{permissionDecision: "deny", message}}))
}}
"""
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".ts",
        prefix="gludd-nested-edit-",
        dir="/tmp",
        delete=False,
    ) as handle:
        handle.write(code)
        test_path = Path(handle.name)

    environment = os.environ.copy()
    environment["OPENCODE_SUBAGENT"] = ""
    environment["GLUDD_HOT_MODULE_PREFIX"] = (
        f"/tmp/gludd-test-no-hot-nested-{os.getpid()}-"
    )
    try:
        process = subprocess.run(
            ["node", "--experimental-strip-types", str(test_path)],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    finally:
        test_path.unlink(missing_ok=True)

    assert process.returncode == 0, process.stderr
    decoded = json.loads(process.stdout.strip().splitlines()[-1])
    assert isinstance(decoded, dict)
    return cast(dict[str, object], decoded)


@pytest.mark.parametrize(
    "nested_source",
    [
        "await tools.apply_patch(patch)",
        "await tools['apply_patch'](patch)",
    ],
)
def test_make_denies_prompt_prone_nested_edit_tool(nested_source: str) -> None:
    """Nested prompt-prone edit calls must be denied before execution."""
    result = _invoke_nested_edit(nested_source)

    assert result.get("permissionDecision") == "deny"
    assert "Prompt-prone edit tool" in str(result.get("message", ""))
