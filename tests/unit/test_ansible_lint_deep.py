"""Deep Ansible lint and best-practice tests for collection roles.

Covers: FQCN usage, command/shell idempotency, handler notification
integrity, variable naming conventions, role structure, and security.
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.xdist_group("ansible_lint_deep")

ROOT = Path(__file__).resolve().parents[2]
COLLECTIONS_ROOT = ROOT / "collections" / "ansible_collections"

MAX_YAML_FILE_SIZE = 512 * 1024

_discover_role_dirs_cache: list[Path] | None = None
_discover_task_files_cache: list[Path] | None = None


def test_yaml_lint_target_avoids_schema_network_and_fails_on_warnings() -> None:
    """The release lint must be hermetic and warning-clean on Python 3.14."""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    block = makefile.split("yaml-lint:", 1)[1].split("\n\n", 1)[0]

    assert "ANSIBLE_LINT_SKIP_SCHEMA_UPDATE=1" in block
    assert "PYTHONWARNINGS=error" in block


def _discover_role_dirs() -> list[Path]:
    global _discover_role_dirs_cache
    if _discover_role_dirs_cache is not None:
        return _discover_role_dirs_cache
    roles: list[Path] = []
    for coll in COLLECTIONS_ROOT.rglob("*/galaxy.yml"):
        coll_root = coll.parent
        for role_dir in sorted(coll_root.rglob("roles/*")):
            if role_dir.is_dir() and (role_dir / "tasks" / "main.yml").exists():
                roles.append(role_dir)
    _discover_role_dirs_cache = roles
    return roles


def _discover_task_files() -> list[Path]:
    global _discover_task_files_cache
    if _discover_task_files_cache is not None:
        return _discover_task_files_cache
    task_files: list[Path] = []
    for role in _discover_role_dirs():
        tasks_dir = role / "tasks"
        for tf in sorted(tasks_dir.rglob("*.yml")):
            if tf.is_file() and tf.stat().st_size <= MAX_YAML_FILE_SIZE:
                task_files.append(tf)
    _discover_task_files_cache = task_files
    return task_files


def _load_yaml(path: Path) -> Any:
    try:
        if path.stat().st_size > MAX_YAML_FILE_SIZE:
            return None
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _collect_task_module_names(task: dict[str, Any]) -> list[str]:
    module_names: list[str] = []
    known_directives = {
        "name",
        "when",
        "notify",
        "loop",
        "register",
        "vars",
        "tags",
        "ignore_errors",
        "become",
        "become_user",
        "become_method",
        "changed_when",
        "failed_when",
        "no_log",
        "check_mode",
        "delegate_to",
        "run_once",
        "environment",
        "any_errors_fatal",
        "async",
        "poll",
        "args",
        "delay",
        "retries",
        "until",
        "throttle",
        "serial",
        "order",
        "strategy",
        "loop_control",
        "with_items",
        "with_dict",
        "with_sequence",
        "with_fileglob",
        "with_together",
        "connection",
        "block",
        "rescue",
        "always",
    }
    for key in task:
        if key not in known_directives and isinstance(task[key], dict):
            module_names.append(key)
    return module_names


def _walk_tasks(
    tasks: Any,
    fn: Callable[..., None],
    *args: Any,
    **kwargs: Any,
) -> None:
    if not isinstance(tasks, list):
        return
    for idx, item in enumerate(tasks):
        if not isinstance(item, dict):
            continue
        fn(item, idx, *args, **kwargs)
        for block_key in ("block", "rescue", "always"):
            if block_key in item:
                _walk_tasks(item[block_key], fn, *args, **kwargs)


# ── Test 1: All module references use FQCN ──────────────────────────


def test_all_task_modules_use_fqcn() -> None:
    """Every module invocation in task files must use fully qualified
    collection name (e.g. 'ansible.builtin.command' not 'command')."""
    task_files = _discover_task_files()
    assert len(task_files) > 0, "No task files found to validate"
    violations: list[str] = []

    def _check(task: dict[str, Any], idx: int, tf: Path) -> None:
        for mod in _collect_task_module_names(task):
            if "." not in mod:
                violations.append(f"{tf.relative_to(ROOT)} task[{idx}] short name '{mod}' — use FQCN")

    for tf in task_files:
        tasks = _load_yaml(tf)
        if tasks is None or not isinstance(tasks, list):
            continue
        _walk_tasks(tasks, _check, tf)
    assert violations == [], f"{len(violations)} FQCN violations:\n" + "\n".join(f"  - {v}" for v in violations)


# ── Test 2: Every task has a name ───────────────────────────────────


def test_all_tasks_have_name() -> None:
    """Every task must have a 'name' key for readability and logging."""
    task_files = _discover_task_files()
    assert len(task_files) > 0, "No task files found"
    violations: list[str] = []
    skip_keys = {
        "name",
        "include_tasks",
        "ansible.builtin.include_tasks",
        "import_tasks",
        "ansible.builtin.import_tasks",
        "block",
        "rescue",
        "always",
    }

    def _check(task: dict[str, Any], idx: int, tf: Path) -> None:
        if "name" not in task and any(k not in skip_keys for k in task):
            violations.append(f"{tf.relative_to(ROOT)} task[{idx}] missing 'name'")

    for tf in task_files:
        tasks = _load_yaml(tf)
        if tasks is None or not isinstance(tasks, list):
            continue
        _walk_tasks(tasks, _check, tf)
    assert violations == [], f"{len(violations)} tasks missing 'name':\n" + "\n".join(f"  - {v}" for v in violations)


# ── Test 3: command/shell must have changed_when ────────────────────


def test_command_shell_tasks_have_changed_when() -> None:
    """Every ansible.builtin.command or ansible.builtin.shell task
    must explicitly set 'changed_when' to make idempotence explicit."""
    task_files = _discover_task_files()
    assert len(task_files) > 0, "No task files found"
    violations: list[str] = []
    cmd_keys = {"ansible.builtin.command", "command", "ansible.builtin.shell", "shell"}

    def _check(task: dict[str, Any], idx: int, tf: Path) -> None:
        for key in cmd_keys:
            if key in task and "changed_when" not in task:
                violations.append(f"{tf.relative_to(ROOT)} task[{idx}] uses '{key}' without 'changed_when'")

    for tf in task_files:
        tasks = _load_yaml(tf)
        if tasks is None or not isinstance(tasks, list):
            continue
        _walk_tasks(tasks, _check, tf)
    assert violations == [], f"{len(violations)} command/shell tasks missing changed_when:\n" + "\n".join(
        f"  - {v}" for v in violations
    )


# ── Test 4: shell not used for simple single commands ───────────────


def test_shell_module_not_abused_for_simple_commands() -> None:
    """ansible.builtin.shell should only be used when shell features
    (pipes, redirects, globs) are actually needed."""
    task_files = _discover_task_files()
    assert len(task_files) > 0, "No task files found"
    violations: list[str] = []
    meta_re = re.compile(r"[|;&$()`<>!\\*?\[\]{}\n]")

    def _check(task: dict[str, Any], idx: int, tf: Path) -> None:
        for sk in ("shell", "ansible.builtin.shell"):
            if sk in task:
                cmd = task[sk]
                if isinstance(cmd, dict):
                    cmd = cmd.get("cmd", "")
                if isinstance(cmd, str) and not meta_re.search(cmd):
                    violations.append(
                        f"{tf.relative_to(ROOT)} task[{idx}] uses '{sk}' for simple cmd — use ansible.builtin.command"
                    )
                break

    for tf in task_files:
        tasks = _load_yaml(tf)
        if tasks is None or not isinstance(tasks, list):
            continue
        _walk_tasks(tasks, _check, tf)
    assert violations == [], f"{len(violations)} shell→command opportunities:\n" + "\n".join(
        f"  - {v}" for v in violations
    )


# ── Test 5: Handler notifications reference existing handlers ───────


def test_handler_notifications_match_existing_handlers() -> None:
    """Every 'notify:' directive must reference a handler that exists
    in the role's handlers/main.yml."""
    roles = _discover_role_dirs()
    assert len(roles) > 0, "No roles found"
    violations: list[str] = []
    for role_dir in roles:
        handler_file = role_dir / "handlers" / "main.yml"
        handler_names: set[str] = set()
        if handler_file.exists():
            handlers = _load_yaml(handler_file)
            if handlers and isinstance(handlers, list):
                for h in handlers:
                    if isinstance(h, dict):
                        if "name" in h:
                            handler_names.add(h["name"])
                        if "listen" in h:
                            handler_names.add(h["listen"])

        tasks_dir = role_dir / "tasks"
        for tf in sorted(tasks_dir.rglob("*.yml")):
            if not tf.is_file():
                continue
            tasks = _load_yaml(tf)
            if tasks is None or not isinstance(tasks, list):
                continue

            def _check(
                task: dict[str, Any],
                _idx: int,
                _role: Path = role_dir,
                _hf: Path = handler_file,
                _names: set[str] = handler_names,
            ) -> None:
                if "notify" in task:
                    notifies = task["notify"]
                    if isinstance(notifies, str):
                        notifies = [notifies]
                    if isinstance(notifies, list):
                        for n in notifies:
                            if isinstance(n, str) and n not in _names:
                                if not _hf.exists():
                                    violations.append(
                                        f"{_role.relative_to(ROOT)}: notify '{n}' but handlers/main.yml missing"
                                    )
                                else:
                                    violations.append(
                                        f"{_role.relative_to(ROOT)}: "
                                        f"handler '{n}' not in "
                                        f"handlers/main.yml "
                                        f"(has: {sorted(_names)})"
                                    )

            _walk_tasks(tasks, _check)
    assert violations == [], f"{len(violations)} dangling handler notifications:\n" + "\n".join(
        f"  - {v}" for v in violations
    )


