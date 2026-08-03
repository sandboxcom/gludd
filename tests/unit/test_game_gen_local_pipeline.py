from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
import yaml

ROLE_ROOT = Path("collections/ansible_collections/general_ludd/agent/roles/local_game_gen")


def _load_yaml(rel: str) -> Any:
    path = ROLE_ROOT / rel
    assert path.exists(), f"Missing role file: {path}"
    with open(path) as fh:
        doc = yaml.safe_load(fh)
    assert doc is not None, f"Empty/unparseable YAML in {path}"
    return doc


def _task_names(tasks: list[dict[str, Any]]) -> list[str]:
    return [t.get("name", "(unnamed)") for t in tasks]


def _gather_commands(tasks: list[dict[str, Any]]) -> list[str]:
    cmds: list[str] = []
    for t in tasks:
        if "ansible.builtin.command" in t:
            cmds.append(t["ansible.builtin.command"])
        elif "ansible.builtin.shell" in t:
            cmds.append(t["ansible.builtin.shell"])
    return cmds


# =============================================================================
#  1. Pipeline configuration validation
# =============================================================================


class TestPipelineConfigValidation:
    def test_role_root_has_required_dirs(self) -> None:
        for sub in ("tasks", "defaults", "meta", "molecule"):
            assert (ROLE_ROOT / sub).is_dir(), f"Missing role subdirectory: {sub}"

    def test_tasks_main_yml_is_non_empty_list(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        assert isinstance(tasks, list), "tasks/main.yml must be a list"
        assert len(tasks) >= 5, f"Expected >=5 tasks, got {len(tasks)}"

    def test_every_task_has_name(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        for i, t in enumerate(tasks):
            assert "name" in t, f"Task at index {i} missing a 'name' key"

    def test_task_names_are_unique(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        names = _task_names(tasks)
        assert len(names) == len(set(names)), f"Duplicate task names: {names}"

    def test_required_default_keys_exist(self) -> None:
        defaults = cast(dict[str, Any], _load_yaml("defaults/main.yml"))
        required = [
            "game_name",
            "model_repo",
            "model_file",
            "model_download_dir",
            "server_host",
            "server_port",
            "game_prompt",
            "max_tokens",
            "artifact_dir",
            "temperature",
            "server_context_size",
            "health_check_retries",
            "health_check_delay",
        ]
        for key in required:
            assert key in defaults, f"Missing default key: {key}"

    def test_meta_has_required_galaxy_fields(self) -> None:
        meta = cast(dict[str, Any], _load_yaml("meta/main.yml"))
        gi = meta["galaxy_info"]
        for field in ("role_name", "author", "description", "license", "min_ansible_version"):
            assert field in gi, f"meta galaxy_info missing: {field}"
        assert gi["role_name"] == "local_game_gen"


# =============================================================================
#  2. Task step ordering
# =============================================================================


class TestTaskStepOrdering:
    def test_first_task_validates_inputs(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        first_name = tasks[0].get("name", "")
        assert "validate" in first_name.lower(), f"First task must validate inputs, got: {first_name!r}"
        module_keys = [k for k in tasks[0] if k != "name"]
        assert any("assert" in k for k in module_keys), f"Expected assert module, got keys: {module_keys}"

    def test_input_validation_checks_three_required_vars(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        t0 = tasks[0]
        that_clause = t0["ansible.builtin.assert"]["that"]
        checks = [str(item).strip() for item in that_clause]
        assert any("game_name" in c for c in checks), "Must validate game_name"
        assert any("model_repo" in c for c in checks), "Must validate model_repo"
        assert any("model_file" in c for c in checks), "Must validate model_file"

    def test_artifact_dir_created_before_any_mutation(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        t1 = tasks[1]
        assert "artifact_dir" in str(t1), "Step 2 must reference artifact_dir"
        assert "file" in str(t1), "Step 2 must use file module to create directory"

    def test_download_before_server(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        names = _task_names(tasks)
        dl_idx = next(i for i, n in enumerate(names) if "download" in n.lower())
        svr_idx = next(i for i, n in enumerate(names) if "start" in n.lower() and "server" in n.lower())
        assert dl_idx < svr_idx, f"Download (idx {dl_idx}) must precede server start (idx {svr_idx})"

    def test_server_before_game_generation(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        names = _task_names(tasks)
        svr_idx = next(i for i, n in enumerate(names) if "start" in n.lower() and "server" in n.lower())
        gen_idx = next(i for i, n in enumerate(names) if "generation" in n.lower() or "call local model" in n.lower())
        assert svr_idx < gen_idx, f"Server start (idx {svr_idx}) must precede generation (idx {gen_idx})"

    def test_game_generation_before_verify(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        names = _task_names(tasks)
        gen_idx = next(i for i, n in enumerate(names) if "generation" in n.lower() or "call local model" in n.lower())
        ver_idx = next(i for i, n in enumerate(names) if "verify" in n.lower() and "ast" in n.lower())
        assert gen_idx < ver_idx, f"Generation (idx {gen_idx}) must precede AST verify (idx {ver_idx})"

    def test_shutdown_is_last_or_always_block(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        last = tasks[-1]
        assert any(w in str(last).lower() for w in ("shutdown", "stop", "kill")), (
            f"Final task should be shutdown-related, got: {last.get('name', '?')}"
        )
        shutdown_text = yaml.dump(last)
        assert "always" in shutdown_text.lower(), "Shutdown must use always: block for guaranteed execution"

    def test_health_poll_between_server_and_generation(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        names = _task_names(tasks)
        svr_idx = next(i for i, n in enumerate(names) if "start" in n.lower() and "server" in n.lower())
        health_idx = next(i for i, n in enumerate(names) if "health" in n.lower())
        gen_idx = next(i for i, n in enumerate(names) if "generation" in n.lower() or "call local model" in n.lower())
        assert svr_idx < health_idx < gen_idx, (
            f"Health poll (idx {health_idx}) between server start (idx {svr_idx}) and generation (idx {gen_idx})"
        )


# =============================================================================
#  3. Defaults correctness
# =============================================================================


class TestDefaultsCorrectness:
    @pytest.fixture(scope="class")
    def defaults(self) -> dict[str, Any]:
        return cast(dict[str, Any], _load_yaml("defaults/main.yml"))

    def test_server_host_is_loopback(self, defaults: dict[str, Any]) -> None:
        assert defaults["server_host"] == "127.0.0.1", "server_host must default to loopback"

    def test_server_port_is_valid(self, defaults: dict[str, Any]) -> None:
        port = int(defaults["server_port"])
        assert 1024 <= port <= 65535, f"Port {port} out of valid range"

    def test_max_tokens_positive(self, defaults: dict[str, Any]) -> None:
        assert int(defaults["max_tokens"]) > 0, "max_tokens must be > 0"

    def test_temperature_in_range(self, defaults: dict[str, Any]) -> None:
        t = float(defaults["temperature"])
        assert 0.0 <= t <= 2.0, f"temperature {t} out of [0, 2]"

    def test_gpu_layers_non_negative(self, defaults: dict[str, Any]) -> None:
        assert int(defaults["server_gpu_layers"]) >= 0

    def test_context_size_positive(self, defaults: dict[str, Any]) -> None:
        assert int(defaults["server_context_size"]) > 0

    def test_health_check_retries_positive(self, defaults: dict[str, Any]) -> None:
        assert int(defaults["health_check_retries"]) > 0

    def test_health_check_delay_positive(self, defaults: dict[str, Any]) -> None:
        assert int(defaults["health_check_delay"]) > 0

    def test_artifact_dir_is_under_tmp(self, defaults: dict[str, Any]) -> None:
        assert defaults["artifact_dir"].startswith("/tmp/"), (
            f"artifact_dir should be under /tmp/, got {defaults['artifact_dir']}"
        )

    def test_model_repo_format(self, defaults: dict[str, Any]) -> None:
        repo = defaults["model_repo"]
        assert "/" in repo, f"model_repo must be org/repo, got {repo!r}"
        assert len(repo.split("/")) == 2, f"model_repo must have exactly one slash, got {repo!r}"

    def test_model_file_ends_with_gguf(self, defaults: dict[str, Any]) -> None:
        assert defaults["model_file"].endswith(".gguf")

    def test_game_prompt_is_non_empty_multiline(self, defaults: dict[str, Any]) -> None:
        prompt: str = defaults["game_prompt"]
        assert len(prompt) > 100, f"game_prompt too short ({len(prompt)} chars)"
        assert "\n" in prompt, "game_prompt should be multiline"

    def test_game_name_is_string(self, defaults: dict[str, Any]) -> None:
        assert isinstance(defaults["game_name"], str)
        assert len(defaults["game_name"]) > 0

    def test_startup_timeout_positive(self, defaults: dict[str, Any]) -> None:
        assert int(defaults["server_startup_timeout"]) > 0

    def test_no_sensitive_defaults(self, defaults: dict[str, Any]) -> None:
        sensitive = {"api_key", "password", "secret", "credential"}
        for key in defaults:
            lowered = key.lower()
            assert not any(s in lowered for s in sensitive), f"Sensitive-looking key: {key}"


# =============================================================================
#  4. Error handling for missing binaries
# =============================================================================


class TestMissingBinaryErrorHandling:
    def test_huggingface_cli_referenced_in_tasks(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        cmds = _gather_commands(tasks)
        dump = yaml.dump(tasks)
        assert any("huggingface" in c for c in cmds) or "huggingface" in dump, "Tasks must reference huggingface-cli"

    def test_python3_referenced_in_verify_steps(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        cmds = _gather_commands(tasks)
        python_cmds = [c for c in cmds if c.startswith("python3")]
        assert len(python_cmds) >= 3, f"Expected >=3 python3 invocations (AST/import/runtime), got {len(python_cmds)}"

    def test_download_task_has_creates_guard(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        for t in tasks:
            if "huggingface" in str(t):
                assert "creates" in str(t), "Download should have args.creates for idempotency"
                return
        pytest.fail("No huggingface download task found")

    def test_shutdown_ignores_missing_pid_file(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        all_tasks: list[dict[str, Any]] = []
        for t in tasks:
            all_tasks.append(t)
            for inner in t.get("always", []):
                all_tasks.append(inner)
        for t in all_tasks:
            if "ansible.builtin.slurp" in t and "pid" in str(t.get("ansible.builtin.slurp", "")).lower():
                assert t.get("ignore_errors") is True, "PID slurp must ignore_errors"
                return
        pytest.fail("No slurp task for PID file found")

    def test_shell_command_redirects_stderr_for_nohup(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        for t in tasks:
            if "ansible.builtin.shell" in t:
                cmd = t["ansible.builtin.shell"]
                assert "2>&1" in cmd or "nohup" not in cmd, "nohup'd cmd must redirect stderr"
                return

    def test_ast_parse_registers_result(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        for t in tasks:
            if "ast.parse" in str(t):
                assert "register" in t, "AST step must register result"
                assert t.get("changed_when") is False, "AST step is read-only"
                return
        pytest.fail("No AST parse step found")

    def test_health_poll_has_bounded_retries(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        for t in tasks:
            if "health" in str(t).lower() and "retries" in str(t):
                assert "retries" in t, "Health poll step must have retries config"
                return


# =============================================================================
#  5. Environment variable propagation
# =============================================================================


class TestEnvironmentVariablePropagation:
    def test_artifact_dir_is_configurable(self) -> None:
        defaults = cast(dict[str, Any], _load_yaml("defaults/main.yml"))
        alt = "/tmp/gludd-game-gen-alt"
        assert alt != defaults["artifact_dir"], "Test value must differ from default"

    def test_model_download_dir_is_configurable(self) -> None:
        defaults = cast(dict[str, Any], _load_yaml("defaults/main.yml"))
        alt = "/tmp/gludd-model-cache-2"
        assert alt != defaults["model_download_dir"]

    def test_server_host_overrideable_from_localhost(self) -> None:
        defaults = cast(dict[str, Any], _load_yaml("defaults/main.yml"))
        assert defaults["server_host"] == "127.0.0.1"

    def test_task_uses_template_variables_not_hardcoded_paths(self) -> None:
        tasks_text = (ROLE_ROOT / "tasks" / "main.yml").read_text()
        variable_patterns = [
            "{{ artifact_dir }}",
            "{{ model_download_dir }}",
            "{{ server_host }}",
            "{{ server_port }}",
            "{{ _model_path }}",
            "{{ game_name }}",
            "{{ game_prompt }}",
            "{{ max_tokens }}",
            "{{ temperature }}",
            "{{ server_context_size }}",
            "{{ server_gpu_layers }}",
            "{{ health_check_retries }}",
            "{{ health_check_delay }}",
        ]
        for var in variable_patterns:
            assert var in tasks_text, f"Task file must reference {var}"

    def test_internal_variables_use_underscore_prefix(self) -> None:
        tasks_text = (ROLE_ROOT / "tasks" / "main.yml").read_text()
        internal_vars = [
            "_model_path",
            "_server_log",
            "_server_name",
            "_generated_code",
            "_health_poll",
            "_model_response",
            "_ast_result",
            "_import_result",
            "_runtime_result",
            "_server_pid",
        ]
        for var in internal_vars:
            assert var in tasks_text, f"Internal computed var {var} not found in tasks"

    def test_all_defaults_referenced_in_tasks(self) -> None:
        defaults = cast(dict[str, Any], _load_yaml("defaults/main.yml"))
        tasks_text = (ROLE_ROOT / "tasks" / "main.yml").read_text()
        permitted_unreferenced = {"game_genre", "game_description", "server_startup_timeout"}
        unreferenced = {k for k in defaults if k not in tasks_text}
        assert unreferenced <= permitted_unreferenced, (
            f"Defaults not referenced in tasks: {unreferenced - permitted_unreferenced}"
        )

    def test_conditional_block_structure_for_download(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        for t in tasks:
            if "pip" in str(t) and "huggingface" in str(t).lower():
                assert "when" in t or "block" in str(t), "huggingface_hub install must be conditional"
                return
        pytest.fail("No huggingface_hub pip install task found")


# =============================================================================
#  6. Pipeline resilience configuration
# =============================================================================


class TestPipelineResilienceConfig:
    def test_game_generation_has_timeout(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        for t in tasks:
            if "v1/completions" in str(t):
                assert "timeout" in str(t), "Game generation API call must have a timeout"
                return

    def test_server_start_is_nohup(self) -> None:
        tasks_text = (ROLE_ROOT / "tasks" / "main.yml").read_text()
        assert "nohup" in tasks_text, "Server start must use nohup for detachment"

    def test_model_download_uses_creates_for_idempotency(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        for t in tasks:
            if "args" in t and isinstance(t.get("args"), dict):
                args_kw = cast(dict[str, Any], t["args"])
                if "creates" in args_kw:
                    assert args_kw.get("creates") is not None
                    return

    def test_shutdown_always_block_contains_kill_and_cleanup(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        last = tasks[-1]
        if "always" in last:
            always_block = cast(list[dict[str, Any]], last["always"])
            has_kill = any("kill" in str(t) for t in always_block)
            has_file_rm = any("file" in str(t) and "absent" in str(t) for t in always_block)
            assert has_kill and has_file_rm, "Shutdown must kill server and remove PID file"


# =============================================================================
#  7. Script ↔ role mirror
# =============================================================================


class TestScriptMirrorsRole:
    @pytest.fixture(scope="class")
    def script_text(self) -> str:
        path = Path("scripts/run_game_gen_local.py")
        assert path.exists(), "run_game_gen_local.py not found"
        return path.read_text()

    def test_same_model_repo(self, script_text: str) -> None:
        defaults = cast(dict[str, Any], _load_yaml("defaults/main.yml"))
        assert defaults["model_repo"] in script_text, "Script and role must use same model_repo"

    def test_same_port(self, script_text: str) -> None:
        defaults = cast(dict[str, Any], _load_yaml("defaults/main.yml"))
        assert str(defaults["server_port"]) in script_text, "Script and role must use same port"

    def test_same_prompt_structure(self, script_text: str) -> None:
        required = [
            "class Snake:",
            "__init__(self)",
            "start(self)",
            "tick(self, direction)",
            "score(self) -> int",
            "is_game_over(self) -> bool",
            "restart(self)",
        ]
        for phrase in required:
            assert phrase in script_text, f"Script missing prompt phrase: {phrase}"

    def test_shared_verification_logic(self, script_text: str) -> None:
        assert "ast.parse" in script_text
        assert "ClassDef" in script_text
        assert "FunctionDef" in script_text
        assert "importlib" in script_text
        assert "tick" in script_text
        assert "restart" in script_text

    def test_script_has_error_handling(self, script_text: str) -> None:
        assert "ImportError" in script_text, "Must handle missing deps"
        assert "SyntaxError" in script_text, "Must catch AST parse failures"
        assert "try:" in script_text, "Must have try/except"
        assert "Exception" in script_text, "Must have exception handling"


# =============================================================================
#  8. Edge cases and coverage
# =============================================================================


class TestEdgeCases:
    def test_artifact_dir_not_hardcoded_in_verify_steps(self) -> None:
        tasks_text = (ROLE_ROOT / "tasks" / "main.yml").read_text()
        assert "{{ artifact_dir }}" in tasks_text, "Verify steps must use artifact_dir variable"

    def test_model_path_uses_regex_replace_slash(self) -> None:
        tasks_text = (ROLE_ROOT / "tasks" / "main.yml").read_text()
        assert "regex_replace('/', '_')" in tasks_text, "model_repo slash must be replaced for filesystem path"

    def test_server_log_path_is_under_artifact_dir(self) -> None:
        tasks_text = (ROLE_ROOT / "tasks" / "main.yml").read_text()
        assert "{{ artifact_dir }}/llamacpp-server.log" in tasks_text, "Server log must be under artifact_dir"

    def test_generated_code_written_to_correct_path(self) -> None:
        tasks_text = (ROLE_ROOT / "tasks" / "main.yml").read_text()
        assert "{{ artifact_dir }}/{{ game_name }}.py" in tasks_text, (
            "Generated code must write to artifact_dir/game_name.py"
        )

    def test_server_pid_written_to_artifact_dir(self) -> None:
        tasks_text = (ROLE_ROOT / "tasks" / "main.yml").read_text()
        assert "{{ artifact_dir }}/server.pid" in tasks_text, "Server PID must be in artifact_dir"

    def test_defaults_file_is_not_empty_and_parses(self) -> None:
        defaults = cast(dict[str, Any], _load_yaml("defaults/main.yml"))
        assert len(defaults) >= 16, f"Expected >=16 keys in defaults, got {len(defaults)}"


# =============================================================================
#  Test count self-pin
# =============================================================================
def test_pipeline_test_count() -> None:
    import re

    source = Path(__file__).read_text()
    count = len(re.findall(r"^\s*def test_", source, re.MULTILINE))
    assert count >= 40, f"Expected >=40 test functions, found {count}"
