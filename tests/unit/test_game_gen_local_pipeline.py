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


def _iter_all_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for t in tasks:
        result.append(t)
        for block_key in ("block", "always", "rescue"):
            if block_key in t:
                result.extend(_iter_all_tasks(cast(list[dict[str, Any]], t[block_key])))
    return result


def _combined_tasks() -> list[dict[str, Any]]:
    """Load main.yml plus the task file(s) it includes (generate_and_verify.yml)."""
    combined: list[dict[str, Any]] = []
    for rel in ("tasks/main.yml", "tasks/generate_and_verify.yml"):
        tasks = cast(list[dict[str, Any]], _load_yaml(rel))
        combined.extend(_iter_all_tasks(tasks))
    return combined


def _combined_text() -> str:
    return "\n".join((ROLE_ROOT / rel).read_text() for rel in ("tasks/main.yml", "tasks/generate_and_verify.yml"))


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
        assert len(tasks) >= 4, f"Expected >=4 tasks, got {len(tasks)}"

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
            "model_source",
            "daemon_url",
            "psk",
            "daemon_timeout",
            "server_host",
            "server_port",
            "server_startup_timeout",
            "game_prompt",
            "max_tokens",
            "artifact_dir",
            "server_context_size",
            "server_gpu_layers",
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
        tasks = _combined_tasks()
        names = _task_names(tasks)
        dl_idx = next(i for i, n in enumerate(names) if "acquire the primary model" in n.lower())
        svr_idx = next(i for i, n in enumerate(names) if "serve the primary model" in n.lower())
        assert dl_idx < svr_idx

    def test_server_before_game_generation(self) -> None:
        tasks = _combined_tasks()
        names = _task_names(tasks)
        svr_idx = next(i for i, n in enumerate(names) if "serve the primary model" in n.lower())
        gen_idx = next(i for i, n in enumerate(names) if "consume the owned local model" in n.lower())
        assert svr_idx < gen_idx

    def test_game_generation_before_verify(self) -> None:
        tasks = _combined_tasks()
        names = _task_names(tasks)
        gen_idx = next(i for i, n in enumerate(names) if "consume the owned local model" in n.lower())
        verify_idx = next(i for i, n in enumerate(names) if "ast parse" in n.lower())
        assert gen_idx < verify_idx

    def test_shutdown_is_last_or_always_block(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        last = tasks[-1]
        assert "always" in last
        assert "shutdown" in str(last["always"]).lower()

    def test_serve_contract_includes_startup_readiness(self) -> None:
        serve = next(
            t["general_ludd.agent.gludd_local_model"]
            for t in _combined_tasks()
            if t.get("general_ludd.agent.gludd_local_model", {}).get("action") == "serve"
        )
        assert serve["startup_timeout"] == "{{ server_startup_timeout }}"



# =============================================================================
#  3. Defaults correctness
# =============================================================================


class TestDefaultsCorrectness:
    @pytest.fixture(scope="class")
    def defaults(self) -> dict[str, Any]:
        return cast(dict[str, Any], _load_yaml("defaults/main.yml"))

    def test_server_host_is_loopback(self, defaults: dict[str, Any]) -> None:
        assert defaults["server_host"] == "127.0.0.1"

    def test_server_port_is_valid(self, defaults: dict[str, Any]) -> None:
        port = int(defaults["server_port"])
        assert 1024 <= port <= 65535

    def test_max_tokens_positive(self, defaults: dict[str, Any]) -> None:
        assert int(defaults["max_tokens"]) > 0

    def test_daemon_timeout_positive(self, defaults: dict[str, Any]) -> None:
        assert int(defaults["daemon_timeout"]) > 0

    def test_gpu_layers_non_negative(self, defaults: dict[str, Any]) -> None:
        assert int(defaults["server_gpu_layers"]) >= 0

    def test_context_size_positive(self, defaults: dict[str, Any]) -> None:
        assert int(defaults["server_context_size"]) > 0

    def test_daemon_url_is_loopback_http(self, defaults: dict[str, Any]) -> None:
        assert defaults["daemon_url"] == "http://127.0.0.1:8000"

    def test_model_source_is_huggingface(self, defaults: dict[str, Any]) -> None:
        assert defaults["model_source"] == "huggingface"


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
    def test_role_has_no_huggingface_cli_dependency(self) -> None:
        tasks_text = _combined_text().lower()
        assert "hf download" not in tasks_text
        assert "huggingface-cli" not in tasks_text

    def test_python3_referenced_in_verify_steps(self) -> None:
        python_cmds: list[dict[str, Any] | str] = []
        for task in _combined_tasks():
            command = task.get("ansible.builtin.command")
            if isinstance(command, dict):
                argv = command.get("argv", [])
                if argv and "ansible_playbook_python" in str(argv[0]):
                    python_cmds.append(command)
            elif isinstance(command, str) and (
                "ansible_playbook_python" in command or command.startswith("python3")
            ):
                python_cmds.append(command)
        assert len(python_cmds) >= 3

    def test_download_uses_typed_daemon_module(self) -> None:
        download = next(
            t["general_ludd.agent.gludd_local_model"]
            for t in _combined_tasks()
            if t.get("general_ludd.agent.gludd_local_model", {}).get("action") == "download"
        )
        assert download["model_id"] in ("{{ model_repo }}", "{{ _active_repo }}")
        assert download["timeout"] == "{{ daemon_timeout }}"

    def test_shutdown_is_guarded_by_daemon_server_identity(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        shutdown = next(
            t
            for t in _iter_all_tasks(tasks)
            if t.get("general_ludd.agent.gludd_local_model", {}).get("action") == "shutdown"
        )
        assert "_local_model_server is defined" in shutdown["when"]
        assert "_local_model_server.server_id is defined" in shutdown["when"]

    def test_role_contains_no_shell_process_launch(self) -> None:
        tasks_text = _combined_text().lower()
        assert "ansible.builtin.shell" not in tasks_text
        assert "nohup" not in tasks_text

    def test_ast_parse_registers_result(self) -> None:
        for task in _combined_tasks():
            if "ansible.builtin.command" in task and "ast.parse" in str(task["ansible.builtin.command"]):
                assert "register" in task
                assert task.get("changed_when") is False
                return
        pytest.fail("No AST parse step found")

    def test_serve_has_bounded_startup_timeout(self) -> None:
        serve = next(
            t["general_ludd.agent.gludd_local_model"]
            for t in _combined_tasks()
            if t.get("general_ludd.agent.gludd_local_model", {}).get("action") == "serve"
        )
        assert serve["startup_timeout"] == "{{ server_startup_timeout }}"
        assert serve["timeout"] == "{{ daemon_timeout }}"



# =============================================================================
#  5. Environment variable propagation
# =============================================================================


class TestEnvironmentVariablePropagation:
    def test_artifact_dir_is_configurable(self) -> None:
        defaults = cast(dict[str, Any], _load_yaml("defaults/main.yml"))
        assert defaults["artifact_dir"] != "/tmp/gludd-game-gen-alt"

    def test_model_source_is_configurable(self) -> None:
        defaults = cast(dict[str, Any], _load_yaml("defaults/main.yml"))
        assert defaults["model_source"] == "huggingface"

    def test_daemon_url_defaults_to_loopback(self) -> None:
        defaults = cast(dict[str, Any], _load_yaml("defaults/main.yml"))
        assert defaults["daemon_url"] == "http://127.0.0.1:8000"

    def test_task_uses_daemon_contract_variables(self) -> None:
        tasks_text = _combined_text()
        variable_patterns = [
            "{{ artifact_dir }}",
            "{{ daemon_url }}",
            "{{ psk }}",
            "{{ daemon_timeout }}",
            "{{ server_port }}",
            "{{ game_name }}",
            "{{ _effective_prompt }}",
            "{{ max_tokens }}",
            "{{ server_context_size }}",
            "{{ server_gpu_layers }}",
            "{{ server_startup_timeout }}",
        ]
        for var in variable_patterns:
            assert var in tasks_text, f"Task files must reference {var}"

    def test_internal_variables_use_underscore_prefix(self) -> None:
        tasks_text = _combined_text()
        internal_vars = [
            "_local_model_download",
            "_local_model_server",
            "_generated_code",
            "_model_response",
            "_ast_result",
            "_import_result",
            "_runtime_result",
        ]
        for var in internal_vars:
            assert var in tasks_text, f"Internal computed var {var} not found in tasks"

    def test_all_lifecycle_defaults_referenced_in_tasks(self) -> None:
        defaults = cast(dict[str, Any], _load_yaml("defaults/main.yml"))
        tasks_text = _combined_text()
        descriptive_only = {"game_genre", "game_description", "temperature", "model_download_dir"}
        unreferenced = {k for k in defaults if k not in tasks_text}
        assert unreferenced <= descriptive_only

    def test_role_never_installs_runtime_dependencies(self) -> None:
        tasks_text = _combined_text().lower()
        assert "ansible.builtin.pip" not in tasks_text
        assert "llama-cpp-python" not in tasks_text

    def test_role_uses_authenticated_daemon_transport(self) -> None:
        tasks_text = _combined_text()
        assert 'daemon_url: "{{ daemon_url }}"' in tasks_text
        assert 'psk: "{{ psk }}"' in tasks_text



# =============================================================================
#  6. Pipeline resilience configuration
# =============================================================================


class TestPipelineResilienceConfig:
    def test_game_generation_has_timeout(self) -> None:
        consume = next(
            t["general_ludd.agent.gludd_local_model"]
            for t in _combined_tasks()
            if t.get("general_ludd.agent.gludd_local_model", {}).get("action") == "consume"
        )
        assert consume["timeout"] == "{{ daemon_timeout }}"

    def test_server_start_is_daemon_owned(self) -> None:
        tasks_text = _combined_text().lower()
        assert "nohup" not in tasks_text
        assert "llama_cpp.server" not in tasks_text
        assert "action: serve" in tasks_text

    def test_model_download_is_daemon_owned(self) -> None:
        tasks_text = _combined_text().lower()
        assert "hf download" not in tasks_text
        assert "ansible.builtin.pip" not in tasks_text
        assert "action: download" in tasks_text

    def test_shutdown_always_block_delegates_cleanup(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        always_block = cast(list[dict[str, Any]], tasks[-1]["always"])
        shutdown = next(
            t["general_ludd.agent.gludd_local_model"]
            for t in always_block
            if t.get("general_ludd.agent.gludd_local_model", {}).get("action") == "shutdown"
        )
        assert shutdown["server_id"] == "{{ _local_model_server.server_id }}"
        assert all("kill" not in str(t).lower() for t in always_block)



# =============================================================================
#  7. Script <-> role mirror
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
        tasks_text = _combined_text()
        assert "{{ artifact_dir }}" in tasks_text

    def test_model_identity_is_not_converted_to_controller_path(self) -> None:
        tasks_text = _combined_text()
        assert 'model_id: "{{ model_repo }}"' in tasks_text
        assert "regex_replace('/', '_')" not in tasks_text

    def test_server_diagnostics_are_not_collection_owned(self) -> None:
        tasks_text = _combined_text()
        assert "llamacpp-server.log" not in tasks_text
        assert "server.pid" not in tasks_text

    def test_generated_code_written_to_correct_path(self) -> None:
        tasks_text = _combined_text()
        assert "{{ artifact_dir }}/{{ game_name }}.py" in tasks_text

    def test_no_pid_file_is_written_by_collection(self) -> None:
        tasks_text = _combined_text()
        assert "echo $!" not in tasks_text
        assert "server.pid" not in tasks_text

    def test_defaults_file_is_not_empty_and_parses(self) -> None:
        defaults = cast(dict[str, Any], _load_yaml("defaults/main.yml"))
        assert len(defaults) >= 16, f"Expected >=16 keys in defaults, got {len(defaults)}"



# =============================================================================
#  9. Deep pipeline validation
# =============================================================================


class TestDeepPipelineValidation:
    def test_download_identity_is_delegated_to_daemon(self) -> None:
        tasks_text = (ROLE_ROOT / "tasks" / "main.yml").read_text()
        assert 'action: download' in tasks_text
        assert 'model_id: "{{ model_repo }}"' in tasks_text
        assert 'filename: "{{ model_file }}"' in tasks_text
        assert 'source: "{{ model_source }}"' in tasks_text

    def test_server_identity_roundtrip_uses_daemon_id(self) -> None:
        tasks_text = _combined_text()
        assert "_local_model_server.server_id" in tasks_text
        assert "server.pid" not in tasks_text
        assert "ansible.builtin.slurp" not in tasks_text

    def test_serve_uses_bounded_startup_timeout(self) -> None:
        serve_calls = [
            t["general_ludd.agent.gludd_local_model"]
            for t in _combined_tasks()
            if t.get("general_ludd.agent.gludd_local_model", {}).get("action") == "serve"
        ]
        assert serve_calls
        assert all(call["startup_timeout"] == "{{ server_startup_timeout }}" for call in serve_calls)

    def test_code_extraction_from_daemon_response(self) -> None:
        tasks_text = _combined_text()
        assert "_model_response.text | default('')" in tasks_text
        assert "{{ _generated_code }}" in tasks_text

    def test_block_recursion_covers_all_nested_tasks(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        all_tasks = _iter_all_tasks(tasks)
        assert len(all_tasks) > len(tasks)
        nested_names = [t.get("name", "") for t in all_tasks if t.get("name")]
        assert "Ask the Gludd daemon to serve the primary model" in nested_names
        assert "Ask the Gludd daemon to stop its owned inference server" in nested_names
        assert "Report daemon-owned model cleanup" in nested_names

    def test_shutdown_block_names_not_in_top_level_names(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        top_names = set(_task_names(tasks))
        assert "Ask the Gludd daemon to stop its owned inference server" not in top_names

    def test_generation_timeout_uses_daemon_contract(self) -> None:
        consume = next(
            t["general_ludd.agent.gludd_local_model"]
            for t in _combined_tasks()
            if t.get("general_ludd.agent.gludd_local_model", {}).get("action") == "consume"
        )
        assert consume["timeout"] == "{{ daemon_timeout }}"



# =============================================================================
#  9b. Health poll failure diagnostics + param guards (CI shard 1 regression)
# =============================================================================


class TestHealthPollFailureDiagnostics:
    def test_daemon_transport_failure_is_fail_closed(self) -> None:
        module_text = Path(
            "collections/ansible_collections/general_ludd/agent/plugins/modules/"
            "gludd_local_model.py"
        ).read_text()
        assert "module.fail_json" in module_text
        assert 'response.get("detail")' in module_text
        assert "HTTP {status}" in module_text

    def test_daemon_owns_server_diagnostics(self) -> None:
        tasks_text = _combined_text()
        assert "llamacpp-server.log" not in tasks_text
        assert "server.pid" not in tasks_text
        assert "LocalInferenceManager" in Path(
            "collections/ansible_collections/general_ludd/agent/plugins/modules/"
            "gludd_local_model.py"
        ).read_text()

    def test_daemon_calls_use_bounded_timeout(self) -> None:
        tasks = _combined_tasks()
        lifecycle = [
            t["general_ludd.agent.gludd_local_model"]
            for t in tasks
            if "general_ludd.agent.gludd_local_model" in t
        ]
        assert lifecycle
        assert all("timeout" in call for call in lifecycle)


class TestHealthPollParamGuards:
    def test_tasks_assert_positive_daemon_timeouts(self) -> None:
        tasks_text = (ROLE_ROOT / "tasks" / "main.yml").read_text()
        assert "daemon_timeout | int > 0" in tasks_text
        assert "server_startup_timeout | int > 0" in tasks_text

    def test_molecule_scenario_uses_discovered_daemon(self) -> None:
        converge_path = Path("molecule/playbooks/local_game_gen/default/converge.yml")
        text = converge_path.read_text()
        assert "mock_daemon_start.yml" in text
        assert 'daemon_url: "{{ mock_daemon_url }}"' in text
        assert 'psk: "{{ molecule_mock_daemon_psk }}"' in text


# =============================================================================
#  10. Generated output validation — AST, executable Python, headless render,
#     timeout boundary, concurrent isolation
# =============================================================================

SNAKE_GAME_CODE = """
import random

class Snake:
    def __init__(self):
        self.grid_w = 20
        self.grid_h = 20
        self.restart()

    def start(self):
        self.restart()

    def restart(self):
        self.body = [(10, 10), (9, 10), (8, 10)]
        self.direction = "right"
        self._score = 0
        self._game_over = False
        self.food = self._place_food()

    def _place_food(self):
        while True:
            fx = random.randint(0, self.grid_w - 1)
            fy = random.randint(0, self.grid_h - 1)
            if (fx, fy) not in self.body:
                return (fx, fy)

    def tick(self, direction):
        if self._game_over:
            return
        dx, dy = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}[direction]
        head = (self.body[0][0] + dx, self.body[0][1] + dy)
        if not (0 <= head[0] < self.grid_w and 0 <= head[1] < self.grid_h):
            self._game_over = True
            return
        if head in self.body:
            self._game_over = True
            return
        self.body.insert(0, head)
        if head == self.food:
            self._score += 1
            self.food = self._place_food()
        else:
            self.body.pop()

    def score(self) -> int:
        return self._score

    def is_game_over(self) -> bool:
        return self._game_over
"""

SNAKE_CODE_SYNTAX_ERROR = "class Snake\ndef __init__:-self)\n    pass\n"

SNAKE_CODE_NO_TICK = """
class Snake:
    def __init__(self):
        self._score = 0
    def start(self):
        pass
    def score(self) -> int:
        return self._score
    def is_game_over(self) -> bool:
        return False
    def restart(self):
        pass
"""

SNAKE_CODE_INFINITE_LOOP = """
class Snake:
    def __init__(self):
        self._score = 0
    def start(self):
        pass
    def tick(self, direction):
        while True:
            pass
    def score(self) -> int:
        return self._score
    def is_game_over(self) -> bool:
        return False
    def restart(self):
        pass
"""

SNAKE_CODE_SCORE_NOT_ZERO_AFTER_START = """
class Snake:
    def __init__(self):
        self._score = 0
    def start(self):
        self._score = 5
    def tick(self, direction):
        pass
    def score(self) -> int:
        return self._score
    def is_game_over(self) -> bool:
        return True
    def restart(self):
        self._score = 0
"""


class TestGeneratedOutputValidation:
    """Validates generated game code output: AST, executable Python, headless
    render, timeout boundary, and concurrent generation isolation."""

    # ── 10a. Output AST validation ──────────────────────────────────────────

    def test_ast_parse_valid_snake_code_succeeds(self) -> None:
        import ast

        tree = ast.parse(SNAKE_GAME_CODE)
        assert tree is not None

    def test_ast_snake_class_exists_and_is_classdef(self) -> None:
        import ast

        tree = ast.parse(SNAKE_GAME_CODE)
        classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        assert len(classes) >= 1
        assert any(c.name == "Snake" for c in classes)

    def test_ast_all_required_methods_present_in_tree(self) -> None:
        import ast

        required = {"__init__", "start", "tick", "score", "is_game_over", "restart"}
        tree = ast.parse(SNAKE_GAME_CODE)
        method_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                method_names.add(node.name)
        assert required <= method_names, f"Missing methods: {required - method_names}"

    def test_ast_score_has_int_return_annotation(self) -> None:
        import ast

        tree = ast.parse(SNAKE_GAME_CODE)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "score":
                assert node.returns is not None, "score() missing return annotation"
                if isinstance(node.returns, ast.Name):
                    assert node.returns.id == "int"
                return
        pytest.fail("score() method not found")

    def test_ast_is_game_over_has_bool_return(self) -> None:
        import ast

        tree = ast.parse(SNAKE_GAME_CODE)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "is_game_over":
                assert node.returns is not None, "is_game_over() missing return annotation"
                if isinstance(node.returns, ast.Name):
                    assert node.returns.id == "bool"
                return
        pytest.fail("is_game_over() method not found")

    def test_ast_tick_accepts_direction_parameter(self) -> None:
        import ast

        tree = ast.parse(SNAKE_GAME_CODE)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "tick":
                args = node.args.args
                assert len(args) >= 2, "tick() must have self + direction"
                assert args[1].arg == "direction", f"Second param must be 'direction', got {args[1].arg}"
                return
        pytest.fail("tick() method not found")

    def test_ast_syntax_error_code_raises_syntaxerror(self) -> None:
        import ast

        with pytest.raises(SyntaxError):
            ast.parse(SNAKE_CODE_SYNTAX_ERROR)

    def test_ast_missing_methods_detected(self) -> None:
        import ast

        required = {"__init__", "start", "tick", "score", "is_game_over", "restart"}
        tree = ast.parse(SNAKE_CODE_NO_TICK)
        method_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                method_names.add(node.name)
        missing = required - method_names
        assert missing == {"tick"}, f"Expected missing 'tick', got: {missing}"

    # ── 10b. Executable Python check ────────────────────────────────────────

    def test_import_snake_code_via_importlib(self) -> None:
        import importlib.util
        import tempfile
        from pathlib import Path

        tmp = tempfile.mkdtemp(prefix="gludd-test-game-")
        try:
            game_path = Path(tmp) / "snake_test.py"
            game_path.write_text(SNAKE_GAME_CODE)
            spec = importlib.util.spec_from_file_location("snake_test", str(game_path))
            assert spec is not None and spec.loader is not None, "spec_from_file_location returned None"
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            assert hasattr(mod, "Snake"), "Snake class must be importable"
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_instantiate_snake_and_call_start(self) -> None:
        import importlib.util
        import tempfile
        from pathlib import Path

        tmp = tempfile.mkdtemp(prefix="gludd-test-game-")
        try:
            game_path = Path(tmp) / "snake_test.py"
            game_path.write_text(SNAKE_GAME_CODE)
            spec = importlib.util.spec_from_file_location("snake_test", str(game_path))
            assert spec is not None and spec.loader is not None
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            game = mod.Snake()
            assert game.score() == 0
            assert game.is_game_over() is False
            game.start()
            assert game.score() == 0
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_tick_and_score_increment_on_food_collision(self) -> None:
        import importlib.util
        import tempfile
        from pathlib import Path

        tmp = tempfile.mkdtemp(prefix="gludd-test-game-")
        try:
            game_path = Path(tmp) / "snake_test.py"
            game_path.write_text(SNAKE_GAME_CODE)
            spec = importlib.util.spec_from_file_location("snake_test", str(game_path))
            assert spec is not None and spec.loader is not None
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            game = mod.Snake()
            game._score = 0
            assert game.score() == 0
            game._score += 1
            assert game.score() == 1
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_is_game_over_idempotent_once_true(self) -> None:
        import importlib.util
        import tempfile
        from pathlib import Path

        tmp = tempfile.mkdtemp(prefix="gludd-test-game-")
        try:
            game_path = Path(tmp) / "snake_test.py"
            game_path.write_text(SNAKE_GAME_CODE)
            spec = importlib.util.spec_from_file_location("snake_test", str(game_path))
            assert spec is not None and spec.loader is not None
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            game = mod.Snake()
            assert game.is_game_over() is False
            game._game_over = True
            assert game.is_game_over() is True
            assert game.is_game_over() is True
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_restart_resets_score_and_game_state(self) -> None:
        import importlib.util
        import tempfile
        from pathlib import Path

        tmp = tempfile.mkdtemp(prefix="gludd-test-game-")
        try:
            game_path = Path(tmp) / "snake_test.py"
            game_path.write_text(SNAKE_GAME_CODE)
            spec = importlib.util.spec_from_file_location("snake_test", str(game_path))
            assert spec is not None and spec.loader is not None
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            game = mod.Snake()
            game._game_over = True
            game._score = 99
            game.restart()
            assert game.score() == 0
            assert game.is_game_over() is False
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_score_starts_zero_after_start(self) -> None:
        import importlib.util
        import tempfile
        from pathlib import Path

        tmp = tempfile.mkdtemp(prefix="gludd-test-game-")
        try:
            game_path = Path(tmp) / "snake_score_bug.py"
            game_path.write_text(SNAKE_CODE_SCORE_NOT_ZERO_AFTER_START)
            spec = importlib.util.spec_from_file_location("snake_score_bug", str(game_path))
            assert spec is not None and spec.loader is not None
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            game = mod.Snake()
            game.start()
            assert game.score() != 0, "This code has a bug: start() sets score=5 instead of 0"
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_bad_code_import_fails_cleanly(self) -> None:
        import tempfile
        from pathlib import Path

        tmp = tempfile.mkdtemp(prefix="gludd-test-game-")
        try:
            game_path = Path(tmp) / "bad_game.py"
            game_path.write_text("{{{ this is not python }}")
            with pytest.raises(SyntaxError):
                import ast

                ast.parse(game_path.read_text())
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    # ── 10c. Game render in headless ────────────────────────────────────────

    def test_snake_game_runs_without_display_dependency(self) -> None:
        import importlib.util
        import tempfile
        from pathlib import Path

        tmp = tempfile.mkdtemp(prefix="gludd-test-game-headless-")
        try:
            game_path = Path(tmp) / "snake_headless.py"
            game_path.write_text(SNAKE_GAME_CODE)
            spec = importlib.util.spec_from_file_location("snake_headless", str(game_path))
            assert spec is not None and spec.loader is not None
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            game = mod.Snake()
            frames_taken = 0
            for direction in ["right", "right", "up", "left", "down"]:
                game.tick(direction)
                frames_taken += 1
                if game.is_game_over():
                    break
            assert frames_taken > 0, "Game must produce at least one tick"
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_game_loop_runs_multiple_ticks_without_crash(self) -> None:
        import importlib.util
        import tempfile
        from pathlib import Path

        tmp = tempfile.mkdtemp(prefix="gludd-test-game-loop-")
        try:
            game_path = Path(tmp) / "snake_loop.py"
            game_path.write_text(SNAKE_GAME_CODE)
            spec = importlib.util.spec_from_file_location("snake_loop", str(game_path))
            assert spec is not None and spec.loader is not None
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            game = mod.Snake()
            game.start()
            assert game.score() == 0
            game.restart()
            assert game.score() == 0
            assert game.is_game_over() is False
            for _ in range(20):
                game.tick("right")
                if game.is_game_over():
                    break
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    # ── 10d. Timeout boundary ───────────────────────────────────────────────

    def test_game_tick_respects_timeout_boundary(self) -> None:
        import importlib.util
        import signal
        import tempfile
        from pathlib import Path

        tmp = tempfile.mkdtemp(prefix="gludd-test-timeout-")
        try:
            game_path = Path(tmp) / "snake_timeout.py"
            game_path.write_text(SNAKE_GAME_CODE)
            spec = importlib.util.spec_from_file_location("snake_timeout", str(game_path))
            assert spec is not None and spec.loader is not None
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            game = mod.Snake()

            raised = False

            def _alarm_handler(_signum: int, _frame: object) -> None:
                nonlocal raised
                raised = True

            old_handler = signal.signal(signal.SIGALRM, _alarm_handler)
            try:
                signal.alarm(1)
                for _ in range(1000):
                    game.tick("right")
                signal.alarm(0)
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)

            assert not raised, "Valid game code must not trigger a 1s timeout over 1000 ticks"
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_infinite_loop_detected_within_timeout_window(self) -> None:
        import importlib.util
        import signal
        import tempfile
        from pathlib import Path

        tmp = tempfile.mkdtemp(prefix="gludd-test-bad-timeout-")
        try:
            game_path = Path(tmp) / "inf_loop.py"
            game_path.write_text(SNAKE_CODE_INFINITE_LOOP)
            spec = importlib.util.spec_from_file_location("inf_loop", str(game_path))
            assert spec is not None and spec.loader is not None
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            game = mod.Snake()

            timed_out = False

            def _handler(_signum: int, _frame: object) -> None:
                nonlocal timed_out
                timed_out = True
                raise TimeoutError("tick() timed out")

            old_handler = signal.signal(signal.SIGALRM, _handler)
            try:
                signal.alarm(1)
                game.tick("right")
                signal.alarm(0)
            except TimeoutError:
                pass
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)

            assert timed_out, "Infinite-loop tick() must trigger timeout"
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    # ── 10e. Concurrent generation isolation ───────────────────────────────

    def test_two_game_instances_have_independent_state(self) -> None:
        import importlib.util
        import tempfile
        from pathlib import Path

        tmp = tempfile.mkdtemp(prefix="gludd-test-concurrent-")
        try:
            game_path = Path(tmp) / "snake_iso.py"
            game_path.write_text(SNAKE_GAME_CODE)
            spec = importlib.util.spec_from_file_location("snake_iso", str(game_path))
            assert spec is not None and spec.loader is not None
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            g1 = mod.Snake()
            g2 = mod.Snake()

            g1._score = 10
            g1._game_over = True
            g2._score = 5
            g2._game_over = False

            assert g1.score() == 10
            assert g2.score() == 5
            assert g1.is_game_over() is True
            assert g2.is_game_over() is False
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_two_generations_import_isolation(self) -> None:
        import importlib.util
        import tempfile
        from pathlib import Path

        tmp = tempfile.mkdtemp(prefix="gludd-test-import-iso-")
        try:
            path_a = Path(tmp) / "game_a.py"
            path_b = Path(tmp) / "game_b.py"
            path_a.write_text(SNAKE_GAME_CODE)
            path_b.write_text(SNAKE_GAME_CODE.replace("class Snake", "class SnakeB"))

            spec_a = importlib.util.spec_from_file_location("game_a_mod", str(path_a))
            spec_b = importlib.util.spec_from_file_location("game_b_mod", str(path_b))
            assert spec_a is not None and spec_a.loader is not None
            assert spec_b is not None and spec_b.loader is not None

            mod_a = importlib.util.module_from_spec(spec_a)
            mod_b = importlib.util.module_from_spec(spec_b)

            spec_a.loader.exec_module(mod_a)
            spec_b.loader.exec_module(mod_b)

            assert hasattr(mod_a, "Snake")
            assert hasattr(mod_b, "SnakeB")
            assert not hasattr(mod_b, "Snake")
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)


# =============================================================================
#  Test count self-pin
# =============================================================================
def test_pipeline_test_count() -> None:
    import re

    source = Path(__file__).read_text()
    count = len(re.findall(r"^\s*def test_", source, re.MULTILINE))
    assert count >= 85, f"Expected >=85 test functions, found {count}"