# ── Test 6: Roles without any role-prefixed variable are flagged ─────


def test_role_variables_use_namespaced_prefix() -> None:
    """Every role with defaults/main.yml should have at least ONE
    variable that uses the role name as a prefix (e.g. 'role__var'
    or 'role_var'). Roles with zero namespaced variables risk
    variable collisions at playbook level."""
    roles = _discover_role_dirs()
    assert len(roles) > 0, "No roles found"
    violations: list[str] = []
    for role_dir in roles:
        role_name = role_dir.name
        defaults_file = role_dir / "defaults" / "main.yml"
        if not defaults_file.exists():
            continue
        defaults = _load_yaml(defaults_file)
        if not isinstance(defaults, dict) or len(defaults) == 0:
            continue
        prefix = f"{role_name}_"
        any_prefixed = any(isinstance(k, str) and k.startswith(prefix) for k in defaults)
        if not any_prefixed:
            violations.append(
                f"{role_dir.relative_to(ROOT)}/defaults/main.yml: "
                f"no variable uses '{role_name}_' prefix "
                f"(vars: {sorted(defaults.keys())})"
            )
    assert len(violations) <= 204, (
        f"{len(violations)} roles with zero role-prefixed variables "
        f"(was 204 at baseline):\n" + "\n".join(f"  - {v}" for v in violations[:10])
    )


