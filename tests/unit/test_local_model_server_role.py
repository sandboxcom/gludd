from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
import yaml

ROLE_ROOT = Path("collections/ansible_collections/general_ludd/agent/roles/local_model_server")


def _load_yaml(rel: str) -> Any:
    path = ROLE_ROOT / rel
    assert path.exists(), f"Missing role file: {path}"
    with open(path) as fh:
        doc = yaml.safe_load(fh)
    assert doc is not None, f"Empty/unparseable YAML in {path}"
    return doc


def _task_names(tasks: list[dict[str, Any]]) -> list[str]:
    return [t.get("name", "(unnamed)") for t in tasks]


def _iter_all_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for t in tasks:
        result.append(t)
        for block_key in ("block", "always", "rescue"):
            if block_key in t:
                result.extend(_iter_all_tasks(cast(list[dict[str, Any]], t[block_key])))
    return result


# =============================================================================
#  1. Role structure
# =============================================================================


class TestRoleStructure:
    def test_role_root_has_required_dirs(self) -> None:
        for sub in ("tasks", "defaults", "meta"):
            assert (ROLE_ROOT / sub).is_dir(), f"Missing role subdirectory: {sub}"

    def test_tasks_main_yml_is_non_empty_list(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        assert isinstance(tasks, list), "tasks/main.yml must be a list"
        assert len(tasks) >= 8, f"Expected >=8 tasks, got {len(tasks)}"

    def test_every_task_has_name(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        for i, t in enumerate(tasks):
            assert "name" in t, f"Task at index {i} missing a 'name' key"

    def test_task_names_are_unique(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        names = _task_names(tasks)
        assert len(names) == len(set(names)), f"Duplicate task names: {names}"

    def test_meta_has_required_galaxy_fields(self) -> None:
        meta = cast(dict[str, Any], _load_yaml("meta/main.yml"))
        gi = meta["galaxy_info"]
        for field in ("role_name", "author", "description", "license", "min_ansible_version"):
            assert field in gi, f"meta galaxy_info missing: {field}"
        assert gi["role_name"] == "local_model_server"


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

    def test_input_validation_checks_model_repo_and_file(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        that_clause = tasks[0]["ansible.builtin.assert"]["that"]
        checks = [str(item).strip() for item in that_clause]
        assert any("model_repo" in c for c in checks), "Must validate model_repo"
        assert any("model_file" in c for c in checks), "Must validate model_file"

    def test_dirs_created_before_download(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        names = _task_names(tasks)
        dir_idx = next(i for i, n in enumerate(names) if "artifact" in n.lower() and "exist" in n.lower())
        dl_idx = next(i for i, n in enumerate(names) if "download" in n.lower())
        assert dir_idx < dl_idx, f"Artifact dir (idx {dir_idx}) must precede download (idx {dl_idx})"

    def test_download_before_server_start(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        names = _task_names(tasks)
        dl_idx = next(i for i, n in enumerate(names) if "download" in n.lower())
        svr_idx = next(i for i, n in enumerate(names) if "start" in n.lower() and "server" in n.lower())
        assert dl_idx < svr_idx, f"Download (idx {dl_idx}) must precede server start (idx {svr_idx})"

    def test_health_poll_between_server_start_and_shutdown(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        names = _task_names(tasks)
        svr_idx = next(i for i, n in enumerate(names) if "start" in n.lower() and "server" in n.lower())
        health_idx = next(i for i, n in enumerate(names) if "health" in n.lower() and "poll" in n.lower())
        stop_idx = next(i for i, n in enumerate(names) if "stop" in n.lower() or "shutdown" in n.lower())
        assert svr_idx < health_idx < stop_idx, (
            f"Health poll (idx {health_idx}) between server start (idx {svr_idx}) and shutdown (idx {stop_idx})"
        )

    def test_shutdown_is_last_with_always_block(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        last = tasks[-1]
        assert any(w in str(last).lower() for w in ("shutdown", "stop", "kill")), (
            f"Final task should be shutdown-related, got: {last.get('name', '?')}"
        )
        shutdown_text = yaml.dump(last)
        assert "always" in shutdown_text.lower(), "Shutdown must use always: block for guaranteed execution"


# =============================================================================
#  3. Defaults sanity
# =============================================================================


class TestDefaultsSanity:
    @pytest.fixture(scope="class")
    def defaults(self) -> dict[str, Any]:
        return cast(dict[str, Any], _load_yaml("defaults/main.yml"))

    def test_required_default_keys_exist(self, defaults: dict[str, Any]) -> None:
        required = [
            "model_repo",
            "model_file",
            "model_download_dir",
            "server_host",
            "server_port",
            "server_context_size",
            "server_gpu_layers",
            "artifact_dir",
            "health_check_retries",
            "health_check_delay",
        ]
        for key in required:
            assert key in defaults, f"Missing default key: {key}"

    def test_no_sensitive_defaults(self, defaults: dict[str, Any]) -> None:
        sensitive = {"api_key", "password", "secret", "credential", "token"}
        for key in defaults:
            lowered = key.lower()
            assert not any(s in lowered for s in sensitive), f"Sensitive-looking key: {key}"

    def test_server_host_is_loopback(self, defaults: dict[str, Any]) -> None:
        assert defaults["server_host"] == "127.0.0.1", "server_host must default to loopback"

    def test_server_port_is_valid(self, defaults: dict[str, Any]) -> None:
        port = int(defaults["server_port"])
        assert 1024 <= port <= 65535, f"Port {port} out of valid range"

    def test_context_size_positive(self, defaults: dict[str, Any]) -> None:
        assert int(defaults["server_context_size"]) > 0

    def test_gpu_layers_non_negative(self, defaults: dict[str, Any]) -> None:
        assert int(defaults["server_gpu_layers"]) >= 0

    def test_health_check_retries_positive(self, defaults: dict[str, Any]) -> None:
        assert int(defaults["health_check_retries"]) > 0

    def test_health_check_delay_positive(self, defaults: dict[str, Any]) -> None:
        assert int(defaults["health_check_delay"]) > 0

    def test_model_repo_format(self, defaults: dict[str, Any]) -> None:
        repo = defaults["model_repo"]
        assert "/" in repo, f"model_repo must be org/repo, got {repo!r}"
        assert len(repo.split("/")) == 2, f"model_repo must have exactly one slash, got {repo!r}"

    def test_model_file_ends_with_gguf(self, defaults: dict[str, Any]) -> None:
        assert defaults["model_file"].endswith(".gguf")

    def test_artifact_dir_is_under_tmp(self, defaults: dict[str, Any]) -> None:
        assert defaults["artifact_dir"].startswith("/tmp/"), (
            f"artifact_dir should be under /tmp/, got {defaults['artifact_dir']}"
        )


# =============================================================================
#  4. Pip install
# =============================================================================


class TestPipInstall:
    def test_pip_install_unconditional_and_idempotent(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        for t in tasks:
            if "ansible.builtin.pip" in t:
                assert "when" not in t, "pip install must be unconditional"
                pip_kw = cast(dict[str, Any], t["ansible.builtin.pip"])
                assert pip_kw.get("state") == "present", "pip install must be idempotent (state: present)"
                return
        pytest.fail("No top-level pip install task found")

    def test_pip_install_uses_server_extra(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        for t in tasks:
            if "ansible.builtin.pip" in t:
                pip_kw = cast(dict[str, Any], t["ansible.builtin.pip"])
                assert "llama-cpp-python[server]" in pip_kw["name"], (
                    "llama-cpp-python[server] must be in the pip install list: "
                    "the [server] extra declares the runtime deps llama_cpp.server "
                    "imports at module import (CI 2026-08-15: sse_starlette, "
                    "starlette_context ModuleNotFoundError)"
                )
                return
        pytest.fail("No top-level pip install task found")

    def test_pip_install_includes_hf_hub(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        for t in tasks:
            if "ansible.builtin.pip" in t:
                pip_kw = cast(dict[str, Any], t["ansible.builtin.pip"])
                names = cast(list[str], pip_kw["name"])
                assert "huggingface_hub" in names, "huggingface_hub must be installed"
                assert any(n.startswith("llama-cpp-python") for n in names), "llama-cpp-python must be installed"
                return
        pytest.fail("No top-level pip install task found")


# =============================================================================
#  5. Download guard
# =============================================================================


class TestDownloadGuard:
    def test_download_task_has_creates_guard(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        for t in _iter_all_tasks(tasks):
            cmd = t.get("ansible.builtin.command")
            if isinstance(cmd, str) and "hf download" in cmd:
                args_kw = t.get("args")
                assert isinstance(args_kw, dict), "Download task must have args"
                assert "creates" in args_kw, "Download should have args.creates for idempotency"
                return
        pytest.fail("No hf download command task found")

    def test_download_block_when_model_absent(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        for t in tasks:
            name = t.get("name", "")
            if "download" in name.lower() and "present" in name.lower():
                assert "when" in t, "Download-if-absent block must have a when clause"
                when_text = str(t["when"])
                assert "_model_path" in when_text, "when clause must reference _model_path"
                assert "not exists" in when_text, "when clause must test file absence"
                return
        pytest.fail("No 'download if not present' block task found")


# =============================================================================
#  6. Server start
# =============================================================================


class TestServerStart:
    def test_server_start_uses_nohup_and_pid_file(self) -> None:
        tasks_text = (ROLE_ROOT / "tasks" / "main.yml").read_text()
        assert "nohup" in tasks_text, "Server start must use nohup for detachment"
        assert "server.pid" in tasks_text, "Server PID file must be written"

    def test_server_start_uses_playbook_python(self) -> None:
        tasks_text = (ROLE_ROOT / "tasks" / "main.yml").read_text()
        assert "ansible_playbook_python" in tasks_text, "Server must run under the playbook python"

    def test_server_start_templates_context_and_gpu_layers(self) -> None:
        tasks_text = (ROLE_ROOT / "tasks" / "main.yml").read_text()
        assert "{{ server_context_size }}" in tasks_text, "Start command must use server_context_size"
        assert "{{ server_gpu_layers }}" in tasks_text, "Start command must use server_gpu_layers"

    def test_nohup_cmd_redirects_stderr(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        for t in tasks:
            if "ansible.builtin.shell" in t:
                cmd = t["ansible.builtin.shell"]
                assert "2>&1" in cmd or "nohup" not in cmd, "nohup'd cmd must redirect stderr"
                return


# =============================================================================
#  7. Health poll
# =============================================================================


class TestHealthPoll:
    def test_health_poll_has_until_retries_delay(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        for t in _iter_all_tasks(tasks):
            if "ansible.builtin.uri" in t and "/v1/models" in str(t.get("ansible.builtin.uri", "")).lower():
                assert "retries" in t, "Health poll missing retries"
                assert "delay" in t, "Health poll missing delay"
                assert "until" in t, "Health poll missing until condition"
                assert t.get("changed_when") is False, "Health poll is read-only"
                return
        pytest.fail("No models-URI poll task found")

    def test_health_poll_retries_and_delay_are_templated(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        for t in _iter_all_tasks(tasks):
            if "ansible.builtin.uri" in t and "/v1/models" in str(t.get("ansible.builtin.uri", "")).lower():
                assert t["retries"] == "{{ health_check_retries }}", "retries must use health_check_retries var"
                assert t["delay"] == "{{ health_check_delay }}", "delay must use health_check_delay var"
                return
        pytest.fail("No models-URI poll task found")

    def test_health_poll_url_uses_host_and_port_vars(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        for t in _iter_all_tasks(tasks):
            uri = t.get("ansible.builtin.uri")
            if isinstance(uri, dict) and "/v1/models" in str(uri.get("url", "")).lower():
                url = cast(str, uri["url"])
                assert "{{ server_host }}" in url, "Health URL must template server_host"
                assert "{{ server_port }}" in url, "Health URL must template server_port"
                return
        pytest.fail("No models-URI poll task found")

    def test_tasks_assert_positive_health_params(self) -> None:
        tasks_text = (ROLE_ROOT / "tasks" / "main.yml").read_text()
        assert "health_check_delay | int > 0" in tasks_text
        assert "health_check_retries | int > 0" in tasks_text


# =============================================================================
#  8. Rescue-with-log
# =============================================================================


class TestRescueWithLog:
    def test_poll_block_has_rescue_with_log_slurp(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        poll_block = next(t for t in tasks if t.get("name") == "Poll health endpoint until ready")
        assert "block" in poll_block, "Health poll must be wrapped in a block"
        rescue = cast(list[dict[str, Any]], poll_block.get("rescue"))
        assert rescue, "Health poll must have a rescue path"
        slurp_tasks = [t for t in rescue if "ansible.builtin.slurp" in t]
        assert slurp_tasks, "Rescue must slurp the server log"
        assert "{{ _server_log }}" in str(slurp_tasks[0]["ansible.builtin.slurp"]), "Rescue must slurp _server_log"

    def test_rescue_fail_msg_includes_server_log(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        poll_block = next(t for t in tasks if t.get("name") == "Poll health endpoint until ready")
        rescue = cast(list[dict[str, Any]], poll_block.get("rescue"))
        fail_tasks = [t for t in rescue if "ansible.builtin.fail" in t]
        assert fail_tasks, "Rescue must fail the play with a diagnostic message"
        msg = str(fail_tasks[0]["ansible.builtin.fail"])
        assert "_server_log" in msg, "Fail message must include the server log content"
        assert "b64decode" in msg, "Fail message must decode the slurped log content"


# =============================================================================
#  9. Shutdown always-block
# =============================================================================


class TestShutdownAlwaysBlock:
    def test_shutdown_always_contains_kill_and_pidfile_removal(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        last = tasks[-1]
        assert "always" in last, "Shutdown must be an always block"
        always_block = cast(list[dict[str, Any]], last["always"])
        has_kill = any("kill" in str(t) for t in always_block)
        has_file_rm = any("file" in str(t) and "absent" in str(t) for t in always_block)
        assert has_kill and has_file_rm, "Shutdown must kill server and remove PID file"

    def test_pid_slurp_ignores_errors(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        all_tasks = _iter_all_tasks(tasks)
        for t in all_tasks:
            if "ansible.builtin.slurp" in t and "pid" in str(t.get("ansible.builtin.slurp", "")).lower():
                assert t.get("ignore_errors") is True, "PID slurp must ignore_errors"
                return
        pytest.fail("No slurp task for PID file found")

    def test_kill_uses_term_signal(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        all_tasks = _iter_all_tasks(tasks)
        for t in all_tasks:
            cmd = t.get("ansible.builtin.command")
            if isinstance(cmd, str) and "kill" in cmd:
                assert "-TERM" in cmd, "Server kill must use SIGTERM"
                assert "b64decode" in cmd, "PID must be b64decoded before kill"
                return
        pytest.fail("No kill command task found")

    def test_shutdown_subtasks_not_top_level(self) -> None:
        tasks = cast(list[dict[str, Any]], _load_yaml("tasks/main.yml"))
        top_names = set(_task_names(tasks))
        shutdown_subtask_names = {"Read server PID", "Kill server process", "Remove PID file"}
        for name in shutdown_subtask_names:
            assert name not in top_names, f"Shutdown subtask '{name}' must be nested, not top-level"


# =============================================================================
#  10. Variable propagation
# =============================================================================


class TestVariablePropagation:
    def test_tasks_reference_all_defaults(self) -> None:
        defaults = cast(dict[str, Any], _load_yaml("defaults/main.yml"))
        tasks_text = (ROLE_ROOT / "tasks" / "main.yml").read_text()
        unreferenced = {k for k in defaults if k not in tasks_text}
        assert not unreferenced, f"Defaults not referenced in tasks: {unreferenced}"

    def test_internal_variables_use_underscore_prefix(self) -> None:
        tasks_text = (ROLE_ROOT / "tasks" / "main.yml").read_text()
        internal_vars = ["_model_path", "_server_log", "_server_pid", "_health_poll", "_server_log_slurp"]
        for var in internal_vars:
            assert var in tasks_text, f"Internal computed var {var} not found in tasks"

    def test_model_path_uses_regex_replace_slash(self) -> None:
        tasks_text = (ROLE_ROOT / "tasks" / "main.yml").read_text()
        assert "regex_replace('/', '_')" in tasks_text, "model_repo slash must be replaced for filesystem path"


# =============================================================================
#  11. Molecule scenario
# =============================================================================


class TestMoleculeScenario:
    def test_molecule_scenario_exists(self) -> None:
        scenario = Path("molecule/playbooks/local_model_server/default")
        assert (scenario / "converge.yml").exists(), "Molecule converge.yml missing"
        assert (scenario / "molecule.yml").exists(), "Molecule molecule.yml missing"

    def test_converge_gated_behind_env_var(self) -> None:
        converge_path = Path("molecule/playbooks/local_model_server/default/converge.yml")
        assert converge_path.exists(), "Molecule converge.yml missing"
        converge_text = converge_path.read_text()
        assert "MOLECULE_LIVE_MODEL" in converge_text, (
            "Converge must be gated behind MOLECULE_LIVE_MODEL so CI stays cheap"
        )

    def test_molecule_scenario_does_not_zero_delay(self) -> None:
        converge_path = Path("molecule/playbooks/local_model_server/default/converge.yml")
        converge_text = converge_path.read_text()
        assert "health_check_delay: 0" not in converge_text, (
            "Molecule scenario must not override health_check_delay to 0"
        )


# =============================================================================
#  Test count self-pin
# =============================================================================
def test_pipeline_test_count() -> None:
    import re

    source = Path(__file__).read_text()
    count = len(re.findall(r"^\s*def test_", source, re.MULTILINE))
    assert count >= 40, f"Expected >=40 test functions, found {count}"
