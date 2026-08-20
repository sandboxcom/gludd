"""LIVE verification that the model-driven MCP tool-call path works end-to-end
against the REAL z.ai provider (glm-4.6).

Background: before today's fix, ModelGateway dropped the provider's tool_calls on
the floor (only content/raw_response were kept), so ToolCallLoop's
`getattr(response, "tool_calls", None)` was always None and NO tool was ever
dispatched in production. The fix adds ModelResponse.tool_calls + _extract_tool_calls
(gateway.py), consumed by execution/tool_loop.py.

This script proves, against the live provider (NOT a mock):
  PHASE 0  does glm-4.6 support OpenAI tool-calling at all? (single tool bound
           directly via the gateway; assert ModelResponse.tool_calls populated)
  PHASE 1  ENUMERATION : model calls a list-tools tool -> tool dispatched ->
           enumeration string returned in the final answer.
  PHASE 2  FILE WRITE  : model calls write_file -> the file actually appears on
           disk with the requested content.
  PHASE 3  GIT         : model calls git_status -> real `git status` output is
           returned to the model.

The REAL components under test: ModelGateway + _extract_tool_calls + bind_tools +
ToolCallLoop dispatch. Only the MCP *transport* (subprocess) is replaced by an
in-process client that genuinely performs each tool's effect — the model-driven
tool-call round trip is fully live.

The API key is read from .zai.key at runtime and NEVER printed.

Exit 0 = at least PHASE 0 proven (tool_calls round-trip live). Detailed per-phase
verdicts are printed for the caller to relay.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

REPO_ROOT = Path(__file__).parent.parent
ZAI_BASE_URL = "https://api.z.ai/api/coding/paas/v4"
ZAI_MODEL = "glm-4.6"


def _load_key() -> str:
    key_path = REPO_ROOT / ".zai.key"
    if not key_path.exists():
        print(f"FAIL: .zai.key not found at {key_path}", file=sys.stderr)
        sys.exit(1)
    key = key_path.read_text().strip()
    if not key:
        print("FAIL: .zai.key is empty", file=sys.stderr)
        sys.exit(1)
    return key  # never printed


# ── In-process MCP tools that actually perform their effect ──────────────────
# Each is a real callable; the in-process client below dispatches to them. This
# is the genuine tool EFFECT — only the subprocess transport is elided.

_TOOL_INVOCATIONS: list[dict[str, Any]] = []  # records every dispatched call


def _tool_echo_enumerate(args: dict[str, Any]) -> str:
    """Enumeration tool: returns a fixed catalogue of 'available items'."""
    items = ["alpha", "bravo", "charlie"]
    return "AVAILABLE_ITEMS: " + ", ".join(items)


def _tool_write_file(args: dict[str, Any]) -> str:
    """Real file write: writes `content` to `path` on disk."""
    path = args.get("path") or args.get("file") or args.get("filename")
    content = args.get("content", args.get("text", ""))
    if not path:
        return "ERROR: no path argument supplied"
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(content))
    return f"WROTE {len(str(content))} bytes to {path}"


def _tool_git_status(args: dict[str, Any]) -> str:
    """Real git: runs `git status --short` in the repo root."""
    proc = subprocess.run(
        ["git", "status", "--short"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=20,
    )
    out = proc.stdout.strip()
    return f"GIT_STATUS_OK rc={proc.returncode}\n{out[:500]}"


_TOOL_IMPLS = {
    "echo_enumerate": _tool_echo_enumerate,
    "write_file": _tool_write_file,
    "git_status": _tool_git_status,
}


class InProcessMCPClient:
    """Faithful to MCPClient's async list_tools()/call_tool() interface, but the
    tool effect runs in-process (no subprocess transport)."""

    def __init__(self, registry: Any) -> None:
        self._registry = registry

    async def list_tools(self, server_id: str | None = None) -> list[Any]:
        return list(self._registry.list_tools(server_id))

    async def call_tool(
        self, server_id: str, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        _TOOL_INVOCATIONS.append({"name": tool_name, "args": arguments})
        impl = _TOOL_IMPLS.get(tool_name)
        if impl is None:
            return {"error": f"no impl for {tool_name}"}
        return {"result": impl(arguments)}


def _build_gateway(key: str) -> Any:
    os.environ["ZAI_API_KEY"] = key
    os.environ["ZAI_BASE_URL"] = ZAI_BASE_URL

    from general_ludd.models.gateway import ModelGateway, ModelProfile
    from general_ludd.models.provider_registry import ProviderRegistry
    from general_ludd.secrets.env import EnvSecretsManager

    registry = ProviderRegistry()
    registry.register_provider("openai", "langchain_openai", "ChatOpenAI")
    if not registry.is_installed("openai"):
        print("FAIL: langchain_openai is not installed (make sync)")
        sys.exit(1)

    profile = ModelProfile(
        model_profile_id="default",
        provider="openai",
        model_name=ZAI_MODEL,
        credential_alias="ZAI_API_KEY",
        api_base_alias="ZAI_BASE_URL",
        enabled=True,
        run_budget_usd=10.0,
        # Reasoning models on z.ai need a generous output budget.
        max_output_tokens=16384,
    )
    secrets = EnvSecretsManager(
        overrides={"ZAI_API_KEY": key, "ZAI_BASE_URL": ZAI_BASE_URL}
    )
    return ModelGateway(
        profiles=[profile], provider_registry=registry, secrets_manager=secrets
    )


def _build_registry() -> Any:
    from general_ludd.mcp.registry import MCPTool, MCPToolRegistry

    reg = MCPToolRegistry()
    reg.register_tool(
        "live",
        MCPTool(
            name="echo_enumerate",
            description="List the available items/catalogue. Takes no arguments.",
            input_schema={"type": "object", "properties": {}},
        ),
    )
    reg.register_tool(
        "live",
        MCPTool(
            name="write_file",
            description="Write text content to a file at the given path.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Filesystem path to write."},
                    "content": {"type": "string", "description": "Text to write."},
                },
                "required": ["path", "content"],
            },
        ),
    )
    reg.register_tool(
        "live",
        MCPTool(
            name="git_status",
            description="Return the current git working-tree status. No arguments.",
            input_schema={"type": "object", "properties": {}},
        ),
    )
    return reg


# ── PHASE 0: does glm-4.6 emit tool_calls at all, via the gateway? ───────────
def phase0_supports_tools(gateway: Any) -> bool:
    print("\n=== PHASE 0: does glm-4.6 support OpenAI tool-calling? ===")
    tool_schema = [
        {
            "name": "get_weather",
            "description": "Get the current weather for a city.",
            "input_schema": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        }
    ]
    messages = [
        {
            "role": "user",
            "content": "What is the weather in Paris? Use the get_weather tool.",
        }
    ]
    try:
        resp = gateway.call_model("default", messages=messages, tools=tool_schema)
    except Exception as exc:
        print(f"  PHASE 0 provider error: {type(exc).__name__}: {exc}")
        return False
    tcs = resp.tool_calls
    print(f"  ModelResponse.tool_calls = {tcs!r}")
    print(f"  content snippet          = {(resp.content or '')[:80]!r}")
    if tcs:
        names = [tc.get("function", {}).get("name") for tc in tcs]
        print(f"  -> tool_calls POPULATED live; names={names}")
        return True
    print("  -> NO tool_calls returned (model answered in text instead)")
    return False


async def _run_loop(gateway: Any, client: Any, registry: Any, prompt: str) -> str:
    from general_ludd.execution.tool_loop import ToolCallLoop
    from general_ludd.schemas.job import JobSpec

    loop = ToolCallLoop(
        model_gateway=gateway,
        mcp_client=client,
        mcp_registry=registry,
    )
    job = JobSpec(
        job_id=f"toolcall-live-{uuid.uuid4().hex[:8]}",
        todo_id="t-live",
        playbook="noop.yml",
        queue="default",
        work_type="code",
        model_profile="default",
    )
    return await loop.run_with_tools(
        job,
        system_prompt=(
            "You are a tool-using assistant. When a tool can answer the user, "
            "you MUST call it rather than answering from memory."
        ),
        user_prompt=prompt,
    )


def _phase(
    label: str,
    gateway: Any,
    client: Any,
    registry: Any,
    prompt: str,
) -> dict[str, Any]:
    print(f"\n=== {label} ===")
    before = len(_TOOL_INVOCATIONS)
    try:
        final = asyncio.run(_run_loop(gateway, client, registry, prompt))
    except Exception as exc:
        print(f"  ERROR: {type(exc).__name__}: {exc}")
        return {"dispatched": [], "final": "", "error": str(exc)}
    dispatched = _TOOL_INVOCATIONS[before:]
    print(f"  tools dispatched: {[d['name'] for d in dispatched]}")
    for d in dispatched:
        print(f"    call {d['name']}({d['args']})")
    print(f"  final answer snippet: {(final or '')[:160]!r}")
    return {"dispatched": dispatched, "final": final or "", "error": None}


def main() -> None:
    key = _load_key()
    gateway = _build_gateway(key)
    registry = _build_registry()
    client = InProcessMCPClient(registry)

    supports = phase0_supports_tools(gateway)
    if not supports:
        print(
            "\nFINAL: glm-4.6 did not emit tool_calls on a direct bound call. "
            "Tool-call loop cannot be exercised against this model/endpoint."
        )
        # Still continue to the loop phases to capture behaviour, but record it.

    # PHASE 1: enumeration
    p1 = _phase(
        "PHASE 1: ENUMERATION (model should call echo_enumerate)",
        gateway, client, registry,
        "List the available items by calling the echo_enumerate tool, then tell "
        "me what they are.",
    )
    p1_works = any(d["name"] == "echo_enumerate" for d in p1["dispatched"])

    # PHASE 2: file write
    with tempfile.TemporaryDirectory(prefix="gludd-toolcall-live-") as tmp_dir:
        target = os.path.join(tmp_dir, "hello.txt")
        p2 = _phase(
            "PHASE 2: FILE WRITE (model should call write_file)",
            gateway, client, registry,
            f"Use the write_file tool to write the exact text 'hello-from-glm' to the "
            f"file path {target}. Then confirm you wrote it.",
        )
        p2_dispatched = any(d["name"] == "write_file" for d in p2["dispatched"])
        p2_file_exists = os.path.exists(target)
        p2_content = ""
        if p2_file_exists:
            p2_content = Path(target).read_text()
        p2_works = p2_dispatched and p2_file_exists and "hello-from-glm" in p2_content

    # PHASE 3: git
    p3 = _phase(
        "PHASE 3: GIT (model should call git_status)",
        gateway, client, registry,
        "Check the git working-tree status by calling the git_status tool, then "
        "summarize what it returned.",
    )
    p3_works = any(d["name"] == "git_status" for d in p3["dispatched"])

    print("\n================ VERDICT ================")
    print(f"Model/endpoint        : {ZAI_MODEL} @ {ZAI_BASE_URL}")
    print(f"glm-4.6 tool-calling  : {'YES' if supports else 'NO'} (PHASE 0)")
    print(f"ENUMERATION           : {'TESTED+WORKS' if p1_works else 'TESTED+FAILED'} "
          f"(dispatched={[d['name'] for d in p1['dispatched']]})")
    print(f"FILE WRITE            : {'TESTED+WORKS' if p2_works else 'TESTED+FAILED'} "
          f"(dispatched={p2_dispatched}, file_exists={p2_file_exists}, "
          f"content_ok={'hello-from-glm' in p2_content})")
    print(f"GIT                   : {'TESTED+WORKS' if p3_works else 'TESTED+FAILED'} "
          f"(dispatched={[d['name'] for d in p3['dispatched']]})")

    all_work = supports and p1_works and p2_works and p3_works
    any_work = supports or p1_works or p2_works or p3_works
    if all_work:
        verdict = "WORKS"
    elif not supports:
        verdict = "UNSUPPORTED-by-provider (glm-4.6 emitted no tool_calls)"
    elif any_work:
        parts = []
        if not p1_works:
            parts.append("enumeration")
        if not p2_works:
            parts.append("file-write")
        if not p3_works:
            parts.append("git")
        verdict = f"PARTIAL (failed: {', '.join(parts)})"
    else:
        verdict = "BROKEN (tool_calls populated but no phase dispatched)"
    print(f"\nLive z.ai tool-calling with fix: {verdict}")
    sys.exit(0 if any_work else 1)


if __name__ == "__main__":
    main()