# ── Test 7: No hardcoded credentials in task files ──────────────────


def test_no_hardcoded_credentials_in_tasks() -> None:
    """Task files must not contain hardcoded passwords, API keys,
    or other secrets in plain text."""
    task_files = _discover_task_files()
    assert len(task_files) > 0, "No task files found"
    violations: list[str] = []
    patterns = [
        (re.compile(r"(?im)^\s*password:\s*(?!.*\{\{)\S+"), "password literal"),
        (re.compile(r"(?im)^\s*api_key:\s*(?!.*\{\{)\S+"), "api_key literal"),
        (re.compile(r'(?im)token:\s*"(?!.*\{\{)[^\"]{20,}"'), "hardcoded token"),
    ]
    for tf in task_files:
        if tf.stat().st_size > MAX_YAML_FILE_SIZE:
            continue
        content = tf.read_text(encoding="utf-8")
        for pat, desc in patterns:
            for match in pat.finditer(content):
                line_no = content[: match.start()].count("\n") + 1
                violations.append(f"{tf.relative_to(ROOT)}:{line_no}: {desc}")
    assert violations == [], f"{len(violations)} hardcoded credentials:\n" + "\n".join(f"  - {v}" for v in violations)


# ── Test 8: Task files are valid YAML lists ─────────────────────────


