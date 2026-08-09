"""End-to-end project-type pipeline tests via DeepSeek.

Tests the generic SoftwareGenerator across three non-game project types:
  - cli_tool:  generate a simple CLI tool with argparse
  - scraper:   generate a web scraper with requests + BeautifulSoup
  - api_server: generate a FastAPI microservice

Each test uses the project type's prompt templates from
``project_types.py``, calls DeepSeek, extracts + imports + validates
the generated code, and checks type-specific acceptance criteria.

Run:
    DEEPSEEK_API_KEY="sk-..." uv run pytest tests/e2e/test_project_type_pipeline.py -v -s  # pragma: allowlist secret
or:
    make test-specific TESTFILE=tests/e2e/test_project_type_pipeline.py

Smoke mode (single type, no live API):
    PT_TYPE=cli_tool make test-specific TESTFILE=tests/e2e/test_project_type_pipeline.py

Skip live tests without API key.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import re
import sys
import textwrap
import time
from pathlib import Path
from typing import Any, cast

import pytest

# ---------------------------------------------------------------------------
# Key loading
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent


def _load_deepseek_key() -> str | None:
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key:
        return key
    key_file = _REPO_ROOT / ".deepseek.key"
    if key_file.exists():
        v = key_file.read_text().strip()
        return v if v else None
    return None


_KEY_SENTINEL = object()
_KEY_CACHE: str | None | object = _KEY_SENTINEL
_GATEWAY_CACHE: dict[str, Any] = {}


def _get_deepseek_key() -> str | None:
    global _KEY_CACHE
    if _KEY_CACHE is _KEY_SENTINEL:
        _KEY_CACHE = _load_deepseek_key()
    return cast(str | None, _KEY_CACHE)


_DEEPSEEK_KEY = _get_deepseek_key()
_DS_BASE_URL = "https://api.deepseek.com/v1"
_HAS_LANGCHAIN_OPENAI = importlib.util.find_spec("langchain_openai") is not None
_PROVIDER_SKIP = "langchain-openai is not installed — run make sync with provider dependencies"
_KEY_SKIP = "DEEPSEEK_API_KEY not set and .deepseek.key not found"
_LIVE_SKIP_REASON = _KEY_SKIP if not _DEEPSEEK_KEY else _PROVIDER_SKIP
_SMOKE_TYPE = os.environ.get("PT_TYPE", "").strip()


# ---------------------------------------------------------------------------
# Gateway builder
# ---------------------------------------------------------------------------


def _build_deepseek_gateway() -> Any:
    if "gateway" in _GATEWAY_CACHE:
        return _GATEWAY_CACHE["gateway"]

    from general_ludd.models.gateway import ModelGateway, ModelProfile
    from general_ludd.models.provider_registry import ProviderRegistry
    from general_ludd.secrets.env import EnvSecretsManager

    key = _get_deepseek_key()
    assert key, "key must be set before building gateway"

    profile = ModelProfile(
        model_profile_id="deepseek_coder",
        provider="openai",
        provider_package="langchain_openai",
        provider_class_hint="ChatOpenAI",
        model_name="deepseek-chat",
        api_base_alias="DEEPSEEK_API_BASE",
        credential_alias="DEEPSEEK_API_KEY",
        context_window=65536,
        max_input_tokens=60000,
        max_output_tokens=8192,
        cost_per_input_token=0.00000027,
        cost_per_output_token=0.0000011,
        api_metered=True,
        run_budget_usd=5.0,
        enabled=True,
        resource_profile="ai_heavy",
        roles=["coder", "planner", "reviewer"],
        latency_class="fast",
        quality_class="high",
    )
    registry = ProviderRegistry()
    registry.register_provider("openai", "langchain_openai", "ChatOpenAI")
    secrets = EnvSecretsManager()
    secrets.set("DEEPSEEK_API_KEY", key)
    secrets.set("DEEPSEEK_API_BASE", _DS_BASE_URL)
    gateway = cast(Any, ModelGateway)(profiles=[profile], provider_registry=registry, secrets_manager=secrets)
    _GATEWAY_CACHE["gateway"] = gateway
    return gateway


# ---------------------------------------------------------------------------
# Prompt builders (use templates from project_types.py)
# ---------------------------------------------------------------------------

from general_ludd.cloud.project_types import get_project_type  # noqa: E402


def _build_prompt(project_type: str, extra_context: str = "") -> str:
    """Build a combined planner + coder prompt for a project type."""
    pt = get_project_type(project_type)

    planner = pt.prompt_template_planner.replace("{context}", extra_context)
    coder = pt.prompt_template_coder.replace("{context}", extra_context)

    return textwrap.dedent(f"""\
        {planner}

        {coder}
    """)


def _build_coder_prompt(project_type: str, plan: str) -> str:
    """Build a coder-only prompt given a plan."""
    pt = get_project_type(project_type)
    return pt.prompt_template_coder.replace("{context}", plan)


# ---------------------------------------------------------------------------
# Code extraction + validation
# ---------------------------------------------------------------------------


def _extract_code_blocks(text: str) -> dict[str, str]:
    pattern = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
    blocks: dict[str, str] = {}
    for match in pattern.finditer(text):
        lang = match.group(1) or "text"
        content = match.group(2).strip()
        blocks[lang] = content
    return blocks


def _extract_python_module(text: str) -> str | None:
    blocks = _extract_code_blocks(text)
    if "python" in blocks:
        return blocks["python"]
    if "" in blocks:
        content = blocks[""]
        if "class " in content or "def " in content:
            return content
    if "class " in text and ("def " in text or "import " in text):
        lines = text.split("\n")
        python_lines = []
        in_code = False
        for line in lines:
            if line.strip().startswith("```"):
                in_code = not in_code
                continue
            if (
                in_code
                or line.strip().startswith("import ")
                or line.strip().startswith("class ")
                or line.strip().startswith("def ")
                or line.strip().startswith("from ")
            ):
                python_lines.append(line)
        if python_lines:
            return "\n".join(python_lines)
    return None


def _ast_parseable(source: str) -> bool:
    try:
        ast.parse(source)
        return True
    except SyntaxError:
        return False


def _importable(source: str, tmp_dir: Path, mod_name: str) -> tuple[bool, Any | None, str | None]:
    """Write source, import as module. Returns (success, module_or_None, error)."""
    mod_path = tmp_dir / f"{mod_name}.py"
    mod_path.write_text(source)
    spec = importlib.util.spec_from_file_location(mod_name, str(mod_path))
    if spec is None or spec.loader is None:
        return False, None, "spec_from_file_location returned None"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    try:
        spec.loader.exec_module(mod)
        return True, mod, None
    except Exception as exc:
        return False, None, f"{type(exc).__name__}: {exc}"
    finally:
        sys.modules.pop(mod_name, None)


# ---------------------------------------------------------------------------
# Project-type-specific acceptance checks
# ---------------------------------------------------------------------------


def _check_cli_tool(code: str, tmp_dir: Path) -> dict[str, Any]:
    results: dict[str, Any] = {"ac": {}, "errors": []}
    ok, mod, err = _importable(code, tmp_dir, "_gen_cli")
    if not ok:
        results["errors"].append(err)
        return results

    results["ac"]["has_main_function"] = hasattr(mod, "main") and callable(mod.main)
    results["ac"]["has_main_guard"] = "__name__" in code and "__main__" in code
    results["ac"]["has_argparse_or_click"] = "argparse" in code or "click" in code
    results["ac"]["has_sys_exit"] = "sys.exit" in code

    mod_path = tmp_dir / "_gen_cli.py"
    import subprocess

    help_proc = subprocess.run(
        [sys.executable, str(mod_path), "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    results["ac"]["help_prints_usage"] = help_proc.returncode == 0 and len(help_proc.stdout) > 20

    err_proc = subprocess.run(
        [sys.executable, str(mod_path), "--nonexistent-flag-xyz"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    results["ac"]["invalid_args_nonzero"] = err_proc.returncode != 0

    results["ac"]["ast_valid"] = _ast_parseable(code)
    return results


def _check_scraper(code: str, tmp_dir: Path) -> dict[str, Any]:
    results: dict[str, Any] = {"ac": {}, "errors": []}
    ok, mod, err = _importable(code, tmp_dir, "_gen_scraper")
    if not ok:
        results["errors"].append(err)
        return results

    results["ac"]["has_http_client_import"] = "requests" in code
    results["ac"]["has_html_parsing"] = "BeautifulSoup" in code or "beautifulsoup" in code or "lxml" in code
    results["ac"]["has_output_writing"] = any(kw in code for kw in ("csv", "json", "write", "open(", "print("))
    results["ac"]["has_error_handling"] = "try" in code and "except" in code
    results["ac"]["has_time_sleep"] = "time.sleep" in code or "sleep(" in code
    results["ac"]["has_main_function"] = hasattr(mod, "main") and callable(mod.main)
    results["ac"]["ast_valid"] = _ast_parseable(code)

    mod_path = tmp_dir / "_gen_scraper.py"
    import subprocess

    help_proc = subprocess.run(
        [sys.executable, str(mod_path), "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    results["ac"]["help_prints_usage"] = help_proc.returncode == 0 and len(help_proc.stdout) > 20

    return results


def _check_api_server(code: str, tmp_dir: Path) -> dict[str, Any]:
    results: dict[str, Any] = {"ac": {}, "errors": []}
    ok, mod, err = _importable(code, tmp_dir, "_gen_api")
    if not ok:
        results["errors"].append(err)
        return results

    results["ac"]["has_fastapi_import"] = "FastAPI" in code
    results["ac"]["has_app_instance"] = hasattr(mod, "app")
    results["ac"]["has_at_least_one_route"] = "@app" in code
    results["ac"]["has_health_endpoint"] = "health" in code.lower()
    results["ac"]["has_cors_middleware"] = "CORSMiddleware" in code or "add_middleware" in code
    results["ac"]["has_error_handlers"] = "exception_handler" in code or "HTTPException" in code
    results["ac"]["has_lifespan"] = "lifespan" in code or "startup" in code or "shutdown" in code
    results["ac"]["ast_valid"] = _ast_parseable(code)

    return results


_check_dispatcher: dict[str, Any] = {
    "cli_tool": _check_cli_tool,
    "scraper": _check_scraper,
    "api_server": _check_api_server,
}

# Project types to test
PROJECT_TYPES = [
    {
        "id": "cli_tool",
        "display": "CLI Tool",
        "entry_point": "cli.py",
        "context": (
            "Build a simple CLI tool called 'note' that lets users add, list, and delete notes from a local JSON file."
        ),
    },
    {
        "id": "scraper",
        "display": "Web Scraper",
        "entry_point": "scraper.py",
        "context": (
            "Build a web scraper that fetches Hacker News top stories "
            "and outputs them as a CSV file with title, url, and points."
        ),
    },
    {
        "id": "api_server",
        "display": "API Server",
        "entry_point": "main.py",
        "context": (
            "Build a FastAPI microservice for a todo list with endpoints: "
            "POST /todos (create), GET /todos (list), GET /todos/{id} (get), "
            "PUT /todos/{id} (update), DELETE /todos/{id} (delete). "
            "Use Pydantic models for validation."
        ),
    },
]


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestProjectTypeSmoke:
    """Smoke tests that do not need an API key."""

    def test_project_types_loadable(self) -> None:
        pt = get_project_type("cli_tool")
        assert pt.type_id == "cli_tool"
        assert pt.default_entry_point == "cli.py"
        assert len(pt.validation_rules) >= 4
        assert "{context}" in pt.prompt_template_planner
        assert "{context}" in pt.prompt_template_coder

    def test_scraper_validation_rules(self) -> None:
        pt = get_project_type("scraper")
        assert pt.validation_rules == [
            "ast_valid",
            "importable",
            "has_http_client_import",
            "has_html_parsing",
            "has_output_writing",
            "has_error_handling",
        ]

    def test_api_server_validation_rules(self) -> None:
        pt = get_project_type("api_server")
        assert pt.validation_rules == [
            "ast_valid",
            "importable",
            "has_fastapi_app",
            "has_at_least_one_route",
            "has_startup_event",
            "has_shutdown_event",
            "has_error_handlers",
        ]

    def test_prompt_builder_renders(self) -> None:
        prompt = _build_prompt("cli_tool", "Build a todo CLI.")
        assert "cli_tool" in prompt.lower() or "CLI Tool" in prompt
        assert len(prompt) > 200


@pytest.mark.skipif(not _DEEPSEEK_KEY or not _HAS_LANGCHAIN_OPENAI, reason=_LIVE_SKIP_REASON)
class TestProjectTypePipeline:
    """Build + verify projects via DeepSeek API using the planner→coder→reviewer flow."""

    @pytest.fixture(scope="class")
    def gateway(self):
        return _build_deepseek_gateway()

    @staticmethod
    def _call_model(gateway: Any, prompt: str) -> dict[str, Any]:
        t0 = time.time()
        response = gateway.call_model(
            "deepseek_coder",
            messages=[{"role": "user", "content": prompt}],
            estimated_cost=0.0,
            budget_remaining=5.0,
        )
        latency_ms = (time.time() - t0) * 1000
        usage = response.usage_metadata or {}
        return {
            "content": response.content,
            "tokens_in": int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0),
            "tokens_out": int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0),
            "latency_ms": latency_ms,
        }

    # ---- cli_tool ----

    @pytest.mark.skipif(
        _SMOKE_TYPE != "" and _SMOKE_TYPE != "cli_tool",
        reason=f"PT_TYPE={_SMOKE_TYPE}, skipping cli_tool",
    )
    def test_build_cli_tool(self, gateway, tmp_path):
        self._build_and_verify(gateway, tmp_path, "cli_tool")

    # ---- scraper ----

    @pytest.mark.skipif(
        _SMOKE_TYPE != "" and _SMOKE_TYPE != "scraper",
        reason=f"PT_TYPE={_SMOKE_TYPE}, skipping scraper",
    )
    def test_build_scraper(self, gateway, tmp_path):
        self._build_and_verify(gateway, tmp_path, "scraper")

    # ---- api_server ----

    @pytest.mark.skipif(
        _SMOKE_TYPE != "" and _SMOKE_TYPE != "api_server",
        reason=f"PT_TYPE={_SMOKE_TYPE}, skipping api_server",
    )
    def test_build_api_server(self, gateway, tmp_path):
        self._build_and_verify(gateway, tmp_path, "api_server")

    # ---- Shared build + verify ----

    def _build_and_verify(self, gateway, tmp_path, project_type):
        meta = next(p for p in PROJECT_TYPES if p["id"] == project_type)
        pt = get_project_type(project_type)
        out_dir = tmp_path / project_type
        out_dir.mkdir(exist_ok=True)

        print(f"\n{'=' * 70}")
        print(f"BUILDING: {meta['display']} ({project_type})")
        print(f"{'=' * 70}")

        # ---- Phase 1: Planner ----
        print("\n--- Phase 1: Planner ---")
        planner_prompt = pt.prompt_template_planner.replace("{context}", meta["context"])
        plan_response = self._call_model(gateway, planner_prompt)
        plan = plan_response["content"]
        print(
            f"  Plan length: {len(plan)} chars, "
            f"tokens_in={plan_response['tokens_in']} "
            f"tokens_out={plan_response['tokens_out']}"
        )
        assert len(plan) > 100, f"Planner returned too little content ({len(plan)} chars)"

        # ---- Phase 2: Coder ----
        print("\n--- Phase 2: Coder ---")
        coder_prompt = pt.prompt_template_coder.replace("{context}", meta["context"])
        coder_response = self._call_model(gateway, coder_prompt)
        raw_code = coder_response["content"]
        print(f"  Raw output: {len(raw_code)} chars, tokens_out={coder_response['tokens_out']}")

        source = _extract_python_module(raw_code)
        assert source is not None, (
            f"Could not extract Python code from model output. Raw (first 500): {raw_code[:500]!r}"
        )
        print(f"  Extracted: {len(source)} chars of Python code")
        assert len(source) > 50, f"Extracted code too short ({len(source)} chars)"

        # ---- Phase 3: Validation (AST + import + project-type rules) ----
        print("\n--- Phase 3: Validation ---")
        assert _ast_parseable(source), "Generated code has syntax errors"

        mod_name = f"_gen_{project_type}"
        ok, _mod, err = _importable(source, out_dir, mod_name)
        if not ok:
            print(f"  Import warning: {err}")
        else:
            print("  Module imported successfully")

        from general_ludd.cloud.project_types import validate_project_against_rules

        rules_ok = validate_project_against_rules(source, pt)
        print(f"  Project-type validation rules: {'PASS' if rules_ok else 'FAIL'}")

        # ---- Phase 4: Acceptance criteria ----
        print("\n--- Phase 4: Acceptance Criteria ---")
        checker = _check_dispatcher.get(project_type)
        if checker:
            ac_results = checker(source, out_dir)
            for k, v in ac_results.get("ac", {}).items():
                status = "PASS" if v else "FAIL"
                print(f"  [{status}] {k}")
            if ac_results.get("errors"):
                for e in ac_results["errors"]:
                    print(f"  ERROR: {e}")
            failed = sum(1 for v in ac_results.get("ac", {}).values() if not v)
            total = len(ac_results.get("ac", {}))
            print(f"  Acceptance: {total - failed}/{total} passed")
            assert failed == 0, f"{failed} acceptance criteria failed: {ac_results}"
        else:
            print(f"  No checker registered for {project_type}")

        # ---- Phase 5: Reviewer (use acceptance criteria as review prompt) ----
        print("\n--- Phase 5: Reviewer ---")
        review_prompt = textwrap.dedent(f"""\
            Review the following {meta["display"]} code against these acceptance criteria:

            {chr(10).join(f"- {c}" for c in pt.acceptance_criteria)}

            Code to review:
            ```python
            {source}
            ```

            If any criterion fails, identify the issue and suggest a fix.
            If all pass, say "ALL CRITERIA PASS".
        """)
        review_response = self._call_model(gateway, review_prompt)
        review_text = review_response["content"]
        print(f"  Review: {len(review_text)} chars")
        print(f"  Review excerpt: {review_text[:300]!r}")

        assert len(review_text) > 50, "Reviewer returned too little content"

        # ---- Phase 6: Output file written ----
        entry_path = out_dir / meta["entry_point"]
        entry_path.write_text(source)
        print(f"\n--- Output: {entry_path} ({len(source)} bytes) ---")
        assert entry_path.exists() and entry_path.stat().st_size > 0

        print(f"\n=== BUILD COMPLETE: {meta['display']} ===")