def test_task_files_are_valid_yaml_lists() -> None:
    """Every tasks/*.yml must parse as a YAML list at the top level."""
    task_files = _discover_task_files()
    assert len(task_files) > 0, "No task files found"
    violations: list[str] = []
    for tf in task_files:
        try:
            tasks = yaml.safe_load(tf.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            violations.append(f"{tf.relative_to(ROOT)}: parse error: {e}")
            continue
        if tasks is not None and not isinstance(tasks, list):
            violations.append(f"{tf.relative_to(ROOT)}: top-level is {type(tasks).__name__}, expected list")
    assert violations == [], f"{len(violations)} malformed task files:\n" + "\n".join(f"  - {v}" for v in violations)


# ── Test 9: Role directory structure completeness ───────────────────


def test_role_has_required_directories() -> None:
    """Every role must have tasks/, meta/, and defaults/ main.yml files."""
    roles = _discover_role_dirs()
    assert len(roles) > 0, "No roles found"
    violations: list[str] = []
    required = ["tasks", "meta", "defaults"]
    for role_dir in roles:
        for sub in required:
            p = role_dir / sub / "main.yml"
            if not p.exists():
                violations.append(f"{role_dir.relative_to(ROOT)}: missing {sub}/main.yml")
    assert violations == [], f"{len(violations)} roles missing required files:\n" + "\n".join(
        f"  - {v}" for v in violations
    )


# ── Test 10: meta/main.yml has galaxy_info ──────────────────────────


def test_meta_main_yml_has_galaxy_info() -> None:
    """meta/main.yml must contain 'galaxy_info'."""
    roles = _discover_role_dirs()
    assert len(roles) > 0, "No roles found"
    violations: list[str] = []
    for role_dir in roles:
        meta_file = role_dir / "meta" / "main.yml"
        if not meta_file.exists():
            continue
        meta = _load_yaml(meta_file)
        if not isinstance(meta, dict):
            violations.append(f"{meta_file.relative_to(ROOT)}: not a dict")
            continue
        if "galaxy_info" not in meta:
            violations.append(f"{meta_file.relative_to(ROOT)}: missing 'galaxy_info'")
    assert violations == [], f"{len(violations)} meta/main.yml issues:\n" + "\n".join(f"  - {v}" for v in violations)


# ── Test 11: defaults/main.yml is dict with string keys ─────────────


def test_defaults_main_yml_is_dict_with_string_keys() -> None:
    """defaults/main.yml must be a mapping with string keys."""
    roles = _discover_role_dirs()
    assert len(roles) > 0, "No roles found"
    violations: list[str] = []
    for role_dir in roles:
        df = role_dir / "defaults" / "main.yml"
        if not df.exists():
            continue
        defaults = _load_yaml(df)
        if not isinstance(defaults, dict):
            violations.append(f"{df.relative_to(ROOT)}: not a dict")
            continue
        for k in defaults:
            if not isinstance(k, str):
                violations.append(f"{df.relative_to(ROOT)}: non-string key '{k}'")
    assert violations == [], f"{len(violations)} defaults/main.yml issues:\n" + "\n".join(
        f"  - {v}" for v in violations
    )


# ── Test 12: Handler names are descriptive ──────────────────────────


def test_handler_names_are_descriptive() -> None:
    """Handler names should be descriptive (>4 chars, contain letters)."""
    roles = _discover_role_dirs()
    assert len(roles) > 0, "No roles found"
    violations: list[str] = []
    for role_dir in roles:
        hf = role_dir / "handlers" / "main.yml"
        if not hf.exists():
            continue
        handlers = _load_yaml(hf)
        if not isinstance(handlers, list):
            continue
        for idx, h in enumerate(handlers):
            if not isinstance(h, dict) or "name" not in h:
                continue
            name = h["name"]
            if len(name) < 5:
                violations.append(f"{hf.relative_to(ROOT)}[{idx}]: '{name}' too short")
            if not any(c.isalpha() for c in name):
                violations.append(f"{hf.relative_to(ROOT)}[{idx}]: '{name}' no letters")
    assert violations == [], f"{len(violations)} handler naming issues:\n" + "\n".join(f"  - {v}" for v in violations)


# ── Test 13: Variable names use snake_case ───────────────────────────


def test_variable_names_use_snake_case() -> None:
    """Role variable names in defaults/main.yml should use snake_case."""
    roles = _discover_role_dirs()
    assert len(roles) > 0, "No roles found"
    violations: list[str] = []
    bad = re.compile(r"[A-Z\-]")
    for role_dir in roles:
        df = role_dir / "defaults" / "main.yml"
        if not df.exists():
            continue
        defaults = _load_yaml(df)
        if not isinstance(defaults, dict):
            continue
        for vname in defaults:
            if isinstance(vname, str) and bad.search(vname):
                violations.append(f"{df.relative_to(ROOT)}: '{vname}' not snake_case")
    assert violations == [], f"{len(violations)} non-snake_case vars:\n" + "\n".join(f"  - {v}" for v in violations)


# ── Test 14: Role directory names use snake_case ────────────────────


def test_role_directory_names_use_snake_case() -> None:
    """Role dir names should use snake_case (Ansible Galaxy convention)."""
    roles = _discover_role_dirs()
    assert len(roles) > 0, "No roles found"
    violations: list[str] = []
    bad = re.compile(r"[A-Z\-]")
    for role_dir in roles:
        if bad.search(role_dir.name):
            violations.append(f"{role_dir.relative_to(ROOT)}: '{role_dir.name}' not snake_case")
    assert violations == [], f"{len(violations)} non-snake_case role names:\n" + "\n".join(
        f"  - {v}" for v in violations
    )


# ── Test 15: File mode values are quoted octal strings ──────────────


def test_file_mode_permissions_use_quoted_octal() -> None:
    """File mode values must be quoted octal strings ('0644' not 0644)
    to prevent YAML octal misinterpretation."""
    task_files = _discover_task_files()
    assert len(task_files) > 0, "No task files found"
    violations: list[str] = []

    def _check(task: dict[str, Any], idx: int, tf: Path) -> None:
        if "mode" in task and isinstance(task["mode"], int):
            violations.append(f"{tf.relative_to(ROOT)} task[{idx}]: mode={task['mode']} int — use quoted octal string")

    for tf in task_files:
        tasks = _load_yaml(tf)
        if tasks is None or not isinstance(tasks, list):
            continue
        _walk_tasks(tasks, _check, tf)
    assert violations == [], f"{len(violations)} unquoted octal modes:\n" + "\n".join(f"  - {v}" for v in violations)


# ── Test 16: Bare variable usage with | default() ───────────────────


def test_task_jinja_expressions_dont_rely_on_undefined_vars() -> None:
    """Jinja expressions for variables not guaranteed defined should
    use the | default() filter (excluding ansible_* built-ins).

    Safe exemptions: role defaults/vars, register:, set_fact:,
    loop_control.loop_var, task-level vars:, Jinja2 ``{% set %}`` /
    ``{% for %}`` locals, and 1-2 character loop-var names.
    """
    import yaml as _yaml

    task_files = _discover_task_files()
    assert len(task_files) > 0, "No task files found"
    violations: list[str] = []
    bare = re.compile(
        r"\{\{\s*(?!ansible_|playbook_dir|item|inventory_hostname|play_hosts|"
        r"groups|hostvars|omit|role_path|networking__|_|__)"
        r"([a-z][a-z0-9_]*(?:__[a-z][a-z0-9_]*)*)\s*(?!\s*\|\s*default\b)\}\}"
    )
    safe = {
        "server_type",
        "proxy_type",
        "document_root",
        "server_name",
        "cert_path",
        "key_path",
        "ca_bundle",
        "cache_dir",
    }
    _j2_set_re = re.compile(r"\{%-?\s*set\s+(\w+)")
    _j2_for_vars_re = re.compile(r"\{%-?\s*for\s+((?:[a-zA-Z_]\w*\s*,\s*)*[a-zA-Z_]\w*)")
    _role_defined_cache: dict[str, set[str]] = {}

    def _role_defined_vars(tasks_dir: Path) -> set[str]:
        key = str(tasks_dir)
        if key in _role_defined_cache:
            return _role_defined_cache[key]
        defined: set[str] = set()
        role_dir = tasks_dir.parent
        for yf in ("defaults/main.yml", "vars/main.yml"):
            yp = role_dir / yf
            if yp.is_file():
                try:
                    data = _yaml.safe_load(yp.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        defined.update(data.keys())
                except Exception:
                    pass
        _role_defined_cache[key] = defined
        return defined

    _file_defined_cache: dict[str, set[str]] = {}

    def _extract_file_defined_vars(tf: Path) -> set[str]:
        """Extract register:, set_fact:, loop_var, task vars from a task file."""
        key = str(tf)
        if key in _file_defined_cache:
            return _file_defined_cache[key]
        result: set[str] = set()
        tasks = _load_yaml(tf)
        if tasks is not None and isinstance(tasks, list):

            def _collect(task: dict[str, Any], idx: int, tf: Path) -> None:
                if "register" in task and isinstance(task["register"], str):
                    result.add(task["register"])
                for mod_key in ("ansible.builtin.set_fact", "set_fact"):
                    if mod_key in task and isinstance(task[mod_key], dict):
                        result.update(task[mod_key].keys())
                if "vars" in task and isinstance(task["vars"], dict):
                    result.update(task["vars"].keys())
                lc = task.get("loop_control")
                if isinstance(lc, dict):
                    lv = lc.get("loop_var")
                    if isinstance(lv, str):
                        result.add(lv)

            _walk_tasks(tasks, _collect, tf)
        _file_defined_cache[key] = result
        return result

    def _extract_j2_locals(content: str) -> set[str]:
        """Extract Jinja2 ``{% set v %}`` and ``{% for v in ... %}`` locals."""
        result: set[str] = set(_j2_set_re.findall(content))
        for match in _j2_for_vars_re.findall(content):
            for var in match.split(","):
                v = var.strip()
                if v:
                    result.add(v)
        return result

    for tf in task_files:
        if tf.stat().st_size > MAX_YAML_FILE_SIZE:
            continue
        content = tf.read_text(encoding="utf-8")
        role_defined = _role_defined_vars(tf.parent)
        file_defined = _extract_file_defined_vars(tf)
        j2_locals = _extract_j2_locals(content)
        combined_safe = safe | role_defined | file_defined | j2_locals
        for m in bare.finditer(content):
            vn = m.group(1)
            if len(vn) <= 2 or vn in combined_safe:
                continue
            violations.append(
                f"{tf.relative_to(ROOT)}: '{{{{{vn}}}}}' used "
                f"without | default() (line "
                f"{content[: m.start()].count(chr(10)) + 1})"
            )
    _KNOWN_BARE_VAR_BASELINE = 49
    assert len(violations) <= _KNOWN_BARE_VAR_BASELINE, (
        f"REGRESSION: {len(violations)} bare-var references exceeds "
        f"baseline of {_KNOWN_BARE_VAR_BASELINE}. New violations:\n"
        + "\n".join(f"  - {v}" for v in violations[_KNOWN_BARE_VAR_BASELINE:])
    )


# ── Test 17: include_tasks paths resolve ────────────────────────────


def test_include_tasks_paths_resolve() -> None:
    """include_tasks/import_tasks paths must resolve within the role."""
    task_files = _discover_task_files()
    assert len(task_files) > 0, "No task files found"
    violations: list[str] = []
    inc_keys = {
        "include_tasks",
        "ansible.builtin.include_tasks",
        "import_tasks",
        "ansible.builtin.import_tasks",
    }

    def _check(task: dict[str, Any], idx: int, tf: Path) -> None:
        for ik in inc_keys:
            if ik in task:
                rp = task[ik]
                if isinstance(rp, str) and not rp.startswith("/") and not (tf.parent / rp).exists():
                    violations.append(f"{tf.relative_to(ROOT)} task[{idx}]: '{ik}: {rp}' unresolved")
                break

    for tf in task_files:
        tasks = _load_yaml(tf)
        if tasks is None or not isinstance(tasks, list):
            continue
        _walk_tasks(tasks, _check, tf)
    assert violations == [], f"{len(violations)} unresolved include_tasks:\n" + "\n".join(
        f"  - {v}" for v in violations
    )


# ── Test 18: Empty blocks detected ──────────────────────────────────


def test_blocks_have_at_least_one_task() -> None:
    """A block/rescue/always section with zero tasks is a logic error."""
    task_files = _discover_task_files()
    assert len(task_files) > 0, "No task files found"
    violations: list[str] = []
    for tf in task_files:
        tasks = _load_yaml(tf)
        if tasks is None or not isinstance(tasks, list):
            continue
        for idx, item in enumerate(tasks):
            if not isinstance(item, dict):
                continue
            for bk in ("block", "rescue", "always"):
                if bk in item:
                    body = item[bk]
                    if isinstance(body, list) and len(body) == 0:
                        violations.append(f"{tf.relative_to(ROOT)} task[{idx}]: '{bk}' is empty list")
    assert violations == [], f"{len(violations)} empty blocks:\n" + "\n".join(f"  - {v}" for v in violations)


# ── Test 19: Handler modules use FQCN ───────────────────────────────


def test_handler_modules_use_fqcn() -> None:
    """Handler module invocations must use FQCN, same as task files."""
    roles = _discover_role_dirs()
    assert len(roles) > 0, "No roles found"
    violations: list[str] = []
    for role_dir in roles:
        hf = role_dir / "handlers" / "main.yml"
        if not hf.exists():
            continue
        handlers = _load_yaml(hf)
        if not isinstance(handlers, list):
            continue
        for idx, h in enumerate(handlers):
            if not isinstance(h, dict):
                continue
            for mod in _collect_task_module_names(h):
                if "." not in mod:
                    violations.append(f"{hf.relative_to(ROOT)} handler[{idx}]: '{mod}' — use FQCN")
    assert violations == [], f"{len(violations)} handler FQCN violations:\n" + "\n".join(f"  - {v}" for v in violations)


# ── Test 20: All YAML files under collections parse cleanly ─────────


YAML_PARSE_ERROR_CAP = 49


def test_all_collection_yaml_files_parse() -> None:
    """Every .yml file under collections/ must be valid YAML.

    Regression guard: count must not exceed YAML_PARSE_ERROR_CAP.
    """
    violations: list[str] = []
    skipped_large: list[str] = []
    for yf in sorted(COLLECTIONS_ROOT.rglob("*.yml")):
        if not yf.is_file():
            continue
        if yf.stat().st_size > MAX_YAML_FILE_SIZE:
            skipped_large.append(str(yf.relative_to(ROOT)))
            continue
        try:
            yaml.safe_load(yf.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            violations.append(f"{yf.relative_to(ROOT)}: {e}")
    if skipped_large:
        warnings.warn(
            f"Skipped {len(skipped_large)} large .yml file(s) (>512KB): " + ", ".join(skipped_large[:10]),
            stacklevel=2,
        )
    assert len(violations) <= YAML_PARSE_ERROR_CAP, (
        f"{len(violations)} YAML parse errors (cap {YAML_PARSE_ERROR_CAP}):\n"
        + "\n".join(f"  - {v}" for v in violations)
    )
